"""Offline tests — 실행 화면 동영상 (run-screen recording, 2026-10-20).

Covers:
  * tools/record_console.py pure helpers — which run to record, size/id validation,
    the console URL the recorder opens.
  * controlplane/recording.py — enable switch, console base URL from the ASGI scope,
    run-view fields (video / video_status), detached spawn (fake Popen) and the
    "never fail the run" paths (disabled / unavailable / spawn error).
  * console_api — ``/api/runs`` + ``/api/runs/{rid}`` carry the video fields,
    ``/api/runs/{rid}/video`` streams the file (404 + status otherwise, path-safe id),
    and ``POST /api/run`` hands the admitted run to ``recording.start`` with the
    recorder's base URL.
  * console2 front-end — the automation hook + ▶ 영상 / ● REC rendering exist.
  * end-to-end (skipped unless Playwright + Chromium are usable, or when
    SCP_VIDEO_E2E=0): an in-process uvicorn on a free port, one simulate run
    (zero cloud calls), a real WebM out.

Hermetic: no network beyond 127.0.0.1, no credentials; all file IO under tmp_path.
"""
from __future__ import annotations

import io
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from controlplane import recording
from tools import record_console as rc

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_recording_state():
    recording.reset_cache()
    yield
    recording.reset_cache()


# --------------------------------------------------------------------------- #
# tools/record_console.py — pure helpers
# --------------------------------------------------------------------------- #
def test_pick_run_prefers_active_real_run_over_scans():
    runs = [  # /api/runs order = newest first
        {"id": "owned-1", "kind": "owned", "status": "running"},
        {"id": "sim-1", "kind": "simulate", "status": "running"},
        {"id": "live-1", "kind": "lifecycle", "status": "queued"},
        {"id": "old", "kind": "lifecycle", "status": "done"},
    ]
    assert rc.pick_run(runs)["id"] == "sim-1"          # newest active real run wins
    assert rc.pick_run(runs[2:])["id"] == "live-1"     # queued counts as active
    assert rc.pick_run([runs[0], runs[3]])["id"] == "owned-1"   # only a scan is active → still something
    assert rc.pick_run([runs[3]]) is None
    assert rc.pick_run(runs, "old")["id"] == "old"     # explicit id regardless of status
    assert rc.pick_run(runs, "nope") is None


def test_parse_size_rounds_to_even_and_rejects_garbage():
    assert rc.parse_size("1440x900") == (1440, 900)
    assert rc.parse_size(" 1281 X 801 ") == (1280, 800)
    for bad in ("1440", "axb", "", "99x99"):
        with pytest.raises(ValueError):
            rc.parse_size(bad)


def test_default_out_and_rid_validation(tmp_path):
    assert rc.default_out("20261020-141114-a1f2", tmp_path) == tmp_path / "20261020-141114-a1f2.webm"
    assert rc.valid_rid("local-1760000000000-1")
    for bad in ("", "../x", "a/b", "-lead", "x" * 130):
        assert not rc.valid_rid(bad)
        with pytest.raises(ValueError):
            rc.default_out(bad, tmp_path)


def test_console_url_is_the_bare_embedded_console():
    assert rc.console_url("http://127.0.0.1:8800/") == "http://127.0.0.1:8800/testing/console/?embed=1"


def test_cli_rejects_bad_size_and_rid(capsys):
    assert rc.main(["--size", "big"]) == 2
    assert rc.main(["--run", "../etc", "--url", "http://127.0.0.1:1"]) == 2


# --------------------------------------------------------------------------- #
# controlplane/recording.py
# --------------------------------------------------------------------------- #
def test_enabled_env_switch():
    assert recording.enabled({}) is True
    assert recording.enabled({"PLATFORM_RECORD_VIDEO": "auto"}) is True
    assert recording.enabled({"PLATFORM_RECORD_VIDEO": "true"}) is True
    for off in ("false", "0", "OFF", "no", " disabled "):
        assert recording.enabled({"PLATFORM_RECORD_VIDEO": off}) is False


