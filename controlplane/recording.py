"""Run-screen video recording — the control-plane side of ``tools/record_console.py``.

When the console admits a run (``POST /api/run``, LIVE or simulate) the spine spawns
``python -m tools.record_console --run <rid>`` **detached**; it opens the console in a
headless Chromium, binds to that run and records the 실행/리포트 screen until the run
ends → ``reports/console2-runs/<rid>.webm`` (gitignored, next to the run's .log /
.events.jsonl). ``view()`` adds ``video`` (URL) / ``video_status`` to the run views so
console2 shows ▶ 영상 on the history row and the 종료 카드; ``GET /api/runs/<rid>/video``
streams the file.

Opt-out / availability (never fails a run — recording is best-effort):
  * ``PLATFORM_RECORD_VIDEO=false`` (0/off/no) disables it. Default ``auto`` = record
    when Playwright + Chromium are usable, probed ONCE per server process with
    ``tools.record_console --check`` (result logged once; ``true`` behaves the same —
    there is nothing to force if the browser is missing).
  * ``PLATFORM_RECORD_URL`` overrides the console URL the recorder opens. Default:
    ``http://127.0.0.1:<bound port>`` from the ASGI scope — the recorder runs on the
    server host, so the externally visible host name is irrelevant.
  * ``PLATFORM_RECORD_SIZE`` (WxH, default 1440x900) · ``PLATFORM_RECORD_TOUR`` (초, 상세
    탭 순환 주기, 0 = 끄기) · ``PLATFORM_RECORD_MAX_MINUTES``.

No web-framework dependency — ``console_api`` is a thin caller, so this module is
unit-testable without FastAPI (same split as ``local_executor``).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "reports" / "console2-runs"            # == tools.console2_server.RUN_DIR
CHECK_TIMEOUT_S = 90.0                                   # first chromium launch can be slow
_FALSE = ("0", "false", "off", "no", "disabled")
_RID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")   # run ids: 20260921-155909-4beb / local-…

_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}
_AVAIL: tuple[bool, str] | None = None                   # probed once per process
_LOGGED_UNAVAILABLE = False


def valid_rid(rid: str) -> bool:
    """Path-safe run id (a URL segment is joined into VIDEO_DIR)."""
    return bool(_RID_RE.match(rid or ""))


def video_path(rid: str, video_dir: Path | None = None) -> Path:
    return (video_dir or VIDEO_DIR) / f"{rid}.webm"


def log_path(rid: str, video_dir: Path | None = None) -> Path:
    return (video_dir or VIDEO_DIR) / f"{rid}.video.log"


def enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("PLATFORM_RECORD_VIDEO", "auto")).strip().lower() not in _FALSE


def base_url_from_scope(scope: dict | None, env: dict | None = None) -> str:
    """Where the recorder (on THIS host) reaches the console: env override, else the
    bound port from the ASGI ``server`` tuple, else the default 8800."""
    env = os.environ if env is None else env
    override = str(env.get("PLATFORM_RECORD_URL", "")).strip()
    if override:
        return override.rstrip("/")
    port = 8800
    server = (scope or {}).get("server")
    if server and len(server) >= 2 and server[1]:
        port = int(server[1])
    return f"http://127.0.0.1:{port}"


def reset_cache() -> None:
    """Tests only: forget the availability probe + spawned processes."""
    global _AVAIL, _LOGGED_UNAVAILABLE
    with _LOCK:
        _AVAIL = None
        _LOGGED_UNAVAILABLE = False
        _PROCS.clear()


def availability(probe=None) -> tuple[bool, str]:
    """(ok, detail) — cached. ``probe`` (tests) replaces the subprocess check."""
    global _AVAIL
    with _LOCK:
        if _AVAIL is not None:
            return _AVAIL
    if probe is None:
        probe = _probe_subprocess
    try:
        result = probe()
    except Exception as exc:                              # noqa: BLE001 — never raise
        result = (False, f"probe failed: {exc}")
    with _LOCK:
        _AVAIL = result
    return result


def _probe_subprocess() -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "tools.record_console", "--check"]
    try:
        cp = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                            timeout=CHECK_TIMEOUT_S, env=_child_env())
    except subprocess.TimeoutExpired:
        return False, f"--check timed out after {CHECK_TIMEOUT_S:.0f}s"
    out = (cp.stdout or cp.stderr or "").strip().splitlines()
    detail = out[-1] if out else f"exit {cp.returncode}"
    return cp.returncode == 0, detail


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def start(rid: str, base_url: str, *, video_dir: Path | None = None, log=print,
          popen=None, probe=None) -> subprocess.Popen | None:
    """Spawn the detached recorder for ``rid``. Returns the process, or None when
    disabled / unavailable / already recording. Never raises (best-effort)."""
    global _LOGGED_UNAVAILABLE
    if not enabled():
        return None
    ok, detail = availability(probe)
    if not ok:
        if not _LOGGED_UNAVAILABLE:
            _LOGGED_UNAVAILABLE = True
            log(f"[recording] 화면 녹화 비활성 — {detail} (PLATFORM_RECORD_VIDEO=false 로 이 안내를 끌 수 있음)")
        return None
    with _LOCK:
        live = _PROCS.get(rid)
        if live is not None and live.poll() is None:
            return live
    vdir = video_dir or VIDEO_DIR
    vdir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "tools.record_console", "--url", base_url, "--run", rid,
           "--out", str(video_path(rid, vdir)), "--quiet",
           "--size", os.environ.get("PLATFORM_RECORD_SIZE", "1440x900"),
           "--tour", os.environ.get("PLATFORM_RECORD_TOUR", "20"),
           "--max-minutes", os.environ.get("PLATFORM_RECORD_MAX_MINUTES", "240")]
    try:
        logfh = open(log_path(rid, vdir), "ab")
        kwargs = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=logfh, stderr=subprocess.STDOUT,
                      env=_child_env(), close_fds=True)
        if os.name == "posix":
            kwargs["start_new_session"] = True            # survives server restart; not in our pgid
        else:                                             # pragma: no cover — Windows hosts
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = (popen or subprocess.Popen)(cmd, **kwargs)
        logfh.close()
    except Exception as exc:                              # noqa: BLE001 — never block the run
        log(f"[recording] 녹화 프로세스를 띄우지 못했습니다 ({exc}) — run 은 그대로 진행")
        return None
    with _LOCK:
        _PROCS[rid] = proc
    log(f"[recording] run {rid} 화면 녹화 시작 (pid {proc.pid}) → {video_path(rid, vdir)}")
    return proc


def status(rid: str, video_dir: Path | None = None) -> str:
    """``ready`` (file exists) · ``recording`` (recorder alive) · ``none``."""
    p = video_path(rid, video_dir)
    try:
        if p.exists() and p.stat().st_size > 0:
            return "ready"
    except OSError:
        pass
    with _LOCK:
        proc = _PROCS.get(rid)
    if proc is not None and proc.poll() is None:
        return "recording"
    return "none"


def view(rid: str, video_dir: Path | None = None) -> dict:
    """Fields merged into a run view: ``video`` (stream URL) once the file exists,
    ``video_status: recording`` while the recorder runs, nothing otherwise."""
    st = status(rid, video_dir)
    if st == "ready":
        return {"video": f"/api/runs/{rid}/video", "video_status": "ready"}
    if st == "recording":
        return {"video_status": "recording"}
    return {}
