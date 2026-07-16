"""Shared local-run pipeline for the `local` PLATFORM_EXECUTOR (convergence S2).

The original local execution console (`tools/console2_server.py`) runs a selection
locally — **simulate** (replay the dag_planner plan to the live-event stream, no
cloud) or **live** (provision shared VPC → `pytest tests/crud` → teardown). The
convergence brings that same capability into the control plane as a third executor
(`local`, alongside `actions`/`worker`). To avoid two copies of the run loop, the
reusable *logic* lives here and both callers wire it to their own run-record /
event sink.

This module owns the **simulate replay** as a pure, deterministic function:
:func:`simulate_run` walks the plan and calls an injected ``emit(kind, **fields)``
sink using the canonical console-event vocabulary (see ``core.console_events`` /
``core.events_contract``). No globals, no I/O, no cloud — ``sleep``/``new_id`` are
injectable so tests run instantly and reproducibly. The caller supplies the plan
(from ``dag_planner``) and an ``emit`` that appends to its event stream
(``core.console_events.emit`` for a real run, a list for a test).

The **live** pipeline (provision→pytest→teardown) is the next slice; it stays in
``console2_server`` until the control-plane executor is wired, then moves here too.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[2]      # repo root (regression/scenarios/..)


def resource_type(path: str) -> str:
    """Coarse resource type from a step path — the first non-version, non-template
    segment. ``/v1/vpcs/{vpc_id}`` → ``vpcs`` · ``/v1/queues`` → ``queues``. Plural
    (the raw collection segment); mirrors ``console2_server._sim_resource_type`` so
    the simulate resource view labels identically."""
    for seg in (path or "").strip("/").split("/"):
        if not seg or seg.startswith("{"):
            continue
        if seg.startswith("v") and len(seg) > 1 and seg[1].isdigit():   # v1, v2, v1.1, v2025-01
            continue
        return seg
    return "resource"


def simulate_run(
    waves: Sequence[Mapping[str, Any]],
    preview: Mapping[str, Mapping[str, Any]],
    emit: Callable[..., None],
    *,
    step_delay: float = 0.0,
    beat: float = 0.0,
    sleep: Callable[[float], None] | None = None,
    new_id: Callable[[], str] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Replay a dag_planner plan to the canonical console-event vocabulary — a DRY
    RUN with **no cloud calls**. Walks the waves in DAG order and, within each wave,
    each lifecycle's HTTP steps, so a live view shows the real creation order + API
    sequence. Emits synthetic ``resource-tracked`` / ``resource-deleted`` (ids
    prefixed ``sim-``) on create/delete steps so a resource view renders too.

    Args:
        waves:   ``[{kind, lifecycles:[lid], vpc_slots}]`` — ``dag_planner`` plan waves.
        preview: ``{lid: {service, heavy, steps:[{name, method, path, kind}]}}`` —
                 each lifecycle's HTTP step list (steps without ``method`` are skipped).
        emit:    sink called ``emit(kind, **fields)`` for every event (the ONLY output).
        step_delay/beat: optional pacing (seconds) so a live view is watchable; 0 = instant.
        sleep:   injectable sleeper (default no-op) — pass ``time.sleep`` for real pacing.
        new_id:  injectable synthetic-id generator (default a deterministic counter).
        meta:    extra fields merged into the ``run-meta`` event (e.g. ``{"runnable": [...]}``).
    """
    sleep = sleep or (lambda _s: None)
    if new_id is None:
        _ctr = itertools.count(1)
        new_id = lambda: "sim-%08x" % next(_ctr)   # noqa: E731 — tiny local default

    waves = list(waves or [])
    emit("run-meta", mode="simulate", waves=len(waves), **(dict(meta) if meta else {}))
    for wi, w in enumerate(waves):
        emit("wave-start", wave=wi, wave_kind=w.get("kind", ""),
             lifecycles=list(w.get("lifecycles", [])), vpc_slots=w.get("vpc_slots", 0))
        for lid in w.get("lifecycles", []):
            pv = preview.get(lid) or {"steps": [], "service": "", "heavy": False}
            steps = [s for s in pv.get("steps", []) if s.get("method")]   # HTTP steps only
            emit("lifecycle-start", lifecycle=lid, service=pv.get("service", ""),
                 heavy=pv.get("heavy", False), n_steps=len(steps), wave=wi)
            for s in steps:
                emit("step-start", lifecycle=lid, step=s["name"],
                     method=s["method"], path=s["path"])
                sleep(step_delay)
                emit("step-end", lifecycle=lid, step=s["name"],
                     method=s["method"], path=s["path"],
                     status=200, category="ok", elapsed_ms=int(step_delay * 1000))
                if s.get("kind") == "create":
                    emit("resource-tracked", lifecycle=lid,
                         resource_type=resource_type(s["path"]),
                         resource_id=new_id(), path=s["path"])
                    sleep(beat)
                elif s.get("kind") == "delete":
                    emit("resource-deleted", lifecycle=lid,
                         resource_type=resource_type(s["path"]), path=s["path"])
                    sleep(beat)
            emit("lifecycle-end", lifecycle=lid, status="passed")
    emit("run-end", status="done")


