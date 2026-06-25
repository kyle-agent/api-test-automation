"""controlplane/console_api.py — console2's ``/api/*`` contract, served by the spine.

Convergence **S3**. The control-plane FastAPI app ABSORBS console2 by (a) serving
console2's REAL frontend (``console2/index.html`` + ``console2/assets/*``, mounted at
``/console`` in ``app.py``) and (b) answering its exact ``/api/*`` contract here.

Every route delegates to the framework-agnostic builder/worker functions in
``tools.console2_server`` — the ORIGINAL console2 backend. Same resource model, same
``composer.graph_view`` composition DAG, same ``dag_planner`` schedule, same VPC-cap
admission queue, same provision→pytest→teardown→reconciler run flow. The only thing
that changes is the HTTP layer: console2_server's stdlib ``http.server`` Handler is
replaced by these FastAPI routes, so the screen lives in the ONE console (the spine)
instead of a second standalone app. ``console2.js`` runs **unchanged** — it fetches
the same absolute ``/api/*`` paths.

"Absorb, don't rewrite": console2 was a mature frontend + a real local-execution
backend that only ever lacked a home in the spine. This is that home. The pure
functions stay the shared engine; ``tools/console2_server.py``'s Handler/``main`` is
retired in S4. We deliberately reach into console2_server's ``_``-prefixed functions
— they are the de-facto module API (``build_static.py`` imports them the same way);
keeping ONE implementation beats forking a second copy that would drift.

Safety (Hard Rule 1) is preserved verbatim from console2_server: live-run gates are
DERIVED from the selection (``mutations``/``destructive`` on, ``heavy`` iff the
selected closure contains a billable lifecycle) — the opt-in is the selection + the
client's pre-flight confirm, never a manual heavy flip. ``/api/cleanup`` is blocked
while anything is running/queued (owner-tag reconciler would reap other runs).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

# the original console2 backend — pure builder/worker functions on module state.
# import-safe: its server only starts under ``if __name__ == '__main__'``.
from tools import console2_server as c2

router = APIRouter()


def _json(obj: dict | list, code: int = 200) -> JSONResponse:
    # default=str via console2_server's own json paths; FastAPI's JSONResponse uses
    # the same encoder behavior for our plain dict/list payloads.
    return JSONResponse(content=obj, status_code=code)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


# --- GET: model · definitions · suites · capacity · runs ----------------------

@router.get("/api/model")
def api_model() -> JSONResponse:
    try:
        m = dict(c2._model())
        m["endpoint_params"] = c2._endpoint_params()      # API-tab param schema
        return _json(m)
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"model build failed: {exc}"}, 500)


@router.get("/api/lifecycles")
def api_lifecycles(service: str = "") -> JSONResponse:
    if not service:
        return _json({"error": "service query param required (cat/svc)"}, 400)
    try:
        return _json(c2._lifecycles_view(service))
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"lifecycles view failed: {exc}"}, 500)


@router.get("/api/knowledge")
def api_knowledge(service: str = "") -> JSONResponse:
    if not service:
        return _json({"error": "service query param required (cat/svc)"}, 400)
    try:
        return _json(c2._knowledge_view(service))
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"knowledge view failed: {exc}"}, 500)


@router.get("/api/endpoint-params")
def api_endpoint_params(method: str = "", path: str = "") -> JSONResponse:
    if not path:
        return _json({"error": "path query param required"}, 400)
    try:
        hit = c2._lookup_endpoint_params(method, path)
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"endpoint-params failed: {exc}"}, 500)
    if not hit:
        return _json({"error": "endpoint not in catalog",
                      "method": (method or "").upper(), "path": path}, 404)
    return _json(hit)


@router.get("/api/suites")
def api_suites_list() -> JSONResponse:
    try:
        return _json({"suites": c2._list_suites_view()})
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"suites list failed: {exc}"}, 500)


@router.get("/api/capacity")
def api_capacity() -> JSONResponse:
    try:
        return _json(c2._capacity_view())
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"capacity failed: {exc}"}, 500)


@router.get("/api/runs")
def api_runs() -> JSONResponse:
    with c2._LOCK:
        rows = [c2._rec_view(r) for r in sorted(c2._RUNS.values(),
                                                key=lambda x: x["started"], reverse=True)]
    return _json({"runs": rows})


@router.get("/api/runs/{rid}/events")
def api_run_events(rid: str) -> JSONResponse:
    with c2._LOCK:
        rec = c2._RUNS.get(rid)
    if not rec:
        return _json({"error": "no such run"}, 404)
    return _json({"id": rid, "status": rec["status"], "events": c2._read_events(rec["events"])})


@router.get("/api/runs/{rid}")
def api_run(rid: str) -> JSONResponse:
    with c2._LOCK:
        rec = c2._RUNS.get(rid)
    if not rec:
        return _json({"error": "no such run"}, 404)
    return _json(c2._rec_view(rec, full=True))


@router.get("/api/runtime", response_class=HTMLResponse)
@router.get("/runtime", response_class=HTMLResponse)
def api_runtime(hours: float = 6.0) -> HTMLResponse:
    """Runtime topology (loggingaudit) as standalone HTML for the 런타임 뷰 popup.
    Best-effort: a cold load returns the '수집 중' auto-refresh placeholder."""
    try:
        out, _ready = c2._runtime_view(hours)
    except Exception:                                      # noqa: BLE001
        out = None
    if not out:
        out = c2._runtime_wait_html(hours)
    return HTMLResponse(out, headers={"Cache-Control": "no-cache"})


# --- POST: graph · plan · run · suites · hygiene ------------------------------

@router.post("/api/graph")
async def api_graph(request: Request) -> JSONResponse:
    try:
        return _json(c2._graph(await _body(request)))
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"graph failed: {exc}"}, 500)


@router.post("/api/plan")
async def api_plan(request: Request) -> JSONResponse:
    sel = await _body(request)
    # explicit lifecycle_ids win ONLY when no resolvable selection axis is present —
    # otherwise resolve the selection (node_ids/services/categories) to its closure.
    ids = sel.get("lifecycle_ids") if "lifecycle_ids" in sel and not (
        sel.get("node_ids") or sel.get("services") or sel.get("categories")) \
        else c2._resolve_lifecycle_ids(sel)
    try:
        return _json({"lifecycle_ids": ids, **c2._plan(ids)})
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"plan failed: {exc}"}, 500)


@router.post("/api/run")
async def api_run_start(request: Request) -> JSONResponse:
    b = await _body(request)
    ids = [str(x).strip() for x in (b.get("lifecycle_ids") or []) if str(x).strip()]
    if not ids:
        ids = c2._resolve_lifecycle_ids(b)                 # allow a selection instead
    if not ids:
        return _json({"error": "no lifecycles selected"}, 400)
    mode = b.get("mode", "live")
    heavy = c2._selection_is_heavy(ids)                    # gate DERIVED from selection
    peak = c2._run_peak_vpcs(ids)
    if mode == "live":
        # live CRUD lifecycles need mutations+destructive; heavy auto-enables iff the
        # selected closure contains a heavy (billable) lifecycle. The deliberate
        # opt-in (Hard Rule 1) is the selection + the client pre-flight confirm.
        rec = c2._new_rec("lifecycle", mode="live", lifecycle_ids=ids,
                          heavy=heavy, mutations=True, destructive=True)
        worker = c2._run_worker
    else:
        # simulate stays a server capability (no UI toggle) — exercises the admission
        # queue with zero billing. console2's UI only ever POSTs mode=live.
        rec = c2._new_rec("simulate", mode="simulate", lifecycle_ids=ids, heavy=heavy)
        worker = c2._simulate_worker
    rec["peak_vpcs"], rec["queued"] = peak, False
    c2._admit_or_queue(rec, worker)                        # reserve VPC slots or enqueue
    return _json(c2._rec_view(rec), 202)


@router.post("/api/suites")
async def api_suites_save(request: Request) -> JSONResponse:
    b = await _body(request)
    try:
        view = c2._save_suite(b)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"suite save failed: {exc}"}, 500)
    return _json({"suite": view, "suites": c2._list_suites_view()}, 201)


@router.post("/api/cleanup")
def api_cleanup() -> JSONResponse:
    # the reconciler reaps by owner-tag account-wide (NOT per run) — running it while
    # other runs are in flight would delete THEIR resources too. Block until idle.
    with c2._ADMIT:
        busy = bool(c2._RESERVED or c2._QUEUE)
    if busy:
        return _json({"error":
            "진행 중(또는 대기 중) 실행이 있어 계정 전체 강제 클린업을 막았습니다 — "
            "reconciler 는 owner-tag 로 전체를 reap 하므로 다른 실행이 만든 자원까지 "
            "삭제됩니다. 모든 실행이 끝난 뒤 다시 시도하세요."}, 409)
    return _json(c2._rec_view(c2._start("cleanup", c2._cleanup_worker)), 202)


@router.post("/api/verify")
def api_verify() -> JSONResponse:
    return _json(c2._rec_view(c2._start("verify", c2._verify_worker)), 202)


@router.post("/api/owned")
def api_owned() -> JSONResponse:
    return _json(c2._rec_view(c2._start("owned", c2._owned_worker)), 202)
