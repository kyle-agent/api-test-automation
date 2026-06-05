"""Shared kernel for the API test automation framework.

`core` is the single dependency surface for both axes (regression, conformance)
and the three supports (spec, dashboard, cleanup). During the restructure it is a
**facade**: the stable kernel still physically lives in ``framework/`` and is
re-exported here, while the genuinely new contracts (registry, results, budgets)
live in ``core`` directly. New code should import from ``core``; the physical
move of ``framework/*`` happens later behind this facade without touching callers.
"""
from __future__ import annotations

# --- stable kernel (re-exported from the existing framework package) ---------
from core.config import Settings, settings
from core.auth import build_signer
from core.http_client import ApiClient, Response, MutationBlocked
from core.catalog import Endpoint, endpoints, load_catalog

# --- new shared contracts ----------------------------------------------------
from core import budgets, registry, results

__all__ = [
    "Settings", "settings", "build_signer",
    "ApiClient", "Response", "MutationBlocked",
    "Endpoint", "endpoints", "load_catalog",
    "budgets", "registry", "results",
]