def test_base_url_from_scope_prefers_override_then_bound_port():
    assert recording.base_url_from_scope({"server": ("0.0.0.0", 8811)}, {}) == "http://127.0.0.1:8811"
    assert recording.base_url_from_scope({}, {}) == "http://127.0.0.1:8800"
    assert recording.base_url_from_scope(None, {}) == "http://127.0.0.1:8800"
    assert recording.base_url_from_scope({"server": ("x", 1)}, {"PLATFORM_RECORD_URL": "http://host:9/"}) == "http://host:9"


class _FakeProc:
    def __init__(self, alive=True, pid=4242):
        self.alive, self.pid = alive, pid

    def poll(self):
        return None if self.alive else 0


def test_view_and_status(tmp_path):
    rid = "20261020-000000-abcd"
    assert recording.status(rid, tmp_path) == "none"
    assert recording.view(rid, tmp_path) == {}
    recording._PROCS[rid] = _FakeProc(alive=True)
    assert recording.status(rid, tmp_path) == "recording"
    assert recording.view(rid, tmp_path) == {"video_status": "recording"}
    recording.video_path(rid, tmp_path).write_bytes(b"\x1a\x45\xdf\xa3 webm")
    assert recording.status(rid, tmp_path) == "ready"
    assert recording.view(rid, tmp_path) == {"video": f"/api/runs/{rid}/video", "video_status": "ready"}
    # an empty file is not a video
    recording.video_path("empty", tmp_path).write_bytes(b"")
    assert recording.status("empty", tmp_path) == "none"


def test_start_disabled_or_unavailable_never_spawns(tmp_path, monkeypatch):
    calls = []
    popen = lambda cmd, **kw: calls.append(cmd) or _FakeProc()      # noqa: E731
    monkeypatch.setenv("PLATFORM_RECORD_VIDEO", "false")
    assert recording.start("r1", "http://127.0.0.1:1", video_dir=tmp_path, popen=popen, probe=lambda: (True, "ok")) is None
    monkeypatch.setenv("PLATFORM_RECORD_VIDEO", "auto")
    logs = []
    assert recording.start("r1", "http://127.0.0.1:1", video_dir=tmp_path, popen=popen,
                           probe=lambda: (False, "no chromium"), log=logs.append) is None
    assert recording.start("r2", "http://127.0.0.1:1", video_dir=tmp_path, popen=popen, log=logs.append) is None
    assert calls == []
    assert len(logs) == 1 and "no chromium" in logs[0]           # unavailable is logged ONCE
    assert recording.availability() == (False, "no chromium")  # probe result is cached


def test_start_spawns_detached_recorder_once_per_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_RECORD_VIDEO", "auto")
    monkeypatch.setenv("PLATFORM_RECORD_SIZE", "1600x1000")
    monkeypatch.setenv("PLATFORM_RECORD_TOUR", "0")
    seen = []

    def popen(cmd, **kw):
        seen.append((cmd, kw))
        return _FakeProc(alive=True, pid=77)

    rid = "20261020-141114-a1f2"
    proc = recording.start(rid, "http://127.0.0.1:8811", video_dir=tmp_path, popen=popen,
                           probe=lambda: (True, "chromium 141"), log=lambda s: None)
    assert proc is not None and proc.pid == 77
    cmd, kw = seen[0]
    assert cmd[:3] == [os.sys.executable, "-m", "tools.record_console"]
    assert cmd[cmd.index("--run") + 1] == rid
    assert cmd[cmd.index("--url") + 1] == "http://127.0.0.1:8811"
    assert cmd[cmd.index("--out") + 1] == str(tmp_path / f"{rid}.webm")
    assert cmd[cmd.index("--size") + 1] == "1600x1000" and cmd[cmd.index("--tour") + 1] == "0"
    assert "--quiet" in cmd
    if os.name == "posix":
        assert kw["start_new_session"] is True                  # survives a server restart
    assert kw["cwd"] == str(ROOT) and str(ROOT) in kw["env"]["PYTHONPATH"]
    assert recording.log_path(rid, tmp_path).exists()          # recorder output goes to <rid>.video.log
    # same run again while the recorder is alive → the live process, no second spawn
    assert recording.start(rid, "http://127.0.0.1:8811", video_dir=tmp_path, popen=popen) is proc
    assert len(seen) == 1
    assert recording.view(rid, tmp_path) == {"video_status": "recording"}


