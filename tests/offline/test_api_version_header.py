"""Offline tests for API microversion pinning (Scp-Api-Version header).

SCP Open APIs are microversioned (docs: apireference/api-common/): the header
value is "{product} {version}" (e.g. "vpc 1.3"); omitting it means "latest
CURRENT", which silently drifts on a version bump (field case 2026-07-15:
subnet create's `type` enum GENERAL->PUBLIC + `category` became required on
vpc 1.2 -> 1.3). core.http_client pins every live call to the latest known
current version from data/api_versions.json.

Locked-in contract:
  * known service        -> "Scp-Api-Version: {service} {version}" injected
  * unknown service/None -> NO header (safer than a guessed one the gateway
                            may 400)
  * SCP_API_VERSION_PIN=false            -> globally off
  * SCP_API_VERSION_OVERRIDES="vpc=1.2"  -> per-service pin wins over the map
  * explicit caller header               -> wins over the injected pin
  * "<svc>-dr" alias                     -> pins the base service's version
No live calls are made: the session is stubbed.
"""
from __future__ import annotations

import json

import pytest

import core.http_client as hc
from core.config import Settings
from core.http_client import ApiClient, api_version_header

ACCESS, SECRET = "AKTESTACCESSKEY", "fake-test-credential"  # 스캐너 오탐 방지: 실자격 아님


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("SCP_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("SCP_SECRET_KEY", SECRET)
    monkeypatch.setenv("SCP_REGION", "kr-west1")
    monkeypatch.setenv("SCP_ENV", "e")
    return Settings()


@pytest.fixture
def version_map(tmp_path, monkeypatch):
    """Point the module at a controlled data/api_versions.json and clear caches."""
    f = tmp_path / "api_versions.json"
    f.write_text(json.dumps({
        "header": "Scp-Api-Version",
        "products": {"vpc": "1.3", "firewall": "1.1", "virtualserver": "1.4",
                     "filestorage": "1.2"},
    }), encoding="utf-8")
    monkeypatch.setattr(hc, "_VERSIONS_FILE", f)
    hc._pinned_versions.cache_clear()
    yield {"vpc": "1.3", "firewall": "1.1", "virtualserver": "1.4",
           "filestorage": "1.2"}
    hc._pinned_versions.cache_clear()


class _OK:
    status_code = 200
    headers: dict = {}
    text = "{}"

    def json(self):
        return {}


def _capture_client(cfg):
    """ApiClient whose session records the headers of each sent request."""
    c = ApiClient(cfg)
    sent = []

    def fake_request(method, url, **kw):
        sent.append({"method": method, "url": url, "headers": kw.get("headers") or {}})
        return _OK()

    c.session.request = fake_request
    return c, sent


# -- unit: api_version_header ------------------------------------------------

def test_known_service_pins_product_and_version(version_map):
    assert api_version_header("vpc") == {"Scp-Api-Version": "vpc 1.3"}
    assert api_version_header("virtualserver") == {
        "Scp-Api-Version": "virtualserver 1.4"}


def test_unknown_service_and_none_send_no_header(version_map):
    assert api_version_header("no-such-service") == {}
    assert api_version_header(None) == {}
    assert api_version_header("") == {}


def test_dr_alias_pins_base_service_version(version_map):
    # filestorage-dr targets the DR-region twin of the SAME product
    assert api_version_header("filestorage-dr") == {
        "Scp-Api-Version": "filestorage 1.2"}


@pytest.mark.parametrize("off", ["false", "0", "no", "off", "False"])
def test_kill_switch_disables_globally(version_map, monkeypatch, off):
    monkeypatch.setenv("SCP_API_VERSION_PIN", off)
    assert api_version_header("vpc") == {}


def test_pin_enabled_by_default_and_for_truthy_values(version_map, monkeypatch):
    monkeypatch.delenv("SCP_API_VERSION_PIN", raising=False)
    assert api_version_header("vpc") == {"Scp-Api-Version": "vpc 1.3"}
    monkeypatch.setenv("SCP_API_VERSION_PIN", "true")
    assert api_version_header("vpc") == {"Scp-Api-Version": "vpc 1.3"}


def test_overrides_win_over_map(version_map, monkeypatch):
    monkeypatch.setenv("SCP_API_VERSION_OVERRIDES", "vpc=1.2, firewall=1.0")
    assert api_version_header("vpc") == {"Scp-Api-Version": "vpc 1.2"}
    assert api_version_header("firewall") == {"Scp-Api-Version": "firewall 1.0"}
    # unlisted service still uses the map
    assert api_version_header("virtualserver") == {
        "Scp-Api-Version": "virtualserver 1.4"}


def test_override_enables_service_missing_from_map(version_map, monkeypatch):
    # back-compat hook: a service with no map entry can still be pinned
    monkeypatch.setenv("SCP_API_VERSION_OVERRIDES", "mysql=1.1")
    assert api_version_header("mysql") == {"Scp-Api-Version": "mysql 1.1"}


def test_malformed_override_items_are_skipped(version_map, monkeypatch):
    monkeypatch.setenv("SCP_API_VERSION_OVERRIDES",
                       "vpc=1.2,,broken, =1.0 ,firewall= , just-a-word")
    assert api_version_header("vpc") == {"Scp-Api-Version": "vpc 1.2"}
    assert api_version_header("firewall") == {"Scp-Api-Version": "firewall 1.1"}


def test_missing_versions_file_means_no_header(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "_VERSIONS_FILE", tmp_path / "nope.json")
    hc._pinned_versions.cache_clear()
    try:
        assert api_version_header("vpc") == {}
    finally:
        hc._pinned_versions.cache_clear()


# -- request-level: header actually rides on the wire ------------------------

def test_request_injects_header_for_known_service(cfg, version_map):
    c, sent = _capture_client(cfg)
    c.get("/v1/vpcs", service="vpc")
    assert sent[0]["headers"]["Scp-Api-Version"] == "vpc 1.3"
    assert sent[0]["url"].startswith("https://vpc.kr-west1.e.samsungsdscloud.com")


def test_request_omits_header_for_unknown_service(cfg, version_map):
    c, sent = _capture_client(cfg)
    c.get("/v1/things", service="no-such-service")
    assert "Scp-Api-Version" not in sent[0]["headers"]


def test_request_omits_header_when_kill_switch_off(cfg, version_map, monkeypatch):
    monkeypatch.setenv("SCP_API_VERSION_PIN", "false")
    c, sent = _capture_client(cfg)
    c.get("/v1/vpcs", service="vpc")
    assert "Scp-Api-Version" not in sent[0]["headers"]


def test_explicit_caller_header_wins_over_pin(cfg, version_map):
    c, sent = _capture_client(cfg)
    c.get("/v1/vpcs", service="vpc", headers={"Scp-Api-Version": "vpc 1.1"})
    assert sent[0]["headers"]["Scp-Api-Version"] == "vpc 1.1"


# -- the committed data file is sane ------------------------------------------

def test_committed_api_versions_json_loads_and_covers_core_services():
    data = json.loads((hc.Path(__file__).resolve().parents[2]
                       / "data" / "api_versions.json").read_text(encoding="utf-8"))
    assert data["header"] == "Scp-Api-Version"
    products = data["products"]
    for svc in ("vpc", "firewall", "virtualserver", "mysql", "ske"):
        assert svc in products, f"{svc} missing from data/api_versions.json"
        assert products[svc].count(".") == 1  # "major.minor"
