"""{zone} 토큰 — 리전별 가용영역 기본값 + SCP_ZONE 오버라이드.

2026-07-29 east 오퍼링 실측(run 20260729-234245-18da)이 계기: 시나리오 바디의
존 리터럴 'kr-west1-b' 하드코딩이 kr-east1 오퍼링에서 400
InvalidAvailabilityZone("must be one of ['kr-east1-a']")을 만들었다. 실측
근거: kr-west1의 유일 존은 '-b'(kr-west1-a는 400 invalid-zone, LIVE
2026-07-15), kr-east1의 유일 존은 '-a'(위 에러 열거). engine._default_zone이
canonical.
"""
from __future__ import annotations

import pathlib

from regression.scenarios import engine


def test_west_defaults_to_b(monkeypatch):
    monkeypatch.delenv("SCP_ZONE", raising=False)
    assert engine._default_zone("kr-west1") == "kr-west1-b"


def test_other_regions_default_to_a(monkeypatch):
    monkeypatch.delenv("SCP_ZONE", raising=False)
    assert engine._default_zone("kr-east1") == "kr-east1-a"
    assert engine._default_zone("kr-south1") == "kr-south1-a"


def test_scp_zone_env_wins(monkeypatch):
    monkeypatch.setenv("SCP_ZONE", "kr-east1-z")
    assert engine._default_zone("kr-west1") == "kr-east1-z"


def test_no_literal_zone_hardcodes_left():
    """값 위치의 존 리터럴이 남아 있으면 교차-리전 400이 재발한다 — _note
    (역사 증거 프로즈)만 허용. 새 스텝은 "{zone}" 토큰을 쓸 것.
    "{region}-b" 꼴도 금지 (2026-08-01: SCP_ZONE 핀을 우회하는 준-리터럴 —
    west1 오퍼링(존 -a)에서 14곳이 -b로 직행하던 잔존 클래스)."""
    root = pathlib.Path(engine.__file__).resolve().parent
    files = list((root / "lifecycles").glob("*.json")) + [root / "scenarios.json"]
    bad = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if '"_note"' in line or '"_comment"' in line:
                continue
            if "kr-west1-b" in line or '"{region}-a"' in line or '"{region}-b"' in line:
                bad.append(f"{p.name}:{i}")
    assert not bad, f"존 리터럴/준-리터럴 하드코딩 잔존 (값 위치): {bad}"


# ── {zone_fs} — filestorage 전용 존 (2026-09-17) ──────────────────────────────
# 오퍼링 캠페인의 SCP_ZONE=kr-west1-a 핀이 {zone}을 통째로 -a로 끌고 가자
# filestorage create 9곳이 400 — filestorage는 kr-west1-b에만 있다 (오너 실측).


def test_fs_zone_west_is_b_even_when_scp_zone_pins_a(monkeypatch):
    monkeypatch.delenv("SCP_ZONE_FS", raising=False)
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    assert engine._default_zone("kr-west1") == "kr-west1-a"
    assert engine._fs_zone("kr-west1") == "kr-west1-b"


def test_fs_zone_other_regions_follow_default(monkeypatch):
    monkeypatch.delenv("SCP_ZONE_FS", raising=False)
    monkeypatch.delenv("SCP_ZONE", raising=False)
    assert engine._fs_zone("kr-east1") == "kr-east1-a"
    monkeypatch.setenv("SCP_ZONE", "kr-east1-z")
    assert engine._fs_zone("kr-east1") == "kr-east1-z"


def test_scp_zone_fs_env_wins(monkeypatch):
    monkeypatch.setenv("SCP_ZONE_FS", "kr-west1-c")
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    assert engine._fs_zone("kr-west1") == "kr-west1-c"


def test_filestorage_volume_creates_use_zone_fs_token():
    """filestorage POST /v1/volumes 스텝이 {zone}을 쓰면 SCP_ZONE 핀에
    다시 끌려간다 — 반드시 {zone_fs}. (parallel-filestorage는 실측 없어 제외.)"""
    import json
    root = pathlib.Path(engine.__file__).resolve().parent
    files = list((root / "lifecycles").glob("*.json")) + [root / "scenarios.json"]
    bad, seen = [], 0
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        lcs = d["lifecycles"] if isinstance(d, dict) and "lifecycles" in d else d
        for lc in lcs:
            for st in lc.get("steps", []):
                svc = st.get("service") or lc.get("service", "")
                body = st.get("json")
                if ("filestorage" in svc and "parallel" not in svc
                        and st.get("path") == "/v1/volumes"
                        and isinstance(body, dict) and "zone" in body):
                    seen += 1
                    if body["zone"] != "{zone_fs}":
                        bad.append(f"{p.name}:{lc['id']}/{st.get('name')}={body['zone']}")
    assert seen >= 9, f"filestorage create 스텝 탐지 실패 (seen={seen})"
    assert not bad, f"filestorage create의 zone은 {{zone_fs}}여야 한다: {bad}"
