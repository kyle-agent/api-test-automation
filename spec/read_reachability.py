"""read_reachability (Piece 2 of the create→조회(show) coverage effort) — a pure
static analysis joining the API catalog with the enrichment sidecar, classifying
EVERY id-bound GET endpoint by whether a known producer can supply the path ids it
needs.

Run with::

    python -m spec.read_reachability

This NEVER touches the network, the engine, or the live model. The verdict source
of truth is the **enrichment sidecar** ``data/api_catalog_params.json`` (written by
``spec.enrich_catalog``), which records, per path-param, the AUTHORITATIVE producer
(``produced_by`` + ``producer_kind`` + ``capture``) by resource identity — the same
data the engine's identity probe uses. We no longer guess producers by capture-var
name (the retired ``near_misses``/``_TRIVIAL`` heuristic mis-judged any producer that
existed under a different var name). It also reads ``core.catalog.load_catalog()``
(endpoint set) and ``data/api_docs.json`` (required query params). It writes two
artifacts and prints a summary:

  * ``docs/working/trackers/READ-REACHABILITY.md``  — dated per-service report (the
    durable gap map; the 2026-06-18 snapshot was archived to
    ``docs/archive/trackers/READ-REACHABILITY.md`` — regenerating writes a fresh
    working-tier report).
  * ``reports/read_reachability.json`` — machine-readable verdict rows (gitignored dir).

Cross-reference: ``docs/archive/plans/COVERAGE-GETID-PLAN.md`` §7 (probe_reads UNDER-SEEDING) and
its "Piece 1 — engine auto-probe" / "Piece 2" / "Piece 3" subsections. The
``model-gap`` list here IS Piece 3's worklist.

VERDICTS (per id-bound GET in a service the model knows):
  model-gap         — at least one path-param has produced_by=null with NO waiver,
                      i.e. no known producer. The real backlog: needs a producer
                      (new capture / child node / list-recover step) or a waiver.
  waiver            — all path-params accounted for, but at least one is an HONEST
                      waiver (produced_by=null, producer_kind="waiver"): no producer
                      exists (name-addressed / console-only / EOL). Not auto-reachable,
                      but not a backlog item either.
  query-param       — all path-params have a known producer, but the GET ALSO carries
                      a required query param (api_docs `in: query`, `required: true`)
                      which the read-only auto-probe cannot supply — needs an explicit
                      model `verify` step wiring those params.
  cat2-needs-child  — all path-params have a known producer, no required query, but at
                      least one producer is NOT a same-service collection `create`
                      (producer_kind in create-xsvc/lookup/detail-read/async-op). Such
                      a read only fires once that cross-service / non-create producer
                      is composed beyond the resource's own create spine.
  cat1-auto         — all path-params produced by a same-service collection POST
                      (producer_kind=="create"). Piece-1 auto-probe (seeding
                      probe_reads from the create lifecycle's capture ctx) fires it
                      for free from the resource's own lifecycle.

verdict source: ``producer_kind`` is assigned by ``spec.enrich_catalog`` from the REST
collection-POST convention plus residual evidence (cross-service body fields, DBaaS
detail-reads, async-op ids, genuine waivers). Reading it here keeps these verdicts
aligned with the engine's actual identity probe rather than a name heuristic.
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
SIDECAR_PATH = ROOT / "data" / "api_catalog_params.json"
MD_OUT = ROOT / "docs" / "working" / "trackers" / "READ-REACHABILITY.md"
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
# model projection: which services the resource model knows (verdict gating only)
# --------------------------------------------------------------------------- #
def model_service_set(model: dict) -> set[str]:
    """Bare service names (``mysql``, not ``database/mysql``) the model has a node
    for. We only classify id-bound GETs whose service the model knows; producer
    identity itself now comes from the enrichment sidecar, not the model graph."""
    out: set[str] = set()
    for task in model.values():
        svc = (task or {}).get("service") or ""
        if svc:
            out.add(svc.split("/")[-1])
    return out


# --------------------------------------------------------------------------- #
# enrichment sidecar: authoritative per-path-param producer
# --------------------------------------------------------------------------- #
def load_param_sidecar(path: Path = SIDECAR_PATH) -> dict:
    """{endpoint-key -> {param-name -> {produced_by, producer_kind, capture, role}}}.

    The sidecar (``spec.enrich_catalog``) is keyed by catalog key (== endpoint.key,
    1:1) and lists ``path_params`` in path order with the AUTHORITATIVE producer per
    param. We index by param NAME (names are unique within a path and align with the
    catalog placeholders) so verdicts are independent of list ordering."""
    try:
        docs = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for key, ep in docs.items():
        out[key] = {
            pp.get("name"): pp
            for pp in (ep.get("path_params") or [])
            if pp.get("name")
        }
    return out


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
# core verdict — driven by the enrichment sidecar's authoritative producer
# --------------------------------------------------------------------------- #
# producer_kind values that mean "auto-probe fires from the resource's OWN create
# lifecycle" (a same-service collection POST). Anything else is a producer beyond
# the create spine (cross-service create, lookup, detail-read, async-op).
_AUTO_KIND = "create"


def classify(endpoint, param_sidecar, required_query) -> dict:
    """Classify one id-bound catalog GET from the enrichment sidecar. Returns a row.

    Verdict precedence (worst-first): a single unproducible param dominates.
      model-gap  : any path-param produced_by=null and NOT a waiver.
      waiver     : (no model-gap) any path-param is an honest waiver.
      query-param: (all producible, no waiver) a required query param blocks probe.
      cat2-needs-child: a producer is not a same-service `create` (xsvc/lookup/…).
      cat1-auto  : every producer is a same-service collection POST.
    """
    path = endpoint.http_path
    pps = _PLACEHOLDER.findall(path)
    params = param_sidecar.get(endpoint.key, {})
    rq = required_query.get(endpoint.key)  # list, [] (none), or None (unknown)

    # Resolve each path-param against the sidecar. ``producers`` keeps the report's
    # producer column populated (param -> [producer_key] | []) for downstream parse.
    producers: dict[str, list[str]] = {}
    kinds: dict[str, str | None] = {}
    has_gap = has_waiver = has_nonauto = False
    for p in pps:
        meta = params.get(p) or {}
        produced_by = meta.get("produced_by")
        kind = meta.get("producer_kind")
        kinds[p] = kind
        if produced_by:
            producers[p] = [produced_by]
            if kind != _AUTO_KIND:
                has_nonauto = True
        else:
            producers[p] = []
            if kind == "waiver":
                has_waiver = True
            else:
                has_gap = True  # null producer, no waiver → genuine backlog

    if has_gap:
        verdict = "model-gap"
    elif has_waiver:
        verdict = "waiver"
    elif rq:  # non-empty required-query list
        verdict = "query-param"
    elif has_nonauto:
        verdict = "cat2-needs-child"
    else:
        verdict = "cat1-auto"

    return {
        "path": path,
        "method": "GET",
        "norm_path": _norm(path),
        "key": endpoint.key,
        "path_params": pps,
        "producers": producers,
        "producer_kinds": kinds,
        "required_query": rq,  # list | None(unknown)
        "verdict": verdict,
    }


def analyze():
    catalog = load_catalog()
    model = composer.load_model()
    model_services = model_service_set(model)
    param_sidecar = load_param_sidecar()
    required_query = load_required_query()

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
        row = classify(e, param_sidecar, required_query)
        rows_by_service[e.service].append(row)

    return rows_by_service, skipped_no_model


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
_VERDICTS = ["model-gap", "waiver", "query-param", "cat2-needs-child", "cat1-auto"]


def _producer_cell(producers: dict, kinds: dict | None = None) -> str:
    kinds = kinds or {}
    parts = []
    for p, nodes in producers.items():
        if nodes:
            k = kinds.get(p)
            suffix = f" ({k})" if k else ""
            parts.append(f"`{p}`→{','.join(nodes)}{suffix}")
        elif kinds.get(p) == "waiver":
            parts.append(f"`{p}`→waiver")
        else:
            parts.append(f"`{p}`→∅")
    return "<br>".join(parts) if parts else "—"


def _query_cell(rq) -> str:
    if rq is None:
        return "unknown"
    if not rq:
        return "no"
    return "**yes**: " + ", ".join(rq)


def render_markdown(rows_by_service, skipped_no_model) -> str:
    # global counts
    totals = {v: 0 for v in _VERDICTS}
    total = 0
    for rows in rows_by_service.values():
        for r in rows:
            totals[r["verdict"]] += 1
            total += 1

    # model-gap params (Piece 3 worklist seed)
    gap_param_count: dict[str, int] = defaultdict(int)
    for rows in rows_by_service.values():
        for r in rows:
            if r["verdict"] != "model-gap":
                continue
            for p, nodes in r["producers"].items():
                if not nodes and r["producer_kinds"].get(p) != "waiver":
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
                 f"create→조회(show) coverage effort). Pure static catalog×sidecar "
                 f"join (`data/api_catalog_params.json` authoritative producers) — "
                 f"no network, no engine, no live model.")
    lines.append(">")
    lines.append("> Cross-ref: `docs/archive/plans/COVERAGE-GETID-PLAN.md` §7 (probe_reads "
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
    lines.append(f"| `model-gap` | {totals['model-gap']} | a path-param has NO known "
                 "producer (`produced_by`=null, not a waiver) — Piece 3 backlog |")
    lines.append(f"| `waiver` | {totals['waiver']} | a path-param is an honest waiver "
                 "(no producer exists: name-addressed / console-only / EOL) |")
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
    lines.append("Every id-bound GET with at least one path-param the enrichment "
                 "sidecar has NO known producer for (`produced_by`=null, not a "
                 "waiver). The `∅` param is the one to close — find/declare a "
                 "producer in `spec.enrich_catalog` (new capture / child node / "
                 "list-recover sub-step), or tag it a waiver if none exists.")
    lines.append("")
    lines.append("| service | GET path | unproduced param(s) |")
    lines.append("|---|---|---|")
    gap_rows = []
    for svc in sorted(rows_by_service):
        for r in rows_by_service[svc]:
            if r["verdict"] != "model-gap":
                continue
            unprod = [p for p, n in r["producers"].items()
                      if not n and r["producer_kinds"].get(p) != "waiver"]
            gap_rows.append((svc, r["path"], unprod))
    for svc, path, unprod in sorted(gap_rows, key=lambda x: (x[0], x[1])):
        up_cell = ", ".join(f"`{p}`" for p in unprod)
        lines.append(f"| {svc} | `{path}` | {up_cell} |")
    lines.append("")
    # aggregate unproduced-param frequency (the most-leveraged fixes)
    lines.append("**Unproduced path-params by frequency** (a single producer "
                 "declaration may close several rows):")
    lines.append("")
    lines.append("| param | # GETs blocked |")
    lines.append("|---|---|")
    for p, n in sorted(gap_param_count.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"| `{p}` | {n} |")
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
                f"| `{r['path']}` | {pp_cell} | "
                f"{_producer_cell(r['producers'], r.get('producer_kinds'))} "
                f"| {_query_cell(r['required_query'])} | `{r['verdict']}` |")
        lines.append("")

    return "\n".join(lines) + "\n"


def build_json(rows_by_service) -> dict:
    """{service: [ {path, method, path_params, producers, producer_kinds,
    required_query, verdict} ]}"""
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
                "producer_kinds": r["producer_kinds"],
                "required_query": r["required_query"],
                "verdict": r["verdict"],
            }
            for r in sorted(rows_by_service[svc], key=lambda r: r["path"])
        ]
    return out


def main() -> int:
    rows_by_service, skipped_no_model = analyze()

    totals = {v: 0 for v in _VERDICTS}
    total = 0
    for rows in rows_by_service.values():
        for r in rows:
            totals[r["verdict"]] += 1
            total += 1

    # write artifacts
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(render_markdown(rows_by_service, skipped_no_model))
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(build_json(rows_by_service), indent=2,
                                   ensure_ascii=False) + "\n")

    # stdout summary
    print("read-reachability — id-bound GET classification (static catalog×sidecar)")
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
