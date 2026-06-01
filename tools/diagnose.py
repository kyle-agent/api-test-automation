#!/usr/bin/env python3
"""Pin down the real SCP Open API gateway path.

Findings so far:
  * vpc.kr-west1.e.samsungsdscloud.com/ -> 200 OpenStack-style version doc
    (real host, /v1 exists) but /v1/vpcs -> 404.
  * openapi.samsungsdscloud.com (Cloudflare) -> 500 {"service":"GW",...} — looks
    like the actual Open API gateway (a route exists; it errored, not 404).

This probes both with FULL bodies + headers, many path shapes, and signed vs
no-auth, to find the path that returns 200/401 (a real route).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import socket
import time
from urllib.parse import urlsplit

import requests

from framework.config import settings as cfg

TIMEOUT = 15
REGION = cfg.region or "kr-west1"
ENV = cfg.env_code or "e"


def signed(method: str, url: str) -> dict:
    ts = str(int(time.time() * 1000))
    parts = urlsplit(url)
    res = parts.path + (("?" + parts.query) if parts.query else "")
    msg = "\n".join([method.upper(), res, ts, cfg.access_key]).encode()
    sig = base64.b64encode(hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    h = {cfg.hmac_access_header: cfg.access_key, cfg.hmac_timestamp_header: ts,
         cfg.hmac_signature_header: sig}
    if cfg.project_id:
        h[cfg.project_header] = cfg.project_id
    return h


def probe(label, url, headers=None, full=400):
    host = url.split("//", 1)[1].split("/", 1)[0]
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        print(f"{label}: {url}\n    DNS-FAIL"); return
    try:
        r = requests.get(url, headers={"Accept": "application/json", **(headers or {})},
                         timeout=TIMEOUT, allow_redirects=False)
    except Exception as exc:
        print(f"{label}: {url}\n    ERR {exc!r}"); return
    hdrs = {k: v for k, v in r.headers.items()
            if k.lower() in ("server", "content-type", "www-authenticate", "location",
                             "x-cmp-trace-id", "x-request-id", "cf-ray", "x-cmp-error")}
    print(f"{label}: {url}\n    -> {r.status_code} {hdrs}\n    body: {r.text.replace(chr(10),' ')[:full]}")


def main():
    print("##### 1. vpc host root version doc (full)")
    probe("vpc /", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/", full=900)
    probe("vpc /v1", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/v1", full=900)
    probe("vpc /v1/ (signed)", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/v1/",
          headers=signed("GET", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/v1/"), full=900)
    probe("vpc /v1/vpcs (signed)", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/v1/vpcs",
          headers=signed("GET", f"https://vpc.{REGION}.{ENV}.samsungsdscloud.com/v1/vpcs"))

    print("\n##### 2. openapi.samsungsdscloud.com gateway (full error + path shapes)")
    GW = "https://openapi.samsungsdscloud.com"
    probe("gw /", GW + "/", full=600)
    paths = [
        "/v1/vpcs",
        f"/vpc/{REGION}/v1/vpcs",
        f"/{REGION}/vpc/v1/vpcs",
        "/vpc/v1/vpcs",
        "/networking/vpc/v1/vpcs",
        f"/v1/vpc/{REGION}/vpcs",
        f"/{ENV}/{REGION}/vpc/v1/vpcs",
        "/vpc/v1/vpcs/",
    ]
    for p in paths:
        probe(f"gw {p} (noauth)", GW + p, full=300)
        probe(f"gw {p} (signed)", GW + p, headers=signed("GET", GW + p), full=300)

    print("\n##### 3. openapi with region/env subdomains")
    for h in (f"https://openapi.{REGION}.{ENV}.samsungsdscloud.com",
              f"https://openapi.{ENV}.samsungsdscloud.com",
              f"https://{REGION}.openapi.samsungsdscloud.com"):
        probe(f"{h}/v1/vpcs (signed)", h + "/v1/vpcs", headers=signed("GET", h + "/v1/vpcs"), full=250)


if __name__ == "__main__":
    main()
