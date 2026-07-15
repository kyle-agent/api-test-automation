"""HTTP client for the SCP API gateway.

Built on `requests` with:
  * automatic auth header signing (framework.auth),
  * exponential-backoff retries for transient failures (503/502/504 and
    network errors) — the docs/gateway return intermittent 503s, and the API
    gateway can behave similarly,
  * a safety gate that refuses mutating/destructive calls unless explicitly
    enabled, so a regression run never changes real cloud state by accident.
"""
from __future__ import annotations

import json as _json
import os as _os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from .auth import build_signer, sign_encodeuri_wire_enabled
from .config import Settings, settings

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
DESTRUCTIVE = {"DELETE"}
RETRY_STATUS = {429, 502, 503, 504}
# 429(Too Many Requests)는 처리 전 거부라 모든 메서드에서 재시도 안전 —
# run-c373 실측(2026-07-13): vpc 호스트 순간 burst 스로틀로 서로 다른 5개
# lifecycle이 각 1건씩 429를 맞고 하드 실패. Retry-After 헤더가 있으면 존중.

# Process-wide count of transient gateway responses (502/503/504). A concurrency
# controller reads the delta to back off when the backend is overloaded. Thread-safe.
import threading as _threading
_retry_status_hits = 0
_rsh_lock = _threading.Lock()


def retry_status_count() -> int:
    """Cumulative 502/503/504 responses seen across all ApiClients this process."""
    with _rsh_lock:
        return _retry_status_hits


def _bump_retry_status() -> None:
    global _retry_status_hits
    with _rsh_lock:
        _retry_status_hits += 1
# Non-idempotent verbs must NOT be retried after a timeout / connection error:
# the server may have applied the change while the response was lost (e.g. a
# slow SKE cluster create that exceeds the client timeout), so a blind retry
# creates a DUPLICATE — a 409 duplicate-name plus an orphaned, untracked
# resource the lifecycle can never tear down. PUT/DELETE are idempotent and
# stay retriable; POST/PATCH do not.
NO_RETRY_ON_EXCEPTION = {"POST", "PATCH"}


class MutationBlocked(Exception):
    """Raised when a mutating call is attempted without opt-in."""


# -- API version pinning (Scp-Api-Version) ------------------------------------
# SCP Open APIs are microversioned (docs: apireference/api-common/). Without the
# header the gateway serves the latest CURRENT version, which silently drifts on
# a version bump (field case 2026-07-15: subnet create's `type` enum GENERAL ->
# PUBLIC + `category` became required on vpc 1.2 -> 1.3). We therefore PIN every
# live call to the latest known current version from data/api_versions.json:
#   Scp-Api-Version: {product} {version}     e.g. "vpc 1.3"
# The product token equals the code service name (== host subdomain), validated
# against every request_example in data/api_docs.json. A service missing from
# the map gets NO header (current behavior = latest current; safer than a
# guessed header the gateway may reject). See docs/API-VERSIONING.md.
#   * kill-switch:      SCP_API_VERSION_PIN=false        (default: enabled)
#   * per-service pin:  SCP_API_VERSION_OVERRIDES="vpc=1.2,firewall=1.0"
#     (the minimal hook for back-compat regression against older versions)
API_VERSION_HEADER = "Scp-Api-Version"
_VERSIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "api_versions.json"


