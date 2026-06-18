"""read_reachability (Piece 2 of the create→조회(show) coverage effort) — a pure
static analysis joining the API catalog with the resource-task model, classifying
EVERY id-bound GET endpoint by whether the model can produce the path ids it needs.

Run with::

    python -m spec.read_reachability

This NEVER touches the network, the engine, or the live model — it only *reads*
``core.catalog.load_catalog()``, ``regression.scenarios.composer.load_model()`` and
``data/api_docs.json``. It writes two artifacts and prints a summary:

  * ``docs/READ-REACHABILITY.md``  — dated per-service report (the durable gap map).
  * ``reports/read_reachability.json`` — machine-readable verdict rows (gitignored dir).

Cross-reference: ``docs/COVERAGE-GETID-PLAN.md`` §7 (probe_reads UNDER-SEEDING) and
its "Piece 1 — engine auto-probe" / "Piece 2" / "Piece 3" subsections. The
``model-gap`` list here IS Piece 3's worklist.

VERDICTS (per id-bound GET in a service the model knows):
  model-gap         — at least one path-param has NO producing model node. The real
                      backlog: needs a new capture / child node / list-recover step.
  query-param       — all path-params produced, but the GET ALSO carries a required
                      query param (api_docs `in: query`, `required: true`) which the
                      read-only auto-probe cannot supply — needs an explicit model
                      `verify` step wiring those params.
  cat2-needs-child  — all path-params produced, no required query, but reading this
                      endpoint depends on a CHILD created beyond the resource's own
                      create spine. Heuristic (documented below): the set of DISTINCT
                      model nodes producing the path-params spans >= 3 levels of
                      nesting (root → child → grandchild), OR a producer is off the
                      deepest node's requires-ancestor chain. Such a read only fires
                      once the leaf child node is itself composed into a lifecycle.
  cat1-auto         — all path-params produced by the deepest resource node plus its
                      requires ancestors, with <= 2 distinct producing nodes. Piece-1
                      auto-probe (seeding probe_reads from the full capture ctx) fires
                      it for free from the resource's own lifecycle.

cat2 heuristic rationale: a single-resource lifecycle's auto-probe seeds the ids it
captured along ONE create spine (root resource + its direct requires). A GET whose
ids come from a deeper nested child (api → resource → method) needs that child
composed; the >=3-distinct-producer threshold is the calibration boundary
(apigw usage-plans/{usage_plan_id} = 2 nodes → cat1; resources/{rid}/methods/{type}
= 3 nodes → cat2). The boundary is deliberately soft (per the plan); query-param and
model-gap are the load-bearing, unambiguous buckets.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from core.catalog import load_catalog
from regression.scenarios import composer

ROOT = Path(__file__).resolve().parent.parent
API_DOCS_PATH = ROOT / "data" / "api_docs.json"
MD_OUT = ROOT / "docs" / "READ-REACHABILITY.md"
JSON_OUT = ROOT / "reports" / "read_reachability.json"

# Same placeholder regex the engine uses (regression/scenarios/engine.py:_probe_reads
# uses _PLACEHOLDER.findall; the path-param shape is the bare {name} form).
_PLACEHOLDER = re.compile(r"{([^}]+)}")


def _norm(p: str) -> str:
    """Collapse templated id segments to '*' — mirrors engine._norm_path so our
    verdicts align with how the dashboard/coverage records each endpoint."""
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


# --------------------------------------------------------------------------- #
# model projection: param producers + requires graph
# --------------------------------------------------------------------------- #
def _node_requires(task: dict) -> list[str]:
    """Flatten a node's `requires` into the plain list of referenced node ids
    (AND deps + every one_of branch; credentials are preconditions, not nodes)."""
    out: list[str] = []
    for entry in (task or {}).get("requires") or []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            if "ref" in entry:
                out.append(entry["ref"])
            elif "one_of" in entry:
                for b in entry["one_of"]:
                    out.append(b if isinstance(b, str) else b.get("ref"))
            # {"credential": ...} → precondition, no create node
    return [r for r in out if r]


def build_model_index(model: dict):
    """Return (param_producer, node_service, requires, ancestors_fn).

    param_producer: {capture-var-name -> [node_id, ...]} — the var name a node
    captures IS the attribute-id it produces (model schema §1).
    """
    param_producer: dict[str, list[str]] = defaultdict(list)
    node_service: dict[str, str] = {}
    requires: dict[str, list[str]] = {}

    for nid, task in model.items():
        task = task or {}
        svc = task.get("service") or ""
        node_service[nid] = svc.split("/")[-1] if svc else ""
        caps = task.get("capture")
        if isinstance(caps, dict):
            for var in caps:
                param_producer[var].append(nid)
        requires[nid] = _node_requires(task)

    _anc_memo: dict[str, set] = {}

    def ancestors(nid: str) -> set:
        if nid in _anc_memo:
            return _anc_memo[nid]
        seen: set = set()
        stack = list(requires.get(nid, []))
        while stack:
            r = stack.pop()
            if r in seen:
                continue
            seen.add(r)
            stack.extend(requires.get(r, []))
        _anc_memo[nid] = seen
        return seen

    return param_producer, node_service, requires, ancestors


# --------------------------------------------------------------------------- #
# api_docs: required query params per endpoint
# --------------------------------------------------------------------------- #
def load_required_query(api_docs_path: Path = API_DOCS_PATH) -> dict:
    """{endpoint-key -> [required-query-param-name, ...]} from api_docs.json.

    api_docs is keyed by 'category/service/name' (== catalog .key). Each endpoint
    carries a `parameters` list; we keep `in: query` entries flagged required.
    Endpoints absent from api_docs map to None (→ verdict marks query as 'unknown').
    """
    try:
        docs = json.loads(api_docs_path.read_text())
    except (OSError, ValueError):
        return {}
    eps = docs.get("endpoints") or {}
    out: dict[str, list[str]] = {}
    for key, ep in eps.items():
        rq = []
        for p in (ep.get("parameters") or []):
            if p.get("in") != "query":
                continue
            req = p.get("required")
            if req is True or str(req).lower() == "true":
                rq.append(p.get("name"))
        out[key] = [n for n in rq if n]
    return out


# --------------------------------------------------------------------------- #
# near-miss name mismatch (catalog path-param vs model capture var)
# --------------------------------------------------------------------------- #
# Bare/ambiguous params that match too many vars to be a useful "near miss".
_TRIVIAL = {"id", "key", "name", "region", "service", "type", "tags_id"}


def _suffix_on_boundary(short: str, long: str) -> bool:
    """True if `short` is a '_'-boundary suffix of `long` (e.g. engine_version_id
    of dbaas_engine_version_id, srn of rg_srn) — a clean prefix-mismatch signal."""
    if short == long or len(short) >= len(long):
        return False
    return long.endswith(short) and long[len(long) - len(short) - 1] == "_"


def near_misses(param: str, all_vars: set[str]) -> list[str]:
    """Model capture vars that look like a renamed form of catalog `param`."""
    if param in _TRIVIAL:
        return []
    out: set[str] = set()
    for v in all_vars:
        if v == param:
            continue
        if _suffix_on_boundary(param, v) or _suffix_on_boundary(v, param):
            out.add(v)
        # abbreviation: shared head token where one stem prefixes the other
        # (repo/repository, reg/registry, cert/certificate) AND both end _id.
        elif param.endswith("_id") and v.endswith("_id"):
            ps, vs = param[:-3].split("_"), v[:-3].split("_")
            if ps and vs and (ps[0].startswith(vs[0]) or vs[0].startswith(ps[0])) \
                    and ps[0] != vs[0] and min(len(ps[0]), len(vs[0])) >= 3:
                out.add(v)
    return sorted(out)


# --------------------------------------------------------------------------- #
# core verdict
# --------------------------------------------------------------------------- #
def classify(endpoint, param_producer, ancestors, required_query) -> dict:
    """Classify one id-bound catalog GET. Returns a row dict."""
    path = endpoint.http_path
    pps = _PLACEHOLDER.findall(path)
    prods = {p: list(param_producer.get(p, [])) for p in pps}
    rq = required_query.get(endpoint.key)  # list, [] (none), or None (unknown)

    missing = [p for p in pps if not prods[p]]
    if missing:
        verdict = "model-gap"
    elif rq:  # non-empty required-query list
        verdict = "query-param"
    else:
        # deepest path-param's producer is the resource being read.
        deep = next(prods[p][0] for p in reversed(pps) if prods[p])
        chain = {deep} | ancestors(deep)
        distinct = {prods[p][0] for p in pps}
        if not distinct <= chain:
            # a path-param produced by a node off the deepest node's chain — a
            # sibling/child created beyond the create spine.
            verdict = "cat2-needs-child"
        elif len(distinct) >= 3:
            # >=3 nesting levels (root → child → grandchild): the leaf child must
            # be composed for its ids to exist in a lifecycle's capture ctx.
            verdict = "cat2-needs-child"
        else:
            verdict = "cat1-auto"

    return {
        "path": path,
        "method": "GET",
        "norm_path": _norm(path),
        "key": endpoint.key,
        "path_params": pps,
        "producers": {p: prods[p] for p in pps},
        "required_query": rq,  # list | None(unknown)
        "verdict": verdict,
    }


def analyze():
    catalog = load_catalog()
    model = composer.load_model()
    param_producer, node_service, _requires, ancestors = build_model_index(model)
    required_query = load_required_query()
    model_services = {s for s in node_service.values() if s}
    all_vars = set(param_producer)

    rows_by_service: dict[str, list[dict]] = defaultdict(list)
    skipped_no_model = 0
    for e in catalog:
        if (e.method or "").upper() != "GET" or not e.http_path:
            continue
        if not _PLACEHOLDER.findall(e.http_path):
            continue
        if e.service not in model_services:
            skipped_no_model += 1
            continue
        row = classify(e, param_producer, ancestors, required_query)
        rows_by_service[e.service].append(row)

    return rows_by_service, all_vars, skipped_no_model


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
_VERDICTS = ["model-gap", "query-param", "cat2-needs-child", "cat1-auto"]


def _producer_cell(producers: dict) -> str:
    parts = []
    for p, nodes in producers.items():
        if nodes:
            parts.append(f"`{p}`→{','.join(nodes)}")
        else:
            parts.append(f"`{p}`→∅")
    return "<br>".join(parts) if parts else "—"


def _query_cell(rq) -> str:
    if rq is None:
        return "unknown"
    if not rq:
        return "no"
    return "**yes**: " + ", ".join(rq)


def render_markdown(rows_by_service, all_vars, skipped_no_model) -> str:
    # global counts
    totals = {v: 0 for v in _VERDICTS}
    total = 0
    for rows in rows_by_service.values():
        for r in rows:
            totals[r["verdict"]] += 1
            total += 1

    # model-gap params + near-misses (Piece 3 worklist seed)
    gap_param_count: dict[str, int] = defaultdict(int)
    for rows in rows_by_service.values():
        for r in rows:
            if r["verdict"] != "model-gap":
                continue
            for p, nodes in r["producers"].items():
                if not nodes:
                    gap_param_count[p] += 1

    # service order: model-gap count desc, then service name
    def gap_count(svc):
        return sum(1 for r in rows_by_service[svc] if r["verdict"] == "model-gap")

    svc_order = sorted(rows_by_service, key=lambda s: (-gap_count(s), s))

    lines: list[str] = []
    lines.append("# READ-REACHABILITY — id-bound GET reachability from the resource model")
    lines.append("")
    lines.append(f"> Generated: **{date.today().isoformat()}** by "
                 f"`python -m spec.read_reachability` (Piece 2 of the "
                 f"create→조회(show) coverage effort). Pure static catalog×model "
                 f"join — no network, no engine, no live model.")
    lines.append(">")
    lines.append("> Cross-ref: `docs/COVERAGE-GETID-PLAN.md` §7 (probe_reads "
                 "UNDER-SEEDING — the create→조회 gap) and its Piece 1 (engine "
                 "auto-probe), Piece 2 (this report), Piece 3 (burn down "
                 "model-gaps). The **model-gap** section below is Piece 3's worklist.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"Total id-bound GETs analyzed (services present in the model): "
                 f"**{total}**")
    lines.append("")
    lines.append("| verdict | count | meaning |")
    lines.append("|---|---|---|")
    lines.append(f"| `model-gap` | {totals['model-gap']} | a path-param has NO "
                 "producing node — Piece 3 backlog |")
    lines.append(f"| `query-param` | {totals['query-param']} | path-params produced "
                 "but a required query param blocks auto-probe |")
    lines.append(f"| `cat2-needs-child` | {totals['cat2-needs-child']} | produced "
                 "via a child beyond the resource's own create spine |")
    lines.append(f"| `cat1-auto` | {totals['cat1-auto']} | auto-probe (Piece 1) fires "
                 "it for free |")
    lines.append("")
    if skipped_no_model:
        lines.append(f"_(All id-bound GETs fall in services the model knows; "
                     f"{skipped_no_model} excluded for no model service.)_"
                     if False else
                     f"_({skipped_no_model} id-bound GET(s) excluded — their "
                     f"service has no model node.)_")
        lines.append("")

    # ---- top section: full model-gap worklist ----
    lines.append("## model-gap worklist (Piece 3)")
    lines.append("")
    lines.append("Every id-bound GET with at least one path-param no model node "
                 "captures. The `∅` param is the one to close (new capture / child "
                 "node / list-recover sub-step). Near-miss column flags likely "
                 "catalog↔model param NAME mismatches.")
    lines.append("")
    lines.append("| service | GET path | unproduced param(s) | near-miss model capture(s) |")
    lines.append("|---|---|---|---|")
    gap_rows = []
    for svc in sorted(rows_by_service):
        for r in rows_by_service[svc]:
            if r["verdict"] != "model-gap":
                continue
            unprod = [p for p, n in r["producers"].items() if not n]
            nm = sorted({m for p in unprod for m in near_misses(p, all_vars)})
            gap_rows.append((svc, r["path"], unprod, nm))
    for svc, path, unprod, nm in sorted(gap_rows, key=lambda x: (x[0], x[1])):
        nm_cell = ", ".join(f"`{m}`" for m in nm) if nm else "—"
        up_cell = ", ".join(f"`{p}`" for p in unprod)
        lines.append(f"| {svc} | `{path}` | {up_cell} | {nm_cell} |")
    lines.append("")
    # aggregate unproduced-param frequency (the most-leveraged fixes)
    lines.append("**Unproduced path-params by frequency** (a single capture/lookup "
                 "node may close several rows):")
    lines.append("")
    lines.append("| param | # GETs blocked | near-miss model capture(s) |")
    lines.append("|---|---|---|")
    for p, n in sorted(gap_param_count.items(), key=lambda x: (-x[1], x[0])):
        nm = near_misses(p, all_vars)
        nm_cell = ", ".join(f"`{m}`" for m in nm) if nm else "—"
        lines.append(f"| `{p}` | {n} | {nm_cell} |")
    lines.append("")

    # ---- per-service sections ----
    lines.append("## Per-service breakdown")
    lines.append("")
    lines.append("Services sorted by `model-gap` count (descending).")
    lines.append("")
    for svc in svc_order:
        rows = sorted(rows_by_service[svc], key=lambda r: r["path"])
        counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in _VERDICTS}
        badge = " · ".join(f"{v}={counts[v]}" for v in _VERDICTS if counts[v])
        lines.append(f"### {svc}  ({len(rows)} id-bound GET — {badge})")
        lines.append("")
        lines.append("| GET path | path-params | producer node(s) | required query? | verdict |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            pp_cell = ", ".join(f"`{p}`" for p in r["path_params"])
            lines.append(
                f"| `{r['path']}` | {pp_cell} | {_producer_cell(r['producers'])} "
                f"| {_query_cell(r['required_query'])} | `{r['verdict']}` |")
        lines.append("")

    return "\n".join(lines) + "\n"


def build_json(rows_by_service) -> dict:
    """{service: [ {path, method, path_params, producers, required_query, verdict} ]}"""
    out: dict[str, list] = {}
    for svc in sorted(rows_by_service):
        out[svc] = [
            {
                "path": r["path"],
                "method": r["method"],
                "norm_path": r["norm_path"],
                "key": r["key"],
                "path_params": r["path_params"],
                "producers": r["producers"],
                "required_query": r["required_query"],
                "verdict": r["verdict"],
            }
            for r in sorted(rows_by_service[svc], key=lambda r: r["path"])
        ]
    return out


def main() -> int:
    rows_by_service, all_vars, skipped_no_model = analyze()

    totals = {v: 0 for v in _VERDICTS}
    total = 0
    for rows in rows_by_service.values():
        for r in rows:
            totals[r["verdict"]] += 1
            total += 1

    # write artifacts
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(render_markdown(rows_by_service, all_vars, skipped_no_model))
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(build_json(rows_by_service), indent=2,
                                   ensure_ascii=False) + "\n")

    # stdout summary
    print("read-reachability — id-bound GET classification (static catalog×model)")
    print(f"  total analyzed: {total}")
    for v in _VERDICTS:
        print(f"  {v:18} {totals[v]}")
    assert sum(totals.values()) == total, "verdict counts must sum to total"
    if skipped_no_model:
        print(f"  (excluded, no model service: {skipped_no_model})")
    # top services by model-gap
    by_gap = sorted(
        ((sum(1 for r in rows if r["verdict"] == "model-gap"), svc)
         for svc, rows in rows_by_service.items()),
        key=lambda t: (-t[0], t[1]))
    print("  top services by model-gap:")
    for n, svc in by_gap[:5]:
        if n:
            print(f"    {svc}: {n}")
    print(f"  wrote {MD_OUT.relative_to(ROOT)} and {JSON_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
