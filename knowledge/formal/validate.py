#!/usr/bin/env python3
"""Validate knowledge/formal/ — the 3-layer formal domain knowledge model.

Offline (no credentials, no network). Run after every human edit:

    python knowledge/formal/validate.py

Layers / checks:
  services/*.yaml      (L1) filename matches `service:` · service exists in the
                       catalog · constraints/quirks carry id + provenance
  cross-service.yaml   (L2) requires-references exist · graph is acyclic ·
                       quota limits agree with dependencies.json vpc_schedule ·
                       cross_constraints reference real services
  flows.yaml           (L3) call-order resources exist in the L2 graph ·
                       encoded_in lifecycle ids exist · flow_rules ids unique
  combo-scenarios.yaml status enum · encoded combos point at a real lifecycle ·
                       draft/idea combos carry a review block with a valid
                       decision · services exist in the catalog
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    print("ERROR pyyaml is required: pip install -r requirements.txt")
    sys.exit(2)

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def load_yaml(path: Path) -> dict:
    rel = path.relative_to(HERE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        err(f"{rel}: YAML parse error: {exc}")
        return {}
    if not isinstance(data, dict):
        err(f"{rel}: top level must be a mapping")
        return {}
    if data.get("version") != 1:
        warn(f"{rel}: expected version: 1")
    return data


def lifecycle_ids() -> set[str]:
    from regression.scenarios.loader import load_lifecycles

    return {l["id"] for l in load_lifecycles()}


def catalog_services() -> set[str]:
    cat = json.loads((ROOT / "data" / "api_catalog.json").read_text(encoding="utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat else cat
    return {f"{e['category']}/{e['service']}" for e in eps}


PROVENANCE = {"VALIDATED", "docs"}


def check_tagged_list(rel: str, section: str, items: list, text_key: str) -> None:
    """Items must each carry id + provenance + the text field; ids unique."""
    seen: set[str] = set()
    for item in items or []:
        iid = item.get("id")
        if not iid:
            err(f"{rel}: {section} entry missing 'id'")
            continue
        if iid in seen:
            err(f"{rel}: duplicate {section} id '{iid}'")
        seen.add(iid)
        if item.get("provenance") not in PROVENANCE:
            err(f"{rel}: {section} '{iid}' provenance must be one of {sorted(PROVENANCE)}")
        if not item.get(text_key):
            err(f"{rel}: {section} '{iid}' missing '{text_key}'")


# --------------------------------------------------------------------------
# Layer 1 — services/*.yaml
# --------------------------------------------------------------------------
def check_services(services: set[str]) -> int:
    files = sorted((HERE / "services").glob("*.yaml"))
    for path in files:
        rel = str(path.relative_to(HERE))
        data = load_yaml(path)
        svc = data.get("service")
        if not svc:
            err(f"{rel}: missing 'service'")
            continue
        expected = path.stem.replace("__", "/")
        if svc != expected:
            err(f"{rel}: service '{svc}' does not match filename (expected '{expected}')")
        if svc not in services:
            err(f"{rel}: service '{svc}' not found in the catalog")
        check_tagged_list(rel, "constraints", data.get("constraints"), "rule")
        check_tagged_list(rel, "quirks", data.get("quirks"), "note")
        for res, st in (data.get("states") or {}).items():
            if not isinstance(st, dict) or "field" not in st or not st.get("ready"):
                err(f"{rel}: states.{res} must be a mapping with 'field' and non-empty 'ready'")
    return len(files)


# --------------------------------------------------------------------------
# Layer 2 — cross-service.yaml
# --------------------------------------------------------------------------
def check_cross_service(services: set[str]) -> tuple[dict, int]:
    rel = "cross-service.yaml"
    graph = load_yaml(HERE / rel)
    resources = graph.get("resources") or {}
    quotas = graph.get("quotas") or {}

    constraints = graph.get("cross_constraints") or []
    check_tagged_list(rel, "cross_constraints", constraints, "rule")
    for c in constraints:
        svcs = c.get("services") or []
        if len(svcs) < 2:
            warn(f"{rel}: cross_constraints '{c.get('id')}' lists <2 services — is it per-service (layer 1)?")
        for svc in svcs:
            if svc not in services:
                err(f"{rel}: cross_constraints '{c.get('id')}' references unknown service '{svc}'")

    for key, res in resources.items():
        if not isinstance(res, dict):
            err(f"{rel}: resource '{key}' must be a mapping")
            continue
        if not res.get("service"):
            err(f"{rel}: resource '{key}' missing 'service'")
        elif res["service"] not in services:
            err(f"{rel}: resource '{key}' service '{res['service']}' not found in the catalog")
        if res.get("provenance") not in PROVENANCE:
            err(f"{rel}: resource '{key}' provenance must be one of {sorted(PROVENANCE)}")
        for dep in res.get("requires") or []:
            if dep not in resources:
                err(f"{rel}: '{key}' requires unknown resource '{dep}'")
        q = res.get("quota")
        if q and q not in quotas:
            err(f"{rel}: '{key}' references undeclared quota '{q}'")

    # acyclicity (DFS)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in resources}

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for dep in resources.get(node, {}).get("requires") or []:
            if dep not in resources:
                continue
            if color[dep] == GRAY:
                err(f"{rel}: dependency cycle: {' -> '.join(path + [node, dep])}")
            elif color[dep] == WHITE:
                dfs(dep, path + [node])
        color[node] = BLACK

    for node in resources:
        if color[node] == WHITE:
            dfs(node, [])

    # quota limits must agree with the engine's scheduling data
    deps = json.loads((ROOT / "regression" / "scenarios" / "dependencies.json").read_text(encoding="utf-8"))
    sched = deps.get("vpc_schedule", {})
    engine_limits = {"vpc": sched.get("vpc_limit"), "private-dns": sched.get("private_dns_limit")}
    for qkey, q in quotas.items():
        engine = engine_limits.get(qkey)
        if engine is not None and q.get("limit") != engine:
            err(
                f"{rel}: quota '{qkey}' limit={q.get('limit')} disagrees with "
                f"dependencies.json vpc_schedule ({engine})"
            )
    return resources, len(constraints)


# --------------------------------------------------------------------------
# Layer 3 — flows.yaml
# --------------------------------------------------------------------------
def check_flows(resources: dict, ids: set[str]) -> tuple[int, int]:
    rel = "flows.yaml"
    flows = load_yaml(HERE / rel)
    rules = flows.get("flow_rules") or []
    check_tagged_list(rel, "flow_rules", rules, "rule")

    orders = flows.get("call_orders") or {}
    for key, order in orders.items():
        if key not in resources:
            err(f"{rel}: call_orders '{key}' is not a resource in cross-service.yaml")
        if order.get("provenance") not in PROVENANCE:
            err(f"{rel}: call_orders '{key}' provenance must be one of {sorted(PROVENANCE)}")
        for phase in ("create", "delete"):
            spec = order.get(phase)
            if not isinstance(spec, dict) or "api" not in spec:
                err(f"{rel}: call_orders '{key}' {phase} must be a mapping with an 'api'")
        for lid in order.get("encoded_in") or []:
            if lid not in ids:
                err(f"{rel}: call_orders '{key}' encoded_in '{lid}' is not a known lifecycle id")
    return len(rules), len(orders)


# --------------------------------------------------------------------------
# Combos + scenario-based review — combo-scenarios.yaml
# --------------------------------------------------------------------------
COMBO_STATUS = {"encoded", "draft", "idea"}
REVIEW_DECISION = {"pending", "approved", "rejected"}


def check_combos(ids: set[str], services: set[str]) -> int:
    rel = "combo-scenarios.yaml"
    combos = (load_yaml(HERE / rel)).get("combos") or []
    seen: set[str] = set()
    for combo in combos:
        cid = combo.get("id", "<missing id>")
        if cid in seen:
            err(f"{rel}: duplicate combo id '{cid}'")
        seen.add(cid)
        status = combo.get("status")
        if status not in COMBO_STATUS:
            err(f"{rel}: '{cid}' status must be one of {sorted(COMBO_STATUS)}")
        if status == "encoded":
            if cid not in ids:
                err(f"{rel}: '{cid}' is encoded but no lifecycle has that id")
            if not combo.get("encoded_in"):
                err(f"{rel}: '{cid}' is encoded but missing 'encoded_in'")
        else:
            review = combo.get("review")
            if not isinstance(review, dict):
                err(f"{rel}: '{cid}' ({status}) must carry a 'review' block (scenario-based review)")
            else:
                if review.get("decision") not in REVIEW_DECISION:
                    err(f"{rel}: '{cid}' review.decision must be one of {sorted(REVIEW_DECISION)}")
                if not review.get("checks"):
                    warn(f"{rel}: '{cid}' review.checks is empty — what should the reviewer verify?")
        if not combo.get("flow"):
            err(f"{rel}: '{cid}' must declare a 'flow'")
        if not combo.get("value"):
            warn(f"{rel}: '{cid}' has no 'value' (why is this combo worth testing?)")
        for svc in combo.get("services") or []:
            if svc not in services:
                err(f"{rel}: '{cid}' references unknown service '{svc}'")
    return len(combos)


WAIVER_CLASSES = {"blast-radius", "entitlement", "unsatisfiable-flow", "billing-prohibitive"}


def check_waivers() -> int:
    """data/baselines/coverage_waivers.json — C3 waivers (docs/COVERAGE-CRITERIA.md)."""
    path = ROOT / "data" / "baselines" / "coverage_waivers.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        err(f"coverage_waivers.json: parse error: {exc}")
        return 0
    cat = json.loads((ROOT / "data" / "api_catalog.json").read_text(encoding="utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat else cat
    cat_keys = {e["key"] for e in eps}
    seen: set[str] = set()
    waivers = data.get("waivers") or []
    for w in waivers:
        key = w.get("key")
        if not key:
            err("coverage_waivers.json: waiver missing 'key'")
            continue
        if key in seen:
            err(f"coverage_waivers.json: duplicate waiver '{key}'")
        seen.add(key)
        if key not in cat_keys:
            err(f"coverage_waivers.json: '{key}' is not a catalog endpoint key")
        if w.get("class") not in WAIVER_CLASSES:
            err(f"coverage_waivers.json: '{key}' class must be one of {sorted(WAIVER_CLASSES)}")
        if not w.get("reason"):
            err(f"coverage_waivers.json: '{key}' missing 'reason'")
    return len(waivers)


def main() -> int:
    ids = lifecycle_ids()
    services = catalog_services()

    n_services = check_services(services)
    resources, n_cross = check_cross_service(services)
    n_rules, n_orders = check_flows(resources, ids)
    n_combos = check_combos(ids, services)
    n_waivers = check_waivers()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(
        f"L1 {n_services} service file(s) · "
        f"L2 {len(resources)} resource(s) + {n_cross} cross-constraint(s) · "
        f"L3 {n_rules} flow-rule(s) + {n_orders} call-order(s) · "
        f"{n_combos} combo(s) · {n_waivers} waiver(s) checked · "
        f"{len(errors)} error(s) · {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
