#!/usr/bin/env python3
"""Local execution server for the platform console (option 3 — zero dependency).

Run this on a machine that has the repo + working SCP creds (.env). It serves the
console AND executes selected lifecycles locally, so the console's "실행 ▶" button
actually runs (no GitHub round-trip, no FastAPI).

    python tools/console_server.py                 # http://127.0.0.1:8800/
    PORT=9000 python tools/console_server.py

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
PORT = int(os.environ.get("PORT", "8800"))

_RUNS: dict[str, dict] = {}                    # id -> run record (in-memory)
_LOCK = threading.Lock()
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


def _pytest_cmd(crud_ids: list[str], parallel: int) -> list[str]:
    n = str(max(1, int(parallel or 2)))
    return [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
            "-n", n, "-o", "addopts=", "-q"]


def _run_worker(rec: dict) -> None:
    """Provision is handled by the tests/crud shared_vpc fixture; we run the selected
    lifecycles then a tag-scoped reconciler sweep. Output streams to the run's log."""
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "SCP_CRUD_IDS": ",".join(rec["crud_ids"]),
           "SCP_ALLOW_MUTATIONS": "true", "SCP_ALLOW_DESTRUCTIVE": "true",
           "SCP_RUN_HEAVY": "true" if rec["heavy"] else "false"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console run {rec['id']}  crud_ids={rec['crud_ids']}  "
                    f"parallel={rec['parallel']} heavy={rec['heavy']}\n\n=== pytest ===\n")
            f.flush()
            rc = subprocess.run(_pytest_cmd(rec["crud_ids"], rec["parallel"]),
                                cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
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


def _start_run(crud_ids: list[str], parallel: int, heavy: bool) -> dict:
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "status": "running", "crud_ids": crud_ids, "parallel": parallel,
           "heavy": heavy, "started": time.time(), "ended": None, "rc": None,
           "log": str(RUN_DIR / f"{rid}.log")}
    with _LOCK:
        _RUNS[rid] = rec
    threading.Thread(target=_run_worker, args=(rec,), daemon=True).start()
    return rec


def _tail(path: str, n: int = 80) -> str:
    try:
        return "".join(open(path, encoding="utf-8").readlines()[-n:])
    except Exception:  # noqa: BLE001
        return ""


def _summary(log: str) -> str:
    import re
    m = re.findall(r"\d+ (?:passed|failed|skipped|error)[^\n]*", log)
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
        v = {k: rec[k] for k in ("id", "status", "crud_ids", "parallel", "heavy", "rc", "started", "ended")}
        log = _tail(rec["log"], 200 if full else 1)
        v["summary"] = _summary(open(rec["log"], encoding="utf-8").read()) if Path(rec["log"]).exists() else ""
        if full:
            v["log"] = log
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
