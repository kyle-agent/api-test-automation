#!/usr/bin/env python3
"""Verify the corrected SCP HMAC auth against real endpoints.

Auth scheme (from the SCP OpenAPI security guide / official sample):
    message   = method + encodeURI(url) + timestamp + accessKey + projectId + clientType
    signature = Base64(HMAC_SHA256(message, secretKey))
    headers   = X-Cmp-AccessKey/Signature/Timestamp/ClientType=OpenApi/ProjectId/Language

Probes vpc (regional) and product (global) with: (A) the framework signer
(signs the path), and (B) a variant that signs the FULL URL — to confirm which
form encodeURI(url) expects. Needs SCP_PROJECT_ID set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote, urlsplit

import requests

from framework.config import settings as cfg
from framework.auth import build_signer

SAFE = "!#$&'()*+,/:;=?@~"
signer = build_signer(cfg)


def sign_full_url(method, full_url, ts):
    enc = quote(full_url, safe=SAFE + ":/")
    msg = (method.upper() + enc + ts + cfg.access_key + cfg.project_id + cfg.client_type).encode()
    sig = base64.b64encode(hmac.new(cfg.secret_key.encode(), msg, hashlib.sha256).digest()).decode()
    return {cfg.hmac_access_header: cfg.access_key, cfg.hmac_timestamp_header: ts,
            cfg.hmac_signature_header: sig, cfg.client_type_header: cfg.client_type,
            cfg.project_header: cfg.project_id, cfg.language_header: cfg.language}


def call(label, url, headers):
    try:
        r = requests.get(url, headers={"Accept": "application/json", **headers}, timeout=15)
    except Exception as exc:
        print(f"{label}: ERR {exc!r}"); return
    print(f"{label}: -> {r.status_code}  {r.text.replace(chr(10),' ')[:200]}")


def main():
    print(f"config: project_id_set={bool(cfg.project_id)} client_type={cfg.client_type!r} "
          f"access_header={cfg.hmac_access_header} project_header={cfg.project_header}")
    if not cfg.project_id:
        print("\n!! SCP_PROJECT_ID is NOT set — X-Cmp-ProjectId is required and signed. "
              "Set it (repo secret) and re-run.\n")

    for name, url in (("vpc /v1/vpcs", cfg.resolve_base_url("vpc") + "/v1/vpcs"),
                      ("product /v1/products", cfg.resolve_base_url("product") + "/v1/products")):
        print(f"\n##### {name}  ({url})")
        ts = str(int(time.time() * 1000))
        call("A signer(path)", url, signer.headers("GET", url))
        call("B sign(full-url)", url, sign_full_url("GET", url, ts))


if __name__ == "__main__":
    main()
