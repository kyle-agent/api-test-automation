#!/usr/bin/env python3
"""Enumerate the real resource paths on the OpenStack-style service host.

vpc.<region>.<env>.samsungsdscloud.com/ returns a 200 OpenStack-style version
doc (so it IS the API host), but /v1/vpcs -> 404. The version root points to
/v1; GET /v1 should enumerate the actual resource collections — which may be
named differently than the docs' '/v1/vpcs'. Probe the host exhaustively with
FULL bodies so we can read the real paths.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

import requests

from framework.config import settings as cfg

TIMEOUT = 15
HOST = f"https://vpc.{cfg.region or 'kr-west1'}.{cfg.env_code or 'e'}.samsungsdscloud.com"


def signed(method, url):
    ts = str(int(time.time() * 1000))
    p = urlsplit(url)
    res = p.path + (("?" + p.query) if p.query else "")
    msg = "\n".join([method.upper(), res, ts, cfg.access_key]).encode()
    sig = base64.b64encode(hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    h = {cfg.hmac_access_header: cfg.access_key, cfg.hmac_timestamp_header: ts,
         cfg.hmac_signature_header: sig}
    if cfg.project_id:
        h[cfg.project_header] = cfg.project_id
    return h


def probe(path, auth=False, full=1200):
    url = HOST + path
    hdr = {"Accept": "application/json"}
    if auth:
        hdr.update(signed("GET", url))
    try:
        r = requests.get(url, headers=hdr, timeout=TIMEOUT, allow_redirects=False)
    except Exception as exc:
        print(f"GET {path} {'(signed)' if auth else ''} -> ERR {exc!r}"); return
    ct = r.headers.get("content-type", "")
    print(f"GET {path} {'(signed)' if auth else ''} -> {r.status_code} [{ct}]")
    print(f"    {r.text.replace(chr(10), ' ')[:full]}")


def main():
    print(f"HOST = {HOST}\n")
    probe("/")          # version doc (full)
    probe("/v1")        # KEY: resource collection listing
    probe("/v1/", auth=True)
    # candidate resource names (docs say 'vpcs'; OpenStack neutron uses 'networks')
    for p in ("/v1/vpcs", "/v1/networks", "/v1/vpc", "/v2.0", "/v2.0/networks"):
        probe(p, auth=True, full=300)
    # maybe the collection needs a project/account segment — show what /v1 says first


if __name__ == "__main__":
    main()
