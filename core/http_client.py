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
_EP_VERSIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "api_endpoint_versions.json"


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


@lru_cache(maxsize=1)
def _endpoint_versions() -> dict:
    """(service) -> {(method, n_segments): [(literal_pos_segments, version)]}.

    필드 실측 2026-07-16 (run fe88): 엔드포인트별 지원 버전은 제품 최신과
    **1,416개 중 903개(64%)가 다르다** — 제품 단위 핀은 그 메서드들에서 406
    NoSuchVersion("API version X is not supported on this method")을 만든다
    (예: scf 제품 1.4 vs showcloudfunctionmetrics 1.3). 그래서 핀의 1차
    소스는 data/api_endpoint_versions.json(스펙 스냅샷의 endpoints[].support),
    제품 핀은 미등재 엔드포인트의 폴백이다."""
    try:
        data = _json.loads(_EP_VERSIONS_FILE.read_text(encoding="utf-8"))
        services = data.get("services") or {}
    except (OSError, ValueError):
        return {}
    idx: dict = {}
    for svc, eps in services.items():
        for key, ver in eps.items():
            method, _, shape = key.partition(" ")
            segs = shape.split("/")
            idx.setdefault(svc, {}).setdefault((method, len(segs)), []).append(
                (tuple(segs), str(ver)))
    return idx


def _endpoint_version_for(svc: str, method: str | None, path: str | None):
    """live path를 카탈로그 shape에 매칭해 그 엔드포인트의 지원 버전을 찾는다.
    모호(0 또는 2+ 매치)하면 None — 호출측이 제품 핀으로 폴백."""
    if not (method and path):
        return None
    cands = _endpoint_versions().get(svc, {}).get(
        (method.upper(), len(path.split("?")[0].split("/"))))
    if not cands:
        return None
    segs = path.split("?")[0].split("/")
    matches = [ver for shape, ver in cands
               if all(s == "{}" or s == seg for s, seg in zip(shape, segs))]
    return matches[0] if len(set(matches)) == 1 else None


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


def api_version_header(service: str | None, method: str | None = None,
                       path: str | None = None) -> dict:
    """Headers to pin the API microversion — {} when nothing applies.

    우선순위: env 오버라이드 > **엔드포인트별 지원 버전**(method+path shape
    매칭 — 제품 최신과 64%가 달라 제품 핀만으로는 406 NoSuchVersion) >
    제품 최신(폴백). `<svc>-dr` aliases pin the base service's product.
    Empty when: no service, kill-switch off, or no known version at all.
    """
    if not service:
        return {}
    if _os.environ.get("SCP_API_VERSION_PIN", "").strip().lower() in (
            "0", "false", "no", "off"):
        return {}
    svc = service[:-3] if service.endswith("-dr") else service
    version = (_version_overrides().get(svc)
               or _endpoint_version_for(svc, method, path)
               or _pinned_versions().get(svc))
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
        _ver_hdr = api_version_header(service, method, path)
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
            # 406 NoSuchVersion 안전망: 우리가 붙인 버전 핀이 이 메서드에서
            # 지원 안 되면(스냅샷 낡음/매핑 오류) 핀 없이 1회 재시도 — 서버
            # 기본(latest current)으로라도 커버리지를 살린다. 재시도 사실은
            # 응답 헤더에 표식(X-Apitest-Version-Fallback)으로 남겨 콘솔에서
            # 구분 가능하게. (오너 2026-07-16: scf metrics 1.3 vs 제품 1.4 실측)
            if (resp.status_code == 406 and _ver_hdr
                    and "NoSuchVersion" in (resp.text or "")):
                _sent = _ver_hdr.get(API_VERSION_HEADER, "")
                print(f"[api-version] 406 NoSuchVersion ({_sent}) "
                      f"{method} {path} — 핀 없이 재시도")
                # 문서 기술 버전 ≠ 실제 지원 = 컨포먼스 finding (오너 2026-07-16:
                # "문서에 기술된 버전이 달라서이면 컨포먼스 기록해두고 버전
                # 헤더 없이 해봐야지"). best-effort — 기록 실패가 호출을 깨면 안 됨.
                try:
                    from core import results as _results
                    _results.record_finding(_results.Finding(
                        endpoint_key=f"{method.upper()} {path.split('?')[0]}",
                        rule_id="versioning.doc-version-not-supported",
                        severity="yellow", source="runtime",
                        detail=(f"docs-derived pin '{_sent}' -> 406 NoSuchVersion"
                                f" ({(resp.text or '')[:160]}) — served without"
                                " the header instead (latest current)")))
                except Exception:  # noqa: BLE001
                    pass
                _ver_hdr = {}
                hdrs2 = {"Accept": "application/json"}
                if json is not None:
                    hdrs2["Content-Type"] = "application/json"
                hdrs2.update(self.signer.headers(method, url, body))
                if headers:
                    hdrs2.update(headers)
                try:
                    resp = self.session.request(
                        method.upper(), url,
                        data=body if json is not None else None,
                        headers=hdrs2, timeout=_to)
                except requests.RequestException:
                    pass    # 폴백 실패 — 원래 406으로 계속
                else:
                    resp.headers = dict(resp.headers)
                    resp.headers["X-Apitest-Version-Fallback"] = _sent
            elapsed = (time.monotonic() - start) * 1000
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            out_headers = dict(resp.headers)
            if _ver_hdr.get(API_VERSION_HEADER):
                # 콘솔 가시성 (오너 2026-07-16: "헤더로 뭘 보냈는지 알 수가
                # 없네") — 보낸 버전 핀을 응답 레코드에 동봉해 스텝 상세에서
                # 확인 가능하게. 서명/자격 헤더는 절대 동봉하지 않는다.
                out_headers["X-Apitest-Sent-Api-Version"] = \
                    _ver_hdr[API_VERSION_HEADER]
            return Response(resp.status_code, elapsed, out_headers, parsed, resp.text)
        raise last_exc  # pragma: no cover

    # convenience verbs
    def get(self, path, **kw):    return self.request("GET", path, **kw)
    def post(self, path, **kw):   return self.request("POST", path, **kw)
    def put(self, path, **kw):    return self.request("PUT", path, **kw)
    def patch(self, path, **kw):  return self.request("PATCH", path, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)
