"""Runtime configuration for the SCP API regression suite.

All secrets and environment-specific values come from environment variables
(or a local .env file) — nothing is ever hardcoded. See .env.example.
"""
from __future__ import annotations

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


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Endpoint -----------------------------------------------------------
    # Base URL of the SCP Open API gateway (NOT the docs host). e.g.
    #   https://<service>.api.samsungsdscloud.com  or a unified gateway host.
    base_url: str = field(default_factory=lambda: os.environ.get("SCP_BASE_URL", "").rstrip("/"))

    # --- Credentials --------------------------------------------------------
    access_key: str = field(default_factory=lambda: os.environ.get("SCP_ACCESS_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("SCP_SECRET_KEY", ""))
    # Optional tenant/project scoping headers used by many SCP services.
    project_id: str = field(default_factory=lambda: os.environ.get("SCP_PROJECT_ID", ""))

    # --- Auth scheme (pluggable / configurable) -----------------------------
    # The exact HMAC header names + signing string must be confirmed against the
    # SCP User Guide / a real 200 response. They are configurable so the suite
    # can be aligned without code changes. Defaults follow the documented
    # "Access Key + HMAC-SHA256" pattern.
    auth_scheme: str = field(default_factory=lambda: os.environ.get("SCP_AUTH_SCHEME", "hmac"))
    hmac_access_header: str = field(
        default_factory=lambda: os.environ.get("SCP_HMAC_ACCESS_HEADER", "x-cmp-accesskey"))
    hmac_signature_header: str = field(
        default_factory=lambda: os.environ.get("SCP_HMAC_SIGNATURE_HEADER", "x-cmp-signature"))
    hmac_timestamp_header: str = field(
        default_factory=lambda: os.environ.get("SCP_HMAC_TIMESTAMP_HEADER", "x-cmp-timestamp"))
    project_header: str = field(
        default_factory=lambda: os.environ.get("SCP_PROJECT_HEADER", "x-cmp-project-id"))

    # --- Run behaviour ------------------------------------------------------
    timeout: int = field(default_factory=lambda: int(os.environ.get("SCP_TIMEOUT", "60")))
    max_retries: int = field(default_factory=lambda: int(os.environ.get("SCP_MAX_RETRIES", "4")))
    # Safety gate: mutating operations (POST/PUT/PATCH/DELETE) are skipped
    # unless this is explicitly enabled, so a smoke run never creates/deletes
    # real cloud resources by accident.
    allow_mutations: bool = field(default_factory=lambda: _bool("SCP_ALLOW_MUTATIONS", False))
    # Extra guard for destructive deletes even when mutations are allowed.
    allow_destructive: bool = field(default_factory=lambda: _bool("SCP_ALLOW_DESTRUCTIVE", False))

    def require_credentials(self) -> None:
        missing = [n for n, v in (("SCP_BASE_URL", self.base_url),
                                  ("SCP_ACCESS_KEY", self.access_key),
                                  ("SCP_SECRET_KEY", self.secret_key)) if not v]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in.")


settings = Settings()