def test_start_spawn_error_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_RECORD_VIDEO", "auto")

    def boom(cmd, **kw):
        raise OSError("no such interpreter")

    logs = []
    assert recording.start("r9", "http://127.0.0.1:1", video_dir=tmp_path, popen=boom,
                           probe=lambda: (True, "ok"), log=logs.append) is None
    assert any("no such interpreter" in l for l in logs)


# --------------------------------------------------------------------------- #
# console_api — video fields, stream endpoint, run-start hook
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient
    from controlplane.app import app
    return TestClient(app)


def test_api_runs_carry_video_fields_and_stream_the_file(tmp_path, monkeypatch):
    from tools import console2_server as c2

    monkeypatch.setattr(recording, "VIDEO_DIR", tmp_path)
    rid = "20261020-150000-vid1"
    rec = {"id": rid, "kind": "simulate", "mode": "simulate", "status": "done", "rc": 0,
           "lifecycle_ids": ["x"], "started": time.time() - 30, "ended": time.time() - 1,
           "log": str(tmp_path / "x.log"), "events": str(tmp_path / "x.events.jsonl")}
    Path(rec["events"]).write_text("", encoding="utf-8")
    with c2._LOCK:
        c2._RUNS[rid] = rec
    client = _client()
    try:
        row = next(r for r in client.get("/api/runs").json()["runs"] if r["id"] == rid)
        assert "video" not in row and "video_status" not in row
        r404 = client.get(f"/api/runs/{rid}/video")
        assert r404.status_code == 404 and r404.json()["video_status"] == "none"
        # the recorder finished → file present → fields + stream
        body = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
        recording.video_path(rid).write_bytes(body)
        row = next(r for r in client.get("/api/runs").json()["runs"] if r["id"] == rid)
        assert row["video"] == f"/api/runs/{rid}/video" and row["video_status"] == "ready"
        assert client.get(f"/api/runs/{rid}").json()["video"] == f"/api/runs/{rid}/video"
        ok = client.get(f"/api/runs/{rid}/video")
        assert ok.status_code == 200 and ok.headers["content-type"].startswith("video/webm")
        assert ok.content == body
        # path-safe: a non-id never reaches the filesystem
        assert client.get("/api/runs/..%2F..%2Fetc/video").status_code == 404
        assert client.get("/api/runs/no-such-run/video").status_code == 404
    finally:
        with c2._LOCK:
            c2._RUNS.pop(rid, None)


def test_api_run_start_hands_the_admitted_run_to_the_recorder(monkeypatch):
    from tools import console2_server as c2

    started = []
    monkeypatch.setattr(recording, "start", lambda rid, base, **kw: started.append((rid, base)))
    admitted = []
    monkeypatch.setattr(c2, "_admit_or_queue", lambda rec, worker: admitted.append(rec))  # no real worker
    client = _client()
    r = client.post("/api/run", json={"mode": "simulate", "lifecycle_ids": ["gen-vpc-endpoint"]})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    try:
        assert admitted and admitted[0]["id"] == rid
        assert started == [(rid, recording.base_url_from_scope({"server": ("testserver", 80)}))]
        assert started[0][1].startswith("http://127.0.0.1:")
    finally:
        with c2._LOCK:
            c2._RUNS.pop(rid, None)


