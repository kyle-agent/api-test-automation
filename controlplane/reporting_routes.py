"""Reporting (④ 평가) — the coverage "color map" face (IA contract §4 Reporting).

A new APIRouter under the sub-prefix ``/reporting/coverage`` so it never clashes
with the existing inline ``/reporting`` route in app.py (the integration owner
reconciles the two — see the contract). READ-ONLY aggregate only: no execution
or mutation endpoints live here.

THE one rule (IA-BUILD-CONTRACT.md §1): every graph face renders the SAME
``composer.graph_view`` data through the SAME renderer
``controlplane/static/resource_graph.js`` (``ResourceGraph.scene(svg, stage,
data, {overlay})``). A face differs ONLY by its ``overlay(id)`` hook. This
face's overlay = COVERAGE color at service→resource granularity (NOT per-API):

  * tested   → green   — the node's service has at least one 2xx observation
  * modeled  → amber   — the node exists in the model (has provenance) but its
                         service has no 2xx yet
  * untested → gray    — no provenance / no observation (e.g. empty results)

The map renders the whole resource model (every node as a graph_view target).
Coverage is derived from ``reports/results/*.jsonl`` via :mod:`core.results`,
aggregated to the ``category/service`` key. The 2-axis summary (regression =
"does it work?" + conformance = "is it well-designed?") reuses the same
``core.results`` store plus the published-dashboard aggregation in
``controlplane.dashdata`` — nothing is recomputed that already exists.

Routes (all GET, read-only):
  GET /reporting/coverage           coverage map page (also ``""``)
  GET /reporting/coverage/map.json  graph + per-service coverage + 2-axis summary
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core import results as core_results
from controlplane import dashdata

# resource_routes.py pattern: ROOT = repo root, templates next to this module.
ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(prefix="/reporting/coverage")

# coverage states (the overlay vocabulary) — kept in lockstep with the legend
# rendered in the template.
TESTED, MODELED, UNTESTED = "tested", "modeled", "untested"

#: worst-observation-wins rank, mirroring controlplane.compare._RANK so the
#: regression fold here matches the A/B comparison view's verdict semantics.
_RANK = {"fail": 3, "soft": 2, "ok": 1}


# --- model + observations (lazy, failure-tolerant) ---------------------------

def _load_model() -> dict:
    """Whole resource model ``{node_id: task}`` — empty dict on any failure so
    the page degrades to an all-gray map instead of 500-ing."""
    try:
        from regression.scenarios import composer
        return composer.load_model()
    except Exception:
        return {}


def _lifecycle_services() -> dict[str, str]:
    """``lifecycle_id -> 'category/service'`` from the loader — the SAME source
    the live model uses (``regression.scenarios.loader.load_lifecycles``).

    This is what makes the Testing→Reporting arrow actually carry signal: the
    Testing engine records observations under a ``lifecycle:step`` endpoint_key
    (e.g. ``gen-cost-reads:create-cost-reads``), NOT the ``category/service/op``
    shape the read-only smoke/crud sweep uses. Without this map every live
    lifecycle run is invisible to coverage. Empty dict on any failure (the page
    then falls back to the slash-key path only)."""
    try:
        from regression.scenarios.loader import load_lifecycles
        return {lc["id"]: lc.get("service", "")
                for lc in load_lifecycles() if lc.get("id")}
    except Exception:
        return {}


def _service_of_key(endpoint_key: str, lc_services: dict[str, str]) -> str:
    """Observation endpoint_key -> ``category/service`` coverage unit. Two shapes:

      * ``lifecycle:step`` (what the Testing engine records) -> the lifecycle's
        declared service via ``lc_services``.
      * ``category/service/op`` (read-only smoke/crud sweep) -> first 2 segments.

    The two are unambiguous: lifecycle ids are hyphenated with no ``/``; sweep
    keys carry no ``:``. Empty string when neither resolves."""
    ek = endpoint_key or ""
    if ":" in ek:
        return lc_services.get(ek.split(":", 1)[0], "")
    parts = ek.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def _tested_services(observations: list[dict],
                     lc_services: dict[str, str]) -> set[str]:
    """Services (``category/service``) with at least one 2xx observation."""
    tested: set[str] = set()
    for o in observations:
        try:
            code = int(o.get("status") or 0)
        except (TypeError, ValueError):
            code = 0
        if 200 <= code < 300:
            svc = _service_of_key(o.get("endpoint_key") or "", lc_services)
            if svc:
                tested.add(svc)
    return tested


def _coverage_by_service(model: dict, observations: list[dict],
                         lc_services: dict[str, str]) -> dict[str, str]:
    """Map every ``category/service`` present in the model to a coverage state.

    Service→resource granularity (NOT per-API): a service is ``tested`` when ANY
    of its endpoints returned a 2xx; ``modeled`` when the model defines it (it
    has nodes with provenance) but no 2xx is recorded; ``untested`` only when a
    node carries no provenance at all (or there are no results). This is exactly
    the contract's tested/modeled/untested ladder, lifted to the service.
    """
    tested = _tested_services(observations, lc_services)
    # provenance present per service (any node with a non-empty provenance)
    has_prov: dict[str, bool] = {}
    for task in model.values():
        svc = (task or {}).get("service") or ""
        if not svc:
            continue
        prov = str((task or {}).get("provenance") or "").strip()
        has_prov[svc] = has_prov.get(svc, False) or bool(prov and prov != "?")
    out: dict[str, str] = {}
    for svc, provd in has_prov.items():
        if svc in tested:
            out[svc] = TESTED
        elif provd:
            out[svc] = MODELED
        else:
            out[svc] = UNTESTED
    return out


def _graph_view(model: dict) -> dict:
    """Full-model ``composer.graph_view`` (every node a target). Empty graph on
    any failure (the JS then shows the 'no model' note)."""
    if not model:
        return {"nodes": [], "edges": [], "levels": [0], "shared": [],
                "peak_quota": {}, "order": [], "teardown": []}
    try:
        from regression.scenarios import composer
        return composer.graph_view(sorted(model.keys()), model=model)
    except Exception:
        return {"nodes": [], "edges": [], "levels": [0], "shared": [],
                "peak_quota": {}, "order": [], "teardown": []}


# --- 2-axis summary ----------------------------------------------------------

def _fold_observations(observations: list[dict]) -> dict[str, str]:
    """One category per endpoint (worst wins), reusing compare.py's rank — so a
    GET probed by smoke + read-chain counts once, by its harshest verdict."""
    folded: dict[str, str] = {}
    for o in observations:
        ek = o.get("endpoint_key") or o.get("path") or ""
        if not ek:
            continue
        key = f"{(o.get('method') or '').upper()} {ek}"
        cat = o.get("category") or ""
        if key not in folded or _RANK.get(cat, 0) > _RANK.get(folded[key], 0):
            folded[key] = cat
    return folded


def _two_axis_summary(model: dict, observations: list[dict],
                      findings: list[dict],
                      lc_services: dict[str, str]) -> dict:
    """The 2-axis evaluation summary (contract §4): regression (does it work?)
    from observations, conformance (well-designed?) from findings + the
    published conformance summary. Counts + percentages, no recompute of the
    published dashboard's own ladder."""
    folded = _fold_observations(observations)
    ok = sum(1 for c in folded.values() if c == "ok")
    soft = sum(1 for c in folded.values() if c == "soft")
    fail = sum(1 for c in folded.values() if c == "fail")
    n_obs = len(folded)

    cov = _coverage_by_service(model, observations, lc_services)
    svc_total = len(cov)
    svc_tested = sum(1 for s in cov.values() if s == TESTED)
    svc_modeled = sum(1 for s in cov.values() if s == MODELED)
    svc_untested = sum(1 for s in cov.values() if s == UNTESTED)

    regression = {
        "ok": ok, "soft": soft, "fail": fail, "endpoints": n_obs,
        "ok_pct": round(ok * 100 / n_obs) if n_obs else 0,
        "services_total": svc_total,
        "services_tested": svc_tested,
        "services_modeled": svc_modeled,
        "services_untested": svc_untested,
        "services_tested_pct": (round(svc_tested * 100 / svc_total)
                                if svc_total else 0),
    }

    # conformance: local findings (severity red/yellow/green) + the published
    # conformance.json summary when the dashboard-data branch is reachable.
    f_red = sum(1 for f in findings if (f.get("severity") or "") == "red")
    f_yellow = sum(1 for f in findings if (f.get("severity") or "") == "yellow")
    f_green = sum(1 for f in findings if (f.get("severity") or "") == "green")
    published = None
    try:
        published = dashdata.conformance_summary()
    except Exception:
        published = None
    conformance = {
        "findings": len(findings),
        "red": f_red, "yellow": f_yellow, "green": f_green,
        "published": (published or {}).get("summary") if published else None,
        "systemic": (published or {}).get("systemic") if published else None,
    }

    return {"regression": regression, "conformance": conformance}


