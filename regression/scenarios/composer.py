"""Composer (R2a) — resource-task-model -> engine lifecycle JSON.

Implements contract C2 of docs/RESOURCE-MODEL-PLAN.md §5:

    load_model(dir="knowledge/formal/resources") -> dict      # {node_id: task}
    plan(targets, choices=None, options=None, model=None) -> Plan
    compose(targets, choices=None, options=None, model=None,
            lifecycle_id=None) -> dict                        # lifecycle JSON

The composer is a *compiler*: it never talks to the network.  Output is a
lifecycle dict in exactly the shape the existing engine consumes
(regression/scenarios/loader.py + validate.py), and every composed lifecycle
is run through the scenario validator's error-level invariants before it is
returned (unique step names, capture-before-use, destructive teardown
present, well-formed poll/expect_status/capture/cleanup).

Model schema (§1 of the plan) — per node ("task"):

    <node_id>:
      code: "nw-vpc-vpc"           # optional, human classification (<cat>-<group>-<resource>)
      service: networking/vpc      # category/service (step service = last segment)
      heavy: true                  # optional; used for default-branch tie-break
      adopt: vpc                   # optional; stamped on create/delete steps
      requires:                    # list of:
        - vpc                      #   plain ref
        - {ref: vpc, count: 2}     #   multiplicity
        - {one_of: [load-balancer, {ref: server, use: server_ip}],
           bind: backend}          #   OR-dependency (see "bind/use" below)
      create:
        endpoint: "POST /v1/vpcs"
        body: {...}                # template; placeholders below
        options:
          cidr:  {type: cidr, required: true, pick: unique-block}
          cidr2: {type: cidr, pick: sub-block-of, of: vpc.cidr}
          target: {type: enum, values: [dns, scr], required: true, vary: true}
          security_group: {type: ref, target: security-group, required: false}
      capture: {vpc_id: "$.vpc.id"}            # first key = primary capture
      ready: {field: "$.vpc.state", until: ACTIVE, timeout: 600,
              interval: 10, endpoint: "GET /v1/vpcs/{vpc_id}"}  # endpoint
              # optional — defaults to GET on the delete endpoint's path
      verify:                       # optional; default = GET read endpoint
        - {name: set, endpoint: "PUT /v1/vpcs/{vpc_id}", json: {...},
           expect_status: [200, 202]}
      delete: {endpoint: "DELETE /v1/vpcs/{vpc_id}", destructive: true}
      quota: vpc
      provenance: VALIDATED

Body/verify template placeholders (C2 refinements, relayed to R1/R2b):

  {opt.<name>}          option value (explicit > default > pick-scheme >
                        first enum value).  A dict KEY whose value references
                        an *unprovided optional* option is dropped from the
                        body.  A `type: ref` option, when provided, pulls its
                        `target:` node into the closure and substitutes the
                        target's primary capture var.
  {<node>.<cap>}        capture var of the FIRST instance of a required node
                        (e.g. {vpc.vpc_id} -> {vpc_id}).
  {<node>.<i>.<cap>}    capture var of instance i (1-based) when count > 1
                        (e.g. {vpc.2.vpc_id} -> {vpc_id_2}).
  {dep.<bind>}          the chosen one_of branch of the group whose `bind:`
                        is <bind>; substitutes the capture var named by the
                        branch's `use:` (default: the node's primary capture).
  {self.<cap>}          this node's own capture var (instance-suffixed).
  {unique}/{ualpha}/... dot-less tokens pass through to the engine untouched.

Instance naming: the shared instance of node N is "N"; extras (count>1) are
"N#2", "N#3", ... and their capture vars get a "_2"/"_3" suffix; their step
names get a "-2"/"-3" suffix.  Per-instance option overrides use the
"N#2" key in `options`.

CIDR derivation (deterministic, documented for tests):
  pick: unique-block   -> 10.<160+k>.0.0/20, k = 0-based order of unique-block
                          allocations within one compose/plan call.
  pick: sub-block-of   -> the (8+j)-th /24 inside the parent instance's
                          allocated cidr, j = 0-based count of prior
                          sub-blocks carved from that same parent instance
                          (e.g. parent 10.160.0.0/20 -> 10.160.8.0/24).
  An explicitly provided option value always wins over the scheme.

Branch choice (plan):  explicit `choices` > in-bundle preference (§2.5 rule
4: a branch already in the closure) > cheapest default (fewest transitive
create instances, then fewest heavy nodes, then branch list order).

Teardown (interval scheduling, §2.5 rule 3): one reverse pass at the end,
ordered by ascending "last use" (a node's last use = the latest creation/
verify position among itself and its transitive dependents), ties broken by
descending creation position — so a shared subnet is deleted only after its
last dependent, and the vpc goes last.
"""
from __future__ import annotations

import json
import ipaddress
import re
from pathlib import Path

__all__ = ["load_model", "plan", "compose", "graph_view", "focus_view",
           "dependents", "ComposeError"]

DEFAULT_MODEL_DIR = "knowledge/formal/resources"

# ---------------------------------------------------------------------------
# validator invariants — import the real scenario validator's helpers when
# importable; otherwise replicate the minimal constants it defines.
try:  # pragma: no cover - exercised implicitly
    from regression.scenarios.validate import (_placeholders_in, BUILTINS,
                                               METHODS)
