"""Same-host worker — consumes the platform run queue and executes the engine.

M4 배포 전환 (docs/PLATFORM-PLAN.md §1 원칙 5, §3; ROADMAP Phase 3 Step 2의
`runner/`). With PLATFORM_EXECUTOR=worker the control plane only records runs
(status 'dispatched', gh_run_id NULL) — that record IS the queue. This worker:

  1. polls controlplane의 SQLite (PLATFORM_DB) every ~15s for the OLDEST such
     run and claims it by assigning gh_run_id = ``local-<unix_ts>`` via
     ``UPDATE … WHERE gh_run_id IS NULL AND status='dispatched' AND id=?`` —
     the WHERE guard makes the claim single-writer safe (a second worker's
     UPDATE matches 0 rows and it moves on);
  2. executes the SAME stage sequence .github/workflows/api-test.yml runs, as
     ``python -m …`` subprocesses from the repo root (engine code unchanged —
     §1 원칙 1), with the suite expanded by core.suites and the profile by
     core.profiles, mirroring the workflow's gates:

       validate (scenario data + knowledge)            spec job parity
       smoke + read-chains                             always
       lane filters → shared infra → adopt-CRUD(-n 6)  only when mutations
         → merge shards → teardown → VPC-CRUD(serial)  (A∥B run serially here:
                                                        same -k partition from
                                                        shared_infra
                                                        --print-filters)
       sweep (cleanup.reconciler)                      mutations|destructive,
                                                       ALWAYS (even after a
                                                       failed stage — the
                                                       workflow's always())
       conformance static+runtime+baseline             only when requested
       dashboard build → core.snapshot → oplog finalize  always

  3. records every stage as a milestone event DIRECTLY in the DB
     (db.insert_event + db.apply_milestone — the same shape
     /api/ingest/events produces), so the live UI works without HTTP. Children
     still inherit APITEST_PLATFORM_URL, so the engine's oplog mirror keeps
     feeding remote UIs; the worker's own S3 oplog parity emits strip that
     variable to avoid double-ingesting milestones into the same DB.

Deliberately NOT mirrored from api-test.yml (Actions-only concerns):
PR comments, artifact upload/download, dashboard-data branch publishing,
``refresh_catalog`` (run it on the host when wanted), the ``.github/heavy.txt``
self-trigger, and the schema-live conformance gate (opt-in via its own run).

CLI:
  python -m runner.worker [--once] [--poll 15]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from controlplane import db  # noqa: E402

# one stage may legitimately grind for hours (heavy lifecycles) — same ceiling
# as the workflow's regression job timeout-minutes: 300.
STAGE_TIMEOUT = int(os.environ.get("PLATFORM_WORKER_STAGE_TIMEOUT", str(300 * 60)))
LOG_DIR = ROOT / "reports" / "worker"

# shared-infra provision retry — the workflow retries 10×45s because job B
# churns VPCs in parallel; here the lanes run serially so contention is only
# leftovers from previous runs.
PROVISION_ATTEMPTS = 3
PROVISION_RETRY_SLEEP = 30.0  # module-level so tests can zero it

# the workflow's xdist shard merge + junit fold steps, verbatim semantics
# (run as subprocesses from the repo root, like every other stage).
MERGE_SHARDS_SNIPPET = """\
from pathlib import Path
from core import results
n = results.merge_worker_shards()
print(f"merged {n} results shard(s) into canonical observations/findings")
tsv = Path("reports/smoke_status.tsv")
shards = sorted(Path("reports").glob("smoke_status-*.tsv"))
if shards:
    with open(tsv, "a") as out:
        for s in shards:
            out.write(s.read_text())
            s.unlink()
    print(f"merged {len(shards)} smoke TSV shard(s)")
"""

FOLD_JUNIT_SNIPPET = """\
import glob, xml.etree.ElementTree as ET
cases = []
for f in sorted(glob.glob("reports/junit-crud-*.xml")):
    try: cases.extend(ET.parse(f).getroot().iter("testcase"))
    except (ET.ParseError, OSError): pass
if cases:
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", {"name": "crud", "tests": str(len(cases))})
    for tc in cases: suite.append(tc)
    ET.ElementTree(suites).write("reports/junit-crud.xml", encoding="utf-8", xml_declaration=True)
    print(f"merged {len(cases)} crud testcase(s) into reports/junit-crud.xml")
