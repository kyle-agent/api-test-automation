"""Record the console2 "실행 → 리포트" screen of a run as a WebM video.

"실제 테스트하는 화면" 을 남긴다 (오너 요청, 2026-10-20): headless Chromium(Playwright)이
콘솔 화면(``/testing/console/?embed=1``)을 열어 run 에 바인딩하고, run 이 끝날 때까지
화면을 녹화해 ``reports/console2-runs/<rid>.webm`` 으로 저장한다 (gitignored, run 의
.log/.events.jsonl 옆). 서버는 LIVE/simulate run 을 admit 할 때 이 스크립트를 백그라운드로
띄우고(``controlplane/recording.py``), 콘솔의 실행 기록 행·종료 카드에 ▶ 영상 링크를 단다.

Usage::
    python -m tools.record_console                        # 진행 중인 run 을 찾아 끝까지 녹화
    python -m tools.record_console --run <rid>            # 특정 run (대기/진행 중이면 끝까지, 끝났으면 리포트 화면 몇 초)
    python -m tools.record_console --wait 300             # run 이 시작될 때까지 최대 300초 기다렸다가 녹화
    python -m tools.record_console --simulate networking-vpc-subnet   # simulate run 을 띄워 녹화 (클라우드 호출 0 — 데모용)
    python -m tools.record_console --check                # Playwright + Chromium 사용 가능 여부 (exit 0/1)

Options:
    --url URL          콘솔 서버 (default http://127.0.0.1:8800)
    --out PATH         저장 경로 (default reports/console2-runs/<rid>.webm)
    --size WxH         화면·영상 크기 (default 1440x900)
    --tour SEC         리포트 상세 탭(리소스 → API → 로그) 순환 주기, 0 = 끄기 (default 20)
    --max-minutes N    이 시간이 지나면 run 이 안 끝나도 녹화를 마무리 (default 240)
    --quiet            진행 로그 없이 결과 경로만 출력

Prereq (선택 — 없어도 서버는 그대로 동작한다)::
    pip install -r controlplane/requirements-video.txt && python -m playwright install chromium

이 도구는 LIVE run 을 **시작하지 않는다** — 콘솔이 admit 한 run 을 녹화만 한다 (Hard Rule 1 의
opt-in 은 그대로 콘솔 선택 + pre-flight 확인). ``--simulate`` 만 run 을 띄우며 클라우드 호출이 없다.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "reports" / "console2-runs"          # == tools.console2_server.RUN_DIR
DEFAULT_URL = "http://127.0.0.1:8800"
DEFAULT_SIZE = (1440, 900)
ACTIVE = ("running", "queued")
#: 리포트 상세 탭 순환 순서 (console2.js setDetailTab 의 data-d 값)
TOUR_TABS = ("api", "log", "res")   # 자원 탭은 기본이라 API → 로그 → 자원 순으로 돈다
#: run 종료 뒤 종료 카드·최종 상태가 그려질 때까지 더 찍는 시간 (UI 폴링 2초 × 2 + 여유)
TAIL_S = 6.0
#: 서버 폴링이 이만큼 연속 실패하면(서버 종료) 녹화를 마무리한다
SERVER_GONE_S = 45.0
_RID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


# --------------------------------------------------------------------------- #
# pure helpers (offline-tested)
# --------------------------------------------------------------------------- #
def is_active(status: str | None) -> bool:
    return (status or "") in ACTIVE


def pick_run(runs: list[dict], rid: str | None = None) -> dict | None:
    """The run to record: ``rid`` if given (whatever its status), else the active
    (running/queued) run — a real ``lifecycle``/``simulate`` run before a scan/cleanup
    record, newest first (``/api/runs`` is already newest-first)."""
    if rid:
        return next((r for r in runs if r.get("id") == rid), None)
    active = [r for r in runs if is_active(r.get("status"))]
    for kinds in (("lifecycle", "simulate"), None):
        for r in active:
            if kinds is None or r.get("kind") in kinds:
                return r
    return None


def parse_size(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{3,5})\s*[xX×]\s*(\d{3,5})\s*", text or "")
    if not m:
        raise ValueError(f"--size 는 WxH 형식이어야 합니다 (예 1440x900): {text!r}")
    w, h = int(m.group(1)), int(m.group(2))
    return w - w % 2, h - h % 2                        # vp8: 짝수 크기가 안전


def valid_rid(rid: str) -> bool:
    return bool(_RID_RE.match(rid or ""))


def default_out(rid: str, run_dir: Path = RUN_DIR) -> Path:
    if not valid_rid(rid):
        raise ValueError(f"run id 형식이 아닙니다: {rid!r}")
    return run_dir / f"{rid}.webm"


def console_url(base_url: str) -> str:
    """The bare console (no spine nav) — the whole viewport is the run screen."""
    return base_url.rstrip("/") + "/testing/console/?embed=1"


# --------------------------------------------------------------------------- #
# HTTP (stdlib only — the recorder must not depend on the engine's packages)
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:      # noqa: S310 — localhost console
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:                                   # noqa: BLE001
            return exc.code, {"error": str(exc)}


def fetch_runs(base_url: str) -> list[dict]:
    return _get_json(base_url.rstrip("/") + "/api/runs").get("runs") or []


def fetch_run(base_url: str, rid: str) -> dict:
    return _get_json(base_url.rstrip("/") + "/api/runs/" + rid)


def start_simulate(base_url: str, lifecycle_ids: list[str]) -> str:
    """``POST /api/run mode=simulate`` — replays the real plan, zero cloud calls."""
    code, j = _post_json(base_url.rstrip("/") + "/api/run",
                         {"mode": "simulate", "lifecycle_ids": list(lifecycle_ids)})
    if code >= 300 or j.get("error") or not j.get("id"):
        raise RuntimeError(f"simulate run 시작 실패 (HTTP {code}): {j.get('error') or j}")
    return str(j["id"])


def wait_for_run(base_url: str, wait_s: float, poll_s: float = 2.0,
                 log=lambda s: None) -> dict | None:
    deadline = time.time() + wait_s
    while True:
        try:
            r = pick_run(fetch_runs(base_url))
        except Exception as exc:                            # noqa: BLE001 — server not up yet
            r = None
            log(f"[record] 서버 응답 없음 ({exc}) — 재시도")
        if r or time.time() >= deadline:
            return r
        time.sleep(poll_s)


# --------------------------------------------------------------------------- #
# Playwright
# --------------------------------------------------------------------------- #
def playwright_available() -> tuple[bool, str]:
    """(ok, detail) — importable AND a headless Chromium launches. Never raises."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:                                # noqa: BLE001
        return False, f"playwright 미설치 ({exc.__class__.__name__}) — pip install -r controlplane/requirements-video.txt"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ver = b.version
            b.close()
        return True, f"chromium {ver}"
    except Exception as exc:                                # noqa: BLE001
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return False, f"chromium 실행 실패 ({first}) — python -m playwright install chromium"


