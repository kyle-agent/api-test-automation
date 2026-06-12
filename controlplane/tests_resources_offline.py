"""Offline tests for the R2b resource-model form UI (controlplane/resource_model.py
+ resource_routes.py) — docs/RESOURCE-MODEL-PLAN.md §1/§3, contracts C1/C3/C4/C6.

No network, no credentials, no real composer (R2a is parallel — the composer is
exercised through a monkeypatched module). The resource model + drafts live in
a throwaway git repo injected via PLATFORM_RESOURCES_ROOT, so the real
knowledge/formal/resources/ is never touched. Rerunnable from the repo root:

    PYTHONPATH=. python3 controlplane/tests_resources_offline.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path

# fresh throwaway DB + clean env BEFORE the imports
os.environ["PLATFORM_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="platform-res-test-"), "platform.db")
for var in ("PLATFORM_INGEST_TOKEN", "PLATFORM_AUTO_TRIAGE", "PLATFORM_GIT_PUSH",
            "ANTHROPIC_API_KEY", "SCP_ACCESS_KEY", "SCP_SECRET_KEY"):
    os.environ.pop(var, None)

# the resource model root is a throwaway git repo (fixture yaml below)
TMP_ROOT = Path(tempfile.mkdtemp(prefix="resmodel-test-")).resolve()
os.environ["PLATFORM_RESOURCES_ROOT"] = str(TMP_ROOT)

import yaml                                        # noqa: E402
from fastapi import FastAPI                        # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from starlette.datastructures import FormData      # noqa: E402

from controlplane import authoring, resource_model, resource_routes  # noqa: E402

# The knowledge/formal CLI validator validates the REAL repo (not our temp
# fixture) and is slow — drop it here; resource_model's local §1/C1 layout
# checks are what this round owns (full resources validation lands with R1).
authoring.VALIDATORS = [(p, fn) for p, fn in authoring.VALIDATORS
                        if p != "knowledge/formal/"]

# the routes land in app.py via include_router at merge time; tests mount the
# router on their own app exactly the way the orchestrator will
test_app = FastAPI()
test_app.include_router(resource_routes.router)
client = TestClient(test_app)

RES_DIR = TMP_ROOT / "knowledge" / "formal" / "resources"

GROUPS_YAML = """\
version: 1
groups:
  "nw-vpc": {label: "네트워크 기본", category: networking}
"""

VPC_YAML = """\
version: 1
resources:
  vpc:
    code: "nw-vpc-vpc"
    service: networking/vpc
    requires: []
    create:
      endpoint: "POST /v1/vpcs"
      body: {name: "regrvpc{ualpha}", cidr: "{opt.cidr}"}
      options:
        cidr: {type: cidr, required: true, pick: unique-block, note: "live VPC 비겹침"}
    capture: {vpc_id: "$.vpc.id"}
    ready: {field: "$.vpc.state", until: ACTIVE, timeout: 600}
    delete: {endpoint: "DELETE /v1/vpcs/{vpc_id}", destructive: true}
    quota: vpc
    provenance: VALIDATED
    notes: "역추출 파일럿 — 폼 밖의 키는 저장에서 보존되어야 한다"
  subnet:
    code: "nw-vpc-subnet"
    service: networking/vpc
    requires: [vpc]
    create:
      endpoint: "POST /v1/subnets"
      body: {vpc_id: "{vpc.vpc_id}", cidr: "{opt.cidr}", type: GENERAL}
      options:
        cidr: {type: cidr, required: true, pick: sub-block-of, of: vpc.cidr}
    provenance: VALIDATED
  vpc-peering:
    code: "nw-vpc-peering"
    service: networking/vpc
    requires:
      - {ref: vpc, count: 2}
    provenance: docs
  vpc-endpoint:
    code: "nw-vpc-endpoint"
    service: networking/vpc
    requires: [subnet]
    create:
      options:
        target: {type: enum, values: [dns, objectstorage, filestorage, scr], required: true, vary: true}
    provenance: docs
