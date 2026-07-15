"""타임라인 도구 (오너 2026-07-14 "시간순으로 어떤 일이 있는지") — hermetic."""
from __future__ import annotations

from tools.lifecycle_timeline import build_timeline, render_html


def _ev(kind, lc, step, ts, **kw):
    return {"kind": kind, "lifecycle": lc, "step": step, "ts": ts, **kw}


def _events():
    return [
        _ev("step-start", "epas", "epas-create", 100.0, method="POST"),
        _ev("step-end", "epas", "epas-create", 106.0, method="POST",
            status=202, category="ok", elapsed_ms=5500),
        _ev("step-start", "epas", "epas-wait", 106.0, method="GET"),
        _ev("poll-progress", "epas", "epas-wait", 130.0, attempt=1),
        _ev("poll-progress", "epas", "epas-wait", 160.0, attempt=2),
        _ev("step-end", "epas", "epas-wait", 706.0, method="GET",
            status=200, category="ok", elapsed_ms=900),
        # 5초 갭(idle) 후 다음 스텝
        _ev("step-start", "epas", "set-parameters", 711.0, method="PUT"),
        _ev("step-end", "epas", "set-parameters", 712.0, method="PUT",
            status=200, category="ok", elapsed_ms=800),
        # 다른 라이프사이클은 필터링 대상
        _ev("step-start", "other", "x", 100.0, method="GET"),
        _ev("step-end", "other", "x", 101.0, method="GET",
            status=200, category="ok", elapsed_ms=100),
    ]


def test_build_timeline_wall_api_settle_and_gap():
    tl = build_timeline(_events(), "epas")
    assert set(tl) == {"epas"}
    steps = tl["epas"]["steps"]
    assert [s["name"] for s in steps] == ["epas-create", "epas-wait",
                                          "set-parameters"]
    create, wait, setp = steps
    assert create["wall_s"] == 6.0 and abs(create["api_s"] - 5.5) < 1e-6
    assert abs(create["settle_s"] - 0.5) < 1e-6
    # wait: 벽시계 600s, api 0.9s → settle 599.1s, 폴 2회 주석
    assert wait["wall_s"] == 600.0 and wait["polls"] == 2
    assert abs(wait["settle_s"] - 599.1) < 1e-6
    # set-parameters 는 직전 스텝 종료(706.0) 대비 5s 갭
    assert setp["gap_s"] == 5.0
    b = tl["epas"]["breakdown"]
    assert abs(b["api_s"] - (5.5 + 0.9 + 0.8)) < 1e-6
    assert tl["epas"]["total_s"] == 612.0


def test_build_timeline_all_lifecycles_when_no_filter():
    tl = build_timeline(_events())
    assert set(tl) == {"epas", "other"}


def test_render_html_contains_bars_and_summary():
    out = render_html(build_timeline(_events(), "epas"))
    assert "epas-wait" in out and "settle" in out
    assert "총 10:12" in out            # 612s = 10:12
    assert out.count("class='row") == 3
    assert "gap" in out                  # 5s 갭 마커
