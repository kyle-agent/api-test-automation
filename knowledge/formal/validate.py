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
  resources/*.yaml     (R1) resource-task model, docs/RESOURCE-MODEL-PLAN.md §1
                       — requires notations (plain ref · {ref,count} ·
                       {one_of:[...]} with optional bind:/use: · {ref, mode:
                       existing_or_create} · {credential: <name>}) · options
                       typed cidr|enum|ref|string · ref/one_of targets resolve
                       to defined node ids across ALL resources files
                       (credential names exempt) · body-template tokens point
                       at existing captures/options · quota kinds known to
                       core/budgets or dependencies.json (unknown = warning) ·
                       divergence from the cross-service.yaml requires graph
                       is a WARNING (the two co-exist during R1-R3) ·
                       _groups.yaml = {groups: {"nw-vpc": {label, category}}}
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


# --------------------------------------------------------------------------
# Resource-task model — resources/*.yaml (docs/RESOURCE-MODEL-PLAN.md §1, C1)
# --------------------------------------------------------------------------
RESOURCES_DIR = HERE / "resources"
OPTION_TYPES = {"cidr", "enum", "ref", "string"}
OPTION_KEYS = {"type", "required", "vary", "default", "pick", "of", "target",
               "values", "note"}
CIDR_PICKS = {"unique-block", "sub-block-of"}
REQUIRES_MODES = {"existing_or_create"}
TASK_KEYS = {"code", "service", "group", "requires", "create", "capture",
             "ready", "verify", "delete", "quota", "provenance", "adopt",
             "heavy", "no_api", "needs_cert_material", "notes", "note",
             "source", "_note"}
_TOKEN_RE_SRC = r"\{([A-Za-z0-9_][A-Za-z0-9_.\-]*)\}"


def _scenario_helpers():
    """Reuse the scenario validator's constants so both validators agree on
    builtins/methods/path normalisation."""
    from regression.scenarios.validate import BUILTINS, METHODS, _norm_path
    return BUILTINS, METHODS, _norm_path


def known_quota_kinds() -> set[str]:
    """Quota kinds the engine knows: core/budgets DEFAULT_LIMITS keys plus
    every kind named in dependencies.json quota_kinds."""
    kinds: set[str] = set()
    try:
        from core.budgets import DEFAULT_LIMITS
        kinds |= set(DEFAULT_LIMITS)
    except Exception:
        pass
    try:
        deps = json.loads(
            (ROOT / "regression" / "scenarios" / "dependencies.json")
            .read_text(encoding="utf-8"))
        for ks in (deps.get("quota_kinds") or {}).values():
            kinds.update(ks or [])
    except (OSError, ValueError):
        pass
    return kinds


def _resources_catalog_index():
    cat = json.loads((ROOT / "data" / "api_catalog.json").read_text(encoding="utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat else cat
    _, _, _norm_path = _scenario_helpers()
    return {((e.get("method") or "").upper(), _norm_path(e["http_path"]),
             e["service"]) for e in eps}


def _tokens_in(obj) -> set[str]:
    import re as _re
    out: set[str] = set()
    if isinstance(obj, str):
        out |= set(_re.findall(_TOKEN_RE_SRC, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out |= _tokens_in(k) | _tokens_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _tokens_in(v)
    return out


def _split_requires(where: str, entries) -> dict:
    """Normalise a node's requires for validation (accepts EVERY §1 notation).

    Returns {"and": [{"ref","count","mode"}], "groups": [{"bind","members":
    [{"ref","use"}]}], "credentials": [name]} and appends errors for
    malformed entries."""
    out = {"and": [], "groups": [], "credentials": []}
    if entries is None:
        return out
    if not isinstance(entries, list):
        err(f"{where}: requires must be a list")
        return out
    for entry in entries:
        if isinstance(entry, str):
            out["and"].append({"ref": entry, "count": 1, "mode": None})
        elif isinstance(entry, dict) and "one_of" in entry:
            extra = set(entry) - {"one_of", "bind", "note"}
            if extra:
                err(f"{where}: one_of entry has unknown key(s) {sorted(extra)}")
            members = []
            for m in entry.get("one_of") or []:
                if isinstance(m, str):
                    members.append({"ref": m, "use": None})
                elif isinstance(m, dict) and m.get("ref"):
                    mx = set(m) - {"ref", "use"}
                    if mx:
                        err(f"{where}: one_of member {m.get('ref')!r} has "
                            f"unknown key(s) {sorted(mx)}")
                    members.append({"ref": str(m["ref"]),
                                    "use": m.get("use")})
                else:
                    err(f"{where}: one_of member must be a node id or "
                        f"{{ref, use}}: {m!r}")
            if len(members) < 2:
                warn(f"{where}: one_of with fewer than 2 alternatives")
            out["groups"].append({"bind": entry.get("bind"),
                                  "members": members})
        elif isinstance(entry, dict) and "credential" in entry:
            extra = set(entry) - {"credential", "note"}
            if extra:
                err(f"{where}: credential entry has unknown key(s) "
                    f"{sorted(extra)}")
            name = entry.get("credential")
            if not isinstance(name, str) or not name:
                err(f"{where}: credential entry needs a non-empty name")
            else:
                out["credentials"].append(name)
        elif isinstance(entry, dict) and "ref" in entry:
            extra = set(entry) - {"ref", "count", "mode", "use", "note"}
            if extra:
                err(f"{where}: requires entry has unknown key(s) "
                    f"{sorted(extra)}")
            count = entry.get("count", 1)
            if not isinstance(count, int) or count < 1:
                err(f"{where}: count must be an integer >= 1 "
                    f"(got {count!r})")
                count = 1
            mode = entry.get("mode")
            if mode is not None and mode not in REQUIRES_MODES:
                err(f"{where}: mode must be one of {sorted(REQUIRES_MODES)} "
                    f"(got {mode!r})")
            out["and"].append({"ref": str(entry["ref"]), "count": count,
                               "mode": mode})
        else:
            err(f"{where}: unrecognised requires entry {entry!r} — expected "
                "a node id, {ref,count}, {ref,mode}, {one_of:[...]} or "
                "{credential: name}")
    return out


def _check_endpoint(where: str, endpoint, methods) -> None:
    if not isinstance(endpoint, str) or " " not in endpoint:
        err(f"{where}: endpoint must be 'METHOD /path' (got {endpoint!r})")
        return
    method, _, path = endpoint.partition(" ")
    if method.strip().upper() not in methods or not path.strip().startswith("/"):
        err(f"{where}: endpoint must be 'METHOD /path' (got {endpoint!r})")


def check_resources(services: set[str], l2_resources: dict) -> tuple[int, int]:
    """resources/*.yaml — the §1 resource-task model (skips when the layer
    does not exist yet). Returns (n_files, n_nodes)."""
    if not RESOURCES_DIR.is_dir():
        return 0, 0
    builtins_, methods, _norm_path = _scenario_helpers()
    catalog = _resources_catalog_index()
    quota_kinds = known_quota_kinds()

    files = [p for p in sorted(RESOURCES_DIR.glob("*.yaml"))
             if not p.name.startswith("_")]

    # ---- first pass: merge all nodes (refs resolve across ALL files, C1) ----
    nodes: dict[str, dict] = {}
    node_file: dict[str, str] = {}
    for path in files:
        rel = str(path.relative_to(HERE))
        data = load_yaml(path)
        res = data.get("resources")
        if not isinstance(res, dict) or not res:
            err(f"{rel}: must define a non-empty 'resources' mapping")
            continue
        for nid, task in res.items():
            nid = str(nid)
            if nid in nodes:
                err(f"{rel}: duplicate resource node '{nid}' (already "
                    f"defined in {node_file[nid]}) — composer.load_model() "
                    "rejects this")
                continue
            if not isinstance(task, dict):
                err(f"{rel}: resource '{nid}' must be a mapping")
                task = {}
            nodes[nid] = task
            node_file[nid] = rel

    # ---- _groups.yaml (C1: {groups: {"nw-vpc": {label, category}}}) --------
    groups: dict = {}
    gpath = RESOURCES_DIR / "_groups.yaml"
    if gpath.exists():
        gdata = load_yaml(gpath)
        graw = gdata.get("groups")
        if not isinstance(graw, dict):
            err("resources/_groups.yaml: must define a 'groups' mapping")
        else:
            for gid, g in graw.items():
                if not isinstance(g, dict) or not g.get("label"):
                    err(f"resources/_groups.yaml: group '{gid}' must be a "
                        "mapping with a 'label'")
                    continue
                if not g.get("category"):
                    warn(f"resources/_groups.yaml: group '{gid}' has no "
                         "'category'")
                groups[str(gid)] = g

    def _group_of(nid: str, task: dict) -> str:
        g = str(task.get("group") or "").strip()
        if g:
            return g
        parts = str(task.get("code") or "").split("-")
        return "-".join(parts[:2]) if len(parts) >= 3 else ""

    # ---- second pass: per-node schema + referential integrity ---------------
    for nid, task in nodes.items():
        rel = node_file[nid]
        where = f"{rel}: '{nid}'"

        for k in task:
            if k not in TASK_KEYS:
                warn(f"{where}: unknown key '{k}'")

        svc = task.get("service")
        if not svc or "/" not in str(svc):
            err(f"{where}: 'service' (category/service) is required")
        elif svc not in services:
            err(f"{where}: service '{svc}' not found in the catalog")
        if task.get("provenance") not in PROVENANCE:
            err(f"{where}: provenance must be one of {sorted(PROVENANCE)}")

        gid = _group_of(nid, task)
        if gid and groups and gid not in groups:
            warn(f"{where}: group '{gid}' is not defined in "
                 "resources/_groups.yaml")

        req = _split_requires(where, task.get("requires"))
        and_refs = {d["ref"] for d in req["and"]}
        counts = {d["ref"]: d["count"] for d in req["and"]}
        member_refs = {m["ref"] for g in req["groups"] for m in g["members"]}
        binds = {g["bind"] for g in req["groups"] if g.get("bind")}
        for d in req["and"]:
            if d["ref"] not in nodes:
                err(f"{where}: requires unknown resource '{d['ref']}'")
        for g in req["groups"]:
            for m in g["members"]:
                if m["ref"] not in nodes:
                    err(f"{where}: one_of references unknown resource "
                        f"'{m['ref']}'")
                elif m.get("use"):
                    caps = (nodes[m["ref"]] or {}).get("capture") or {}
                    if m["use"] not in caps:
                        err(f"{where}: one_of branch '{m['ref']}' has no "
                            f"capture '{m['use']}'")
        # credential names are deliberately exempt from resolution (they are
        # console-issued preconditions, not graph nodes — plan §1)

        create = task.get("create") or {}
        no_api = bool(task.get("no_api"))
        if not isinstance(create, dict):
            err(f"{where}: 'create' must be a mapping")
            create = {}
        if no_api:
            if create.get("endpoint"):
                err(f"{where}: no_api nodes must not declare a "
                    "create.endpoint")
            if not (task.get("notes") or task.get("note")):
                warn(f"{where}: no_api node should carry a note explaining "
                     "how the resource comes to exist")
        elif not create.get("endpoint"):
            err(f"{where}: create.endpoint is required (or set no_api: true "
                "with a note)")
        if create.get("endpoint"):
            _check_endpoint(f"{where} create", create["endpoint"], methods)
            method, _, path = create["endpoint"].partition(" ")
            short = str(svc or "").split("/")[-1]
            if (method.strip().upper(), _norm_path(path.strip()), short) \
                    not in catalog:
                warn(f"{where}: create endpoint '{create['endpoint']}' does "
                     f"not resolve to a catalog endpoint for service "
                     f"'{short}'")

        # options ------------------------------------------------------------
        ref_opt_targets: set[str] = set()
        opts = create.get("options") or {}
        if not isinstance(opts, dict):
            err(f"{where}: create.options must be a mapping")
            opts = {}
        for oname, ospec in opts.items():
            owhere = f"{where} option '{oname}'"
            if not isinstance(ospec, dict):
                err(f"{owhere}: must be a mapping")
                continue
            for k in ospec:
                if k not in OPTION_KEYS:
                    warn(f"{owhere}: unknown key '{k}'")
            otype = ospec.get("type")
            if otype not in OPTION_TYPES:
                err(f"{owhere}: type must be one of {sorted(OPTION_TYPES)}")
                continue
            if otype == "enum":
                if not isinstance(ospec.get("values"), list) \
                        or not ospec["values"]:
                    err(f"{owhere}: enum options need a non-empty 'values' "
                        "list")
                elif "default" in ospec \
                        and ospec["default"] not in ospec["values"]:
                    err(f"{owhere}: default {ospec['default']!r} not in "
                        f"values {ospec['values']}")
            if otype == "ref":
                target = ospec.get("target")
                if not target:
                    err(f"{owhere}: ref options need a 'target' node")
                elif target not in nodes:
                    err(f"{owhere}: target '{target}' is not a defined node")
                else:
                    ref_opt_targets.add(target)
            if otype == "cidr" and ospec.get("pick"):
                pick = ospec["pick"]
                if pick not in CIDR_PICKS:
                    err(f"{owhere}: unknown cidr pick scheme '{pick}' "
                        f"(known: {sorted(CIDR_PICKS)})")
                elif pick == "sub-block-of":
                    of = str(ospec.get("of") or "")
                    parent, _, popt = of.partition(".")
                    if not parent:
                        err(f"{owhere}: pick sub-block-of needs "
                            "of: <node>.<option>")
                    elif parent not in (and_refs | member_refs
                                        | ref_opt_targets):
                        err(f"{owhere}: of-parent '{parent}' is not among "
                            f"this node's prerequisites")
                    elif parent in nodes:
                        popts = ((nodes[parent].get("create") or {})
                                 .get("options")) or {}
                        if (popt or "cidr") not in popts:
                            err(f"{owhere}: parent '{parent}' has no option "
                                f"'{popt or 'cidr'}'")

        # capture / ready / verify / delete -----------------------------------
        # a capture value is a JSONPath string OR a filter-object selector
        # (engine._capture): {list: $.path, get: field, where_prefix?,
        # where_not_prefix?} — the validated lookup-node pattern.
        def _cap_ok(v) -> bool:
            if isinstance(v, str):
                return True
            return (isinstance(v, dict) and isinstance(v.get("list"), str)
                    and isinstance(v.get("get"), str)
                    and set(v) <= {"list", "get", "where_prefix",
                                   "where_not_prefix"})

        caps = task.get("capture") or {}
        if not isinstance(caps, dict) or not all(
                isinstance(k, str) and _cap_ok(v) for k, v in caps.items()):
            err(f"{where}: capture must map var -> $.jsonpath or a filter "
                "object {list, get, where_prefix?, where_not_prefix?}")
            caps = {}

        ready = task.get("ready")
        delete = task.get("delete") or {}
        if not isinstance(delete, dict):
            err(f"{where}: 'delete' must be a mapping")
            delete = {}
        if delete.get("endpoint"):
            _check_endpoint(f"{where} delete", delete["endpoint"], methods)
        elif not no_api and create.get("endpoint"):
            warn(f"{where}: create without delete.endpoint — composed "
                 "lifecycles will have no teardown for it")
        if ready is not None:
            if not isinstance(ready, dict) or not ready.get("field") \
                    or ready.get("until") in (None, "", []):
                err(f"{where}: ready needs 'field' and 'until'")
            else:
                if ready.get("endpoint"):
                    _check_endpoint(f"{where} ready", ready["endpoint"],
                                    methods)
                elif not delete.get("endpoint"):
                    err(f"{where}: ready without endpoint and without a "
                        "delete.endpoint to derive a read path from")
                for k in ("timeout", "interval"):
                    if k in ready and not isinstance(ready[k], int):
                        err(f"{where}: ready.{k} must be an integer")

        verify = task.get("verify")
        if verify is not None and not isinstance(verify, list):
            err(f"{where}: verify must be a list of steps")
            verify = None
        for v in verify or []:
            if not isinstance(v, dict) or not v.get("endpoint"):
                err(f"{where}: each verify step needs an 'endpoint'")
                continue
            _check_endpoint(f"{where} verify", v["endpoint"], methods)
            es = v.get("expect_status")
            if es is not None and not (isinstance(es, list) and all(
                    isinstance(x, int) for x in es)):
                err(f"{where}: verify expect_status must be a list of ints")

        # template tokens (mirror composer._Ctx token rules; delete/ready
        # paths use the node's own dot-less capture vars, and child resources
        # may use dotted prerequisite tokens there too) ------------------------
        templated = [create.get("body"), delete.get("endpoint"),
                     (ready or {}).get("endpoint") if isinstance(ready, dict)
                     else None]
        for v in verify or []:
            templated += [v.get("endpoint"), v.get("json")]
        for token in sorted(_tokens_in(templated)):
            parts = token.split(".")
            if len(parts) == 1:
                if token not in builtins_ and token not in caps:
                    err(f"{where}: template token '{{{token}}}' is neither "
                        "an engine builtin nor an own capture var — use "
                        "{<node>.<capture>} for prerequisites")
            elif parts[0] == "opt":
                if len(parts) != 2 or parts[1] not in opts:
                    err(f"{where}: template references unknown option "
                        f"'{{{token}}}'")
            elif parts[0] == "self":
                if len(parts) != 2 or parts[1] not in caps:
                    err(f"{where}: '{{{token}}}' names no own capture var")
            elif parts[0] == "dep":
                if len(parts) != 2 or parts[1] not in binds:
                    err(f"{where}: '{{{token}}}' names no one_of bind "
                        f"(declared binds: {sorted(binds)})")
            elif parts[0] in nodes:
                dep = parts[0]
                if len(parts) == 3 and parts[1].isdigit():
                    idx, cap_key = int(parts[1]), parts[2]
                    if idx > counts.get(dep, 1):
                        err(f"{where}: '{{{token}}}' wants instance {idx} "
                            f"of '{dep}' but requires declares count "
                            f"{counts.get(dep, 1)}")
                elif len(parts) == 2:
                    cap_key = parts[1]
                else:
                    err(f"{where}: malformed template token '{{{token}}}'")
                    continue
                if dep not in (and_refs | ref_opt_targets):
                    if dep in member_refs:
                        warn(f"{where}: '{{{token}}}' references one_of "
                             f"branch '{dep}' directly — only valid when "
                             "that branch is chosen (prefer {dep.<bind>})")
                    else:
                        err(f"{where}: '{{{token}}}' references '{dep}' "
                            "which is not among this node's prerequisites")
                dep_caps = (nodes.get(dep) or {}).get("capture") or {}
                if isinstance(dep_caps, dict) and cap_key not in dep_caps:
                    err(f"{where}: '{dep}' has no capture '{cap_key}'")
            else:
                err(f"{where}: unresolvable template token '{{{token}}}'")

        # quota kinds (unknown = warning, per plan: budgets grows with model)
        q = task.get("quota")
        if q and q not in quota_kinds:
            warn(f"{where}: quota kind '{q}' unknown to core/budgets "
                 f"DEFAULT_LIMITS and dependencies.json quota_kinds")

        # divergence vs cross-service.yaml (L2) — WARNING, the two co-exist
        if nid in l2_resources:
            l2_req = set((l2_resources[nid] or {}).get("requires") or [])
            if and_refs != l2_req:
                only_model = sorted(and_refs - l2_req)
                only_l2 = sorted(l2_req - and_refs)
                detail = []
                if only_model:
                    detail.append(f"model adds {only_model}")
                if only_l2:
                    detail.append(f"cross-service has {only_l2}")
                warn(f"{where}: requires diverges from cross-service.yaml "
                     f"({'; '.join(detail)}) — reconcile during R3")

    return len(files), len(nodes)


def main() -> int:
    ids = lifecycle_ids()
    services = catalog_services()

    n_services = check_services(services)
    resources, n_cross = check_cross_service(services)
    n_rules, n_orders = check_flows(resources, ids)
    n_combos = check_combos(ids, services)
    n_waivers = check_waivers()
    n_res_files, n_res_nodes = check_resources(services, resources)

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(
        f"L1 {n_services} service file(s) · "
        f"L2 {len(resources)} resource(s) + {n_cross} cross-constraint(s) · "
        f"L3 {n_rules} flow-rule(s) + {n_orders} call-order(s) · "
        f"{n_combos} combo(s) · {n_waivers} waiver(s) · "
        f"R1 {n_res_nodes} resource task(s) in {n_res_files} file(s) checked · "
        f"{len(errors)} error(s) · {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