def test_api_run_start_survives_recorder_failure(monkeypatch):
    from tools import console2_server as c2

    def boom(*a, **k):
        raise RuntimeError("recorder exploded")

    monkeypatch.setattr(recording, "start", boom)
    monkeypatch.setattr(c2, "_admit_or_queue", lambda rec, worker: None)
    client = _client()
    r = client.post("/api/run", json={"mode": "simulate", "lifecycle_ids": ["gen-vpc-endpoint"]})
    assert r.status_code == 202, r.text
    with c2._LOCK:
        c2._RUNS.pop(r.json()["id"], None)


# --------------------------------------------------------------------------- #
# console2 front-end — hook + rendering (string-level, like test_console2.py)
# --------------------------------------------------------------------------- #
def test_console2_js_exposes_hook_and_renders_video_links():
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    assert "window.__c2 = { go, loadRunIntoReport, setDetailTab" in js      # recorder entry point
    assert "function vidLink(r)" in js and 'class="vidlink"' in js and 'class="vidrec"' in js
    assert "${vidLink(r)}" in js.split("function runRowHtml", 1)[1][:3000]  # history rows
    assert "renderDoneCardVideo();" in js.split("function renderDoneCard(", 1)[1][:4000]
    # ▶ 영상 click must not rebind the row
    assert js.count('closest("a.vidlink")') >= 2
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    assert ".vidlink{" in css and ".vidrec{" in css


# --------------------------------------------------------------------------- #
# tools/record_demo.py — the captioned walkthrough (pure parts; the film itself needs a browser)
# --------------------------------------------------------------------------- #
def test_record_demo_cli_validation_and_overlay_escaping():
    from tools import record_demo as rd

    assert rd.main(["--service", "virtualserver"]) == 2              # must be cat/svc
    assert rd.main(["--size", "huge"]) == 2
    assert rd._esc("a<b>&") == "a&lt;b&gt;&amp;"                      # captions go into innerHTML
    assert rd._svc_label("compute/virtualserver") == "virtualserver"
    assert "modeling" in rd.__doc__.lower() or "Modeling" in rd.__doc__
    # ④ never starts a LIVE run: the only /api/run body the tour can send is simulate
    src = (ROOT / "tools" / "record_demo.py").read_text(encoding="utf-8")
    assert '"mode": "live"' not in src and '"mode": "simulate"' in src


# --------------------------------------------------------------------------- #
# end-to-end — a real recording of a simulate run (skipped without a browser)
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_E2E_OFF = os.environ.get("SCP_VIDEO_E2E", "1").strip().lower() in ("0", "false", "off")
_PW_OK, _PW_WHY = (False, "SCP_VIDEO_E2E=0") if _E2E_OFF else rc.playwright_available()


@pytest.mark.skipif(not _PW_OK, reason=f"browser recording unavailable: {_PW_WHY}")
def test_e2e_records_a_simulate_run_to_webm(tmp_path, monkeypatch):
    import uvicorn
    from controlplane.app import app

    monkeypatch.setenv("PLATFORM_RECORD_VIDEO", "false")       # the test records; no auto-spawn
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                rc.fetch_runs(base)
                break
            except Exception:                                   # noqa: BLE001 — not up yet
                time.sleep(0.2)
        else:
            pytest.fail("in-process uvicorn did not come up")
        rid = rc.start_simulate(base, ["gen-vpc-endpoint"])      # replays the plan, zero cloud calls
        out = tmp_path / f"{rid}.webm"
        log = io.StringIO()
        got = rc.record(base, rid, out, size=(1280, 800), tour_s=2.0, poll_s=1.0, tail_s=2.0,
                        log=lambda s: log.write(s + "\n"))
        assert got == out and out.exists() and out.stat().st_size > 10_000, log.getvalue()
        assert out.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"       # EBML magic = WebM container
        assert "녹화 시작" in log.getvalue() and "저장:" in log.getvalue()
        assert rc.fetch_run(base, rid)["status"] == "done"
    finally:
        server.should_exit = True
        th.join(timeout=15)