"""

PRIVATELINK_YAML = """\
version: 1
resources:
  privatelink-service:
    code: "nw-vpc-privatelink-svc"
    service: networking/privatelink
    requires:
      - subnet
      - one_of: [load-balancer, {ref: server, use: ip}]
    create:
      options:
        security_group: {type: ref, target: security-group, required: false}
    provenance: docs
"""


def _setup_fixture_repo() -> None:
    RES_DIR.mkdir(parents=True, exist_ok=True)
    (RES_DIR / "_groups.yaml").write_text(GROUPS_YAML, encoding="utf-8")
    (RES_DIR / "networking__vpc.yaml").write_text(VPC_YAML, encoding="utf-8")
    (RES_DIR / "networking__privatelink.yaml").write_text(PRIVATELINK_YAML,
                                                          encoding="utf-8")
    (TMP_ROOT / "drafts").mkdir(exist_ok=True)
    run = subprocess.run
    run(["git", "init", "-q", str(TMP_ROOT)], check=True)
    for k, v in (("user.name", "Platform UI"), ("user.email", "platform@local"),
                 ("commit.gpgsign", "false")):
        run(["git", "-C", str(TMP_ROOT), "config", k, v], check=True)
    run(["git", "-C", str(TMP_ROOT), "add", "-A"], check=True)
    run(["git", "-C", str(TMP_ROOT), "commit", "-qm", "fixture"], check=True)


_setup_fixture_repo()


def _form_pairs(node: dict) -> list[tuple[str, str]]:
    """node 정의 -> 폼이 제출할 (name, value) 쌍 — 템플릿이 렌더하는 것과
    동일한 표현(resource_model의 *_rows/*_text 헬퍼)에서 만든다."""
    pairs: list[tuple[str, str]] = []
    for k in ("code", "service", "group", "quota"):
        if node.get(k):
            pairs.append((k, str(node[k])))
    pairs.append(("provenance", str(node.get("provenance", "docs"))))
    for r in resource_model.requires_rows(node):
        pairs += [("req_type", r["type"]), ("req_target", r["target"]),
                  ("req_count", str(r["count"]))]
    create = node.get("create") or {}
    if create.get("endpoint"):
        pairs.append(("create_endpoint", create["endpoint"]))
    bt = resource_model.body_text(node)
    if bt:
        pairs.append(("create_body", bt))
    for o in resource_model.options_rows(node):
        pairs += [("opt_name", o["name"]), ("opt_type", o["type"]),
                  ("opt_required", "yes" if o["required"] else "no"),
                  ("opt_vary", "yes" if o["vary"] else "no"),
                  ("opt_default", o["default"]), ("opt_enum", o["enum_values"]),
                  ("opt_target", o["target"]), ("opt_pick", o["pick"]),
                  ("opt_of", o["of"]), ("opt_note", o["note"])]
    ct = resource_model.capture_text(node)
    if ct:
        pairs.append(("capture", ct))
    ready = node.get("ready") or {}
    if ready.get("field"):
        pairs += [("ready_field", str(ready["field"])),
                  ("ready_until", str(ready.get("until", ""))),
                  ("ready_timeout", str(ready.get("timeout", "")))]
    delete = node.get("delete") or {}
    if delete.get("endpoint"):
        pairs += [("delete_endpoint", str(delete["endpoint"])),
                  ("delete_destructive", "yes" if delete.get("destructive") else "no")]
    return pairs


def _managed(node: dict) -> dict:
    return {k: v for k, v in node.items() if k in resource_model.MANAGED_KEYS}


def _as_data(pairs: list[tuple[str, str]]) -> dict:
    """httpx의 data=는 list-of-tuples를 조용히 버린다 — dict-of-lists로 변환."""
    out: dict[str, list[str]] = {}
    for k, v in pairs:
        out.setdefault(k, []).append(v)
    return out


# --- 1. loader (C1: merge + 부재 tolerance) -----------------------------------------

def test_loader_merges_files_and_tolerates_absence():
    model, sources = resource_model.load_model(with_sources=True)
    assert set(model) == {"vpc", "subnet", "vpc-peering", "vpc-endpoint",
                          "privatelink-service"}, set(model)
    assert sources["vpc"] == "networking__vpc.yaml"
    assert sources["privatelink-service"] == "networking__privatelink.yaml"
    # _groups.yaml is groups, never a node file
    groups = resource_model.load_groups()
    assert groups["nw-vpc"]["label"] == "네트워크 기본"
    # absence -> empty model, no raise (R1 미머지 상태)
    empty = Path(tempfile.mkdtemp(prefix="no-resources-"))
    assert resource_model.load_model(dir=empty / "nope") == {}
    assert resource_model.load_groups(dir=empty / "nope") == {}
    # group derivation: explicit field wins, else code prefix
    assert resource_model.group_of("vpc", {"code": "nw-vpc-vpc"}) == "nw-vpc"
    assert resource_model.group_of("x", {"group": "g9", "code": "nw-vpc-vpc"}) == "g9"


# --- 2. pages render ------------------------------------------------------------------

def test_list_page_renders_groups_and_nodes():
    page = client.get("/planning/resources").text
    assert "네트워크 기본" in page                      # _groups.yaml label
    assert "nw-vpc-peering" in page and "vpc-peering" in page
    assert "vpc×2" in page                              # 다중성 요약
    assert "load-balancer | server:ip" in page          # OR-의존 요약
    assert "VALIDATED" in page and "docs" in page       # provenance 배지
    assert "/planning/resources/vpc-endpoint" in page   # 노드 -> 폼 링크
    # composer는 R2a 머지로 실재 — 목록 페이지는 미탑재 배너가 없어야 한다
    assert "합성기 미탑재" not in page


def test_list_page_with_empty_model_still_renders():
    empty_root = Path(tempfile.mkdtemp(prefix="resmodel-empty-"))
    os.environ["PLATFORM_RESOURCES_ROOT"] = str(empty_root)
    try:
        page = client.get("/planning/resources").text
        assert "아직 자원 모델이 없습니다" in page
    finally:
        os.environ["PLATFORM_RESOURCES_ROOT"] = str(TMP_ROOT)


def test_form_page_renders_existing_node():
    page = client.get("/planning/resources/vpc-endpoint").text
    assert 'value="nw-vpc-endpoint"' in page                  # code
    assert 'value="subnet"' in page                     # requires row
    assert "dns, objectstorage, filestorage, scr" in page  # enum 값
    assert "datalist" in page and 'value="vpc-peering"' in page  # 대상 노드 datalist
    page = client.get("/planning/resources/vpc").text
    assert "POST /v1/vpcs" in page                      # 검증된 endpoint
    assert "regrvpc{ualpha}" in page                    # body 템플릿 (역추출값)
    assert "vpc_id: $.vpc.id" in page                   # capture textarea
    assert 'value="ACTIVE"' in page and 'value="600"' in page  # ready
    # one_of 행은 'id:use' 표기로 렌더된다
    page = client.get("/planning/resources/privatelink-service").text
    assert "load-balancer, server:ip" in page


def test_form_page_new_node_mode():
    r = client.get("/planning/resources/security-group?service=networking/security-group")
    assert r.status_code == 200
    assert "신규" in r.text and 'value="networking/security-group"' in r.text
    # 잘못된 노드 id는 페이지가 없다
    assert client.get("/planning/resources/Bad..Name").status_code == 404


# --- 3. form -> yaml round-trip --------------------------------------------------------

def test_form_roundtrip_produces_schema_correct_yaml():
    model = resource_model.load_model()
    # 폼 표현(템플릿이 렌더하는 헬퍼 출력)을 다시 파싱하면 §1 구조와 동치여야
    # 한다 — ref/count/one_of(use 포함), cidr/enum/ref 옵션, body, capture,
    # ready, delete 전부.
    for nid in ("vpc", "subnet", "vpc-peering", "vpc-endpoint",
                "privatelink-service"):
        form = FormData(_form_pairs(model[nid]))
        node, errors = resource_model.parse_form(form)
        assert errors == [], (nid, errors)
        assert node == _managed(model[nid]), (
            nid, "\n got: %r\nwant: %r" % (node, _managed(model[nid])))


def test_parse_form_reports_errors():
    base = [("service", "networking/vpc"), ("provenance", "docs")]
    # body가 JSON이 아니면
    node, errors = resource_model.parse_form(
        FormData(base + [("create_body", "{broken")]))
    assert any("JSON 파싱 실패" in e for e in errors), errors
    # enum 옵션에 값 목록이 없으면
    node, errors = resource_model.parse_form(FormData(base + [
        ("opt_name", "target"), ("opt_type", "enum"), ("opt_required", "yes"),
        ("opt_vary", "no"), ("opt_default", ""), ("opt_enum", ""),
        ("opt_target", ""), ("opt_pick", ""), ("opt_of", ""), ("opt_note", "")]))
    assert any("enum" in e for e in errors), errors
    # one_of 대안 1개는 거부
    node, errors = resource_model.parse_form(FormData(base + [
        ("req_type", "one_of"), ("req_target", "vpc"), ("req_count", "")]))
    assert any("one_of" in e for e in errors), errors
    # service 형식 / provenance enum (C5)
    node, errors = resource_model.parse_form(
        FormData([("service", "../../evil"), ("provenance", "guess")]))
    assert any("category/service" in e for e in errors), errors
    assert any("provenance" in e for e in errors), errors


def test_layout_errors_local_c1_checks():
    node = {"service": "networking/vpc", "provenance": "docs",
            "requires": ["no-such-node", {"ref": "vpc", "count": 0}],
            "create": {"options": {"x": {"type": "weird"}}}}
    errors, warnings = resource_model.layout_errors("ok-node", node,
                                                    {"vpc": {}})
    assert any("count는 1 이상" in e for e in errors), errors
    assert any("cidr|enum|ref|string" in e for e in errors), errors
    # 미지 참조는 경고만 — 다른 파일/나중 저장으로 채워질 수 있다
    assert any("no-such-node" in w for w in warnings), warnings
    assert not any("no-such-node" in e for e in errors), errors


# --- 4. save path — authoring.propose_edit 경유 ----------------------------------------

def test_save_roundtrips_through_authoring_pipeline():
    model = resource_model.load_model()
    vpc = json.loads(json.dumps(model["vpc"]))
    vpc["ready"]["timeout"] = 900                       # 폼에서 바꾼 값
    r = client.post("/planning/resources/vpc/save", data=_as_data(_form_pairs(vpc)))
    assert r.status_code == 200, r.text[:300]
    assert "저장됨" in r.text, r.text[:500]
    assert "networking__vpc.yaml" in r.text
    # R1 머지 후: knowledge/formal/validate.py가 resources 레이어를 검사하므로
    # '전용 validator 미탑재' degrade 안내는 더 이상 나오지 않아야 한다
    assert resource_model.validator_knows_resources()
    assert "validator 미탑재" not in r.text
    # 파일이 §1 구조로 round-trip 되었는가
    doc = yaml.safe_load((RES_DIR / "networking__vpc.yaml").read_text())
    saved = doc["resources"]["vpc"]
    assert saved["ready"]["timeout"] == 900
    assert saved["create"]["body"]["name"] == "regrvpc{ualpha}"
    assert saved["create"]["options"]["cidr"]["pick"] == "unique-block"
    assert saved["notes"].startswith("역추출 파일럿")   # 폼 밖의 키 보존
    assert doc["resources"]["subnet"]["code"] == "nw-vpc-subnet"  # 이웃 노드 무사
    # 로컬 git 커밋 (authoring 파이프라인 4단계)
    log = subprocess.run(["git", "-C", str(TMP_ROOT), "log", "-1", "--pretty=%s"],
                         capture_output=True, text=True).stdout.strip()
    assert log == ("authoring: knowledge/formal/resources/networking__vpc.yaml "
                   "via platform UI"), log


def test_new_node_goes_to_service_derived_file():
    node = {"code": "nw-sg-sg", "service": "networking/security-group",
            "requires": ["vpc"], "provenance": "docs",
            "create": {"endpoint": "POST /v1/security-groups",
                       "body": {"name": "regrsg{ualpha}"}}}
    r = client.post("/planning/resources/security-group/save",
                    data=_as_data(_form_pairs(node)))
    assert "저장됨" in r.text, r.text[:500]
    path = RES_DIR / "networking__security-group.yaml"   # <category>__<service>.yaml
    assert path.exists()
    doc = yaml.safe_load(path.read_text())
    assert doc["version"] == 1
    assert doc["resources"]["security-group"]["requires"] == ["vpc"]
    # 이제 모델/목록에 보인다
    assert "security-group" in resource_model.load_model()


def test_save_guards_resources_dir_only():
    # service에 경로 구분자/형식 위반 -> 파일 유도 자체가 거부된다
    for bad in ("../../evil", "knowledge/../x", "no-slash", "UPPER/case"):
        try:
            resource_model.node_filename(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass
    before = sorted(p.name for p in RES_DIR.glob("*.yaml"))
    r = client.post("/planning/resources/evil-node/save", data=_as_data([
        ("service", "../../etc"), ("provenance", "docs")]))
    assert "검증 실패" in r.text
    assert sorted(p.name for p in RES_DIR.glob("*.yaml")) == before
    # URL 세그먼트로도 못 빠져나간다
    assert client.post("/planning/resources/Bad..Name/save",
                       data={"service": "a/b"}).status_code == 404
    # save_node 직접 호출도 동일 게이트 (knowledge/formal/resources/ 밖 금지)
    res = resource_model.save_node("x!", {"service": "a/b", "provenance": "docs",
                                          "requires": []})
    assert not res["ok"] and any("노드 id" in e for e in res["errors"]), res


def test_save_validation_failure_keeps_file_intact():
    orig = (RES_DIR / "networking__vpc.yaml").read_bytes()
    model = resource_model.load_model()
    vpc = json.loads(json.dumps(model["vpc"]))
    pairs = [(k, ("{broken" if k == "create_body" else v))
             for k, v in _form_pairs(vpc)]
    r = client.post("/planning/resources/vpc/save", data=_as_data(pairs))
    assert "검증 실패" in r.text and "JSON 파싱 실패" in r.text
    assert (RES_DIR / "networking__vpc.yaml").read_bytes() == orig


# --- 5. compose — composer 미탑재 degrade + 스텁 합성기 ---------------------------------

def test_compose_page_degrades_without_composer():
    # composer는 main에 실재 — 부재 상황은 _composer 패치로 시뮬레이션 (통합 후 형태)
    orig_composer = resource_routes._composer
    resource_routes._composer = lambda: None
    page = client.get("/planning/resources/compose").text
    assert "합성기 미탑재" in page and "disabled" in page
    assert 'value="vpc-endpoint"' in page               # 대상 체크박스는 동작
    assert "choice__privatelink-service" in page        # one_of 분기 select
    r = client.post("/planning/resources/compose",
                    data={"targets": ["vpc"], "action": "plan"})
    assert r.status_code == 200 and "합성기 미탑재" in r.text
    # 프리필: 노드 폼의 "이 자원만 테스트" 링크 경로
    page = client.get("/planning/resources/compose?targets=vpc").text
    assert 'value="vpc" checked' in page
    resource_routes._composer = orig_composer


class _stub_composer:
    """with _stub_composer() as mod: — C2 모양의 가짜 composer 모듈 주입."""

    def __enter__(self):
        mod = types.ModuleType("regression.scenarios.composer")
        mod.calls = {}

        def plan(targets, choices=None, options=None, model=None):
            mod.calls["plan"] = {"targets": list(targets), "choices": choices,
                                 "options": options, "model_nodes": sorted(model or {})}
            return {"order": [{"action": "create", "node": "vpc"},
                              {"action": "create", "node": "subnet"},
                              {"action": "verify", "node": targets[0]},
                              {"action": "delete", "node": "subnet"},
                              {"action": "delete", "node": "vpc"}],
                    "dedup": {"vpc": "1회 생성 (공유)"},
                    "peak_quota": {"vpc": 1},
                    "branches": dict(choices or {})}

        def compose(targets, choices=None, options=None, model=None,
                    lifecycle_id=None):
            mod.calls["compose"] = {"targets": list(targets),
                                    "lifecycle_id": lifecycle_id}
            return {"id": lifecycle_id or f"gen-{targets[0]}", "enabled": True,
                    "steps": [{"name": "create-vpc"}, {"name": "verify"},
                              {"name": "delete-vpc"}]}

        mod.plan, mod.compose = plan, compose
        sys.modules["regression.scenarios.composer"] = mod
        return mod

    def __exit__(self, *exc):
        sys.modules.pop("regression.scenarios.composer", None)


def test_compose_plan_preview_with_stubbed_composer():
    with _stub_composer() as mod:
        r = client.post("/planning/resources/compose", data=_as_data([
            ("targets", "vpc-endpoint"),
            ("choice__privatelink-service", "load-balancer"),
            ("opt__vpc-endpoint__target", "dns"),
            ("action", "plan")]))
        assert r.status_code == 200, r.text[:300]
        assert "생성/검증/삭제 순서표" in r.text
        assert "create" in r.text and "verify" in r.text and "delete" in r.text
        assert "1회 생성 (공유)" in r.text               # dedup 표시
        assert "peak quota" in r.text                    # 할당량 카드
        # C2 인자 계약: choices = {node: branch}, options = {node: {opt: val}}
        assert mod.calls["plan"]["targets"] == ["vpc-endpoint"]
        assert mod.calls["plan"]["choices"] == {"privatelink-service": "load-balancer"}
        assert mod.calls["plan"]["options"] == {"vpc-endpoint": {"target": "dns"}}
        assert "vpc" in mod.calls["plan"]["model_nodes"]
        # 대상 미선택은 안내만
        r = client.post("/planning/resources/compose", data={"action": "plan"})
        assert "대상 노드를 1개 이상" in r.text


def test_compose_save_writes_draft_and_links_run():
    with _stub_composer() as mod:
        r = client.post("/planning/resources/compose", data=_as_data([
            ("targets", "vpc-endpoint"), ("action", "save")]))
        assert "draft 저장됨" in r.text, r.text[:500]
        path = TMP_ROOT / "drafts" / "lifecycle-gen-vpc-endpoint.json"  # C4
        assert path.exists()
        draft = json.loads(path.read_text())
        assert draft["id"] == "gen-vpc-endpoint"
        assert draft["enabled"] is False                 # 자동 enable 금지 (C4)
        assert mod.calls["compose"]["lifecycle_id"] is None
        # run 연계: /runs/trigger 재사용 + /testing crud_filter 프리필 링크
        assert 'name="crud_filter" value="gen-vpc-endpoint"' in r.text
        assert "/testing?crud_filter=gen-vpc-endpoint" in r.text
        # 사용자 지정 lifecycle id
        r = client.post("/planning/resources/compose", data=_as_data([
            ("targets", "vpc"), ("lifecycle_id", "bundle-nw-vpc"),
            ("action", "save")]))
        assert (TMP_ROOT / "drafts" / "lifecycle-bundle-nw-vpc.json").exists()


def test_lifecycle_draft_guards():
    name, errs = resource_model.save_lifecycle_draft({"id": "../escape"})
    assert not name and errs, (name, errs)
    name, errs = resource_model.save_lifecycle_draft("not-a-dict")
    assert not name and errs
    name, errs = resource_model.save_lifecycle_draft({"id": "gen-ok", "steps": []})
    assert name == "lifecycle-gen-ok.json" and not errs


# --- runner -----------------------------------------------------------------------

TESTS = [
    test_loader_merges_files_and_tolerates_absence,
    test_list_page_renders_groups_and_nodes,
    test_list_page_with_empty_model_still_renders,
    test_form_page_renders_existing_node,
    test_form_page_new_node_mode,
    test_form_roundtrip_produces_schema_correct_yaml,
    test_parse_form_reports_errors,
    test_layout_errors_local_c1_checks,
    test_save_roundtrips_through_authoring_pipeline,
    test_new_node_goes_to_service_derived_file,
    test_save_guards_resources_dir_only,
    test_save_validation_failure_keeps_file_intact,
    test_compose_page_degrades_without_composer,
    test_compose_plan_preview_with_stubbed_composer,
    test_compose_save_writes_draft_and_links_run,
    test_lifecycle_draft_guards,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed (root: {TMP_ROOT})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
