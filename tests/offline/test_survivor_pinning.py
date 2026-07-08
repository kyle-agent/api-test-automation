"""실측 잔존 핀 고정 (2026-07-08 GO) — /runtime 창 밖 잔존을 owned-scan 오버레이로
항상 표시. render_flow가 survivor 스팬을 (a) 붉은 점선 + 스캔 배지로 그리고,
(b) 상태 집계(생성중 pulse)로 새지 않게 분리 집계하고, (c) 범례 칩을 내는지 검증.
계기: 렌더 창(1h) 밖의 잔존 5건이 라이브 뷰에서 통째로 보이지 않았던 라이브 목격.
"""
from datetime import datetime, timezone

from audit import live_view as lv

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"   # loggingaudit 스팬 ts 어휘 (live_view._t가 파싱)


def _ts(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


def _spans(now: datetime) -> dict:
    return {
        ("vpc", "regr", "regrvpc12ab34cd"): {
            "rtype": "vpc", "tag": "regr", "name": "regrvpc12ab34cd",
            "start": _ts(now), "end": None,
            "ops": [(_ts(now), "VPC Create End")], "res_id": "aaa-111"},
        ("vpcs", "regr-survivor", "(잔존) 0f803ef7deadbeef"): {
            "rtype": "vpcs", "tag": "regr-survivor",
            "name": "(잔존) 0f803ef7deadbeef",
            "start": _ts(now), "end": None,
            "ops": [(_ts(now), "SurvivorScan")],
            "res_id": "0f803ef7deadbeef", "survivor": True,
            "scan_hhmm": "19:42"},
        ("security-groups", "regr-survivor", "(잔존) sg-bulk-1"): {
            "rtype": "security-groups", "tag": "regr-survivor",
            "name": "(잔존) sg-bulk-1",
            "start": _ts(now), "end": None,
            "ops": [(_ts(now), "SurvivorScan")],
            "res_id": "sg-bulk-1", "survivor": True, "scan_hhmm": "19:42"},
    }


def test_render_flow_pins_survivors():
    now = datetime.now(timezone.utc)
    html_out = lv.render_flow(_spans(now), now, {"start": "t0", "end": "t1"})
    # (a) 붉은 점선 박스 2개 + 스캔시각 배지 + 실측 툴팁
    assert html_out.count('stroke-dasharray="5 3"') == 2
    assert "스캔 19:42" in html_out
    assert "owned 스캔" in html_out
    # (b) 잔존은 상태 집계와 분리 — SurvivorScan ops가 '생성중'으로 새면 pulse 오보
    import re
    m = re.search(r"생성중 (\d+)", html_out)
    assert m and m.group(1) == "0"
    # (c) 범례 칩
    assert "실측 잔존 2" in html_out


def test_render_flow_without_survivors_has_no_chip():
    now = datetime.now(timezone.utc)
    spans = {k: v for k, v in _spans(now).items() if not v.get("survivor")}
    html_out = lv.render_flow(spans, now, {"start": "t0", "end": "t1"})
    assert "실측 잔존" not in html_out
    assert 'stroke-dasharray="5 3"' not in html_out