def _step_kind(step: Mapping[str, Any]) -> str:
    """Coarse create/delete classification for the synthetic simulate resources, by
    HTTP method (POST→create, DELETE→delete); a wait/ready step is neither. Simpler
    than console2's predicate table — it only affects which steps emit a synthetic
    ``resource-tracked`` / ``-deleted`` in a dry run."""
    name = (step.get("name") or "").lower()
    if any(w in name for w in ("wait", "ready", "active")):
        return "wait"
    return {"POST": "create", "DELETE": "delete"}.get((step.get("method") or "").upper(), "step")


def build_plan(lifecycle_ids: Sequence[str]) -> dict:
    """Build the simulate inputs for a selection using ENGINE modules only — the same
    ``dag_planner`` schedule + per-lifecycle step preview console2's ``_plan`` produces,
    so the control-plane ``local`` executor is self-contained (no console2 import).

    Returns ``{waves, preview, runnable, skipped_disabled, leaf_set}`` — hand
    ``plan["waves"], plan["preview"]`` straight to :func:`simulate_run`.
    """
    from regression.scenarios import dag_planner, validate_dag
    from regression.scenarios.loader import load_lifecycles
    deps = validate_dag._load_deps()
    all_lcs = validate_dag._load_lifecycles()
    enabled = {lc["id"] for lc in all_lcs if lc.get("enabled")}
    requested = list(lifecycle_ids or [])
    runnable = [lid for lid in requested if lid in enabled]
    # leaf set = the runnable subset of the SELECTION; None (= all enabled) ONLY when
    # nothing was selected — never plan the whole platform for an all-disabled selection.
    leaf_set = runnable if requested else None
    p = dag_planner.plan(leaf_set=leaf_set, deps=deps, lifecycles=all_lcs)
    lcs, _ = load_lifecycles(with_sources=True)
    by_id = {lc["id"]: lc for lc in lcs}
    preview: dict[str, dict] = {}
    for lid in p.leaf_set:
        lc = by_id.get(lid, {})
        steps = [{"name": s.get("name", ""), "method": s.get("method"),
                  "path": s.get("path"), "kind": _step_kind(s)} for s in lc.get("steps", [])]
        preview[lid] = {"service": lc.get("service", ""), "heavy": bool(lc.get("heavy")),
                        "n_steps": len(steps), "steps": steps}
    return {"waves": p.to_dict()["waves"], "preview": preview, "runnable": runnable,
            "skipped_disabled": sorted(set(requested) - enabled), "leaf_set": list(p.leaf_set)}


