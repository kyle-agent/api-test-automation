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

from .config import Settings


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


def _encode_uri(path: str) -> str:
    """Match JavaScript encodeURI(): escape spaces etc., keep reserved/unreserved."""
    return quote(path, safe=_ENCODE_URI_SAFE)


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
        return (method.upper() + _encode_uri(self._url_for_sign(url)) + ts
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