"""


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


# --- suite expansion → workflow gate semantics -------------------------------------

def parse_kv_lines(text: str) -> dict[str, str]:
    """KEY=VALUE lines (comments/blank skipped, last wins, only leading/
    trailing whitespace trimmed — crud_filter values contain spaces). The same
    parse the spec job's `get()` shell helper implements."""
    out: dict[str, str] = {}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        key, _, val = ln.partition("=")
        out[key.strip()] = val.strip()
    return out


def expand_suite(suite_id: str) -> dict[str, str]:
    """suites/<id>.yaml → run-request option dict via core.suites render —
    the exact text the workflow's spec job parses. Unknown suite raises
    (fail BEFORE anything touches the live API, like the workflow)."""
    if not suite_id:
        return {}
    from core import suites
    return parse_kv_lines(suites.render(suites.load_suite(suite_id)))


def gates(opts: dict[str, str], environ=os.environ) -> dict:
    """Mirror api-test.yml's job-level gate expressions. Repo Variables
    (vars.SCP_RUN_CRUD / SCP_RUN_HEAVY / SCP_RUN_CONFORMANCE) translate to
    worker-host environment variables of the same name."""
    crud_var = _truthy(environ.get("SCP_RUN_CRUD"))
    mutations = _truthy(opts.get("mutations")) or crud_var
    destructive = _truthy(opts.get("destructive")) or crud_var
    return {
        "mutations": mutations,
        "destructive": destructive,
        "heavy": _truthy(opts.get("heavy")) or _truthy(environ.get("SCP_RUN_HEAVY")),
        "conformance": (_truthy(opts.get("conformance"))
                        or _truthy(environ.get("SCP_RUN_CONFORMANCE"))),
        # sweep job gate: mutations || destructive (|| the CRUD repo var)
        "sweep": mutations or destructive,
        "sweep_force": _truthy(opts.get("sweep_force")),
    }


def k_filter(base: str, *extra: str) -> str:
    """AND pytest -k expressions the way the workflow composes them:
    ``(base) and (extra)`` — empty parts dropped."""
    parts = [p for p in (base, *extra) if p]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return " and ".join(f"({p})" for p in parts)


