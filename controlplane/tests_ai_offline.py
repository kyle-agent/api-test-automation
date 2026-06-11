"""Offline tests for the M3 AI pipelines (A1 spec-impact · A2 scenario draft ·
A3 fact extraction).

No network, no API key, no bucket: the Claude layer is exercised by stubbing
the ``ai_pipelines._client`` seam, the drafts dir is a temp dir, and the DB is
a throwaway file. Rerunnable any time from the repo root:

    PYTHONPATH=. python3 controlplane/tests_ai_offline.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# fresh throwaway DB + clean env BEFORE the imports
os.environ["PLATFORM_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="platform-ai-test-"), "platform.db")
for var in ("ANTHROPIC_API_KEY", "PLATFORM_INGEST_TOKEN", "PLATFORM_AUTO_TRIAGE",
            "SCP_ACCESS_KEY", "SCP_SECRET_KEY",
            "SCP_OPLOG_ACCESS_KEY", "SCP_OPLOG_SECRET_KEY"):
    os.environ.pop(var, None)

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402

from controlplane import ai_pipelines, ai_routes, snapshots  # noqa: E402

# the routes land in app.py via include_router at merge time; tests mount the
# router on their own app exactly the way the orchestrator will
test_app = FastAPI()
test_app.include_router(ai_routes.router)
client = TestClient(test_app)

# keep test drafts out of the real drafts/ dir
ai_pipelines.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="drafts-test-"))


# --- Claude stub (the _client seam) -----------------------------------------------

class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Response:
    def __init__(self, payload, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [] if payload is None else \
            [_Block(json.dumps(payload, ensure_ascii=False))]


class _StubClient:
    """anthropic.Anthropic() stand-in; records the last create() kwargs."""

    def __init__(self, payload, stop_reason="end_turn"):
        outer = self
        self.last_kwargs = None

        class _Messages:
            def create(self, **kwargs):
                outer.last_kwargs = kwargs
                return _Response(payload, stop_reason)

        self.messages = _Messages()


class stub_claude:
    """with stub_claude(payload) as stub: ... — patches the seam + the key."""

    def __init__(self, payload, stop_reason="end_turn"):
        self.stub = _StubClient(payload, stop_reason)

    def __enter__(self):
        self._orig_client = ai_pipelines._client
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        ai_pipelines._client = lambda: self.stub
        return self.stub

    def __exit__(self, *exc):
        ai_pipelines._client = self._orig_client
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key


# --- synthetic catalogs for A1 -----------------------------------------------------

def _write_catalog(entries: list[dict]) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(entries, fh)
    return path


def _entry(key: str, method: str, path: str) -> dict:
    cat, svc, name = key.split("/")
    return {"key": key, "category": cat, "service": svc, "name": name,
            "method": method, "http_path": path, "title": "1.0"}


OLD_CAT = [
    _entry("networking/vpc/createvpc", "POST", "/v1/vpcs"),
    _entry("networking/vpc/getvpc", "GET", "/v1/vpcs/{vpc_id}"),
    _entry("compute/virtualserver/listservers", "GET", "/v1/servers"),
]
NEW_CAT = [
    _entry("networking/vpc/createvpc", "POST", "/v2/vpcs"),          # changed
    _entry("networking/vpc/getvpc", "GET", "/v1/vpcs/{vpc_id}"),     # unchanged
    _entry("storage/filestorage/createvolume", "POST", "/v1/volumes"),  # added
]                                                                     # listservers removed


# --- 1. A1 mechanical diff ----------------------------------------------------------

def test_a1_mechanical_diff_correctness():
    old, new = _write_catalog(OLD_CAT), _write_catalog(NEW_CAT)
    r = ai_pipelines.spec_impact(old_path=old, new_path=new, save=False)
    assert r["summary_counts"]["added"] == 1, r["summary_counts"]
    assert r["summary_counts"]["removed"] == 1
    assert r["summary_counts"]["changed"] == 1
    assert r["summary_counts"]["unchanged"] == 1
    assert [e["key"] for e in r["diff"]["added"]] == \
        ["storage/filestorage/createvolume"]
    assert [e["key"] for e in r["diff"]["removed"]] == \
        ["compute/virtualserver/listservers"]
    assert r["diff"]["changed"][0]["key"] == "networking/vpc/createvpc"
    assert "http_path" in r["diff"]["changed"][0]["fields"]
    assert r["affected_services"] == \
        ["compute/virtualserver", "networking/vpc", "storage/filestorage"]
    # no key -> mechanical result intact, AI marked disabled
    assert r["ai"] is None
    assert "ANTHROPIC_API_KEY" in r["ai_error"]


def test_a1_no_change_skips_ai():
    old = _write_catalog(OLD_CAT)
    new = _write_catalog(OLD_CAT)
    r = ai_pipelines.spec_impact(old_path=old, new_path=new, save=False)
    assert r["summary_counts"]["added"] == 0
    assert r["ai"] is None and "변경 없음" in r["ai_error"]


def test_a1_git_rev_baseline():
    # the repo's own HEAD carries data/api_catalog.json — a real git baseline
    path = ai_pipelines.catalog_from_git("HEAD")
    assert json.loads(Path(path).read_text()), "empty catalog from git"
    # malformed / unknown revs raise instead of crashing the route
    for bad in ("$(rm -rf)", "no-such-rev-xyz"):
        try:
            ai_pipelines.catalog_from_git(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_a1_claude_layer_via_stub():
    old, new = _write_catalog(OLD_CAT), _write_catalog(NEW_CAT)
    payload = {"summary": "VPC 생성 경로 변경",
               "impacted_services": [{"service": "networking/vpc",
                                      "why": "createvpc 경로가 v2로 변경",
                                      "suggested_action": "vpc 시나리오 재실행"}],
               "rerun": {"suites": ["smoke"],
                         "service_filters": ["networking/vpc"],
                         "crud_filters": ["networking/vpc*"]}}
    with stub_claude(payload) as stub:
        r = ai_pipelines.spec_impact(old_path=old, new_path=new)
    assert r["ai"]["summary"] == "VPC 생성 경로 변경"
    assert r["ai_error"] == ""
    # the call followed the triage.py pattern
    kw = stub.last_kwargs
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["model"] == "claude-opus-4-8"
    # draft was saved and round-trips
    name = r["draft_name"]
    assert name.startswith("spec-impact-") and name.endswith(".json")
    loaded = ai_pipelines.load_draft(name)
    assert loaded and loaded["kind"] == "spec-impact"
    assert loaded["ai"]["rerun"]["suites"] == ["smoke"]


def test_ask_refusal_and_garbage_are_defended():
    with stub_claude(None, stop_reason="refusal"):
        got, err = ai_pipelines._ask("s", "u", {"type": "object"})
    assert got is None and "거부" in err
    # non-JSON text block
    with stub_claude("x") as stub:
        stub.messages.create = lambda **kw: type(
            "R", (), {"stop_reason": "end_turn",
                      "content": [_Block("not json {")]})()
        got, err = ai_pipelines._ask("s", "u", {"type": "object"})
    assert got is None and "파싱" in err


# --- 2. A2 mechanical validation -----------------------------------------------------

FAKE_SERVICE = "networking/vpc"
FAKE_ENDPOINTS = [
    {"key": "networking/vpc/createvpc", "method": "POST", "path": "/v1/vpcs",
     "name": "createvpc"},
    {"key": "networking/vpc/getvpc", "method": "GET",
     "path": "/v1/vpcs/{vpc_id}", "name": "getvpc"},
    {"key": "networking/vpc/deletevpc", "method": "DELETE",
     "path": "/v1/vpcs/{vpc_id}", "name": "deletevpc"},
]


def _with_fake_catalog(fn):
    orig = ai_pipelines._service_endpoints
    ai_pipelines._service_endpoints = \
        lambda svc: FAKE_ENDPOINTS if svc == FAKE_SERVICE else []
    try:
        return fn()
    finally:
        ai_pipelines._service_endpoints = orig


GOOD_LIFECYCLE = {
    "id": "vpc-draft", "service": FAKE_SERVICE, "enabled": False,
    "steps": [
        {"name": "create", "method": "POST", "path": "/v1/vpcs",
         "json": {"name": "regr-{unique}"}, "expect_status": [201],
         "capture": {"my_vpc": "$.vpc.id"}},
        {"name": "get", "method": "GET", "path": "/v1/vpcs/{my_vpc}",
         "expect_status": [200]},
        {"name": "delete", "method": "DELETE", "path": "/v1/vpcs/{my_vpc}",
         "expect_status": [202, 204], "destructive": True},
    ],
}


def test_a2_validation_passes_good_draft():
    problems = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(GOOD_LIFECYCLE, FAKE_SERVICE))
    assert problems == [], problems


def test_a2_validation_catches_bad_path():
    bad = json.loads(json.dumps(GOOD_LIFECYCLE))
    bad["steps"][1]["path"] = "/v1/nonexistent/{my_vpc}"
    problems = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(bad, FAKE_SERVICE))
    assert any("카탈로그" in p and "/v1/nonexistent" in p for p in problems), problems


def test_a2_validation_catches_capture_used_before_defined():
    bad = json.loads(json.dumps(GOOD_LIFECYCLE))
    bad["steps"][0]["path"] = "/v1/vpcs/{my_vpc}"          # uses its own capture
    bad["steps"][0]["method"] = "GET"
    problems = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(bad, FAKE_SERVICE))
    assert any("placeholder" in p and "my_vpc" in p for p in problems), problems


def test_a2_validation_requires_final_destructive_step():
    bad = json.loads(json.dumps(GOOD_LIFECYCLE))
    bad["steps"] = bad["steps"][:2]                         # no delete at all
    problems = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(bad, FAKE_SERVICE))
    assert any("destructive" in p for p in problems), problems
    # destructive exists but is not last
    bad2 = json.loads(json.dumps(GOOD_LIFECYCLE))
    bad2["steps"] = [bad2["steps"][0], bad2["steps"][2], bad2["steps"][1]]
    problems2 = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(bad2, FAKE_SERVICE))
    assert any("마지막" in p for p in problems2), problems2


def test_a2_engine_globals_do_not_flag():
    lc = json.loads(json.dumps(GOOD_LIFECYCLE))
    lc["steps"][0]["json"] = {"name": "x-{unique}", "region": "{region}",
                              "tag": "{today}"}
    problems = _with_fake_catalog(
        lambda: ai_pipelines.validate_lifecycle_draft(lc, FAKE_SERVICE))
    assert problems == [], problems


def test_a2_full_pipeline_via_stub_forces_disabled():
    payload = {
        "id": "vpc-draft", "service": FAKE_SERVICE,
        "steps": [
            {"name": "create", "method": "POST", "path": "/v1/vpcs",
             "json_body": json.dumps({"name": "regr-{unique}"}),
             "expect_status": [201],
             "capture": [{"var": "my_vpc", "path": "$.vpc.id"}],
             "poll_json": json.dumps({"field": "$.vpc.state",
                                      "until": ["ACTIVE"]}),
             "destructive": False, "optional": False},
            {"name": "delete", "method": "DELETE", "path": "/v1/vpcs/{my_vpc}",
             "json_body": "", "expect_status": [202, 204], "capture": [],
             "poll_json": "", "destructive": True, "optional": False},
        ],
        "notes": ["body의 name 필드는 추정"],
    }

    def run():
        with stub_claude(payload):
            return ai_pipelines.scenario_draft(FAKE_SERVICE)
    # scenario_draft refuses services missing from the real catalog, so route
    # it through the fake-catalog seam too
    orig = ai_pipelines._service_endpoints
    ai_pipelines._service_endpoints = \
        lambda svc: FAKE_ENDPOINTS if svc == FAKE_SERVICE else orig(svc)
    try:
        r = run()
    finally:
        ai_pipelines._service_endpoints = orig

    lc = r["lifecycle"]
    assert lc["enabled"] is False, "drafts must NEVER be enabled"
    assert lc["service"] == FAKE_SERVICE
    assert lc["steps"][0]["json"] == {"name": "regr-{unique}"}
    assert lc["steps"][0]["capture"] == {"my_vpc": "$.vpc.id"}
    assert lc["steps"][0]["poll"] == {"field": "$.vpc.state", "until": ["ACTIVE"]}
    assert lc["steps"][1]["destructive"] is True
    assert r["validation_problems"] == [], r["validation_problems"]
    assert r["notes"] == ["body의 name 필드는 추정"]
    assert r["draft_name"].startswith("lifecycle-networking-vpc-")


# --- 3. A3 fact extraction -----------------------------------------------------------

OBS_ROWS = [
    {"endpoint_key": "networking/vpc/createvpc", "method": "POST",
     "path": "/v1/vpcs", "status": 201, "category": "ok", "source": "crud_probe",
     "note": ""},
    {"endpoint_key": "networking/vpc/getvpc", "method": "GET",
     "path": "/v1/vpcs/{id}", "status": 200, "category": "ok",
     "source": "smoke", "note": ""},
    {"endpoint_key": "networking/vpc/deletevpc", "method": "DELETE",
     "path": "/v1/vpcs/{id}", "status": 500, "category": "fail",
     "source": "crud_probe", "note": "server error"},
]


def test_a3_filters_to_2xx_and_extracts_via_stub():
    orig = snapshots.observations
    snapshots.observations = lambda rid: OBS_ROWS if rid == "777" else []
    payload = {"facts": [{"service": "networking/vpc",
                          "fact": "createvpc body가 실제로 동작함",
                          "evidence": "POST /v1/vpcs -> 201",
                          "confidence": "validated"}],
               "formal_yaml_suggestions": "version: 1\nservice: networking/vpc\n"}
    try:
        with stub_claude(payload) as stub:
            r = ai_pipelines.extract_facts("777")
        # only the two 2xx rows feed the prompt — the 500 must not
        assert r["observation_count"] == 2
        assert "deletevpc" not in stub.last_kwargs["messages"][0]["content"]
        assert "createvpc" in stub.last_kwargs["messages"][0]["content"]
        assert r["ai"]["facts"][0]["confidence"] == "validated"
        assert r["draft_name"] == "facts-777.json"
        # the prompt is honest about what observations contain
        assert "NOT request" in stub.last_kwargs["system"]
        # a run with no snapshot raises a clean error
        try:
            ai_pipelines.extract_facts("000")
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        snapshots.observations = orig


# --- 4. draft store + path guard -------------------------------------------------------

def test_draft_save_load_roundtrip_and_listing():
    name = ai_pipelines.save_draft("unit/тест weird name!!", {"kind": "facts",
                                                              "created": "x"})
    assert ai_pipelines._DRAFT_NAME_RE.match(name), name
    assert ai_pipelines.load_draft(name)["kind"] == "facts"
    assert any(d["name"] == name for d in ai_pipelines.list_drafts())


def test_draft_path_guard():
    assert ai_pipelines.load_draft("../tests_ai_offline.py") is None
    assert ai_pipelines.load_draft("..%2F..%2Fetc%2Fpasswd") is None
    assert ai_pipelines.load_draft("/etc/passwd") is None
    assert ai_pipelines.load_draft("no-such-file.json") is None
    assert ai_pipelines.load_draft("") is None
    r = client.get("/ai/drafts/no-such-file.json")
    assert r.status_code == 404
    r = client.get("/ai/drafts/..%2Fsecret.json")
    assert r.status_code in (404, 422), r.status_code


# --- 5. pages render in disabled (no-key) mode -------------------------------------------

def test_ai_home_renders_disabled():
    assert not ai_pipelines.enabled()
    r = client.get("/ai")
    assert r.status_code == 200, r.text[:300]
    assert "ANTHROPIC_API_KEY" in r.text          # 비활성 안내
    assert "A1" in r.text and "A2" in r.text and "A3" in r.text


def test_a1_route_mechanical_only_without_key():
    old = _write_catalog(OLD_CAT)
    # point the route's "new" at the real repo file? no — the route always
    # diffs against data/api_catalog.json; here we only check it renders the
    # mechanical result without a key, using the old synthetic file.
    r = client.post("/ai/spec-impact", data={"old_path": old})
    assert r.status_code == 200, r.text[:300]
    assert "Mechanical diff" in r.text
    assert "AI 분석 비활성" in r.text


def test_a2_a3_routes_explain_disabled_state():
    services = ai_pipelines.list_catalog_services()
    assert services, "real catalog should list services"
    r = client.post("/ai/scenario-draft", data={"service": services[0]})
    assert r.status_code == 200
    assert "ANTHROPIC_API_KEY" in r.text
    r = client.post("/ai/scenario-draft", data={"service": "no/such"})
    assert r.status_code == 400
    # A3 without snapshot -> clean 400, not a 500
    orig = snapshots.observations
    snapshots.observations = lambda rid: []
    try:
        r = client.post("/ai/extract-facts", data={"run_id": "123"})
        assert r.status_code == 400
    finally:
        snapshots.observations = orig


def test_draft_view_roundtrip_through_route():
    old, new = _write_catalog(OLD_CAT), _write_catalog(NEW_CAT)
    r = ai_pipelines.spec_impact(old_path=old, new_path=new)
    page = client.get(f"/ai/drafts/{r['draft_name']}")
    assert page.status_code == 200
    assert "Mechanical diff" in page.text


TESTS = [
    test_a1_mechanical_diff_correctness,
    test_a1_no_change_skips_ai,
    test_a1_git_rev_baseline,
    test_a1_claude_layer_via_stub,
    test_ask_refusal_and_garbage_are_defended,
    test_a2_validation_passes_good_draft,
    test_a2_validation_catches_bad_path,
    test_a2_validation_catches_capture_used_before_defined,
    test_a2_validation_requires_final_destructive_step,
    test_a2_engine_globals_do_not_flag,
    test_a2_full_pipeline_via_stub_forces_disabled,
    test_a3_filters_to_2xx_and_extracts_via_stub,
    test_draft_save_load_roundtrip_and_listing,
    test_draft_path_guard,
    test_ai_home_renders_disabled,
    test_a1_route_mechanical_only_without_key,
    test_a2_a3_routes_explain_disabled_state,
    test_draft_view_roundtrip_through_route,
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
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed "
          f"(drafts: {ai_pipelines.DRAFTS_DIR})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
