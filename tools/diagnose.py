#!/usr/bin/env python3
"""Discover the real SCP Open API endpoint.

Every <service>.<...>.samsungsdscloud.com host we've tried returns a Spring
Cloud Gateway "no route" 404 (incl. requestId) for both no-auth and signed
requests — so auth isn't even reached; the host/path has no route. This probes
the root path and a set of candidate host patterns from the CI runner (which
*can* reach the backend) and reports DNS + status + body, to locate a host/path
that actually routes.
"""
from __future__ import annotations

import socket
import requests

from framework.config import settings as cfg

TIMEOUT = 12


def resolves(host: str) -> str:
    try:
        return socket.getaddrinfo(host, 443)[0][4][0]
    except OSError as e:
        return f"DNS-FAIL ({e.errno})"


def probe(label: str, url: str):
    host = url.split("//", 1)[1].split("/", 1)[0]
    ip = resolves(host)
    line = f"{label:42} {url}\n    dns={ip}"
    if ip.startswith("DNS-FAIL"):
        print(line + " -> skip"); return
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=TIMEOUT,
                         allow_redirects=False)
        loc = r.headers.get("location", "")
        srv = r.headers.get("server", "")
        body = r.text.replace("\n", " ")[:140]
        print(line + f" -> {r.status_code} server={srv!r} loc={loc!r}\n    body: {body}")
    except Exception as exc:
        print(line + f" -> ERR {exc!r}")


def main():
    print(f"config: region={cfg.region!r} env={cfg.env_code!r}\n")

    print("##### A. root vs /v1 on the current hosts (does the host route anything?)")
    for base in (cfg.resolve_base_url("vpc"), cfg.resolve_base_url("product")):
        probe("root /", base + "/")
        probe("GET /v1/vpcs|products", base + ("/v1/vpcs" if "vpc" in base else "/v1/products"))
        probe("GET /actuator/health", base + "/actuator/health")
        probe("GET /swagger-ui/index.html", base + "/swagger-ui/index.html")
        print()

    print("##### B. candidate API host patterns (probe GET /v1/vpcs)")
    region, env = cfg.region or "kr-west1", cfg.env_code or "e"
    candidates = [
        f"https://vpc-api.{region}.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://vpc.api.{region}.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://api-vpc.{region}.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://api.{region}.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://api.{region}.{env}.samsungsdscloud.com/vpc/v1/vpcs",
        f"https://{region}.{env}.samsungsdscloud.com/vpc/v1/vpcs",
        f"https://api.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://openapi.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://openapi.samsungsdscloud.com/v1/vpcs",
        f"https://api.samsungsdscloud.com/v1/vpcs",
        f"https://gw.{env}.samsungsdscloud.com/vpc/v1/vpcs",
        f"https://core.{env}.samsungsdscloud.com/v1/vpcs",
        f"https://vpc.{region}.{env}.samsungsdscloud.com/v2/vpcs",
    ]
    for url in candidates:
        probe("candidate", url)


if __name__ == "__main__":
    main()
