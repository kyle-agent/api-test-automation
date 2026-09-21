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


def test_ske_nodepool_os_version_is_captured_not_hardcoded():
    """2026-09-17 run 915f: k8s 버전 목록이 밀려 [1]=v1.35.5가 되자 하드코딩
    ubuntu 22.04(v1.34.3 이하에만 존재)가 create-nodepool을 죽였다. OS 버전은
    list-images 응답에서 {kube_ver}/{kube_ver_next}에 맞춰 캡처한다."""
    import json
    root = pathlib.Path(engine.__file__).resolve().parent
    d = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    lc = next(l for l in d["lifecycles"] if l["id"] == "container-ske-cluster-nodepool")
    steps = {s["name"]: s for s in lc["steps"]}
    cap = steps["list-images"]["capture"]
    assert cap["np_os_ver"]["where_prefix"] == {"kubernetes_version": "{kube_ver}", "os": "ubuntu"}
    assert cap["np_os_ver_next"]["where_prefix"] == {"kubernetes_version": "{kube_ver_next}", "os": "ubuntu"}
    assert steps["create-nodepool"]["json"]["image_os_version"] == "{np_os_ver}"
    assert steps["upgrade-nodepool"]["json"]["os_version"] == "{np_os_ver_next}"
    names = [s["name"] for s in lc["steps"]]
    assert names.index("list-images") < names.index("create-nodepool")
    imgs = {"nodepool_images": [
        {"kubernetes_version": "v1.36.3", "os": "ubuntu", "os_version": "24.04"},
        {"kubernetes_version": "v1.35.5", "os": "rhel", "os_version": "9.6"},
        {"kubernetes_version": "v1.35.5", "os": "ubuntu", "os_version": "24.04"},
        {"kubernetes_version": "v1.34.3", "os": "ubuntu", "os_version": "22.04"}]}
    ctx = {"kube_ver": "v1.35.5", "kube_ver_next": "v1.36.3"}
    assert engine._capture(imgs, cap["np_os_ver"], ctx) == "24.04"
    ctx = {"kube_ver": "v1.34.3", "kube_ver_next": "v1.35.5"}
    assert engine._capture(imgs, cap["np_os_ver"], ctx) == "22.04"
    assert engine._capture(imgs, cap["np_os_ver_next"], ctx) == "24.04"


def test_every_vpc_create_body_carries_zone_type():
    """vpc 1.4 (VpcCreateRequestV1Dot4) makes zone_type REQUIRED. Runs eb41/643b
    (2026-09-21): the engine's main shared VPC had it, the shared net-A/B bodies did
    not -> both 400'd silently, vpc#a adopters fell back to self-created VPCs and
    vpc#b users IB-049-skipped. Guard every POST /v1/vpcs body: engine + scenarios."""
    import re
    from pathlib import Path
    from regression.scenarios.loader import load_lifecycles
    src = Path("regression/scenarios/engine.py").read_text(encoding="utf-8")
    # every dict literal that ends up in a _run_step(..., _VPC_CREATE_PATH, ...) call
    # is built via _inject_owner_tags({...}); each such block must name zone_type.
    blocks = re.findall(r"_inject_owner_tags\(\{(.*?)\}, axis=\"regression\"\)", src, re.S)
    # VPC bodies carry cidr+name and no vpc_id (subnet bodies reference a vpc_id)
    vpc_blocks = [b for b in blocks if '"cidr"' in b and '"name"' in b and '"vpc_id"' not in b]
    assert len(vpc_blocks) >= 2, "expected the main shared VPC body AND the net-A/B body"
    for b in vpc_blocks:
        assert '"zone_type"' in b, f"engine VPC create body without zone_type: {b[:120]!r}"
    lcs, _ = load_lifecycles(with_sources=True)
    missing = [(lc["id"], s.get("name")) for lc in lcs for s in lc.get("steps", [])
               if s.get("method") == "POST" and s.get("path") == "/v1/vpcs"
               and isinstance(s.get("json"), dict) and "zone_type" not in s["json"]]
    assert not missing, f"scenario create-vpc bodies without zone_type: {missing}"