# JS run inside the console page via the automation hook console2.js exposes as
# ``window.__c2 = { go, loadRunIntoReport, setDetailTab }`` (the app itself is an IIFE).
_JS_READY = "!!(window.__c2 && typeof window.__c2.loadRunIntoReport === 'function' && document.getElementById('screen-run'))"
_JS_BIND = "(id) => { window.__c2.go('run'); window.__c2.loadRunIntoReport(id); }"
_JS_TAB = "(t) => { if (window.__c2 && window.__c2.setDetailTab) window.__c2.setDetailTab(t); }"


def record(base_url: str, rid: str, out: Path, *, size: tuple[int, int] = DEFAULT_SIZE,
           tour_s: float = 20.0, max_s: float = 240 * 60, poll_s: float = 2.0,
           tail_s: float = TAIL_S, log=print) -> Path:
    """Open the console, bind it to ``rid``, record until the run leaves
    running/queued (or ``max_s``), then save the WebM to ``out``. Returns ``out``."""
    from playwright.sync_api import sync_playwright

    base_url = base_url.rstrip("/")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    started = time.time()
    # 대기 큐에 있는 run 은 running 이 될 때까지 브라우저 없이 기다린다 — 몇 분짜리 "대기 중" 화면을
    # 영상에 남길 이유가 없다 (큐 → 실행 전환은 콘솔이 보여 준다). 그 사이 끝나면 리포트만 찍는다.
    waited = False
    while True:
        try:
            st = fetch_run(base_url, rid).get("status")
        except Exception as exc:                            # noqa: BLE001 — server hiccup
            if time.time() - started > SERVER_GONE_S:
                raise RuntimeError(f"콘솔 서버 응답 없음: {exc}") from exc
            st = "queued"
        if st != "queued" or time.time() - started > max_s:
            break
        if not waited:
            waited = True
            log(f"[record] run {rid} 은 대기 큐에 있음 — 실행이 시작되면 녹화합니다")
        time.sleep(min(poll_s, 5.0))
    tmp = Path(tempfile.mkdtemp(prefix="c2video-"))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": w, "height": h},
                record_video_dir=str(tmp), record_video_size={"width": w, "height": h},
                locale="ko-KR", timezone_id="Asia/Seoul")
            page = context.new_page()
            video = page.video
            page.goto(console_url(base_url), wait_until="domcontentloaded")
            page.wait_for_function(_JS_READY, timeout=60_000)
            page.wait_for_timeout(1_500)                    # /api/model + run records settle
            page.evaluate(_JS_BIND, rid)
            log(f"[record] run {rid} 녹화 시작 → {out} ({w}x{h})")

            tab_i, next_tour = 0, time.time() + tour_s
            last_ok, status = time.time(), "…"
            while True:
                now = time.time()
                try:
                    status = fetch_run(base_url, rid).get("status") or "unknown"
                    last_ok = now
                except Exception as exc:                    # noqa: BLE001
                    if now - last_ok > SERVER_GONE_S:
                        log(f"[record] 서버 응답이 {int(SERVER_GONE_S)}초 없어 녹화를 마무리합니다 ({exc})")
                        break
                if not is_active(status):
                    log(f"[record] run {rid} 종료 ({status}) — 마지막 화면 {tail_s:.0f}초 더 녹화")
                    break
                if now - started > max_s:
                    log(f"[record] 최대 녹화 시간 {max_s / 60:.0f}분 도달 — run 은 계속되지만 녹화를 마무리합니다")
                    break
                if tour_s > 0 and now >= next_tour:
                    page.evaluate(_JS_TAB, TOUR_TABS[tab_i % len(TOUR_TABS)])
                    tab_i, next_tour = tab_i + 1, now + tour_s
                page.wait_for_timeout(int(poll_s * 1000))
            page.wait_for_timeout(int(tail_s * 1000))
            context.close()                                 # finalizes the video file
            if video is not None:
                video.save_as(str(out))
                try:
                    video.delete()
                except Exception:                           # noqa: BLE001 — temp copy only
                    pass
            browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"영상 파일이 만들어지지 않았습니다: {out}")
    log(f"[record] 저장: {out} ({out.stat().st_size // 1024} KB, {time.time() - started:.0f}s)")
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="콘솔 서버 (default %(default)s)")
    ap.add_argument("--run", help="녹화할 run id (default: 진행 중인 run)")
    ap.add_argument("--wait", type=float, default=0.0, metavar="SEC",
                    help="진행 중인 run 이 없으면 이 시간까지 기다렸다가 녹화")
    ap.add_argument("--simulate", nargs="+", metavar="LIFECYCLE_ID",
                    help="simulate run 을 띄워서 녹화 (클라우드 호출 없음)")
    ap.add_argument("--out", help="저장 경로 (default reports/console2-runs/<rid>.webm)")
    ap.add_argument("--size", default="%dx%d" % DEFAULT_SIZE, help="WxH (default %(default)s)")
    ap.add_argument("--tour", type=float, default=20.0, metavar="SEC",
                    help="상세 탭 순환 주기, 0 = 끄기 (default %(default)s)")
    ap.add_argument("--max-minutes", type=float, default=240.0, help="최대 녹화 시간 (default %(default)s)")
    ap.add_argument("--check", action="store_true", help="Playwright + Chromium 사용 가능 여부만 확인")
    ap.add_argument("--quiet", action="store_true", help="결과 경로만 출력")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda s: None) if args.quiet else (lambda s: print(s, flush=True))
    if args.check:
        ok, detail = playwright_available()
        print(("OK " if ok else "UNAVAILABLE ") + detail)
        return 0 if ok else 1
    try:
        size = parse_size(args.size)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    base = args.url.rstrip("/")
    try:
        if args.simulate:
            rid = start_simulate(base, args.simulate)
            log(f"[record] simulate run 시작: {rid} ({', '.join(args.simulate)})")
        elif args.run:
            rid = args.run
            if not valid_rid(rid):
                print(f"run id 형식이 아닙니다: {rid!r}", file=sys.stderr)
                return 2
        else:
            rec = wait_for_run(base, args.wait, log=log)
            if not rec:
                print("진행 중인 run 이 없습니다 — 콘솔에서 실행을 시작한 뒤 다시 하거나 --run <rid> / --wait SEC / --simulate <id> 를 쓰세요.",
                      file=sys.stderr)
                return 3
            rid = str(rec["id"])
            log(f"[record] 진행 중인 run: {rid} ({rec.get('status')}, {', '.join(rec.get('lifecycle_ids') or [])[:80]})")
        out = Path(args.out) if args.out else default_out(rid)
        record(base, rid, out, size=size, tour_s=args.tour, max_s=args.max_minutes * 60, log=log)
    except urllib.error.URLError as exc:
        print(f"콘솔 서버({base})에 연결하지 못했습니다: {exc.reason} — ./start.sh 또는 uvicorn controlplane.app:app --port 8800",
              file=sys.stderr)
        return 4
    except Exception as exc:                                # noqa: BLE001 — CLI surface
        print(f"녹화 실패: {exc}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
