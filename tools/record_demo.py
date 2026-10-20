"""Record a captioned walkthrough video of the platform screens for ONE example service.

오너 요청(2026-10-20): "실제 테스트하는 화면" 을 **이야기 순서대로** 보여 주는 영상 —
Virtual Server 를 예로:

  ① Modeling   — 그 서비스 자원들의 연관관계(REQUIRES)가 테스트 모델에 정의되어 있다
  ② 구성        — AI(composer)가 그 관계를 파악해 의존 폐쇄집합을 모으고 생성 순서(DAG)대로 API 테스트를 짠다
  ③ 실행 계획   — 한정 자원 VPC 를 슬롯 개수로 최적화해 웨이브/워커에 배치 (예상 타임라인·vpc_schedule)
  ④ 실행        — 실제 실행 화면: 실행용량(VPC) · 진행 · 자원 / API / 로그
  ⑤ 대시보드    — 테스트 뒤 서비스 단위 커버리지와 개별 API 의 실행 여부·상태

headless Chromium(Playwright)이 control-plane 화면을 차례로 열고, 화면 아래 **자막 바**와 챕터
**타이틀 카드**를 페이지에 주입해 설명을 넣으며 녹화한다 → ``reports/demo/<date>-<svc>.webm``.

④ 는 **simulate run** 이다 (``POST /api/run mode=simulate`` — 같은 계획·같은 화면·클라우드 호출 0,
자막에도 그렇게 적는다). LIVE run 은 이 도구가 절대 시작하지 않는다 (Hard Rule 1: opt-in 은 콘솔
선택 + pre-flight 확인). LIVE 실행 화면 영상은 run 마다 자동 녹화되는 ``tools/record_console.py`` 쪽.

Usage::
    python -m tools.record_demo                                   # compute/virtualserver, 서버 http://127.0.0.1:8800
    python -m tools.record_demo --service networking/vpc --url http://127.0.0.1:8811 --out reports/demo/vpc.webm
    python -m tools.record_demo --no-run                          # ④ 생략 (simulate run 도 띄우지 않음)
    python -m tools.record_demo --speed 0.5                       # 모든 멈춤 시간을 절반으로 (빠른 점검용)

Prereq: ``pip install -r controlplane/requirements-video.txt && python -m playwright install chromium``
and a running console (``./start.sh`` 또는 ``uvicorn controlplane.app:app --port 8800``). The published
dashboard embed (⑤) needs the dashboard-data snapshot the server already reads for Reporting.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from tools import record_console as rc

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "reports" / "demo"                        # gitignored (reports/)
DEFAULT_SERVICE = "compute/virtualserver"
DEFAULT_SIZE = (1440, 900)

# --------------------------------------------------------------------------- #
# in-page overlays (top document — survive iframe content changes, re-injected after goto)
# --------------------------------------------------------------------------- #
_JS_CAPTION = """([text, sub]) => {
  let bar = document.getElementById('__demo_cap');
  if (!bar) {
    bar = document.createElement('div'); bar.id = '__demo_cap';
    Object.assign(bar.style, { position: 'fixed', left: '0', right: '0', bottom: '0', zIndex: 2147483647,
      background: 'rgba(17,24,39,.93)', color: '#fff', padding: '14px 28px 16px', pointerEvents: 'none',
      font: '600 20px/1.4 -apple-system, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", Roboto, sans-serif',
      boxShadow: '0 -2px 14px rgba(0,0,0,.35)', letterSpacing: '-0.01em' });
    document.body.appendChild(bar);
  }
  bar.innerHTML = '<div>' + text + '</div>' +
    (sub ? '<div style="font-weight:400;font-size:15px;color:#cbd5e1;margin-top:5px">' + sub + '</div>' : '');
}"""

_JS_CARD = """([title, sub, show]) => {
  let card = document.getElementById('__demo_card');
  if (!show) { if (card) card.remove(); return; }
  if (!card) {
    card = document.createElement('div'); card.id = '__demo_card';
    Object.assign(card.style, { position: 'fixed', inset: '0', zIndex: 2147483646, display: 'flex',
      flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center',
      background: 'linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%)', color: '#fff', padding: '60px',
      font: '700 44px/1.3 -apple-system, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", Roboto, sans-serif' });
    document.body.appendChild(card);
  }
  card.innerHTML = '<div style="font-size:16px;font-weight:600;letter-spacing:.18em;color:#93c5fd;margin-bottom:22px">SCP API REGRESSION TEST PLATFORM</div>'
    + '<div>' + title + '</div>'
    + (sub ? '<div style="font-size:22px;font-weight:400;color:#cbd5e1;margin-top:22px;max-width:980px">' + sub + '</div>' : '');
}"""

_JS_HIGHLIGHT = """(sel) => {
  document.querySelectorAll('.__demo_hl').forEach(e => { e.classList.remove('__demo_hl'); e.style.outline = ''; e.style.outlineOffset = ''; e.style.borderRadius = e.dataset.__r || ''; });
  if (!sel) return false;
  const el = document.querySelector(sel);
  if (!el) return false;
  el.classList.add('__demo_hl'); el.dataset.__r = el.style.borderRadius;
  el.style.outline = '3px solid #f59e0b'; el.style.outlineOffset = '3px'; el.style.borderRadius = '6px';
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return true;
}"""

_JS_SCROLL = "(y) => window.scrollBy({ top: y, behavior: 'smooth' })"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Tour:
    """One recorded page + the overlay helpers. ``speed`` scales every pause."""

    def __init__(self, page, base_url: str, *, speed: float = 1.0, log=print):
        self.page, self.base, self.speed, self.log = page, base_url.rstrip("/"), speed, log
        self._cap: tuple[str, str] = ("", "")

    # -- timing / navigation ------------------------------------------------
    def wait(self, s: float) -> None:
        self.page.wait_for_timeout(int(max(0.0, s) * 1000 * self.speed))

    def goto(self, path: str, *, settle: float = 1.5) -> None:
        self.page.goto(self.base + path, wait_until="load")
        self.wait(settle)
        if self._cap[0]:
            self.page.evaluate(_JS_CAPTION, [self._cap[0], self._cap[1]])   # re-inject after navigation

    def console_frame(self):
        """The console2 iframe inside /testing/embed (its automation hook lives there)."""
        for _ in range(60):
            for fr in self.page.frames:
                if "/testing/console" in fr.url:
                    return fr
            self.page.wait_for_timeout(500)
        raise RuntimeError("console iframe (#tcon) not found on /testing/embed")

    # -- overlays -----------------------------------------------------------
    def caption(self, text: str, sub: str = "", hold: float = 0.0) -> None:
        self._cap = (_esc(text), _esc(sub))
        self.page.evaluate(_JS_CAPTION, [self._cap[0], self._cap[1]])
        self.log(f"  [cap] {text}")
        if hold:
            self.wait(hold)

    def card(self, title: str, sub: str = "", hold: float = 3.0) -> None:
        self.page.evaluate(_JS_CARD, [_esc(title), _esc(sub), True])
        self.wait(hold)
        self.page.evaluate(_JS_CARD, ["", "", False])

    def highlight(self, selector: str | None, frame=None, hold: float = 0.0) -> bool:
        ok = bool((frame or self.page).evaluate(_JS_HIGHLIGHT, selector))
        if hold:
            self.wait(hold)
        return ok

    def scroll(self, y: int, frame=None, hold: float = 1.2) -> None:
        (frame or self.page).evaluate(_JS_SCROLL, y)
        self.wait(hold)


# --------------------------------------------------------------------------- #
# chapters
# --------------------------------------------------------------------------- #
def _svc_label(service: str) -> str:
    return service.split("/")[-1]


def chapter_modeling(t: Tour, service: str) -> None:
    name = _svc_label(service)
    t.card("① Modeling — 자원의 연관관계",
           f"{name} 서비스의 자원들이 서로 무엇을 필요로 하는지(REQUIRES)가 테스트 모델에 정의되어 있다")
    t.goto("/planning/resources/map")
    t.caption(f"① Modeling — {name} 자원 모델: 각 노드의 REQUIRES(의존) 가 정의되어 있다",
              "예) server ← subnet · keypair · server-type · security-group,  volume-attachment ← server · block-volume")
    sel = t.page.locator("select#f-service")
    if sel.count():
        sel.select_option(service)
        t.wait(1.5)
    t.highlight("tr.depdrawer, table thead", hold=0.5)
    t.highlight(None)
    # REQUIRES column — scroll through the service's rows slowly
    row = t.page.locator("tr", has_text=service).first
    try:
        row.scroll_into_view_if_needed()
    except Exception:                                      # noqa: BLE001 — best effort
        pass
    t.wait(1.0)
    for _ in range(4):
        t.scroll(260, hold=1.6)
    # 의존 미니그래프 (서비스 행의 🕸 의존 ▸)
    try:
        dep = t.page.locator("tr", has_text=service).locator("a.depbtn").first
        if dep.count():
            dep.click()
            t.wait(1.5)
            t.caption(f"① Modeling — {name} 의 의존 미니그래프: 실행 시 함께 생성·정리되는 의존 폐쇄집합",
                      "모델이 아는 연관관계가 곧 테스트 순서의 근거가 된다")
            t.highlight(f'tr.depdrawer[data-svc="{service}"]', hold=5.0)
            t.highlight(None)
    except Exception as exc:                               # noqa: BLE001
        t.log(f"  (의존 드로어 생략: {exc})")
    t.wait(1.0)


def chapter_compose(t: Tour, service: str) -> None:
    name = _svc_label(service)
    t.card("② 구성 — 연관관계를 따라 API 테스트를 순서대로",
           "AI(composer)가 선택한 서비스의 의존 폐쇄집합을 모으고, 생성 순서(DAG)대로 API 호출 계획을 만든다")
    t.goto("/testing/embed", settle=1.0)
    fr = t.console_frame()
    fr.goto(t.base + "/testing/console/?embed=1&service=" + urllib.parse.quote(service, safe="/"), wait_until="load")
    fr.wait_for_function(rc._JS_READY, timeout=60_000)
    t.wait(3.0)
    t.caption(f"② 구성 — {name} 선택 → 합성 배포 그림: 의존 폐쇄집합을 생성 순서(L0 → L4)로 배치한 API 테스트 DAG",
              "★ = 선택 대상 · 나머지는 연관관계 때문에 함께 들어온 자원(vpc · subnet · security-group …) · 화살표 = 의존")
    t.highlight("#dag-stage-wrap", frame=fr, hold=7.0)
    t.highlight(None, frame=fr)
    # 순서표
    tab = fr.locator("#dag-mode button", has_text=re.compile("표"))
    if tab.count():
        tab.first.click()
        t.wait(1.5)
        t.caption("② 구성 — 생성 · 검증 · 삭제 순서표: 이 순서대로 API 를 호출하고, 역순으로 정리한다",
                  "keypair → image → server-type → … → vpc → subnet → server → volume-attachment …  (공유 = 다른 시나리오와 함께 쓰는 자원)")
        t.highlight("#dag-tableview, #order-tbl", frame=fr, hold=2.0)
        t.highlight(None, frame=fr)
        for _ in range(3):
            t.scroll(240, frame=fr, hold=1.5)
        fr.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        t.wait(1.0)
        pic = fr.locator("#dag-mode button", has_text=re.compile("그림"))
        if pic.count():
            pic.first.click()
            t.wait(1.0)


def chapter_plan(t: Tour, service: str) -> None:
    t.card("③ 실행 계획 — 한정 자원(VPC) 최적화와 순서 최적화",
           "계정 VPC 한도 안에서 슬롯을 나눠 시나리오를 웨이브·워커에 배치하고, 예상 타임라인으로 미리 본다")
    fr = t.console_frame()
    t.caption("③ 실행 계획 — 선택 요약: 포함 자원 · API 스텝 · peak quota(동시 VPC) · 과금 자원 여부",
              "실행 전에 자원 사용량이 한 줄로 보인다")
    t.highlight("#launchSum", frame=fr, hold=4.0)
    t.highlight(None, frame=fr)
    btn = fr.get_by_role("button", name=re.compile("예상 타임라인"))
    if btn.count():
        btn.first.click()
        t.wait(3.5)
        t.caption("③ 실행 계획 — 📊 예상 타임라인: 시나리오를 워커에 LPT 배치, VPC 슬롯(한정 자원)은 자체 생성 시나리오만 점유",
                  "예상 makespan · 워커 수 · VPC 슬롯 수를 바꿔 재계산 — 실행 화면의 '예측 vs 실제' 가 같은 계산을 쓴다")
        t.highlight("#pf-modal", frame=fr, hold=8.0)
        t.highlight(None, frame=fr)
        fr.evaluate("const c = document.getElementById('pf-close'); if (c) c.click();")
        t.wait(0.8)
    t.goto("/planning/dependencies")
    t.caption("③ 실행 계획 — 할당량: 계정 VPC 한도 5 · per-run cap · 시뮬레이션 peak 동시 VPC",
              "vpc_schedule: 공유 VPC adopt(병렬) vs 자체 VPC 생성(직렬) — 동시 VPC 수를 한도 아래로 최적화")
    t.wait(5.0)
    t.scroll(420, hold=5.0)


def chapter_run(t: Tour, service: str, *, run_max_s: float, run_ids: list[str] | None = None) -> str | None:
    name = _svc_label(service)
    t.card("④ 실행 — 실제 테스트 실행 화면",
           "실행용량(VPC 슬롯) · 웨이브 진행 · 자원 / API / 로그.  이 영상의 run 은 simulate 재생(같은 계획·같은 화면, 클라우드 호출 없음)")
    # 기본은 서비스 전체 범위(②와 같은 폐쇄집합). --run-ids 로 짧은 시나리오만 돌려 종료 카드까지 빨리 보여 줄 수 있다
    body = {"mode": "simulate", "lifecycle_ids": run_ids} if run_ids else {"mode": "simulate", "services": [service]}
    code, j = rc._post_json(t.base + "/api/run", body)
    if code >= 300 or not j.get("id"):
        t.caption("④ 실행 — simulate run 을 시작하지 못했습니다: " + str(j.get("error") or code), hold=4.0)
        return None
    rid = str(j["id"])
    t.log(f"  simulate run {rid} ({service})")
    t.goto("/testing/embed", settle=1.0)
    fr = t.console_frame()
    fr.wait_for_function(rc._JS_READY, timeout=60_000)
    t.wait(1.0)
    fr.evaluate(rc._JS_BIND, rid)
    t.wait(2.5)
    scope = ", ".join(run_ids) if run_ids else f"{name} 서비스 전체"
    t.caption(f"④ 실행 — run {rid} ({scope}): 실행용량 바가 한정 자원 VPC 의 사용(기존 · 보유 · 예약 · 여유)을 보여 준다",
              "simulate 재생 — 실제 LIVE run 도 같은 화면이며, 매 run 의 이 화면이 자동 녹화된다 (▶ 영상)")
    t.highlight("#cap-strip", frame=fr, hold=5.0)
    t.highlight(None, frame=fr)
    # 상세(탭) 영역이 자막 아래로 잘리지 않게 — 탭 투어 동안은 상세 영역을 화면 위쪽으로 올린다
    _JS_TO_DETAIL = "() => { const el = document.getElementById('detail-subtabs'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }"
    fr.evaluate(_JS_TO_DETAIL)
    t.wait(1.0)
    tabs = [
        ("api", f"④ 실행 — API 탭: {name} 시나리오가 호출하는 API (메서드 · 경로 · 결과 · 상태 · 응답시간)", "lifecycle 별로 묶여 생성 순서대로 쌓인다"),
        ("res", "④ 실행 — 자원 탭: 만들어진 자원의 생성 · 테스트 · 삭제 단계 (resource_id 포함)", "시나리오가 끝나면 역순으로 정리된다 — 남는 자원이 없어야 한다"),
        ("log", "④ 실행 — 로그 탭: 런 전체 실행 로그 (실시간)", "실패하면 마지막 step 과 오류가 여기 남는다"),
    ]
    started, i, next_tab = time.time(), 0, time.time()
    last_status = "running"
    while True:
        try:
            last_status = rc.fetch_run(t.base, rid).get("status") or "unknown"
        except Exception:                                  # noqa: BLE001 — poll hiccup
            pass
        if not rc.is_active(last_status) or time.time() - started > run_max_s:
            break
        if time.time() >= next_tab:
            tab, text, sub = tabs[i % len(tabs)]
            fr.evaluate(rc._JS_TAB, tab)
            t.caption(text, sub)
            fr.evaluate(_JS_TO_DETAIL)
            i, next_tab = i + 1, time.time() + 12.0 * t.speed
        t.page.wait_for_timeout(1000)
    if rc.is_active(last_status):
        t.caption("④ 실행 — (run 은 계속 진행 중 — 영상은 여기서 다음 장으로 넘어갑니다)", hold=3.0)
    else:
        fr.evaluate(rc._JS_TAB, "res")
        t.caption(f"④ 실행 — run 종료 ({last_status}): 종료 카드 — passed / fail · 생성 · 삭제 · 경과, 그리고 잔존 재스캔 예고",
                  "LIVE run 이면 여기서 ▶ 실행 영상 링크가 붙는다")
        t.wait(2.5)                                       # 종료 카드가 그려질 시간 (UI 폴링 2초)
        if not t.highlight("#donecard:not(.hidden)", frame=fr, hold=5.0):
            fr.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
            t.wait(4.0)
        t.highlight(None, frame=fr)
    return rid


def chapter_dashboard(t: Tour, service: str) -> None:
    name = _svc_label(service)
    cat = service.split("/")[0]
    t.card("⑤ 테스트 후 — 대시보드",
           "커버리지 사다리(C1 도달 → C2 호출 → C3 검증)와 통과율, 서비스 단위 커버리지와 개별 API 의 실행 여부")
    t.goto("/reporting?tab=dashboard", settle=3.0)
    t.caption("⑤ 대시보드 — 전체: 신규 회귀 · Pass rate · C3 검증 커버리지 · Known-red, 커버리지 사다리",
              "발행 대시보드(GitHub Pages)와 같은 렌더링 — 모든 수치는 발행 스냅샷 기준")
    t.wait(5.0)
    t.scroll(380, hold=4.0)
    t.goto(f"/dashboard/services/{cat}__{name}.html", settle=2.0)
    t.caption(f"⑤ 대시보드 — 서비스 단위: {name} 의 검증(2xx) 커버 · 읽기/쓰기 비율 · 설계/동작 결함",
              "'다음 작업 후보' 가 커버리지를 더 올릴 시나리오를 제안한다")
    t.wait(5.0)
    t.caption(f"⑤ 대시보드 — 개별 API: {name} 의 API 하나하나가 실행됐는지 · 최근 STATUS · 응답시간 · 결함",
              "✔ 검증 2xx · ◗ 도달·미검증 · fail / 계약 위반 — 리소스 그룹별로 묶어서 본다")
    for _ in range(5):
        t.scroll(520, hold=2.2)
    t.wait(1.0)


def record_demo(base_url: str, service: str, out: Path, *, size=DEFAULT_SIZE, speed: float = 1.0,
                with_run: bool = True, run_max_s: float = 240.0, run_ids: list[str] | None = None,
                log=print) -> Path:
    from playwright.sync_api import sync_playwright

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    tmp = Path(tempfile.mkdtemp(prefix="c2demo-"))
    started = time.time()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": w, "height": h},
                                          record_video_dir=str(tmp), record_video_size={"width": w, "height": h},
                                          locale="ko-KR", timezone_id="Asia/Seoul")
            page = context.new_page()
            video = page.video
            t = Tour(page, base_url, speed=speed, log=log)
            t.goto("/", settle=0.5)
            name = _svc_label(service)
            t.card("Virtual Server 예시로 보는 API 테스트 흐름" if name == "virtualserver" else f"{name} 예시로 보는 API 테스트 흐름",
                   "① 자원 연관관계(Modeling) → ② 순서대로 구성(DAG) → ③ VPC·순서 최적화 → ④ 실행 화면 → ⑤ 대시보드(커버리지 · API 실행 여부)",
                   hold=4.5)
            chapters = [("modeling", lambda: chapter_modeling(t, service)),
                        ("compose", lambda: chapter_compose(t, service)),
                        ("plan", lambda: chapter_plan(t, service))]
            if with_run:
                chapters.append(("run", lambda: chapter_run(t, service, run_max_s=run_max_s, run_ids=run_ids)))
            chapters.append(("dashboard", lambda: chapter_dashboard(t, service)))
            for cname, fn in chapters:
                log(f"[demo] chapter {cname}")
                try:
                    fn()
                except Exception as exc:                    # noqa: BLE001 — keep the film rolling
                    log(f"[demo] chapter {cname} failed: {exc}")
                    try:
                        t.caption(f"({cname} 장면을 건너뜁니다: {str(exc).splitlines()[0][:90]})", hold=2.5)
                    except Exception:                       # noqa: BLE001
                        pass
            try:
                t.card("매 run 의 실행 화면은 자동 녹화됩니다",
                       "콘솔 실행 기록의 ▶ 영상 (reports/console2-runs/<rid>.webm) · 이 투어: python -m tools.record_demo",
                       hold=4.0)
            except Exception:                               # noqa: BLE001
                pass
            context.close()
            if video is not None:
                video.save_as(str(out))
                try:
                    video.delete()
                except Exception:                           # noqa: BLE001
                    pass
            browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"영상 파일이 만들어지지 않았습니다: {out}")
    log(f"[demo] 저장: {out} ({out.stat().st_size // 1024} KB, {time.time() - started:.0f}s)")
    mp4 = to_mp4(out, log=log)
    return mp4 or out


def to_mp4(webm: Path, *, log=print) -> Path | None:
    """WebM → MP4(H.264) 변환 — 메신저·사내 PC 플레이어는 WebM 을 못 여는 경우가 많다. PATH 에 ffmpeg 가
    있을 때만(없으면 WebM 그대로). Playwright 번들 ffmpeg 는 vp8 전용이라 쓰지 않는다."""
    ff = shutil.which("ffmpeg")
    if not ff:
        log("[demo] ffmpeg 가 PATH 에 없어 MP4 변환은 생략합니다 (WebM 은 Chrome/Edge 에서 바로 재생)")
        return None
    mp4 = webm.with_suffix(".mp4")
    cp = subprocess.run([ff, "-v", "error", "-y", "-i", str(webm), "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], capture_output=True, text=True)
    if cp.returncode != 0 or not mp4.exists():
        log(f"[demo] MP4 변환 실패 ({cp.stderr.strip()[-200:]}) — WebM 을 쓰세요")
        return None
    log(f"[demo] MP4: {mp4} ({mp4.stat().st_size // 1024} KB)")
    return mp4


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=rc.DEFAULT_URL, help="콘솔 서버 (default %(default)s)")
    ap.add_argument("--service", default=DEFAULT_SERVICE, help="예시 서비스 cat/svc (default %(default)s)")
    ap.add_argument("--out", help="저장 경로 (default reports/demo/<YYYYMMDD-HHMM>-<svc>.webm)")
    ap.add_argument("--size", default="%dx%d" % DEFAULT_SIZE, help="WxH (default %(default)s)")
    ap.add_argument("--speed", type=float, default=1.0, help="멈춤 시간 배율 (0.5 = 두 배 빠르게)")
    ap.add_argument("--no-run", action="store_true", help="④ 실행 장면(simulate run) 생략")
    ap.add_argument("--run-max", type=float, default=240.0, metavar="SEC", help="④ 최대 대기 (default %(default)s)")
    ap.add_argument("--run-ids", metavar="ID[,ID…]",
                    help="④ 에서 서비스 전체 대신 이 lifecycle 만 simulate (짧은 영상용, 예 compute-virtualserver-full)")
    ap.add_argument("--check", action="store_true", help="Playwright + Chromium 사용 가능 여부만 확인")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        ok, detail = rc.playwright_available()
        print(("OK " if ok else "UNAVAILABLE ") + detail)
        return 0 if ok else 1
    try:
        size = rc.parse_size(args.size)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", args.service or ""):
        print(f"--service 는 cat/svc 형식이어야 합니다 (예 {DEFAULT_SERVICE}): {args.service!r}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else DEMO_DIR / f"{time.strftime('%Y%m%d-%H%M')}-{_svc_label(args.service)}.webm"
    try:
        rc.fetch_runs(args.url)                             # server reachable?
    except Exception as exc:                                # noqa: BLE001
        print(f"콘솔 서버({args.url})에 연결하지 못했습니다: {exc} — ./start.sh 또는 uvicorn controlplane.app:app --port 8800",
              file=sys.stderr)
        return 4
    try:
        run_ids = [x.strip() for x in (args.run_ids or "").split(",") if x.strip()] or None
        record_demo(args.url, args.service, out, size=size, speed=args.speed,
                    with_run=not args.no_run, run_max_s=args.run_max, run_ids=run_ids)
    except Exception as exc:                                # noqa: BLE001 — CLI surface
        print(f"데모 녹화 실패: {exc}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
