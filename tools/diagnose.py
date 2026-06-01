#!/usr/bin/env python3
"""One-shot diagnostics for the systemic 404 on SCP API calls.

The sandbox can't reach the API backend (edge 503), so this runs in CI where the
runner CAN reach it. It probes a few request variants against one global and one
regional endpoint and prints status + response headers + body, so we can tell:

  * no-auth 401 vs 404  -> is this an auth problem or a routing/path problem?
  * full response headers -> gateway type, any WWW-Authenticate / x-cmp-* hints
  * path variants (prefix / trailing slash) -> is the path wrong?
  * header-name variants -> are the auth header names wrong?

Read the output from the PR comment posted by the workflow.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

import requests

from framework.config import settings as cfg

INTERESTING_HEADERS = (
    "server", "www-authenticate", "content-type", "x-cmp-trace-id",
    "x-request-id", "x-envoy-upstream-service-time", "via", "x-cmp-error",
)


def signed_headers(method: str, url: str, access_overrides=None):
    ts = str(int(time.time() * 1000))
    parts = urlsplit(url)
    resource = parts.path + (("?" + parts.query) if parts.query else "")
    msg = "\n".join([method.upper(), resource, ts, cfg.access_key]).encode()
    sig = base64.b64encode(
        hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    h = {
        cfg.hmac_access_header: cfg.access_key,
        cfg.hmac_timestamp_header: ts,
        cfg.hmac_signature_header: sig,
    }
    if cfg.project_id:
        h[cfg.project_header] = cfg.project_id
    if access_overrides:
        h.update(access_overrides)
    return h


def show(label: str, method: str, url: str, headers: dict):
    print(f"\n=== {label}: {method} {url}")
    print(f"    sent headers: {sorted(headers)}")
    try:
        r = requests.request(method, url, headers={"Accept": "application/json", **headers},
                             timeout=25)
    except Exception as exc:
        print(f"    ERROR: {exc!r}")
        return
    print(f"    -> HTTP {r.status_code}")
    for k in INTERESTING_HEADERS:
        if k in r.headers:
            print(f"    resp {k}: {r.headers[k]}")
    body = r.text.replace("\n", " ")[:400]
    print(f"    body: {body}")


def main():
    print(f"config: region={cfg.region!r} env={cfg.env_code!r} "
          f"access_key_set={bool(cfg.access_key)} secret_set={bool(cfg.secret_key)} "
          f"project_id_set={bool(cfg.project_id)}")
    print(f"auth headers: access={cfg.hmac_access_header} sig={cfg.hmac_signature_header} "
          f"ts={cfg.hmac_timestamp_header} project={cfg.project_header}")

    targets = [
        ("global product", cfg.resolve_base_url("product"), "/v1/products"),
        ("regional vpc", cfg.resolve_base_url("vpc"), "/v1/vpcs"),
    ]
    for name, base, path in targets:
        url = base + path
        print(f"\n########## {name} :: base={base}")
        # 1. no auth at all -> 401 means auth matters; 404 means path/routing
        show("no-auth", "GET", url, {})
        # 2. current signed scheme
        show("signed (current)", "GET", url, signed_headers("GET", url))
        # 3. trailing slash
        show("signed + trailing slash", "GET", url + "/", signed_headers("GET", url + "/"))
        # 4. Authorization: Bearer <accesskey> (alt scheme)
        show("bearer access_key", "GET", url, {"Authorization": f"Bearer {cfg.access_key}"})
        # 5. common alt header names
        alt = {"X-Cmp-AccessKey": cfg.access_key, "Cmp-AccessKey": cfg.access_key,
               "apikey": cfg.access_key, "X-Cmp-Api-Key": cfg.access_key}
        show("alt header names", "GET", url, alt)

    # path-prefix probes on vpc (does the backend want a prefix?)
    base = cfg.resolve_base_url("vpc")
    for p in ("/vpc/v1/vpcs", "/api/v1/vpcs", "/networking/v1/vpcs", "/v1/vpc/vpcs"):
        show(f"path-probe {p}", "GET", base + p, signed_headers("GET", base + p))


if __name__ == "__main__":
    main()