@lru_cache(maxsize=1)
def _pinned_versions() -> dict:
    """service -> latest current version, from data/api_versions.json (cached)."""
    try:
        data = _json.loads(_VERSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    products = data.get("products")
    if not isinstance(products, dict):
        return {}
    return {str(k): str(v) for k, v in products.items() if k and v}


def _version_overrides() -> dict:
    """Parse SCP_API_VERSION_OVERRIDES ("svc=ver,svc=ver"); malformed items skipped.

    Read per call (not cached) so tests / back-compat runs can flip it live.
    """
    out: dict = {}
    for item in _os.environ.get("SCP_API_VERSION_OVERRIDES", "").split(","):
        svc, sep, ver = item.strip().partition("=")
        svc, ver = svc.strip(), ver.strip()
        if sep and svc and ver:
            out[svc] = ver
    return out


def api_version_header(service: str | None) -> dict:
    """Headers to pin `service`'s API microversion — {} when nothing applies.

    Empty when: no service, kill-switch off, or no known version for it.
    `<svc>-dr` aliases pin the base service's version (same product).
    """
    if not service:
        return {}
    if _os.environ.get("SCP_API_VERSION_PIN", "").strip().lower() in (
            "0", "false", "no", "off"):
        return {}
    svc = service[:-3] if service.endswith("-dr") else service
    version = _version_overrides().get(svc) or _pinned_versions().get(svc)
    if not version:
        return {}
    return {API_VERSION_HEADER: f"{svc} {version}"}


@dataclass
class Response:
    status: int
    elapsed_ms: float
    headers: dict
    body: Any
    raw_text: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ApiClient:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or settings
        self.signer = build_signer(self.cfg)
        self.session = requests.Session()
        # Warm per-host connection pool — FUNDAMENTAL, so every execution path (pytest
        # CRUD / xdist, the dag dynamic dispatcher, smoke, coverage probes) reuses it
        # rather than it living only in one driver. urllib3 caches ``pool_connections``
        # HOST pools (LRU); we hit ~60 SCP service hosts, so the default 10 evicts host
        # pools and a re-hit host REOPENS a connection — a fresh cold connect that can
        # 503 under a gateway/proxy connection burst. Keep ALL service host pools warm
        # (>> #hosts) with per-host headroom. Env-tunable.
        from requests.adapters import HTTPAdapter as _HTTPAdapter
        _adapter = _HTTPAdapter(
            pool_connections=int(_os.getenv("SCP_POOL_CONNECTIONS", "96")),
            pool_maxsize=int(_os.getenv("SCP_POOL_MAXSIZE", "40")))
        self.session.mount("http://", _adapter)
        self.session.mount("https://", _adapter)

    # -- safety --------------------------------------------------------------
    def _guard(self, method: str) -> None:
        m = method.upper()
        if m in MUTATING and not self.cfg.allow_mutations:
            raise MutationBlocked(
                f"{m} blocked: set SCP_ALLOW_MUTATIONS=true to enable mutating calls.")
        if m in DESTRUCTIVE and not self.cfg.allow_destructive:
            raise MutationBlocked(
                f"{m} blocked: set SCP_ALLOW_DESTRUCTIVE=true to enable destructive calls.")

    # -- request -------------------------------------------------------------
    def request(self, method: str, path: str, *, params: dict | None = None,
                json: Any | None = None, headers: dict | None = None,
                service: str | None = None, timeout: float | None = None,
                retry: bool = True) -> Response:
        self._guard(method)
        # best-effort callers (e.g. read-only coverage probes) pass timeout=...,
        # retry=False so a slow/unreachable endpoint costs one short deadline
        # instead of cfg.timeout x cfg.max_retries + backoff (~42s).
        _to = timeout or self.cfg.timeout
        _max = self.cfg.max_retries if retry else 1
        if path.startswith("http"):
            url = path
        else:
            url = f"{self.cfg.resolve_base_url(service)}{path}"
        # Fold query params into the URL BEFORE signing: SCP signs the full URL,
        # so the signed string must include the query string we actually send.
        if params:
            from urllib.parse import urlencode
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        # Sign EXACTLY the bytes that go on the wire. `requests` re-quotes the
        # URL it sends (requote_uri/IDNA), so pre-normalize with requests' own
        # preparation and sign THAT. Preparation is idempotent (proven in
        # tests/offline/test_hmac_signing.py), so the session emits these exact
        # bytes — the gateway's HMAC check sees the same URL we signed.
        # Gated by SCP_SIGN_ENCODEURI (default on; set false for the legacy
        # raw-assembled-URL signing, which 401s any %XX-carrying query).
        if sign_encodeuri_wire_enabled():
            _prepared = requests.PreparedRequest()
            _prepared.prepare_url(url, None)
            url = _prepared.url
        body = _json.dumps(json).encode("utf-8") if json is not None else b""
        # Pin the API microversion for every live call (smoke/CRUD/sweep all
        # build requests here). Computed once per request; headers are NOT part
        # of the HMAC signing string, so this does not interact with signing.
        # An explicit caller-supplied Scp-Api-Version still wins (hdrs.update
        # below runs after).
        _ver_hdr = api_version_header(service)
        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, _max + 1):
            hdrs = {"Accept": "application/json"}
            if json is not None:
                hdrs["Content-Type"] = "application/json"
            hdrs.update(_ver_hdr)
            hdrs.update(self.signer.headers(method, url, body))
            if headers:
                hdrs.update(headers)
            start = time.monotonic()
            try:
                resp = self.session.request(
                    method.upper(), url,
                    data=body if json is not None else None,
                    headers=hdrs, timeout=_to)
            except requests.RequestException as exc:
                last_exc = exc
                if (attempt < _max
                        and method.upper() not in NO_RETRY_ON_EXCEPTION):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 16)
                    continue
                raise
            if resp.status_code in RETRY_STATUS:
                _bump_retry_status()
                if attempt < _max:
                    _wait = backoff
                    if resp.status_code == 429:
                        try:
                            _wait = max(_wait, float(resp.headers.get("Retry-After", 0)))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(min(_wait, 30))
                    backoff = min(backoff * 2, 16)
                    continue
            elapsed = (time.monotonic() - start) * 1000
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            return Response(resp.status_code, elapsed, dict(resp.headers), parsed, resp.text)
        raise last_exc  # pragma: no cover

    # convenience verbs
    def get(self, path, **kw):    return self.request("GET", path, **kw)
    def post(self, path, **kw):   return self.request("POST", path, **kw)
    def put(self, path, **kw):    return self.request("PUT", path, **kw)
    def patch(self, path, **kw):  return self.request("PATCH", path, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)
