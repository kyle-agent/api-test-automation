"""HEAVY-PREMISE-CONTRACT §4 — soft-observation 3-way classifier.

A "soft" observation is an endpoint call whose non-2xx response was *tolerated*
(``core.results`` category ``soft``). For the run report those must not render
as one undifferentiated orange: the contract splits them into three chips —

* ``"policy"``    — the endpoint is reachability-waived (``coverage_waivers.json``
  entry with ``class == "reachability"``): reached IS the definition-of-done,
  the 4xx is expected forever. (파랑 · "만점=도달")
* ``"duplicate"`` — the *same endpoint* (method + normalized path, or exact
  catalog key) already has real-2xx evidence — either earlier in the same run
  (``run_endpoint_2xx``) or accumulated in ``verified_endpoints.json``. The
  soft entry is redundant noise. (회색 · 접힘)
* ``"gap"``       — neither: no verify-role lifecycle can 2xx this endpoint yet;
  a recipe is still owed. (주황 · 레시피 숙제)

Priority (contract-fixed): **policy > duplicate > gap**.

Key-shape handling (knowledge/validated-facts.md "observation ``endpoint_key``
has TWO shapes"): observations carry ``endpoint_key`` either as a catalog key
``category/service/op`` (slash — smoke/CRUD sweep) or as ``<lifecycle>:<step>``
(colon — the lifecycle engine). The shapes are unambiguous (lifecycle ids carry
no ``/``; sweep keys carry no ``:``). Colon-shape keys are resolved to catalog
keys by ``method`` + normalized path against ``data/api_catalog.json``;
normalization reuses :func:`tools.derive_verified.norm_path` (the contract
forbids inventing a new normalizer).

``classify`` is a pure function — callers pass every input in; the thin
``load_*`` helpers at the bottom read the real repo files for convenience but
are never called implicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.derive_verified import norm_path

# The three classes (contract-fixed strings).
POLICY = "policy"
DUPLICATE = "duplicate"
GAP = "gap"
CLASSES = (DUPLICATE, GAP, POLICY)

_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = _ROOT / "data" / "api_catalog.json"
WAIVERS_PATH = _ROOT / "data" / "baselines" / "coverage_waivers.json"
VERIFIED_PATH = _ROOT / "data" / "baselines" / "verified_endpoints.json"


# ---------------------------------------------------------------- primitives

def _is_2xx(status) -> bool:
    try:
        return 200 <= int(status) <= 299
    except (TypeError, ValueError):
        return False


def _is_catalog_shape(key: str) -> bool:
    """Catalog-key shape ``category/service/op`` — has ``/`` and no ``:``.
    Everything else is treated as the ``<lifecycle>:<step>`` engine shape."""
    return "/" in key and ":" not in key


def endpoint_token(method: str | None, path: str | None) -> str:
    """Canonical ``"METHOD norm_path"`` identity token for an endpoint, using
    :func:`tools.derive_verified.norm_path` (query stripped, ``{param}``
    segments folded to ``*``). Empty string when either part is missing —
    an empty token never matches anything."""
    m = (method or "").strip().upper()
    np = norm_path(path or "")
    if not m or not np:
        return ""
    return f"{m} {np}"


def build_run_2xx(observations: list[dict]) -> set[str]:
    """Convenience builder for ``classify``'s ``run_endpoint_2xx`` parameter:
    fold a run's observations into the set of endpoint identities that recorded
    a **real 2xx** in that run (any category — a soft 2xx is evidence too,
    same rule as ``tools.derive_verified``).

    Each 2xx observation contributes its ``"METHOD norm_path"`` token and its
    raw ``endpoint_key``, so membership checks work across both key shapes.
    """
    out: set[str] = set()
    for o in observations:
        if not _is_2xx(o.get("status")):
            continue
        tok = endpoint_token(o.get("method"), o.get("path"))
        if tok:
            out.add(tok)
        key = o.get("endpoint_key") or ""
        if key:
            out.add(key)
    return out


# ------------------------------------------------------------------- indexes

def _catalog_token_index(catalog: list[dict] | None) -> dict[str, set[str]]:
    """``"METHOD norm_path"`` -> set of catalog keys (``data/api_catalog.json``
    items: ``key`` / ``method`` / ``http_path``). A token can map to several
    keys when ops share method+path."""
    index: dict[str, set[str]] = {}
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or ""
        tok = endpoint_token(entry.get("method"), entry.get("http_path"))
        if key and tok:
            index.setdefault(tok, set()).add(key)
    return index


def _reachability_waived(waivers) -> set[str]:
    """Catalog keys waived with ``class == "reachability"`` — the ONLY waiver
    class that classifies as policy (billing-prohibitive / entitlement / … do
    not). Accepts the contract's ``list[dict]`` or, tolerantly, the whole
    ``coverage_waivers.json`` dict (``{"waivers": [...]}``)."""
    if isinstance(waivers, dict):
        waivers = waivers.get("waivers", [])
    out: set[str] = set()
    for w in waivers or []:
        if not isinstance(w, dict):
            continue
        if (w.get("class") or "").strip().lower() != "reachability":
            continue
        key = w.get("key") or ""
        if key:
            out.add(key)
    return out


def _verified_index(verified: dict | None) -> tuple[set[str], set[str]]:
    """(tokens, keys) evidence sets from ``verified_endpoints.json`` — every
    entry in that file IS ≥1 real 2xx by the file's contract. Tokens are
    ``"METHOD norm_path"`` (entry's ``norm_path``, else re-derived from
    ``path``) so evidence matches across BOTH key shapes; keys support exact
    endpoint_key / catalog-key equality."""
    tokens: set[str] = set()
    keys: set[str] = set()
    for key, entry in (verified or {}).items():
        if key:
            keys.add(key)
        if not isinstance(entry, dict):
            continue
        method = (entry.get("method") or "").strip().upper()
        np = (entry.get("norm_path") or "").strip("/") or norm_path(entry.get("path") or "")
        if method and np:
            tokens.add(f"{method} {np}")
    return tokens, keys


# ------------------------------------------------------------------ classify

def classify(
    observations: list[dict],
    *,
    verified: dict,
    waivers: list[dict],
    run_endpoint_2xx: set[str],
    catalog: list[dict] | None = None,
) -> dict[int, str]:
    """Classify every ``category == "soft"`` observation as
    ``"policy" | "duplicate" | "gap"`` (priority policy > duplicate > gap).

    Returns ``{observation index -> class}``; non-soft observations (ok/fail)
    get NO entry. Pure: no I/O — pass data in.

    :param observations: observation dicts (``core.results`` schema —
        ``endpoint_key`` / ``method`` / ``path`` / ``status`` / ``category``).
    :param verified: ``verified_endpoints.json`` dict (accumulated 2xx
        evidence; keys in both shapes).
    :param waivers: ``coverage_waivers.json`` ``"waivers"`` list (the whole
        file dict is tolerated).
    :param run_endpoint_2xx: endpoint identities that got a real 2xx in the
        same run — ``"METHOD norm_path"`` tokens and/or raw endpoint_keys
        and/or catalog keys (see :func:`build_run_2xx`).
    :param catalog: ``data/api_catalog.json`` list; required to resolve
        colon-shape (``lifecycle:step``) keys to catalog keys. Without it,
        colon-shape observations can still match duplicates by method+path
        but can never classify as policy.
    """
    token_to_keys = _catalog_token_index(catalog)
    waived = _reachability_waived(waivers)
    verified_tokens, verified_keys = _verified_index(verified)
    run_2xx = run_endpoint_2xx or set()

    out: dict[int, str] = {}
    for idx, obs in enumerate(observations):
        if not isinstance(obs, dict):
            continue
        if (obs.get("category") or "").strip().lower() != "soft":
            continue  # ok/fail entries are not soft — no key for them

        key = obs.get("endpoint_key") or ""
        token = endpoint_token(obs.get("method"), obs.get("path"))

        # Resolve the observation's catalog key(s): a slash-shape key IS one;
        # a colon-shape key maps via method + normalized path.
        if key and _is_catalog_shape(key):
            catalog_keys = {key}
        else:
            catalog_keys = set(token_to_keys.get(token, ())) if token else set()

        # 1) policy — reachability-waived catalog key (highest priority).
        if catalog_keys & waived:
            out[idx] = POLICY
            continue

        # 2) duplicate — 2xx evidence for the same endpoint, in this run or in
        #    the accumulated verified store (both key shapes via the token).
        identities = {key, token, *catalog_keys}
        identities.discard("")
        if (
            identities & run_2xx
            or (token and token in verified_tokens)
            or identities & verified_keys
        ):
            out[idx] = DUPLICATE
            continue

        # 3) gap — no waiver, no 2xx evidence anywhere: recipe still owed.
        out[idx] = GAP
    return out


def summarize(class_map: dict) -> dict[str, int]:
    """``{"duplicate": n, "gap": n, "policy": n}`` — all three keys always
    present (0 default) so report chips can render unconditionally."""
    counts = {DUPLICATE: 0, GAP: 0, POLICY: 0}
    for cls in class_map.values():
        if cls in counts:
            counts[cls] += 1
    return counts


# ------------------------------------------------- thin loaders (I/O helpers)
# classify() never calls these — callers wire them in explicitly.

def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def load_catalog(path: Path | str = CATALOG_PATH) -> list[dict]:
    """``data/api_catalog.json`` — list of endpoint entries ([] if unreadable)."""
    data = _load_json(Path(path), [])
    return data if isinstance(data, list) else []


def load_waivers(path: Path | str = WAIVERS_PATH) -> list[dict]:
    """``coverage_waivers.json`` -> its ``"waivers"`` list ([] if unreadable)."""
    data = _load_json(Path(path), {})
    waivers = data.get("waivers", []) if isinstance(data, dict) else []
    return waivers if isinstance(waivers, list) else []


def load_verified(path: Path | str = VERIFIED_PATH) -> dict:
    """``verified_endpoints.json`` — accumulated 2xx evidence ({} if unreadable)."""
    data = _load_json(Path(path), {})
    return data if isinstance(data, dict) else {}