def build_env(gh_run_id: str, profile: str, g: dict) -> dict[str, str]:
    """Per-run child environment: inherited host env (APITEST_PLATFORM_URL/
    TOKEN pass through so the engine mirror keeps working) + profile export +
    the regression jobs' safety gates + APITEST_RUN_ID for registry tagging."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["APITEST_RUN_ID"] = gh_run_id
    if profile:
        from core import profiles
        data = profiles.load_profile(profile)
        errs = profiles.validate_profile(data, profiles.profile_path(profile))
        if errs:
            raise RuntimeError(f"environment profile {profile!r} invalid: {errs}")
        for key, val in profiles.export_pairs(data, env):
            env[key] = val
    env["SCP_ALLOW_MUTATIONS"] = "true" if g["mutations"] else "false"
    env["SCP_ALLOW_DESTRUCTIVE"] = "true" if g["destructive"] else "false"
    env["SCP_RUN_HEAVY"] = "true" if g["heavy"] else "false"
    return env


# --- subprocess + milestone seams (tests stub _run) ---------------------------------

def _run(args: list[str], env: dict, timeout: int | None = None,
         label: str = "") -> tuple[int, str]:
    """One stage subprocess from the repo root; returns (rc, stdout). Full
    output goes to reports/worker/<run>-<label>.log, the tail to stdout for
    `docker compose logs`. Never raises."""
    print(f"[worker] $ {' '.join(a if len(a) < 80 else a[:77] + '...' for a in args)}",
          flush=True)
    try:
        proc = subprocess.run(args, cwd=str(ROOT), env=env, text=True,
                              capture_output=True, timeout=timeout or STAGE_TIMEOUT)
        rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timeout after {timeout or STAGE_TIMEOUT}s"
    except Exception as exc:  # missing interpreter etc. — a stage, not the loop, fails
        rc, out, err = 1, "", f"spawn failed: {exc}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rid = env.get("APITEST_RUN_ID", "local")
        with open(LOG_DIR / f"{rid}-{label or 'stage'}.log", "a") as fh:
            fh.write(f"\n$ {' '.join(args)}\n[rc={rc}]\n{out}{err}\n")
    except OSError:
        pass
    tail = "\n".join((out + err).splitlines()[-8:])
    if tail:
        print(tail, flush=True)
    if rc:
        print(f"[worker] rc={rc} ({label})", flush=True)
    return rc, out


def _milestone(gh_run_id: str, stage: str, status: str, job: str = "",
               detail: str = "", env: dict | None = None) -> None:
    """Stage result → DB directly (same shape /api/ingest/events writes), plus
    a best-effort `core.oplog emit` subprocess for S3 archive parity. The emit
    strips APITEST_PLATFORM_URL — the direct DB write IS the platform mirror
    here, so mirroring again would double every milestone."""
    db.insert_event(gh_run_id, "milestone", db.now(), job, stage, status, detail)
    db.apply_milestone(gh_run_id, stage, status, detail)
    if stage == "dashboard":  # ingest parity: post-run AI triage hook
        try:
            from controlplane import triage
            triage.auto_triage(gh_run_id)
        except Exception:
            pass
    emit_env = dict(env or os.environ)
    emit_env.pop("APITEST_PLATFORM_URL", None)
    emit_env["APITEST_RUN_ID"] = gh_run_id
    emit_env["PYTHONPATH"] = str(ROOT)
    _run([sys.executable, "-m", "core.oplog", "emit", "--stage", stage,
          "--status", status, "--job", job, "--detail", detail],
         emit_env, timeout=120, label=f"oplog-{stage}")


# --- queue claim ---------------------------------------------------------------------

def claim_next() -> dict | None:
    """Claim the oldest dispatched, unbound run. Single-writer safe: the
    UPDATE's WHERE re-checks the unclaimed state, so when two workers race,
    exactly one sees rowcount 1 (the loser gets 0 and polls again)."""
    with db.connect() as con:
        row = con.execute(
            "SELECT id, suite, profile, detail FROM runs"
            " WHERE status = 'dispatched' AND gh_run_id IS NULL"
            " ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        local_id = f"local-{int(time.time())}"
        for suffix in ("", f"-{row['id']}"):  # UNIQUE gh_run_id: same-second fallback
            try:
                cur = con.execute(
                    "UPDATE runs SET gh_run_id = ?, status = 'running', started_at = ?"
                    " WHERE id = ? AND gh_run_id IS NULL AND status = 'dispatched'",
                    (local_id + suffix, db.now(), row["id"]))
            except sqlite3.IntegrityError:
                continue
            if cur.rowcount != 1:
                return None  # another worker won this row
            return {"id": row["id"], "gh_run_id": local_id + suffix,
                    "suite": row["suite"] or "", "profile": row["profile"] or "",
                    # dispatch-form narrowing (service/crud_filter) rides in detail
                    "options": row["detail"] or ""}
    return None


def _fail_run(gh_run_id: str, detail: str) -> None:
    _milestone(gh_run_id, "dashboard", "failure", job="worker", detail=detail[:500])


# --- the stage sequence (api-test.yml parity) ------------------------------------------

def process_run(run: dict) -> str:
    """Execute one claimed run; returns the final status ('done'/'failed').
    Failing stages are collected, never raised — sweep (when armed), dashboard
    and snapshot still run, mirroring the workflow's always() semantics."""
    gh, suite, profile = run["gh_run_id"], run["suite"], run["profile"]
    py = sys.executable
    failures: list[str] = []

    # resolve options — spec-job "Resolve run options" + "Apply profile" parity;
    # a bad suite/profile fails BEFORE anything touches the live API.
    try:
        opts = expand_suite(suite)
        # per-run narrowing from the dispatch form (KEY=VALUE lines in detail)
        opts.update(parse_kv_lines(run.get("options", "")))
        g = gates(opts)
        env = build_env(gh, profile, g)
    except Exception as exc:
        print(f"[worker] run {gh}: cannot resolve suite/profile — {exc}", flush=True)
        _fail_run(gh, f"suite/profile resolution failed: {exc}")
        return "failed"

    detail = (f"event=worker suite={suite or '-'}"
              f" mutations={str(g['mutations']).lower()}"
              f" destructive={str(g['destructive']).lower()}"
              f" heavy={str(g['heavy']).lower()}")
    _run([py, "-m", "core.oplog", "ensure"],
         {**env, "APITEST_PLATFORM_URL": ""}, timeout=120, label="oplog-ensure")
    _milestone(gh, "run-start", "running", job="spec", detail=detail, env=env)

    # --- stage: validate (spec job) — scenario data + knowledge, offline guard
    rc_v1, _ = _run([py, "-m", "regression.scenarios.validate"], env, label="validate")
    rc_v2, _ = _run([py, str(ROOT / "knowledge" / "formal" / "validate.py")],
                    env, label="validate-knowledge")
    validate_failed = bool(rc_v1 or rc_v2)
    _milestone(gh, "validate", "failure" if validate_failed else "success",
               job="spec", env=env)
    if validate_failed:
        # spec job failure ⇒ regression/conformance skipped (needs: spec without
        # always()); sweep/dashboard still run below.
        failures.append("validate")

    category, service = opts.get("category", ""), opts.get("service", "")
    crud_filter = opts.get("crud_filter", "")

    if not validate_failed:
        # --- stage: smoke + read-chains (always, read-only)
        args = [py, "-m", "pytest", "tests/smoke", "-m", "smoke"]
        if category:
            args += ["--category", category]
        if service:
            args += ["--service", service]
        rc, _ = _run(args, env, label="smoke")
        if rc:
            failures.append("smoke")
        _milestone(gh, "smoke", "success" if rc == 0 else "failure",
                   job="regression-A", env=env)

        # --- stages: CRUD lanes — A(adopt, parallel) then B(vpc-crud, serial).
        # The workflow runs A∥B as two jobs; on one host they run serially with
        # the SAME partition filters, so wall-clock is A+B but semantics match.
        if g["mutations"]:
            rc, out = _run([py, "-m", "regression.scenarios.shared_infra",
                            "--print-filters"], env, label="lane-filters")
            lanes = parse_kv_lines(out)
            parallel_k, vpc_crud_k = lanes.get("PARALLEL_K", ""), lanes.get("VPC_CRUD_K", "")
            if rc or not (parallel_k and vpc_crud_k):
                failures.append("lane-filters")
            else:
                # provision the shared VPC+subnet every adopt lifecycle reuses
                shared: dict[str, str] = {}
                for attempt in range(PROVISION_ATTEMPTS):
                    _, out = _run([py, "-m", "regression.scenarios.shared_infra",
                                   "--provision"], env, label="provision")
                    shared = {k: v for k, v in parse_kv_lines(out).items()
                              if k.startswith("SCP_SHARED_")}
                    if "SCP_SHARED_VPC_ID" in shared:
                        break
                    if attempt + 1 < PROVISION_ATTEMPTS:
                        print(f"[worker] shared-infra provision attempt {attempt + 1} "
                              f"got no ids — retry in {PROVISION_RETRY_SLEEP:.0f}s",
                              flush=True)
                        time.sleep(PROVISION_RETRY_SLEEP)

                # A: adopt-class CRUD, parallel (-n 6), adopting the shared ids
                rc, _ = _run([py, "-m", "pytest", "tests/crud", "-m", "crud",
                              "-n", "6", "--junitxml=reports/junit-crud-parallel.xml",
                              "-k", k_filter(parallel_k, crud_filter)],
                             {**env, **shared}, label="adopt-crud")
                if rc == 5:  # no tests matched — normal for scoped runs
                    rc = 0
                if rc:
                    failures.append("adopt-crud")
                _run([py, "-c", MERGE_SHARDS_SNIPPET], env, timeout=300,
                     label="merge-shards")
                _milestone(gh, "adopt-crud", "success" if rc == 0 else "failure",
                           job="regression-A", env=env)

                # teardown BEFORE the serial vpc-crud lane — frees the shared
                # VPC slot the self-creating lifecycles need (account cap 5)
                _run([py, "-m", "regression.scenarios.shared_infra", "--teardown"],
                     env, label="teardown")

                # B: VPC-CRUD class, serial (-n 0), heavy-shared-networking LAST
                b_env = {**env, "SCP_SHARED_VPC_DISABLE": "true"}
                rc_b = 0
                for pass_k in ("not heavy-shared-networking", "heavy-shared-networking"):
                    junit = "reports/junit-crud-" + "".join(
                        ch for ch in pass_k if ch.islower() or ch == "-") + ".xml"
                    rc, _ = _run([py, "-m", "pytest", "tests/crud", "-m", "crud",
                                  "-n", "0", f"--junitxml={junit}",
                                  "-k", k_filter(vpc_crud_k, pass_k, crud_filter)],
                                 b_env, label="vpc-crud")
                    if rc == 5:
                        rc = 0
                    rc_b = rc or rc_b
                if rc_b:
                    failures.append("vpc-crud")
                _run([py, "-c", FOLD_JUNIT_SNIPPET], env, timeout=300,
                     label="fold-junit")
                _milestone(gh, "vpc-crud", "success" if rc_b == 0 else "failure",
                           job="regression-B", env=env)

    # --- stage: sweep — always() && (mutations || destructive): runs even after
    # failed stages so nothing strands (continue-on-error in the workflow)
    if g["sweep"]:
        sweep_env = dict(env)
        sweep_env["SCP_ALLOW_MUTATIONS"] = "true"  # mutations||destructive — armed
        sweep_env["SCP_ALLOW_DESTRUCTIVE"] = "true" if g["destructive"] else "false"
        if g["sweep_force"]:
            sweep_env["SCP_SWEEP_IGNORE_TTL"] = "true"
        _run([py, "-u", "-m", "cleanup.reconciler"], sweep_env, label="sweep")
        _milestone(gh, "sweep", "done", job="sweep", env=env)

    # --- stage: conformance — opt-in; needs spec (skipped when validate failed).
    # Workflow parity: every step is `|| true` (best-effort), read-only/empty-
    # body probes only — schema-live stays behind its own opt-in run.
    if g["conformance"] and not validate_failed:
        c_env = {**env, "SCP_PROBE_RUNTIME": "true", "SCP_PROBE_VALIDATION": "true",
                 "SCP_ALLOW_MUTATIONS": "true", "SCP_ALLOW_DESTRUCTIVE": "false",
                 "SCP_RUN_SCHEMA_LIVE": "false"}
        _run([py, "-m", "conformance.static"], c_env, label="conformance-static")
        _run([py, "-m", "conformance.runtime", "--probe", "all",
              "--category", category, "--limit", "0"], c_env,
             label="conformance-runtime")
        _run([py, "-m", "conformance.baseline", "--baseline",
              "data/conformance_baseline.json", "--init-if-missing"], c_env,
             label="conformance-baseline")
        _milestone(gh, "conformance", "done", job="conformance", env=env)

    # --- stages: dashboard build + per-run snapshot + finalize (always)
    sha = _git_out(["rev-parse", "--short", "HEAD"])
    branch = _git_out(["rev-parse", "--abbrev-ref", "HEAD"])
    rc, _ = _run([py, "-m", "dashboard.build", "--run-type", "worker",
                  "--sha", sha, "--branch", branch,
                  "--out", "dashboard/index.html"], env, label="dashboard")
    if rc:
        failures.append("dashboard")
    _run([py, "-m", "core.snapshot", "upload", "--suite", suite,
          "--profile", profile], env, label="snapshot")
    _milestone(gh, "dashboard", "failure" if failures else "done", job="dashboard",
               detail=("failed stages: " + ", ".join(failures)) if failures else "",
               env=env)
    _run([py, "-m", "core.oplog", "finalize", "--history", "dashboard/history.jsonl"],
         {**env, "APITEST_PLATFORM_URL": ""}, timeout=300, label="finalize")
    return "failed" if failures else "done"


