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

from .auth import build_signer
from .config import Settings, settings

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
DESTRUCTIVE = {"DELETE"}
RETRY_STATUS = {502, 503, 504}


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
                service: str | None = None) -> Response:
        self._guard(method)
        if path.startswith("http"):
            url = path
        else:
            url = f"{self.cfg.resolve_base_url(service)}{path}"
        # Fold query params into the URL BEFORE signing: SCP signs the full URL,
        # so the signed string must include the query string we actually send.
        if params:
            from urllib.parse import urlencode
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        body = _json.dumps(json).encode("utf-8") if json is not None else b""
        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
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
                    headers=hdrs, timeout=self.cfg.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.cfg.max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 16)
                    continue
                raise
            if resp.status_code in RETRY_STATUS and attempt < self.cfg.max_retries:
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
