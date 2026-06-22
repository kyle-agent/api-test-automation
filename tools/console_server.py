#!/usr/bin/env python3
"""Local execution server for the platform console (option 3 — zero dependency).

Run this on a machine that has the repo + working SCP creds (.env). It serves the
console AND executes selected lifecycles locally, so the console's "실행 ▶" button
actually runs (no GitHub round-trip, no FastAPI).

    python tools/console_server.py                 # http://127.0.0.1:9000/
    PORT=8800 python tools/console_server.py        # override the default port

Flow: open the console -> Plan tab -> pick service(s)/combo -> 실행 ▶ -> the console
POSTs the selected lifecycle ids to /api/run -> the server runs
``pytest tests/crud -m crud -n N`` with ``SCP_CRUD_IDS=<ids>`` (the conftest exact-id
allowlist), then a tag-scoped reconciler sweep -> the console polls /api/run/<id>.

Safety: mutations/destructive gates are set per run from the request (same opt-in as
chat-heavy). Stdlib only (http.server) so it runs straight after ``git pull``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "poc" / "scenario-viz"            # console.html + data/ + assets/ + kdocs/
RUN_DIR = ROOT / "reports" / "console-runs"
PORT = int(os.environ.get("PORT", "9000"))

_RUNS: dict[str, dict] = {}                    # id -> run record (in-memory)
_LOCK = threading.Lock()
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


def _pytest_cmd(crud_ids: list[str], parallel: int) -> list[str]:
    n = str(max(1, int(parallel or 2)))
    return [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
            "-n", n, "-o", "addopts=", "-q"]


def _provision_shared(env: dict, f) -> dict:
    """Mirror chat-heavy.yml's 'Provision shared VPC' step. Under ``-n`` the tests/crud
    ``shared_vpc`` fixture yields ``{}`` for xdist workers WITHOUT ``SCP_SHARED_VPC_ID``
    (conftest.py — never provision per-worker), so pure ADOPTERS (SKE/MySQL clusters)
    IB-049-skip. We provision ONE shared VPC+subnet up front and pass its ids in, so
    adopters adopt it instead of skipping. Best-effort: on failure adopters still skip
    (self-creating lifecycles run regardless), exactly as before this step existed.

    Returns the SCP_SHARED_* env to merge into the pytest run (empty if provision failed)."""
    f.write("\n=== provision shared VPC (adopters need this under -n) ===\n")
    f.flush()
    out = subprocess.run(
        [sys.executable, "-m", "regression.scenarios.shared_infra", "--provision"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    f.write(out.stdout or "")
    shared = {}
    for line in (out.stdout or "").splitlines():
        if line.startswith("SCP_SHARED_") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                shared[k.strip()] = v.strip()
    if shared.get("SCP_SHARED_VPC_ID"):
        shared["SCP_VPC_SHARED_RESERVED"] = "1"
        f.write(f"\n[provision] shared VPC ready: {shared['SCP_SHARED_VPC_ID']}\n")
    else:
        f.write("\n[provision] no shared VPC id — adopter lifecycles will skip "
                "(self-creating lifecycles still run)\n")
    f.flush()
    return shared


def _teardown_shared(env: dict, shared: dict, f) -> None:
    """Delete EXACTLY the shared VPC+subnet we provisioned (by id), symmetric to
    _provision_shared. Precise (targets our own ids), so it's safe regardless of TTL
    and never touches a concurrent run — unlike the tag-scoped reconciler sweep, which
    skips not-yet-expired owned resources and so would leak this just-made VPC."""
    if not shared.get("SCP_SHARED_VPC_ID"):
        return
    f.write("\n=== teardown shared VPC (precise, by id) ===\n")
    f.flush()
    subprocess.run(
        [sys.executable, "-m", "regression.scenarios.shared_infra", "--teardown"],
        cwd=str(ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT)


def _run_worker(rec: dict) -> None:
    """Provision a shared VPC (so adopters don't skip under -n), run the selected
    lifecycles, tear that VPC down by id, then a tag-scoped reconciler sweep as the
    catch-all safety net. Output streams to the run's log."""
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "SCP_CRUD_IDS": ",".join(rec["crud_ids"]),
           "SCP_ALLOW_MUTATIONS": "true", "SCP_ALLOW_DESTRUCTIVE": "true",
           "SCP_RUN_HEAVY": "true" if rec["heavy"] else "false"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console run {rec['id']}  crud_ids={rec['crud_ids']}  "
                    f"parallel={rec['parallel']} heavy={rec['heavy']}\n")
            f.flush()
            # adopters (SKE/MySQL) need a shared VPC under -n; self-creators don't care.
            shared = _provision_shared(env, f) if rec["heavy"] else {}
            f.write("\n=== pytest ===\n")
            f.flush()
            rc = subprocess.run(_pytest_cmd(rec["crud_ids"], rec["parallel"]),
                                cwd=str(ROOT), env={**env, **shared},
                                stdout=f, stderr=subprocess.STDOUT).returncode
            _teardown_shared(env, shared, f)
            f.write("\n=== reconciler sweep (cleanup) ===\n")
            f.flush()
            sweep_env = {**env, "SCP_SWEEP_NOWAIT": "true"}
            subprocess.run([sys.executable, "-m", "cleanup.reconciler"],
                           cwd=str(ROOT), env=sweep_env, stdout=f, stderr=subprocess.STDOUT)
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
    except Exception as exc:  # noqa: BLE001 — surface the failure to the UI, never crash the server
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _cleanup_worker(rec: dict) -> None:
    """FORCE cleanup: reconciler sweep with SCP_SWEEP_IGNORE_TTL=true, so it deletes
    EVERY owned (owner=apitest) resource regardless of TTL — for "a run broke midway,
    just delete everything we made". Same tag-scoped ownership guard as always (never
    touches resources we don't own); only the not-yet-expired guard is overridden."""
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "SCP_ALLOW_MUTATIONS": "true", "SCP_ALLOW_DESTRUCTIVE": "true",
           "SCP_SWEEP_IGNORE_TTL": "true", "SCP_SWEEP_NOWAIT": "true"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console FORCE cleanup {rec['id']}\n\n"
                    "=== reconciler sweep (FORCE: delete ALL owned, ignore TTL) ===\n")
            f.flush()
            rc = subprocess.run([sys.executable, "-m", "cleanup.reconciler"],
                                cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _verify_worker(rec: dict) -> None:
    """READ-ONLY verification: cleanup.verify_clean lists (never deletes) and reports how
    many owned resources still survive per service — proof the cleanup actually worked."""
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SCP_ALLOW_DESTRUCTIVE": "false"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console cleanup VERIFY {rec['id']} (read-only owned-resource inventory)\n\n"
                    "=== verify_clean (no deletes; counts surviving owned resources) ===\n")
            f.flush()
            rc = subprocess.run([sys.executable, "-m", "cleanup.verify_clean"],
                                cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _new_rec(kind: str, **extra) -> dict:
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "kind": kind, "status": "running",
           "crud_ids": extra.get("crud_ids", []), "parallel": extra.get("parallel", 2),
           "heavy": extra.get("heavy", False), "started": time.time(),
           "ended": None, "rc": None, "log": str(RUN_DIR / f"{rid}.log")}
    with _LOCK:
        _RUNS[rid] = rec
    return rec


