"""Pre-flight 존 가드 (2026-09-17 run 89de 계기) — 판정 3분류 + 모드 + 훅.

실측 근거: GET filestorage /v1/replications/zones 는 유효 source 존이면 복제
대상 존 목록(kr-west1-b → ['kr-east1-a']), 무효 존(kr-west1-a)이면 빈 목록.
"""
from __future__ import annotations

import json

import pytest

from regression.scenarios import zone_guard as zg


class FakeResp:
    def __init__(self, status, body):
        self.status, self.body, self.raw_text = status, body, json.dumps(body)


class FakeClient:
    """source_zone -> (status, body). 미등록 존은 200 + 빈 목록."""

    def __init__(self, table=None, raise_exc=None):
        self.table = table or {}
        self.calls = []
        self.raise_exc = raise_exc

    def get(self, path, *, service=None, params=None, timeout=None, retry=True):
        self.calls.append((service, path, dict(params or {})))
        if self.raise_exc:
            raise self.raise_exc
        status, body = self.table.get(params["source_zone"], (200, {"zones": []}))
        return FakeResp(status, body)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("SCP_ZONE", "SCP_ZONE_ALT", "SCP_ZONE_CHECK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCP_REGION", "kr-west1")


def test_probe_hits_replication_zones_read_only():
    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    assert zg.probe(c, "kr-west1-b") == ["kr-east1-a"]
    svc, path, params = c.calls[0]
    assert (svc, path) == ("filestorage", "/v1/replications/zones")
    assert params == {"type_name": "HDD", "source_zone": "kr-west1-b",
                      "replication_type": "replication"}


def test_probe_never_raises_and_returns_none_on_failure():
    assert zg.probe(FakeClient(raise_exc=ConnectionError("boom")), "kr-west1-b") is None
    assert zg.probe(FakeClient({"kr-west1-b": (403, {})}), "kr-west1-b") is None
    # docs example renders the list as a string — still parsed
    assert zg.probe(FakeClient({"kr-west1-b": (200, {"zones": "['kr-east1-a']"})}),
                    "kr-west1-b") == ["kr-east1-a"]


def test_default_zone_is_ok_when_probe_nonempty():
    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    res = zg.check(c)
    assert (res["zone"], res["zone_alt"], res["verdict"]) == ("kr-west1-b", "kr-west1-a", "ok")
    assert res["source"] == "default(kr-west1)"
    assert len(c.calls) == 1          # alt 존은 프로브하지 않음


def test_stale_env_pin_is_invalid_when_sibling_is_valid(monkeypatch):
    """run 89de 재현: SCP_ZONE=kr-west1-a 핀, 실제 유효 존은 -b."""
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    res = zg.check(c)
    assert res["verdict"] == "invalid"
    assert res["source"] == "env SCP_ZONE"
    assert res["targets"] == [] and res["alt_targets"] == ["kr-east1-a"]
    assert "kr-west1-b" in res["detail"]
    with pytest.raises(zg.ZoneGuardError):
        zg.enforce(c, log=lambda s: None)


def test_both_empty_is_unknown_not_blocking(monkeypatch):
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    res = zg.enforce(FakeClient(), log=lambda s: None)      # 둘 다 빈 목록
    assert res["verdict"] == "unknown"


def test_probe_failure_is_unknown_not_blocking(monkeypatch):
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    res = zg.enforce(FakeClient(raise_exc=TimeoutError()), log=lambda s: None)
    assert res["verdict"] == "unknown"


def test_warn_mode_logs_but_does_not_raise(monkeypatch):
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    monkeypatch.setenv("SCP_ZONE_CHECK", "warn")
    lines = []
    res = zg.enforce(FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})}),
                     log=lines.append)
    assert res["verdict"] == "invalid" and res["mode"] == "warn"
    assert lines and lines[0].startswith("[zone-guard] WARN")


def test_off_mode_skips_probe(monkeypatch):
    monkeypatch.setenv("SCP_ZONE_CHECK", "false")
    c = FakeClient()
    res = zg.enforce(c, log=lambda s: None)
    assert res["verdict"] == "skipped" and c.calls == []


def test_cli_exit_code_and_json_line(monkeypatch, capsys):
    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    real_check = zg.check
    monkeypatch.setattr(zg, "check", lambda client=None, cfg=None, probe_fn=None: real_check(c))
    assert zg.main([]) == 2
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["verdict"] == "invalid" and out["zone"] == "kr-west1-a"
    monkeypatch.setenv("SCP_ZONE_CHECK", "warn")
    assert zg.main([]) == 0


def test_shared_infra_provision_stops_on_invalid_zone(monkeypatch):
    """훅 ①: provision()이 공유 VPC를 만들기 전에 exit 2로 멈춘다."""
    from regression.scenarios import shared_infra as si

    class Cfg:
        allow_mutations = True
        region = "kr-west1"

    monkeypatch.setenv("SCP_ZONE", "kr-west1-a")
    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    monkeypatch.setattr(si, "_build_client", lambda: (Cfg(), c))
    touched = []
    monkeypatch.setattr(si, "shared_needs", lambda *a, **k: touched.append("needs") or {"any": False})
    assert si.provision() == 2
    assert touched == []            # 가드 이후 단계로 진행하지 않음


def test_shared_infra_provision_continues_when_zone_ok(monkeypatch):
    from regression.scenarios import shared_infra as si

    class Cfg:
        allow_mutations = True
        region = "kr-west1"

    c = FakeClient({"kr-west1-b": (200, {"zones": ["kr-east1-a"]})})
    monkeypatch.setattr(si, "_build_client", lambda: (Cfg(), c))
    monkeypatch.setattr(si, "_needs_logsink", lambda: False)
    monkeypatch.setattr(si, "_needs_image_asset", lambda: False)
    monkeypatch.setattr(si, "shared_needs", lambda *a, **k: {"any": False})
    assert si.provision() == 0
