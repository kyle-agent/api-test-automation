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
    (역사 증거 프로즈)만 허용. 새 스텝은 "{zone}" 토큰을 쓸 것."""
    root = pathlib.Path(engine.__file__).resolve().parent
    files = list((root / "lifecycles").glob("*.json")) + [root / "scenarios.json"]
    bad = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ("kr-west1-b" in line and '"_note"' not in line
                    and '"_comment"' not in line):
                bad.append(f"{p.name}:{i}")
    assert not bad, f"존 리터럴 하드코딩 잔존 (값 위치): {bad}"
