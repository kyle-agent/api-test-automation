#!/usr/bin/env python3
"""Verify the SCP HMAC auth (per docs.e API Reference 'Common / API 호출하기').

    message   = method + encodeURI(url) + timestamp + accessKey + clientType
    signature = Base64(HMAC_SHA256(message, secretKey))
    headers   = Scp-Accesskey, Scp-Signature, Scp-Timestamp,
                Scp-ClientType=Openapi, Accept-Language   (NO projectId)

Probes vpc (regional) and product (global) with the framework signer, and also
tries signing the path vs the full URL to confirm which the gateway expects.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote, urlsplit

import requests

from framework.config import settings as cfg
from framework.auth import build_signer, _encode_uri

signer = build_signer(cfg)


def manual_headers(method, url, use_full):
    ts = str(int(time.time() * 1000))
    if use_full:
        signed = url
    else:
        p = urlsplit(url)
        signed = p.path + (("?" + p.query) if p.query else "")
    msg = (method.upper() + _encode_uri(signed) + ts + cfg.access_key + cfg.client_type).encode()
    sig = base64.b64encode(hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    return {cfg.hmac_access_header: cfg.access_key, cfg.hmac_timestamp_header: ts,
            cfg.hmac_signature_header: sig, cfg.client_type_header: cfg.client_type,
            cfg.language_header: cfg.language}


def call(label, url, headers):
    try:
        r = requests.get(url, headers={"Accept": "application/json", **headers}, timeout=15)
    except Exception as exc:
        print(f"{label}: ERR {exc!r}"); return
    print(f"{label}: -> {r.status_code}  {r.text.replace(chr(10),' ')[:220]}")


def main():
    print(f"client_type={cfg.client_type!r} access_header={cfg.hmac_access_header} "
          f"lang_header={cfg.language_header} sign_full_url={cfg.sign_full_url}")
    for name, url in (("vpc /v1/vpcs", cfg.resolve_base_url("vpc") + "/v1/vpcs"),
                      ("product /v1/products", cfg.resolve_base_url("product") + "/v1/products")):
        print(f"\n##### {name}  ({url})")
        call("framework signer", url, signer.headers("GET", url))
        call("manual full-url ", url, manual_headers("GET", url, use_full=True))
        call("manual path     ", url, manual_headers("GET", url, use_full=False))


if __name__ == "__main__":
    main()
