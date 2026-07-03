"""/testing/resources 실측(owned) 스캔 — scan_owned 결과의 행 확장 + 기지 항목
(known_issues.stuck_resources) 매칭의 오프라인 단위 테스트 (네트워크 없음)."""
from controlplane import resources


def test_expand_scan_generic_kms_keypairs_bulk():
    scan = [
        {"service": "vpc", "path": "/v1/vpcs/VPC-1"},
        {"service": "kms", "path": "/v1/kms/transit/KEY-1"},
        {"service": "virtualserver", "path": "/v1/keypairs/regrkey1"},
        {"service": "servicewatch", "path": "/v1/log-groups",
         "json": {"ids": ["LG-1", "LG-2"]}},
        {"service": "secretsmanager", "path": "/v1/secrets/SEC-1",
         "json": {"waiting_time_ndays": 7}},
    ]
    rows = resources._expand_scan(scan)
    by_id = {r["res_id"]: r for r in rows}
    assert by_id["VPC-1"]["collection"] == "vpcs" and by_id["VPC-1"]["kind"] == "vpcs"
    assert by_id["KEY-1"]["kind"] == "kms"           # 단건 삭제 매핑과 일치 (/v1/kms/transit)
    assert by_id["regrkey1"]["kind"] == "keypairs"
    assert by_id["regrkey1"]["name"] == "regrkey1"   # keypair 는 이름으로 삭제
    # bulk(body.ids) → id 하나당 한 행
    assert by_id["LG-1"]["kind"] == "log-groups" and by_id["LG-2"]["service"] == "servicewatch"
    # secrets 의 body 는 ids 가 아니므로 일반형으로
    assert by_id["SEC-1"]["collection"] == "secrets"
    assert len(rows) == 6


def test_stuck_matching_folds_documented_leftovers():
    stuck = [{"id": "47fabeca13f24958a0344a00011a274d", "service": "servicewatch",
              "name": "/scp/ske/regrske4936128d-0325b", "reason": "IAM-gated",
              "since": "2026-06-25", "ref": "x"}]
    hit = resources._match_stuck(
        {"res_id": "47fabeca13f24958a0344a00011a274d", "name": ""}, stuck)
    assert hit and hit["reason"] == "IAM-gated"
    # 이름 fallback (부분 일치 포함)
    assert resources._match_stuck(
        {"res_id": "OTHER", "name": "/scp/ske/regrske4936128d-0325b"}, stuck)
    assert resources._match_stuck({"res_id": "NOPE", "name": "zzz"}, stuck) is None


def test_stuck_entries_baseline_has_documented_three():
    entries = resources.stuck_entries()
    ids = {e.get("id") for e in entries}
    assert "47fabeca13f24958a0344a00011a274d" in ids          # IAM-gated log-group
    assert "9e231b01e2394d7aaa8dcca218e770cb" in ids          # SCF PLS deadlock fn1
    assert "3aac2e34203a42cab56b089336bbd18d" in ids          # SCF PLS deadlock fn2
    for e in entries:
        assert e.get("reason") and e.get("since") and e.get("ref")