except Exception:  # pragma: no cover - fallback mirrors validate.py
    METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    BUILTINS = {"unique", "ualpha", "region", "today", "today_plus_5y",
                "shared_vpc_id", "shared_subnet_id",
                "cert_body", "private_key", "cert_chain"}
    _PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

    def _placeholders_in(obj):
        out = set()
        if isinstance(obj, str):
            out |= set(_PLACEHOLDER_RE.findall(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                out |= _placeholders_in(v)
        elif isinstance(obj, list):
            for v in obj:
                out |= _placeholders_in(v)
        return out


class ComposeError(ValueError):
    """Raised on a model/selection problem or a failed validation hook."""


# ---------------------------------------------------------------------------
# load_model

def load_model(dir: str = DEFAULT_MODEL_DIR) -> dict:
    """Merge every ``<category>__<service>.yaml`` under *dir* (contract C1).

    Files starting with ``_`` (e.g. ``_groups.yaml``) are skipped.  A node id
    defined in two files is a hard error.  Returns ``{node_id: task}``.
    """
    import yaml

    root = Path(dir)
    if not root.is_dir():
        raise ComposeError(f"model directory not found: {root}")
    model: dict = {}
    origin: dict = {}
    for path in sorted(root.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        for node_id, task in (data.get("resources") or {}).items():
            if node_id in model:
                raise ComposeError(
                    f"duplicate resource node '{node_id}' in {path.name} "
                    f"(already defined in {origin[node_id]})")
            model[node_id] = task or {}
            origin[node_id] = path.name
    return model


# ---------------------------------------------------------------------------
# requires-entry normalisation

def _norm_requires(task: dict) -> tuple[list, list, list]:
    """Split a node's requires into (and_deps, one_of_groups, credentials).

    and_deps:      [{"ref": node, "count": n}]
    one_of_groups: [{"bind": name|None, "branches": [{"ref":..,"use":..}]}]
    credentials:   [name, ...] — preconditions, never create steps (§1)
    """
    and_deps, groups, creds = [], [], []
    for entry in task.get("requires") or []:
        if isinstance(entry, str):
            and_deps.append({"ref": entry, "count": 1})
        elif isinstance(entry, dict) and "one_of" in entry:
            branches = []
            for b in entry["one_of"]:
                if isinstance(b, str):
                    branches.append({"ref": b, "use": None})
                else:
                    branches.append({"ref": b["ref"], "use": b.get("use")})
            groups.append({"bind": entry.get("bind"), "branches": branches})
        elif isinstance(entry, dict) and "credential" in entry:
            creds.append(str(entry["credential"]))
        elif isinstance(entry, dict) and "ref" in entry:
            and_deps.append({"ref": entry["ref"],
                             "count": int(entry.get("count", 1))})
        else:
            raise ComposeError(f"unrecognised requires entry: {entry!r}")
    return and_deps, groups, creds


def _ref_option_targets(task: dict, node_id: str, options: dict) -> dict:
    """{opt_name: target_node} for ref options that are active (provided or
    required)."""
    out = {}
    opts = ((task.get("create") or {}).get("options")) or {}
    provided = options.get(node_id) or {}
    for name, spec in opts.items():
        if not isinstance(spec, dict) or spec.get("type") != "ref":
            continue
        if spec.get("required") or provided.get(name):
            target = spec.get("target")
            if not target:
                raise ComposeError(
                    f"{node_id}: ref option '{name}' has no target")
            out[name] = target
    return out


# ---------------------------------------------------------------------------
# branch cost (cheapest default = fewest transitive creates, light > heavy)

def _branch_cost(node: str, model: dict, _memo: dict, _stack: frozenset
                 ) -> tuple[int, int]:
    """(transitive create instances, heavy node count) of *node*'s default
    closure (nested one_of resolved by the same default rule)."""
    if node in _memo:
        return _memo[node]
    if node in _stack:
        return (10 ** 6, 10 ** 6)  # cycle: effectively infinite
    task = model.get(node)
    if task is None:
        raise ComposeError(f"unknown resource node '{node}'")
    stack = _stack | {node}
    creates, heavy = 1, (1 if task.get("heavy") else 0)
    and_deps, groups, _creds = _norm_requires(task)
    for dep in and_deps:
        # count:N adds N instances of the ref node but its own prerequisite
        # closure is shared, so cost = closure once + (count-1) extras.
        c, h = _branch_cost(dep["ref"], model, _memo, stack)
        creates += c + (dep["count"] - 1)
        heavy += h
    for grp in groups:
        best = min((_branch_cost(b["ref"], model, _memo, stack) + (i,)
                    for i, b in enumerate(grp["branches"])),
                   key=lambda t: (t[0], t[1], t[2]))
        creates += best[0]
        heavy += best[1]
    _memo[node] = (creates, heavy)
    return _memo[node]


def _resolve_group(node: str, grp: dict, choices: dict,
                   in_bundle: set, model: dict, memo: dict) -> dict:
    """Pick a branch for one one_of group -> {"ref","use"}."""
    refs = [b["ref"] for b in grp["branches"]]
    chosen = choices.get(node)
    if chosen is not None:
        # `choices[node]` may be a single branch ref or a list (several
        # one_of groups on one node).
        cands = chosen if isinstance(chosen, (list, tuple)) else [chosen]
        for c in cands:
            if c in refs:
                return grp["branches"][refs.index(c)]
    # §2.5 rule 4 — prefer a branch already present in the bundle closure
    for b in grp["branches"]:
        if b["ref"] in in_bundle:
            return b
    # cheapest default
    best, best_key = None, None
    for i, b in enumerate(grp["branches"]):
        c, h = _branch_cost(b["ref"], model, memo, frozenset())
        key = (c, h, i)
        if best_key is None or key < best_key:
            best, best_key = b, key
    return best


# ---------------------------------------------------------------------------
# plan

def plan(targets: list, choices: dict | None = None,
         options: dict | None = None, model: dict | None = None) -> dict:
    """Dependency closure + ordering for *targets* (contract C2).

    Returns ``{"targets", "order", "teardown", "dedup", "peak_quota",
    "branches", "instances"}`` — ``order``/``teardown`` are instance ids in
    creation/deletion order; ``branches`` records the one_of branch actually
    taken per node; ``dedup`` maps each shared prerequisite to the sorted
    list of nodes that consume it (created once, §2.5 rule 1).
    """
    choices = choices or {}
    options = options or {}
    if model is None:
        model = load_model()
    if not targets:
        raise ComposeError("plan() needs at least one target node")
    for t in targets:
        if t not in model:
            raise ComposeError(f"unknown target node '{t}'")

    decl_index = {n: i for i, n in enumerate(model)}

    pool: dict[str, int] = {}            # node -> instances needed
    consumers: dict[str, set] = {}       # node -> consumer node ids
    deps: dict[str, list] = {}           # node -> [(dep_node, count)]
    binds: dict[str, dict] = {}          # node -> {bind_name: {"ref","use"}}
    branch_refs: dict[str, set] = {}     # node -> all one_of branch refs
    branches_taken: dict[str, object] = {}
    credentials: set[str] = set()        # §1 credential preconditions (no steps)
    pending_groups: list = []            # (node, group_index, group)

    def _expand(node: str):
        if node in pool:
            return
        pool[node] = 1
        deps[node] = []
        binds[node] = {}
        task = model.get(node)
        if task is None:
            raise ComposeError(f"unknown resource node '{node}'")
        and_deps, groups, creds = _norm_requires(task)
        for c in creds:
            credentials.add(c)
        for d in and_deps:
            deps[node].append((d["ref"], d["count"]))
            consumers.setdefault(d["ref"], set()).add(node)
            _expand(d["ref"])
            pool[d["ref"]] = max(pool[d["ref"]], d["count"])
        for gi, grp in enumerate(groups):
            pending_groups.append((node, gi, grp))
            branch_refs.setdefault(node, set()).update(
                b["ref"] for b in grp["branches"])
        for opt_name, target in _ref_option_targets(task, node,
                                                    options).items():
            deps[node].append((target, 1))
            consumers.setdefault(target, set()).add(node)
            _expand(target)

    for t in sorted(targets):
        _expand(t)

    cost_memo: dict = {}
    while pending_groups:
        pending_groups.sort(key=lambda g: (decl_index.get(g[0], 10 ** 6),
                                           g[1]))
        node, gi, grp = pending_groups.pop(0)
        b = _resolve_group(node, grp, choices, set(pool), model, cost_memo)
        ref = b["ref"]
        bind_name = grp.get("bind") or f"one_of_{gi}"
        binds[node][bind_name] = {"ref": ref, "use": b.get("use")}
        if node not in branches_taken:
            branches_taken[node] = ref
        else:
            cur = branches_taken[node]
            branches_taken[node] = (cur if isinstance(cur, list)
                                    else [cur]) + [ref]
        deps[node].append((ref, 1))
        consumers.setdefault(ref, set()).add(node)
        _expand(ref)

    # an explicit choice that names no branch of its node is a typo — fail
    # loudly rather than silently composing the default branch
    for node, chosen in sorted(choices.items()):
        if node not in pool:
            continue
        refs = branch_refs.get(node, set())
        cands = chosen if isinstance(chosen, (list, tuple)) else [chosen]
        for c in cands:
            if c not in refs:
                raise ComposeError(
                    f"{node}: choice '{c}' is not a one_of branch "
                    f"(expected one of {sorted(refs)})")

    # ---- instances + edges -------------------------------------------------
    def _inst(node: str, i: int) -> str:
        return node if i == 1 else f"{node}#{i}"

    instances: list[str] = []
    inst_deps: dict[str, list[str]] = {}
    assign: dict[str, dict[str, list[str]]] = {}   # inst -> node -> [insts]
    for node in pool:
        for i in range(1, pool[node] + 1):
            instances.append(_inst(node, i))
    for node in pool:
        a: dict[str, list[str]] = {}
        for dep_node, count in deps[node]:
            a.setdefault(dep_node, [])
            for p in (_inst(dep_node, i) for i in range(1, count + 1)):
                if p not in a[dep_node]:
                    a[dep_node].append(p)
        for i in range(1, pool[node] + 1):
            inst = _inst(node, i)
            assign[inst] = a
            inst_deps[inst] = [x for lst in a.values() for x in lst]

    # ---- deterministic topological order (Kahn; ready set sorted by model
    # declaration order, then instance number) ------------------------------
    def _sort_key(inst: str):
        node, _, num = inst.partition("#")
        return (decl_index.get(node, 10 ** 6), int(num or 1))

    remaining = {i: set(inst_deps[i]) for i in instances}
    order: list[str] = []
    while remaining:
        ready = sorted((i for i, d in remaining.items() if not d),
                       key=_sort_key)
        if not ready:
            raise ComposeError(
                f"dependency cycle among: {sorted(remaining)}")
        nxt = ready[0]
        order.append(nxt)
        del remaining[nxt]
        for d in remaining.values():
            d.discard(nxt)

    # ---- interval-scheduled teardown (§2.5 rule 3) -------------------------
    pos = {inst: i for i, inst in enumerate(order)}
    dependents: dict[str, set] = {i: set() for i in instances}
    for inst, dl in inst_deps.items():
        for d in dl:
            dependents[d].add(inst)

    last_use: dict[str, int] = {}

    def _last_use(inst: str, _seen=()):
        if inst in last_use:
            return last_use[inst]
        lu = pos[inst]
        for d in sorted(dependents[inst]):
            if d not in _seen:
                lu = max(lu, _last_use(d, _seen + (inst,)))
        last_use[inst] = lu
        return lu

    for inst in order:
        _last_use(inst)
    teardown = sorted(order, key=lambda i: (last_use[i], -pos[i]))

    # ---- dedup report + peak quota -----------------------------------------
    dedup = {node: sorted(consumers[node])
             for node in sorted(consumers, key=lambda n: decl_index.get(n, 0))
             if consumers[node]}
    # All instances are alive together between the last create and the first
    # delete (teardown is one end-phase reverse pass), so the peak concurrent
    # count per quota kind equals the instance total per kind.
    peak_quota: dict[str, int] = {}
    for node, n in pool.items():
        kind = (model[node] or {}).get("quota")
        if kind:
            peak_quota[kind] = peak_quota.get(kind, 0) + n
    peak_quota = dict(sorted(peak_quota.items()))

    return {
        "targets": list(targets),
        "order": order,
        "teardown": teardown,
        "dedup": dedup,
        "peak_quota": peak_quota,
        "branches": dict(sorted(branches_taken.items())),
        "credentials": sorted(credentials),
        "instances": {n: pool[n] for n in sorted(pool,
                                                 key=lambda x: decl_index.get(
                                                     x, 10 ** 6))},
        "_binds": binds,
        "_assign": assign,
        "_dependents": {i: sorted(d) for i, d in dependents.items()},
    }


# ---------------------------------------------------------------------------
# option resolution + template substitution

_TOKEN_RE = re.compile(r"\{([A-Za-z0-9_][A-Za-z0-9_.\-]*)\}")


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    method, _, path = (endpoint or "").partition(" ")
    method, path = method.strip().upper(), path.strip()
    if method not in METHODS or not path:
        raise ComposeError(f"bad endpoint '{endpoint}' (want 'METHOD /path')")
    return method, path


class _Ctx:
    """Per-compose substitution state (cidr counters, resolved options)."""

    def __init__(self, model, planned, options):
        self.model = model
        self.planned = planned
        self.options = options or {}
        self.unique_blocks = 0                  # unique-block counter
        self.sub_blocks: dict[str, int] = {}    # parent inst -> carved count
        self.opt_values: dict[str, dict] = {}   # inst -> {opt: value}
        self.capvar: dict[str, dict] = {}       # inst -> {cap_key: var}

    # -- capture vars --------------------------------------------------------
    def capture_vars(self, inst: str) -> dict:
        if inst not in self.capvar:
            node, _, num = inst.partition("#")
            suffix = f"_{num}" if num else ""
            caps = (self.model[node] or {}).get("capture") or {}
            self.capvar[inst] = {k: f"{k}{suffix}" for k in caps}
        return self.capvar[inst]

    def primary_capture(self, inst: str) -> str:
        caps = self.capture_vars(inst)
        if not caps:
            node = inst.partition("#")[0]
            raise ComposeError(f"node '{node}' has no capture to reference")
        return next(iter(caps.values()))

    # -- options ---------------------------------------------------------------
    def explicit_option(self, inst: str, name: str):
        node = inst.partition("#")[0]
        for key in (inst, node) if inst != node else (node,):
            val = (self.options.get(key) or {}).get(name)
            if val is not None:
                return val
        return None

    def resolve_option(self, inst: str, name: str):
        vals = self.opt_values.setdefault(inst, {})
        if name in vals:
            return vals[name]
        node = inst.partition("#")[0]
        spec = (((self.model[node] or {}).get("create") or {})
                .get("options") or {}).get(name)
        if spec is None:
            raise ComposeError(f"{node}: unknown option '{name}'")
        explicit = self.explicit_option(inst, name)
        value = None
        typ = spec.get("type")
        if typ == "ref":
            if explicit or spec.get("required"):
                target = spec["target"]
                value = "{%s}" % self.primary_capture(target)
            else:
                value = None  # optional ref not taken -> caller drops the key
        elif explicit is not None:
            if typ == "enum" and explicit not in (spec.get("values") or []):
                raise ComposeError(
                    f"{node}: option '{name}'={explicit!r} not in enum "
                    f"{spec.get('values')}")
            value = explicit
        elif "default" in spec:
            value = spec["default"]
        elif typ == "enum":
            values = spec.get("values") or []
            if not values:
                raise ComposeError(f"{node}: enum option '{name}' has no "
                                   f"values")
            value = values[0]
        elif typ == "cidr":
            value = self._pick_cidr(inst, name, spec)
        elif spec.get("required"):
            raise ComposeError(
                f"{node}: required option '{name}' has no value, default or "
                f"pick scheme")
        # option defaults may themselves be templates (e.g. ske-cluster's
        # kubernetes_version defaulting to the lookup capture
        # "{kubernetes-version.kube_ver}") — resolve nested tokens so an
        # explicit override and the default land in the same (engine-ready)
        # form
        if isinstance(value, str) and "{" in value and "." in value:
            value = self.sub(inst, value)
        vals[name] = value
        return value

    def _pick_cidr(self, inst: str, name: str, spec: dict) -> str:
        pick = spec.get("pick")
        if pick == "unique-block":
            block = f"10.{160 + self.unique_blocks}.0.0/20"
            self.unique_blocks += 1
            return block
        if pick == "sub-block-of":
            of = spec.get("of") or ""
            parent_node, _, parent_opt = of.partition(".")
            node = inst.partition("#")[0]
            parents = (self.planned["_assign"].get(inst) or {}).get(
                parent_node)
            if not parents:
                raise ComposeError(
                    f"{node}: sub-block-of refers to '{parent_node}' which "
                    f"is not among its prerequisites")
            parent_inst = parents[0]
            parent_cidr = self.resolve_option(parent_inst,
                                              parent_opt or "cidr")
            j = self.sub_blocks.get(parent_inst, 0)
            self.sub_blocks[parent_inst] = j + 1
            net = ipaddress.ip_network(str(parent_cidr))
            subs = list(net.subnets(new_prefix=24))
            idx = 8 + j
            if idx >= len(subs):
                raise ComposeError(f"no /24 left in {parent_cidr}")
            return str(subs[idx])
        raise ComposeError(f"unknown cidr pick scheme '{pick}'")

    # -- token substitution ----------------------------------------------------
    def _token_value(self, inst: str, token: str):
        """Resolve one dotted token for instance *inst*; returns the
        replacement, or the sentinel ``_DROP`` for an unprovided optional
        option."""
        parts = token.split(".")
        node = inst.partition("#")[0]
        if parts[0] == "opt":
            val = self.resolve_option(inst, parts[1])
            return _DROP if val is None else val
        if parts[0] == "self":
            caps = self.capture_vars(inst)
            if parts[1] not in caps:
                raise ComposeError(f"{node}: no capture '{parts[1]}'")
            return "{%s}" % caps[parts[1]]
        if parts[0] == "dep":
            bind = (self.planned["_binds"].get(node) or {}).get(parts[1])
            if bind is None:
                raise ComposeError(f"{node}: no one_of bind '{parts[1]}'")
            dep_inst = (self.planned["_assign"][inst].get(bind["ref"])
                        or [bind["ref"]])[0]
            caps = self.capture_vars(dep_inst)
            use = bind.get("use")
            if use:
                if use not in caps:
                    raise ComposeError(
                        f"{node}: branch '{bind['ref']}' has no capture "
                        f"'{use}'")
                return "{%s}" % caps[use]
            return "{%s}" % self.primary_capture(dep_inst)
        if parts[0] in self.model:
            dep_node = parts[0]
            assigned = (self.planned["_assign"].get(inst) or {}).get(dep_node)
            if not assigned:
                raise ComposeError(
                    f"{node}: template references '{dep_node}' which is not "
                    f"among its prerequisites")
            if len(parts) == 3 and parts[1].isdigit():
                idx = int(parts[1]) - 1
                cap_key = parts[2]
            else:
                idx, cap_key = 0, parts[1]
            if idx >= len(assigned):
                raise ComposeError(
                    f"{node}: needs instance {idx + 1} of '{dep_node}' but "
                    f"only {len(assigned)} assigned")
            caps = self.capture_vars(assigned[idx])
            if cap_key not in caps:
                raise ComposeError(
                    f"{node}: '{dep_node}' has no capture '{cap_key}'")
            return "{%s}" % caps[cap_key]
        raise ComposeError(f"{node}: unresolvable placeholder "
                           f"{{{token}}}")

    def sub(self, inst: str, obj, *, own_vars: bool = True):
        """Recursively substitute composer tokens in *obj* for *inst*.

        Dot-less tokens that match the node's own capture keys are rewritten
        to the instance var (suffix for count>1); other dot-less tokens
        (engine builtins / already-wired vars) pass through.
        """
        if isinstance(obj, str):
            caps = self.capture_vars(inst) if own_vars else {}
            # extra instances (node#2, node#3) must get instance-unique NAMES,
            # else two instances of the same node render identical {unique}
            # values and collide (live 409 PolicyAlreadyExist, run 27452095757).
            # Append the instance number to the run-unique builtins so the
            # engine's single per-run value still yields distinct names.
            inum = inst.partition("#")[2]

            single = _TOKEN_RE.fullmatch(obj)
            if single:
                tok = single.group(1)
                if "." in tok:
                    return self._token_value(inst, tok)
                if tok in caps:
                    return "{%s}" % caps[tok]
                if inum and tok in ("unique", "ualpha"):
                    return "{%s}%s" % (tok, inum)
                return obj

            def repl(m):
                tok = m.group(1)
                if "." in tok:
                    val = self._token_value(inst, tok)
                    if val is _DROP:
                        raise _DropKey()
                    return val if isinstance(val, str) else str(val)
                if tok in caps:
                    return "{%s}" % caps[tok]
                if inum and tok in ("unique", "ualpha"):
                    return "{%s}%s" % (tok, inum)
                return m.group(0)

            return _TOKEN_RE.sub(repl, obj)
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                try:
                    sv = self.sub(inst, v, own_vars=own_vars)
                except _DropKey:
                    continue
                if sv is _DROP:
                    continue
                out[k] = sv
            return out
        if isinstance(obj, list):
            out_l = []
            for v in obj:
                try:
                    sv = self.sub(inst, v, own_vars=own_vars)
                except _DropKey:
                    continue
                if sv is not _DROP:
                    out_l.append(sv)
            return out_l
        return obj


_DROP = object()


class _DropKey(Exception):
    pass


# ---------------------------------------------------------------------------
# compose

def _short_service(task: dict) -> str | None:
    svc = task.get("service")
    return svc.split("/")[-1] if svc else None


def _step_suffix(inst: str) -> str:
    _, _, num = inst.partition("#")
    return f"-{num}" if num else ""


def compose(targets: list, choices: dict | None = None,
            options: dict | None = None, model: dict | None = None,
            lifecycle_id: str | None = None) -> dict:
    """Emit an engine lifecycle JSON for *targets* (contract C2).

    Single target -> id ``gen-<node>``; multiple targets -> bundle
    composition per §2.5 with id ``bundle-<sorted-targets>``.  Output is
    deterministic (same inputs => byte-identical ``json.dumps``) and has
    passed the scenario-validator invariants before being returned.
    """
    if model is None:
        model = load_model()
    planned = plan(targets, choices=choices, options=options, model=model)
    ctx = _Ctx(model, planned, options)

    # capture_soft nodes may end up with unfilled capture vars — they cannot
    # be prerequisites of other planned nodes (those would inherit a literal
    # "{var}" in their bodies/paths).
    for inst in planned["order"]:
        node = inst.partition("#")[0]
        if not (model[node] or {}).get("capture_soft"):
            continue
        for other_inst in planned["order"]:
            other = other_inst.partition("#")[0]
            if other == node:
                continue
            and_deps, one_ofs, _ = _norm_requires(model[other] or {})
            dep_nodes = [d["ref"] for d in and_deps] + \
                [b["ref"] for grp in one_ofs for b in grp["branches"]]
            if node in dep_nodes:
                raise ComposeError(
                    f"node '{other}' requires capture_soft node '{node}' — "
                    f"soft lookups cannot feed other nodes")

    target_set = set(targets)
    if lifecycle_id is None:
        lifecycle_id = (f"gen-{targets[0]}" if len(targets) == 1
                        else "bundle-" + "-".join(sorted(target_set)))

    steps: list[dict] = []

    def _read_endpoint(task: dict) -> tuple[str, str]:
        ready = task.get("ready") or {}
        if ready.get("endpoint"):
            return _split_endpoint(ready["endpoint"])
        delete = task.get("delete") or {}
        if delete.get("endpoint"):
            return "GET", _split_endpoint(delete["endpoint"])[1]
        raise ComposeError("no ready.endpoint and no delete.endpoint to "
                           "derive a read path from")

    # ---- create (+ ready, + grafted verify for targets) in plan order -----
    for inst in planned["order"]:
        node = inst.partition("#")[0]
        task = model[node]
        create = task.get("create") or {}
        if not create.get("endpoint"):
            raise ComposeError(f"node '{node}' has no create.endpoint")
        method, path = _split_endpoint(create["endpoint"])
        svc = _short_service(task)
        sfx = _step_suffix(inst)

        step = {"name": f"create-{node}{sfx}"}
        if svc:
            step["service"] = svc
        step["method"] = method
        step["path"] = ctx.sub(inst, path)
        body = create.get("body")
        if body is not None:
            step["json"] = ctx.sub(inst, body)
        if create.get("headers") is not None:
            step["headers"] = ctx.sub(inst, create["headers"])
        # explicit create retry semantics (e.g. attach-time 409 races where
        # the prerequisite is ready but the target briefly conflicts) — same
        # passthrough the delete side already has
        if create.get("retry_on_status"):
            step["retry_on_status"] = list(create["retry_on_status"])
            step["retries"] = int(create.get("retries", 8))
            step["retry_interval"] = int(create.get("retry_interval", 15))
        step["expect_status"] = [200, 201, 202]
        caps = ctx.capture_vars(inst)
        if caps:
            model_caps = task.get("capture") or {}
            # capture_soft: true (lookup nodes whose source list can be
            # legitimately empty — e.g. quota-requests/inquiries): the engine
            # fills the var only when present, and the node's verify steps are
            # emitted optional so an unfilled {var} skips the group instead of
            # failing the lifecycle. Such a node must not feed other nodes.
            ck = "capture_soft" if task.get("capture_soft") else "capture"
            step[ck] = {caps[k]: model_caps[k] for k in model_caps}
        delete = task.get("delete") or {}
        if delete.get("endpoint"):
            dmethod, dpath = _split_endpoint(delete["endpoint"])
            cleanup = {"method": dmethod, "path": ctx.sub(inst, dpath)}
            if delete.get("json") is not None:
                cleanup["json"] = ctx.sub(inst, delete["json"])
            if svc:
                cleanup["service"] = svc
            step["cleanup"] = cleanup
        # adopt marks only the SHARED instance — count>1 extras self-create
        # (plan §2 step 5)
        if task.get("adopt") and "#" not in inst:
            step["adopt"] = task["adopt"]
        steps.append(step)

        ready = task.get("ready")
        if ready:
            rmethod, rpath = _read_endpoint(task)
            until = ready.get("until")
            poll = {"field": ready["field"],
                    "until": until if isinstance(until, list) else [until],
                    "timeout": ready.get("timeout", 180),
                    "interval": ready.get("interval", 10)}
            wstep = {"name": f"wait-{node}{sfx}"}
            if svc:
                wstep["service"] = svc
            wstep.update({"method": rmethod, "path": ctx.sub(inst, rpath),
                          "expect_status": [200], "poll": poll})
            steps.append(wstep)

        # verify: only for target nodes, grafted on the shared first
        # instance (a target that is also a prerequisite gets NO second
        # create, §2.5 rule 2).
        if node in target_set and "#" not in inst:
            vdefs = task.get("verify")
            if vdefs:
                for vi, v in enumerate(vdefs):
                    vmethod, vpath = _split_endpoint(v["endpoint"])
                    vstep = {"name": f"verify-{node}-"
                                     f"{v.get('name', vi + 1)}"}
                    if svc:
                        vstep["service"] = svc
                    vstep["method"] = vmethod
                    vstep["path"] = ctx.sub(inst, vpath)
                    if v.get("json") is not None:
                        vstep["json"] = ctx.sub(inst, v["json"])
                    if v.get("headers") is not None:
                        vstep["headers"] = ctx.sub(inst, v["headers"])
                    vstep["expect_status"] = v.get("expect_status") or [200]
                    # verify entries may carry retry semantics (e.g. DBaaS
                    # state-sensitive ops that 400 'not in RUNNING' while the
                    # cluster reconciles after a prior setter) — passthrough.
                    if v.get("retry_on_status"):
                        vstep["retry_on_status"] = list(v["retry_on_status"])
                        vstep["retries"] = int(v.get("retries", 8))
                        vstep["retry_interval"] = int(v.get("retry_interval", 30))
                    if len(target_set) > 1:
                        vstep["group"] = node
                    if task.get("capture_soft"):
                        vstep["optional"] = True
                        vstep.setdefault("group", node)
                    steps.append(vstep)
            else:
                rmethod, rpath = _read_endpoint(task)
                vstep = {"name": f"verify-{node}"}
                if svc:
                    vstep["service"] = svc
                vstep.update({"method": rmethod,
                              "path": ctx.sub(inst, rpath),
                              "expect_status": [200]})
                if len(target_set) > 1:
                    vstep["group"] = node
                if task.get("capture_soft"):
                    vstep["optional"] = True
                    vstep.setdefault("group", node)
                steps.append(vstep)

    # ---- teardown: one reverse pass in interval-scheduled order ------------
    dependents_of = planned.get("_dependents") or {}
    for inst in planned["teardown"]:
        node = inst.partition("#")[0]
        task = model[node]
        delete = task.get("delete") or {}
        if not delete.get("endpoint"):
            continue
        dmethod, dpath = _split_endpoint(delete["endpoint"])
        svc = _short_service(task)
        sfx = _step_suffix(inst)
        dstep = {"name": f"delete-{node}{sfx}"}
        if svc:
            dstep["service"] = svc
        dstep.update({"method": dmethod, "path": ctx.sub(inst, dpath),
                      "expect_status": [200, 202, 204],
                      "destructive": True})
        if delete.get("json") is not None:  # body-carrying teardown (PUT/POST
            dstep["json"] = ctx.sub(inst, delete["json"])  # terminate etc.)
        # A parent's delete can race its children's async (202) deletes and
        # 409 until they finish — the same conflict-retry semantics every
        # VALIDATED hand-written lifecycle carries on such deletes (live
        # lesson: gen-pilot-net-basics delete-vpc 409 after igw/subnet 202s).
        if dependents_of.get(inst):
            dstep.update({"expect_status": [200, 202, 204, 409, 404],
                          "retry_on_status": [409],
                          "retries": 40, "retry_interval": 30})
        # an explicit model expectation wins over both defaults
        if delete.get("expect_status"):
            dstep["expect_status"] = list(delete["expect_status"])
        # explicit retry semantics (e.g. provisioning-race 500s) also win
        if delete.get("retry_on_status"):
            dstep["retry_on_status"] = list(delete["retry_on_status"])
            dstep["retries"] = int(delete.get("retries", 8))
            dstep["retry_interval"] = int(delete.get("retry_interval", 15))
        if task.get("adopt") and "#" not in inst:
            dstep["adopt"] = task["adopt"]
        steps.append(dstep)

    service = model[targets[0] if len(target_set) == 1
                    else sorted(target_set)[0]].get("service")
    lifecycle = {
        "id": lifecycle_id,
        "service": service,
        "enabled": False,  # drafts are never auto-enabled (contract C4)
        "_note": ("composed by regression/scenarios/composer.py — targets: "
                  + ", ".join(sorted(target_set))
                  + (("; branches: " + json.dumps(planned["branches"],
                                                  sort_keys=True))
                     if planned["branches"] else "")
                  + (("; needs credentials: "
                      + ", ".join(planned.get("credentials") or []))
                     if planned.get("credentials") else "")),
        "steps": steps,
    }
    # §1 credential preconditions surface on the lifecycle (no create steps);
    # the run gate / operator checks these before enabling.
    if planned.get("credentials"):
        lifecycle["credentials"] = list(planned["credentials"])
    _validate_composed(lifecycle)
    return lifecycle


# ---------------------------------------------------------------------------
# validation hook — the scenario validator's error-level invariants applied
# to one in-memory lifecycle (validate.py only runs over the merged on-disk
# set, so its checks are replicated here using its own imported helpers).

def _validate_composed(lc: dict) -> None:
    errors: list[str] = []
    steps = lc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ComposeError(f"{lc.get('id')}: 'steps' must be a non-empty "
                           f"list")
    names = [s.get("name") for s in steps]
    for n in names:
        if not n:
            errors.append("a step is missing 'name'")
    dupes = sorted({n for n in names if n and names.count(n) > 1})
    if dupes:
        errors.append(f"duplicate step names: {dupes}")

    available = set(BUILTINS)
    destructive_paths: set[str] = set()
    for s in steps:
        if s.get("destructive"):
            destructive_paths.add(s.get("path", ""))
    for s in steps:
        sw = f"step '{s.get('name')}'"
        method = (s.get("method") or "").upper()
        if "path" in s:
            if method not in METHODS:
                errors.append(f"{sw}: bad method '{method}'")
            used = (_placeholders_in(s.get("path"))
                    | _placeholders_in(s.get("json"))
                    | _placeholders_in(s.get("params")))
            missing = used - available
            if missing:
                errors.append(f"{sw}: references undefined placeholders "
                              f"{sorted(missing)} (capture them earlier?)")
        es = s.get("expect_status")
        if es is not None and not (isinstance(es, list)
                                   and all(isinstance(x, int) for x in es)):
            errors.append(f"{sw}: expect_status must be a list of ints")
        for ck in ("capture", "capture_soft"):
            if s.get(ck) is not None and not isinstance(s[ck], dict):
                errors.append(f"{sw}: {ck} must be a dict")
        cu = s.get("cleanup")
        if cu is not None and (not isinstance(cu, dict) or "method" not in cu
                               or "path" not in cu):
            errors.append(f"{sw}: cleanup needs 'method' and 'path'")
        poll = s.get("poll")
        if poll is not None and (not isinstance(poll, dict) or not (
                poll.get("until_status")
                or (poll.get("field") and poll.get("until")))):
            errors.append(f"{sw}: poll needs until_status OR field+until")
        # every create that captures a resource must have a destructive
        # teardown step (matching its cleanup path) later in the lifecycle
        if method == "POST" and s.get("capture") and isinstance(cu, dict):
            if cu.get("path") not in destructive_paths:
                errors.append(f"{sw}: no destructive teardown step for "
                              f"{cu.get('path')}")
        for ck in ("capture", "capture_soft"):
            if isinstance(s.get(ck), dict):
                available |= set(s[ck])

    if errors:
        raise ComposeError(f"{lc.get('id')}: composed lifecycle failed "
                           "validation:\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------------------
# graph views (R-platform P0) — read-only projections of the model for the
# control-plane graph UI. Pure: no network, no engine. The composer stays the
# single source of truth; the UI renders what these return.

def dependents(node_id: str, model: dict | None = None) -> list:
    """Nodes whose ``requires`` reference *node_id* (AND or any one_of branch).

    The inverse of the dependency edge — "what needs this resource".
    """
    if model is None:
        model = load_model()
    out = []
    for nid, task in model.items():
        and_deps, groups, _ = _norm_requires(task or {})
        refs = {d["ref"] for d in and_deps}
        for g in groups:
            refs.update(b["ref"] for b in g["branches"])
        if node_id in refs:
            out.append(nid)
    return sorted(out)


def graph_view(targets: list, choices: dict | None = None,
               options: dict | None = None, model: dict | None = None) -> dict:
    """Layout-agnostic graph projection of a target set's dependency closure.

    Returns ``{nodes, edges, levels, shared, peak_quota, order, teardown}``
    where each node carries ``{id, service, provenance, quota, heavy,
    options, level, is_target, shared}`` and ``level`` is the longest-path
    topological depth (level-parallel grouping). Reuses :func:`plan` so the
    closure/branch/dedup decisions are identical to what gets composed.
    """
    if model is None:
        model = load_model()
    p = plan(targets, choices, options, model)

    def _base(inst):
        return inst.split("#", 1)[0]

    nodeset, seen = [], set()
    for inst in p["order"]:
        b = _base(inst)
        if b not in seen:
            seen.add(b)
            nodeset.append(b)

    branches = p.get("branches", {})

    def _chosen(node):
        bt = branches.get(node)
        if bt is None:
            return []
        return list(bt) if isinstance(bt, (list, tuple)) else [bt]

    edges, adj = [], {n: [] for n in nodeset}
    for node in nodeset:
        and_deps, _groups, _ = _norm_requires(model.get(node) or {})
        refs = [d["ref"] for d in and_deps] + _chosen(node)
        for r in refs:
            if r in seen and r != node:
                edges.append({"from": r, "to": node})
                adj[node].append(r)

    depth: dict = {}

    def _d(n, stack):
        if n in depth:
            return depth[n]
        if n in stack:
            return 0
        stack.add(n)
        ds = [_d(r, stack) for r in adj.get(n, []) if r in adj]
        v = 0 if not ds else 1 + max(ds)
        stack.discard(n)
        depth[n] = v
        return v

    for n in nodeset:
        _d(n, set())

    tset, shared = set(targets), set(p.get("dedup", {}).keys())
    nodes = []
    for n in nodeset:
        t = model.get(n) or {}
        nodes.append({
            "id": n,
            "service": t.get("service", ""),
            "provenance": t.get("provenance", "?"),
            "quota": t.get("quota"),
            "heavy": bool(t.get("heavy", False)),
            "options": list(((t.get("create") or {}).get("options") or {}).keys()),
            "level": depth.get(n, 0),
            "is_target": n in tset,
            "shared": n in shared,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "levels": sorted(set(depth.values())) if depth else [0],
        "shared": sorted(shared),
        "peak_quota": p.get("peak_quota", {}),
        "order": p["order"],
        "teardown": p["teardown"],
    }


def focus_view(focus: str, model: dict | None = None) -> dict:
    """Graph for ONE node: its upstream closure + direct dependents (downstream).

    Dependents are added as leaf nodes (``is_dependent: true``) one level past
    the focus so the UI can show "what this needs" (left) and "what needs this"
    (right) in a single picture.
    """
    if model is None:
        model = load_model()
    gv = graph_view([focus], model=model)
    seen = {n["id"] for n in gv["nodes"]}
    flevel = next((n["level"] for n in gv["nodes"] if n["id"] == focus), 0)
    for dep in dependents(focus, model):
        if dep not in seen:
            t = model.get(dep) or {}
            gv["nodes"].append({
                "id": dep,
                "service": t.get("service", ""),
                "provenance": t.get("provenance", "?"),
                "quota": t.get("quota"),
                "heavy": bool(t.get("heavy", False)),
                "options": list(((t.get("create") or {}).get("options") or {}).keys()),
                "level": flevel + 1,
                "is_target": False,
                "shared": False,
                "is_dependent": True,
            })
            seen.add(dep)
        gv["edges"].append({"from": focus, "to": dep})
    gv["focus"] = focus
    return gv
