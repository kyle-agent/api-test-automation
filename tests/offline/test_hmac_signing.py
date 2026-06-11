"""Offline tests for HMAC URL signing (no network, no gateway).

Root cause being locked in here (run 27346642059 / handoff "query-string HMAC
401" class): the harness assembles URLs whose query is ALREADY percent-encoded
(`urlencode`), but the legacy signing transform was a strict JS-encodeURI clone
that re-escapes '%' -> '%25'. The signature then covered
``...?name=%25ED%2595%259C...`` while the wire carried ``...?name=%ED%95%9C...``
— a guaranteed 401 for every request whose URL contains a %XX escape (Korean /
space-as-%20 / brace-placeholder query values, e.g. the check-duplication GETs
whose scenario params carry unfilled ``{unique}``/``{reg_id}`` tokens).

The fix (SCP_SIGN_ENCODEURI, default on):
  * core.http_client pre-normalizes the assembled URL with requests' OWN
    preparation (PreparedRequest.prepare_url) before signing, and sends that
    exact string — preparation is idempotent, so signed bytes == wire bytes;
  * core.auth signs through ``_encode_uri_wire`` which never re-escapes an
    existing %XX sequence (identity on a prepared URL, encodeURI-equivalent on
    raw input without '%').

Regression safety proven below: for every request shape that signed correctly
before (no '%' in the assembled URL), the signing string is byte-identical
with the toggle on, off, and on the pre-fix transform.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json as _jsonlib
from urllib.parse import urlencode

import pytest
import requests

from core.auth import (
    HmacSigner,
    _encode_uri,
    _encode_uri_wire,
    sign_encodeuri_wire_enabled,
)
from core.config import Settings
from core.http_client import ApiClient


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
ACCESS, SECRET = "AKTESTACCESSKEY", "sk-test-secret"
BASE = "https://x.kr-west1.e.samsungsdscloud.com"


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("SCP_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("SCP_SECRET_KEY", SECRET)
    monkeypatch.setenv("SCP_BASE_URL", BASE)
    monkeypatch.setenv("SCP_ALLOW_MUTATIONS", "true")
    monkeypatch.setenv("SCP_ALLOW_DESTRUCTIVE", "true")
    monkeypatch.delenv("SCP_SIGN_ENCODEURI", raising=False)
    monkeypatch.delenv("SCP_SIGN_FULL_URL", raising=False)
    return Settings()


def _wire_url(url: str) -> str:
    """The exact URL `requests` puts on the wire for a given target."""
    pr = requests.PreparedRequest()
    pr.prepare_url(url, None)
    return pr.url


def _manual_sig(method: str, exact_url: str, ts: str) -> str:
    """HMAC over the RAW url string with NO transform — what the gateway
    computes from the wire URL it received."""
    msg = (method.upper() + exact_url + ts + ACCESS + "Openapi").encode("utf-8")
    return base64.b64encode(
        hmac_mod.new(SECRET.encode(), msg, hashlib.sha256).digest()).decode()


# --------------------------------------------------------------------------- #
# 1. the encodeURI clone is EXACTLY JS-equivalent (full character set)
# --------------------------------------------------------------------------- #
# JS: encodeURI() leaves A-Z a-z 0-9 and ; , / ? : @ & = + $ - _ . ! ~ * ' ( ) #
_JS_UNESCAPED = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    ";,/?:@&=+$-_.!~*'()#"
)


def test_encode_uri_keeps_exactly_the_js_unescaped_set():
    for ch in _JS_UNESCAPED:
        assert _encode_uri(ch) == ch, f"encodeURI must keep {ch!r}"


def test_encode_uri_escapes_everything_else_like_js():
    # every other printable ASCII char must be %XX-escaped, exactly as JS does
    expected = {
        " ": "%20", '"': "%22", "%": "%25", "<": "%3C", ">": "%3E",
        "[": "%5B", "\\": "%5C", "]": "%5D", "^": "%5E", "`": "%60",
        "{": "%7B", "|": "%7C", "}": "%7D",
    }
    for ch, esc in expected.items():
        assert _encode_uri(ch) == esc, f"encodeURI({ch!r}) must be {esc}"
    # full sweep: any printable ASCII not in the JS keep-set must be escaped
    for code in range(0x20, 0x7F):
        ch = chr(code)
        out = _encode_uri(ch)
        if ch in _JS_UNESCAPED:
            assert out == ch
        else:
            assert out == "%{:02X}".format(code)


def test_encode_uri_escapes_non_ascii_as_utf8_like_js():
    assert _encode_uri("한") == "%ED%95%9C"
    assert _encode_uri("글") == "%EA%B8%80"
    assert _encode_uri("é") == "%C3%A9"


def test_wire_variant_differs_only_in_percent_and_brackets():
    for code in range(0x20, 0x7F):
        ch = chr(code)
        if ch in "%[]":
            assert _encode_uri_wire(ch) == ch  # never re-escaped
        else:
            assert _encode_uri_wire(ch) == _encode_uri(ch)
    assert _encode_uri_wire("한") == _encode_uri("한")


# --------------------------------------------------------------------------- #
# 2. the root cause, byte-for-byte (legacy transform vs the wire)
# --------------------------------------------------------------------------- #
def test_legacy_transform_double_encodes_percent_escapes():
    built = BASE + "/v1/q/check-duplication?" + urlencode({"name": "한글이름"})
    wire = _wire_url(built)
    assert wire == built  # requests sends our %-escapes untouched
    legacy_signed = _encode_uri(built)
    assert "%25ED" in legacy_signed and "%ED%95" in wire
    assert legacy_signed != wire          # <- the 401: signature covers %25ED...
    assert _encode_uri_wire(built) == wire  # <- the fix: byte-identical


def test_unfilled_placeholder_params_diverged_under_legacy():
    # scenario params are not _fill()ed (engine.py), so '{unique}' goes out as
    # %7Bunique%7D — enough to trip the legacy double-encoding.
    built = BASE + "/v1/container-registries/check-duplication/name?" + urlencode(
        {"name": "regrscr{unique}"})
    wire = _wire_url(built)
    assert wire.endswith("name=regrscr%7Bunique%7D")
    assert _encode_uri(built) != wire        # legacy 401
    assert _encode_uri_wire(built) == wire   # fixed


# --------------------------------------------------------------------------- #
# 3. regression safety: no-op for every previously-passing request shape
# --------------------------------------------------------------------------- #
_PASSING_SHAPES = [
    ("GET", BASE + "/v1/devops-services/check-duplication?name=regrprobesmoke"),
    ("GET", BASE + "/v1/repositories/check-duplication/name"
            "?registry_id=11111111-2222&name=regrrepo123"),
    ("GET", BASE + "/v1/things?page=0&size=1&limit=1"),
    ("GET", BASE + "/v1/q?name=my+name"),  # urlencode space -> '+', '%'-free
    ("DELETE", BASE + "/v1/clusters/abc-123/backups"),
    ("POST", BASE + "/v1/clusters/abc-123/backups"),
    ("PUT", BASE + "/v1/clusters/abc-123/backup-histories"),
    ("GET", BASE + "/v1/vpcs"),
]


def test_signing_string_unchanged_for_passing_shapes(cfg, monkeypatch):
    signer = HmacSigner(cfg)
    ts = "1760000000000"
    for method, url in _PASSING_SHAPES:
        monkeypatch.setenv("SCP_SIGN_ENCODEURI", "true")
        new = signer.signing_string(method, url, ts)
        monkeypatch.setenv("SCP_SIGN_ENCODEURI", "false")
        legacy = signer.signing_string(method, url, ts)
        assert new == legacy, f"fix must be a no-op for {method} {url}"
        # and these shapes were already wire-identical
        assert _wire_url(url) in new


def test_toggle_semantics(monkeypatch):
    monkeypatch.delenv("SCP_SIGN_ENCODEURI", raising=False)
    assert sign_encodeuri_wire_enabled() is True  # default ON
    monkeypatch.setenv("SCP_SIGN_ENCODEURI", "false")
    assert sign_encodeuri_wire_enabled() is False
    monkeypatch.setenv("SCP_SIGN_ENCODEURI", "")  # empty == unset
    assert sign_encodeuri_wire_enabled() is True


# --------------------------------------------------------------------------- #
# 4. end-to-end through ApiClient: signature == HMAC over the exact wire URL
# --------------------------------------------------------------------------- #
class _StubResponse:
    status_code = 200
    headers: dict = {}
    text = "{}"

    def json(self):
        return {}


@pytest.fixture
def client(cfg):
    c = ApiClient(cfg)
    c.captured = []

    def fake_request(method, url, **kw):
        c.captured.append({"method": method, "url": url,
                           "headers": kw.get("headers", {}),
                           "data": kw.get("data")})
        return _StubResponse()

    c.session.request = fake_request
    return c


_E2E_CASES = [
    # (method, path, params, json_body)
    ("GET", "/v1/devops-services/check-duplication", {"name": "regrprobesmoke"}, None),
    ("GET", "/v1/container-registries/check-duplication/name",
     {"name": "regrscr{unique}"}, None),                       # brace placeholder
    ("GET", "/v1/q/check-duplication", {"name": "한글이름"}, None),  # Korean
    ("GET", "/v1/q/check-duplication", {"name": "my name+x&y=z"}, None),
    ("GET", "/v1/things", {"page": 0, "size": 1, "limit": 1}, None),
    ("DELETE", "/v1/clusters/abc-123/backups", None, None),       # bodyless DELETE
    ("DELETE", "/v1/clusters/abc-123/backups", None, {"k": "v"}),  # DELETE + body
    ("PUT", "/v1/clusters/abc-123/backup-histories", None,
     {"backup_history_number": []}),
]


@pytest.mark.parametrize("method,path,params,body", _E2E_CASES)
def test_signature_matches_wire_url_exactly(client, method, path, params, body):
    client.request(method, path, params=params, json=body)
    sent = client.captured[-1]
    ts = sent["headers"]["Scp-Timestamp"]
    # signed bytes == wire bytes: recompute with NO transform over the URL the
    # session was given (requests re-preparation of it is the identity)
    assert _wire_url(sent["url"]) == sent["url"]
    assert sent["headers"]["Scp-Signature"] == _manual_sig(method, sent["url"], ts)


def test_delete_body_does_not_change_signing_string(cfg):
    signer = HmacSigner(cfg)
    ts = "1760000000000"
    url = BASE + "/v1/clusters/abc-123/backups"
    s = signer.signing_string("DELETE", url, ts)
    # body is not part of the SCP signing string; only method/url/ts/key/type
    assert s == "DELETE" + url + ts + ACCESS + "Openapi"


def test_delete_with_body_sends_body_and_signs_wire_url(client):
    client.request("DELETE", "/v1/clusters/abc-123/backups", json={"k": "v"})
    sent = client.captured[-1]
    assert sent["data"] == _jsonlib.dumps({"k": "v"}).encode("utf-8")
    ts = sent["headers"]["Scp-Timestamp"]
    assert sent["headers"]["Scp-Signature"] == _manual_sig("DELETE", sent["url"], ts)


def test_sign_path_only_mode_matches_wire_path(monkeypatch):
    monkeypatch.setenv("SCP_SIGN_FULL_URL", "false")
    monkeypatch.setenv("SCP_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("SCP_SECRET_KEY", SECRET)
    cfg = Settings()
    signer = HmacSigner(cfg)
    ts = "1760000000000"
    built = BASE + "/v1/q/check-duplication?" + urlencode({"name": "한글"})
    wire = _wire_url(built)
    s = signer.signing_string("GET", wire, ts)
    assert s == "GET" + "/v1/q/check-duplication?name=%ED%95%9C%EA%B8%80" \
        + ts + ACCESS + "Openapi"


# --------------------------------------------------------------------------- #
# 5. invariants the fix relies on
# --------------------------------------------------------------------------- #
def test_prepare_url_is_idempotent():
    samples = [
        BASE + "/v1/q?name=regrscr%7Bunique%7D",
        BASE + "/v1/q?name=%ED%95%9C",
        BASE + "/v1/q?name=my+name",
        BASE + "/v1/path with space/한글?a=[1]&b=*'()!",
        BASE + "/v1/clusters/abc/backups",
    ]
    for s in samples:
        once = _wire_url(s)
        assert _wire_url(once) == once


def test_wire_transform_is_identity_on_prepared_urls():
    samples = [
        BASE + "/v1/q?name=regrscr%7Bunique%7D",
        BASE + "/v1/q?name=%ED%95%9C%EA%B8%80",
        BASE + "/v1/q?name=my+name",
        BASE + "/v1/path with space/한글?a=[1]&b=*'()!",
        BASE + "/v1/clusters/abc/backups",
    ]
    for s in samples:
        wire = _wire_url(s)
        assert _encode_uri_wire(wire) == wire
