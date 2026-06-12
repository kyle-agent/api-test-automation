"""Offline tests for the R2c AI task-definition draft pipeline
(RESOURCE-MODEL-PLAN §1 / 경로 2) — sibling of tests_ai_offline.py.

No network, no API key: the Claude layer is stubbed at the
``ai_pipelines._client`` seam, drafts go to a temp dir, resources-dir checks
use a temp dir. Rerunnable any time from the repo root:

    PYTHONPATH=. python3 controlplane/tests_ai_taskdraft_offline.py
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

from controlplane import ai_pipelines, ai_routes  # noqa: E402

test_app = FastAPI()
test_app.include_router(ai_routes.router)
client = TestClient(test_app)

# keep test drafts out of the real drafts/ dir
ai_pipelines.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="drafts-test-"))


# --- Claude stub (same seam pattern as tests_ai_offline) ----------------------------

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
    def __init__(self, payload, stop_reason="end_turn"):
        outer = self
        self.last_kwargs = None

        class _Messages:
            def create(self, **kwargs):
                outer.last_kwargs = kwargs
                return _Response(payload, stop_reason)

        self.messages = _Messages()


class stub_claude:
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


# --- 1. mechanical context gathering on REAL repo data --------------------------------

def test_context_servicewatch_real_data():
    ctx = ai_pipelines.gather_task_context("management/servicewatch")
    assert ctx["catalog_endpoints"], "servicewatch must have catalog endpoints"
    assert all(e["key"].startswith("management/servicewatch/")
               for e in ctx["catalog_endpoints"])
    assert ctx["request_body_templates"], "servicewatch has body templates"
    assert all(k.startswith("management/servicewatch/")
               for k in ctx["request_body_templates"])
    # the knowledge yaml exists in the repo and is included
    assert "servicewatch" in ctx["service_knowledge_yaml"]
    # known node ids include the cross-service graph
    assert "vpc" in ctx["known_node_ids"]
    assert "queue" in ctx["known_node_ids"]


def test_context_cross_edges_touching_service():
    # queueservice owns the 'queue' node -> the edge set must contain it
    ctx = ai_pipelines.gather_task_context("application-service/queueservice")
    assert "queue" in ctx["cross_service_edges"], ctx["cross_service_edges"]
    assert ctx["cross_service_edges"]["queue"]["service"] == \
        "application-service/queueservice"
    # filestorage: ske-cluster REQUIRES filestorage-volume -> dependent included
    ctx2 = ai_pipelines.gather_task_context("storage/filestorage")
    assert "filestorage-volume" in ctx2["cross_service_edges"]
    assert "ske-cluster" in ctx2["cross_service_edges"], \
        "nodes requiring a node of the service must be included"
    # unknown service -> clean ValueError
    try:
        ai_pipelines.gather_task_context("no/such")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- 2. §1-shape conversion ------------------------------------------------------------

def _req(ref="", count=1, one_of=None, use=""):
    return {"ref": ref, "count": count, "one_of": one_of or [], "use": use}


def _opt(name, type="string", required=False, vary=False, values=None,
         target="", pick="", of="", note=""):
    return {"name": name, "type": type, "required": required, "vary": vary,
            "values": values or [], "target": target, "pick": pick,
            "of": of, "note": note}


AI_PAYLOAD = {
    "resources": [
        {"id": "queue", "code": "012-001-a",
         "requires": [],
         "create_endpoint": "POST /v1/queues",
         "create_body_json": json.dumps({"name": "regrq{ualpha}",
                                         "type": "{opt.queue_type}"}),
         "options": [_opt("queue_type", type="enum", required=True, vary=True,
                          values=["STANDARD", "FIFO"])],
         "capture": [{"var": "queue_id", "path": "$.id"}],
         "ready_json": json.dumps({"field": "$.state", "until": "ACTIVE",
                                   "timeout": 600}),
         "delete_endpoint": "DELETE /v1/queues/{queue_id}",
         "quota": "queue"},
        {"id": "queue-subscription", "code": "",
         "requires": [_req(ref="queue"),                      # in-draft ref
                      _req(ref="vpc", count=2),               # known, count>1
                      _req(one_of=["security-group", "queue"])],
         "create_endpoint": "POST /v1/queues/{queue_id}/subscriptions",
         "create_body_json": "",
         "options": [],
         "capture": [],
         "ready_json": "",
         "delete_endpoint": "",
         "quota": ""},
    ],
    "uncertainties": ["queue_type enum 값은 docs 추정"],
}

FAKE_SERVICE = "application-service/queueservice"
FAKE_ENDPOINTS = [
    {"key": f"{FAKE_SERVICE}/createqueue", "method": "POST",
     "path": "/v1/queues", "name": "createqueue"},
    {"key": f"{FAKE_SERVICE}/createsub", "method": "POST",
     "path": "/v1/queues/{queue_id}/subscriptions", "name": "createsub"},
    {"key": f"{FAKE_SERVICE}/deletequeue", "method": "DELETE",
     "path": "/v1/queues/{queue_id}", "name": "deletequeue"},
]


def _with_fake_catalog(fn):
    orig = ai_pipelines._service_endpoints
    ai_pipelines._service_endpoints = \
        lambda svc: FAKE_ENDPOINTS if svc == FAKE_SERVICE else orig(svc)
    try:
        return fn()
    finally:
        ai_pipelines._service_endpoints = orig


def test_to_task_model_folds_section1_shapes():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    q = model["queue"]
    assert q["service"] == FAKE_SERVICE
    assert q["provenance"] == "docs", "C5: AI 초안은 항상 docs"
    assert q["create"]["endpoint"] == "POST /v1/queues"
    assert q["create"]["body"] == {"name": "regrq{ualpha}",
                                   "type": "{opt.queue_type}"}
    assert q["create"]["options"]["queue_type"] == {
        "type": "enum", "required": True, "vary": True,
        "values": ["STANDARD", "FIFO"]}
    assert q["capture"] == {"queue_id": "$.id"}
    assert q["ready"] == {"field": "$.state", "until": "ACTIVE", "timeout": 600}
    assert q["delete"] == {"endpoint": "DELETE /v1/queues/{queue_id}",
                           "destructive": True}
    sub = model["queue-subscription"]
    assert sub["requires"][0] == "queue"                       # plain str
    assert sub["requires"][1] == {"ref": "vpc", "count": 2}    # multiplicity
    assert sub["requires"][2] == {"one_of": ["security-group", "queue"]}
    assert "delete" not in sub and "ready" not in sub and "quota" not in sub


# --- 3. mechanical post-validation -------------------------------------------------------

KNOWN = {"vpc", "subnet", "security-group"}


def test_validation_passes_good_model():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    problems, demoted = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model, FAKE_SERVICE, set(KNOWN)))
    assert problems == [], problems
    assert demoted == [], demoted


def test_validation_rejects_non_catalog_endpoint():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    model["queue"]["create"]["endpoint"] = "POST /v1/nonexistent"
    model["queue"]["delete"]["endpoint"] = "DELETE /v1/queues"   # wrong shape
    problems, _ = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model, FAKE_SERVICE, set(KNOWN)))
    assert any("/v1/nonexistent" in p and "카탈로그" in p for p in problems), problems
    assert any("queue.delete" in p for p in problems), problems
    # malformed endpoint string is also rejected
    model2 = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    model2["queue"]["create"]["endpoint"] = "/v1/queues"  # no METHOD
    problems2, _ = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model2, FAKE_SERVICE, set(KNOWN)))
    assert any("형식" in p for p in problems2), problems2


def test_validation_demotes_unknown_refs_not_silently_kept():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    sub = model["queue-subscription"]
    sub["requires"] = ["queue", "ghost-node",
                       {"ref": "phantom", "count": 2},
                       {"one_of": ["security-group", "spectre"]}]
    sub["create"]["options"] = {"sg": {"type": "ref", "target": "no-such-node",
                                       "required": False}}
    problems, demoted = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model, FAKE_SERVICE, set(KNOWN)))
    # unknown refs REMOVED from the model...
    assert sub["requires"] == ["queue", {"one_of": ["security-group"]}], \
        sub["requires"]
    assert "sg" not in sub["create"]["options"]
    # ...and surfaced as demotions (uncertainties), never silently kept
    joined = "\n".join(demoted)
    for ghost in ("ghost-node", "phantom", "spectre", "no-such-node"):
        assert ghost in joined, (ghost, demoted)
    assert problems == [], problems


def test_validation_flags_bad_jsonpath():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    model["queue"]["capture"] = {"qid": "id of the queue", "ok": "$.servers[0].id"}
    problems, _ = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model, FAKE_SERVICE, set(KNOWN)))
    assert any("jsonpath" in p and "qid" in p for p in problems), problems
    assert not any("'ok'" in p for p in problems), problems


def test_validation_one_of_fully_unknown_is_dropped():
    model = ai_pipelines.to_task_model(AI_PAYLOAD, FAKE_SERVICE)
    model["queue-subscription"]["requires"] = [{"one_of": ["x1", "x2"]}]
    _, demoted = _with_fake_catalog(
        lambda: ai_pipelines.validate_task_draft(model, FAKE_SERVICE, set(KNOWN)))
    assert model["queue-subscription"]["requires"] == []
    assert len(demoted) == 2, demoted


# --- 4. full pipeline via stub + draft save/load + path guards ----------------------------

def test_full_pipeline_via_stub_saves_yaml_and_envelope():
    def run():
        with stub_claude(AI_PAYLOAD) as stub:
            r = ai_pipelines.task_draft(FAKE_SERVICE)
        return r, stub
    r, stub = _with_fake_catalog(run)
    # the call followed the house pattern
    kw = stub.last_kwargs
    assert kw["model"] == "claude-opus-4-8"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "known_node_ids" in kw["messages"][0]["content"]
    assert "docs" in kw["system"] and "uncertainties" in kw["system"]
    # result: model + per-resource views + uncertainties + yaml
    assert r["kind"] == "taskdef"
    assert set(r["model"]) == {"queue", "queue-subscription"}
    assert r["uncertainties"] == ["queue_type enum 값은 docs 추정"]
    assert r["validation_problems"] == [], r["validation_problems"]
    views = {v["id"]: v for v in r["resource_views"]}
    assert views["queue-subscription"]["requires"] == \
        ["queue", "vpc ×2", "one of: security-group | queue"]
    # yaml draft (contract C4) — §1 shape, provenance docs, version 1
    assert r["yaml_name"].startswith("taskdef-application-service__queueservice-")
    assert r["yaml_name"].endswith(".yaml")
    import yaml as _yaml
    path = ai_pipelines.taskdef_yaml_path(r["yaml_name"])
    assert path is not None
    parsed = _yaml.safe_load(path.read_text(encoding="utf-8"))
    assert parsed["version"] == 1
    assert parsed["resources"]["queue"]["provenance"] == "docs"
    assert parsed["resources"]["queue"]["create"]["options"]["queue_type"]["vary"]
    # JSON envelope round-trips through the normal draft store
    assert r["draft_name"].endswith(".json")
    loaded = ai_pipelines.load_draft(r["draft_name"])
    assert loaded and loaded["kind"] == "taskdef"
    assert loaded["yaml"] == r["yaml"]
    # NEVER written into knowledge/formal/resources/
    assert ai_pipelines.RESOURCES_DIR not in path.parents


def test_demotions_land_in_uncertainties_through_pipeline():
    payload = json.loads(json.dumps(AI_PAYLOAD))
    payload["resources"][1]["requires"].append(_req(ref="ghost-node"))
    def run():
        with stub_claude(payload):
            return ai_pipelines.task_draft(FAKE_SERVICE, save=False)
    r = _with_fake_catalog(run)
    assert any("ghost-node" in u for u in r["uncertainties"]), r["uncertainties"]
    assert "ghost-node" not in json.dumps(r["model"])


def test_taskdef_yaml_path_guard():
    assert ai_pipelines.taskdef_yaml_path("../secret.yaml") is None
    assert ai_pipelines.taskdef_yaml_path("/etc/passwd") is None
    assert ai_pipelines.taskdef_yaml_path("x.json") is None
    assert ai_pipelines.taskdef_yaml_path("no-such.yaml") is None
    assert ai_pipelines.taskdef_yaml_path("") is None
    name = ai_pipelines.save_taskdef_yaml("unit weird/../name!!", "version: 1\n")
    assert ai_pipelines._TASKDEF_NAME_RE.match(name), name
    assert ai_pipelines.taskdef_yaml_path(name) is not None
    r = client.get("/ai/taskdefs/no-such.yaml")
    assert r.status_code == 404
    r = client.get(f"/ai/taskdefs/{name}")
    assert r.status_code == 200 and "version: 1" in r.text


# --- 5. gap list (모델 공백) ----------------------------------------------------------------

def test_model_gap_list_against_temp_resources_dir():
    tmp = Path(tempfile.mkdtemp(prefix="resources-test-"))
    # nothing modeled yet -> every catalog service is a gap
    gaps0 = ai_pipelines.model_gap_services(tmp)
    services = ai_pipelines.list_catalog_services()
    assert gaps0 == services
    # a file named <cat>__<svc>.yaml models that service
    (tmp / "networking__vpc.yaml").write_text(
        "version: 1\nresources:\n  vpc:\n    service: networking/vpc\n",
        encoding="utf-8")
    # a node's service: field counts too, regardless of the file name
    (tmp / "misc.yaml").write_text(
        "version: 1\nresources:\n  queue:\n"
        "    service: application-service/queueservice\n", encoding="utf-8")
    # _groups.yaml is ignored
    (tmp / "_groups.yaml").write_text(
        "groups: {'nw-vpc': {label: net, category: networking}}\n",
        encoding="utf-8")
    gaps = ai_pipelines.model_gap_services(tmp)
    assert "networking/vpc" not in gaps
    assert "application-service/queueservice" not in gaps
    assert "management/servicewatch" in gaps
    assert len(gaps) == len(services) - 2
    # known node ids pick up the temp dir nodes as ref targets
    known = ai_pipelines.known_resource_nodes(tmp)
    assert {"vpc", "queue"} <= known


# --- 6. keyless rendering --------------------------------------------------------------------

def test_task_draft_page_renders_without_key():
    assert not ai_pipelines.enabled()
    r = client.get("/ai/task-draft")
    assert r.status_code == 200, r.text[:300]
    assert "모델 공백" in r.text
    assert "category/service" in r.text


def test_post_keyless_shows_mechanical_context_ai_disabled():
    r = client.post("/ai/task-draft",
                    data={"service": "management/servicewatch"})
    assert r.status_code == 200, r.text[:300]
    assert "ANTHROPIC_API_KEY" in r.text          # AI 섹션 비활성 안내
    assert "Mechanical 컨텍스트" in r.text         # 컨텍스트 요약은 표시
    assert "카탈로그 endpoint" in r.text
    r404 = client.post("/ai/task-draft", data={"service": "no/such"})
    assert r404.status_code == 400


def test_home_has_fourth_card():
    r = client.get("/ai")
    assert r.status_code == 200
    assert "R2c" in r.text and "/ai/task-draft" in r.text
    assert "A1" in r.text and "A2" in r.text and "A3" in r.text


def test_draft_view_route_renders_taskdef_kind():
    def run():
        with stub_claude(AI_PAYLOAD):
            return ai_pipelines.task_draft(FAKE_SERVICE)
    r = _with_fake_catalog(run)
    page = client.get(f"/ai/drafts/{r['draft_name']}")
    assert page.status_code == 200, page.text[:300]
    assert "queue-subscription" in page.text
    assert "provenance" in page.text


TESTS = [
    test_context_servicewatch_real_data,
    test_context_cross_edges_touching_service,
    test_to_task_model_folds_section1_shapes,
    test_validation_passes_good_model,
    test_validation_rejects_non_catalog_endpoint,
    test_validation_demotes_unknown_refs_not_silently_kept,
    test_validation_flags_bad_jsonpath,
    test_validation_one_of_fully_unknown_is_dropped,
    test_full_pipeline_via_stub_saves_yaml_and_envelope,
    test_demotions_land_in_uncertainties_through_pipeline,
    test_taskdef_yaml_path_guard,
    test_model_gap_list_against_temp_resources_dir,
    test_task_draft_page_renders_without_key,
    test_post_keyless_shows_mechanical_context_ai_disabled,
    test_home_has_fourth_card,
    test_draft_view_route_renders_taskdef_kind,
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