def _start(kind: str, worker, **extra) -> dict:
    rec = _new_rec(kind, **extra)
    threading.Thread(target=worker, args=(rec,), daemon=True).start()
    return rec


def _start_run(crud_ids: list[str], parallel: int, heavy: bool) -> dict:
    return _start("lifecycle", _run_worker, crud_ids=crud_ids, parallel=parallel, heavy=heavy)


def _tail(path: str, n: int = 80) -> str:
    try:
        return "".join(open(path, encoding="utf-8").readlines()[-n:])
    except Exception:  # noqa: BLE001
        return ""


def _summarize(kind: str, log: str) -> str:
    """One-line headline per run kind, shown on the run row in the console."""
    import re
    if kind == "verify":
        if "NONE — every swept collection is empty" in log:
            return "✅ clean — owned survivors: 0"
        m = re.search(r"TOTAL owned survivors across all collections:\s*(\d+)", log)
        if m:
            return "✅ clean — owned survivors: 0" if m.group(1) == "0" else f"⚠️ {m.group(1)} owned survivors"
        return ""
    if kind == "cleanup":
        m = re.findall(r"sweep done:\s*(\d+) resource\(s\) deleted", log)
        return f"🧹 {sum(int(x) for x in m)} resource(s) deleted" if m else ""
    m = re.findall(r"\d+ (?:passed|failed|skipped|error)[^\n]*", log)  # lifecycle: pytest summary
    return m[-1] if m else ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/console.html", "/index.html"):
            return self._file(WEB / "console.html")
        if p == "/api/runs":
            with _LOCK:
                return self._json(200, {"runs": [self._rec_view(r) for r in
                                                  sorted(_RUNS.values(), key=lambda x: x["started"], reverse=True)]})
        if p.startswith("/api/runs/"):
            rid = p.rsplit("/", 1)[-1]
            with _LOCK:
                rec = _RUNS.get(rid)
            if not rec:
                return self._json(404, {"error": "no such run"})
            return self._json(200, self._rec_view(rec, full=True))
        # static files under the console bundle (data/, assets/, kdocs/)
        rel = p.lstrip("/")
        target = (WEB / rel).resolve()
        if str(target).startswith(str(WEB.resolve())) and target.is_file():
            return self._file(target)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        # FORCE cleanup (delete all owned) + read-only verify — no request body needed.
        if p == "/api/cleanup":
            return self._json(202, self._rec_view(_start("cleanup", _cleanup_worker)))
        if p == "/api/verify":
            return self._json(202, self._rec_view(_start("verify", _verify_worker)))
        if p != "/api/run":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return self._json(400, {"error": "bad json"})
        ids = [str(x).strip() for x in (body.get("crud_ids") or []) if str(x).strip()]
        if not ids:
            return self._json(400, {"error": "no crud_ids"})
        rec = _start_run(ids, int(body.get("parallel") or 2), bool(body.get("heavy", True)))
        self._json(202, self._rec_view(rec))

    def _rec_view(self, rec: dict, full: bool = False) -> dict:
        v = {k: rec.get(k) for k in
             ("id", "kind", "status", "crud_ids", "parallel", "heavy", "rc", "started", "ended")}
        full_log = open(rec["log"], encoding="utf-8").read() if Path(rec["log"]).exists() else ""
        v["summary"] = _summarize(rec.get("kind", "lifecycle"), full_log)
        if full:
            v["log"] = _tail(rec["log"], 200)
        return v

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except Exception:  # noqa: BLE001
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", _CT.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    if not (WEB / "console.html").exists():
        sys.exit(f"console not found at {WEB} — run from the repo root")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"platform console + executor: http://127.0.0.1:{PORT}/")
    print("  select services -> 실행 ▶ runs pytest tests/crud with SCP_CRUD_IDS locally")
    print(f"  run logs: {RUN_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
