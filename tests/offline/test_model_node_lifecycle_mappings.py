"""M5 리소스 모델(node)→lifecycle 매핑 무결성 (P2C-26, 2026-07-10).

오너 실측: 콘솔에서 private-nat·apigw-privatelink-endpoint 리소스를 선택했는데
매핑이 stale(폐기된 gen-wave5-privnat / 파일명 generated__wave5-appsvc)이라
계획에서 조용히 빠지고 iam만 실행됐다. 이 게이트는 두 가지를 고정한다:

1. 모든 source.lifecycle 는 실존하는 lifecycle id여야 한다 (파일명/오타 금지).
   비활성(enabled:false)은 허용 — 은퇴는 정당한 상태이고, 그 경우 pre-flight의
   dropped 경고가 사용자에게 사유를 보여준다.
2. _selection_dropped 가 stale/비활성 매핑을 사유와 함께 보고한다.
"""
from __future__ import annotations

import glob
from pathlib import Path

import yaml

from regression.scenarios.loader import load_lifecycles

_RES = Path(__file__).resolve().parents[2] / "knowledge" / "formal" / "resources"


def _mappings():
    out = []
    for f in sorted(glob.glob(str(_RES / "*.yaml"))):
        name = Path(f).name
        if name.startswith("_"):
            continue
        data = yaml.safe_load(open(f)) or {}
        for nid, task in (data.get("resources") or {}).items():
            if not isinstance(task, dict):
                continue
            src = task.get("source")
            lid = src.get("lifecycle") if isinstance(src, dict) else None
            if lid:
                out.append((name, nid, lid))
    return out


def test_every_node_source_lifecycle_exists():
    lcs, _ = load_lifecycles(with_sources=True)
    ids = {l["id"] for l in lcs}
    stale = [(f, n, lid) for f, n, lid in _mappings() if lid not in ids]
    assert not stale, (
        "stale node→lifecycle 매핑 (리소스 선택이 조용히 무시됨): "
        f"{stale} — 실존 lifecycle id로 재배선하거나 source 제거")


def test_selection_dropped_reports_stale_and_disabled():
    import tools.console2_server as c2

    nodes = {
        "ok": {"lifecycle": "lc-on"},
        "stale": {"lifecycle": "lc-ghost"},
        "off": {"lifecycle": "lc-off"},
        "deponly": {},
    }
    lcs = {"lc-on": {"enabled": True, "role": "verify"},
           "lc-off": {"enabled": False, "role": "verify"}}
    orig = c2._MODEL
    c2._MODEL = {"nodes": nodes, "lifecycles": lcs}
    try:
        d = c2._selection_dropped(
            {"node_ids": ["ok", "stale", "off", "deponly", "ghost-node"]},
            ["lc-on"])
    finally:
        c2._MODEL = orig
    by = {x["node"]: x["why"] for x in d}
    assert "ok" not in by
    assert "stale 매핑" in by["stale"]
    assert "비활성" in by["off"]
    assert "의존전용" in by["deponly"]
    assert "모델에 없는" in by["ghost-node"]
