"""SCP API Regression Test Platform — control-plane server (M1 MVP).

Server-rendered FastAPI + htmx (docs/PLATFORM-PLAN.md §3), organized in the
plan's three areas:

  Planning   environments · suites · scenario catalog · knowledge (read-only
             views now; M3 adds editing on top)
  Testing    manual runs · schedules · live progress
  Reporting  run history · per-run dashboard snapshots · AI triage

Suites and environment profiles are read live from the repo files (suites/,
environments/); runs/schedules/events/triage live in SQLite (db.py).

Run from the repo root:
  pip install -r controlplane/requirements.txt
  uvicorn controlplane.app:app --host 0.0.0.0 --port 8800

Config (env): see controlplane/README.md.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlplane import (authoring, common, compare, dashdata, db, dispatch,
                          local_executor, resources, scheduler, snapshots, triage)
from core import profiles as core_profiles
from core import suites as core_suites

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield


app = FastAPI(title="SCP API Regression Test Platform", lifespan=lifespan)

# AI pipelines (M3 A1~A3) live in their own router — see controlplane/ai_routes.py
from controlplane import ai_routes  # noqa: E402  (import after app to match layout)
app.include_router(ai_routes.router)

# Resource-task-model form UI + composer screens (M5 R2b) — resource_routes.py
from controlplane import resource_routes  # noqa: E402
app.include_router(resource_routes.router)

# Testing(③) — console2 ABSORBED into the spine (convergence S3, IA =
# Catalog·Modeling·Testing·Reporting). console_api serves console2's exact /api/*
# contract by delegating to tools.console2_server's real engine; the unchanged
# console2 frontend is mounted at /testing/console (its 구성|실행 screen-toggle IS
# the Test Planning | Test Execution sub-tabs). console2.js runs verbatim.
from controlplane import console_api  # noqa: E402
app.include_router(console_api.router)
from fastapi.staticfiles import StaticFiles  # noqa: E402


class _NoCacheStatic(StaticFiles):
    """console2 자산은 항상 재검증 — StaticFiles 기본 응답의 휴리스틱 캐시가
    `git pull`+재시작 후에도 브라우저에 옛 console2.js를 남겼다 (2026-07-09 오너
    실측: P2C-22 반영 후에도 구형 실행 뷰가 렌더, 캐시 비우니 정상). 단독
    tools/console2_server 는 이미 no-cache — 이 마운트만 빠져 있었다."""

    async def get_response(self, path, scope):  # noqa: D102
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


app.mount("/testing/console",
          _NoCacheStatic(directory=str(ROOT / "console2"), html=True),
          name="testing-console")

# Catalog(①) + Reporting(④) faces of the confirmed IA — each its own router (built in
# parallel; Modeling(②) rides resource_routes, Testing(③) is console_api above).
from controlplane import catalog_routes, reporting_routes  # noqa: E402
app.include_router(catalog_routes.router)
app.include_router(reporting_routes.router)
# 설정(⚙) — 환경/계정(.env) 입력 화면 (오너 지시 2026-07-15: 테스터 인수인계 시
# 환경·계정이 달라질 수 있으니 화면에서 입력) — settings_routes.py
from controlplane import settings_routes  # noqa: E402
app.include_router(settings_routes.router)
# shared scene renderer (controlplane/static/resource_graph.js) for the graph faces —
# Modeling's model-map and Reporting's coverage-map load it from /static/.
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def _render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    """Every page rides the shared base context (controlplane.common — P1-3):
    suites/profiles for the trigger forms + ctx_snapshot for the ctxbar."""
    return templates.TemplateResponse(request, name,
                                      {**common.base_ctx(active), **ctx})


# --- home ----------------------------------------------------------------------

def _catalog_count() -> int:
    """엔드포인트 수 — 홈 Modeling 칸의 '전체 N API' 분모 (2026-07-07 IA 개정:
    Catalog 칸은 제거, Modeling이 카탈로그를 흡수 — best-effort, 부재 시 0)."""
    try:
        from controlplane import catalog_routes
        return len(catalog_routes._load_catalog())
    except Exception:
        return 0


_STALE_RUNNING_S = 24 * 3600


def _split_stale_running(runs) -> tuple[list, list]:
    """표시층 전용 stale 분리 — DB 상태는 건드리지 않는다. running/dispatched인데
    requested_at이 24h 이상 과거인 run은 죽은 워커/미회수 기록일 가능성이 높아
    '진행 중' 집계에 넣으면 오신호가 된다(페르소나-2 P2C-6) → 별도 목록으로."""
    active, stale = [], []
    for r in runs:
        if r["status"] not in ("running", "dispatched"):
            continue
        try:
            age = time.time() - calendar.timegm(
                time.strptime(r["requested_at"] or "", "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            age = _STALE_RUNNING_S + 1  # 나이 불명 → 집계 신뢰 불가, stale 쪽으로
        (stale if age > _STALE_RUNNING_S else active).append(r)
    return active, stale


# ---- 최근 RUN 표 보강 (오너 '전부 반영' 2026-07-15) ---------------------------
# 표가 "어떻게 됐나"까지 답하게: KST 시작 + 소요 1열(구 UTC ISO 2열 대체 — 배너와
# 같은 시간 언어) + 로컬 런 lifecycle 결과 요약 + 연속 실패 스트릭. 종료 런 요약은
# 불변이라 캐시 — 15s 폴링이 이벤트 파일을 매번 다시 읽지 않는다.
_RUN_SUM_CACHE: dict[str, str | None] = {}


def _run_dur_label(a: str, b: str, status: str) -> str:
    import datetime as _dt
    try:
        t0 = _dt.datetime.strptime(a or "", "%Y-%m-%dT%H:%M:%SZ")
        t1 = _dt.datetime.strptime(b or "", "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "진행 중" if status in ("running", "dispatched") else ""
    sec = max(0, int((t1 - t0).total_seconds()))
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분"


def _run_summary_label(d: dict) -> str | None:
    """로컬 종료 런의 'lifecycle n/m 실패|통과' — c2._local_run_summary 재사용
    (런 상세 페이지와 같은 소스). CI/미종료 런은 None (현행 status 만)."""
    gid = str(d.get("gh_run_id") or "")
    if not gid.startswith("local-") or d.get("status") not in ("failed", "done"):
        return None
    if gid in _RUN_SUM_CACHE:
        return _RUN_SUM_CACHE[gid]
    label = None
    try:
        from tools import console2_server as c2
        s = (c2._local_run_summary(gid) or {}).get("lifecycles") or {}
        if s.get("total"):
            label = (f"lifecycle {s['failed']}/{s['total']} 실패" if s.get("failed")
                     else f"lifecycle {s.get('passed', 0)}/{s['total']} 통과")
    except Exception:                                   # noqa: BLE001 — 요약은 보너스
        label = None
    _RUN_SUM_CACHE[gid] = label
    return label


def _runs_view(rows, display: int | None = None) -> dict:
    """_runs_table.html 컨텍스트: 보강 행(표시분만) + 연속 실패 스트릭(전체 창).

    스트릭 = 최신 종료 런부터 연속 'failed' 수 (진행/대기 중은 건너뜀) —
    3건 이상이면 표 위에 경고 한 줄. last_ok_when = 마지막 성공(done) 시각."""
    n = len(rows) if display is None else display
    view = []
    for r in rows[:n]:
        d = dict(r)
        d["when_label"] = common.snap_ts_short({"ts": d.get("requested_at")})
        d["dur_label"] = _run_dur_label(d.get("requested_at") or "",
                                        d.get("finished_at") or "",
                                        d.get("status") or "")
        d["summary"] = _run_summary_label(d)
        view.append(d)
    streak, last_ok = 0, None
    for r in rows:                                      # 최신순 전제 (list_runs)
        st = r["status"]
        if st in ("running", "dispatched", "pending", "queued"):
            continue
        if st == "failed":
            streak += 1
            continue
        if st == "done":
            last_ok = common.snap_ts_short({"ts": r["requested_at"]}) \
                or (r["requested_at"] or "")
        break
    return {"runs": view, "fail_streak": streak, "last_ok_when": last_ok}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    runs = db.list_runs(limit=50)
    running, stale_running = _split_stale_running(runs)
    today = db.now()[:10]
    runs_today = sum(1 for r in runs
                     if (r["requested_at"] or "").startswith(today))
    coverage = dashdata.latest_coverage()
    # 회귀 배너 내역 미리보기 (오너 공동 설계 2026-07-15, 결정 1-a): 신규 fail 을
    # 서비스별로 집계 — 발행 fail_new.json(정공법, results_data 60s 캐시 경유),
    # 미발행 동안 index 배너 폴백(capped). best-effort: 실패는 빈 목록(미리보기만
    # 생략, 판정·건수는 history 기준 그대로). (구 D2 칩 local_since_publish 는
    # 칩 제거로 함께 정리.)
    # 판정 이후 이 서버 로컬 런 수 (오너 2026-07-15 "오늘 아침에도 돌렸는데 왜
    # 21시간 전?"): 배너의 '이후 로컬 런 N건 미반영' 주석용 — 판정(공식 CI 런)과
    # 콘솔 로컬 런의 관계를 화면이 직접 설명한다. gh_run_id 'local-' 접두 = 로컬,
    # ts 는 양쪽 다 "%Y-%m-%dT%H:%M:%SZ" 라 문자열 비교 안전. limit=50 창 하한값.
    snap_ts = str((coverage or {}).get("ts") or "")
    local_after = sum(
        1 for r in runs
        if str(r["gh_run_id"] or "").startswith("local-")
        and (r["finished_at"] or "") > snap_ts) if snap_ts else 0
    fail_svcs, fail_svcs_capped = [], False
    if coverage and (coverage.get("fail_new") or 0) > 0:
        try:
            from controlplane import results_data
            reg = results_data.get_new_regressions()
            if reg and reg.get("items"):
                cnt: dict[str, int] = {}
                for it in reg["items"]:
                    label = it.get("service_label") or it.get("lifecycle") or "기타"
                    cnt[label] = cnt.get(label, 0) + 1
                fail_svcs = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
                fail_svcs_capped = bool(reg.get("capped"))
        except Exception:                                  # noqa: BLE001
            fail_svcs = []
    rv = _runs_view(runs, display=5)
    return _render(request, "home.html", "home",
                   runs=rv["runs"], fail_streak=rv["fail_streak"],
                   last_ok_when=rv["last_ok_when"], running=running,
                   stale_running=stale_running, runs_today=runs_today,
                   fail_svcs=fail_svcs, fail_svcs_capped=fail_svcs_capped,
                   local_after=local_after,
                   schedules=db.list_schedules(),
                   coverage=coverage,
                   # D8: 잔존 자원 홈 승격 — 마지막 캐시만 읽고 스캔은 트리거하지
                   # 않는다(홈 열 때 자동 수집 금지). 잔존은 라이브 이 서버 관측.
                   # (파이프라인 스트립 제거로 scenario/catalog/model_stats 전달도
                   # 정리 — 헬퍼 함수들은 다른 라우트가 계속 사용.)
                   owned_kpi=resources.owned_summary())


# --- Planning ------------------------------------------------------------------

def _scenario_stats() -> dict:
    try:
        from regression.scenarios.loader import load_lifecycles
        ls = load_lifecycles()
        services = {l.get("service", "") for l in ls}
        return {"total": len(ls),
                "enabled": sum(1 for l in ls if l.get("enabled")),
                "heavy": sum(1 for l in ls if l.get("heavy")),
                "enabled_heavy": sum(1 for l in ls
                                     if l.get("enabled") and l.get("heavy")),
                "services": len(services)}
    except Exception:
        return {"total": 0, "enabled": 0, "heavy": 0, "enabled_heavy": 0,
                "services": 0}


# directories the read-only knowledge browser may serve
_BROWSE_DIRS = ("knowledge", "suites", "environments", "docs")


def _safe_repo_file(rel: str) -> Path | None:
    try:
        path = (ROOT / rel).resolve()
        path.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    if not any(path.relative_to(ROOT).as_posix().startswith(d + "/")
               or path.relative_to(ROOT).as_posix() == d for d in _BROWSE_DIRS):
        return None
    return path if path.is_file() else None


def _model_stats() -> dict:
    """자원 모델 요약 — Plan 흐름 스트립의 ① 재료 칸."""
    try:
        from controlplane import resource_model
        nodes = resource_model.load_model() or {}
        groups = resource_model.load_groups() or {}
        validated = sum(1 for n in nodes.values()
                        if (n or {}).get("provenance") == "VALIDATED")
        return {"nodes": len(nodes), "validated": validated,
                "docs": len(nodes) - validated, "groups": len(groups)}
    except Exception:
        return {"nodes": 0, "validated": 0, "docs": 0, "groups": 0}


@app.get("/search", response_class=HTMLResponse)
def global_search(request: Request, q: str = ""):
    """전역 검색 (v2 접목 6a — 계약 §2.8): 서비스·엔드포인트(카탈로그)·런(이
    서버)을 한 화면에서. 헤더 검색폼(base.html)의 종착지."""
    from controlplane import search as search_mod
    return _render(request, "search.html", "search", data=search_mod.search(q))


@app.get("/planning", include_in_schema=False)
def planning_legacy():
    """구 4단계 스테퍼(?step=) 은퇴 (IA 확정: Catalog·Modeling·Testing·Reporting).
    Modeling의 정본 진입은 /planning/resources/map — 하위 라우트
    (/planning/edit·view·validate·dependencies·scenarios·resources/*)는 유지."""
    return RedirectResponse("/planning/resources/map", status_code=301)


def _run_validate() -> dict:
    """Run `python -m regression.scenarios.validate` as a guarded subprocess and
    parse its stdout into a structured result for the ④ Validate panel.

    The validator prints WARN/ERROR lines plus a summary tail
    'N lifecycle(s) checked · N error(s) · N warning(s)'. Failures (non-zero rc,
    timeout, crash) degrade to {error: ...} rather than 500ing the page."""
    import re
    import subprocess
    import sys as _sys
    try:
        proc = subprocess.run(
            [_sys.executable, "-m", "regression.scenarios.validate"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": str(ROOT)})
    except subprocess.TimeoutExpired:
        return {"error": "검증이 120초 안에 끝나지 않았습니다 (timeout)."}
    except Exception as exc:
        return {"error": f"검증 실행 실패: {exc}"}
    out = proc.stdout or ""
    err_lines = [ln[6:].rstrip() for ln in out.splitlines()
                 if ln.startswith("ERROR ")]
    warn_lines = [ln[6:].rstrip() for ln in out.splitlines()
                  if ln.startswith("WARN  ")]
    checked = errors = warnings = 0
    m = re.search(r"(\d+) lifecycle\(s\) checked · (\d+) error\(s\) · "
                  r"(\d+) warning\(s\)", out)
    if m:
        checked, errors, warnings = (int(m.group(1)), int(m.group(2)),
                                     int(m.group(3)))
    return {"rc": proc.returncode, "checked": checked,
            "errors": errors, "warnings": warnings,
            "error_lines": err_lines[:200],
            "warning_lines": warn_lines[:200],
            "stderr": (proc.stderr or "").strip()[:4000]}


@app.get("/planning/validate", response_class=HTMLResponse)
def planning_validate(request: Request):
    return _render(request, "validate.html", "planning",
                   plan_step="validate", result=_run_validate())


def _fragment_rel(source_name: str) -> str:
    """loader.load_lifecycles(with_sources=True) filename -> repo-relative path
    (the merge in regression/scenarios/loader.py is the mapping's source of
    truth: the base scenarios.json plus one fragment file per service)."""
    if source_name == "scenarios.json":
        return "regression/scenarios/scenarios.json"
    return f"regression/scenarios/lifecycles/{source_name}"


def _scenario_rows(service: str = "", note_chars: int = 300) -> list[dict]:
    """Catalog rows for the Plan-area tables (shared by /planning and
    /planning/scenarios)."""
    from regression.scenarios.loader import load_lifecycles
    lifecycles, sources = load_lifecycles(with_sources=True)
    if service:
        lifecycles = [l for l in lifecycles if service in (l.get("service") or "")]
    return [{
        "id": l.get("id"), "service": l.get("service", ""),
        "enabled": bool(l.get("enabled")), "heavy": bool(l.get("heavy")),
        "adopt": l.get("adopt", ""), "steps": len(l.get("steps") or []),
        "note": (l.get("_note") or "")[:note_chars],
        "file": _fragment_rel(sources.get(l.get("id"), "scenarios.json")),
    } for l in lifecycles]


@app.get("/planning/scenarios", response_class=HTMLResponse)
def planning_scenarios(request: Request, service: str = ""):
    return _render(request, "scenarios.html", "planning", plan_step="compose",
                   rows=_scenario_rows(service, note_chars=160), service=service)


@app.get("/planning/knowledge", include_in_schema=False)
def planning_knowledge_legacy():
    """Deduped (IA.md): knowledge has ONE home at /knowledge."""
    return RedirectResponse("/knowledge", status_code=301)


@app.get("/knowledge", response_class=HTMLResponse)
def planning_knowledge(request: Request):
    def listing(pattern: str) -> list[dict]:
        out = []
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file():
                rel = p.relative_to(ROOT).as_posix()
                out.append({"rel": rel, "kb": round(p.stat().st_size / 1024, 1)})
        return out
    return _render(request, "knowledge.html", "knowledge",
                   narrative=listing("knowledge/*.md"),
                   formal=listing("knowledge/formal/*.yaml")
                          + listing("knowledge/formal/*.md")
                          + listing("knowledge/formal/services/*.yaml"),
                   suite_files=listing("suites/*.yaml"),
                   env_files=listing("environments/*.yaml"))


def _file_listing(pattern: str) -> list[dict]:
    """Repo files matching pattern -> [{rel, kb}] (the knowledge-browser shape)."""
    out = []
    for p in sorted(ROOT.glob(pattern)):
        if p.is_file():
            out.append({"rel": p.relative_to(ROOT).as_posix(),
                        "kb": round(p.stat().st_size / 1024, 1)})
    return out


def _file_picker(request: Request, mode: str) -> HTMLResponse:
    """`?path=` 없이 /planning/edit·view에 오면 raw 422 JSON 대신 친절한 HTML
    선택기를 보여준다 (UIUX-AUDIT P2-12)."""
    groups = [
        ("suites/ — 스위트 정의", _file_listing("suites/*.yaml")),
        ("environments/ — 환경 프로파일", _file_listing("environments/*.yaml")),
        ("regression/scenarios/ — 시나리오·의존",
         _file_listing("regression/scenarios/*.json")
         + _file_listing("regression/scenarios/lifecycles/*.json")),
        ("knowledge/formal/ — 정형 지식",
         _file_listing("knowledge/formal/*.yaml")
         + _file_listing("knowledge/formal/*.md")),
    ]
    return _render(request, "file_picker.html", "planning",
                   mode=mode, groups=groups)


@app.get("/planning/view", response_class=HTMLResponse)
def planning_view(request: Request, path: str = ""):
    if not path.strip():
        return _file_picker(request, "view")
    f = _safe_repo_file(path)
    if not f:
        raise HTTPException(404, "file not found (or outside the browsable dirs)")
    try:
        content = f.read_text(errors="replace")
    except OSError:
        raise HTTPException(500, "unreadable")
    return _render(request, "file_view.html", "planning",
                   rel=path, content=content[:400_000],
                   editable=authoring.editable_path(path) is not None)


# --- Planning: 저작 편집기 (M3 §3.1 — 검증 → 쓰기 → 로컬 git 커밋) -----------------

@app.get("/planning/edit", response_class=HTMLResponse)
def planning_edit(request: Request, path: str = "", find: str = ""):
    if not path.strip():
        return _file_picker(request, "edit")
    f = authoring.editable_path(path)
    if not f or not f.is_file():
        raise HTTPException(404, "file not found (or outside the editable dirs)")
    if f.stat().st_size > 2_000_000:
        raise HTTPException(413, "file too large for the textarea editor")
    rel = f.relative_to(ROOT).as_posix()
    return _render(request, "editor.html", "planning",
                   rel=rel, content=f.read_text(errors="replace"),
                   find=find[:200], push=authoring.push_enabled())


@app.post("/planning/edit/validate", response_class=HTMLResponse)
def planning_edit_validate(request: Request, path: str = Form(...),
                           content: str = Form("")):
    result = authoring.propose_edit(path, content, validate_only=True)
    return templates.TemplateResponse(request, "_edit_result.html",
                                      {"result": result, "saved": False})


@app.post("/planning/edit/save", response_class=HTMLResponse)
def planning_edit_save(request: Request, path: str = Form(...),
                       content: str = Form("")):
    result = authoring.propose_edit(path, content)
    return templates.TemplateResponse(request, "_edit_result.html",
                                      {"result": result, "saved": result["ok"]})


# --- Planning: 의존 그래프 뷰 (M3 §2.3 — read-only, 편집은 원본 파일 편집기로) -------

def _cross_graph(cross: dict) -> dict:
    """cross-service.yaml resources -> layered boxes/arrows for an inline SVG
    (column = requires-depth; arrows point prerequisite -> dependent)."""
    res = {k: (v or {}) for k, v in (cross.get("resources") or {}).items()}
    memo: dict[str, int] = {}

    def depth(name: str, seen: tuple = ()) -> int:
        if name in memo:
            return memo[name]
        if name in seen:           # cycle — validator rejects it, degrade here
            return 0
        reqs = [r for r in res[name].get("requires") or [] if r in res]
        memo[name] = 0 if not reqs else 1 + max(
            depth(r, seen + (name,)) for r in reqs)
        return memo[name]

    cols: dict[int, list[str]] = {}
    for name in res:
        cols.setdefault(depth(name), []).append(name)
    BW, BH, XGAP, YGAP = 190, 38, 252, 50
    pos, nodes = {}, []
    for d in sorted(cols):
        for i, name in enumerate(sorted(cols[d],
                                        key=lambda n: (res[n].get("service", ""), n))):
            x, y = 12 + d * XGAP, 12 + i * YGAP
            pos[name] = (x, y)
            nodes.append({"id": name, "x": x, "y": y,
                          "service": res[name].get("service", ""),
                          "quota": res[name].get("quota", ""),
                          "provenance": res[name].get("provenance", ""),
                          "notes": (res[name].get("notes") or "")[:200]})
    edges = []
    for name, r in res.items():
        for req in r.get("requires") or []:
            if req in pos:
                (x1, y1), (x2, y2) = pos[req], pos[name]
                edges.append({"x1": x1 + BW, "y1": y1 + BH // 2,
                              "x2": x2, "y2": y2 + BH // 2})
    return {"nodes": nodes, "edges": edges, "bw": BW, "bh": BH,
            "w": 24 + (max(cols, default=0) + 1) * XGAP,
            "h": 24 + max((len(v) for v in cols.values()), default=1) * YGAP}


@app.get("/planning/dependencies", response_class=HTMLResponse)
def planning_dependencies(request: Request):
    import yaml
    deps, cross, load_errs = {}, {}, []
    try:
        deps = json.loads((ROOT / "regression" / "scenarios"
                           / "dependencies.json").read_text())
    except Exception as exc:
        load_errs.append(f"dependencies.json 읽기 실패: {exc}")
    try:
        cross = yaml.safe_load((ROOT / "knowledge" / "formal"
                                / "cross-service.yaml").read_text()) or {}
    except Exception as exc:
        load_errs.append(f"cross-service.yaml 읽기 실패: {exc}")
    try:
        from regression.scenarios.loader import load_lifecycles
        lifecycles = load_lifecycles()
    except Exception as exc:
        lifecycles, load_errs = [], load_errs + [f"lifecycle 로드 실패: {exc}"]
    sched = deps.get("vpc_schedule") or {}
    sim = authoring.vpc_peak(deps, lifecycles)
    vpc_paths = {str(p).split("?")[0].rstrip("/") for p, k
                 in (deps.get("budget_paths") or {"/v1/vpcs": "vpc"}).items()
                 if k == "vpc"}
    by_id = {l.get("id"): l for l in lifecycles}
    crud_rows = [{"id": lid, "creates": sum(
        1 for s in (by_id.get(lid, {}).get("steps") or [])
        if isinstance(s, dict) and str(s.get("method", "")).upper() == "POST"
        and str(s.get("path") or "").split("?")[0].rstrip("/") in vpc_paths
        and not s.get("adopt"))}
        for lid in sched.get("vpc_crud_lifecycles") or []]
    return _render(request, "dependencies.html", "planning",
                   plan_step="model",
                   load_errs=load_errs, sched=sched, sim=sim,
                   sim_warnings=authoring.vpc_quota_warnings(deps, lifecycles),
                   crud_rows=crud_rows,
                   fixed_ip_map={k: v for k, v in
                                 (sched.get("fixed_ip_map") or {}).items()
                                 if not k.startswith("_")},
                   quota_kinds=deps.get("quota_kinds") or {},
                   budget_paths=deps.get("budget_paths") or {},
                   graph=_cross_graph(cross),
                   cross_constraints=cross.get("cross_constraints") or [])


# --- Testing -------------------------------------------------------------------

def _run_preview_data() -> dict:
    """Per-suite/per-profile facts the RUN 조립 preview renders client-side.
    All numbers come from the real suite definitions + scenario catalog;
    durations are coarse buckets and labelled 대략치 in the UI."""
    stats = _scenario_stats()
    suites = {}
    for s in core_suites.list_suites():
        req = s.get("request") or {}
        mut, heavy = bool(req.get("mutations")), bool(req.get("heavy"))
        if not mut:                       # read-only probes sweep the catalog
            targets = stats["total"]
        elif heavy:
            targets = stats["enabled"]
        else:
            targets = stats["enabled"] - stats["enabled_heavy"]
        gates = [k for k in ("mutations", "destructive", "heavy", "conformance")
                 if req.get(k)]
        suites[s.get("id")] = {
            "label": s.get("label", ""), "targets": targets,
            "heavy": stats["enabled_heavy"] if heavy else 0,
            "gates": " + ".join(gates) if gates else "read-only",
            "eta": "~3–4시간" if heavy else ("~1시간" if mut else "~15–20분"),
        }
    profiles = {p.get("id"): {"label": p.get("label", ""),
                              "forbid": list(p.get("forbid") or [])}
                for p in core_profiles.list_profiles()}
    return {"suites": suites, "profiles": profiles}


@app.get("/testing", response_class=HTMLResponse)
def testing(request: Request, suite: str = "", service: str = "",
            profile: str = "", crud_filter: str = ""):
    runs = db.list_runs(limit=15)
    running, stale_running = _split_stale_running(runs)
    live = []
    for r in running:
        if r["gh_run_id"]:
            evs = db.list_events(r["gh_run_id"], kind="milestone", limit=50)
            live.append({"run": r, "milestones": evs})
    # prefill the trigger form from query hints — the static /platform console
    # deep-links here carrying the picked service/suite (read-plane → write-plane
    # hand-off, IA.md A). The actual dispatch stays a server-side POST so the
    # safety gates are never bypassed.
    prefill = {"suite": suite.strip(), "service": service.strip(),
               "profile": profile.strip(), "crud_filter": crud_filter.strip()}
    rv = _runs_view(runs)
    return _render(request, "testing.html", "testing",
                   runs=rv["runs"], fail_streak=rv["fail_streak"],
                   last_ok_when=rv["last_ok_when"],
                   live=live, stale_running=stale_running,
                   schedules=db.list_schedules(),
                   preview=_run_preview_data(), prefill=prefill)


@app.get("/testing/embed", response_class=HTMLResponse)
def testing_embed(request: Request):
    """Testing(③) inside the spine shell — base.html nav + console2 (absorbed) in an
    iframe with its own brand/nav suppressed (?embed=1). Keeps the 4-stage nav put."""
    return _render(request, "testing_embed.html", "testing")


@app.get("/partials/runs", response_class=HTMLResponse)
def runs_partial(request: Request, limit: int = 15):
    return templates.TemplateResponse(request, "_runs_table.html",
                                      _runs_view(db.list_runs(limit=limit)))


@app.post("/runs/trigger")
def trigger_run(suite: str = Form(""), profile: str = Form(""),
                service: str = Form(""), crud_filter: str = Form("")):
    ok, msg = dispatch.dispatch_run(suite, profile, service, crud_filter)
    # narrowing options ride in detail as KEY=VALUE lines: traceability in the
    # UI, and in worker mode the worker merges them over the suite expansion
    lines = [f"{k}={v}" for k, v in (("service", service),
                                     ("crud_filter", crud_filter)) if v]
    if not ok:
        lines.append(msg)
    db.create_run(suite, profile, trigger="manual", detail="\n".join(lines))
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules")
def add_schedule(cron: str = Form(...), suite: str = Form(...),
                 profile: str = Form(""), note: str = Form("")):
    from croniter import croniter
    if not croniter.is_valid(cron):
        raise HTTPException(400, f"invalid cron expression: {cron!r}")
    # 스케줄 = 이 서버 LIVE 무인 실행 (D5 후속, 2026-07-11) — heavy(과금) suite 는
    # 등록 단계에서 차단 (Hard Rule 1: heavy 는 pre-flight 수동 opt-in).
    # 발화 시점에도 launch_suite_run 이 이중으로 거부한다 (yaml 이 바뀌어도 안전).
    from core import suites as core_suites
    s = next((x for x in core_suites.list_suites() if x.get("id") == suite), None)
    if s and (s.get("request") or {}).get("heavy"):
        raise HTTPException(400, (
            f"suite {suite!r} 는 heavy(과금) — 스케줄 무인 실행이 금지됩니다. "
            "heavy 는 Testing 콘솔의 pre-flight 에서 수동으로 실행하세요."))
    db.add_schedule(cron, suite, profile, note)
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules/{schedule_id}/toggle")
def schedule_toggle(schedule_id: int):
    db.toggle_schedule(schedule_id)
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules/{schedule_id}/delete")
def schedule_delete(schedule_id: int):
    db.delete_schedule(schedule_id)
    return RedirectResponse("/testing", status_code=303)


# --- Testing: 잔존 자원 단일 정본 (실측 owned 스캔) + ingest 이력 + 단일 삭제 (M2 §2.5)

def _cleanup_busy() -> tuple[bool, str]:
    """강제 클린업의 409 조건(실행/대기 중인 run 존재)을 클릭 '전에' 계산 — 화면이
    버튼을 비활성화+사유 표기할 수 있도록. console2 엔진의 admission 상태가 출처."""
    try:
        from tools import console2_server as c2
        with c2._ADMIT:
            n_run, n_q = len(c2._RESERVED), len(c2._QUEUE)
        if n_run or n_q:
            return True, (f"진행 중 {n_run}건 · 대기 {n_q}건 — reconciler 는 owner-tag "
                          "전체를 reap 하므로 모든 실행이 끝난 뒤에만 가능합니다.")
    except Exception:
        pass
    return False, ""


def _owned_ctx() -> dict:
    st = resources.owned_state()
    busy, busy_reason = _cleanup_busy()
    return {"owned": st, "owned_age": resources._age_label(st["age_s"]),
            "busy": busy, "busy_reason": busy_reason,
            "destructive": resources.destructive_enabled()}


@app.get("/testing/resources", response_class=HTMLResponse)
def testing_resources(request: Request, gh_run_id: str = "", msg: str = ""):
    rows = resources.inventory(gh_run_id or None)
    run_ids = [r["gh_run_id"] for r in db.list_runs(limit=50) if r["gh_run_id"]]
    return _render(request, "resources.html", "testing",
                   rows=rows, gh_run_id=gh_run_id, msg=msg[:300],
                   live_count=sum(1 for r in rows if r["live"]),
                   run_ids=run_ids, **_owned_ctx())


@app.get("/testing/resources/owned", response_class=HTMLResponse)
def testing_resources_owned(request: Request):
    """실측(owned 스캔) 섹션 fragment — 스캔 중일 때 htmx 가 폴링한다."""
    return templates.TemplateResponse(request, "_owned_section.html", _owned_ctx())


@app.post("/testing/resources/scan")
def testing_resources_scan():
    """owned 스캔(read-only LIST 인벤토리)을 백그라운드로 시작하고 돌아온다."""
    resources.start_owned_scan()
    return RedirectResponse("/testing/resources", status_code=303)


@app.post("/testing/resources/cleanup")
def testing_resources_cleanup():
    """계정 전체 강제 클린업 (reconciler FORCE sweep) — console2 의 /api/cleanup 과
    같은 엔진·같은 409 가드. 버튼 앞의 pre-scan 모달이 삭제 대상 목록을 보여준 뒤
    호출된다."""
    busy, reason = _cleanup_busy()
    if busy:
        msg = "강제 클린업 차단 — " + reason
    else:
        try:
            from tools import console2_server as c2
            rec = c2._start("cleanup", c2._cleanup_worker)
            msg = (f"강제 클린업 시작 (run {rec['id']}) — 진행 로그는 Testing 콘솔 "
                   "실행 기록에서, 끝나면 [다시 스캔]으로 확인하세요.")
        except Exception as exc:
            msg = f"강제 클린업 시작 실패: {exc}"
    q = urlencode({"msg": msg})
    return RedirectResponse(f"/testing/resources?{q}", status_code=303)


@app.post("/testing/resources/delete")
def testing_resource_delete(gh_run_id: str = Form(""), service: str = Form(""),
                            kind: str = Form(""), res_id: str = Form(...),
                            name: str = Form(""), lifecycle: str = Form(""),
                            filter_run: str = Form("")):
    if not resources.destructive_enabled():
        msg = ("SCP_ALLOW_DESTRUCTIVE=true 미설정 — 삭제가 차단되었습니다 "
               "(서버 환경변수로 활성화 후 재시도).")
    else:
        ok, msg = resources.delete_resource(service, kind, res_id, name=name)
        # the attempt itself is part of the run's resource history
        resources.record_attempt(gh_run_id, service=service, kind=kind,
                                 res_id=res_id, name=name, lifecycle=lifecycle,
                                 ok=ok, message=msg)
        msg = f"{kind} {name or res_id}: {msg}"
    q = urlencode({"gh_run_id": filter_run, "msg": msg})
    return RedirectResponse(f"/testing/resources?{q}", status_code=303)


# --- 개입 명령 (M2 명령 채널 — UI가 쌓고 엔진이 폴링/ack) ---------------------------

COMMAND_ACTIONS = ("abort_run", "skip_scenario", "stop_polling")


@app.post("/runs/{gh_run_id}/commands")
def add_run_command(gh_run_id: str, action: str = Form(...), target: str = Form("")):
    if action not in COMMAND_ACTIONS:
        raise HTTPException(400, f"unknown command action {action!r}")
    if action == "skip_scenario" and not target.strip():
        raise HTTPException(400, "skip_scenario 명령에는 lifecycle id(target)가 필요합니다")
    db.add_command(gh_run_id, action, target.strip())
    return RedirectResponse(f"/runs/{gh_run_id}", status_code=303)


# --- Reporting -----------------------------------------------------------------

# tab keys only — labels live in the single subtab include
# (templates/_reporting_tabs.html, P2-8; coverage/compare are sibling routes).
REPORT_TABS = ("summary", "dashboard", "runs", "triage")


@app.get("/reporting", response_class=HTMLResponse)
def reporting(request: Request, tab: str = "summary"):
    if tab not in REPORT_TABS:
        tab = "summary"
    # Reporting 개선 A (계약 §2.5): 트리아지 탭에 "신규 fail 상세 목록" + known
    # 목록 — 발행 fail_new.json(정공법) 우선, 미발행 동안 index.html 배너 파싱
    # 폴백. 실패는 None → 해당 섹션만 empty-state.
    triage_detail = triage_known = None
    if tab == "triage":
        from controlplane import results_data
        triage_detail = results_data.get_new_regressions()
        triage_known = results_data.get_known_issues()
    runs = db.list_runs(limit=100)
    archive = snapshots.archive_index(limit=100)
    # Reporting 개선 C (계약 §2.1 병합 런 타임라인): RUN 히스토리(control plane)
    # ∪ 아카이브(oplog 버킷 — 구형 CI 런 포함)를 gh_run_id 로 dedupe 해 시간순
    # 단일 목록으로. "어제 런"의 단일한 답이 없던 문제(페르소나 P2 막힘) 해소.
    seen = {str(r["gh_run_id"]) for r in runs if r["gh_run_id"]}
    merged = [{"gh_run_id": r["gh_run_id"], "id": r["id"], "suite": r["suite"],
               "profile": r["profile"], "trigger": r["trigger"],
               "status": r["status"], "requested_at": r["requested_at"],
               "finished_at": r["finished_at"], "sha": "",
               "archive_only": False} for r in runs]
    for a in archive:
        rid = str(a.get("run_id") or "")
        if not rid or rid in seen:
            continue
        merged.append({"gh_run_id": rid, "id": None, "suite": "", "profile": "",
                       "trigger": "", "status": "done",
                       "requested_at": "", "finished_at": a.get("finished") or "",
                       "sha": a.get("sha") or "", "archive_only": True})
    merged.sort(key=lambda r: str(r["finished_at"] or r["requested_at"] or ""),
                reverse=True)
    return _render(request, "reporting.html", "reporting",
                   tab=tab,
                   coverage=dashdata.latest_coverage(),
                   triage_detail=triage_detail, triage_known=triage_known,
                   runs=runs, merged_runs=merged,
                   archive=archive)


@app.get("/dashboard/{rel_path:path}")
def current_dashboard(rel_path: str = ""):
    """The CURRENT published dashboard (dashboard-data branch) — coverage
    included — served inside the platform so all screens live in one place."""
    got = dashdata.file(rel_path or "index.html")
    if not got:
        raise HTTPException(404, "dashboard-data branch unavailable (or no such file)")
    body, ctype = got
    return Response(content=body, media_type=ctype)


@app.get("/reporting/compare", response_class=HTMLResponse)
def reporting_compare(request: Request, a: str = "", b: str = ""):
    """Run A vs B diff (M2 §2.6) — joins both snapshots' observations."""
    run_ids = [r["gh_run_id"] for r in db.list_runs(limit=100) if r["gh_run_id"]]
    for row in snapshots.archive_index(limit=100):
        rid = str(row.get("run_id", ""))
        if rid and rid not in run_ids:
            run_ids.append(rid)
    result = a_missing = b_missing = None
    if a and b:
        a_obs = snapshots.observations(a)
        b_obs = snapshots.observations(b)
        a_missing, b_missing = not a_obs, not b_obs
        result = compare.diff(a_obs, b_obs)
    return _render(request, "compare.html", "reporting",
                   a=a, b=b, run_ids=run_ids, result=result,
                   a_missing=a_missing, b_missing=b_missing)


@app.get("/runs/{gh_run_id}", response_class=HTMLResponse)
def run_detail(request: Request, gh_run_id: str):
    run = db.get_run(gh_run_id)
    tri = db.get_triage(gh_run_id)
    tri_detail = None
    if tri and tri["detail"]:
        try:
            tri_detail = json.loads(tri["detail"])
        except ValueError:
            pass
    meta = snapshots.meta(gh_run_id)
    milestones = db.list_events(gh_run_id, kind="milestone")
    commands = db.list_commands(gh_run_id)
    # 어떤 근거도 없는 id는 404 (P2-12) — 아카이브에만 있는 과거 run은 스냅샷/
    # index 근거가 있으므로 계속 200 + 내용으로 렌더된다.
    if not (run or meta or milestones or commands or tri or any(
            str(row.get("run_id", "")) == gh_run_id
            for row in snapshots.archive_index(limit=500))):
        return templates.TemplateResponse(
            request, "run_notfound.html",
            {**common.base_ctx("reporting"), "gh_run_id": gh_run_id},
            status_code=404)
    # local (console2) runs: pass/fail summary from the run's events file
    # (P2-9 잔여 — lifecycle pass/fail + api ok/soft/fail on the detail page).
    local_summary = (console_api.local_run_summary(gh_run_id)
                     if gh_run_id.startswith("local-") else None)
    return _render(request, "run_detail.html", "reporting",
                   gh_run_id=gh_run_id, run=run, meta=meta,
                   milestones=milestones, commands=commands,
                   triage=tri, triage_detail=tri_detail,
                   local_summary=local_summary)


@app.post("/runs/{gh_run_id}/triage", response_class=HTMLResponse)
def trigger_triage(request: Request, gh_run_id: str):
    if not triage.enabled():
        raise HTTPException(400, "triage disabled — set ANTHROPIC_API_KEY")
    try:
        triage.run_triage(gh_run_id)
    except Exception as exc:
        raise HTTPException(502, f"triage failed: {exc}")
    return RedirectResponse(f"/runs/{gh_run_id}", status_code=303)


# --- snapshot serving (per-run dashboard restore) ------------------------------

@app.get("/runs/{gh_run_id}/snapshot/{rel_path:path}")
def snapshot_file(gh_run_id: str, rel_path: str):
    got = snapshots.fetch(gh_run_id, rel_path)
    if not got:
        raise HTTPException(404, "snapshot file not found (or bucket unconfigured)")
    body, ctype = got
    return Response(content=body, media_type=ctype)


# --- engine-facing API (oplog 미러 ingest + M2 명령 채널) ------------------------

def _require_ingest_token(request: Request) -> None:
    """Shared bearer check — PLATFORM_INGEST_TOKEN guards every engine-facing
    endpoint (ingest / command poll / ack) with the same token."""
    token = os.environ.get("PLATFORM_INGEST_TOKEN", "").strip()
    if token:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            raise HTTPException(401, "bad ingest token")


@app.get("/api/runs/{gh_run_id}/commands")
def api_pending_commands(request: Request, gh_run_id: str):
    """Pending (un-acked) intervention commands — the engine polls this at
    step boundaries (PLATFORM-PLAN §2.5 명령 채널). LOCAL console runs (embed
    mode — the run worker lives in THIS process via console_api → console2_server)
    keep their queue in console2_server._COMMANDS; merge it so one URL serves
    both CI(DB) and local runs (owner 2026-07-09 per-lifecycle 중단)."""
    _require_ingest_token(request)
    from tools import console2_server as _c2
    local = _c2.local_pending_commands(gh_run_id)
    return {"commands": local + [
        {"id": c["id"], "action": c["action"], "target": c["target"]}
        for c in db.pending_commands(gh_run_id)]}


@app.post("/api/commands/{command_id}/ack")
def api_ack_command(request: Request, command_id: int):
    _require_ingest_token(request)
    from tools import console2_server as _c2
    if _c2.local_ack_command(command_id):   # local ids ride a 1e9 offset
        return {"ok": True}
    if not db.ack_command(command_id):
        raise HTTPException(404, "no such command")
    return {"ok": True}


@app.post("/api/ingest/events")
async def ingest(request: Request):
    _require_ingest_token(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    kind = payload.get("kind", "")
    gh_run_id = str(payload.get("run_id", "")) or "unknown"
    db.attach_run(gh_run_id)
    if kind == "milestone":
        stage, status = payload.get("stage", ""), payload.get("status", "")
        db.insert_event(gh_run_id, "milestone", payload.get("ts", db.now()),
                        payload.get("job", ""), stage, status,
                        payload.get("detail", ""))
        db.apply_milestone(gh_run_id, stage, status, payload.get("detail", ""))
        if stage == "dashboard":
            triage.auto_triage(gh_run_id)
    elif kind == "resources":
        for ev in payload.get("events", [])[:500]:
            db.insert_event(
                gh_run_id, "resource", ev.get("ts", db.now()),
                stage=ev.get("action", ""), status=ev.get("status", ""),
                detail=json.dumps(ev, ensure_ascii=False))
    else:
        raise HTTPException(400, f"unknown kind {kind!r}")
    return {"ok": True}


# --- local executor (S2) — run a SELECTION's simulate in-process and stream the
# fine console-events for the live DAG view (no CI dispatch, no cloud). Selection is
# lifecycle ids (console2's model), distinct from the suite-based dispatch above.
@app.post("/api/local/run")
async def api_local_run(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    ids = payload.get("lifecycle_ids") or []
    if not ids:
        raise HTTPException(400, "lifecycle_ids required")
    mode = (payload.get("mode") or "simulate").lower()
    if mode == "live":
        # LIVE = real cloud calls. Gates are explicit opt-ins; heavy defaults OFF.
        rec = local_executor.start_live(
            ids, mutations=bool(payload.get("mutations", True)),
            destructive=bool(payload.get("destructive", True)),
            heavy=bool(payload.get("heavy", False)))
    else:
        try:
            step_delay = float(payload.get("step_delay", 0) or 0)
        except (TypeError, ValueError):
            step_delay = 0.0
        rec = local_executor.start_simulate(ids, step_delay=step_delay)
    # surface in the run list too (gh_run_id = the local run id)
    db.create_run(f"(local {mode})", "", trigger="local", gh_run_id=rec["id"],
                  detail="lifecycle_ids=" + ",".join(map(str, ids)))
    return {"ok": True, "run": rec}


@app.get("/api/local/runs")
def api_local_runs():
    return {"runs": local_executor.list_runs()}


@app.get("/api/local/runs/{run_id}/events")
def api_local_events(run_id: str):
    res = local_executor.read_events(run_id)
    if res is None:
        raise HTTPException(404, "no such local run")
    return res


@app.get("/api/local/lifecycles")
def api_local_lifecycles():
    """Runnable lifecycles (id · service · heavy) for the Local Run picker."""
    from regression.scenarios.loader import load_lifecycles
    lcs, _ = load_lifecycles(with_sources=True)
    out = [{"id": lc["id"], "service": lc.get("service", ""), "heavy": bool(lc.get("heavy"))}
           for lc in lcs if lc.get("enabled")]
    out.sort(key=lambda r: (r["service"], r["id"]))
    return {"lifecycles": out}


@app.get("/api/local/plan")
def api_local_plan(lifecycle_ids: str = ""):
    """The dag_planner plan (waves = creation-order levels) for a selection, so the
    Local Run screen can draw the level DAG and color it by live run state."""
    from regression.scenarios import local_run
    ids = [s for s in lifecycle_ids.split(",") if s]
    if not ids:
        return {"waves": [], "runnable": [], "leaf_set": []}
    return local_run.build_plan(ids)


@app.get("/resource_graph.js")
def resource_graph_js():
    """Serve the scene() renderer (ported from console2; light theme = controlplane)."""
    return Response((HERE / "static" / "resource_graph.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


@app.get("/api/local/graph")
def api_local_graph(lifecycle_ids: str = ""):
    """The composition DAG (composer.graph_view) for a lifecycle selection + a
    node->lifecycle map, so the live run state can color the resource nodes."""
    from regression.scenarios import composer
    ids = {s for s in lifecycle_ids.split(",") if s}
    empty = {"nodes": [], "edges": [], "levels": [0], "node_lifecycle": {}}
    if not ids:
        return empty
    model = composer.load_model()
    targets = sorted(nid for nid, task in model.items()
                     if ((task.get("source") or {}).get("lifecycle")) in ids)
    if not targets:
        return empty
    g = composer.graph_view(targets, model=model)
    g["node_lifecycle"] = {
        n["id"]: (((model.get(n["id"]) or {}).get("source") or {}).get("lifecycle") or "")
        for n in g["nodes"]}
    return g


@app.post("/api/local/cleanup")
def api_local_cleanup():
    """FORCE account-wide reconciler sweep (destructive — owner-tagged only)."""
    return {"ok": True, "run": local_executor.start_cleanup()}


@app.post("/api/local/verify")
def api_local_verify():
    """Read-only owned-resource inventory (no deletes)."""
    return {"ok": True, "run": local_executor.start_verify()}


@app.get("/api/local/runs/{run_id}/log")
def api_local_log(run_id: str):
    res = local_executor.read_log(run_id)
    if res is None:
        raise HTTPException(404, "no such local run")
    return res


@app.get("/local-run", include_in_schema=False)
def local_run_page():
    """RETIRED (고아 페이지 — UIUX-AUDIT P1-4): 러너 UI는 Testing 콘솔(/testing/embed)
    이, 계정 위생 역할은 /testing/resources(실측 owned 스캔 단일 정본)가 대체.
    /api/local/* 는 콘솔이 계속 쓰므로 유지."""
    return RedirectResponse("/testing/resources", status_code=301)
