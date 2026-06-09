#!/usr/bin/env python3
"""Validate knowledge/formal/*.yaml — structure + consistency with the engine data.

Offline (no credentials, no network). Run after every human edit:

    python knowledge/formal/validate.py

Checks:
  service-graph.yaml   requires-references exist · graph is acyclic · quota keys
                       declared · limits agree with dependencies.json vpc_schedule
  call-orders.yaml     resource keys exist in the graph · encoded_in lifecycle
                       ids exist in the merged scenario data
  combo-scenarios.yaml status enum · encoded combos point at a real lifecycle id
                       · services exist in the catalog's category/service set
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


def load_yaml(name: str) -> dict:
    path = HERE / name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        err(f"{name}: YAML parse error: {exc}")
        return {}
    if not isinstance(data, dict):
        err(f"{name}: top level must be a mapping")
        return {}
    if data.get("version") != 1:
        warn(f"{name}: expected version: 1")
    return data


def lifecycle_ids() -> set[str]:
    from regression.scenarios.loader import load_lifecycles

    return {l["id"] for l in load_lifecycles()}


def catalog_services() -> set[str]:
    cat = json.loads((ROOT / "data" / "api_catalog.json").read_text(encoding="utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat else cat
    return {f"{e['category']}/{e['service']}" for e in eps}


PROVENANCE = {"VALIDATED", "docs"}


def check_service_graph(graph: dict) -> dict:
    resources = graph.get("resources") or {}
    quotas = graph.get("quotas") or {}
    for key, res in resources.items():
        if not isinstance(res, dict):
            err(f"service-graph: resource '{key}' must be a mapping")
            continue
        if "service" not in res:
            err(f"service-graph: resource '{key}' missing 'service'")
        if res.get("provenance") not in PROVENANCE:
            err(f"service-graph: resource '{key}' provenance must be one of {sorted(PROVENANCE)}")
        for dep in res.get("requires") or []:
            if dep not in resources:
                err(f"service-graph: '{key}' requires unknown resource '{dep}'")
        q = res.get("quota")
        if q and q not in quotas:
            err(f"service-graph: '{key}' references undeclared quota '{q}'")

    # acyclicity (DFS)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in resources}

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for dep in resources.get(node, {}).get("requires") or []:
            if dep not in resources:
                continue
            if color[dep] == GRAY:
                err(f"service-graph: dependency cycle: {' -> '.join(path + [node, dep])}")
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
                f"service-graph: quota '{qkey}' limit={q.get('limit')} disagrees with "
                f"dependencies.json vpc_schedule ({engine})"
            )
    return resources


def check_call_orders(orders: dict, resources: dict, ids: set[str]) -> None:
    for key, order in (orders.get("call_orders") or {}).items():
        if key not in resources:
            err(f"call-orders: '{key}' is not a resource in service-graph.yaml")
        if order.get("provenance") not in PROVENANCE:
            err(f"call-orders: '{key}' provenance must be one of {sorted(PROVENANCE)}")
        for phase in ("create", "delete"):
            spec = order.get(phase)
            if not isinstance(spec, dict) or "api" not in spec:
                err(f"call-orders: '{key}' {phase} must be a mapping with an 'api'")
        for lid in order.get("encoded_in") or []:
            if lid not in ids:
                err(f"call-orders: '{key}' encoded_in '{lid}' is not a known lifecycle id")


COMBO_STATUS = {"encoded", "draft", "idea"}


def check_combos(combos: dict, ids: set[str], services: set[str]) -> None:
    seen: set[str] = set()
    for combo in combos.get("combos") or []:
        cid = combo.get("id", "<missing id>")
        if cid in seen:
            err(f"combo-scenarios: duplicate combo id '{cid}'")
        seen.add(cid)
        status = combo.get("status")
        if status not in COMBO_STATUS:
            err(f"combo-scenarios: '{cid}' status must be one of {sorted(COMBO_STATUS)}")
        if status == "encoded":
            if cid not in ids:
                err(f"combo-scenarios: '{cid}' is encoded but no lifecycle has that id")
            if not combo.get("encoded_in"):
                err(f"combo-scenarios: '{cid}' is encoded but missing 'encoded_in'")
        if not combo.get("flow"):
            err(f"combo-scenarios: '{cid}' must declare a 'flow'")
        if not combo.get("value"):
            warn(f"combo-scenarios: '{cid}' has no 'value' (why is this combo worth testing?)")
        for svc in combo.get("services") or []:
            if svc not in services:
                err(f"combo-scenarios: '{cid}' references unknown service '{svc}'")


def main() -> int:
    graph = load_yaml("service-graph.yaml")
    orders = load_yaml("call-orders.yaml")
    combos = load_yaml("combo-scenarios.yaml")
    ids = lifecycle_ids()
    services = catalog_services()

    resources = check_service_graph(graph)
    check_call_orders(orders, resources, ids)
    check_combos(combos, ids, services)

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(
        f"{len(resources)} resource(s) · "
        f"{len((orders.get('call_orders') or {}))} call-order(s) · "
        f"{len((combos.get('combos') or []))} combo(s) checked · "
        f"{len(errors)} error(s) · {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
