"""Offline regression test for the read-chain required-query-param fix.

Locks the fix for the owner report "GET /v1/queues/{queue_id}/attributes
(getqueueattributes) shows 400 in the catalog". Source: the generic read-chain
fired every id-bound GET as `/path/{id}` with NO query string, so endpoints
whose api_docs declares a `required: true` query param (getqueueattributes needs
`?attributes=...&name=...`) 400'd with "Field required" — separate from the
lifecycle's read-attributes step, which already 2xx's.

The read-chain now honors api_docs required query params:
  * a known-safe constant is supplied where one exists (attributes=All), and a
    value carried by the list item is used otherwise (e.g. a queue's `name`);
  * if a required param can't be satisfied, the probe is SKIPPED (no spurious
    400) instead of firing a guaranteed-bad call.
"""
from __future__ import annotations

from core.catalog import Endpoint
from regression import read_chains


def _ep(key, path):
    cat, svc, name = key.split("/", 2)
    return Endpoint(key=key, category=cat, service=svc, name=name, version="1",
                    method="GET", http_path=path, title=None, doc_url="")


class _Resp:
    def __init__(self, status, body=None):
        self.status = status
        self.ok = 200 <= status < 300
        self.body = body if body is not None else []
        self.raw_text = ""
        self.elapsed_ms = 1.0


class _FakeClient:
    """Records every GET; returns a queue list for the parent collection."""
    def __init__(self, items):
        self._items = items
        self.calls = []  # (path, params)

    def get(self, path, *, service=None, params=None):
        self.calls.append((path, params))
        if path.endswith("/attributes"):
            return _Resp(200, {"attributes": {}})
        return _Resp(200, self._items)


QKEY = "application-service/queueservice/getqueueattributes"
QPATH = "/v1/queues/{queue_id}/attributes"


def test_getqueueattributes_required_query_recognized():
    ep = _ep(QKEY, QPATH)
    assert set(read_chains.required_query_params(ep)) == {"attributes", "name"}


def test_run_chain_appends_required_query_for_getqueueattributes(monkeypatch):
    # Avoid touching real result/TSV files during the offline unit test.
    monkeypatch.setattr(read_chains, "_record", lambda *a, **k: None)
    ep = _ep(QKEY, QPATH)
    client = _FakeClient([{"id": "q-123", "name": "regrq-demo"}])
    res = read_chains.run_chain(ep, "queue_id", "/v1/queues", client, {})

    assert res["recorded"] and not res["skipped"]
    # the show GET must carry BOTH required query params (no more bare-call 400)
    show = [c for c in client.calls if c[0] == "/v1/queues/q-123/attributes"]
    assert show, "show endpoint was never called"
    params = show[0][1]
    assert params["attributes"] == "All"          # known-safe constant
    assert params["name"] == "regrq-demo"          # derived from the list item


def test_resolve_required_query_skips_when_unsatisfiable():
    # instance_id is required for IdC showgroup but is NOT derivable from a plain
    # group list item -> we must SKIP rather than emit a guaranteed 400.
    ep = _ep("management/iam-identity-center/showgroup", "/v1/groups/{group_id}")
    query, reason = read_chains.resolve_required_query(ep, {"id": "g-1", "name": "g"})
    assert query is None and "instance_id" in reason


def test_run_chain_skips_unsatisfiable_without_firing(monkeypatch):
    monkeypatch.setattr(read_chains, "_record", lambda *a, **k: None)
    ep = _ep("management/iam-identity-center/showgroup", "/v1/groups/{group_id}")
    client = _FakeClient([{"id": "g-1", "name": "grp"}])
    res = read_chains.run_chain(ep, "group_id", "/v1/groups", client, {})

    assert res["skipped"] and not res["recorded"]
    # only the parent list was hit; the show GET was never fired (no 400 noise)
    assert all(not c[0].startswith("/v1/groups/g-1") for c in client.calls)


def test_endpoint_without_required_query_unaffected(monkeypatch):
    # a normal id-bound GET (no required query) still fires with no params.
    monkeypatch.setattr(read_chains, "_record", lambda *a, **k: None)
    ep = _ep("compute/virtualserver/showservertype", "/v1/server-types/{server_type_id}")
    client = _FakeClient([{"id": "st-1"}])
    res = read_chains.run_chain(ep, "server_type_id", "/v1/server-types", client, {})

    assert res["recorded"] and not res["skipped"]
    show = [c for c in client.calls if c[0] == "/v1/server-types/st-1"]
    assert show and (show[0][1] is None or show[0][1] == {})