def _coverage_payload() -> dict:
    """Everything the map page needs, assembled once. Degrades to an all-gray /
    empty-but-valid payload when the model or results are missing."""
    model = _load_model()
    observations = core_results.load_observations()
    findings = core_results.load_findings()
    lc_services = _lifecycle_services()
    graph = _graph_view(model)
    coverage = _coverage_by_service(model, observations, lc_services)
    summary = _two_axis_summary(model, observations, findings, lc_services)
    legend = {
        TESTED: {"fill": "#e7f6ed", "stroke": "#15924f", "label": "tested · 2xx 관측"},
        MODELED: {"fill": "#fdf3e2", "stroke": "#b5740b", "label": "modeled · 모델만"},
        UNTESTED: {"fill": "#eef1f4", "stroke": "#9aa3ad", "label": "untested · 관측 없음"},
    }
    return {
        "graph": graph,
        "coverage": coverage,
        "summary": summary,
        "legend": legend,
        "has_results": bool(observations),
        "has_model": bool(model),
        "history": dashdata.history(limit=20),
    }


# --- routes ------------------------------------------------------------------

@router.get("/map.json")
def coverage_map_json():
    """Graph + per-service coverage state + 2-axis summary for the map JS.

    Read-only; the JS feeds ``graph`` to ``ResourceGraph.scene`` and uses
    ``coverage[node.service]`` inside its ``overlay(id)``.
    """
    return JSONResponse(_coverage_payload())


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def coverage_map(request: Request):
    """Coverage color-map page (active nav = 'reporting')."""
    payload = _coverage_payload()
    ctx = {
        "active": "reporting",
        "summary": payload["summary"],
        "legend": payload["legend"],
        "has_results": payload["has_results"],
        "has_model": payload["has_model"],
        "history": payload["history"],
        "node_count": len(payload["graph"].get("nodes") or []),
    }
    return templates.TemplateResponse(request, "reporting_coverage.html", ctx)
