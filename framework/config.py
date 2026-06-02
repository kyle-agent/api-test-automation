"""Runtime configuration for the SCP API regression suite.

All secrets and environment-specific values come from environment variables
(or a local .env file) — nothing is ever hardcoded. See .env.example.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency). Existing env vars win."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    """Like os.environ.get, but an empty value counts as unset.

    CI passes optional inputs as empty strings (e.g. SCP_HOST_TEMPLATE: ${{ vars.X }}
    with X unset), which would otherwise shadow the intended default.
    """
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Account-scoped (region-less) services, verified by DNS against the platform:
# their host is <service>.<env>.samsungsdscloud.com (no region segment).
DEFAULT_GLOBAL_SERVICES = frozenset({
    "billingplan", "budget", "cloudcontrol", "costexplorer", "iam",
    "organization", "pricing", "product", "quota", "resourcemanager", "support",
})


def _global_services() -> frozenset:
    """Global-service set; SCP_GLOBAL_SERVICES (comma-separated) overrides the default."""
    raw = _env("SCP_GLOBAL_SERVICES")
    if not raw:
        return DEFAULT_GLOBAL_SERVICES
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _json_env(name: str) -> dict:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(val, dict):
        raise RuntimeError(f"{name} must be a JSON object mapping service -> host/url")
    return val


@dataclass(frozen=True)
class Settings:
    # --- Endpoint (per-service hosts) ---------------------------------------
    # SCP Open API endpoints are PER SERVICE, not a single gateway. The host
    # follows: https://<service>.<region>.<env>.samsungsdscloud.com
    #   e.g. https://vpc.kr-west1.e.samsungsdscloud.com  (+ path /v1/vpcs)
    # Path roots collide across services (e.g. /v1/clusters is used by ske,
    # mariadb, mysql, ...), so each call must target its own service host.
    #
    # Services come in two flavours (verified by DNS against the platform):
    #   * regional: https://<service>.<region>.<env>.samsungsdscloud.com  (e.g. vpc)
    #   * global  : https://<service>.<env>.samsungsdscloud.com           (e.g. product)
    # Resolution order for a given service:
    #   1. SCP_SERVICE_HOSTS override map  (JSON: {"<service>": "<host-or-url>"})
    #   2. global template (if service is global)   — no region
    #   3. regional template + region + env          (the common case)
    #   4. SCP_BASE_URL                              (explicit single-host fallback)
    region: str = field(default_factory=lambda: _env("SCP_REGION"))
    env_code: str = field(default_factory=lambda: _env("SCP_ENV", "e"))
    host_template: str = field(default_factory=lambda: _env(
        "SCP_HOST_TEMPLATE", "https://{service}.{region}.{env}.samsungsdscloud.com"))
    global_host_template: str = field(default_factory=lambda: _env(
        "SCP_GLOBAL_HOST_TEMPLATE", "https://{service}.{env}.samsungsdscloud.com"))
    # Account-scoped services that have no region segment in their host.
    global_services: frozenset = field(default_factory=lambda: _global_services())
    # Optional explicit overrides for services whose API subdomain differs from
    # the catalog service name, or to pin a full URL.
    service_hosts: dict = field(default_factory=lambda: _json_env("SCP_SERVICE_HOSTS"))
    # Explicit single-host fallback (rarely enough on its own — see note above).
    base_url: str = field(default_factory=lambda: _env("SCP_BASE_URL").rstrip("/"))

    # --- Credentials --------------------------------------------------------
    access_key: str = field(default_factory=lambda: _env("SCP_ACCESS_KEY"))
    secret_key: str = field(default_factory=lambda: _env("SCP_SECRET_KEY"))
    # Optional tenant/project scoping headers used by many SCP services.
    project_id: str = field(default_factory=lambda: _env("SCP_PROJECT_ID"))

    # --- Auth scheme (pluggable / configurable) -----------------------------
    # The exact HMAC header names + signing string must be confirmed against the
    # SCP User Guide / a real 200 response. They are configurable so the suite
    # can be aligned without code changes. Defaults follow the documented
    # Per the SCP API Reference "Common / API 호출하기" guide: HMAC-SHA256 over
    #   method + encodeURI(url) + timestamp + accessKey + clientType   (no projectId)
    # Base64-encoded, sent in Scp-* headers; clientType value is "Openapi".
    auth_scheme: str = field(default_factory=lambda: _env("SCP_AUTH_SCHEME", "hmac"))
    hmac_access_header: str = field(
        default_factory=lambda: _env("SCP_HMAC_ACCESS_HEADER", "Scp-Accesskey"))
    hmac_signature_header: str = field(
        default_factory=lambda: _env("SCP_HMAC_SIGNATURE_HEADER", "Scp-Signature"))
    hmac_timestamp_header: str = field(
        default_factory=lambda: _env("SCP_HMAC_TIMESTAMP_HEADER", "Scp-Timestamp"))
    client_type_header: str = field(
        default_factory=lambda: _env("SCP_CLIENT_TYPE_HEADER", "Scp-ClientType"))
    client_type: str = field(default_factory=lambda: _env("SCP_CLIENT_TYPE", "Openapi"))
    language_header: str = field(
        default_factory=lambda: _env("SCP_LANGUAGE_HEADER", "Accept-Language"))
    language: str = field(default_factory=lambda: _env("SCP_LANGUAGE", "en-US"))
    # Whether the signed url is the full URL (default) or just the path+query.
    sign_full_url: bool = field(default_factory=lambda: _bool("SCP_SIGN_FULL_URL", True))

    # --- Run behaviour ------------------------------------------------------
    timeout: int = field(default_factory=lambda: int(os.environ.get("SCP_TIMEOUT", "60")))
    max_retries: int = field(default_factory=lambda: int(os.environ.get("SCP_MAX_RETRIES", "4")))
    # Heavy lifecycles (real VM / K8s cluster) only run when opted in.
    run_heavy: bool = field(default_factory=lambda: _bool("SCP_RUN_HEAVY", False))
    # Safety gate: mutating operations (POST/PUT/PATCH/DELETE) are skipped
    # unless this is explicitly enabled, so a smoke run never creates/deletes
    # real cloud resources by accident.
    allow_mutations: bool = field(default_factory=lambda: _bool("SCP_ALLOW_MUTATIONS", False))
    # Extra guard for destructive deletes even when mutations are allowed.
    allow_destructive: bool = field(default_factory=lambda: _bool("SCP_ALLOW_DESTRUCTIVE", False))

    def is_global(self, service: str | None) -> bool:
        return bool(service) and service in self.global_services

    def resolve_base_url(self, service: str | None = None) -> str:
        """Return the API base URL (scheme+host, no trailing slash) for a service."""
        if service and service in self.service_hosts:
            host = self.service_hosts[service]
            return (host if host.startswith("http") else f"https://{host}").rstrip("/")
        if self.is_global(service) and self.global_host_template:
            return self.global_host_template.format(
                service=service, env=self.env_code).rstrip("/")
        if service and self.region and self.host_template:
            return self.host_template.format(
                service=service, region=self.region, env=self.env_code).rstrip("/")
        if self.base_url:
            return self.base_url
        raise RuntimeError(
            f"Cannot resolve base URL for service={service!r}. Global services need "
            f"SCP_ENV; regional services need SCP_REGION. Or set SCP_SERVICE_HOSTS / "
            f"SCP_BASE_URL.")

    def require_credentials(self) -> None:
        missing = [n for n, v in (("SCP_ACCESS_KEY", self.access_key),
                                  ("SCP_SECRET_KEY", self.secret_key)) if not v]
        if not self.region and not self.base_url and not self.service_hosts:
            missing.append("SCP_REGION (or SCP_BASE_URL / SCP_SERVICE_HOSTS)")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in.")


settings = Settings()
