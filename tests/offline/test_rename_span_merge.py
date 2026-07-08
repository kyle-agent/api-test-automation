"""rename 유령 수리 (2026-07-08) — build_spans의 인스턴스 키를 resource_id
우선으로: rename 검증 스텝(regrsrvXu 류)이 이름을 바꿔도 스팬이 쪼개지지 않고,
개명 후 Delete End가 원 스팬을 닫는다 (이번 전체 런에서 영구 유령 3쌍 실측).
"""
from datetime import datetime, timezone

from audit import live_view as lv


def _e(ts, name, rid, event):
    return {"timestamp": ts, "resource_type": "virtualserver",
            "resource_name": name, "resource_id": rid, "event_name": event}


def test_rename_does_not_split_span_and_delete_closes_it():
    now = datetime.now(timezone.utc)
    events = [
        _e("2026-07-08T11:20:00Z", "regrsrv3235efea", "6ab96003", "VirtualServer Create End"),
        _e("2026-07-08T11:25:00Z", "regrsrv3235efeau", "6ab96003", "VirtualServer Update End"),
        _e("2026-07-08T11:32:00Z", "regrsrv3235efeau", "6ab96003", "VirtualServer Delete End"),
    ]
    spans = lv.build_spans(events, now, ours_only=True)
    assert len(spans) == 1, f"rename이 스팬을 쪼갬: {list(spans)}"
    d = next(iter(spans.values()))
    assert d["end"] is not None                    # 유령 아님 — 삭제로 닫힘
    assert lv._state_of(d) == "deleted"
    assert d["name"] == "regrsrv3235efea"          # 표시는 첫-등장 이름 (_lk 보존)
    assert d.get("renamed_to") == "regrsrv3235efeau"
    assert d.get("res_id") == "6ab96003"


def test_events_without_id_join_via_learned_name_map():
    now = datetime.now(timezone.utc)
    events = [
        _e("2026-07-08T11:20:00Z", "regrdash341915d2", "2b4391e1", "Dashboard Create End"),
        # id가 빠진 이벤트라도 같은 (rtype, tag, name)이 id를 가진 적 있으면 병합
        {"timestamp": "2026-07-08T11:21:00Z", "resource_type": "virtualserver",
         "resource_name": "regrdash341915d2", "resource_id": None,
         "event_name": "Dashboard Update End"},
    ]
    spans = lv.build_spans(events, now, ours_only=True)
    assert len(spans) == 1
    assert len(next(iter(spans.values()))["ops"]) == 2
