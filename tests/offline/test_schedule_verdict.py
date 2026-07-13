"""Offline tests for the time-based schedule verdict (판정 C, 2026-07-13).

근거: schedule_verdict의 겹침율(판정 A)은 시작 **rank** 기반이라 worksteal
라운드로빈 수리 하에서 낮게 나오는 게 정상이다 (몬스터가 경량 항목 뒤 offset 1
에서 시작 → rank는 밀리지만 시각은 이르다). makespan을 정하는 건 rank가 아니라
시작 **시각**이므로, 판정 C(예측 첫 배치 몬스터의 실제 최대 시작 시각)가 수리
성패의 정본 신호다 — run-c373에서 pg-cluster(+42분)·vs-server-actions(+35분)만
지각이고 중앙값은 0.9분이었다.
"""
from __future__ import annotations

from tools.schedule_verdict import monster_start_verdict


def _rows(specs):
    # specs: list of (id, pred_rank, act_s)
    return [{"id": i, "pred_rank": pr, "act_s": a} for i, pr, a in specs]


def test_flags_late_monsters_only_c373_shape():
    """c373 재현: 예측 첫 배치 24개 중 2개만 >5분 지각 (pg +42분, vs-sa +35분),
    나머지 22개는 조기 시작(≈1분) → late=2, 최대≈42분, 중앙값은 낮다."""
    specs = [(f"m{i}", i, 60.0) for i in range(24)]   # 기본 조기 시작
    specs[2] = ("database-postgresql-cluster", 2, 2508.0)   # +41.8분 (오염 지각)
    specs[3] = ("vs-server-actions-verify", 3, 2070.0)      # +34.5분
    rows = _rows(specs) + _rows([("light-outside", 99, 30.0)])  # 첫 배치 밖
    top_pred, mx, med, late = monster_start_verdict(rows, w=24)
    assert len(top_pred) == 24 and "light-outside" not in {r["id"] for r in top_pred}
    assert {r["id"] for r in late} == {
        "database-postgresql-cluster", "vs-server-actions-verify"}
    assert round(mx) == 2508 and med <= 60         # 22개가 조기 → 중앙값 낮음


def test_no_late_monsters_when_fix_effective():
    """수리 실효 형상: 모든 첫 배치 몬스터가 경량 뒤 offset 1(≈1분)에서 시작 →
    late=0, 최대 시작 시각이 임계(5분) 이하."""
    rows = _rows([(f"m{i}", i, 60.0 + i) for i in range(24)])
    _, mx, _, late = monster_start_verdict(rows, w=24)
    assert late == [] and mx <= 300


def test_empty_rows_safe():
    assert monster_start_verdict([], w=8) == ([], 0.0, 0.0, [])


def test_threshold_is_configurable():
    rows = _rows([("a", 0, 100.0), ("b", 1, 400.0)])
    _, _, _, late_default = monster_start_verdict(rows, w=2)          # 300s
    _, _, _, late_strict = monster_start_verdict(rows, w=2, late_thresh_s=50.0)
    assert {r["id"] for r in late_default} == {"b"}
    assert {r["id"] for r in late_strict} == {"a", "b"}
