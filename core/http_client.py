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
import time
from dataclasses import dataclass
from typing import Any

import requests

from .auth import build_signer, sign_encodeuri_wire_enabled
from .config import Settings, settings

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
DESTRUCTIVE = {"DELETE"}
RETRY_STATUS = {502, 503, 504}

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
        import os as _os
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
        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, _max + 1):
            hdrs = {"Accept": "application/json"}
            if json is not None:
                hdrs["Content-Type"] = "application/json"
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
                    time.sleep(backoff)
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
