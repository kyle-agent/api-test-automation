"""Offline test locking in the non-idempotent-retry fix (Wave H, run
27645841461): the http client retried POST on a timeout / connection error,
so a slow SKE cluster create that exceeded the 20s client timeout was retried
and hit ``409 scp-kubernetes.cluster.duplicate-cluster-name`` — the cluster
HAD been created server-side, but the lifecycle never captured its id, so it
could not be torn down (a 47-minute orphan in the ops dashboard) and the test
hard-failed.

Fix (core.http_client.NO_RETRY_ON_EXCEPTION): a timeout / connection error is
ambiguous for a non-idempotent verb (the server may have applied the change),
so POST/PATCH are NOT retried. PUT/DELETE are idempotent by HTTP semantics and
stay retriable, as does GET.
"""
from __future__ import annotations

import pytest
import requests

from core.config import Settings
from core.http_client import ApiClient

ACCESS, SECRET = "AKTESTACCESSKEY", "sk-test-secret"
BASE = "https://x.kr-west1.e.samsungsdscloud.com"


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("SCP_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("SCP_SECRET_KEY", SECRET)
    monkeypatch.setenv("SCP_BASE_URL", BASE)
    monkeypatch.setenv("SCP_ALLOW_MUTATIONS", "true")
    monkeypatch.setenv("SCP_ALLOW_DESTRUCTIVE", "true")
    monkeypatch.setenv("SCP_MAX_RETRIES", "3")
    return Settings()


class _OK:
    status_code = 200
    headers: dict = {}
    text = "{}"

    def json(self):
        return {}


def _client_that_raises(cfg, monkeypatch, *, fail_times):
    """ApiClient whose session raises ConnectionError `fail_times` times, then
    returns 200. Returns (client, counter-list-of-one)."""
    monkeypatch.setattr("core.http_client.time.sleep", lambda *_: None)
    c = ApiClient(cfg)
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise requests.ConnectionError("boom")
        return _OK()

    c.session.request = fake_request
    return c, calls


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_idempotent_methods_retry_on_exception(cfg, monkeypatch, method):
    # all attempts fail -> client exhausts max_retries (3) before raising
    c, calls = _client_that_raises(cfg, monkeypatch, fail_times=99)
    with pytest.raises(requests.RequestException):
        c.request(method, "/v1/things")
    assert calls["n"] == cfg.max_retries == 3


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_non_idempotent_methods_not_retried_on_exception(cfg, monkeypatch, method):
    # a single ambiguous failure must NOT be retried (could duplicate a create)
    c, calls = _client_that_raises(cfg, monkeypatch, fail_times=99)
    with pytest.raises(requests.RequestException):
        c.request(method, "/v1/clusters", json={"name": "regrske-x"})
    assert calls["n"] == 1


def test_idempotent_method_recovers_after_transient_exception(cfg, monkeypatch):
    # GET that fails once then succeeds returns 200 on the 2nd attempt
    c, calls = _client_that_raises(cfg, monkeypatch, fail_times=1)
    resp = c.request("GET", "/v1/things")
    assert resp.status == 200
    assert calls["n"] == 2