def _git_out(args: list[str]) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), *args],
                              capture_output=True, text=True, timeout=30)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


# --- the loop ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="same-host worker — consumes the platform run queue "
                    "(PLATFORM_EXECUTOR=worker)")
    ap.add_argument("--once", action="store_true",
                    help="process at most one run, then exit")
    ap.add_argument("--poll", type=float, default=15.0,
                    help="queue poll interval in seconds (default 15)")
    a = ap.parse_args(argv)
    print(f"[worker] repo {ROOT}\n[worker] queue {db.DB_PATH} "
          f"(poll {a.poll:.0f}s{', once' if a.once else ''})", flush=True)
    while True:
        try:
            run = claim_next()
            if run is not None:
                print(f"[worker] claimed run #{run['id']} as {run['gh_run_id']} "
                      f"(suite={run['suite'] or '-'} profile={run['profile'] or '-'})",
                      flush=True)
                try:
                    status = process_run(run)
                except Exception as exc:  # one broken run never kills the loop
                    traceback.print_exc()
                    try:
                        _fail_run(run["gh_run_id"], f"worker crashed: {exc}")
                    except Exception:
                        pass
                    status = "failed"
                print(f"[worker] run {run['gh_run_id']} → {status}", flush=True)
                if a.once:
                    return 0
                continue  # drain the queue before sleeping again
            if a.once:
                print("[worker] queue empty", flush=True)
                return 0
        except KeyboardInterrupt:
            print("\n[worker] stopped", flush=True)
            return 0
        except Exception:  # DB locked/missing etc. — log and keep polling
            traceback.print_exc()
        try:
            time.sleep(a.poll)
        except KeyboardInterrupt:
            print("\n[worker] stopped", flush=True)
            return 0


if __name__ == "__main__":
    sys.exit(main())
