"""_run_step must NOT blindly re-send a POST/PATCH after a READ timeout.

2026-09-21 run 643b (security-secretsmanager-writes create-kms): the server had
created the key (03:39:14) but the response timed out; the engine's one-shot
"transport blip" retry re-sent the POST and got 400 kms.duplicate-name, leaving an
orphan the lifecycle could not tear down. Same policy as
core.http_client.NO_RETRY_ON_EXCEPTION: re-send a non-idempotent verb only when the
request provably never reached the server (connect timeout / connection error).
"""
from __future__ import annotations

import pytest
import requests

from regression.scenarios import engine


class _Resp:
    def __init__(self, status, body=None):
        self.status, self.body, self.raw_text = status, body, ""


class _Client:
    def __init__(self, exc):
        self.calls, self._exc = 0, exc

    def request(self, method, path, *, json=None, **k):
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return _Resp(201, {"key": {"id": "k1"}})


def _run(method, exc):
    c = _Client(exc)
    step = {"name": "x", "method": method, "expect_status": [200, 201]}
    resp = engine._run_step(c, step, "/v1/kms/transit", {"name": "n"}, "kms", {})
    return c.calls, resp


def test_post_read_timeout_is_not_resent(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
    with pytest.raises(requests.exceptions.ReadTimeout):
        _run("POST", requests.exceptions.ReadTimeout("read timed out"))


def test_patch_read_timeout_is_not_resent(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
    with pytest.raises(requests.exceptions.ReadTimeout):
        _run("PATCH", requests.exceptions.ReadTimeout("read timed out"))


def test_post_connect_timeout_still_retried_once(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
    calls, resp = _run("POST", requests.exceptions.ConnectTimeout("connect timed out"))
    assert calls == 2 and resp.status == 201


def test_get_read_timeout_still_retried_once(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda *_: None)
    calls, resp = _run("GET", requests.exceptions.ReadTimeout("read timed out"))
    assert calls == 2 and resp.status == 201
