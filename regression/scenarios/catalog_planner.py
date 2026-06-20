"""catalog_planner — scheduler ADR 1.0: plan from the WHOLE catalog graph.

dag_planner schedules the 184 composed lifecycles under the VPC cap — a projection
that only sees the shared-VPC adoption edges. This module works one level deeper, on
the FULL resource-task model (`composer.load_model`, ~275 resource nodes / ~295
``requires`` edges): vpc, subnet, security-group, keypair, filestorage-volume,
ske-cluster, nodepool, … with the real cross-resource relationships (ske-cluster
requires filestorage-volume, nodepool requires ske-cluster, …).

Given a target set it computes, from that graph:
  * **closure**  — every resource transitively required to stand the targets up.
  * **layers**   — the topological CREATE order (L0 = roots that require nothing:
                   vpc, filestorage-volume, …; each later layer requires earlier
                   ones). This is "what to build first, then what to test" — the
                   order the platform would provision + exercise resources in.
  * **annotations** — which closure nodes are capped (``quota``: vpc≤5, private-dns
                   ≤3), shared/dedup-able (``adopt``: one shared instance serves
                   all dependents), or heavy.

The cap-aware EXECUTION waves (which lifecycles run concurrently) stay in
dag_planner; this is the resource-level dependency brain the Plan/Run overlays draw.
Pure + offline: reads only the composed model + core.budgets limits.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


def _requires_ids(task: dict) -> list[str]:
    """Normalized dependency ids of a node. A ``requires`` entry is usually a bare
    id string; tolerate dict forms ({id}/{one_of:[...]}/{and:[...]}) by collecting
    every id mentioned (closure stays a superset, which is the safe direction)."""
    out: list[str] = []
    for entry in (task.get("requires") or []):
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            if entry.get("id"):
                out.append(entry["id"])
            for key in ("one_of", "and", "any", "all"):
                for m in entry.get(key, []) or []:
                    out.append(m if isinstance(m, str) else m.get("id"))
    return [x for x in out if x]


@dataclass
class Node:
    id: str
    requires: list[str] = field(default_factory=list)   # deps present in the graph
    required_by: list[str] = field(default_factory=list)  # reverse edges
    service: str = ""
    quota: str | None = None        # cap kind (e.g. 'vpc'); None if uncapped
    adopt: str | None = None        # shared-root token if this is dedup-able
    heavy: bool = False
    provenance: str = ""


def load_graph(model: dict | None = None) -> dict[str, Node]:
    """Build the resource dependency graph from the composed model. Edges are kept
    only between nodes that both exist in the model (dangling refs dropped)."""
    if model is None:
        from regression.scenarios import composer
        model = composer.load_model() or {}
    ids = set(model)
    g: dict[str, Node] = {}
    for nid, task in model.items():
        if not isinstance(task, dict):
            continue
        reqs = [r for r in _requires_ids(task) if r in ids]
        g[nid] = Node(
            id=nid, requires=sorted(set(reqs)), service=task.get("service", "") or "",
            quota=task.get("quota"), adopt=task.get("adopt"),
            heavy=bool(task.get("heavy")), provenance=task.get("provenance", "") or "",
        )
    for nid, node in g.items():
        for r in node.requires:
            g[r].required_by.append(nid)
    for node in g.values():
        node.required_by.sort()
    return g


def closure(targets, graph: dict[str, Node]) -> set[str]:
    """Every node transitively required by ``targets`` (targets included)."""
    seen: set[str] = set()
    dq = deque(t for t in targets if t in graph)
    while dq:
        n = dq.popleft()
        if n in seen:
            continue
        seen.add(n)
        dq.extend(r for r in graph[n].requires if r not in seen)
    return seen


def topo_layers(graph: dict[str, Node], subset: set[str] | None = None) -> list[list[str]]:
    """Topological CREATE-order layers over ``subset`` (default: whole graph).

    layer(n) = longest dependency path from a root, so a node always lands strictly
    below every node it requires. Returns ``[[layer-0 ids], [layer-1 ids], ...]``,
    each layer sorted. Raises on a cycle (the model should be a DAG).
    """
    nodes = set(graph) if subset is None else {n for n in subset if n in graph}
    indeg = {n: sum(1 for r in graph[n].requires if r in nodes) for n in nodes}
    children = defaultdict(list)
    for n in nodes:
        for r in graph[n].requires:
            if r in nodes:
                children[r].append(n)
    layer = {n: 0 for n in nodes if indeg[n] == 0}
    dq = deque(layer)
    processed = 0
    order = list(layer)
    while dq:
        n = dq.popleft()
        processed += 1
        for c in children[n]:
            layer[c] = max(layer.get(c, 0), layer[n] + 1)
            indeg[c] -= 1
            if indeg[c] == 0:
                dq.append(c)
                order.append(c)
    if processed != len(nodes):
        cyc = sorted(n for n in nodes if indeg.get(n, 0) > 0)
        raise ValueError(f"requires graph has a cycle among: {cyc[:10]}")
    out: list[list[str]] = [[] for _ in range(max(layer.values(), default=-1) + 1)]
    for n, lvl in layer.items():
        out[lvl].append(n)
    return [sorted(band) for band in out]


@dataclass
class CatalogPlan:
    targets: list[str]
    closure: list[str]
    layers: list[list[str]]           # topological create order
    capped: dict                      # {node: quota_kind}
    shared: dict                      # {node: adopt_token}  dedup-able shared roots
    heavy: list[str]
    caps: dict                        # {quota_kind: limit}

    @property
    def create_order(self) -> list[str]:
        return [n for band in self.layers for n in band]

    def to_dict(self) -> dict:
        return {
            "targets": self.targets, "closure": self.closure,
            "layers": self.layers, "capped": self.capped, "shared": self.shared,
            "heavy": self.heavy, "caps": self.caps,
        }


def _account_caps() -> dict:
    try:
        from core import budgets
        return dict(getattr(budgets, "DEFAULT_LIMITS", {}) or {})
    except Exception:
        return {"vpc": 5, "private-dns": 3}


def plan(targets=None, graph: dict[str, Node] | None = None, caps: dict | None = None) -> CatalogPlan:
    """Plan the create/test order for ``targets`` (default = the whole catalog)."""
    if graph is None:
        graph = load_graph()
    if caps is None:
        caps = _account_caps()
    if targets is None:
        targets = sorted(graph)
    targets = [t for t in targets if t in graph]

    clo = closure(targets, graph)
    layers = topo_layers(graph, clo)
    capped = {n: graph[n].quota for n in sorted(clo) if graph[n].quota}
    shared = {n: graph[n].adopt for n in sorted(clo) if graph[n].adopt}
    heavy = sorted(n for n in clo if graph[n].heavy)
    return CatalogPlan(targets=sorted(targets), closure=sorted(clo), layers=layers,
                       capped=capped, shared=shared, heavy=heavy, caps=dict(caps))


def format_plan(p: CatalogPlan, *, graph: dict[str, Node] | None = None) -> str:
    L = [f"catalog plan: {len(p.targets)} target(s) → closure {len(p.closure)} "
         f"resource(s) in {len(p.layers)} create-layer(s)"]
    cap_note = ", ".join(f"{k}≤{v}" for k, v in sorted(p.caps.items()))
    L.append(f"account caps: {cap_note}")
    if p.capped:
        L.append("capped resources in closure: "
                 + ", ".join(f"{n}({q})" for n, q in p.capped.items()))
    if p.shared:
        L.append("shared/dedup roots: " + ", ".join(sorted(p.shared)))
    for i, band in enumerate(p.layers):
        def tag(n):
            m = ""
            if n in p.capped:
                m += "⚑"           # capped
            if n in p.shared:
                m += "◆"           # shared/dedup
            if graph and graph[n].heavy:
                m += "△"           # heavy
            return n + m
        L.append(f"  L{i} ({len(band)}): " + ", ".join(tag(n) for n in band))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="Plan create/test order from the full catalog resource graph.")
    ap.add_argument("--target", nargs="*", help="resource id(s) to plan for (default: whole catalog)")
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = ap.parse_args(argv)

    graph = load_graph()
    p = plan(targets=args.target, graph=graph)
    if args.json:
        print(json.dumps(p.to_dict(), indent=2))
    else:
        print(format_plan(p, graph=graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