# --------------------------------------------------------------------------- #
# live pipeline — the REAL run path (provision shared VPC -> pytest tests/crud ->
# precise teardown). Extracted from console2_server._run_worker so the control-plane
# `local` executor doesn't import the console2 dev server. Safety gates are EXPLICIT
# args (the caller's opt-in) — never defaulted on "to make a test pass" (Hard Rule 1).
# The engine emits fine console-events to events_path during pytest, so the live view
# is identical to simulate. Needs SCP creds + egress (real cloud calls).
# --------------------------------------------------------------------------- #
def _events_note(env: Mapping, kind: str, **fields) -> None:
    """Append one server-side event line to the run's live-event stream (the
    ``SCP_CONSOLE_EVENTS`` path in ``env``; same line shape as
    ``core.console_events``) so the live view can narrate run phases that happen
    OUTSIDE pytest — today: shared-infra provisioning. Best-effort: no sink
    configured → silent no-op; narration must never break a run."""
    path = (env or {}).get("SCP_CONSOLE_EVENTS")
    if not path:
        return
    try:
        rec = {"ts": round(time.time(), 3), "kind": kind}
        rec.update(fields)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _stream_cmd(cmd: Sequence[str], env: Mapping, f) -> tuple[int, list[str]]:
    """Run ``cmd`` with merged stdout/stderr STREAMED line-by-line into the open
    log file ``f`` (flush per line). ``subprocess.run(stdout=PIPE)`` held the
    ENTIRE provision output until process exit, so the 로그 tab froze at the
    ``=== provision shared VPC ===`` header for the full VPC+서브넷 ACTIVE wait
    (1~3분) — owner report 2026-07-08 "실제 리소스 생성 시작까지가 너무 오래걸려".
    Returns ``(rc, lines)`` so callers can parse KEY=VALUE output."""
    proc = subprocess.Popen(list(cmd), cwd=str(_ROOT),
                            env={**env, "PYTHONUNBUFFERED": "1"},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        f.write(line)
        f.flush()
    return proc.wait(), lines


def selection_needs_shared_vpc(lifecycle_ids: Sequence[str]) -> bool:
    """선택에 adopt:vpc 라이프사이클이 하나라도 있으면 True — 공유 VPC 프로비저닝
    필요 판정. 종전에는 heavy 선택에서만 프로비저닝해서, non-heavy 선택에 섞인
    adopt형(gen-private-nat 등)이 전부 IB-049 스킵됐다 (오너 실측 2026-07-10
    run-adfd: 'no shared VPC and running under xdist worker')."""
    from regression.scenarios.loader import load_lifecycles
    lcs, _ = load_lifecycles(with_sources=True)
    want = set(lifecycle_ids)
    for lc in lcs:
        if lc["id"] in want:
            for s in lc.get("steps", []):
                # vpc / vpc#a / vpc#b 전부 provision 필요 (net-VPC A/B는
                # provision이 공유 VPC와 함께 만든다, 2026-07-13)
                if (s.get("adopt") or "").split("#", 1)[0] == "vpc":
                    return True
    return False


def provision_shared(env: dict, f) -> dict:
    """Provision ONE session-shared VPC+subnet so adopter lifecycles don't skip under
    ``-n``. Best-effort: on failure adopters self-skip and self-creators still run.
    ``f`` is the run's open log file (output streams into it line-by-line, so the
    ACTIVE waits read as heartbeats, not a frozen header). Returns the
    ``SCP_SHARED_*`` env to merge into the pytest env."""
    f.write("\n=== provision shared VPC (adopters need this under -n) ===\n")
    f.flush()
    t0 = time.monotonic()
    _events_note(env, "provision-start",
                 note="공유 인프라(VPC+서브넷) 준비 — 서브넷은 no-wait(생성만 하고 "
                      "ACTIVE는 첫 adopt 시점 게이트), 통상 ~30초")
    # 서브넷 ACTIVE head 대기 제거 (run-543a 실측 4.3분 유휴 — 오너 2026-07-13
    # "2번 바로 수정"): adopt 시점의 engine._ensure_adopted_active가 보장한다.
    env = {**env, "SCP_PROVISION_SUBNET_NOWAIT": "true"}
    _, lines = _stream_cmd([sys.executable, "-m", "regression.scenarios.shared_infra",
                            "--provision"], env, f)
    shared: dict = {}
    for line in lines:
        line = line.strip()
        if line.startswith("SCP_SHARED_") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                shared[k.strip()] = v.strip()
    elapsed = round(time.monotonic() - t0, 1)
    if shared.get("SCP_SHARED_VPC_ID"):
        shared["SCP_VPC_SHARED_RESERVED"] = "1"
        f.write(f"\n[provision] shared VPC ready: {shared['SCP_SHARED_VPC_ID']} "
                f"({elapsed:.0f}s)\n")
    else:
        f.write("\n[provision] no shared VPC id — adopters will skip (self-creators still run)\n")
    f.flush()
    _events_note(env, "provision-end",
                 vpc=shared.get("SCP_SHARED_VPC_ID", ""), elapsed_s=elapsed)
    return shared


def teardown_shared(env: dict, shared: dict, f) -> None:
    """Delete the session shared VPC precisely by id (no name-guessing — Hard Rule 3)."""
    if not shared.get("SCP_SHARED_VPC_ID"):
        return
    f.write("\n=== teardown shared VPC (precise, by id) ===\n")
    f.flush()
    subprocess.run([sys.executable, "-m", "regression.scenarios.shared_infra", "--teardown"],
                   cwd=str(_ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT)


def pytest_did_not_run(rc: int, pytest_out: str) -> bool:
    """True when the pytest runner itself never executed (e.g. pytest not installed) —
    so there are no results to trust AND nothing was created (skip teardown/sweep)."""
    low = (pytest_out or "").lower()
    if "no module named pytest" in low or "no module named 'pytest'" in low:
        return True
    has_outcome = bool(re.search(r"\d+\s+(passed|failed|skipped|error|xfailed|deselected)",
                                 pytest_out or ""))
    return rc in (3, 4) and not has_outcome


def live_run(lifecycle_ids, events_path: str, log_path: str, *, mutations: bool,
             destructive: bool, heavy: bool, parallel: int | None = None) -> dict:
    """REAL run: provision shared VPC (heavy only) → ``pytest tests/crud -m crud`` with
    ``SCP_CRUD_IDS`` + ``SCP_CONSOLE_EVENTS`` + the EXPLICIT safety gates → precise
    teardown. Per-run cleanup is teardown-scoped (the lifecycle deletes what it created);
    the account-wide reconciler sweep stays the manual 강제 클린업 (it can't scope to one
    run). Returns ``{rc, runner_missing}``; everything else surfaces in ``log_path``."""
    ids = list(lifecycle_ids)
    env = {**os.environ, "PYTHONPATH": str(_ROOT),
           # line-buffer children so the 로그 tail sees lines, not block bursts
           "PYTHONUNBUFFERED": "1",
           "SCP_CRUD_IDS": ",".join(ids), "SCP_CONSOLE_EVENTS": events_path,
           "SCP_ALLOW_MUTATIONS": "true" if mutations else "false",
           "SCP_ALLOW_DESTRUCTIVE": "true" if destructive else "false",
           "SCP_RUN_HEAVY": "true" if heavy else "false"}
    # 워커 캡 10 (owner 2026-07-08; console2_server._run_worker와 동일 근거)
    _cap = int(os.environ.get("SCP_LOCAL_WORKERS", "30"))
    n = str(parallel or max(1, min(_cap, len(ids) or 2)))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"# local live run  lifecycle_ids={ids}\n"
                f"# gates: mutations={mutations} destructive={destructive} heavy={heavy}  parallel={n}\n")
        f.flush()
        shared = (provision_shared(env, f)
                  if heavy or selection_needs_shared_vpc(ids) else {})
        f.write("\n=== pytest === (수집 → xdist 워커 기동 — 첫 step 로그까지 보통 수십 초)\n")
        f.flush()
        pos = f.tell()
        # opt-in: SCP_NATIVE_RUNNER=true면 pytest-xdist 대신 목적특화 스케줄러
        # (regression.scenarios.native_runner) — 동적 LPT·공유 쿼터·no-shutdown.
        # 별도 프로세스라 콘솔 kill/응답성 동일. xdist 경로는 폴백으로 유지.
        # (2026-07-13 오너 지시; scheduler_sim: native 70분/400=0 vs xdist 90분/400=4)
        if os.environ.get("SCP_NATIVE_RUNNER", "").strip().lower() == "true":
            _cmd = [sys.executable, "-m", "regression.scenarios.native_runner"]
        else:
            _cmd = [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
             # --dist=load --maxschedchunk=1 (2026-07-13 run-afa8 판정): load는
             # 글로벌 pending 풀을 유지해 워커가 완료할 때마다 리필하고, pending이
             # 남아있는 한 워커를 죽이지 않는다 → 빈 워커가 의존성 없는 대기를
             # 즉시 집는다(work-conserving). worksteal은 수집 순서를 워커별 블록으로
             # 전부 선분배 후 "훔칠 게 없으면 유휴 워커 shutdown"이라, 나중에 무거운
             # 게 끝나 일이 풀려도 집을 워커가 없어 라이트 꼬리가 30~46분까지 밀렸다
             # (afa8: 2분짜리 scr-repo가 46.3분에 시작해 makespan 정의). load의 초기
             # 청크=워커당 2개라 conftest의 [heavy,light] 인터리브와 한 쌍 — 상위 n
             # 몬스터가 전부 offset 0(t=0)에서 출발. (이전 worksteal 전환은 워커수
             # 버그로 인터리브가 죽은 상태의 오판 — 그 버그는 2026-07-11 수리됨.)
             "-n", n, "--dist=load", "--maxschedchunk=1", "-o", "addopts=", "-q"]
        rc = subprocess.run(
            _cmd, cwd=str(_ROOT), env={**env, **shared},
            stdout=f, stderr=subprocess.STDOUT).returncode
        f.flush()
        try:
            with open(log_path, encoding="utf-8") as rf:
                rf.seek(pos)
                pytest_out = rf.read()
        except Exception:
            pytest_out = ""
        runner_missing = pytest_did_not_run(rc, pytest_out)
        if runner_missing:
            f.write("\n⚠ pytest runner missing — no tests ran; skipping teardown/sweep "
                    "(nothing was created).\n")
        else:
            # 순서 재배열 (오너 2026-07-16): 이 런의 잔존 자식(원장 reap)을
            # 먼저 걷어야 공유 서브넷/VPC teardown이 409 사다리를 안 태운다
            # (run fe88: teardown→reap 순서로 net-B VPC 5회 409 낭비 실측).
            f.write("\n=== per-run cleanup: run-scoped reap (이 런의 잔존만 · "
                    "공유 teardown보다 먼저) ===\n")
            f.flush()
            try:
                from cleanup.run_scoped import reap_run_leftovers
                reap_run_leftovers(events_path,
                                   log=lambda m: (f.write(m + "\n"), f.flush()))
            except Exception as exc:  # noqa: BLE001 — best-effort tail
                f.write(f"  run-scoped reap 실패(무시): {exc}\n")
            teardown_shared(env, shared, f)
            f.write("\n=== per-run cleanup: teardown-scoped ===\n"
                    "  reap → 공유 teardown 순서로 수행 완료.\n"
                    "  account-wide reaping = the manual 강제 클린업 (POST /api/cleanup).\n")
            # 런 종료 자동 클린업 (owner 2026-07-10) — CLI 경로는 단일 런이라
            # 동시성 가드 불요. 끄기: SCP_RUN_END_SWEEP=false.
            # 자원-생성 게이트 (svc-opt #3 실측 2026-07-11): 3초짜리 read-only
            # 런에도 계정 전체 스윕(수 분)이 통째로 돌았다 — 이 런의 events
            # 원장에 resource-tracked가 하나도 없으면 지울 것이 없으므로 스킵
            # (run-scoped reap이 이미 '잔존 후보 0'을 확인한 것과 같은 원장).
            def _run_created_resources() -> bool:
                try:
                    for line in Path(events_path).read_text().splitlines():
                        if '"resource-tracked"' in line:
                            return True
                except OSError:
                    return True   # 원장을 못 읽으면 안전측: 스윕 수행
                return False
            if os.environ.get("SCP_RUN_END_SWEEP", "").strip().lower() \
                    in ("false", "0", "no"):
                pass
            elif not _run_created_resources():
                f.write("\n=== 런 종료 자동 클린업: 생략 — 이 런은 자원을 "
                        "생성하지 않음 (resource-tracked 0) ===\n")
            else:
                f.write("\n=== 런 종료 자동 클린업: owner-tag 강제 스윕 (IGNORE_TTL) ===\n")
                f.flush()
                subprocess.run(
                    [sys.executable, "-m", "cleanup.reconciler"], cwd=str(_ROOT),
                    env={**env, "SCP_ALLOW_MUTATIONS": "true",
                         "SCP_ALLOW_DESTRUCTIVE": "true",
                         "SCP_SWEEP_IGNORE_TTL": "true", "SCP_SWEEP_NOWAIT": "true"},
                    stdout=f, stderr=subprocess.STDOUT)
        f.flush()
    return {"rc": rc, "runner_missing": runner_missing}


def cleanup_sweep(log_path: str) -> dict:
    """FORCE account-wide reconciler sweep — delete ALL owner-tagged resources,
    ignoring TTL (DESTRUCTIVE). The explicit opt-in is the operator's button click;
    only OUR owner tag is reaped (Hard Rule 3 — no name-guessing). Writes ``log_path``."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "SCP_ALLOW_MUTATIONS": "true",
           "SCP_ALLOW_DESTRUCTIVE": "true", "SCP_SWEEP_IGNORE_TTL": "true"}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# FORCE cleanup — reconciler sweep (owner-tagged only, ignore TTL)\n\n")
        f.flush()
        rc = subprocess.run([sys.executable, "-m", "cleanup.reconciler"],
                            cwd=str(_ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    return {"rc": rc}


def verify_clean(log_path: str) -> dict:
    """Read-only owned-resource inventory (no deletes) — counts survivors. Writes log."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "SCP_ALLOW_DESTRUCTIVE": "false"}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# verify clean — owned inventory (read-only, no deletes)\n\n")
        f.flush()
        rc = subprocess.run([sys.executable, "-m", "cleanup.verify_clean"],
                            cwd=str(_ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    return {"rc": rc}


def simulate_schedule(lifecycle_ids: Sequence[str] | None = None,
                      workers: int | None = None, vpc_slots: int = 4) -> dict:
    """오프라인 스케줄 시뮬레이션 — 실제 conftest 정렬(실측 LPT + 스텝수
    타이브레이크)과 동일 규칙으로 greedy 배치를 예측한다 (오너 2026-07-11
    "전체 실행했을 때 시나리오가 동시에 어떻게 배치될지 시뮬레이션").
    API 호출 없음. 미모델: 공유-VPC 내 IGW/NAT 1:1 대기, 백엔드 지연/재시도.
    반환: {workers, vpc_slots, makespan_s, bars:[{id,w,s,e,vpc,measured}]}"""
    import json as _json
    from regression.scenarios.loader import load_lifecycles
    from tools.duration_stats import CLASS_DEFAULT_S, classify_lifecycle

    dur: dict[str, float] = {}
    for name in ("durations.json", "durations.local.json"):
        # 병합 = max (2026-07-13, conftest._durations와 동일 규칙): 오버레이의
        # 하향 오염이 LPT 랭크를 망치는 클래스 방지 — run-c373에서 pg-cluster가
        # 경량 오분류로 +42분 지각, 홀로 런 꼬리 23분.
        p = _ROOT / "data" / "optimizer" / name
        try:
            for k, v in _json.loads(p.read_text()).items():
                dur[k] = max(dur.get(k, 0.0), float(v.get("avg_s") or 0.0))
        except Exception:  # noqa: BLE001 — best-effort
            continue
    lcs, _ = load_lifecycles(with_sources=True)
    en = {lc["id"]: lc for lc in lcs
          if lc.get("enabled") and lc.get("role", "verify") == "verify"}
    ids = list(lifecycle_ids) if lifecycle_ids else sorted(en)
    items = [en[i] for i in ids if i in en]

    def _dur(lc: dict) -> float:
        v = dur.get(lc["id"], 0.0)
        return v if v > 0 else float(CLASS_DEFAULT_S[classify_lifecycle(lc)])

    def _self_vpc(lc: dict) -> bool:
        for s in lc.get("steps", []):
            if (s.get("method") == "POST"
                    and (s.get("path") or "").rstrip("/").endswith("/vpcs")):
                # vpc#a/vpc#b adopt(net-VPC 상주 공유)도 슬롯 비소비 (2026-07-13)
                if (s.get("adopt") or "").split("#", 1)[0] != "vpc":
                    return True
        return False

    # 0차 키 = priority_first 핀 — 실행 경로(tests/crud/conftest.py 수집 정렬)와
    # 동일 규칙이어야 예측 Gantt가 실제 투입 순서를 재현한다 (오너 2026-07-13).
    from regression.scenarios.schedule_optimizer import load_priority_first
    pinned = frozenset(load_priority_first())
    items.sort(key=lambda lc: (lc["id"] in pinned, _dur(lc),
                               len(lc.get("steps") or [])), reverse=True)
    cap = int(os.environ.get("SCP_LOCAL_WORKERS", "30"))
    n_w = int(workers) if workers else max(1, min(cap, len(items) or 1))
    # net-VPC A/B(vpc#a/b adopt)가 선택에 있으면 상주 VPC 2개가 슬롯을 상시
    # 점유한다 — 자체생성 가용 슬롯에서 차감해야 예측이 실제와 맞는다 (2026-07-13).
    net_standing = 2 if any((s.get("adopt") or "") in ("vpc#a", "vpc#b")
                            for lc in items for s in lc.get("steps", [])) else 0
    n_v = max(1, int(vpc_slots) - net_standing)
    # 공유 인프라 사전작업(prework) 모델 (오너 2026-07-15: "매번 사전 VPC 작업에서
    # subnet 생성이 5분 정도 소요되던데 예측은 제일 앞에 있어서 비교가 안 됨 —
    # 사전작업도 예측에 넣자"): 선택에 adopter가 있으면 러너가 lifecycle들보다
    # 먼저 공유 VPC+서브넷을 세우고 ACTIVE를 기다린다. 관측 창: VPC ACTIVE
    # ~75s + 서브넷 ACTIVE ~240s (run-543a: 백엔드가 같은 VPC 서브넷 전이를
    # 직렬화해 4~5분) + IGW/TGW 필요 시 가산. SCP_SIM_PREWORK_S 로 강제 가능.
    # 실측 대응: native_runner 가 lifecycle="shared-infra" start/end 이벤트를
    # 쏘므로 '예측 vs 실제' 패널에서 같은 행으로 겹쳐 보인다.
    from regression.scenarios import shared_infra as _shared
    _needs = _shared.shared_needs(only_ids=set(ids))
    prework = 0.0
    if _needs["any"]:
        try:
            prework = float(os.environ.get("SCP_SIM_PREWORK_S", "") or 0)
        except ValueError:
            prework = 0.0
        if prework <= 0:
            # 실측 우선 (오너 2026-07-16 "예측시간 현실화"): native_runner가
            # 쏘는 shared-infra lifecycle 스팬 실측(durations.json)이 있으면
            # 그걸 쓴다 — 정적 모델(75+240+igw60+tgw120=495s)은 IGW/TGW
            # 프로비전이 실제로는 subnet 대기와 겹쳐 돌아 이중계산이었다
            # (a690=260s, e68b=262.5s 실측).
            prework = float(dur.get("shared-infra", 0.0))
        if prework <= 0:
            prework = (75.0 + 240.0
                       + (60.0 if _needs["igw"] else 0.0)
                       + (120.0 if _needs["tgw"] else 0.0))
    # 자체-VPC(adopt 마커 0) 시나리오는 공유 인프라가 필요 없으니 t=0에 즉시
    # 출발 — adopter만 사전작업 종료를 기다린다 (오너 2026-07-16: "vpc 생성하는
    # 시나리오들은 시작부터 만들어도 되는거 아닌가? ske 등"). SKE(임계경로)가
    # 사전작업 ~4.3분을 겪지 않아 makespan이 그만큼 준다. 실행기(native_runner)
    # 도 동일 게이트로 정렬.
    wfree = [0.0] * n_w
    vfree = [0.0] * n_v
    def _adopts_shared(lc: dict) -> bool:
        return any(s.get("adopt") for s in lc.get("steps", []))
    bars = []
    if prework > 0:
        bars.append({"id": "shared-infra", "w": 0, "s": 0.0,
                     "e": round(prework, 1), "vpc": True, "measured": False,
                     "prework": True})
    for lc in items:
        d = _dur(lc)
        earliest = prework if _adopts_shared(lc) else 0.0
        wi = min(range(n_w), key=lambda i: wfree[i])
        start = max(wfree[wi], earliest)
        v = _self_vpc(lc)
        if v:
            vi = min(range(n_v), key=lambda i: vfree[i])
            start = max(start, vfree[vi])
            vfree[vi] = start + d
        wfree[wi] = start + d
        bars.append({"id": lc["id"], "w": wi, "s": round(start, 1),
                     "e": round(start + d, 1), "vpc": v,
                     "measured": dur.get(lc["id"], 0.0) > 0})
    return {"workers": n_w, "vpc_slots": n_v,
            "makespan_s": max((b["e"] for b in bars), default=0.0),
            "bars": bars}
