"""Authentication signers for the SCP Open API gateway.

SCP authenticates Open API calls with an Access Key + Secret Key using an
HMAC-SHA256 signature. The precise signing-string layout and header names are
not published on the API Reference pages (they live in the User Guide, which is
JS-rendered), so this module is intentionally *pluggable*:

  * header names are configurable via env vars (see framework/config.py);
  * the signing-string builder is isolated in `HmacSigner.signing_string` so it
    can be aligned to the real spec in one place once confirmed against a live
    200 response.

Swap `auth_scheme` to "none" or "bearer" via SCP_AUTH_SCHEME for environments
that use a different mechanism.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Protocol
from urllib.parse import quote, urlsplit

from .config import Settings, _bool


class Signer(Protocol):
    def headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        ...


class NoAuthSigner:
    def headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        return {}


class BearerSigner:
    def __init__(self, token: str):
        self._token = token

    def headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


# Characters JavaScript's encodeURI() leaves unescaped (SCP signs encodeURI(url)).
# Python's quote() never escapes alphanumerics or "_.-~"; add the rest here.
_ENCODE_URI_SAFE = "!#$&'()*+,/:;=?@~"

# Wire-faithful variant of the encodeURI keep-set, for signing the URL exactly
# as `requests` puts it on the wire:
#   * '%'  — the harness assembles URLs whose query is ALREADY percent-encoded
#     (urlencode); a strict encodeURI clone re-escapes '%' -> '%25', so the
#     signature covers `name=%25ED...` while the wire carries `name=%ED...`
#     (systematic 401 on every URL containing a %XX escape). encodeURI(raw)
#     and the wire form agree everywhere else, so keeping '%' makes the
#     transform idempotent on already-encoded URLs.
#   * '[]' — requests' requote_uri keeps brackets where it leaves them (e.g.
#     IPv6 hosts); escaping them here would diverge from the wire.
_ENCODE_URI_WIRE_SAFE = _ENCODE_URI_SAFE + "%[]"


def _encode_uri(path: str) -> str:
    """Match JavaScript encodeURI(): escape spaces etc., keep reserved/unreserved."""
    return quote(path, safe=_ENCODE_URI_SAFE)


def _encode_uri_wire(path: str) -> str:
    """encodeURI-equivalent that never re-escapes an existing %XX sequence.

    Identity on a URL already prepared by `requests` (proven in
    tests/offline/test_hmac_signing.py), and encodeURI-equivalent on raw
    input that carries no '%' — i.e. byte-identical to the legacy transform
    for every request shape that signed correctly before.
    """
    return quote(path, safe=_ENCODE_URI_WIRE_SAFE)


def sign_encodeuri_wire_enabled() -> bool:
    """SCP_SIGN_ENCODEURI toggle (read per call so tests/runs can flip it).

    true (default): sign the wire-identical form — existing %XX escapes in the
        assembled URL are NOT re-escaped, so the signed URL is byte-identical
        to what `requests` sends (core.http_client also pre-normalizes the URL
        with requests' own preparation under this toggle).
    false: legacy behavior — a strict JS-encodeURI clone over the assembled
        URL, which double-encodes '%' and therefore 401s any request whose
        query/path contains a percent-escape (Korean/space/brace values).
    """
    return _bool("SCP_SIGN_ENCODEURI", True)


class HmacSigner:
    """SCP Open API Access Key + HMAC-SHA256 signer.

    Per the SCP API Reference "Common / API 호출하기" guide and its Java/JS sample:
        url       = encodeURI(url)
        message   = method + url + timestamp + accessKey + clientType
        signature = Base64( HMAC_SHA256(message, secretKey) )
    Headers: Scp-Accesskey, Scp-Signature, Scp-Timestamp,
             Scp-ClientType=Openapi, Accept-Language. (No project id.)
    `url` is the full request URL by default (SCP_SIGN_FULL_URL); set it false to
    sign only the path+query.
    """

    def __init__(self, cfg: Settings):
        self._cfg = cfg

    def _url_for_sign(self, url: str) -> str:
        if self._cfg.sign_full_url:
            return url
        parts = urlsplit(url)
        return parts.path + (("?" + parts.query) if parts.query else "")

    def signing_string(self, method: str, url: str, ts: str) -> str:
        encode = _encode_uri_wire if sign_encodeuri_wire_enabled() else _encode_uri
        return (method.upper() + encode(self._url_for_sign(url)) + ts
                + self._cfg.access_key + self._cfg.client_type)

    def headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        msg = self.signing_string(method, url, ts).encode("utf-8")
        digest = hmac.new(self._cfg.secret_key.encode("utf-8"), msg, hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode("ascii")
        return {
            self._cfg.hmac_access_header: self._cfg.access_key,
            self._cfg.hmac_timestamp_header: ts,
            self._cfg.hmac_signature_header: signature,
            self._cfg.client_type_header: self._cfg.client_type,
            self._cfg.language_header: self._cfg.language,
        }


def build_signer(cfg: Settings) -> Signer:
    scheme = (cfg.auth_scheme or "hmac").lower()
    if scheme == "none":
        return NoAuthSigner()
    if scheme == "bearer":
        return BearerSigner(cfg.access_key)  # token carried in SCP_ACCESS_KEY
    return HmacSigner(cfg)
