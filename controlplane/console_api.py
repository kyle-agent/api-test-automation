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

# per-lifecycle 중단 채널 (owner 2026-07-09): embed 모드에선 이 컨트롤플레인이
# /api/runs/{id}/commands 를 서빙하므로, 로컬 런 엔진 subprocess가 폴링할 URL을
# 컨트롤플레인 자신으로 지정한다 (표준 uvicorn 8000; 다른 포트면 서버 기동 env
# APITEST_PLATFORM_URL로 지정). os.environ은 건드리지 않는다 (import 부작용 금지).
import os as _os
c2.EMBED_PLATFORM_URL = _os.environ.get("APITEST_PLATFORM_URL",
                                        "http://127.0.0.1:8000")

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
        m["durations"] = c2._durations_view()             # now-playing 평균 ETA
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
def api_run_events(rid: str, offset: int = 0) -> JSONResponse:
    with c2._LOCK:
        rec = c2._RUNS.get(rid)
    if not rec:
        return _json({"error": "no such run"}, 404)
    # P2C-24 폴링 다이어트: ?offset=N 증분 응답 — tail 만 전송 (계약: c2._events_view;
    # lifecycle_ids 포함). §4 soft_class(중복/갭/정책) enrich 는 run 전체 문맥
    # 의존이라 슬라이스 **전에** 전체 리스트에 적용한다.
    return _json({"id": rid, **c2._events_view(rec, offset,
                                               enrich=c2._enrich_soft_classes)})


@router.get("/api/runs/{rid}/graph")
def api_run_graph(rid: str) -> JSONResponse:
    """The run's OWN composition DAG (composer.graph_view over its lifecycle
    closure) — the master 흐름 scene binds to THIS in run 모드, so navigating
    away / clicking a history row never leaves it on the 구성 selection [F1·F2].
    Same renderer contract as /api/graph (IA-BUILD-CONTRACT)."""
    with c2._LOCK:
        rec = c2._RUNS.get(rid)
    if not rec:
        return _json({"error": "no such run"}, 404)
    try:
        return _json({"id": rid, **c2._run_graph(rec)})
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"run graph failed: {exc}"}, 500)


@router.get("/api/runs/{rid}")
def api_run(rid: str) -> JSONResponse:
    with c2._LOCK:
        rec = c2._RUNS.get(rid)
    if not rec:
        return _json({"error": "no such run"}, 404)
    return _json(c2._rec_view(rec, full=True))


@router.get("/api/runtime", response_class=HTMLResponse)
@router.get("/runtime", response_class=HTMLResponse)
def api_runtime(hours: float = 1.0, scope: str = "mine",
                deleted: str = "hide") -> HTMLResponse:
    """Runtime topology (loggingaudit × oplog origin join) as standalone HTML.
    scope=mine|all (default mine; auto-falls back to all when mine is empty and
    nothing local is running) · hours∈{1,6,24} (default 1) · deleted=hide|show.
    Best-effort: a cold load returns the '수집 중' auto-refresh placeholder."""
    hours = hours if hours in c2._RUNTIME_HOURS else 1.0
    try:
        out, _ready = c2._runtime_view(hours, scope=scope, deleted=deleted)
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


@router.post("/api/schedule-sim")
async def api_schedule_sim(request: Request) -> JSONResponse:
    """오프라인 스케줄 시뮬 (오너 2026-07-11 '간트를 실제 화면에서 보고 조정') —
    conftest 와 동일 규칙의 예상 동시 배치. API 호출 없음. console2 stdlib
    Handler 의 동명 라우트와 동일 계약 (실서빙은 이 스파인 경로)."""
    b = await _body(request)
    ids = b.get("lifecycle_ids") or None
    if not ids and (b.get("node_ids") or b.get("services") or b.get("categories")):
        ids = c2._resolve_lifecycle_ids(b)
    try:
        from regression.scenarios import local_run as _lr
        return _json(_lr.simulate_schedule(
            ids, workers=b.get("workers"), vpc_slots=int(b.get("vpc_slots") or 4)))
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"schedule-sim failed: {exc}"}, 500)


@router.post("/api/preflight")
async def api_preflight(request: Request) -> JSONResponse:
    """HEAVY-PREMISE-CONTRACT §3 — 실행 전 confirm의 정보원 (자원·과금·예상시간).
    선택 payload를 받아 {lifecycles, resources, peak_quota, billable_count, est,
    warnings}를 반환. staging·직접실행 confirm이 이걸 표시한다."""
    try:
        return _json(c2._preflight(await _body(request)))
    except Exception as exc:                               # noqa: BLE001
        return _json({"error": f"preflight failed: {exc}"}, 500)


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
        # dup-admit guard (2026-07-04): a second LIVE run while one is in flight
        # pollutes the account-wide scan/rescan verdicts — 409, never silent admit.
        act = c2._active_live_run()
        if act:
            return _json({
                "error": ("이미 진행 중(또는 대기 중)인 LIVE 실행이 있습니다 — "
                          f"run {act['id']} "
                          f"({', '.join(act.get('lifecycle_ids') or [])[:120]}). "
                          "동시 LIVE 실행은 자원 스캔·재스캔 판정을 오염시키므로 "
                          "차단됩니다. 완료(또는 중단) 후 다시 시작하세요."),
                "active_run": act["id"]}, 409)
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


@router.post("/api/runs/{rid}/abort")
def api_run_abort(rid: str) -> JSONResponse:
    """로컬 run 중단 (2026-07-04): kill the pytest process tree → teardown 스윕
    → status '중단됨(aborted)'. Engine half lives in c2._abort_run."""
    code, payload = c2._abort_run(rid)
    return _json(payload, code)


@router.post("/api/runs/{rid}/skip-lifecycle")
async def api_run_skip_lifecycle(rid: str, request: Request) -> JSONResponse:
    """특정 라이프사이클만 중단 (owner 2026-07-09) — 엔진 명령 채널에
    stop_polling+skip_scenario 를 넣어 다음 안전 지점에서 정리 후 스킵."""
    body = await _body(request)
    code, payload = c2.skip_lifecycle(rid, str(body.get("lifecycle") or ""))
    return _json(payload, code)


@router.post("/api/verify")
def api_verify() -> JSONResponse:
    return _json(c2._rec_view(c2._start("verify", c2._verify_worker)), 202)


@router.post("/api/owned")
def api_owned() -> JSONResponse:
    return _json(c2._rec_view(c2._start("owned", c2._owned_worker)), 202)


def local_run_summary(gh_run_id: str) -> dict | None:
    """Pass/fail summary for a ``local-*`` run — used by the /runs/{id} detail
    page (P2-9 잔여). Thin re-export of the engine helper."""
    try:
        return c2._local_run_summary(gh_run_id)
    except Exception:                                      # noqa: BLE001
        return None


# Server start (spine import): mirror the rehydrated finished local runs into
# the controlplane runs DB so Reporting ▸ 실행 기록 shows them (신규2 · P2-9).
# c2 already rehydrated _RUNS at its own import; this only backfills the DB.
try:
    c2._backfill_runs_db()
except Exception:                                          # noqa: BLE001
    pass
