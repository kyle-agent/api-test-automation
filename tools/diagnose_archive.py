#!/usr/bin/env python3
"""Targeted diagnosis of archivestorage 401 HmacValidFail.

Every other SCP service authenticates with the same signer (full-URL HMAC), but
archivestorage returns 401 HmacValidFail. Probe one archivestorage endpoint with
several signing variants + host variants and dump status/headers/body to find
what archivestorage expects.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

import requests

from framework.config import settings as cfg

PATH = "/v1/buckets"
HOSTS = {
    "regional": f"https://archivestorage.{cfg.region}.{cfg.env_code}.samsungsdscloud.com",
    "global": f"https://archivestorage.{cfg.env_code}.samsungsdscloud.com",
}
SAFE = "!#$&'()*+,/:;=?@~"


def enc(s):
    from urllib.parse import quote
    return quote(s, safe=SAFE)


def sign(method, url_for_sign):
    ts = str(int(time.time() * 1000))
    msg = (method.upper() + enc(url_for_sign) + ts + cfg.access_key + cfg.client_type).encode()
    sig = base64.b64encode(hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    return {cfg.hmac_access_header: cfg.access_key, cfg.hmac_timestamp_header: ts,
            cfg.hmac_signature_header: sig, cfg.client_type_header: cfg.client_type,
            cfg.language_header: cfg.language}


def call(label, url, headers):
    try:
        r = requests.get(url, headers={"Accept": "application/json", **headers}, timeout=15)
    except Exception as e:
        print(f"{label}: ERR {e!r}"); return
    hk = {k: v for k, v in r.headers.items() if k.lower() in
          ("server", "www-authenticate", "x-cmp-trace-id", "x-request-id", "content-type")}
    print(f"{label}: {r.status_code} {hk}\n    {r.text.replace(chr(10),' ')[:200]}")


def main():
    for hname, base in HOSTS.items():
        url = base + PATH
        host = base.split("//", 1)[1]
        try:
            import socket; socket.getaddrinfo(host, 443)
        except OSError:
            print(f"\n## {hname} {host}: DNS-FAIL (skip)"); continue
        print(f"\n## {hname}  {url}")
        full = url
        path_only = urlsplit(url).path
        call("  A full-url sign ", url, sign("GET", full))
        call("  B path-only sign", url, sign("GET", path_only))
        call("  C no-auth        ", url, {})


if __name__ == "__main__":
    main()
