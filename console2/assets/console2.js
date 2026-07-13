/* console2 — single-page execution console (LIGHT theme).
 * IA (locked): ① 구성 — a COMPACT collapsible category→service MENU-TREE on the
 * LEFT (click a service row = select whole service; "리소스…" → modal for specific
 * resources) driving a LARGE LIVE composition DAG on the RIGHT (composer.graph_view
 * via /api/graph) + a 생성/검증/삭제 순서표. ② 실행 & 리포트 — the run (simulate | live)
 * + event-driven report (흐름 진행 / 자원 / API / 로그); the run screen leads with a
 * 남은 자원(잔존) pre-flight panel. The ① selection is carried into the launch.
 *
 * Vocabulary (locked concept model): category → service → resource → api.
 *   selection (resource/service) pulls its dependency CLOSURE (auto-ordered) ·
 *   execution unit = lifecycle · reporting unit = api (a lifecycle step). */
(function () {
"use strict";

// ---- tiny DOM helpers ----
const $ = id => document.getElementById(id);
const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const els = (q, r) => [...(r || document).querySelectorAll(q)];

// ---- global state ----
let MODEL = null;           // raw /api/model payload
let N = {};                 // MODEL.nodes (by id)
let targets = new Set();    // selected resource node ids (the targets)
let screen = "build";       // build | run
let lastGraph = null;       // last /api/graph response (composition DAG)
let graphTimer = null;      // debounce for /api/graph
let modalSvc = null;        // service short id whose resource modal is open
let defSvc = null;          // service whose 📖 definition viewer is open (read-only)
let collapsed = null;       // Set of collapsed category names (menu-tree). null = not yet initialised
let ownedScan = null;       // last /api/owned result {status, owned, owned_total} for the run-screen panel
// 마지막 완료 스캔을 sessionStorage 에 보존 — run 시작/iframe 재로드가 패널을
// "아직 확인하지 않음" 으로 리셋하지 않게 결과+시각을 유지한다 (신규8 후속).
const OWNED_KEY = "c2.ownedScan.v1";
try {
  const savedScan = JSON.parse(sessionStorage.getItem(OWNED_KEY) || "null");
  if (savedScan && savedScan.status === "done") ownedScan = savedScan;
} catch (e) { /* corrupt/absent → null */ }

// ---- 실행 대기열 (client-side STAGED queue) -------------------------------------
// ① 구성 ▶ ENQUEUES a snapshot of the current selection (it no longer runs). The
// actual run is a deliberate, budget-informed [▶ 실행] on the ② 실행 screen, where
// each staged item shows 필요 VPC vs the live 여유(headroom) before you commit. A
// staged item is a plain snapshot {id, selection, nServices, nResources, peak_vpcs,
// heavy, closure} captured from /api/plan + the current selection at stage time.
let STAGED = [];            // queued (not-yet-run) selection snapshots
let stagedOpen = null;      // id of the staged item whose detail is expanded (1 at a time)

// run/report state
let runId = null;
let runEvents = [];
let runStatus = "idle";
let runSelIds = [];         // 이 run의 전체 선택 (rec.lifecycle_ids) — 대기 중 표시용
let pollTimer = null;
let lastLogText = null;     // last log text written to the 로그 <pre> (in-place diff → no flicker)
let r4LogTimer = null;      // dedicated slow (2s) log poller while running (detail 로그 tab)
let expandedApi = null;     // key of the currently-expanded API row (detail API tab)
let hideDupSoft = true;     // §5: 중복-soft(다른 곳에서 이미 2xx 검증된 것) 행은 기본 접힘
// 성능 수리 + UX (2026-07-11 오너 제보 "soft 건수 클릭 시 멈춤"): kpi 타일이
// 클릭돼도 아무 동작이 없었다 → 결과 필터로 승격. 대형 런 표는 행 상한.
let apiCatFilter = "all";   // API 탭 결과 필터 — all|ok|soft|fail (kpi 타일 클릭)
let apiShowAll = false;     // 행 상한(API_ROW_CAP) 해제 여부 — 스코프 전환 시 리셋
const API_ROW_CAP = 500;    // 이 이상은 최신순으로 자르고 '전체 표시'로 해제

// ---- run 뷰 바인딩 (F1·F2): the master 흐름 graph binds to the RUN, not to the
// 구성 selection. runGraph = /api/runs/<id>/graph (the run's lifecycle closure,
// same composer.graph_view + resource_graph.js contract); graphMode toggles
// between "run 뷰" and "구성 미리보기" (manual chip; default = run when bound).
let runGraph = null;        // the loaded run's own composition DAG
let runGraphFor = null;     // runId the runGraph belongs to
let graphMode = "run";      // "run" (run 뷰) | "build" (구성 미리보기)
const endToastShown = {};   // runId -> true once the run-end toast fired
const lateAlertSeen = {};   // runId -> true once its 늦출현 alert was surfaced
let cleanupJustRan = false; // a force-cleanup finished → post-rescan hint (신규7)
let graphPending = false;   // /api/graph in flight → '포함 API' shows a spinner

// ---- B1 master→detail report state ----------------------------------------
// 흐름 is the persistent MASTER (the B2 scene). The DETAIL pane is scoped to ONE
// lifecycle (detailScope = its id) or to the cross-run aggregate (detailScope="*").
// Node-click on the master = B2 focus + open that lifecycle's detail (reconciled:
// the focus gesture IS the drill). The detail sub-tabs (자원·API·로그) mean
// "…for this scope". State persists across polls so a live refresh never loses the
// user's selected lifecycle / open API row / sub-tab.
let detailScope = "*";      // "*" = 전체 (aggregate) · else a lifecycle id
let detailTab = "res";      // res | api | log
let scopeAuto = true;       // true until the user explicitly picks a scope (so a
                            //   single-lifecycle run can auto-select without fighting them)

// DAG-at-scale (B2) scene controllers — one for the 구성 composition DAG, one for
// the 흐름 live-run DAG. Both drive the SAME graph object via ResourceGraph.scene
// (group/collapse · focus · zoom). buildView toggles 그림|표 on ①.
let dagScene = null;        // 구성 (#dag-svg) scene
let r1Scene = null;         // DAG 씬 — 이제 온디맨드 팝업(#dag-modal) 안에서만 산다
let dagOpen = false;        // 🕸 의존 그래프 팝업 열림 상태 (owner 2026-07-08)
let stagedScene = null;     // 실행 대기열 미리보기 (#sp-svg) — DAG of the OPEN staged item
let buildView = "fig";      // 그림 | 표 (구성 DAG mode)
let dagFocus = null;        // current focus info on the 구성 DAG (for 표 scoping)

// ---- bootstrap: fetch the model, then render ----
fetch("/api/model").then(r => r.json()).then(m => {
  if (m.error) throw new Error(m.error);
  MODEL = m; N = m.nodes;
  init();
}).catch(e => fatal("백엔드 연결 실패 — <code>python tools/console2_server.py</code> 실행 중인가요? (" + esc(e.message) + ")"));

function fatal(html) {
  $("ctxbar").innerHTML = '<span class="seg" style="color:var(--red)">● ' + html + "</span>";
}

function init() {
  // 기본 선택 = 비어 있음 (신규6 — pre-selected vpc+subnet was noise): restore
  // the user's own last selection from sessionStorage (iframe reload / tab
  // navigation safe); a ?service= deep-link still overrides.
  restoreSelection();
  deepLinkService();
  wireNav();
  wireModal();
  wireDefModal();
  wireLaunch();
  wireSuites();
  wireReportDelegation();   // P2C-24: rail/scopebar/detail/now-playing 위임 1회
  go("build");
  reattachActiveRun();      // page load with an active run → auto-rebind to it
}

// ---- selection persistence across iframe reloads (sessionStorage) ----------
const SEL_KEY = "c2.selection.v1";
function restoreSelection() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SEL_KEY) || "[]");
    if (Array.isArray(saved)) saved.forEach(id => { if (N[id] && N[id].lifecycle) targets.add(id); });
  } catch (e) { /* corrupt/absent → empty default */ }
}
function persistSelection() {
  try { sessionStorage.setItem(SEL_KEY, JSON.stringify([...targets])); } catch (e) { /* quota/file:// */ }
}

// ---- auto-reattach (A·2): on load, if a run is active (running/queued) bind the
// console to it — graph(run 뷰) + 진행 리스트 + 상세 + 로그 — instead of an idle
// screen that pretends nothing is happening. Static demo / fetch error → no-op.
function reattachActiveRun() {
  fetch("/api/runs").then(r => r.json()).then(j => {
    const act = (j.runs || []).find(r => r.status === "running" || r.status === "queued");
    if (!act || runId) return;
    runId = act.id; runEvents = []; evOffset = 0; runStatus = act.status || "running";
    detailScope = "*"; detailTab = "res"; scopeAuto = true; expandedApi = null; apiCatFilter = "all"; apiShowAll = false;
    graphMode = "run"; ensureRunGraph();
    go("run");
    pollEvents();
  }).catch(() => { /* static demo or server unreachable — stay idle */ });
}

// fetch the run's OWN graph (lifecycle closure) once per run — the 흐름 master
// scene binds to THIS in run 모드 (F1: it used to render the 구성 selection).
function ensureRunGraph() {
  if (!runId || runGraphFor === runId) return;
  const id = runId;
  runGraphFor = id; runGraph = null;
  fetch("/api/runs/" + id + "/graph").then(r => r.json()).then(g => {
    if (runId !== id) return;
    if (g.error || !g.nodes) { runGraphFor = null; return; }  // allow retry (L1)
    runGraph = g;
    if (screen === "run") drawReport();
  }).catch(() => { runGraphFor = null; /* keep 구성 preview; retry next draw (L1) */ });
}

// ---- ?service=<cat>/<svc> deep-link (from the dashboard's per-service links) ----
// If present and resolvable to a selectable service, REPLACE the default selection
// with that whole service so the dashboard "Platform →" links land here focused on
// it. Accepts the full slug ("networking/vpc") or a bare short name ("vpc"); a miss
// is silent (keeps the default). Returns true iff it changed the selection.
function deepLinkService() {
  let q;
  try { q = new URLSearchParams(location.search).get("service"); } catch (e) { return false; }
  if (!q) return false;
  q = q.trim().toLowerCase();
  const slugs = [...new Set(Object.values(N).map(n => n.service).filter(Boolean))];
  const svc = slugs.find(s => s.toLowerCase() === q)
           || slugs.find(s => s.toLowerCase().split("/").pop() === q.split("/").pop());
  if (!svc || !svcSelectable(svc).length) return false;
  targets.clear();
  setSvc(svc, true);
  return true;
}

// ---- a resource node is standalone-selectable iff it maps to a lifecycle.
// (lookup / pure-dep resources have lifecycle=null → never selectable; they
// still appear on the composition DAG when pulled in as a dependency.) ----
const hasLifecycle = id => !!(N[id] && N[id].lifecycle);
const svcNodes = svc => Object.keys(N).filter(id => N[id].service === svc);          // all nodes of a service
const svcSelectable = svc => svcNodes(svc).filter(hasLifecycle);                       // its lifecycle-bearing nodes
const shortName = svc => svc.split("/").pop();
// English category display label (shared with the DAG via ResourceGraph.catLabel);
// the raw slug stays the data key, only the shown name is localized to English.
const catName = c => (window.ResourceGraph && window.ResourceGraph.catLabel
  ? window.ResourceGraph.catLabel(c) : c);

// resource KIND from a delete/create path: the collection segment right after the
// version (e.g. /v1/vpcs/{id} → vpc, /v1/subnets/... → subnet, /v1/nat-gateways →
// nat-gateway), singularized by dropping a trailing 's' (kept for words ending in
// a non-pluralizing 's'-pair is overkill here — collection names are simple). The
// version segment (v1, v2025-…) and any {template}/concrete-id segments are
// skipped. Returns null when no collection segment can be found (caller falls back
// to the service name).
function kindFromPath(path) {
  const segs = (path || "").split("?")[0].split("/").filter(Boolean);
  // first segment that is NOT a version (v1, v2, v1.1, v2025-01-01) → the collection
  const isVer = s => /^v\d/.test(s);
  let coll = null;
  for (const s of segs) {
    if (isVer(s)) continue;
    coll = s; break;                 // the collection comes right after the version
  }
  if (!coll) return null;
  // singularize: vpcs→vpc, subnets→subnet, ports→port, nat-gateways→nat-gateway.
  // only strip a trailing 's' (not 'ss'); leave already-singular names alone.
  if (coll.length > 1 && coll.endsWith("s") && !coll.endsWith("ss")) coll = coll.slice(0, -1);
  return coll;
}

// build category → [services] (sorted), services that have ≥1 node
function categoryMap() {
  const byCat = {};
  Object.keys(N).forEach(id => {
    const n = N[id];
    if (!n.service) return;
    (byCat[n.category] = byCat[n.category] || new Set()).add(n.service);
  });
  const out = {};
  Object.keys(byCat).sort().forEach(c => { out[c] = [...byCat[c]].sort(); });
  return out;
}

// ================= top nav (① 구성 / ② 실행 & 리포트) =================
function wireNav() {
  els("#screenToggle button").forEach(b => b.onclick = () => go(b.dataset.scr));
  // detail sub-tabs (자원·API·로그) — switch the DETAIL pane's tab; the master 흐름
  // scene is persistent and untouched by a tab switch.
  els("#detail-subtabs button").forEach(b => b.onclick = () => { setDetailTab(b.dataset.d); });
  // 🌐 런타임 뷰 (새 창) — open the current live-resource topology in a separate
  // popup. Static demo (no backend) → baked snapshot; live server → the dynamic
  // endpoint. The INLINE face of the same page is the ② detail '런타임' tab.
  const rl = $("runtimeLink");
  if (rl) rl.onclick = (e) => {
    e.preventDefault();
    window.open(runtimeUrl(), "scp-runtime", "width=1320,height=900,scrollbars=yes,resizable=yes");
  };
  // 실행 기록 fold — 기본 접힘 (CX 재배치: 과거 히스토리가 현재 실행을 가리지 않게)
  const ht = $("hist-toggle");
  if (ht) ht.onclick = () => setHistOpen(!histOpen);
  syncHistFold();
  // master strip fold (P2C-22) — 기본 열림 (배너+①→②칩을 접어 rail/상세에 세로 양보)
  const mf = $("master-fold");
  if (mf) mf.onclick = () => setMasterOpen(!masterOpen);
  syncMasterFold();
}

// the ONE runtime-view URL (single source — popup 링크와 인라인 iframe 이 공유).
// scope=mine 기본 · 페이지 자체가 주기 자동 갱신(6fa9ec12)을 갖고 있다.
function runtimeUrl() {
  return window.__C2_STATIC__ ? "runtime.html" : "/runtime?scope=mine";
}

// ---- 실행 기록 접힘 (CX 재배치 2026-07-07) --------------------------------------
// 과거 히스토리는 기본 접힘 — 항상 노출되는 것은 토글 헤더 + (실행이 없을 때)
// 최근 종료 1건 요약 행뿐. 펼침 상태는 sessionStorage 유지.
let histOpen = false;
try { histOpen = sessionStorage.getItem("c2.histOpen.v1") === "1"; } catch (e) { /* private mode */ }
function setHistOpen(v) {
  histOpen = !!v;
  try { sessionStorage.setItem("c2.histOpen.v1", histOpen ? "1" : "0"); } catch (e) { /* ignore */ }
  syncHistFold();
}
function syncHistFold() {
  const body = $("report-side");
  if (body) body.classList.toggle("hidden", !histOpen);
  const car = $("hist-car");
  if (car) car.textContent = histOpen ? "▾" : "▸";
}

// ---- master strip 접힘 (P2C-22 2026-07-09) ----------------------------------
// 전폭 master(배너 + ①→② 연속성 칩)를 접어 rail/상세에 세로 공간을 양보한다.
// 기본 열림 — 계획↔실행 연속성 칩은 상시성이 원칙(P2C-19), 접힘은 명시적 선택만.
let masterOpen = true;
try { masterOpen = sessionStorage.getItem("c2.masterOpen.v1") !== "0"; } catch (e) { /* private mode */ }
function setMasterOpen(v) {
  masterOpen = !!v;
  try { sessionStorage.setItem("c2.masterOpen.v1", masterOpen ? "1" : "0"); } catch (e) { /* ignore */ }
  syncMasterFold();
}
function syncMasterFold() {
  const body = $("report-main");
  if (body) body.classList.toggle("hidden", !masterOpen);
  const car = $("master-fold");
  if (car) car.textContent = masterOpen ? "▾" : "▸";
}
// ================= P2C-24 (owner 2026-07-09): 폴링 다이어트 + 무깜빡 렌더 ======
// 오너 실측: 초당 /api/runs 2-3회 + events 2회 + capacity 1회 폭주, 라이브 중
// rail/detail 전체 innerHTML 재빌드로 깜빡임·클릭 유실. 처방: (1) 이벤트 폴을
// 단일 tick 2s + 증분(?offset=)으로, capacity 30s(대기열 있으면 5s), /api/runs 는
// 시작/종료/종료 후 감시로만, 숨은 탭은 정지. (2) 렌더는 키 기반 in-place patch
// + 정적 컨테이너 위임 클릭 — 바뀐 행만 교체되고 나머지 DOM 은 살아남는다.
const EV_TICK_MS = 2000;          // 라이브 이벤트 tick (구 700ms)
const EV_TICK_QUEUED_MS = 3000;   // 대기 큐 상태
const CAP_MS = 30000;             // capacity 기본 주기 (구 2s)
const CAP_QUEUED_MS = 5000;       // 대기열이 있을 때만 빠르게 (admit 관찰)
const RUNS_WATCH_MS = 30000;      // 종료 후 늦출현(+5m/+15m 재스캔) 감시 주기
const HIDDEN_RETRY_MS = 3000;     // document.hidden 동안 fetch 없이 재확인만
let evOffset = 0;                 // 이번 run 에서 이미 받은 이벤트 수 (증분 fetch)

// 내용이 실제로 바뀐 경우에만 innerHTML 교체 — 동일하면 DOM 유지(hover/클릭 생존).
function setHtmlIfChanged(el, html) {
  if (!el) return false;
  if (el._h === html) return false;
  el._h = html; el.innerHTML = html;
  return true;
}
// 키 기반 유닛 patch: units = [{k, html}] (html 의 첫 요소에 data-k="{k}" 필수,
// apirow+detail 처럼 형제 여러 개도 한 유닛). 컨테이너를 통째로 다시 그리지 않고
// 바뀐 유닛만 교체/삽입, 사라진 유닛만 제거한다.
function syncUnits(container, units) {
  if (!container) return;
  const inTable = container.tagName === "TBODY";
  const parse = html => {
    const t = document.createElement(inTable ? "tbody" : "div");
    t.innerHTML = html;
    return [...t.children];
  };
  const have = {};
  [...container.children].forEach(el => {
    const k = el.dataset && el.dataset.k;
    if (k !== undefined && k !== "") have[k] = el;
  });
  const removeUnit = head => {
    let cur = head;
    const stop = head._tail || head;
    while (cur) { const nx = cur.nextElementSibling; cur.remove(); if (cur === stop) break; cur = nx; }
  };
  let anchor = null;   // 마지막 확정 노드 — 새 유닛은 이 뒤에 들어간다
  units.forEach(u => {
    const head = have[u.k];
    if (head && head._h === u.html) { anchor = head._tail || head; delete have[u.k]; return; }
    const fresh = parse(u.html);
    if (!fresh.length) { if (head) { removeUnit(head); delete have[u.k]; } return; }
    fresh[0]._h = u.html;
    fresh[0]._tail = fresh[fresh.length - 1];
    const before = head || (anchor ? anchor.nextElementSibling : container.firstElementChild);
    fresh.forEach(n => container.insertBefore(n, before || null));
    if (head) { removeUnit(head); delete have[u.k]; }
    anchor = fresh[fresh.length - 1];
  });
  Object.keys(have).forEach(k => removeUnit(have[k]));
}

// ---- 런 진행률 (오너: "run 이 얼마나 진행되고 있는지") -----------------------
// 종결(done/fail/skip) lifecycle 수 / 전체 + 경과 + 잔여 추정. ETA 는
// durations.json 실측 평균(MODEL.durations)의 미종결 합 / 병렬 가정 6
// (duration_stats 와 동일 가정) — pre-flight 견적과 같은 데이터 소스.
const ETA_PARALLEL = 6;
function runProgress() {
  const st = lifecycleStates();
  const ids = Object.keys(st);
  const total = ids.length;
  const doneN = ids.filter(i => st[i] === "done" || st[i] === "fail" || st[i] === "skip").length;
  let firstTs = null;
  for (const e of runEvents) { if (e.ts) { firstTs = e.ts; break; } }
  const elapsed = firstTs ? Math.max(0, Date.now() / 1000 - firstTs) : null;
  const durs = (MODEL && MODEL.durations) || {};
  let rem = 0, known = 0;
  ids.forEach(i => {
    if (st[i] === "queued" || st[i] === "running") {
      const d = durs[i];
      if (d && d.avg_s) { rem += d.avg_s; known++; }
    }
  });
  const eta = known ? rem / Math.min(ETA_PARALLEL, known) : null;
  return { total, done: doneN, pct: total ? Math.round(doneN / total * 100) : 0,
           elapsed, eta };
}

// ---- per-lifecycle 중단 (서버 7624e296: POST /api/runs/{rid}/skip-lifecycle) --
function skipLifecycle(lc) {
  if (!runId || !lc) return;
  fetch("/api/runs/" + runId + "/skip-lifecycle", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lifecycle: lc }) })
    .then(r => r.json().then(j => ({ ok: r.ok, j })))
    .then(({ ok, j }) => toast(ok ? `⏸ ${lc} — ${j.note || "정리 후 스킵 예약됨"}`
                                  : `라이프사이클 중단 실패: ${j.error || "?"}`, ok ? "ok" : "fail"))
    .catch(e => toast("라이프사이클 중단 요청 실패: " + e.message, "fail"));
}

// ---- 위임 배선 (1회, 정적 컨테이너) — 행이 patch 로 교체돼도 클릭 불멸 --------
function wireReportDelegation() {
  const rail = $("lc-picker");
  if (rail && !rail._wired) {
    rail._wired = true;
    rail.addEventListener("click", ev => {
      const f = ev.target.closest(".fchip");
      if (f) { railFilter = f.dataset.f; renderLcPicker(); return; }
      if (ev.target.closest("#agg-toggle")) { selectScope("*"); return; }
      const row = ev.target.closest(".lcitem[data-lc]");
      if (row) { railUserTs = Date.now(); selectScope(row.dataset.lc); }
    });
  }
  const bar = $("scopebar");
  if (bar && !bar._wired) {
    bar._wired = true;
    bar.addEventListener("click", ev => {
      if (ev.target.closest("#scope-clear")) { selectScope("*"); return; }
      const sk = ev.target.closest("#scope-skip");
      if (sk) skipLifecycle(sk.dataset.lc);
    });
  }
  const body = $("detail-body");
  if (body && !body._wired) {
    body._wired = true;
    body.addEventListener("click", ev => {
      if (ev.target.closest("#hidedup-soft") || ev.target.closest("label")) return;  // change 가 처리
      const dl = ev.target.closest("[data-defsvc]");
      if (dl) { openDefinition(dl.dataset.defsvc); return; }
      // kpi 타일 = 결과 필터 토글 (2026-07-11 성능 수리 + 오너 기대 동작)
      const tile = ev.target.closest("#r3-kpi .selcat[data-cat]");
      if (tile) {
        const cat = tile.dataset.cat;
        apiCatFilter = (apiCatFilter === cat || cat === "all") ? "all" : cat;
        keepDetailScroll(reportR3);
        return;
      }
      if (ev.target.closest("#api-showall")) {   // 행 상한 해제 (명시적 opt-in)
        apiShowAll = true;
        keepDetailScroll(reportR3);
        return;
      }
      const row = ev.target.closest(".apirow[data-apik]");
      if (row) {
        expandedApi = expandedApi === row.dataset.apik ? null : row.dataset.apik;
        keepDetailScroll(reportR3);
      }
    });
    body.addEventListener("change", ev => {
      if (ev.target && ev.target.id === "hidedup-soft") {
        hideDupSoft = ev.target.checked;
        keepDetailScroll(reportR3);
      }
    });
  }
  const np = $("nowplaying");
  if (np && !np._wired) {
    np._wired = true;
    np.addEventListener("click", ev => { if (ev.target.closest("#np-abort")) abortConfirm(); });
  }
}

// ---- 비활성 탭 = 폴링 정지, 복귀 = 즉시 새로고침 ------------------------------
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (runId && (runStatus === "running" || runStatus === "queued")) pollEvents();
  if (screen === "run") startCapPoll(true);
});

function go(scr) {
  screen = scr;
  // leaving the run screen: tear down the 흐름 master scene (its window listeners must
  // not dangle on a hidden stage) and stop the log poller. Rebuilt cleanly on return
  // (the scene shell is keyed by runId → a fresh build re-attaches everything).
  if (scr !== "run") {
    closeDagModal();         // 팝업 + 그 안의 DAG 씬까지 정리 (열려있지 않아도 무해)
    if (stagedScene) { stagedScene.destroy(); stagedScene = null; }
    stopR4Poll();
    stopCapPoll();           // leaving the run screen → stop the capacity poll
    stopRunsWatch();         // P2C-24: 종료 후 감시도 화면과 함께 정지
  }
  ["build", "run"].forEach(s => $("screen-" + s).classList.toggle("hidden", s !== scr));
  els("#screenToggle button").forEach(b => b.classList.toggle("on", b.dataset.scr === scr));
  ctxBar();
  renderNowPlaying();       // persistent strip — follows the run on BOTH screens
  if (scr === "build") drawBuild();
  else drawRunScreen();
}
window.go = go;

// ---- global context bar ----
// '포함 자원' = 선택의 의존 폐쇄집합 크기 (자원 수 — 구 라벨 '포함 API' 는 실제
// 의미와 어긋났다, 2차 수용 후속). API 스텝 수는 별도로 병기. 재계산 중이면
// 스피너(⟳)를 보여 '0'이 잠깐 참값처럼 보이는 것을 막는다.
const CLOSURE_TITLE = "선택한 리소스가 의존성으로 자동으로 끌어오는 전체 자원(의존 폐쇄집합)의 수 — " +
  "이 자원들의 생성·검증·삭제 API 스텝이 실행 대상에 포함됩니다";
const APISTEP_TITLE = "폐쇄집합 자원들의 생성·검증·삭제 API 스텝 수 (모델 정의 기준 추정)";
function closureCount() {
  if (graphPending) return '<span class="spin" title="재계산 중">⟳</span>';
  return lastGraph ? lastGraph.nodes.length : "…";
}
// API-step count over a closure graph: sum of each node's modeled api list
// (create/verify/delete endpoints). null = not computable yet.
function apiStepCount(g) {
  const gg = g || lastGraph;
  if (!gg || !gg.nodes) return null;
  let n = 0;
  gg.nodes.forEach(nd => { const m = N[nd.id]; if (m && m.api) n += m.api.length; });
  return n;
}
// shared "포함 자원 N (API 스텝 ~M)" fragment for the context/readout/launch bars
function closureLabel() {
  const api = graphPending ? null : apiStepCount();
  return `<span title="${esc(CLOSURE_TITLE)}">포함 자원 <b>${closureCount()}</b></span>` +
    (api != null ? ` <span class="muted small" title="${esc(APISTEP_TITLE)}">(API 스텝 ~${api})</span>` : "");
}
function ctxBar() {
  const svcs = new Set([...targets].map(id => N[id].service));
  // heavy-전제(HEAVY-PREMISE-CONTRACT §5): 선택 화면에 heavy/light 어휘를 두지 않는다 —
  // 비용 정보는 pre-flight(대기열/미리보기 견적)에서만 등장한다.
  $("ctxbar").innerHTML =
    `<span class="seg">env <b>local</b></span>
     <span class="seg">· 선택 <b>${targets.size}</b> 리소스</span>
     <span class="seg">· 서비스 <b>${svcs.size}</b></span>
     <span class="seg">· ${closureLabel()}</span>
     <span class="seg">· 모델 <b>${MODEL.node_count}</b> 자원 / <b>${MODEL.lifecycle_count}</b> lifecycle</span>
     <span class="badge live">LIVE</span>`;
}

// ================= ① 구성 =================
function drawBuild() {
  initCollapse();
  drawSvcTree();
  wireDagControls();        // granularity / 전체 접기·펼치기 / 그림|표 (idempotent)
  refreshGraph();           // fetch /api/graph for the current selection
  $("sel-search").oninput = drawSvcTree;
  $("sel-all").onclick = toggleAll;
}

// wire the DAG-at-scale toolbar once (granularity, 전체 접기/펼치기 = the re-collapse
// fix, 그림|표 mode, zoom controls). Buttons call into the scene controller.
let _dagWired = false;
function wireDagControls() {
  if (_dagWired) return; _dagWired = true;
  // granularity 카테고리 / 서비스 / 전체 펼침
  els("#dag-gran button").forEach(b => b.onclick = () => {
    els("#dag-gran button").forEach(x => x.classList.toggle("on", x === b));
    if (dagScene) dagScene.setGranularity(b.dataset.gran);
  });
  // 전체 접기 / 전체 펼치기 — the OBVIOUS collapse affordance the user asked for
  $("dag-collapse").onclick = () => { syncGranBtn("category"); if (dagScene) { dagScene.setGranularity("category"); } };
  $("dag-expand").onclick = () => { syncGranBtn("resource"); if (dagScene) dagScene.expandAll(); };
  // zoom + / − / 맞춤
  $("dag-zin").onclick = () => dagScene && dagScene.zoomIn();
  $("dag-zout").onclick = () => dagScene && dagScene.zoomOut();
  $("dag-zfit").onclick = () => dagScene && dagScene.zoomToFit();
  // 그림 | 표 mode toggle
  els("#dag-mode button").forEach(b => b.onclick = () => {
    buildView = b.dataset.mode;
    els("#dag-mode button").forEach(x => x.classList.toggle("on", x === b));
    applyBuildView();
  });
}
function syncGranBtn(gran) {
  els("#dag-gran button").forEach(x => x.classList.toggle("on", x.dataset.gran === gran));
}
// reflect the scene's current granularity onto the toolbar (after a reset/auto-collapse)
function syncGranFromScene() {
  if (dagScene) syncGranBtn(dagScene.gran);
}

// 그림|표: show the stage OR the order table as the primary view. The 표 is scoped
// to the focus path when the DAG is focused (same selection, linear order).
function applyBuildView() {
  const fig = buildView === "fig";
  $("dag-stage-wrap").classList.toggle("hide", !fig);
  $("dag-readout").style.display = fig ? "" : "none";
  $("dag-tableview").classList.toggle("tab-primary", !fig);
  if (fig) { if (dagScene) dagScene.zoomToFit(); }
  else if (lastGraph) orderTable(lastGraph, dagFocus);   // re-render table scoped to focus
}

// categories start COLLAPSED except ones that already carry a selection. Computed
// once per session (first build render) so the user's manual toggles stick after.
function initCollapse() {
  if (collapsed) return;
  collapsed = new Set();
  const cats = categoryMap();
  Object.keys(cats).forEach(cat => {
    const hasSel = cats[cat].some(s => svcState(s) !== "off" && svcState(s) !== "none");
    if (!hasSel) collapsed.add(cat);
  });
}

// selection helpers --------------------------------------------------------
// "service selected" state over its lifecycle-bearing nodes: off | partial | on
function svcState(svc) {
  const sel = svcSelectable(svc);
  if (!sel.length) return "none";
  const on = sel.filter(id => targets.has(id)).length;
  return on === 0 ? "off" : on === sel.length ? "on" : "partial";
}
function setSvc(svc, want) {
  svcSelectable(svc).forEach(id => want ? targets.add(id) : targets.delete(id));
}
function allSelectableServices() {
  const out = [];
  Object.values(categoryMap()).forEach(svcs => svcs.forEach(s => { if (svcSelectable(s).length) out.push(s); }));
  return out;
}
function toggleAll() {
  const svcs = allSelectableServices();
  const allOn = svcs.every(s => svcState(s) === "on");
  svcs.forEach(s => setSvc(s, !allOn));
  selectionChanged();
}

// the selection readout: 선택: N 서비스 · M 리소스 · 폐포 K
// (heavy-전제 §5: 비용 표기는 pre-flight로 이동 — 선택 화면엔 두지 않는다)
function selReadout() {
  const svcs = new Set([...targets].map(id => N[id].service));
  $("sel-readout").innerHTML =
    `선택: <b>${svcs.size}</b> 서비스 · <b>${targets.size}</b> 리소스 · ` + closureLabel();
  // sync the "전체 선택" toggle state
  const svcsAll = allSelectableServices();
  const allOn = svcsAll.length && svcsAll.every(s => svcState(s) === "on");
  const btn = $("sel-all");
  if (btn) { btn.classList.toggle("on", !!allOn); btn.textContent = allOn ? "전체 해제" : "전체 선택"; }
}

// COMPACT collapsible category→service MENU-TREE (picker). One fixed-height row
// per category (▸/▾ + 카테고리 전체 토글 + count) and, when expanded, one fixed-
// height row per service (click = toggle whole service; "리소스…" → modal). Menu
// density — NOT cards. A search filter auto-expands matching categories.
function drawSvcTree() {
  const q = ($("sel-search").value || "").toLowerCase();
  const cats = categoryMap();
  let h = "";
  Object.keys(cats).forEach(cat => {
    const svcs = cats[cat].filter(s => !q || (shortName(s) + " " + s).toLowerCase().includes(q));
    if (!svcs.length) return;
    const selectableSvcs = svcs.filter(s => svcSelectable(s).length);
    const onCount = selectableSvcs.filter(s => svcState(s) !== "off").length;
    const catAllOn = selectableSvcs.length && selectableSvcs.every(s => svcState(s) === "on");
    const catPartial = !catAllOn && onCount > 0;
    // a search query forces every matching category open; otherwise honour state.
    const open = q ? true : !collapsed.has(cat);
    const catCls = catAllOn ? "on" : catPartial ? "partial" : "";
    h += `<div class="tcat ${open ? "open" : ""}">
      <div class="trow tcat-row ${catCls}" data-cat="${esc(cat)}">
        <span class="tcar">${open ? "▾" : "▸"}</span>
        <span class="tchk cat" data-catchk="${esc(cat)}" title="카테고리 전체 선택/해제">${catAllOn ? "✓" : catPartial ? "◐" : ""}</span>
        <span class="tname">${esc(catName(cat))}</span>
        <span class="tmeta">${onCount}/${svcs.length}</span>
      </div>`;
    if (open) {
      h += `<div class="tsvcs">`;
      svcs.forEach(svc => {
        const sel = svcSelectable(svc);
        const all = svcNodes(svc);
        const st = svcState(svc);
        const quota = all.some(id => N[id].quota);
        const onN = sel.filter(id => targets.has(id)).length;
        const cls = st === "on" ? "on" : st === "partial" ? "partial" : "";
        const noLc = !sel.length;
        const fracTxt = !sel.length ? "—" : st === "partial" ? `${onN}/${sel.length}` : `${sel.length}`;
        h += `<div class="trow tsvc-row ${cls} ${noLc ? "nolc" : ""}" data-svc="${esc(svc)}" title="${esc(svc)}${noLc ? " — 생애주기 없음(의존전용)" : " — 클릭하면 서비스 전체 선택"}">
            <span class="tchk svc">${st === "on" ? "✓" : st === "partial" ? "◐" : ""}</span>
            <span class="tname">${esc(shortName(svc))}${quota ? ' <span class="glyph q" title="quota 제약">⛔</span>' : ""}</span>
            <span class="tcount">${fracTxt}</span>
            <button class="tdef" data-def-svc="${esc(svc)}" title="📖 정의 보기 — 이 서비스의 생애주기·엔드포인트·지식(read-only)">📖</button>
            ${noLc
              ? '<span class="tdep" title="생애주기 없음 — 의존전용">의존전용</span>'
              : `<button class="tres ${st === "partial" ? "pick" : ""}" data-res-svc="${esc(svc)}" title="특정 리소스만 선택">리소스…</button>`}
          </div>`;
      });
      h += `</div>`;
    }
    h += `</div>`;
  });
  $("svcWrap").innerHTML = h || '<p class="empty">검색 결과 없음</p>';

  // category caret/name click = collapse/expand
  els("#svcWrap .tcat-row[data-cat]").forEach(row => row.onclick = ev => {
    if (ev.target.closest("[data-catchk]")) return;    // the cat checkbox toggles selection
    const cat = row.dataset.cat;
    collapsed.has(cat) ? collapsed.delete(cat) : collapsed.add(cat);
    drawSvcTree();
  });
  // category checkbox = 카테고리 전체 선택/해제
  els("#svcWrap [data-catchk]").forEach(chk => chk.onclick = ev => {
    ev.stopPropagation();
    const cat = chk.dataset.catchk;
    const svcs = (categoryMap()[cat] || []).filter(s => svcSelectable(s).length);
    const allOn = svcs.length && svcs.every(s => svcState(s) === "on");
    svcs.forEach(s => setSvc(s, !allOn));
    selectionChanged();
  });
  // service row click = toggle whole service
  els("#svcWrap .tsvc-row[data-svc]").forEach(row => row.onclick = ev => {
    if (ev.target.closest("[data-res-svc]") || ev.target.closest("[data-def-svc]")) return;  // buttons have their own handlers
    const svc = row.dataset.svc;
    if (!svcSelectable(svc).length) return;             // 의존전용 row — not selectable
    setSvc(svc, svcState(svc) !== "on");
    selectionChanged();
  });
  // "리소스…" → modal
  els("#svcWrap [data-res-svc]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openModal(b.dataset.resSvc);
  });
  // "📖" → read-only definition viewer (lifecycle + endpoints + knowledge)
  els("#svcWrap [data-def-svc]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openDefinition(b.dataset.defSvc);
  });
  selReadout();
}

// any selection change: re-render the tree (state), readout, and re-fetch the DAG
function selectionChanged() {
  persistSelection();       // survive iframe reloads (신규6)
  if (screen === "build") drawSvcTree();
  selReadout();
  ctxBar();
  refreshGraph();
  if (screen === "build") launchSummary();
}

// ---- live composition DAG via /api/graph (debounced) ----
function refreshGraph() {
  if (graphTimer) clearTimeout(graphTimer);
  graphPending = true;                       // '포함 API' shows ⟳ while stale
  graphTimer = setTimeout(fetchGraph, 180);
}
function fetchGraph() {
  const body = selectionPayload();
  fetch("/api/graph", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json()).then(g => {
      graphPending = false;
      if (g.error) { renderGraphError(g.error); return; }
      lastGraph = g;
      renderGraph(g);
      ctxBar(); selReadout(); launchSummary();
    }).catch(e => { graphPending = false; renderGraphError(e.message); });
}
function renderGraphError(msg) {
  $("dag-svg").innerHTML = `<text x="12" y="24" fill="#cf222e">graph: ${esc(msg)}</text>`;
}
function renderGraph(g) {
  const svg = $("dag-svg");
  if (!g.nodes.length) {
    if (dagScene) { dagScene.destroy(); dagScene = null; }
    dagFocus = null;
    svg.removeAttribute("style");
    svg.innerHTML = '<text x="12" y="24" fill="#656d76">서비스를 선택하면 합성 배포 DAG가 생성 순서대로 표시됩니다.</text>';
    svg.setAttribute("viewBox", "0 0 420 40"); svg.setAttribute("width", 420); svg.setAttribute("height", 40);
    $("dag-readout").innerHTML = "";
    $("order-tbl").innerHTML = "";
    $("dag-legend").innerHTML = "";
    $("dag-hint").innerHTML = "";
    $("dag-stat").innerHTML = "";
    $("dag-gran-note").textContent = "";
    return;
  }
  $("dag-legend").innerHTML = legend([
    ["#e6effd", "★ 대상"], ["#fffaf0", "■ 공유(dedup)"], ["#f3eefc", "↓ 의존"]
  ]) + '<span>그룹 = <b>접힘</b>(클릭=펼치기) · <span style="color:var(--val)">●</span> VALIDATED · <span style="color:var(--docs)">●</span> docs · ⛔ quota</span>';
  // (re)build or update the interactive scene. The node SET drives whether the
  // scene re-chooses its collapse state (new selection) or refreshes in place.
  if (!dagScene) {
    dagScene = makeDagScene(svg, g);
    dagScene.start();
  } else {
    dagScene.update(g);
  }
  syncGranFromScene();
  graphReadout(g);
  orderTable(g, dagFocus);
  applyBuildView();
}

// construct the 구성 DAG scene controller: group/collapse + focus + zoom.
// Interaction decision (least-surprising at scale): click a resource node = FOCUS
// (dependency path); the small ＋/✓ corner control toggles TARGET selection. Click a
// collapsed group = expand; click it again (or 전체 접기) collapses it back.
function makeDagScene(svg, g) {
  return window.ResourceGraph.scene(svg, $("dag-stage"), g, {
    hint: $("dag-hint"), stat: $("dag-stat"), granNote: $("dag-gran-note"),
    isSelectable: id => hasLifecycle(id),
    onToggleTarget: id => {                // ＋/✓ corner = toggle this target
      if (!hasLifecycle(id)) return;
      targets.has(id) ? targets.delete(id) : targets.add(id);
      selectionChanged();
    },
    onFocus: info => {                     // focus changed → keep the 표 in sync if shown
      dagFocus = info;
      if (buildView === "tab" && lastGraph) orderTable(lastGraph, dagFocus);
    },
  });
}

// readout: 생성 순서 · peak quota · 공유(dedup)
function graphReadout(g) {
  const order = (g.order || []).map(baseId);
  const seen = new Set(), uniqOrder = [];
  order.forEach(b => { if (!seen.has(b)) { seen.add(b); uniqOrder.push(b); } });
  const pq = Object.entries(g.peak_quota || {}).map(([k, v]) => `${esc(k)} ×${v}`).join(" · ") || "없음";
  $("dag-readout").innerHTML =
    `<b>생성 순서:</b> <span class="mono">${uniqOrder.map(esc).join(" → ") || "—"}</span><br>` +
    `<b>peak quota:</b> ${pq} · <b>공유(dedup):</b> ${(g.shared || []).length}` +
    `${(g.shared || []).length ? ' <span class="muted">(' + g.shared.map(esc).join(", ") + ')</span>' : ""}`;
}

// 생성/검증/삭제 순서표: create order (graph.order) · verify count (model.verify_n) · delete order (graph.teardown).
// When `focus` is given (그림|표 with a focused node), the table is SCOPED to the
// focus dependency path — the same selection shown linearly. A scope note above the
// table tells the user what they're looking at.
// pure order data over a graph: 생성 순서(dedup) · 삭제 rank · node lookup — the
// SAME table backs ① (dag-tableview) and ② (run 그래프 아래 접힘 순서표).
function orderRowsData(g, scopeSet) {
  const inScope = id => !scopeSet || scopeSet.has(id);
  const createOrder = [], seen = new Set();
  (g.order || []).forEach(inst => { const b = baseId(inst); if (!seen.has(b) && inScope(b)) { seen.add(b); createOrder.push(b); } });
  // teardown rank by base node (first occurrence)
  const delRank = {}; let r = 0;
  (g.teardown || []).forEach(inst => { const b = baseId(inst); if (!(b in delRank) && inScope(b)) delRank[b] = ++r; });
  const nodeById = {}; (g.nodes || []).forEach(n => { nodeById[n.id] = n; });
  return { createOrder, delRank, nodeById };
}
const ORDER_THEAD = "<thead><tr><th>생성#</th><th>리소스</th><th>service</th><th>검증(verify)</th><th>삭제#</th></tr></thead>";
function orderRowHtml(id, i, data, cls) {
  const n = data.nodeById[id] || {};
  const verifyN = (N[id] && N[id].verify_n != null) ? N[id].verify_n : 0;
  const tgt = n.is_target ? '<span class="bdg run" style="border:none;background:none;color:var(--accent);padding:0">★</span>' : "";
  const sh = n.shared ? '<span class="tag amber" title="공유(dedup)">공유</span>' : "";
  return `<tr${cls ? ` class="${cls}"` : ""}>
    <td class="ordn">${i + 1}</td>
    <td><b>${esc(id)}</b> ${tgt} ${sh}</td>
    <td class="muted">${esc(shortName(n.service || (N[id] && N[id].service) || ""))}</td>
    <td class="ordn">${verifyN}</td>
    <td class="ordn">${data.delRank[id] || "—"}</td>
  </tr>`;
}

function orderTable(g, focus) {
  const scopeSet = focus && focus.resourceIds ? new Set(focus.resourceIds) : null;
  const data = orderRowsData(g, scopeSet);
  const createOrder = data.createOrder;
  // scope note (shown in 표 mode; harmless in 그림 mode where the table is hidden)
  const titleEl = $("dag-table-title");
  if (titleEl) {
    const note = scopeSet
      ? `<div class="tab-scope">focus: <b>${esc(focus.label)}</b> 경로 · <b>${createOrder.length}</b> 자원 (전체 ${g.nodes.length})</div>`
      : `<div class="tab-scope">전체 선택 · <b>${createOrder.length}</b> 자원</div>`;
    titleEl.innerHTML = `생성 · 검증 · 삭제 순서표${note}`;
  }
  const rows = createOrder.map((id, i) => orderRowHtml(id, i, data, "")).join("");
  $("order-tbl").innerHTML = ORDER_THEAD +
    `<tbody>${rows || '<tr><td colspan="5" class="empty">없음</td></tr>'}</tbody>`;
}

const baseId = inst => (inst + "").split("#")[0];

// ================= resource modal (specific-resource selection) =================
function wireModal() {
  const close = () => closeModal();
  $("modal-close").onclick = close;
  $("modal-scrim").onclick = close;
  $("modal-done").onclick = close;
  $("modal-clear").onclick = () => {
    if (modalSvc) setSvc(modalSvc, false);
    drawModalBody();
    selectionChanged();
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
}
function openModal(svc) {
  modalSvc = svc;
  $("modal-title").textContent = "리소스 선택 — " + shortName(svc);
  $("modal-svc").textContent = svc;
  drawModalBody();
  $("res-modal").classList.add("open");
  $("modal-scrim").classList.add("open");
}
function closeModal() {
  $("res-modal").classList.remove("open");
  $("modal-scrim").classList.remove("open");
  modalSvc = null;
}
function drawModalBody() {
  const svc = modalSvc; if (!svc) return;
  // dependency set pulled in by the CURRENT closure (so we can flag "의존으로 포함")
  const depIds = new Set((lastGraph ? lastGraph.nodes : []).filter(n => !n.is_target).map(n => n.id));
  const ids = svcNodes(svc).slice().sort();
  let h = "";
  ids.forEach(id => {
    const n = N[id];
    const lc = hasLifecycle(id);
    const on = targets.has(id);
    const pulledDep = !on && depIds.has(id);
    const prov = n.provenance === "VALIDATED" ? "var(--val)" : "var(--docs)";
    if (!lc) {
      // lookup / no-lifecycle resource: dimmed + disabled, never selectable
      h += `<div class="mres nolc"><label>
          <span class="cb dash"></span>
          <span class="nodedot" style="background:${prov}"></span>
          <span>${esc(id)}</span></label>
          <span class="meta"><span class="tag dep">의존전용</span></span></div>`;
    } else {
      h += `<div class="mres ${pulledDep ? "dep" : ""}" data-mid="${esc(id)}"><label>
          <span class="cb ${on ? "on" : ""}"></span>
          <span class="nodedot" style="background:${prov}"></span>
          <b>${esc(id)}</b></label>
          <span class="meta">${n.quota ? '<span class="tag amber">⛔ ' + esc(n.quota) + "</span>" : ""}${pulledDep ? '<span class="tag dep">의존으로 포함</span>' : ""}</span></div>`;
    }
  });
  $("modal-body").innerHTML = h || '<p class="empty">리소스 없음</p>';
  const sel = svcSelectable(svc);
  const onN = sel.filter(id => targets.has(id)).length;
  $("modal-hint").innerHTML = onN
    ? `이 서비스에서 <b>${onN}</b>개 리소스만 선택됨 (서비스 전체 대신 이것만 합성).`
    : `아무것도 고르지 않고 닫으면 이 서비스는 선택되지 않습니다.`;
  els("#modal-body [data-mid]").forEach(row => row.onclick = () => {
    const id = row.dataset.mid;
    targets.has(id) ? targets.delete(id) : targets.add(id);
    drawModalBody();
    selectionChanged();
  });
}

// ================= 📖 definition viewer (READ-ONLY) =================
// Surfaces a service's TEST DEFINITION (runnable lifecycle steps + each resource's
// create/verify/delete endpoints, request options, dependencies — from the model)
// and the accumulated KNOWLEDGE facts (knowledge/*.md paragraphs that mention it).
// Pure read: opening it never touches the selection, the DAG, or any run.
function wireDefModal() {
  const close = () => closeDefinition();
  $("def-close").onclick = close;
  $("def-scrim").onclick = close;
  $("def-done").onclick = close;
  document.addEventListener("keydown", e => { if (e.key === "Escape" && defSvc) close(); });
}
function openDefinition(svc) {
  defSvc = svc;
  $("def-title").textContent = "📖 정의 — " + shortName(svc);
  $("def-svc").textContent = svc;
  $("def-body").innerHTML = '<p class="empty">정의 불러오는 중…</p>';
  $("def-modal").classList.add("open");
  $("def-scrim").classList.add("open");
  const q = "?service=" + encodeURIComponent(svc);
  Promise.all([
    fetch("/api/lifecycles" + q).then(r => r.json()).catch(e => ({ error: String(e && e.message || e) })),
    fetch("/api/knowledge" + q).then(r => r.json()).catch(e => ({ error: String(e && e.message || e) })),
  ]).then(([lc, kn]) => { if (defSvc === svc) renderDefBody(lc, kn); });
}
function closeDefinition() {
  $("def-modal").classList.remove("open");
  $("def-scrim").classList.remove("open");
  defSvc = null;
}
function renderDefBody(lc, kn) {
  const defm = mth => `<span class="defm defm-${esc((mth || "").toLowerCase())}">${esc(mth || "·")}</span>`;
  const depName = x => esc((x && x.ref) ? x.ref : x) + (x && x.count > 1 ? `×${x.count}` : "");
  let h = "";
  // ---- runnable lifecycles (the ordered steps the engine executes) ----
  if (lc && lc.error) {
    h += `<div class="def-sec"><h4>생애주기</h4><p class="err">${esc(lc.error)}</p></div>`;
  } else if (lc) {
    const lcs = lc.lifecycles || [];
    h += `<div class="def-sec"><h4>생애주기 <span class="muted small">${lcs.length}개 · 엔진이 실행하는 단계 (생성→검증→삭제)</span></h4>`;
    if (!lcs.length) h += '<p class="muted small">정의된 생애주기 없음 (의존전용 서비스일 수 있음).</p>';
    lcs.forEach(L => {
      const steps = L.steps || [];
      h += `<details class="def-lc"><summary><b>${esc(L.id)}</b>${L.heavy ? ' <span class="bdg heavy">과금</span>' : ""}${L.role === "probe" ? ' <span class="bdg off" title="도달 프로브 — CI 스윕 전용, 서비스 선택 실행에서는 제외">프로브</span>' : ""}${L.enabled === false ? ' <span class="bdg off">disabled</span>' : ""} <span class="muted small">${L.n_steps || steps.length} steps</span></summary><ol class="def-steps">`;
      steps.forEach(s => {
        h += `<li>${defm(s.method)}<code>${esc(s.path || "")}</code>${s.kind ? `<span class="kind">${esc(s.kind)}</span>` : ""}${s.optional ? ' <span class="muted small">optional</span>' : ""}</li>`;
      });
      h += "</ol></details>";
    });
    h += "</div>";
  }
  // ---- resource definitions (endpoints · request options · dependencies) ----
  if (lc && lc.resources) {
    const rs = lc.resources;
    h += `<div class="def-sec"><h4>리소스 <span class="muted small">${rs.length}개 · 엔드포인트·요청옵션·의존</span></h4>`;
    rs.forEach(r => {
      h += `<details class="def-res"><summary><b>${esc(r.code || r.id)}</b> <span class="prov ${r.provenance === "VALIDATED" ? "val" : "docs"}">${esc(r.provenance)}</span>${r.heavy ? ' <span class="bdg heavy">과금</span>' : ""}${r.quota ? ' <span class="bdg q">⛔ quota</span>' : ""}</summary>`;
      (r.api || []).forEach(a => {
        const parts = (a.endpoint || "").split(" ");
        h += `<div class="def-apirow"><span class="phase ${esc(a.phase)}">${esc(a.phase)}</span>${defm(parts[0])}<code>${esc(parts.slice(1).join(" "))}</code></div>`;
      });
      if ((r.options || []).length) {
        h += '<div class="def-opts"><span class="def-lbl">요청 옵션</span>';
        r.options.forEach(o => { h += `<span class="opt${o.required ? " req" : ""}">${esc(o.name)}<span class="ty">${esc(o.type)}</span>${o.ref_target ? `→${esc(o.ref_target)}` : ""}</span>`; });
        h += "</div>";
      }
      const d = r.deps || {};
      if ((d.and || []).length) h += `<div class="def-deps"><span class="def-lbl">의존(필수)</span> ${d.and.map(depName).join(", ")}</div>`;
      if ((d.one_of || []).length) h += `<div class="def-deps"><span class="def-lbl">택1</span> ${d.one_of.map(depName).join(" | ")}</div>`;
      if ((d.creds || []).length) h += `<div class="def-deps"><span class="def-lbl">자격</span> ${d.creds.map(esc).join(", ")}</div>`;
      h += "</details>";
    });
    h += "</div>";
  }
  // ---- knowledge facts (filtered view of knowledge/*.md) ----
  if (kn && kn.error) {
    h += `<div class="def-sec"><h4>지식</h4><p class="err">${esc(kn.error)}</p></div>`;
  } else if (kn) {
    const facts = kn.facts || [];
    h += `<div class="def-sec"><h4>지식 <span class="muted small">knowledge/*.md · ${facts.length} facts${kn.truncated ? "+" : ""}</span></h4>`;
    if (!facts.length) h += '<p class="muted small">이 서비스에 매칭된 지식 항목 없음.</p>';
    facts.forEach(f => {
      h += `<details class="def-fact"><summary><span class="kfile">${esc(f.file)}</span>${f.anchor ? ` › <span class="kanchor">${esc(f.anchor)}</span>` : ""}</summary><pre class="ksnip">${esc(f.snippet)}</pre></details>`;
    });
    h += "</div>";
  }
  $("def-body").innerHTML = h || '<p class="empty">정의 없음.</p>';
}

// ================= launch bar (carry selection into ②) =================
// 구성 ▶ no longer RUNS — it STAGES (enqueues) the current selection and crosses to
// ② where the deliberate, budget-informed [▶ 실행] commits it. The run-settings panel
// on ② keeps its own direct LIVE button (startRun) for the current selection.
function wireLaunch() {
  const lg = $("launch-go"); if (lg) lg.onclick = stageRun;
  const rg = $("run-go"); if (rg) rg.onclick = startRun;   // drawRunSettings rebuilds + rebinds this too
  // 📊 예상 타임라인 — launchSum 은 launchSummary() 가 폴/선택 변경마다 innerHTML
  // 재생성하므로 위임 1회 배선 (재배선 불요, 클릭 불멸).
  const ls = $("launchSum");
  if (ls && !ls._simWired) {
    ls._simWired = true;
    ls.addEventListener("click", e => { if (e.target.closest("#sim-open")) openSimModal(); });
  }
}

// a small uuid for a staged item key (crypto.randomUUID when available, else a
// timestamp+random fallback so older/file:// contexts still get a unique id).
function uuid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

// staged 항목의 pre-flight 요약 문구 (자원 N (과금 M) · 예상 T) — pf 없으면 "" 반환
function pfSummary(pf) {
  if (!pf) return "";
  const nRes = (pf.resources || []).reduce((a, r) => a + (r.count || 1), 0);
  const bill = pf.billable_count || 0;
  const est = pf.est || {};
  return `자원 <b>${nRes}</b>개${bill ? ` (<span class="hv">과금 ${bill}</span>)` : ""} · 예상 <b>${fmtDur(est.p50_s)}</b>`;
}

// 구성 ▶ = STAGE: snapshot the current selection into STAGED (peak_vpcs/heavy from
// /api/plan, nServices/nResources from the selection, closure from the live DAG),
// then cross to ② so the user sees it in the 실행 대기열. NO /api/run here, no
// confirm — execution is a separate, deliberate action on ②.
function stageRun() {
  if (!targets.size) return;
  const selection = selectionPayload();
  const nServices = new Set([...targets].map(id => N[id].service)).size;
  const nResources = targets.size;
  const closure = lastGraph ? lastGraph.nodes.length : nResources;   // dependency closure size
  const apiSteps = apiStepCount();                                   // 폐쇄집합 API 스텝 수
  const heavyGuess = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  // resolve peak_vpcs + heavy from the REAL plan; fall back to the local guess if the
  // pre-flight plan call fails (the staged item is still actionable on ②).
  const add = (peak, heavy, pf) => {
    STAGED.push({ id: uuid(), selection, nServices, nResources,
                  peak_vpcs: peak, heavy: heavy, closure, apiSteps, pf: pf || null });
    go("run");
  };
  // heavy-전제(§3): staging 시점에 pre-flight 견적(자원·과금·예상시간)을 확보해 항목에 싣는다.
  // /api/preflight가 없는(구) 서버면 /api/plan으로 강등 — 항목은 어느 쪽이든 실행 가능.
  fetch("/api/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })
    .then(r => { if (!r.ok) throw new Error("no /api/preflight"); return r.json(); })
    .then(pf => {
      const peak = (pf.peak_quota || {}).vpc || 0;
      add(peak, (pf.billable_count || 0) > 0, pf);
    })
    .catch(() =>
      fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })
        .then(r => r.json()).then(plan => {
          plan = plan || {};
          const peak = plan.peak_vpcs || 0;
          const heavy = Object.values(plan.preview || {}).some(p => p && p.heavy) || heavyGuess;
          add(peak, heavy, null);
        }).catch(() => add(0, heavyGuess, null)));
}

// ================= 스윗 (suites/*.yaml · CI 공유) =================
// A suite = a named (scope × safety-gates) preset. Loading one applies its SCOPE
// (node_ids ▸ services ▸ categories, else the whole catalog) to the selection;
// saving POSTs the current selection back to suites/<id>.yaml via /api/suites. The
// built-in 4 (smoke/full/full-heavy/conformance) are the canonical run shapes;
// saved ones round-trip console2's exact selection through the CI-ignored `scope:`
// block. There is no axis anymore — the run derives its gates from the selection,
// so a loaded suite only restores WHAT is selected.
let SUITES = [];

async function loadSuites() {
  try {
    const r = await fetch("/api/suites");
    SUITES = (await r.json()).suites || [];
  } catch (e) { SUITES = []; }
  drawSuiteMenu();
}
function closeSuiteMenu() {
  const m = $("suite-menu"); if (m) m.classList.add("hidden");
  const b = $("suite-btn"); if (b) b.classList.remove("on");
}
function drawSuiteMenu() {
  const m = $("suite-menu"); if (!m) return;
  const rows = SUITES.map(s => {
    const on = Object.keys(s.gates || {}).filter(k => s.gates[k]);
    const chips = on.length
      ? on.map(k => `<span class="sgate g-${esc(k)}">${esc(k)}</span>`).join("")
      : `<span class="sgate ro">read-only</span>`;
    const tag = s.builtin ? `<span class="stag b">기본</span>` : `<span class="stag">저장됨</span>`;
    return `<div class="srow" data-suite="${esc(s.id)}">
      <div class="sline"><span class="sname">${esc(s.id)}</span>${tag}<span class="sgates">${chips}</span></div>
      ${s.label ? `<div class="slabel">${esc(s.label)}</div>` : ""}</div>`;
  }).join("");
  m.innerHTML = rows +
    `<div class="srow ssave" id="suite-save">＋ 현재 선택을 스윗으로 저장…</div>`;
  els("#suite-menu .srow[data-suite]").forEach(r => r.onclick = () => {
    const s = SUITES.find(x => x.id === r.dataset.suite); if (s) applySuite(s);
  });
  const sv = $("suite-save"); if (sv) sv.onclick = saveCurrentAsSuite;
}
function applySuite(s) {
  const sc = s.scope || {}, req = s.request || {};
  // scope → targets (prefer the richest available: nodes ▸ services ▸ categories ▸ all).
  // No axis mapping — the run derives its gates from the resulting SELECTION.
  targets.clear();
  const addIf = pred => Object.keys(N).forEach(id => { if (N[id].lifecycle && pred(id)) targets.add(id); });
  if (Array.isArray(sc.node_ids) && sc.node_ids.length) {
    sc.node_ids.forEach(id => { if (N[id] && N[id].lifecycle) targets.add(id); });
  } else if ((sc.services && sc.services.length) || req.service) {
    const set = new Set(sc.services || []);
    if (req.service) Object.keys(N).forEach(id => { if (shortName(N[id].service) === req.service) set.add(N[id].service); });
    addIf(id => set.has(N[id].service));
  } else if ((sc.categories && sc.categories.length) || req.category) {
    const set = new Set([...(sc.categories || []), ...(req.category ? [req.category] : [])]);
    addIf(id => set.has(N[id].category));
  } else {
    addIf(() => true);                       // whole-catalog suite (smoke/full/…)
  }
  closeSuiteMenu();
  selectionChanged();
}
function currentSuitePayload(id, label) {
  const node_ids = [...targets];
  const services = [...new Set(node_ids.map(i => N[i].service))];
  const categories = [...new Set(node_ids.map(i => N[i].category))];
  // gates are DERIVED from the selection (no axis): a CRUD run always needs
  // mutations+destructive; heavy iff the selection pulls in a heavy lifecycle.
  const request = {
    mutations: true, destructive: true,
    heavy: [...targets].some(id => N[id] && N[id].heavy),
  };
  if (services.length === 1) {               // single service → CI-precise filter (README convention)
    const sn = shortName(services[0]);
    request.service = sn; request.crud_filter = sn;
  }
  return { id, label, request, scope: { node_ids, services, categories } };
}
async function saveCurrentAsSuite() {
  if (!targets.size) { alert("선택된 리소스가 없습니다 — 먼저 서비스를 선택하세요."); return; }
  const id = (prompt("스윗 id (소문자·숫자·-_. · 예: net-core):", "") || "").trim().toLowerCase();
  if (!id) return;
  const label = (prompt("설명 (label, 선택):", "") || "").trim();
  try {
    const res = await fetch("/api/suites", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentSuitePayload(id, label))
    });
    const j = await res.json();
    if (!res.ok) { alert("저장 실패: " + (j.error || res.status)); return; }
    SUITES = j.suites || SUITES; drawSuiteMenu();
    alert(`스윗 '${id}' 저장됨 → suites/${id}.yaml (CI 공유)`);
  } catch (e) { alert("저장 실패: " + e); }
}
function wireSuites() {
  const b = $("suite-btn"); if (!b) return;
  b.onclick = e => {
    e.stopPropagation();
    const hidden = $("suite-menu").classList.toggle("hidden");
    b.classList.toggle("on", !hidden);
  };
  document.addEventListener("click", e => {
    const w = $("suitewrap"); if (w && !w.contains(e.target)) closeSuiteMenu();
  });
  loadSuites();
}
function launchSummary() {
  const svcs = new Set([...targets].map(id => N[id].service));
  const heavy = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  const pq = lastGraph ? Object.values(lastGraph.peak_quota || {}).reduce((a, b) => a + b, 0) : 0;
  $("launchSum").innerHTML =
    `대상 <b>${svcs.size}</b> svc / <b>${targets.size}</b> 리소스 · ` +
    closureLabel() + ` · peak quota <b>${pq}</b> · ` +
    `과금 자원 <span class="${heavy ? "hv" : ""}">${heavy ? "포함" : "없음"}</span> ` +
    // 📊 스케줄 시뮬 (오너 2026-07-11): 선택(비면 전체 enabled)의 예상 동시 배치
    // 간트 — /api/schedule-sim 오프라인 계산, 워커/VPC 슬롯 조정 후 재계산 가능.
    `<button class="minibtn" id="sim-open"
       title="예상 동시 배치 간트 — 선택(비어 있으면 전체 enabled)을 conftest 와 동일 규칙으로 오프라인 시뮬 (API 호출 없음) · 워커/VPC 슬롯 조정">📊 예상 타임라인</button>`;
  const go = $("launch-go");
  if (go) {
    go.disabled = !targets.size;
    go.className = "btn stagebtn";          // 구성 ▶ STAGES (enqueues) — not a LIVE run
    // (NOTE: class is "stagebtn", NOT "stage" — ".stage" is the tall DAG-scene
    //  container; sharing it gave this button a 560px min-height.)
    go.textContent = "▶ 실행 대기열에 추가";
  }
}

// ================= ② 실행 & 리포트 =================
function drawRunScreen() {
  // CX 재설계 (owner GO 2026-07-08): 좌측 컬럼 폐지 — 실행-전 1회성 정보는 상단
  // 슬림 스트립 3줄(용량 · 잔존 · 대기열), 라이브 리포트는 전폭. 실행설정 패널은
  // 폐지(게이트는 pre-flight 모달 + 대기열 빈-상태 줄의 직접실행에 표시).
  $("cap-strip").innerHTML = '<div id="cap-bar"></div>';
  $("leftover-strip").innerHTML = '<div id="leftover-panel"></div>';
  $("staged-strip").innerHTML = '<div id="staged-panel"></div>';
  // 🕸 버튼은 배너가 폴마다 재렌더되므로 위임 리스너 1개로 배선.
  $("report-main").onclick = e => { if (e.target.closest("#r1-dag-open")) openDagModal(); };
  drawCapBar();
  drawLeftover();
  drawStagedPanel();
  startCapPoll(true);       // P2C-24: capacity 30s 주기(대기열 있으면 5s), 진입 시 즉시 1회
  if (runId) loadRunRecords();   // 히스토리 헤더는 화면 진입 시 1회 (폴 동승 금지)
  drawReport();
  startRunsWatch();         // 종료 상태로 진입한 경우의 늦출현 감시
}

// ---- 실행 대기열 (#staged-panel) — decide + execute -----------------------------
// The STAGED list (snapshots from 구성 ▶). Each row summarises 필요 VPC; click to
// expand 필요 VPC · 폐포 · 현재 여유(headroom from the last /api/capacity poll) + the
// commit/discard buttons. [▶ 실행] is the ACTUAL run: POST /api/run for that item's
// selection (server admits or auto-queues), remove it from STAGED, and drive the
// report to the new run. [✕ 제거] just drops it. Re-rendered on every STAGED change
// and on each capacity poll so 여유 updates live.
function drawStagedPanel() {
  const host = $("staged-panel"); if (!host) return;
  const c = lastCapacity || {};
  const headroom = c.headroom != null ? c.headroom
    : Math.max(0, (c.cap || 0) - ((c.baseline || 0) + (c.reserved || 0)));
  let body;
  if (!STAGED.length) {
    // 빈 상태 = 직접 실행 줄 (실행설정 패널 폐지분 흡수 — 게이트 칩 + LIVE 버튼).
    const svcs = new Set([...targets].map(id => (N[id] && N[id].service) || ""));
    const heavy = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id] && N[id].heavy);
    body = `<div class="staged-empty-row">
      <span class="muted small">비어 있음 — ① 구성에서 추가하거나 현재 선택 바로 실행:</span>
      <span class="chip" style="border-color:var(--red)" title="LIVE 는 항상 mutations ON — 게이트는 선택에서 파생, 토글 없음">✔ mutations</span>
      <span class="chip" style="border-color:var(--red)">✔ destructive</span>
      <span class="chip" style="border-color:${heavy ? "var(--red)" : "var(--line)"}">${heavy ? "✔ 과금 포함" : "✕ 과금 없음"}</span>
      <span class="muted small">선택 <b>${svcs.size}</b> svc · <b>${targets.size}</b> 리소스</span>
      <button class="minibtn go" id="run-go" ${targets.size ? "" : "disabled"} title="pre-flight confirm 후 실행">⚠ LIVE 실행 ▶</button>
      <button class="minibtn" id="run-toconf" title="① 구성으로 돌아가 선택 변경">← 구성</button>
    </div>`;
  } else {
    body = STAGED.map(it => {
      const open = stagedOpen === it.id;
      const over = (it.peak_vpcs || 0) > headroom;
      // pre-flight 요약(§3): 견적이 있으면 자원·과금·예상시간이 요약/버튼에 그대로 —
      // [▶ 실행]이 곧 informed confirm (heavy-전제: 별도 게이트 없음).
      const pf = it.pf;
      const est = (pf && pf.est) || {};
      const summary = `<b>${it.nServices}</b> 서비스 · <b>${it.nResources}</b> 리소스 · ` +
        (pf ? pfSummary(pf) : `VPC <b>${it.peak_vpcs || 0}</b> 필요`);
      const runLabel = pf && pf.billable_count
        ? `▶ 실행 <span class="small">(과금 ${pf.billable_count} · ${fmtDur(est.p50_s)})</span>`
        : "▶ 실행";
      const detail = open ? `<div class="staged-detail">
          <div class="staged-facts">필요 VPC <b>${it.peak_vpcs || 0}</b> · <span title="${esc(CLOSURE_TITLE)}">포함 자원 <b>${it.closure}</b></span>${it.apiSteps != null ? ` <span class="muted small" title="${esc(APISTEP_TITLE)}">(API 스텝 ~${it.apiSteps})</span>` : ""} · 현재 여유 <b>${headroom}</b></div>
          ${pf ? `<div class="staged-facts">예상 <b>${fmtDur(est.p50_s)}</b> ~ ${fmtDur(est.p90_s)} <span class="muted small">(${esc(est.basis || "?")})</span> · ${pf.billable_count ? `과금 자원 <b>${pf.billable_count}</b>개` : "과금 자원 없음"}${(pf.warnings || []).length ? ` · ⚠ ${esc(pf.warnings[0])}` : ""}</div>
          ${(pf.resources || []).length ? `<div class="staged-facts muted small">${(pf.resources || []).slice(0, 8).map(r => `${esc(r.node)}${(r.count || 1) > 1 ? "×" + r.count : ""}${r.billable ? "💰" : ""}`).join(" · ")}${(pf.resources || []).length > 8 ? " …" : ""}</div>` : ""}` : ""}
          ${over ? `<div class="staged-over">여유 부족 → 대기 큐로 들어갑니다</div>` : ""}
          <div class="staged-act">
            <button class="minibtn go" data-stage-run="${esc(it.id)}">${runLabel}</button>
            <button class="minibtn red" data-stage-del="${esc(it.id)}">✕ 제거</button>
          </div>
        </div>` : "";
      // [▶ 실행]은 행에 인라인 (owner 2026-07-08: "매번 뭘 열어야 보이던데" —
      // 대기열 1건이 대부분인 흐름에서 펼침 없이 바로 실행). 펼침(▸)은 상세·DAG
      // 미리보기용으로 유지.
      return `<div class="staged-item ${open ? "open" : ""}">
        <div class="staged-rowwrap">
          <button class="staged-row" data-stage-tog="${esc(it.id)}" title="클릭하면 상세 + 합성 DAG 미리보기">
            <span class="staged-sum">${summary}</span>
            <span class="staged-car">${open ? "▾" : "▸"}</span>
          </button>
          <span class="staged-inline-act">
            <button class="minibtn go" data-stage-run="${esc(it.id)}" title="바로 실행 (LIVE) — cap 아래면 ADMIT, 아니면 대기 큐">${runLabel}</button>
            <button class="minibtn red" data-stage-del="${esc(it.id)}" title="대기열에서 제거">✕</button>
          </span>
        </div>${detail}</div>`;
    }).join("");
  }
  host.innerHTML = `<div class="panel staged-pnl staged-line">
    <b class="cap-t" title="① 구성에서 '실행 대기열에 추가'한 계획 — 행에서 바로 ▶ 실행, 클릭=상세/DAG 미리보기">대기열${STAGED.length ? ` <span class="n">${STAGED.length}</span>` : ""}</b>
    <div class="staged-body">${body}</div>
  </div>`;
  if ($("run-go")) $("run-go").onclick = startRun;
  if ($("run-toconf")) $("run-toconf").onclick = () => go("build");
  els("#staged-panel [data-stage-tog]").forEach(b => b.onclick = () => {
    const id = b.dataset.stageTog;
    stagedOpen = stagedOpen === id ? null : id;     // toggle (one open at a time)
    drawStagedPanel();
    renderStagedPreview();      // open item → show its 합성 DAG in the 흐름 area
  });
  els("#staged-panel [data-stage-del]").forEach(b => b.onclick = () => {
    const id = b.dataset.stageDel;
    STAGED = STAGED.filter(x => x.id !== id);
    if (stagedOpen === id) stagedOpen = null;
    drawStagedPanel();
    renderStagedPreview();      // removed the previewed item → restore placeholder
  });
  els("#staged-panel [data-stage-run]").forEach(b => b.onclick = () => {
    const it = STAGED.find(x => x.id === b.dataset.stageRun);
    if (it) runStaged(it);
  });
}

// ---- 대기열 미리보기: render the OPEN staged item's composition DAG into the 흐름
// (report-main) area — which is otherwise idle until a run starts. Read-only (no
// target selection), keyed by item id so a capacity poll never rebuilds the live
// scene. A live run owns this area (runId set) → the preview steps aside. Clicking
// the item again (stagedOpen=null) restores the idle placeholder. ----
function renderStagedPreview() {
  if (screen !== "run") return;
  const host = $("report-main"); if (!host) return;
  if (runId) return;                                  // a live run owns the 흐름 area
  const item = stagedOpen ? STAGED.find(x => x.id === stagedOpen) : null;
  if (!item) {                                        // nothing open → idle placeholder
    if (stagedScene) { stagedScene.destroy(); stagedScene = null; }
    host.dataset.preview = "";
    host.innerHTML = '<p class="empty">아직 실행이 없습니다 — 대기열 항목을 클릭하면 합성 DAG가 여기 보입니다. 실제 실행은 좌측 <b>▶ 실행</b>.</p>';
    return;
  }
  if (host.dataset.preview === item.id && stagedScene) return;   // already showing this item
  if (stagedScene) { stagedScene.destroy(); stagedScene = null; }
  // 현재 여유(headroom) + 부족 badge live in #sp-headroom/#sp-overbadge (filled by
  // updateStagedPreviewBudget from the cap poll) so "그림 보고 → 바로 실행" has the
  // budget context right by the button, without rebuilding the DAG scene each poll.
  host.dataset.preview = item.id;
  const pf = item.pf, est = (pf && pf.est) || {};
  const pfLine = pf ? ` · ${pfSummary(pf)} <span class="muted">(p90 ${fmtDur(est.p90_s)})</span>` : "";
  const runLabel = pf && pf.billable_count
    ? `▶ 실행 <span class="small">(과금 ${pf.billable_count} · ${fmtDur(est.p50_s)})</span>` : "▶ 실행";
  host.innerHTML = `<div class="nowbar sp-head"><span class="dot" style="background:var(--accent)"></span>
      <b>대기열 미리보기</b>
      <span class="muted small">${item.nServices} 서비스 · ${item.nResources} 리소스 · <span title="${esc(CLOSURE_TITLE)}">포함 자원 ${item.closure}</span>${item.apiSteps != null ? `<span title="${esc(APISTEP_TITLE)}"> (API 스텝 ~${item.apiSteps})</span>` : ""} · VPC <b>${item.peak_vpcs || 0}</b> 필요 · 현재 여유 <b id="sp-headroom">…</b>${pfLine}</span>
      <span class="sp-act">
        <span id="sp-overbadge"></span>
        <button class="minibtn go" id="sp-run" title="이 계획을 실제 실행(LIVE) — cap 아래면 ADMIT, 아니면 대기 큐로">${runLabel}</button>
      </span></div>
    <div class="legend" id="sp-legend"></div>
    <div class="stage-wrap"><div class="stage" id="sp-stage">
        <svg id="sp-svg" class="scene-svg" xmlns="http://www.w3.org/2000/svg"></svg>
        <div class="hint-pill" id="sp-hint"></div>
        <div class="zoomctl"><button id="sp-zin" title="확대">+</button><button id="sp-zout" title="축소">−</button><button id="sp-zfit" class="fit" title="전체 보기">맞춤</button></div>
      </div></div>`;
  $("sp-run").onclick = () => runStaged(item);     // ▶ 실행 right by the DAG (그림 → 실행)
  updateStagedPreviewBudget();                     // fill 현재 여유 / 부족 badge now (and on each cap poll)
  $("sp-legend").innerHTML = legend([["#e6effd", "★ 대상"], ["#fffaf0", "■ 공유(dedup)"], ["#f3eefc", "↓ 의존"]])
    + '<span>합성 배포 DAG · 레벨 = 생성 순서</span>';
  fetch("/api/graph", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item.selection) })
    .then(r => r.json()).then(g => {
      if (host.dataset.preview !== item.id) return;   // user moved on while we fetched
      if (g.error || !g.nodes || !g.nodes.length) {
        $("sp-svg").innerHTML = '<text x="12" y="24" fill="#656d76">미리볼 합성 DAG가 없습니다.</text>'; return;
      }
      stagedScene = window.ResourceGraph.scene($("sp-svg"), $("sp-stage"), g, { hint: $("sp-hint") });
      stagedScene.start();
      $("sp-zin").onclick = () => stagedScene.zoomIn();
      $("sp-zout").onclick = () => stagedScene.zoomOut();
      $("sp-zfit").onclick = () => stagedScene.zoomToFit();
    }).catch(e => {
      if (host.dataset.preview === item.id)
        $("sp-svg").innerHTML = '<text x="12" y="24" fill="#cf222e">graph: ' + esc(e.message) + "</text>";
    });
}

// update ONLY the 현재 여유 number + 부족 badge in the open preview header, in place,
// from the latest /api/capacity (so the cap poll keeps it live without rebuilding the
// DAG scene). No-op unless a staged preview is the active 흐름 content.
function updateStagedPreviewBudget() {
  const host = $("report-main");
  if (!host || runId || !host.dataset.preview) return;
  const hr = $("sp-headroom"); if (!hr) return;
  const item = STAGED.find(x => x.id === host.dataset.preview);
  const ob = $("sp-overbadge");
  const c = lastCapacity;
  if (!c || !item) { hr.textContent = "…"; if (ob) ob.innerHTML = ""; return; }
  const headroom = c.headroom != null ? c.headroom
    : Math.max(0, (c.cap || 0) - ((c.baseline || 0) + (c.reserved || 0)));
  hr.textContent = headroom;
  if (ob) ob.innerHTML = (item.peak_vpcs || 0) > headroom
    ? '<span class="sp-over" title="필요 VPC > 여유 — 실행하면 대기 큐로 들어갑니다">여유 부족 → 대기 큐</span>' : "";
}

// [▶ 실행] — commit ONE staged item through the SAME pre-flight blast-radius 모달
// as the direct run button (Hard Rule 1: the deliberate opt-in = 선택 + pre-flight
// confirm). This path used to POST /api/run directly with no modal — the staged
// queue was a confirm-bypass hole (persona 2차 수용, 2026-07-04). On a confirmed
// launch the item leaves STAGED; postRun drives the report flow as before.
function runStaged(item) {
  preflightRun(item.selection, {
    onLaunched: () => {
      STAGED = STAGED.filter(x => x.id !== item.id);
      if (stagedOpen === item.id) stagedOpen = null;
      drawStagedPanel();
    }
  });
}

// ---- capacity bar (GET /api/capacity, polled ~2s while on the 실행 screen) ------
// The visible surface of the cross-run admission model: VPC budget (used/cap +
// headroom) + a 진행중 chip per running run and a 대기 chip per queued run. Clicking
// a running chip loads that run into the report. Light theme, compact; reuses the
// chip/kindtag styles. The poll timer is cleared in go() when leaving the screen.
let capTimer = null;
let lastCapacity = null;    // last /api/capacity payload (for the 강제 클린업 disable)
// P2C-24: capacity 는 30s 기본 (구 2s), 대기열이 있을 때만 5s (admit 관찰이 필요한
// 유일한 국면). /api/runs 동승 제거 — 종료 후 늦출현 감시는 startRunsWatch 로 분리.
function startCapPoll(immediate) {
  stopCapPoll();
  const tick = () => {
    if (screen !== "run") { capTimer = null; return; }
    if (document.hidden) { capTimer = setTimeout(tick, CAP_MS); return; }   // 숨은 탭 — 정지
    fetch("/api/capacity").then(r => r.json()).then(c => {
      if (c.error) return;
      lastCapacity = c;
      if (screen === "run") { drawCapBar(); drawStagedPanel(); drawLeftover(); updateStagedPreviewBudget(); }
    }).catch(() => { /* transient — keep last good capacity */ })
      .finally(() => {
        const queued = runStatus === "queued"
          || !!(lastCapacity && (lastCapacity.queued || []).length);
        if (screen === "run") capTimer = setTimeout(tick, queued ? CAP_QUEUED_MS : CAP_MS);
      });
  };
  capTimer = setTimeout(tick, immediate ? 0 : 1000);
}
function stopCapPoll() { if (capTimer) { clearTimeout(capTimer); capTimer = null; } }

// ---- 종료 후 실행 기록 감시 (P2C-24 분리) ------------------------------------
// +5m/+15m owned 재스캔 결과·늦출현(late_alert) 알림은 run 종료 뒤에 도착한다 —
// 종전엔 capacity 2s 폴에 /api/runs 를 동승시켜 폭주. 이제 종료 후에만 30s 주기.
let runsWatchTimer = null;
function startRunsWatch() {
  stopRunsWatch();
  const tick = () => {
    runsWatchTimer = null;
    if (screen !== "run") return;
    if (runStatus === "running" || runStatus === "queued") return;  // 라이브 = 이벤트 폴이 주도
    if (!document.hidden) loadRunRecords();
    runsWatchTimer = setTimeout(tick, RUNS_WATCH_MS);
  };
  runsWatchTimer = setTimeout(tick, RUNS_WATCH_MS);
}
function stopRunsWatch() { if (runsWatchTimer) { clearTimeout(runsWatchTimer); runsWatchTimer = null; } }

function drawCapBar() {
  const host = $("cap-bar"); if (!host) return;
  const c = lastCapacity;
  if (!c) {
    host.innerHTML = `<div class="panel cap-line">
      <b class="cap-t" title="VPC 동시 실행 한도(cap)">실행용량</b>
      <span class="muted small">⏳ 확인 중… (/api/capacity)</span></div>`;
    return;
  }
  const cap = c.cap || 0;
  const baseline = c.baseline || 0;          // 기존 — 서버 시작 시점 계정 VPC (내 실행 아님)
  const reserved = c.reserved || 0;          // 내 실행 예약 (in-flight)
  // 현재 계정 VPC = /v1/vpcs 실측(지금 실제 떠 있는 것). 내가 돌린 것 + 기존 + 타 세션 포함.
  const acct = c.account_live != null ? c.account_live : baseline;
  const headroom = c.headroom != null ? c.headroom : Math.max(0, cap - baseline - reserved);
  const running = c.running || [], queued = c.queued || [];
  const idTail = id => (id || "").slice(-6);
  // meter: one cell per cap slot. Fill by the LIVE account count first ('live' =
  // 지금 실제 떠 있는 VPC), then my not-yet-created reservations ('resv'), rest 여유.
  const liveN = Math.min(cap, acct);
  const resvN = Math.min(Math.max(0, cap - liveN), reserved);
  const cells = [];
  for (let i = 0; i < cap; i++) {
    const cls = i < liveN ? "live" : (i < liveN + resvN ? "resv" : "");
    cells.push(`<i class="${cls}"></i>`);
  }
  const runChips = running.length
    ? running.map(r => `<button class="capchip run" data-runid="${esc(r.id)}" title="${esc(r.id)} — 리포트 열기">
        <span class="kindtag">${esc(idTail(r.id))}</span> ${r.peak_vpcs || 0} VPC${r.heavy ? " 🜂" : ""}</button>`).join("")
    : '<span class="muted small">없음</span>';
  const queChips = queued.length
    ? queued.map(r => `<span class="capchip que" title="${esc(r.id)} — 여유가 생기면 자동 실행">
        <span class="kindtag">${esc(idTail(r.id))}</span> ${r.peak_vpcs || 0} VPC 필요 · 여유 ${headroom}</span>`).join("")
    : '<span class="muted small">없음</span>';
  // CX 재설계 (owner 2026-07-08): 세로 패널 → 한 줄 인라인. 진행중/대기는 비어
  // 있으면 아예 숨긴다 ("없음" 두 줄이 낭비 — 눈에 안 들어옴 지적). 상세 설명은
  // 전부 title 툴팁으로 유지.
  host.innerHTML = `<div class="panel cap-line">
    <b class="cap-t" title="VPC 동시 실행 한도(cap) — 이 아래에서 ADMIT 되거나 대기 큐로">실행용량</b>
    <span class="cap-meter mini" title="칸 = cap 슬롯 · 채움 = 지금 떠 있는 VPC · 노랑 = 내 예약">${cells.join("")}</span>
    <b title="지금 실제 떠 있는 실측 (/v1/vpcs · 내 실행 + 기존 포함)">VPC ${acct}/${cap}</b>
    <span class="muted small">기존 <b title="내 실행 소유가 아닌 계정 VPC (baseline) — 다른 세션·수동 생성분 포함">${baseline}</b>
      · 보유 <b title="내 실행이 지금 실제로 쥐고 있는 VPC (공유 VPC 포함)">${c.mine_live != null ? c.mine_live : 0}</b>
      · 예약 <b title="ADMIT 된 실행이 계획(peak VPC)상 선점한 슬롯 — 생성 전이어도 미리 차감">${reserved}</b>
      · 여유 <b title="cap − 기존 − max(예약, 보유) — 즉시 ADMIT 가능한 슬롯">${headroom}</b></span>
    ${running.length ? `<span class="cap-grp-in"><span class="cap-lbl">진행중</span>${runChips}</span>` : ""}
    ${queued.length ? `<span class="cap-grp-in"><span class="cap-lbl">대기</span>${queChips}</span>` : ""}
    ${(running.length + queued.length) ? `<button class="minibtn red" id="cap-abort-all"
        style="margin-left:auto"
        title="진행 중 + 대기 전체 중단 — 대기열을 먼저 비우고, 실행 중인 LIVE는 pytest 종료 + teardown 스윕 (확인 모달)">⏹ 전체 중단</button>` : ""}
  </div>`;
  els("#cap-bar .capchip[data-runid]").forEach(b => b.onclick = () => loadRunIntoReport(b.dataset.runid));
  const aa = $("cap-abort-all");
  if (aa) aa.onclick = () => abortAllConfirm(running.length, queued.length);
}

// ---- 전체 중단 (오너 요구 2026-07-11 "전체 시나리오 중단") -------------------
// abortConfirm(단일 런)의 전체판 — POST /api/abort-all. 서버가 대기열을 먼저
// 비우고(자동 재admit 두더지잡기 방지) 실행 중을 죽인다. 파괴적 동작이라
// pre-flight 모달 셸로 무엇이 일어나는지 명시한다.
function abortAllConfirm(nRun, nQue) {
  pfOpen(`⏹ 전체 중단 — 진행 ${nRun} · 대기 ${nQue}`);
  $("pf-body").innerHTML =
    '<p><b style="color:var(--red)">진행 중과 대기 중인 실행을 모두 중단합니다.</b></p>' +
    '<ul class="muted small" style="margin:6px 0 0;padding-left:18px">' +
    "<li><b>대기열을 먼저 비웁니다</b> (시작 전 취소 — 자동 재개 없음).</li>" +
    "<li>실행 중인 LIVE는 pytest 프로세스 트리를 종료하고 공유 VPC teardown + run-scoped 정리 스윕을 수행합니다.</li>" +
    "<li>각 run 은 <b>중단됨(aborted)</b> 으로 기록·미러되고, 종료 후 재스캔(+0·+5m·+15m)이 잔존을 감시합니다.</li>" +
    "<li>스캔·시뮬레이션 등 짧은 읽기 작업은 중단 대상이 아니라 건너뜁니다 (결과에 표시).</li></ul>";
  $("pf-foot").innerHTML =
    '<button class="btn ghost" id="pf-aa-cancel">취소</button>' +
    '<button class="btn warn" id="pf-aa-go">⏹ 전체 중단 실행</button>';
  $("pf-aa-cancel").onclick = pfClose;
  $("pf-aa-go").onclick = () => {
    $("pf-aa-go").disabled = true;
    $("pf-aa-go").textContent = "중단 요청 중…";
    fetch("/api/abort-all", { method: "POST" })
      .then(r => r.json().then(j => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        pfClose();
        if (!ok || j.error) { toast("전체 중단 실패: " + (j.error || "?"), "fail"); return; }
        const n = (j.aborted || []).length, sk = (j.skipped || []).length;
        toast(`전체 중단 요청됨 — ${n}건 중단${sk ? ` · ${sk}건 건너뜀(중단 비대상)` : ""}`,
              n ? "ok" : "fail");
        startCapPoll(true);
        if (runId) pollEvents();
      }).catch(e => { pfClose(); toast("전체 중단 요청 실패: " + e.message, "fail"); });
  };
}

// load a run (by id) into the master→detail report — shared by the cap-bar chips
// and the run-records list. Fetches the run's events, resets scope, and draws.
function loadRunIntoReport(id) {
  // status starts UNKNOWN ("…") until the fetch answers — a finished history
  // row must never flash "running" in the status tile (신규9).
  // A pending poll timer from a previously-watched ACTIVE run would fire
  // against the newly bound (already-ended) run and toast a spurious
  // "run 종료" (M1 review 2026-07-04) — cancel it on rebind.
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  runId = id; runEvents = []; evOffset = 0; runStatus = "…";
  detailScope = "*"; scopeAuto = true; expandedApi = null; apiCatFilter = "all"; apiShowAll = false;
  graphMode = "run"; ensureRunGraph();       // run 클릭 = run 뷰로 재바인딩 (F2)
  fetch("/api/runs/" + id + "/events?offset=0").then(r => r.json()).then(j => {
    runEvents = j.events || []; runStatus = j.status || "done";
    evOffset = j.next_offset != null ? j.next_offset : runEvents.length;
    runSelIds = j.lifecycle_ids || [];
    if (runStatus === "running" || runStatus === "queued") pollEvents();
    drawReport();
    renderNowPlaying();
  }).catch(() => drawReport());
}

// 남은 자원(잔존) — pre-flight panel: list owned (leftover) resources + force cleanup.
// "🔍 남은 자원 확인" → POST /api/owned, renders the returned list (service · path ·
// count) with a 없음 ✅ / N건 ⚠️ headline; 🧹 강제 클린업 → POST /api/cleanup; re-check.
function drawLeftover() {
  const host = $("leftover-panel");
  if (!host) return;
  // the 2s capacity poll re-renders this panel wholesale — preserve the 기지
  // 항목 fold's open state across re-renders or it snaps shut mid-read (L3)
  const stuckOpen = !!host.querySelector("details.lo-stuck[open]");
  const s = ownedScan;
  let head, list = "";
  if (!s) {
    head = '<span class="muted small">아직 확인하지 않음 — 실행 전 남은 자원을 점검하세요.</span>';
  } else if (s.status === "running" && !s.owned) {
    head = '<span class="muted small">⏳ 스캔 중… (read-only LIST)</span>';
  } else if (s.status === "error") {
    head = `<span class="lo-warn">스캔 실패: ${esc(s.error || "")}</span>`;
  } else {
    // known_issues.stuck_resources 매칭(기지 항목)은 접힘 그룹으로 — 빨간 카운트에
    // 절대 넣지 않는다 (/testing/resources 와 같은 folding, 신규8).
    const all = s.owned || [];
    const stuckRows = all.filter(o => o.known_stuck);
    const active = all.filter(o => !o.known_stuck);
    const n = active.length;
    // scan freshness is load-bearing: a cached scan mid-run showed stale rows
    // with no cue (2026-07-04) — show WHEN this inventory was measured, always.
    const ts = s.ended ? new Date(s.ended * 1000).toLocaleTimeString() : null;
    const stuckNote = stuckRows.length ? ` <span class="muted small">(기지 ${stuckRows.length}건 제외)</span>` : "";
    head = (n === 0
      ? `<span class="lo-ok">없음 ✅ — 남은 자원 0건${stuckNote}</span>`
      : `<span class="lo-warn">⚠️ ${n}건 — 실행 전 정리 권장${stuckNote}</span>`)
      + (ts ? ` <b style="margin-left:8px">🕒 마지막 스캔 ${ts}</b>` : "")
      + (s.status === "running" || s.rescanning
         ? ' <span class="muted small">· ⏳ 재스캔 중…</span>' : "");
    // 클린업 직후 재스캔에 여전히 삭제 가능 항목이 남았다면 의존 잠금 힌트 (신규7)
    if (cleanupJustRan && n > 0) {
      head += '<div class="lo-warn" style="margin-top:5px">의존 잠금 가능성 — 클린업 재실행 필요 '
        + '<span class="muted small">(자원이 의존 순서 때문에 이번 스윕에서 안 지워졌을 수 있음)</span></div>';
    } else if (cleanupJustRan && n === 0) {
      cleanupJustRan = false;                 // converged — hint no longer needed
    }
    if (n > 0) {
      // group by service for a service · path · count rollup
      const bySvc = {};
      active.forEach(o => {
        const k = o.service || "?";
        (bySvc[k] = bySvc[k] || {}).__n = (bySvc[k].__n || 0) + 1;
        bySvc[k][o.path] = (bySvc[k][o.path] || 0) + 1;
      });
      const rows = Object.keys(bySvc).sort().map(svc => {
        const paths = Object.keys(bySvc[svc]).filter(k => k !== "__n");
        return `<tr><td><b>${esc(svc)}</b></td>
          <td class="muted">${paths.map(p => esc(p) + (bySvc[svc][p] > 1 ? " ×" + bySvc[svc][p] : "")).join("<br>")}</td>
          <td class="ordn">${bySvc[svc].__n}</td></tr>`;
      }).join("");
      list = `<div class="scroll" style="max-height:200px;margin-top:7px"><table class="tbl">
        <thead><tr><th>service</th><th>path</th><th>count</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    }
    if (stuckRows.length) {
      list += `<details class="lo-stuck"${stuckOpen ? " open" : ""}><summary>기지 항목 ${stuckRows.length}건
          <span class="muted small">— 문서화된 잔존 (API로 삭제 불가 · known_issues.stuck_resources)</span></summary>
        ${stuckRows.map(o => `<div class="lo-stuck-row"><b>${esc(o.service || "?")}</b>
            <code>${esc(o.path || "")}</code>
            <span class="muted small">${esc((o.known_stuck && o.known_stuck.reason) || "")}</span></div>`).join("")}
      </details>`;
    }
  }
  // 종료 후 자원 늦출현 배너 (신규1) — 최근 알림을 패널 상단에 상시 고정
  const lateBanner = lateAlertBanner
    ? `<div class="lo-late">⚠ ${esc(lateAlertBanner)} <span class="muted small">— 아래 실측 목록 자동 갱신됨</span></div>`
    : "";
  // 강제 클린업 is account-wide (reaps by owner-tag) so the server BLOCKS it (409)
  // while any run is running/queued — grey it out with a tooltip while busy, and
  // surface any non-OK {error} inline (no alert/crash).
  const busy = !!(lastCapacity && ((lastCapacity.running || []).length || (lastCapacity.queued || []).length));
  // a run in flight makes the scan MISLEADING (its own in-progress resources show
  // up as 잔존, e.g. TGW/VPC rows mid-run 2026-07-04) — warn, don't let it read
  // as a leak report.
  const busyWarn = busy
    ? '<div class="lo-warn" style="margin-top:5px">⚠ 실행 중 — 실행 자원이 잔존으로 보일 수 있음, 종료 후 재스캔</div>'
    : "";
  // CX 재설계 (owner 2026-07-08): 세로 패널 → 한 줄 스트립 + 필요할 때만 펼침.
  // 상태기계(늦출현 배너 · 기지 접힘 · busy 경고 · 스캔 시각 · 의존잠금 힌트)는
  // 전부 보존 — 표시 밀도만 압축.
  const moreOpen = !!host.querySelector("details.lo-more[open]");
  host.innerHTML = `<div class="panel lo-line">
    ${lateBanner}
    <div class="lo-row">
      <b class="cap-t" title="실행 전 점검 (read-only) — owner 태그 자원의 잔존 여부. 강제 클린업은 TTL 무시 계정 전체 스윕">잔존</b>
      <span class="lo-head">${head}</span>
      ${busyWarn ? '<span class="lo-warn small">⚠ 실행 중 — 실행 자원이 잔존으로 보일 수 있음 (종료 후 재스캔)</span>' : ""}
      <span class="lo-act">
        <button class="minibtn" id="lo-scan">🔍 확인</button>
        <button class="minibtn red" id="lo-cleanup" ${busy ? "disabled" : ""}
          title="${busy ? "진행 중 실행이 있어 비활성화" : "owner=apitest 자원을 TTL 무시하고 삭제"}">🧹 클린업</button>
        ${s && s.owned_total != null ? '<button class="minibtn" id="lo-recheck" title="다시 확인">↻</button>' : ""}
      </span>
    </div>
    ${list ? `<details class="lo-more"${moreOpen ? " open" : ""}><summary class="muted small">상세 목록 펼치기</summary>${list}</details>` : ""}
    <div class="lo-err" id="lo-err" style="display:none"></div>
  </div>`;
  $("lo-scan").onclick = scanOwned;
  if ($("lo-recheck")) $("lo-recheck").onclick = scanOwned;
  $("lo-cleanup").onclick = () => {
    if (busy) return;
    const errEl = $("lo-err"); if (errEl) errEl.style.display = "none";
    // blind confirm 대신 fresh /api/owned 스캔이 채운 '삭제 대상 N건' 모달
    cleanupConfirm(j => {
      runId = j.id; runEvents = []; runStatus = "running"; detailTab = "log"; scopeAuto = true;
      drawReport(); startR4Poll();
      watchCleanup(j.id);   // 종료를 기다렸다가 실측 재스캔 (1.2s 후 조기 스캔 금지)
    });
  };
}

// force-cleanup 이 실제로 END난 뒤에 owned 재스캔 (신규7): 스윕은 몇 분 걸리는데
// 예전 코드는 1.2초 뒤에 재스캔해 '여전히 N건'이라는 낡은 스냅샷을 보여줬다.
// 재스캔 결과에 삭제 가능 항목이 남아 있으면 drawLeftover 가 의존 잠금 힌트를 단다.
function watchCleanup(id) {
  const poll = () => fetch("/api/runs/" + id).then(r => r.json()).then(rec => {
    if (rec.status === "running" || rec.status === "queued") { setTimeout(poll, 2000); return; }
    cleanupJustRan = true;
    toast("강제 클린업 종료 — 남은 자원 실측 재스캔 중…");
    scanOwned();
  }).catch(() => setTimeout(poll, 3000));
  setTimeout(poll, 2000);
}

// trigger the owned-resource scan (POST /api/owned) and poll its record for the list.
// 재스캔 중에도 직전 완료 결과(목록+시각)를 유지한다 — 패널이 스피너로 비지 않게.
function scanOwned() {
  const prev = (ownedScan && ownedScan.status === "done") ? ownedScan : null;
  ownedScan = prev
    ? Object.assign({}, prev, { status: "running", rescanning: true })
    : { status: "running" };
  drawLeftover();
  fetch("/api/owned", { method: "POST" }).then(r => r.json()).then(j => {
    if (j.error) { ownedScan = { status: "error", error: j.error }; drawLeftover(); return; }
    pollOwned(j.id);
  }).catch(e => { ownedScan = { status: "error", error: e.message }; drawLeftover(); });
}
function pollOwned(id) {
  fetch("/api/runs/" + id).then(r => r.json()).then(j => {
    if (j.status === "running") {
      // keep showing the previous completed inventory while the re-scan runs
      if (ownedScan) ownedScan.rescanning = true;
      if (screen === "run") drawLeftover();
      setTimeout(() => pollOwned(id), 800);
      return;
    }
    ownedScan = { status: j.status, owned: j.owned || [], owned_total: j.owned_total,
                  error: j.error, ended: j.ended };
    if (j.status === "done") {
      try { sessionStorage.setItem(OWNED_KEY, JSON.stringify(ownedScan)); } catch (e) { /* quota */ }
    }
    if (screen === "run") drawLeftover();
  }).catch(() => { ownedScan = { status: "error", error: "연결 실패" }; if (screen === "run") drawLeftover(); });
}

// (실행 설정 패널은 CX 재설계로 폐지 — 게이트 칩 + LIVE 직접실행은 대기열
// 스트립의 빈-상태 줄이, 확정 게이트는 pre-flight confirm 모달이 담당한다.)

// ================= pre-flight blast-radius 모달 (native confirm 대체) ==========
// 실행 전 '무엇이 얼마나 만들어지고 지워지는가'를 서비스 단위 표(생성~삭제 예상 ·
// 실측 ETA · 과금 배지)로 보여주고, heavy(과금)가 있으면 명시 체크 후에만 LIVE 가
// 열린다. plan/capacity 사전 점검 실패 = 실행 '완전 차단' — [다시 점검]만 제공
// (우회 confirm 없음, env 탈출구 없음). 디스패치 성공 시 모달은 성공 상태로 남아
// /runtime?scope=mine(내 실행 활동 흐름) 링크를 제공한다.

function pfEnsure() {
  if ($("pf-modal")) return;
  const scrim = document.createElement("div");
  scrim.className = "scrim"; scrim.id = "pf-scrim";
  const modal = document.createElement("div");
  modal.className = "modal"; modal.id = "pf-modal";
  modal.innerHTML = '<div class="mh"><h3 id="pf-title"></h3>' +
    '<button class="mclose" id="pf-close">×</button></div>' +
    '<div class="mbody" id="pf-body"></div>' +
    '<div class="mfoot" id="pf-foot"></div>';
  document.body.appendChild(scrim); document.body.appendChild(modal);
  $("pf-close").onclick = pfClose;
  scrim.onclick = pfClose;
}
function pfOpen(title) {
  pfEnsure();
  $("pf-modal").classList.remove("sim-wide");   // 시뮬 간트가 넓힌 모달 폭 리셋
  $("pf-title").textContent = title;
  $("pf-body").innerHTML = ""; $("pf-foot").innerHTML = "";
  $("pf-modal").classList.add("open"); $("pf-scrim").classList.add("open");
}
function pfClose() {
  if ($("pf-modal")) { $("pf-modal").classList.remove("open"); $("pf-scrim").classList.remove("open"); }
}
function fmtDur(s) {
  if (s == null) return "미측정";
  if (s < 90) return Math.round(s) + "초";
  if (s < 5400) return (s / 60).toFixed(1) + "분";
  return (s / 3600).toFixed(1) + "시간";
}

// Runs are always LIVE. Before posting, fetch the plan + capacity (parallel) and
// show the pre-flight blast-radius modal. On [LIVE 실행], POST /api/run (mode live;
// the server derives the gates) and drive the existing report flow.
function startRun() {
  if (!targets.size) return;
  preflightRun(selectionPayload());
}

// the ONE pre-flight gate every UI path to POST /api/run goes through — the direct
// run button, the staged queue's [▶ 실행] and the 대기열 미리보기's [▶ 실행] all
// funnel here (선택 요약 · heavy 명시 확인 · 남은 자원/용량 경고 포함).
// `opts.onLaunched(rec)` fires after a successful dispatch (e.g. drop staged item).
function preflightRun(sel, opts) {
  opts = opts || {};
  pfOpen("⚠ LIVE 실행 사전 점검 (blast radius)");
  $("pf-body").innerHTML = '<p class="muted small">사전 점검 중… (plan + capacity)</p>';
  Promise.all([
    fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sel) }).then(r => r.json()),
    fetch("/api/capacity").then(r => r.json()),
    // §3 견적(병렬 makespan p50~p90) — 합계 ETA용. 실패해도 모달은 순차합산으로 동작.
    fetch("/api/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sel) })
      .then(r => (r.ok ? r.json() : null)).catch(() => null),
  ]).then(([plan, capacity, pf]) => {
    plan = plan || {}; capacity = capacity || {};
    // preflight FAILURE = 실행 차단 (우회 없음) — 이유 + [다시 점검]만.
    if (plan.error || capacity.error || capacity.headroom == null) {
      pfFail(plan.error || capacity.error || "capacity 응답이 불완전합니다 (headroom 없음)", sel, opts);
      return;
    }
    opts.pf = pf && !pf.error ? pf : null;
    pfRender(plan, capacity, sel, opts);
  }).catch(e => pfFail(e.message, sel, opts));
}

// preflight 실패 상태: 실행 경로 없음 — 이유 + [다시 점검] 버튼만.
function pfFail(msg, sel, opts) {
  $("pf-body").innerHTML =
    '<p><b style="color:var(--red)">사전 점검(plan/capacity) 실패 — 실행이 차단되었습니다.</b></p>' +
    '<p class="muted small">' + esc(msg || "") + "</p>" +
    '<p class="muted small">서버가 계획/용량을 답하지 못하면 blast radius 를 알 수 없어 ' +
    "LIVE 실행을 허용하지 않습니다 (우회 없음).</p>";
  $("pf-foot").innerHTML = '<button class="btn" id="pf-retry">↻ 다시 점검</button>';
  $("pf-retry").onclick = () => preflightRun(sel || selectionPayload(), opts);
}

function pfRender(plan, capacity, sel, opts) {
  opts = opts || {};
  const N_lc = plan.runnable ? plan.runnable.length : (plan.lifecycle_ids ? plan.lifecycle_ids.length : 0);
  const peak = plan.peak_vpcs || 0;
  const headroom = capacity.headroom != null ? capacity.headroom : 0;
  // 서비스 단위 집계 (per-lifecycle preview → service rows)
  const bySvc = {}; const heavyIds = [];
  let tCreates = 0, tDeletes = 0, tDur = 0, tMeasured = 0, tN = 0;
  Object.keys(plan.preview || {}).sort().forEach(lid => {
    const p = plan.preview[lid] || {};
    // 그룹 키는 짧은 이름으로 정규화 — 태그 표기가 "virtualserver" 와
    // "compute/virtualserver" 로 갈려도 한 서비스는 한 행이어야 한다.
    const svc = shortName(p.service || "?");
    const a = bySvc[svc] = bySvc[svc] || { n: 0, creates: 0, deletes: 0, dur: 0, measured: 0, heavy: 0 };
    a.n++; tN++;
    a.creates += p.est_creates || 0; tCreates += p.est_creates || 0;
    a.deletes += p.est_deletes || 0; tDeletes += p.est_deletes || 0;
    if (p.duration_s != null) { a.dur += p.duration_s; a.measured++; tDur += p.duration_s; tMeasured++; }
    if (p.heavy) { a.heavy++; heavyIds.push(lid); }
  });
  const eta = a => !a.measured ? '<span class="muted">미측정</span>'
    : "~" + fmtDur(a.dur) + (a.measured < a.n ? ' <span class="muted small">(미측정 ' + (a.n - a.measured) + ")</span>" : "");
  // 표 안 서비스 표기는 한 형태로 통일: 짧은 이름 (신규9). 생성/삭제 예상은
  // "26 ~ 15"(범위로 오독)가 아니라 "생성 ~26 · 삭제 ~15"로.
  const rows = Object.keys(bySvc).sort().map(svc => {
    const a = bySvc[svc];
    return "<tr><td><b>" + esc(shortName(svc)) + "</b></td><td>" + a.n + "</td>" +
      "<td>생성 ~" + a.creates + " · 삭제 ~" + a.deletes + "</td><td>" + eta(a) + "</td>" +
      "<td>" + (a.heavy ? '<span style="color:var(--red);font-weight:700">⚠️과금 ' + a.heavy + "</span>" : "—") + "</td></tr>";
  }).join("");
  const nSvc = Object.keys(bySvc).length;
  const heavy = heavyIds.length > 0;
  // 합계 ETA (병렬 makespan p50~p90 우선, 없으면 순차 합산) — 요약줄·표 tfoot 공유.
  const etaText = (opts.pf && opts.pf.est && opts.pf.est.p50_s != null)
    ? "~" + fmtDur(opts.pf.est.p50_s) + " ~ " + fmtDur(opts.pf.est.p90_s)
    : (tMeasured ? "~" + fmtDur(tDur) : "미측정");
  const gates =
    '<div class="chiprow" style="margin:2px 0 8px">' +
    '<span class="chip" style="border-color:var(--red)">✔ mutations ON</span>' +
    '<span class="chip" style="border-color:var(--red)">✔ destructive ON</span>' +
    '<span class="chip" style="border-color:' + (heavy ? "var(--red)" : "var(--line)") + '">' +
    (heavy ? "✔" : "✕") + " 과금 라이프사이클 " + heavyIds.length + "</span></div>";
  // blast radius 한눈에 — 세부(표·과금 목록)는 접고 이 요약줄을 1급으로 (오너:
  // "목록이 잘 보이지도 않고 · 세부는 접혀있고"). 실행 직전 '무엇이 얼마나'가 한 줄.
  const summary =
    '<div class="pf-sum">' +
    '<span><b>' + N_lc + '</b> lifecycle</span>' +
    '<span>생성 <b>~' + tCreates + '</b></span>' +
    '<span>삭제 <b>~' + tDeletes + '</b></span>' +
    '<span>ETA <b>' + etaText + '</b></span>' +
    '<span>VPC peak <b>' + peak + '</b> / 여유 ' + headroom + '</span>' +
    (heavy ? '<span class="pf-sum-heavy">⚠️ 과금 <b>' + heavyIds.length + '</b></span>'
           : '<span class="pf-sum-ok">과금 없음</span>') +
    '</div>';
  const queueNote = peak > headroom
    ? '<p class="muted small" style="color:var(--amber)">→ VPC 여유(' + headroom + ') 초과: 즉시 실행되지 않고 <b>대기 큐</b>에 들어갑니다.</p>' : "";
  const skipped = (plan.skipped_disabled || []).length
    ? '<p class="muted small">disabled 로 건너뜀: ' + plan.skipped_disabled.map(esc).join(", ") + "</p>" : "";
  // P2C-26: 선택한 리소스가 계획에서 조용히 빠지면 반드시 보여준다 (stale 매핑/
  // 비활성 — "3개 선택했는데 iam만 실행" 실측 구멍의 재발 방지). 경고는 접지 않는다.
  const dropped = (plan.dropped || []).length
    ? '<div class="note" style="border-left-color:var(--amber)"><b style="color:var(--amber)">⚠ 선택 중 ' +
      plan.dropped.length + '개 리소스가 계획에 포함되지 못했습니다:</b><br>' +
      plan.dropped.map(d => "<code>" + esc(d.node) + "</code> — " + esc(d.why)).join("<br>") + "</div>"
    : "";
  // 세부 1 (접힘): 서비스별 표 + tfoot 합계 + ETA 설명.
  const tableDetails =
    '<details class="pf-det"><summary>서비스별 상세 · ' + nSvc + " 서비스</summary>" +
    '<div class="pf-det-body"><table class="tbl"><thead><tr><th>service</th><th>lifecycle</th><th>생성·삭제 예상</th><th>실측 ETA</th><th>과금</th></tr></thead>' +
    "<tbody>" + rows + "</tbody>" +
    '<tfoot><tr class="lc-head"><td>합계</td><td>' + N_lc + "</td><td>생성 ~" + tCreates + " · 삭제 ~" + tDeletes + "</td><td>" +
    (opts.pf && opts.pf.est && opts.pf.est.p50_s != null
      ? "~" + fmtDur(opts.pf.est.p50_s) + ' <span class="muted small">~ ' + fmtDur(opts.pf.est.p90_s) +
        " (병렬 makespan · " + esc(opts.pf.est.basis || "?") + ")</span>"
      : (tMeasured ? "~" + fmtDur(tDur) + (tMeasured < tN ? ' <span class="muted small">(미측정 ' + (tN - tMeasured) + ")</span>" : "") : '<span class="muted">미측정</span>')) +
    "</td><td>" + (heavy ? '<span style="color:var(--red);font-weight:700">⚠️ ' + heavyIds.length + "</span>" : "—") + "</td></tr></tfoot></table>" +
    '<p class="muted small" style="margin:7px 0 0">행 ETA = 라이프사이클 실측 평균의 순차 합산 · <b>합계 ETA = 병렬 makespan 추정</b>' +
    (opts.pf ? "" : " (견적 API 미응답 — 순차 합산 표시)") + "</p></div></details>";
  // 세부 2 (접힘, heavy 일 때만): 과금 라이프사이클 목록.
  const heavyDetails = heavy
    ? '<details class="pf-det"><summary style="color:var(--red)">과금 라이프사이클 ' + heavyIds.length + "개</summary>" +
      '<div class="pf-det-body pf-heavy-list">' + heavyIds.map(id => "<code>" + esc(id) + "</code>").join(" · ") + "</div></details>"
    : "";
  // 확인 게이트 (접지 않음 — 실행 직전 명시 opt-in, Hard Rule 1). heavy 일 때만.
  const heavyConfirm = heavy
    ? '<label class="pf-confirm"><input type="checkbox" id="pf-heavy-ok"> <b>⚠️ 과금 실행임을 확인했습니다</b></label>'
    : "";
  $("pf-body").innerHTML =
    '<p class="muted small">실제 클라우드 자원을 만들고 삭제합니다 — 게이트는 선택(의존 폐쇄집합)에서 파생됩니다.</p>' +
    gates + summary + queueNote + skipped + dropped + tableDetails + heavyDetails + heavyConfirm;
  $("pf-foot").innerHTML =
    '<span class="muted small">취소해도 선택은 유지됩니다.</span>' +
    '<button class="btn ghost" id="pf-cancel">취소</button>' +
    '<button class="btn warn" id="pf-go"' + (heavy ? " disabled" : "") + ">⚠ LIVE 실행 ▶</button>";
  $("pf-cancel").onclick = pfClose;
  if (heavy) $("pf-heavy-ok").onchange = e => { $("pf-go").disabled = !e.target.checked; };
  $("pf-go").onclick = () => {
    $("pf-go").disabled = true;
    $("pf-go").textContent = "실행 요청 중…";
    postRun(sel, (err, j) => {
      if (err) {
        $("pf-body").innerHTML = '<p><b style="color:var(--red)">실행 실패:</b> ' + esc(err) + "</p>";
        $("pf-foot").innerHTML = '<button class="btn ghost" id="pf-cancel2">닫기</button>';
        $("pf-cancel2").onclick = pfClose;
        return;
      }
      if (opts.onLaunched) opts.onLaunched(j);
      // 성공 확인 창 제거 (오너: "이 창은 없어도 될 듯 · 필요하면 내가 알아서
      // 리포트 볼게"). postRun 이 이미 run 화면으로 전환·리포트를 렌더 중이므로,
      // 모달을 닫으면 그 리포트가 바로 드러난다 — 중간 확인 창은 중복이었다.
      const queued = j && j.status === "queued";
      pfClose();
      toast((queued ? "⌛ 대기 큐 등록 — run " : "✅ LIVE 실행 시작 — run ") + j.id, "ok");
    });
  };
}

// ---- 강제 클린업 confirm 업그레이드 (item 4/5 정합): blind confirm 대신 fresh
// /api/owned 스캔이 채운 '삭제 대상 N건' 목록 모달 — pre-flight 모달과 같은 셸.
// 스캔이 실패하면 클린업도 차단([다시 점검]만). onStarted(rec) = 클린업 run 시작 후.
function cleanupConfirm(onStarted) {
  pfOpen("🧹 강제 클린업 — 계정 전체 (owner=apitest)");
  $("pf-body").innerHTML = '<p class="muted small">⏳ 삭제 대상 스캔 중… (read-only 인벤토리 /api/owned)</p>';
  fetch("/api/owned", { method: "POST" }).then(r => r.json()).then(j => {
    if (j.error) return cleanupScanFail(j.error, onStarted);
    const poll = () => fetch("/api/runs/" + j.id).then(r => r.json()).then(rec => {
      if (rec.status === "running") return setTimeout(poll, 800);
      if (rec.status !== "done") return cleanupScanFail(rec.error || "스캔 실패", onStarted);
      cleanupRender(rec.owned || [], onStarted);
    }).catch(e => cleanupScanFail(e.message, onStarted));
    poll();
  }).catch(e => cleanupScanFail(e.message, onStarted));
}
function cleanupScanFail(msg, onStarted) {
  $("pf-body").innerHTML =
    '<p><b style="color:var(--red)">삭제 대상 스캔 실패 — 강제 클린업이 차단되었습니다.</b></p>' +
    '<p class="muted small">' + esc(msg || "") + "</p>";
  $("pf-foot").innerHTML = '<button class="btn ghost" id="pf-cl-cancel">취소</button>' +
    '<button class="btn" id="pf-rescan">↻ 다시 점검</button>';
  $("pf-cl-cancel").onclick = pfClose;
  $("pf-rescan").onclick = () => cleanupConfirm(onStarted);
}
function cleanupRender(owned, onStarted) {
  const n = owned.length;
  const rows = owned.map(o => "<tr><td><b>" + esc(o.service || "?") + "</b></td><td><code>" +
    esc(o.path || "") + "</code></td></tr>").join("");
  $("pf-body").innerHTML =
    "<p><b>삭제 대상 " + n + "건</b> <span class=\"muted small\">— 방금 실측한 owned 인벤토리 (owner-tag 전체, TTL 무시)</span></p>" +
    (n ? '<div style="max-height:280px;overflow:auto"><table class="tbl"><thead><tr><th>service</th><th>path</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
       : '<p class="muted small">지울 것이 없습니다 — 계정이 이미 깨끗합니다 ✅</p>') +
    '<p class="muted small">우리 소유가 아닌 자원은 절대 건드리지 않습니다.</p>';
  $("pf-foot").innerHTML =
    '<button class="btn ghost" id="pf-cl-cancel">취소</button>' +
    '<button class="btn warn" id="pf-cl-go"' + (n ? "" : " disabled") + ">삭제 대상 " + n + "건 — 강제 클린업 실행</button>";
  $("pf-cl-cancel").onclick = pfClose;
  $("pf-cl-go").onclick = () => {
    $("pf-cl-go").disabled = true;
    fetch("/api/cleanup", { method: "POST" }).then(r => r.json().then(j => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok || j.error) return cleanupScanFail(j.error || "강제 클린업 실패", onStarted);  // 409 포함
      pfClose();
      if (onStarted) onStarted(j);
    }).catch(e => cleanupScanFail("서버 연결 실패: " + e.message, onStarted));
  };
}

// POST /api/run (always mode live) and drive the existing report flow. Tolerates a
// "queued" status (pollEvents shows the wait banner until it flips to running).
// `cb(err, rec)` (optional) lets the pre-flight modal show its success/fail state.
function postRun(sel, cb) {
  const body = Object.assign({ mode: "live" }, sel);
  if (screen !== "run") go("run");
  $("report-main").innerHTML = '<p class="muted small">실행 요청 중…</p>';
  fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })))
    .then(({ ok, status, j }) => {
      if (!ok || j.error) {
        const msg = j.error || ("실행 실패 (HTTP " + status + ")");
        // 409 = 중복 admit 가드 (이미 LIVE 실행 진행/대기 중) — 토스트로 명확히
        toast((status === 409 ? "⚠ " : "") + msg, "fail");
        $("report-main").innerHTML = '<p class="empty">실행 실패: ' + esc(msg) + "</p>";
        if (cb) cb(msg, null); return;
      }
      runId = j.id; runEvents = []; evOffset = 0; runStatus = j.status || "running";
      detailScope = "*"; detailTab = "res"; scopeAuto = true; expandedApi = null; apiCatFilter = "all"; apiShowAll = false;   // fresh run → reconcile auto-selects
      loadRunRecords();   // P2C-24: /api/runs 는 이벤트 시점(시작)에만 — 폴 동승 제거
      graphMode = "run"; ensureRunGraph();   // 흐름 = 이 run 의 그래프 (F1)
      // A QUEUED run has no events / no live scene yet: show the wait banner and let
      // pollEvents own the report until it flips to running (drawReport here would
      // build+discard the r1 scene and momentarily show a misleading "완료" banner).
      if (runStatus === "queued") {
        $("report-main").innerHTML =
          '<div class="nowbar"><span class="dot"></span><b>대기 큐에서 대기 중</b> — 여유가 생기면 자동 실행</div>';
        $("lc-picker").innerHTML = "";
      } else {
        drawReport();
      }
      pollEvents();
      drawCapBar();   // reflect the new run in the capacity bar (refreshes on next poll)
      // 실행 admit → ② 히어로(현재 실행)로 자동 포커스: go("run") 은 위에서 보장,
      // 화면이 길 때 히어로가 뷰포트에 들어오게 스크롤까지 (CX 재배치 4).
      try {
        const hero = $("md-report");
        if (hero && hero.scrollIntoView) hero.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) { /* older browsers — non-fatal */ }
      if (cb) cb(null, j);
    }).catch(e => { $("report-main").innerHTML = '<p class="empty">실행 연결 실패: ' + esc(e.message) + "</p>"; if (cb) cb(e.message, null); });
}

// ---- poll the live event stream until run-end / status done ----
// P2C-24 폴링 다이어트: 단일 tick EV_TICK_MS(2s) + 증분 fetch(?offset=N — 서버가
// tail 만 보낸다) + 숨은 탭 정지. 구 700ms 전체-재fetch 는 백엔드 폭주 + 매 폴
// 전체 재렌더(깜빡임)의 근원이었다 (오너 실측 2026-07-09).
function pollEvents() {
  if (!runId) return;
  if (pollTimer) clearTimeout(pollTimer);
  if (document.hidden) { pollTimer = setTimeout(pollEvents, HIDDEN_RETRY_MS); return; }
  const reqId = runId;
  fetch("/api/runs/" + runId + "/events?offset=" + evOffset).then(r => r.json()).then(j => {
    if (runId !== reqId) return;             // 폴 도중 run 재바인딩 — 응답 폐기
    const tail = j.events || [];
    if (j.next_offset != null) {
      // 증분 계약: offset==0 응답 = 전체 재전송(리셋/구간 초과) → 교체, 아니면 append
      runEvents = j.offset === 0 ? tail : runEvents.concat(tail);
      evOffset = j.next_offset;
    } else {
      runEvents = tail;                      // 구버전 서버 폴백 — 전체 응답
    }
    runStatus = j.status || runStatus;
    if (j.lifecycle_ids) runSelIds = j.lifecycle_ids;
    // A run admitted under the cap is "running"; one that exceeded the cap is
    // "queued" — no events yet. Show a waiting banner and keep polling the record
    // (cheap, robust) until it flips to running, then the normal event flow takes
    // over. Either state is "in flight" (not ended).
    if (runStatus === "queued") {
      if (screen === "run") {
        $("report-main").innerHTML =
          '<div class="nowbar"><span class="dot"></span><b>대기 큐에서 대기 중</b> — 여유가 생기면 자동 실행</div>';
        renderLcPicker();
      }
      pollTimer = setTimeout(pollEvents, EV_TICK_QUEUED_MS);
      return;
    }
    const ended = runEvents.some(e => e.kind === "run-end")
      || (runStatus !== "running" && runStatus !== "queued");
    if (screen === "run") drawReport();
    renderNowPlaying();
    if (!ended) pollTimer = setTimeout(pollEvents, EV_TICK_MS);
    else {
      runStatus = runStatus === "running" ? "done" : runStatus;
      if (screen === "run") drawReport();
      onRunEnded();
    }
  }).catch(() => { pollTimer = setTimeout(pollEvents, EV_TICK_MS * 2); });
}

// run 종료 시 1회: 완료/실패 토스트 + (열려 있으면) 로그 자동 새로고침 (F3/신규3).
function onRunEnded() {
  renderNowPlaying();
  if (!runId || endToastShown[runId]) return;
  endToastShown[runId] = true;
  // v2 접목 3 (§2.9 C층): 토스트 대신 "다음 행동 카드" — 종료 요약은 사라지지
  // 않고, fail/+검증(fold)/잔존 각각에 다음 행동을 단다. 토스트는 유지하되
  // 짧은 확인용으로만 (카드가 본체).
  const st = lifecycleStates();
  const ids = Object.keys(st);
  const passed = ids.filter(l => st[l] === "done").length;
  const failed = ids.filter(l => st[l] === "fail");
  // 미종료(중단/크래시 — lifecycle-end 없음)는 정직하게 별도 표기: "3/7 passed"
  // 만 보이면 나머지가 통과처럼 읽힌다 (리뷰 후속).
  const unfin = ids.filter(l => st[l] === "running" || st[l] === "queued").length;
  let msg = `run 종료: ${passed}/${ids.length} passed`;
  if (failed.length) msg += ` — ${failed.length} failed`;
  if (unfin) msg += ` — ${unfin} 미종료(중단)`;
  toast(msg, (failed.length || unfin) ? "fail" : "ok");
  renderDoneCard({ passed, total: ids.length, failed, unfin });
  if (screen === "run" && detailTab === "log" && isAggScope()) loadLog(true);
  loadRunRecords();
  startRunsWatch();   // P2C-24: 종료 후 +5m/+15m 재스캔·늦출현 감시 (30s 주기)
}

// ---- 종료 후 다음 행동 카드 (v2 접목 3 — §2.9 C층, donor: run_exec.js/목업) ---
// 3줄: ① fail → 레일 fail 필터 ② +검증 → fold 안내(공식 미반영 증거 약 N건,
// 절차는 안내만 — 자동 실행 없음, Hard Rule 7) ③ 잔존 → 재스캔 확인.
// + 회고 1줄: PLAN(접목 2의 runPlan) 대비 실제 생성/경과.
function renderDoneCard(sum) {
  const host = $("donecard"); if (!host) return;
  const rid = runId;
  const created = runEvents.filter(e => e.kind === "resource-tracked").length;
  const deleted = runEvents.filter(e => e.kind === "resource-deleted").length;
  const prog = runProgress();
  let retro = `생성 ${created} · 삭제 ${deleted}` +
    (prog.elapsed != null ? ` · 경과 ${fmtElapsed(prog.elapsed)}` : "");
  if (runPlan && !runPlan._failed && runPlanFor === rid) {
    // 회고의 예측도 스트립·간트 패널과 같은 단일 소스(schedule-sim makespan)
    const t = planTotals(runPlan);
    const sim = (pvaSimFor === rid && pvaSim && !pvaSim.error && pvaSim.makespan_s) ? pvaSim : null;
    retro = `계획 생성 ~${t.creates}${sim ? ` · 예측 ~${fmtDur(sim.makespan_s)}` : ""} → 실제 ${retro}`;
  }
  const failRow = sum.failed.length
    ? `<div class="dc-row bad"><span class="dc-k">fail</span> <b>${sum.failed.length}건</b> — ${sum.failed.slice(0, 4).map(esc).join(", ")}${sum.failed.length > 4 ? " …" : ""}
       <button class="minibtn" id="dc-fails" title="레일을 fail만 보기로 전환">→ 실패만 보기</button></div>`
    : `<div class="dc-row ok"><span class="dc-k">fail</span> 0건 — 전부 통과 ✅</div>`;
  const unfinRow = sum.unfin
    ? `<div class="dc-row bad"><span class="dc-k">미종료</span> ${sum.unfin}건 (중단/크래시) — 로그 tab에서 마지막 step 확인</div>` : "";
  setHtmlIfChanged(host,
    `<div class="dc-head"><b>run 종료 — ${sum.passed}/${sum.total} passed</b>
       <span class="muted small">${retro}</span>
       <button class="minibtn dc-x" id="dc-close" title="카드 닫기">✕</button></div>`
    + failRow + unfinRow
    + `<div class="dc-row"><span class="dc-k">+검증</span> <span id="dc-fold">공식 미반영 검증 증거 계산 중…</span></div>`
    + `<div class="dc-row"><span class="dc-k">잔존</span> 종료 후 재스캔(+5m/+15m)이 늦출현을 감시합니다 —
         <button class="minibtn" id="dc-owned" title="지금 바로 실측 스캔 (POST /api/owned, 30~90초)">🔍 지금 확인</button></div>`);
  host.classList.remove("hidden");
  $("dc-close").onclick = () => { host.classList.add("hidden"); host._h = null; };
  const fb = $("dc-fails");
  if (fb) fb.onclick = () => { go("run"); railFilter = "fail"; renderLcPicker(); };
  $("dc-owned").onclick = () => { go("run"); scanOwned(); };
  // +검증 줄 — fold evidence (서버 계산: 시간창 근사라 '약 N건', 실행은 안내만)
  fetch("/api/runs/" + encodeURIComponent(rid) + "/fold-evidence")
    .then(r => r.json()).then(j => {
      const el = $("dc-fold"); if (!el || runId !== rid) return;
      if (!j || j.error || j.available === false) {
        el.innerHTML = `계산 불가 — ${esc((j && (j.reason || j.error)) || "?")}`;
        return;
      }
      el.innerHTML = j.count === 0
        ? "이 런의 2xx 관측은 (시간창 근사) 이미 발행 집계에 반영되어 있습니다"
        : `<b>공식 미반영 검증 증거 약 ${j.count}건</b>
           <button class="minibtn" id="dc-fold-how" title="fold 절차 안내 — 자동 실행 없음">공식 반영 절차</button>`;
      const hb = $("dc-fold-how");
      if (hb) hb.onclick = () => foldHowModal(j);
    }).catch(() => { const el = $("dc-fold"); if (el) el.textContent = "계산 불가 (요청 실패)"; });
}

// fold 절차 모달 — v2 run_detail.html §2.4 안내의 이식. 콘솔은 fold를 실행하지
// 않는다: 절차(derive_verified → promote_validated → 검토 커밋)만 보여준다.
function foldHowModal(j) {
  pfOpen("공식 반영(fold) — 절차 안내");
  const rows = (j.preview || []).map(p =>
    `<tr><td><code>${esc(p.endpoint_key)}</code></td><td><code>${esc(p.status)}</code></td></tr>`).join("");
  $("pf-body").innerHTML =
    `<p><b>공식 미반영 검증 증거 약 ${j.count}건</b> <span class="muted small">— 런 시간창(±30s) 내
       2xx 관측 중 발행본 verified_endpoints.json에 없는 endpoint (시간창 근사라 '약')</span></p>`
    + (rows ? `<div class="scroll" style="max-height:180px"><table class="tbl"><thead><tr><th>endpoint</th><th>status</th></tr></thead><tbody>${rows}</tbody></table></div>` : "")
    + (j.truncated ? `<p class="muted small">미리보기 상한 — 전체 약 ${j.count}건 중 일부만 표시.</p>` : "")
    + `<p style="margin-top:10px"><b>절차 (이 콘솔은 실행하지 않음 — 검토 커밋 경로 유지)</b></p>
       <ol class="muted small" style="margin:4px 0 0;padding-left:18px">
         <li><code>python -m tools.derive_verified</code> — 2xx 관측을 evidence(data/baselines/verified_endpoints.json)에 병합</li>
         <li><code>python -m tools.promote_validated --apply</code> — evidence 근거로 모델 provenance VALIDATED 승격</li>
         <li>변경분 검토 후 커밋·push — 다음 CI 턴에 발행 파이프라인이 공식 집계에 반영</li>
       </ol>`;
  $("pf-foot").innerHTML = '<button class="btn ghost" id="pf-fold-close">닫기</button>';
  $("pf-fold-close").onclick = pfClose;
}

// ---- 완료/실패 토스트 (콘솔 내 비차단 알림) ---------------------------------
let toastTimer = null;
function toast(msg, kind) {
  let el = $("c2-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "c2-toast";
    document.body.appendChild(el);
  }
  el.className = "c2toast " + (kind || "");
  el.textContent = msg;
  el.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 9000);
}

// ================= now-playing bar (신규4) ====================================
// A persistent strip above the ①/② tabs: which lifecycle:step is running RIGHT
// NOW (METHOD path), how long the step has been running, and the lifecycle's
// measured average duration (data/optimizer/durations.json via /api/model) —
// "서버 생성 대기 중 — 4m32s 경과 / 평균 ~12m".
function fmtElapsed(s) {
  if (s == null || !isFinite(s) || s < 0) return "";
  s = Math.round(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(s / 3600) + "h" + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
}
// ---- PLAN vs ACTUAL 스트립 (v2 접목 2 — 계획↔실행 연속성, §2.9 B층) ----------
// pre-flight가 보여준 견적과 "지금"을 한 줄에서 대조한다: 생성 n/~m · ETA 대비
// 경과 · VPC 슬롯. PLAN은 rec.lifecycle_ids로 /api/plan 서버 재계산(스테이지
// 스냅샷은 서버 재기동/기록 복원 시 없을 수 있어 재계산이 항상 참) — run별 1회.
let runPlan = null, runPlanFor = null;
function ensureRunPlan() {
  if (!runId || runPlanFor === runId || !(runSelIds || []).length) return;
  runPlanFor = runId; runPlan = null;
  const reqFor = runId;
  fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lifecycle_ids: runSelIds }) })
    .then(r => r.json())
    .then(p => { if (runPlanFor !== reqFor) return;
      runPlan = p.error ? { _failed: true } : p; renderPlanActual(); })
    .catch(() => { if (runPlanFor === reqFor) { runPlan = { _failed: true }; renderPlanActual(); } });
}
function planTotals(p) {
  let creates = 0, deletes = 0;
  Object.values(p.preview || {}).forEach(pv => {
    creates += pv.est_creates || 0; deletes += pv.est_deletes || 0;
  });
  // 시간 예측은 여기서 하지 않는다 — 단일 예측 소스는 schedule-sim(pvaSim,
  // '예측 vs 실제 타임라인' 패널과 공유 캐시). 같은 화면에 가정이 다른 예측이
  // 둘 뜨는 것 금지 (초기 접목 2의 병렬-6 근사를 콘솔 간트 작업과 정합·대체).
  return { creates, deletes };
}
function slotMeterHtml(cap, minePeak) {
  if (!cap || !cap.cap) return "";
  const total = cap.cap, base = Math.max(0, cap.baseline || 0);
  const mine = Math.min(minePeak || 0, total);
  const other = Math.max(0, (cap.reserved || 0) - mine);
  const kinds = [];
  for (let i = 0; i < base; i++) kinds.push("base");
  for (let i = 0; i < mine; i++) kinds.push("mine");
  for (let i = 0; i < other; i++) kinds.push("other");
  while (kinds.length < total) kinds.push("free");
  const cells = kinds.slice(0, total).map(k => `<i class="sl ${k}"></i>`).join("");
  return `<span class="slotmeter" title="계정 VPC ${total}슬롯 — 기존 ${base}(회색) · 이 런 peak ${mine}(파랑) · 다른 런 ${other}(보라) · 여유 ${cap.headroom != null ? cap.headroom : "?"}">VPC ${cells}</span>`;
}
function renderPlanActual() {
  const host = $("planactual"); if (!host) return;
  const inFlight = runId && (runStatus === "running" || runStatus === "queued");
  // 새 런이 뜨면 이전 런의 종료 카드(접목 3)는 치운다 — 두 스트립이 겹치지 않게
  if (inFlight) { const dc = $("donecard"); if (dc && !dc.classList.contains("hidden")) { dc.classList.add("hidden"); dc._h = null; } }
  if (!inFlight) { host.classList.add("hidden"); host._h = null; host.innerHTML = ""; return; }
  host.classList.remove("hidden");
  if (!host._wired) {          // [📊 타임라인] — 요약(스트립) → 상세(간트 패널) 딥링크
    host._wired = true;
    host.addEventListener("click", ev => {
      if (!ev.target.closest("#pa-tl")) return;
      ev.preventDefault();
      const d = pvaEnsure(); if (!d) return;
      go("run"); d.open = true; renderPva();
      d.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
  ensureRunPlan();
  ensurePvaSim();              // 예측은 간트 패널과 같은 캐시(pvaSim) — 단일 소스
  const sim = (pvaSimFor === runId && pvaSim && !pvaSim.error && pvaSim.makespan_s) ? pvaSim : null;
  const predTxt = sim ? "~" + fmtDur(sim.makespan_s)
    : (pvaSimFor === runId && pvaSim && pvaSim.error ? "불가" : "계산 중…");
  const tlLink = ` <a href="#" id="pa-tl" title="예측 vs 실제 타임라인(간트) 패널 열기 — 이 예측(schedule-sim)의 lifecycle별 상세">📊 타임라인</a>`;
  let t = null, planTxt = `예측 <b>${predTxt}</b>${tlLink}`;
  if (runPlan && runPlan._failed) planTxt = `예측 <b>${predTxt}</b> · 생성/삭제 견적 불가 (/api/plan 실패)${tlLink}`;
  else if (runPlan) {
    t = planTotals(runPlan);
    planTxt = `생성 ~<b>${t.creates}</b> · 삭제 ~${t.deletes} · peak VPC <b>${runPlan.peak_vpcs || 0}</b> · 예측 <b>${predTxt}</b>${tlLink}`;
  }
  let actTxt;
  if (runStatus === "queued") {
    // WHY QUEUED — 여유 < 필요 peak 를 수치로 (§2.9 queued 상태)
    const cap = lastCapacity, peak = runPlan && !runPlan._failed ? (runPlan.peak_vpcs || 0) : null;
    actTxt = cap && peak != null
      ? `여유 <b>${cap.headroom != null ? cap.headroom : "?"}</b> &lt; 필요 peak <b>${peak}</b> — 슬롯이 나면 자동 시작 ${slotMeterHtml(cap, 0)}`
      : "대기 큐 — 여유가 생기면 자동 시작";
  } else {
    const created = runEvents.filter(e => e.kind === "resource-tracked").length;
    const deleted = runEvents.filter(e => e.kind === "resource-deleted").length;
    const prog = runProgress();
    // 편차 칩 — '예측 초과'만, 간트 패널의 amber 와 같은 기준(schedule-sim
    // makespan 초과). 지연 의심의 정식 판정은 접목 4(엔진 요청 #5 세마포어
    // 대기 이벤트) 전에는 오탐 위험이 있어 여기서 하지 않는다.
    const over = sim && prog.elapsed != null && prog.elapsed > sim.makespan_s;
    actTxt = `생성 <b>${created}</b>${t ? `/~${t.creates}` : ""} · 삭제 ${deleted}${t ? `/~${t.deletes}` : ""} · 경과 <b>${prog.elapsed != null ? fmtElapsed(prog.elapsed) : "—"}</b>`
      + (over ? ` <span class="pa-over" title="경과가 예측 makespan(~${fmtDur(sim.makespan_s)})을 넘었습니다 — 타임라인 패널의 amber(예측 초과)와 같은 기준. 근사 예측이라 초과 자체가 이상은 아닙니다">예측 초과</span>` : "")
      + " " + slotMeterHtml(lastCapacity, runPlan && !runPlan._failed ? runPlan.peak_vpcs : 0);
  }
  setHtmlIfChanged(host,
    `<span class="pa-col"><span class="pa-k" title="pre-flight 견적(/api/plan 서버 재계산) + 예측 makespan(schedule-sim — '예측 vs 실제 타임라인' 패널과 동일 소스)">PLAN</span> <span class="pa-v">${planTxt}</span></span>`
    + `<span class="pa-mid">→</span>`
    + `<span class="pa-col"><span class="pa-k" title="이 run의 이벤트 실측 (resource-tracked/-deleted 집계) + /api/capacity 슬롯">${runStatus === "queued" ? "WHY QUEUED" : "ACTUAL"}</span> <span class="pa-v">${actTxt}</span></span>`);
}

function renderNowPlaying() {
  const host = $("nowplaying"); if (!host) return;
  renderPlanActual();          // PLAN↔ACTUAL 스트립은 now-playing과 같은 생명주기
  const inFlight = runId && (runStatus === "running" || runStatus === "queued");
  if (!inFlight) { host.classList.add("hidden"); host._h = null; host.innerHTML = ""; return; }
  host.classList.remove("hidden");
  // 로컬 run 중단 버튼 — 클릭은 위임(wireReportDelegation)이라 재생성돼도 불멸.
  const abortBtn = `<button class="minibtn red np-abort" id="np-abort"
      title="이 로컬 실행 중단 — pytest 프로세스 트리 종료 + teardown 스윕 (확인 모달)">⏹ 중단</button>`;
  // P2C-24 런 진행률: 종결 N/전체 · % · 경과 · 잔여 — 바 셸은 setHtmlIfChanged 로
  // 고정하고(⏹ 클릭 유실 방지), 시각·진행 값은 volatile 스팬 텍스트만 갱신.
  const progShell = `<span class="np-prog"
      title="런 진행률 — 종결 lifecycle/전체 · 잔여 = durations.json 실측 평균(병렬 ${ETA_PARALLEL} 가정) 추정"><i id="np-prog-fill"></i></span><span class="np-progtxt" id="np-progtxt"></span>`;
  if (runStatus === "queued") {
    setHtmlIfChanged(host, `<span class="np-dot que"></span><b>대기 큐</b>
      <span class="muted small">run ${esc(runId)} — 여유가 생기면 자동 실행</span>${abortBtn}`);
    return;
  }
  const prog = liveProgress();
  if (!prog.active) {
    // 프로비저닝 국면은 이름을 붙여서 — "다음 step 대기…"가 1~3분 얼어 보이던 게
    // 실은 공유 VPC+서브넷 ACTIVE 대기였다 (진행은 로그 tab에 실시간 스트리밍).
    setHtmlIfChanged(host, prog.provisioning
      ? `<span class="np-dot run"></span><b>공유 인프라 준비 중</b>
      <span class="muted small">run ${esc(runId)} — VPC+서브넷 ACTIVE 대기 (<span id="np-elapsed"></span>) · 진행은 로그 tab</span>${progShell}${abortBtn}`
      : `<span class="np-dot"></span><b>실행 중</b>
      <span class="muted small">run ${esc(runId)} — 다음 step 대기…</span>${progShell}${abortBtn}`);
    const pe = $("np-elapsed");
    if (pe) pe.textContent = prog.provisioning && prog.provStart && prog.provStart.ts
      ? fmtElapsed(Date.now() / 1000 - prog.provStart.ts) + " 경과" : "통상 1~3분";
    updateNpProgress();
    return;
  }
  const a = prog.active;
  const dur = (MODEL && MODEL.durations || {})[a.lifecycle];
  // '평균' 이 무엇의 평균인지 명시: 이 lifecycle 의 실측 평균 (durations.json)
  const avg = dur && dur.avg_s ? `이 lifecycle 평균 ~${fmtElapsed(dur.avg_s)}` : "이 lifecycle 평균 미측정";
  setHtmlIfChanged(host, `<span class="np-dot run"></span>
    <b>${esc(prog.phaseLabel || "진행 중")}</b>
    <code class="np-step">${esc(a.lifecycle)} : ${esc(a.step || "")}</code>
    <span class="mtag ${esc(a.method || "")}">${esc(a.method || "")}</span>
    <code class="np-path">${esc(a.path || "")}</code>
    <span class="np-time"><span id="np-elapsed"></span> / ${avg}</span>${progShell}${abortBtn}`);
  const se = $("np-elapsed");
  if (se) se.textContent = a.ts ? fmtElapsed(Date.now() / 1000 - a.ts) + " 경과" : "";
  updateNpProgress();
}

// 진행률 volatile 갱신 — rail '전체' 카드 링(--p)과 같은 runProgress() 소스.
function updateNpProgress() {
  const fill = $("np-prog-fill"), txt = $("np-progtxt");
  if (!fill || !txt) return;
  const p = runProgress();
  fill.style.width = p.pct + "%";
  txt.textContent = `${p.done}/${p.total} · ${p.pct}%`
    + (p.elapsed != null ? ` · 경과 ${fmtElapsed(p.elapsed)}` : "")
    + (p.eta != null && p.eta > 0 ? ` · 잔여 ~${fmtElapsed(p.eta)}` : "");
}

// ---- 로컬 run 중단 (확인 모달 → POST /api/runs/<id>/abort) --------------------
// 파괴적 동작이므로 native confirm 이 아니라 pre-flight 모달 셸로 무엇이 일어나는지
// 명시한다: pytest 트리 종료 → 공유 VPC teardown + run-scoped 정리 스윕 → status
// '중단됨(aborted)' 기록/미러. 서버가 지원하지 않는 기록(스캔/sim)은 409 → 토스트.
function abortConfirm() {
  if (!runId) return;
  const id = runId;
  pfOpen("⏹ 실행 중단 — " + id);
  $("pf-body").innerHTML =
    '<p><b style="color:var(--red)">진행 중인 로컬 실행을 중단합니다.</b></p>' +
    '<ul class="muted small" style="margin:6px 0 0;padding-left:18px">' +
    "<li>pytest 프로세스 트리(병렬 워커 포함)를 종료합니다.</li>" +
    "<li>공유 VPC teardown + 이 run 잔존에 대한 run-scoped 정리 스윕을 실행합니다.</li>" +
    "<li>run 상태는 <b>중단됨(aborted)</b> 으로 기록되고 실행 기록에 미러됩니다.</li>" +
    "<li>종료 후 실측 재스캔(+0·+5m·+15m)이 잔존 자원을 감시합니다.</li></ul>";
  $("pf-foot").innerHTML =
    '<button class="btn ghost" id="pf-ab-cancel">취소</button>' +
    '<button class="btn warn" id="pf-ab-go">⏹ 중단 실행</button>';
  $("pf-ab-cancel").onclick = pfClose;
  $("pf-ab-go").onclick = () => {
    $("pf-ab-go").disabled = true;
    $("pf-ab-go").textContent = "중단 요청 중…";
    fetch("/api/runs/" + id + "/abort", { method: "POST" })
      .then(r => r.json().then(j => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        pfClose();
        if (!ok || j.error) { toast("중단 실패: " + (j.error || "?"), "fail"); return; }
        toast(j.status === "aborted"
          ? "run " + id + " 중단됨 (시작 전 대기열에서 제거)"
          : "중단 요청됨 — pytest 종료 → teardown 스윕 후 '중단됨' 으로 기록됩니다");
        pollEvents();          // status 가 aborted 로 flip 되는 것을 따라간다
      }).catch(e => { pfClose(); toast("중단 요청 실패: " + e.message, "fail"); });
  };
}

// ================= 리포트 — master(흐름) → detail(자원·API·로그) + 전체 ===========
// The report is a master→detail drill-down: 흐름 is the PERSISTENT master (the B2
// live scene + a compact lifecycle list); the DETAIL pane (자원·API·로그) is scoped
// to the currently-selected lifecycle, or to the cross-run aggregate (전체). Both
// the master and the open detail refresh in place on every poll — no flicker, and
// the user's selected lifecycle / sub-tab / open API row survive.
function drawReport() {
  if (!runId) {
    closeDagModal();         // run 바인딩 해제 — 팝업/씬 동반 정리
    // P2C-24: 직접 초기화하므로 in-place patch 마커(_shell/_h)도 함께 리셋 —
    // 안 하면 다음 run 에서 setHtmlIfChanged/셸 재사용이 낡은 상태를 참이라 믿는다.
    const lp = $("lc-picker"); lp._shell = null; lp.innerHTML = "";
    $("md-report") && $("md-report").classList.remove("has-detail");
    const sb = $("scopebar"); sb._h = null; sb.innerHTML = "";
    const db = $("detail-body"); db._shell = null; db._h = null;
    db.innerHTML = '<p class="empty">실행이 시작되면 라이프사이클을 선택해 상세를 봅니다.</p>';
    stopR4Poll();
    renderStagedPreview();   // 흐름 area shows the OPEN 대기열 item's DAG (else placeholder)
    loadRunRecords();
    renderPva();             // 예측 vs 실제 패널 — 런 없음 상태 표시
    return;
  }
  if (stagedScene) { stagedScene.destroy(); stagedScene = null; }   // a run owns the 흐름 area now
  reconcileScope();        // auto-select for a single-lifecycle run; validate scope
  reportR1();              // MASTER: the 흐름 scene (B2) — persistent, refresh in place
  renderLcPicker();        // MASTER: compact lifecycle list (collapsed-group / dense escape)
  renderDetail();          // DETAIL: scope bar + 자원/API/로그 for the current scope
  renderPva();             // 예측 vs 실제 타임라인 — 기존 폴 사이클 동승 (새 타이머 금지)
  // P2C-24: 여기 있던 실행 기록(/api/runs) fetch 동승 제거 — 이벤트 폴마다 백엔드를
  // 때리던 폭주 원인. 실행 기록은 시작/종료/종료 후 감시(startRunsWatch)로만 갱신.
}

// ---- event → lifecycle grouping (the pure core of the drill-down) -----------
// Walk the event stream once and bucket everything BY LIFECYCLE so the detail pane
// can scope 자원/API/로그 to one lifecycle (or aggregate across all). Each bucket
// carries: status, ordered resources (from resource-tracked/-deleted), ordered api
// calls (from step-start/-end, enriched), and counts. Pure (events in → object out)
// so it is unit-testable offline; the UI just renders the buckets.
function groupEventsByLifecycle(events) {
  const lcs = {};       // id -> bucket
  const order = [];     // lifecycle ids in first-seen order
  const ensure = id => {
    if (!lcs[id]) { lcs[id] = { id, status: "queued", service: "", heavy: false,
      resources: [], _resByKey: {}, api: [], _apiByKey: {}, softN: 0, failN: 0, createN: 0 }; order.push(id); }
    return lcs[id];
  };
  (events || []).forEach(e => {
    const id = e.lifecycle;
    // lifecycle 귀속이 없는 이벤트(공유 인프라 프로비저닝의 poll-progress 등,
    // lifecycle:"")가 유령 빈 행(ensure(""))을 만들지 않게 건너뛴다 — run-meta/
    // wave-start 만 리스트 필드로 행을 만든다 (2026-07-08 "빈 카드" 목격).
    if (!id && e.kind !== "run-meta" && e.kind !== "wave-start") return;
    if (e.kind === "run-meta") (e.runnable || []).forEach(ensure);
    else if (e.kind === "wave-start") (e.lifecycles || []).forEach(ensure);
    else if (e.kind === "lifecycle-start") { const b = ensure(id); b.status = "running";
      if (e.service) b.service = e.service; if (e.heavy) b.heavy = true; }
    else if (e.kind === "lifecycle-end") { const b = ensure(id);
      b.status = e.status === "passed" ? "done" : e.status === "skipped" ? "skip" : "fail";
      // F3: CLOSE the lifecycle's still-open (⏳) api rows — a step that never
      // emitted step-end (timeout / engine abort) must read as a FAIL, not hang
      // as an in-flight row forever, and it must count in the fail KPI now.
      b.api.forEach(c => {
        if (c.category === "run") { c.category = "fail"; c.failNote = "timeout/중단"; b.failN++; }
      }); }
    else if (e.kind === "step-start") { const b = ensure(id); const k = e.step;
      const c = { key: k, lifecycle: id, step: k, method: e.method, path: e.path,
        status: null, category: "run", ms: null, params: null, req_body: null, resp_snippet: null };
      b._apiByKey[k] = c; b.api.push(c); }
    else if (e.kind === "poll-progress") { const b = ensure(id); const c = b._apiByKey[e.step];
      // 진행 중 폴링의 생존 신호: ⏳ 행에 "N회차 · 상태 · 경과"를 실어준다.
      if (c) c.poll = { attempt: e.attempt, state: e.state, elapsed_s: e.elapsed_s, timeout_s: e.timeout_s }; }
    else if (e.kind === "step-end") { const b = ensure(id); const k = e.step;
      let c = b._apiByKey[k];
      if (!c) { c = { key: k, lifecycle: id, step: k, method: e.method, path: e.path }; b._apiByKey[k] = c; b.api.push(c); }
      c.poll = null;   // 폴 종료 — 생존 신호 제거
      c.status = e.status; c.category = e.category;
      c.ms = e.elapsed_ms != null ? Math.round(e.elapsed_ms) : null;   // integer ms (drop the long float)
      if (e.params != null) c.params = e.params;
      if (e.req_body != null) c.req_body = e.req_body;
      if (e.resp_snippet != null) c.resp_snippet = e.resp_snippet;
      if (e.soft_class != null) c.soft_class = e.soft_class;   // §4: duplicate|gap|policy (서버 분류)
      if (e.category === "soft") b.softN++; else if (e.category === "fail") b.failN++;
    }
    else if (e.kind === "resource-tracked") { const b = ensure(id);
      const r = { id: e.resource_id, type: e.resource_type, lifecycle: id, path: e.path,
        name: e.name || "", created: true, deleted: false }; b.resources.push(r); b.createN++; }
    else if (e.kind === "resource-deleted") { const b = ensure(id);
      const cand = b.resources.filter(r => r.type === e.resource_type && !r.deleted);
      if (cand.length) cand[cand.length - 1].deleted = true; }
  });
  return { lcs, order };
}

// the current scope's grouped buckets. 성능 수리 (2026-07-11 오너 제보 "1,500+
// 호출 런에서 클릭 시 화면 멈춤"): 종전엔 호출마다 전체 이벤트를 재스캔했고,
// 한 폴 틱에 소비자가 여럿(drawReport·runProgress·rail·PLAN/ACTUAL·간트)이라
// 대형 런에서 틱당 수백 ms 롱태스크가 됐다. runEvents 는 폴마다 새 배열로
// 교체되므로(concat/재전송) 배열 참조가 곧 캐시 키 — 같은 틱 안에서는 1회만
// 계산한다.
let _grCache = { ref: null, val: null };
function groupedRun() {
  if (_grCache.ref !== runEvents) {
    _grCache = { ref: runEvents, val: groupEventsByLifecycle(runEvents) };
  }
  return _grCache.val;
}
const isAggScope = () => detailScope === "*";

// auto-select the scope for a single-lifecycle run (so the detail shows without an
// extra click), and drop a stale scope (e.g. a lifecycle that vanished). Honours an
// explicit user pick (scopeAuto=false) — we never yank them off their chosen scope.
function reconcileScope() {
  const { order } = groupedRun();
  if (scopeAuto) {
    if (order.length === 1) detailScope = order[0];        // single lifecycle → drill in
    else detailScope = "*";                                 // multi → aggregate by default
  } else if (detailScope !== "*" && !order.includes(detailScope)) {
    detailScope = "*"; scopeAuto = true;                    // chosen lifecycle gone → aggregate
  }
}

// set the detail scope to a lifecycle id (or "*"); record it as an explicit pick so
// reconcileScope won't override it, then re-render the master list highlight + detail.
// `fromScene` marks a selection that ORIGINATED from a master node focus (so we don't
// fight the scene). A selection from the compact list sets scope only — the master
// scene's focus stays where the user left it (resource_graph.js exposes no
// focus-by-id, and it's frozen; the list is the escape hatch for a dense graph).
function selectScope(id, opts) {  // eslint-disable-line no-unused-vars
  detailScope = id;
  scopeAuto = false;
  renderLcPicker();
  renderDetail();
}
function setDetailTab(tab) {
  detailTab = tab;
  els("#detail-subtabs button").forEach(b => b.classList.toggle("on", b.dataset.d === tab));
  renderDetailBody();
}

// ---- lifecycle status presentation (shared by the picker + scope bar) -------
function lcStatusClass(s) { return s === "done" ? "done" : s === "running" ? "run" : s === "fail" ? "fail" : s === "skip" ? "skip" : "queued"; }
function lcStatusGlyph(s) { return s === "done" ? "✓" : s === "running" ? "⏳" : s === "fail" ? "✕" : s === "skip" ? "–" : "·"; }
function lcStatusLabel(s) { return s === "done" ? "완료" : s === "running" ? "진행 중" : s === "fail" ? "실패" : s === "skip" ? "건너뜀" : "대기"; }

// ---- RAIL (P2C-22, owner 2026-07-09): 전체 카드 + 상태 필터 + 시나리오 목록 --
// 세로 카드 스택(96행)이 하단 상세와 스크롤 왕복하던 것을 좌 rail ↔ 우 상세
// 2-pane 으로. rail = ① 전체(집계) 카드 최상단 고정(진행률 링 done/total + fail
// 스텝 합계 — "메인은 전체") ② 상태 필터 칩 ③ 1줄 압축 행 목록(내부 스크롤;
// API/자원/soft 카운트는 title 툴팁으로) + 대기(이벤트 0) 회색 행 통합.
// 시맨틱 무변경: 행 클릭 = selectScope(기존), 전체 카드 = selectScope("*").
let railFilter = "all";     // all | run | fail | queued — rail 상태 필터 (로컬 상태)
let railUserTs = 0;         // 사용자가 목록을 만진 최근 시각 — follow-active 유보
let railProgTs = 0;         // 프로그램적 scrollTop 조정 시각 (scroll 이벤트 오인 방지)
const RAIL_FOLLOW_HOLD_MS = 10000;

function renderLcPicker() {
  const host = $("lc-picker"); if (!host) return;
  const { lcs, order } = groupedRun();
  const agg = aggregateBucket(lcs, order);
  const prog = liveProgress();
  const activeLc = prog.running ? prog.activeLifecycle : null;
  // 대기 중(이벤트 0) 라이프사이클 — rec 전체 선택(runSelIds)과의 차집합 파생은
  // 유지 (2026-07-08 owner), 표시만 접힌 요약 한 줄 → rail 회색 행으로.
  const pending = (runSelIds || []).filter(id => !lcs[id]).sort();
  const n = { run: 0, fail: 0, queued: pending.length, done: 0 };
  order.forEach(id => { const s = lcs[id].status;
    if (s === "running") n.run++; else if (s === "fail") n.fail++;
    else if (s === "queued") n.queued++; else if (s === "done") n.done++; });
  const total = order.length + pending.length;
  const rp = runProgress();          // 링 = now-playing 진행률 바와 같은 소스 (P2C-24)
  const failSteps = order.reduce((a, id) => a + lcs[id].failN, 0);
  const match = s => railFilter === "all"
    || (railFilter === "run" && s === "running")
    || (railFilter === "fail" && s === "fail")
    || (railFilter === "queued" && s === "queued");
  // ---- 셸: run/필터가 바뀔 때만 재구축 (P2C-24 — 폴마다 innerHTML 전체 재빌드가
  // 깜빡임·클릭 유실의 원인; 클릭은 위임(wireReportDelegation)이라 재배선 불요) ----
  const shellKey = String(runId) + "|" + railFilter;
  if (host._shell !== shellKey) {
    host._shell = shellKey;
    const chip = (k, label) =>
      `<button class="fchip ${railFilter === k ? "on" : ""}" data-f="${k}" title="${label} 시나리오만 표시">${label} <b data-fc="${k}">0</b></button>`;
    host.innerHTML =
      `<button class="aggitem top ${isAggScope() ? "sel" : ""}" id="agg-toggle" title="크로스-런 집계 — 런 전체 자원/API/로그 합산">
         <span class="ring" style="--p:0"></span>
         <span class="aggtxt"><b>🗂️ 전체 (집계)</b>
           <span class="sub"></span></span>
       </button>
       <div class="lcfilter" id="lc-filter">${chip("all", "전체")}${chip("run", "진행")}${chip("fail", "실패")}${chip("queued", "대기")}</div>
       <div class="lcp-h">시나리오 <span class="muted small">· 클릭 = 우측 상세</span></div>
       <div class="lclist"></div>`;
    const fresh = host.querySelector(".lclist");
    const touch = () => { if (Date.now() - railProgTs > 200) railUserTs = Date.now(); };
    fresh.addEventListener("scroll", touch, { passive: true });
    fresh.addEventListener("mouseenter", touch);
  }
  // ---- patch: 링/카운트/하이라이트/행 — 바뀐 것만 갱신 ----
  const ring = host.querySelector(".ring");
  if (ring) {
    ring.style.setProperty("--p", rp.pct);
    ring.title = `진행률 ${rp.pct}% — 종결 ${rp.done}/${rp.total}`;
  }
  setHtmlIfChanged(host.querySelector(".aggitem .sub"),
    `완료 ${n.done}/${total}${failSteps ? ` · <span class="failn">✕${failSteps}</span>` : ""} · ${agg.resources.length} 자원 · ${agg.api.length} API`);
  const aggBtn = host.querySelector("#agg-toggle");
  if (aggBtn) aggBtn.classList.toggle("sel", isAggScope());
  const fc = { all: total, run: n.run, fail: n.fail, queued: n.queued };
  els("#lc-filter [data-fc]").forEach(b => {
    const v = String(fc[b.dataset.fc] || 0);
    if (b.textContent !== v) b.textContent = v;
  });
  els("#lc-filter .fchip").forEach(b => b.classList.toggle("on", b.dataset.f === railFilter));
  const units = order.filter(id => match(lcs[id].status)).map(id => {
    const b = lcs[id];
    const cls = lcStatusClass(b.status);
    const tip = `${id}${b.service ? " — " + b.service : ""} · ${lcStatusLabel(b.status)}`
      + ` · ${b.api.length} API · ${b.resources.length} 자원`
      + (b.softN ? ` · ${b.softN} soft` : "") + (b.failN ? ` · ${b.failN} fail` : "")
      + " — 상세 열기";
    return { k: "lc:" + id, html:
      `<button class="lcitem ${detailScope === id ? "sel" : ""}${activeLc === id ? " now" : ""}" data-k="lc:${esc(id)}" data-lc="${esc(id)}" title="${esc(tip)}">
      <span class="st ${cls}">${lcStatusGlyph(b.status)}</span>
      <span class="lcname">${b.heavy ? "🜂 " : ""}${esc(id)}</span>
      ${b.failN ? `<span class="pill fail">✕${b.failN}</span>` : ""}
    </button>` };
  });
  if (railFilter === "all" || railFilter === "queued") {
    pending.forEach(id => units.push({ k: "pend:" + id, html:
      `<div class="lcitem pend" data-k="pend:${esc(id)}" title="${esc(id)} — 대기 중, 워커가 비면 순서대로 시작">
        <span class="st queued">·</span><span class="lcname">${esc(id)}</span></div>` }));
  }
  if (!units.length) units.push({ k: "__empty",
    html: '<p class="muted small" data-k="__empty">라이프사이클 대기 중…</p>' });
  const list = host.querySelector(".lclist");
  syncUnits(list, units);
  // follow-active: 실행 중이면 now 행을 목록 뷰포트 안으로 — 단 사용자가 최근
  // ~10s 내 목록을 스크롤/호버했으면 유보 (keepDetailScroll 과 같은 존중 원칙).
  if (activeLc && Date.now() - railUserTs > RAIL_FOLLOW_HOLD_MS) {
    const row = list.querySelector(".lcitem.now");
    if (row) {
      // .lclist 는 position:relative — row.offsetTop 이 곧 목록-상대 좌표다.
      const top = row.offsetTop, bot = top + row.offsetHeight;
      if (top < list.scrollTop || bot > list.scrollTop + list.clientHeight) {
        railProgTs = Date.now();
        list.scrollTop = Math.max(0, top - Math.round(list.clientHeight / 2));
      }
    }
  }
}

// aggregate bucket across all lifecycles (the 전체 scope) — concatenates resources +
// api in lifecycle order, tagging each item with its source lifecycle (_lc) so the
// aggregate views can show which lifecycle each row came from.
function aggregateBucket(lcs, order) {
  const resources = [], api = [];
  order.forEach(id => {
    lcs[id].resources.forEach(r => resources.push(Object.assign({ _lc: id }, r)));
    lcs[id].api.forEach(c => api.push(Object.assign({}, c, { _lc: id })));
  });
  return { resources, api };
}

// the buckets for the CURRENT scope: one lifecycle's, or the aggregate.
function scopeData() {
  const { lcs, order } = groupedRun();
  if (isAggScope()) {
    const agg = aggregateBucket(lcs, order);
    return { agg: true, resources: agg.resources, api: agg.api, lcCount: order.length, order, lcs };
  }
  const b = lcs[detailScope] || { resources: [], api: [], status: "queued", service: "", heavy: false };
  return { agg: false, id: detailScope, resources: b.resources, api: b.api,
    status: b.status, service: b.service, heavy: b.heavy, lcs, order };
}

// ---- DETAIL: sticky scope bar + sub-tab counts + tab body -------------------
function renderDetail() {
  $("md-report") && $("md-report").classList.add("has-detail");
  renderScopeBar();
  renderDetailCounts();
  els("#detail-subtabs button").forEach(b => b.classList.toggle("on", b.dataset.d === detailTab));
  renderDetailBody();
}

// the sticky scope bar — PINS which lifecycle the detail is showing (the thing flat
// tabs lose). e.g.  스코프 ▸ networking-vpc-subnet · networking/vpc · 진행 중 · 14 API · 3 자원
function renderScopeBar() {
  const bar = $("scopebar"); if (!bar) return;
  const d = scopeData();
  if (d.agg) {
    setHtmlIfChanged(bar, `<span class="lbl">스코프</span>
      <span class="cur agg">🗂️ 전체 (집계)</span>
      <span class="crumb">— ${d.lcCount} lifecycle 합산 · ${d.resources.length} 자원 · ${d.api.length} API</span>`);
    return;
  }
  // P2C-24 (오너: "중간에 특정 라이프사이클을 멈출 수는 없네"): 선택 스코프가
  // 진행/대기 중이고 런이 살아 있으면 per-lifecycle 중단 버튼 — 서버 7624e296
  // 채널로 다음 안전 지점에서 정리 후 스킵. 클릭 배선은 위임(scope-skip).
  const skippable = runStatus === "running" && (d.status === "running" || d.status === "queued");
  setHtmlIfChanged(bar, `<span class="lbl">스코프</span>
    <span class="cur"><span class="st ${lcStatusClass(d.status)}">${lcStatusGlyph(d.status)}</span> ${d.heavy ? "🜂 " : ""}${esc(d.id)}</span>
    <span class="crumb">— ${d.service ? esc(d.service) + " · " : ""}${lcStatusLabel(d.status)} · ${d.api.length} API · ${d.resources.length} 자원</span>
    ${skippable ? `<button class="clear skip" id="scope-skip" data-lc="${esc(d.id)}"
      title="이 라이프사이클만 정리 후 스킵 — 나머지 라이프사이클은 계속 (긴 대기는 즉시 탈출, 다음 안전 지점에서 집행)">⏸ 이 라이프사이클 중단</button>` : ""}
    <button class="clear" id="scope-clear" title="전체 집계로">전체 집계로 ↺</button>`);
}

function renderDetailCounts() {
  const d = scopeData();
  $("d-nres").textContent = d.resources.length;
  $("d-napi").textContent = d.api.length;
}

// route the detail body to the scoped 자원 / API / 로그 view.
function renderDetailBody() {
  if (!runId) return;
  keepDetailScroll(() => {
    if (detailTab === "res") reportR2();
    else if (detailTab === "api") reportR3();
    else if (detailTab === "rt") reportRT();
    else reportR4();
  });
}

// 런타임(계정 실측) — DETAIL 4번째 탭 (CX 재배치): 기존 /runtime 페이지(scope=mine
// 기본 · 페이지 자체 주기 자동 갱신, 6fa9ec12)를 iframe 으로 그대로 임베드. 단일
// 소스 원칙 — 런타임 로직을 콘솔에 복제하지 않는다. 셸은 1회만 구축: 폴마다
// drawReport→renderDetailBody 가 다시 불려도 iframe 을 리로드하지 않는다.
function reportRT() {
  if ($("rt-frame")) return;                 // already embedded — keep it alive
  const url = runtimeUrl();
  $("detail-body").innerHTML =
    `<h3 class="detail-h">런타임 <span class="muted small">· 계정 실측 — 지금 실제 떠 있는 자원 토폴로지 (내 실행 우선 · 자동 갱신)</span>
       <button class="minibtn" id="rt-popout" title="런타임 뷰를 별도 창으로 크게">↗ 새 창</button></h3>
     <iframe id="rt-frame" class="rt-frame" src="${esc(url)}" title="런타임 뷰 — 계정 실측 토폴로지"></iframe>`;
  $("rt-popout").onclick = () =>
    window.open(url, "scp-runtime", "width=1320,height=900,scrollbars=yes,resizable=yes");
}

// Preserve the detail list's scroll position across a re-render. The live poll
// re-renders the whole detail body via innerHTML (reportR2/R3/R4 each rebuild the
// inner `.scroll` container), which would otherwise snap the 자원/API/로그 list
// back to the top every refresh while the user is scrolled down reading it.
function keepDetailScroll(render) {
  // 로그 <pre class="runlog">도 스크롤 보존 대상 (owner 2026-07-08: 스코프 로그가
  // 이벤트 폴마다 재렌더되며 맨 위로 스냅 — 바닥까지 내려도 확인 불가). 바닥
  // 근처였으면 tail 추적(바닥 고정), 아니면 기존 위치 유지.
  const pick = () => $("detail-body")
    && ($("detail-body").querySelector(".scroll") || $("detail-body").querySelector("pre.runlog"));
  const before = pick();
  const top = before ? before.scrollTop : 0;
  const stick = before ? _nearBottom(before) : false;
  render();
  const after = pick();
  if (!after) return;
  if (stick) after.scrollTop = after.scrollHeight;
  else if (top) after.scrollTop = top;
}

// derive live lifecycle state from events: queued/running/done/fail/skip
function lifecycleStates() {
  // 성능 수리 (2026-07-11): 전체 이벤트 재스캔 대신 groupedRun() 캐시의 버킷
  // status 를 그대로 읽는다 — 어휘 동일(queued/running/done/skip/fail),
  // run-meta/wave-start 의 ensure 도 groupEventsByLifecycle 가 이미 수행.
  const { lcs, order } = groupedRun();
  const st = {};
  order.forEach(id => { st[id] = lcs[id].status; });
  return st;
}

// derive the CURRENT in-progress activity from the event stream so the report can
// show "생성 중 → 테스트 중 → 삭제 중 → 완료" as it advances. The active step is the
// last step-start with no matching step-end; we phase it by method/kind. Also the
// most-recently-tracked (not-yet-deleted) resource id, for the 자원 view's cursor.
function liveProgress() {
  const running = runStatus === "running" && !runEvents.some(e => e.kind === "run-end");
  const openSteps = {};        // key -> step event still open
  let lastStart = null, lastTrack = null, lastDelete = null;
  let provStart = null, provEnd = null;   // 공유 인프라 프로비저닝 국면 (서버 narrator)
  runEvents.forEach(e => {
    if (e.kind === "step-start") { openSteps[e.lifecycle + "|" + e.step] = e; lastStart = e; }
    if (e.kind === "step-end") delete openSteps[e.lifecycle + "|" + e.step];
    if (e.kind === "resource-tracked") lastTrack = e;
    if (e.kind === "resource-deleted") lastDelete = e;
    if (e.kind === "provision-start") { provStart = e; provEnd = null; }
    if (e.kind === "provision-end") provEnd = e;
  });
  // the active step = the most recent still-open step-start (fallback: lastStart)
  const openList = Object.values(openSteps);
  const active = running ? (openList[openList.length - 1] || lastStart) : null;
  // 프로비저닝 중 = provision-start 후 provision-end 전이고 아직 어떤 step도 없음
  // ("실행 중 — 다음 step 대기…" 로 얼어 보이던 1~3분의 정체를 이름 붙인다).
  const provisioning = running && !active && !!provStart && !provEnd && !lastStart;
  let phase = null, phaseLabel = "";
  if (active) {
    const m = (active.method || "").toUpperCase();
    if (m === "POST") { phase = "create"; phaseLabel = "생성 중"; }
    else if (m === "DELETE") { phase = "delete"; phaseLabel = "삭제 중"; }
    else if (m === "PUT" || m === "PATCH") { phase = "update"; phaseLabel = "설정 중"; }
    else { phase = "test"; phaseLabel = "테스트 중"; }
  } else if (provisioning) {
    phase = "provision"; phaseLabel = "공유 인프라 준비 중";
  } else if (!running) {
    phaseLabel = "완료";
  }
  return { running, active, phase, phaseLabel, provisioning, provStart,
           activeLifecycle: active ? active.lifecycle : null,
           lastTrack, lastDelete };
}

// run-state palettes (shared by the node overlay + the collapsed-group summary).
const R1_FILL = { queued: "#ffffff", running: "#e8f0fd", done: "#eaf7ee", fail: "#fdeaea", skip: "#f6f8fa" };
const R1_STK = { queued: "#8a93a0", running: "#2563c9", done: "#2da44e", fail: "#cf222e", skip: "#8a93a0" };
const R1_BDG = { queued: "", running: "⏳", done: "✓", fail: "✕", skip: "–" };

// the live run-state of a resource node id (via its lifecycle), recomputed fresh on
// each call so the scene's overlay reflects the latest event stream.
function r1NodeState(id) {
  const st = lifecycleStates();
  const lc = N[id] && N[id].lifecycle;
  return lc && st[lc] ? st[lc] : null;
}
// per-node overlay — run-state PRIMARY; the ACTIVE lifecycle pulses blue + phase glyph.
function r1Overlay(id) {
  const prog = liveProgress();
  const lc = N[id] && N[id].lifecycle;
  if (prog.running && lc && lc === prog.activeLifecycle) {
    const glyph = prog.phase === "create" ? "⊕" : prog.phase === "delete" ? "⊖" : "⏳";
    return { fill: "#dbe8fd", stroke: "#1a56c4", badge: glyph, pulse: true };
  }
  const s = r1NodeState(id);
  if (!s) return null;
  // 삭제됨: a DONE lifecycle whose tracked resources were all deleted shows 🗑
  // (대기/진행중(pulse)/완료/실패에 더해 run 뷰의 다섯 번째 상태).
  if (s === "done" && lc) {
    const b = groupedRun().lcs[lc];
    if (b && b.resources.length && b.resources.every(r => r.deleted))
      return { fill: R1_FILL.done, stroke: R1_STK.done, badge: "🗑" };
  }
  return { fill: R1_FILL[s], stroke: R1_STK[s], badge: R1_BDG[s] };
}
// collapsed-group overlay — so a LARGE run is navigable while still showing progress:
// the group card tints by its members' aggregate state + a "done/total" chip.
function r1GroupOverlay(unit) {
  const st = lifecycleStates();
  const states = unit.members.map(id => { const lc = N[id] && N[id].lifecycle; return lc ? st[lc] : null; }).filter(Boolean);
  if (!states.length) return null;
  const total = states.length;
  const done = states.filter(s => s === "done").length;
  const anyFail = states.some(s => s === "fail");
  const anyRun = states.some(s => s === "running");
  const key = anyFail ? "fail" : anyRun ? "running" : done === total ? "done" : "queued";
  return { fill: R1_FILL[key], stroke: R1_STK[key], badge: `${done}/${total}`,
    chipFill: R1_STK[key] + "22", chipStroke: R1_STK[key], chipText: R1_STK[key],
    title: `진행 ${done}/${total}${anyFail ? " · 실패 포함" : anyRun ? " · 진행 중" : ""}` };
}

// 흐름 — composition DAG colored by live lifecycle state + the ACTIVE node pulsed,
// so the user watches the order advance (생성→테스트→삭제). + wave progress below.
// FLICKER FIX (mirrors 로그): the shell (legend + scene stage) is built ONCE per run;
// the fast poll only refreshes the banner/progress text + the scene overlay in place
// (r1Scene.refresh()), so zoom / focus / collapse survive every poll.
// which graph does the master 흐름 scene render? run 모드 = the run's OWN
// lifecycle-closure graph (/api/runs/<id>/graph); 구성 미리보기 = the 구성
// selection's graph (the ONLY behavior before F1 — it broke whenever the
// selection was reset/empty). Falls back gracefully when one is missing.
function reportGraphChoice() {
  const runG = (graphMode !== "build" || !(lastGraph && lastGraph.nodes.length))
    && runGraph && runGraph.nodes && runGraph.nodes.length ? runGraph : null;
  if (graphMode === "run" && runG) return { g: runG, mode: "run" };
  if (lastGraph && lastGraph.nodes.length) return { g: lastGraph, mode: "build" };
  if (runGraph && runGraph.nodes && runGraph.nodes.length) return { g: runGraph, mode: "run" };
  return { g: null, mode: "none" };
}

function graphModeChip(mode) {
  const canRun = !!(runGraph && runGraph.nodes && runGraph.nodes.length);
  const canBuild = !!(lastGraph && lastGraph.nodes.length);
  const cur = mode === "run"
    ? `<b>run 뷰: ${esc(runId || "")}</b> <span class="muted small">— 이 실행의 라이프사이클 폐쇄집합</span>`
    : `<b>구성 미리보기</b> <span class="muted small">— ① 구성의 현재 선택</span>`;
  const other = mode === "run"
    ? (canBuild ? '<button class="minibtn" id="r1-mode-toggle">↔ 구성 미리보기</button>' : "")
    : (canRun ? `<button class="minibtn" id="r1-mode-toggle">↔ run 뷰: ${esc((runId || "").slice(-6))}</button>` : "");
  return `<div class="modechip mode-${esc(mode)}">${cur} ${other}</div>`;
}

// 계획↔실행 연속성 칩 (CX 재배치): run 그래프가 바인딩되면 "① 에서 계획한
// 폐쇄집합 그대로" 임을 명시 — 같은 composer.graph_view 합성, 같은 생성 순서.
function planContinuityHtml(choice, g) {
  if (!choice || choice.mode !== "run" || !g || !g.nodes) return "";
  const live = runStatus === "running" || runStatus === "queued";
  return `<div class="plan-cont" title="run 그래프는 ① Test Planning 과 동일한 composer.graph_view 합성 — 같은 폐쇄집합, 레벨 = 같은 생성 순서. 실 ID·연관은 그래프 노드와 자원 탭에서">` +
    `①→② ① 에서 계획한 폐쇄집합 그대로 ${live ? "실행 중" : "실행"} — <b>${g.nodes.length}</b> 리소스 · 생성 순서 동일</div>`;
}

// ② run 순서표 — ① 의 생성·검증·삭제 순서표와 같은 표를 표시 중인 그래프로,
// 접힘(details) 아래 제공 + 현재 진행 행 하이라이트 (now-playing 의 active
// lifecycle 에 속한 리소스 행 = .ordnow). 폴마다 tbody 만 다시 그리므로 details
// 의 열림 상태는 유지된다.
function renderRunOrderTable(g) {
  const tbl = $("r1-order-tbl"); if (!tbl) return;
  if (!g || !g.nodes || !g.nodes.length) {
    tbl.innerHTML = ORDER_THEAD + '<tbody><tr><td colspan="5" class="empty">없음</td></tr></tbody>';
    const cnt0 = $("r1-order-n"); if (cnt0) cnt0.textContent = 0;
    return;
  }
  const data = orderRowsData(g, null);
  const prog = liveProgress();
  const activeLc = prog.running ? prog.activeLifecycle : null;
  const rows = data.createOrder.map((id, i) => orderRowHtml(id, i, data,
    activeLc && N[id] && N[id].lifecycle === activeLc ? "ordnow" : "")).join("");
  tbl.innerHTML = ORDER_THEAD +
    `<tbody>${rows || '<tr><td colspan="5" class="empty">없음</td></tr>'}</tbody>`;
  const cnt = $("r1-order-n"); if (cnt) cnt.textContent = data.createOrder.length;
}

function reportR1() {
  const prog = liveProgress();
  const activeLc = prog.activeLifecycle;
  const choice = reportGraphChoice();
  const g = choice.g;
  // 인라인 KPI (CX 재설계: 카드 4개 → 배너 한 줄 흡수) — 카드 리스트가 그만큼 위로.
  const st = lifecycleStates();
  const counts = k => Object.values(st).filter(v => v === k).length;
  const total = Object.keys(st).length || (g ? g.nodes.filter(n => n.is_target).length : 0);
  const kpis = `<span class="r1-kpis">
      <span title="lifecycle 완료 수">완료 <b>${counts("done")}/${total}</b></span>
      ${counts("running") ? `<span>실행중 <b style="color:var(--run)">${counts("running")}</b></span>` : ""}
      ${counts("fail") ? `<span title="하나 이상의 스텝이 fail(5xx/HMAC-401/timeout)인 lifecycle">fail <b style="color:var(--fail)">${counts("fail")}</b></span>` : ""}
      <button class="minibtn" id="r1-dag-open"
        title="이 시나리오의 폐쇄집합 의존 그래프 + 생성·검증·삭제 순서표 — 팝업">🕸 의존 그래프</button>
    </span>`;
  const banner = prog.running
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <span class="mono">${esc(activeLc || "")}</span>
        ${prog.active ? `<span class="muted small">${esc((prog.active.method || "") + " " + (prog.active.path || ""))}</span>` : ""}
        ${kpis}</div>`
    : `<div class="nowbar done"><span class="dot"></span><b>${runStatus === "aborted" ? "중단됨" : "완료"}</b> · 상태 ${esc(runStatus === "aborted" ? "aborted (사용자 중단 — teardown 스윕 수행)" : runStatus)} ${kpis}</div>`;
  // (re)build the shell only when missing or the run/graph-binding changed.
  // 메인 = "현재 실행" 전용 — DAG 씬은 🕸 팝업(openDagModal, 위임 배선)이 소유.
  const shellKey = String(runId) + "|" + choice.mode + "|" + (choice.mode === "run" ? String(runGraphFor) : "build");
  const shell = $("r1-shell");
  const fresh = !shell || shell.dataset.run !== shellKey;
  if (fresh) {
    $("report-main").innerHTML = `<div id="r1-shell"><div id="r1-banner">${banner}</div>
      <div id="r1-plan-cont">${planContinuityHtml(choice, g)}</div></div>`;
    $("r1-shell").dataset.run = shellKey;
    if (dagOpen) openDagModal();   // 팝업이 열린 채 run/그래프 바인딩이 바뀜 → 재구성
  } else {
    // same run, subsequent poll: refresh the banner + overlay in place (no rebuild)
    // P2C-24: 내용이 실제로 바뀐 tick 에만 교체 — phase/카운트 불변이면 DOM 유지.
    setHtmlIfChanged($("r1-banner"), banner);
    const pc = $("r1-plan-cont");
    if (pc) setHtmlIfChanged(pc, planContinuityHtml(choice, g));   // 실행 중 → 실행 (종료 시)
    if (r1Scene) r1Scene.refresh();   // 팝업이 열려 있을 때만 존재
  }
  renderRunOrderTable(g);   // 팝업이 닫혀 있으면 r1-order-tbl 부재로 no-op
}

// 🕸 의존 그래프 — 온디맨드 팝업 (owner 2026-07-08: "메인은 현재의 실행을 확인하게
// 하고 dag는 … 별도 팝업으로"). 씬(레전드·granularity·줌·순서표)을 통째로 이 모달이
// 소유한다; 폴 tick 은 reportR1 의 r1Scene.refresh() + renderRunOrderTable 이 그대로
// 갱신해 준다 (요소가 팝업 안에 있을 뿐 id 계약은 동일).
function openDagModal() {
  const choice = reportGraphChoice();
  const g = choice.g;
  dagOpen = true;
  if (r1Scene) { r1Scene.destroy(); r1Scene = null; }
  $("dag-body").innerHTML = `${graphModeChip(choice.mode)}
    <div class="legend">${legend([["#ffffff", "대기"], ["#e8f0fd", "진행 중"], ["#eaf7ee", "완료"], ["#fdeaea", "실패"]])}
      <span>접힌 그룹 = done/total · 그룹 클릭=펼치기 · <b>노드 클릭 = 그 라이프사이클 상세 열기(팝업 닫힘)</b> · 🗑 = 자원 삭제됨</span></div>
    <div class="dag-toolbar">
      <div class="tgroup" id="r1-gran"><button data-gran="category" class="on">카테고리</button><button data-gran="service">서비스</button><button data-gran="resource">전체 펼침</button></div>
      <button class="minibtn" id="r1-collapse">⊟ 전체 접기</button>
      <button class="minibtn" id="r1-expand">⊞ 전체 펼치기</button>
      <span class="statchip" id="r1-stat"></span>
    </div>
    <div class="stage-wrap" id="r1-stage-wrap">
      <div class="stage" id="r1-stage">
        <svg id="r1-svg" class="scene-svg" xmlns="http://www.w3.org/2000/svg"></svg>
        <div class="hint-pill" id="r1-hint"></div>
        <div class="zoomctl"><button id="r1-zin">+</button><button id="r1-zout">−</button><button id="r1-zfit" class="fit">맞춤</button></div>
      </div>
    </div>
    <details class="r1-order" id="r1-order" open>
      <summary>생성 · 검증 · 삭제 순서표 <span class="muted small">— ① 과 동일한 표 · <b id="r1-order-n">0</b> 자원 · 진행 중 행 하이라이트</span></summary>
      <div class="scroll r1-order-scroll"><table class="tbl" id="r1-order-tbl"></table></div>
    </details>`;
  $("dag-modal").classList.add("open");
  $("dag-scrim").classList.add("open");
  $("dag-close").onclick = closeDagModal;
  $("dag-scrim").onclick = closeDagModal;
  const mt = $("r1-mode-toggle");
  if (mt) mt.onclick = () => {
    graphMode = choice.mode === "run" ? "build" : "run";
    openDagModal();          // 새 바인딩으로 팝업 재구성
  };
  if (g) {
    r1Scene = window.ResourceGraph.scene($("r1-svg"), $("r1-stage"), g, {
      hint: $("r1-hint"), stat: $("r1-stat"),
      overlay: r1Overlay, groupOverlay: r1GroupOverlay,
      // node focus = DRILL into that lifecycle's detail; the popup closes so the
      // detail pane behind it is immediately visible (master→detail 유지).
      onFocus: info => {
        if (!info) return;
        const lc = N[info.label] && N[info.label].lifecycle;
        if (lc) { selectScope(lc, { fromScene: true }); closeDagModal(); }
      },
    });
    r1Scene.start();
    els("#r1-gran button").forEach(b => b.onclick = () => {
      els("#r1-gran button").forEach(x => x.classList.toggle("on", x === b));
      r1Scene.setGranularity(b.dataset.gran);
    });
    $("r1-collapse").onclick = () => { els("#r1-gran button").forEach(x => x.classList.toggle("on", x.dataset.gran === "category")); r1Scene.setGranularity("category"); };
    $("r1-expand").onclick = () => { els("#r1-gran button").forEach(x => x.classList.toggle("on", x.dataset.gran === "resource")); r1Scene.expandAll(); };
    $("r1-zin").onclick = () => r1Scene.zoomIn();
    $("r1-zout").onclick = () => r1Scene.zoomOut();
    $("r1-zfit").onclick = () => r1Scene.zoomToFit();
  } else {
    $("r1-svg").innerHTML = runGraphFor === runId && !runGraph
      ? '<text x="12" y="22" fill="#656d76">run 그래프 로딩 중…</text>'
      : '<text x="12" y="22" fill="#656d76">합성 그래프 없음</text>';
  }
  renderRunOrderTable(g);
}

function closeDagModal() {
  dagOpen = false;
  if (r1Scene) { r1Scene.destroy(); r1Scene = null; }
  $("dag-modal").classList.remove("open");
  $("dag-scrim").classList.remove("open");
}

// 자원 (DETAIL · scoped) — per-resource rows (생성·테스트·삭제 + id) for the current
// scope (one lifecycle, or the 전체 aggregate). Resources come from groupEventsBy
// Lifecycle (resource-tracked/-deleted, ordered). "tested" = the lifecycle saw a GET
// 2xx. While running, the most-recent resource in the active lifecycle shows a live
// phase (생성→테스트→삭제) so the user watches each resource step through its cycle.
function reportR2() {
  const d = scopeData();
  // per-lifecycle verify flag (a GET-ok step-end) — used to mark resources "tested".
  const lcVerifyOk = {};
  runEvents.forEach(e => {
    if (e.kind === "step-end" && (e.method || "").toUpperCase() === "GET" && e.category === "ok")
      lcVerifyOk[e.lifecycle] = true;
  });
  const list = d.resources;            // already scope-filtered + ordered
  list.forEach(r => { r.tested = !!lcVerifyOk[r.lifecycle || d.id]; });
  const prog = liveProgress();
  // the live cursor = newest tracked-not-deleted in the active lifecycle, but only
  // when that lifecycle is IN this scope (aggregate, or the active one is selected).
  let cursorId = null;
  if (prog.running && (d.agg || prog.activeLifecycle === d.id)) {
    const live = list.filter(r => !r.deleted
      && (!prog.activeLifecycle || (r.lifecycle || d.id) === prog.activeLifecycle));
    cursorId = live.length ? live[live.length - 1].id : null;
  }
  const phaseChip = r => {
    if (r.deleted) return '<span class="phch del">삭제됨</span>';
    if (prog.running && r.id === cursorId) {
      if (prog.phase === "create") return '<span class="phch act create">생성 중</span>';
      if (prog.phase === "delete") return '<span class="phch act delete">삭제 중</span>';
      if (prog.phase === "update") return '<span class="phch act test">설정 중</span>';
      return '<span class="phch act test">테스트 중</span>';
    }
    if (r.tested) return '<span class="phch ok">테스트됨</span>';
    if (r.created) return '<span class="phch created">생성됨</span>';
    return "";
  };
  // TYPE = the resource KIND derived from the create/delete PATH (vpc/subnet/port),
  // NOT the service name — the path is the source of truth for what was created.
  const rowKind = r => kindFromPath(r.path) || shortName(r.type || "") || "?";
  // the lifecycle column only matters in the aggregate view (single-lc scope already
  // names the lifecycle in the scope bar).
  const lcCol = d.agg ? "<th>lifecycle</th>" : "";
  const ncol = d.agg ? 7 : 6;
  // P2C-24: 셸(제목·표 골격)은 스코프가 바뀔 때만 재구축, 행은 키 기반 patch —
  // 폴마다 detail-body 전체를 다시 그리며 깜빡이던 것 제거 (스크롤도 자연 보존).
  const body = $("detail-body");
  const shellKey = "res|" + String(runId) + "|" + detailScope;
  if (body._shell !== shellKey) {
    body._shell = shellKey;
    body.innerHTML = `<h3 class="detail-h">자원 <span class="muted small">· ${d.agg ? "런 전체" : "이 라이프사이클"} — 생성 · 테스트 · 삭제 + id</span></h3>
    <div id="r2-now"></div>
    <table class="tbl">
      <thead><tr><th>type</th><th>resource_id</th>${lcCol}<th>생성</th><th>테스트</th><th>삭제</th><th>단계</th></tr></thead>
      <tbody id="r2-body"></tbody></table>`;
  }
  setHtmlIfChanged($("r2-now"), prog.running && cursorId
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <code class="resid">${esc(cursorId)}</code></div>` : "");
  const units = list.length ? list.map((r, i) => {
    const k = "r" + i + "|" + (r.id || r.name || "");
    return { k, html: `<tr data-k="${esc(k)}" class="${r.id === cursorId ? "rowact" : ""}">
      <td>${esc(rowKind(r))}</td>
      <td><code class="resid" title="${esc(r.id || r.name || "")}">${esc(r.id || r.name || "")}</code></td>
      ${d.agg ? `<td>${esc(r._lc || r.lifecycle || "")}</td>` : ""}
      <td class="${r.created ? "tick" : "tickno"}">${r.created ? "✓" : "—"}</td>
      <td class="${r.tested ? "tick" : "tickno"}">${r.tested ? "✓" : "—"}</td>
      <td class="${r.deleted ? "tick" : "tickno"}">${r.deleted ? "✓" : "—"}</td>
      <td>${phaseChip(r)}</td>
    </tr>` };
  }) : [{ k: "__empty", html: `<tr data-k="__empty"><td colspan="${ncol}" class="empty">${d.agg ? "추적된 자원 없음"
        : (d.status === "running" ? "이 라이프사이클은 아직 자원을 만들지 않았습니다 (진행 중)…"
           : "이 라이프사이클에는 추적된 자원이 없습니다.")}</td></tr>` }];
  syncUnits($("r2-body"), units);
}

// API (DETAIL · scoped) — api-first table of this scope's calls (method+path, 결과,
// 응답시간). Rows are CLICKABLE: an inline detail panel shows the actual 요청
// params/body + 응답 status/snippet (from the enriched step-end event) AND the
// endpoint's parameter SCHEMA (from /api/model endpoint_params) marking which params
// were actually sent — a coverage hint ("what COULD be tested" vs "what WAS"). Works
// inside a per-lifecycle scope (flat list) AND the 전체 view (grouped by lifecycle).
function reportR3() {
  const d = scopeData();
  const calls = d.api;                  // already scope-filtered + ordered
  // a globally-unique row key (lifecycle|step) so the open-row state is stable in the
  // aggregate view where two lifecycles can share a step name.
  const rowKey = c => (c._lc || c.lifecycle || detailScope) + "|" + c.step;
  const okN = calls.filter(c => c.category === "ok").length;
  const softN = calls.filter(c => c.category === "soft").length;
  const failN = calls.filter(c => c.category === "fail").length;
  // §4/§5 soft 분류 — 서버가 step-end에 soft_class를 실어주면 chip + 분해 표시.
  // confirm=삭제확인(teardown 검증 성공) · dup_run=이번 런에서 이미 2xx ·
  // dup_store=과거 기록만(이번 런 미확인 — 회귀 관점에서 눈에 띄어야 함) · gap · policy.
  const sc = { confirm: 0, dup_run: 0, dup_store: 0, duplicate: 0, gap: 0, policy: 0 };
  calls.forEach(c => { if (c.category === "soft" && sc[c.soft_class] != null) sc[c.soft_class]++; });
  const hasSC = Object.values(sc).some(n => n > 0);
  const softChip = cls => {
    const m = { confirm: ["삭제확인", "cfm", "DELETE 후 404 = 자원이 정말 지워졌다는 증명 (teardown 검증 성공)"],
                dup_run: ["중복(이번 런)", "dup", "같은 endpoint가 이번 런에서 이미 진짜 2xx를 땄음 — 무시 가능"],
                duplicate: ["중복(이번 런)", "dup", "같은 endpoint가 이번 런에서 이미 진짜 2xx를 땄음 — 무시 가능"],
                dup_store: ["과거 기록", "dups", "과거 런의 2xx 기록만 있음 — 이번 런에서는 직접 확인되지 않음 (회귀 미검증)"],
                gap: ["갭", "gapc", "어떤 검증 레시피에도 2xx 스텝이 없음 — 레시피 숙제"],
                policy: ["정책", "pol", "reachability waiver — 만점=도달(4xx=접근 증거)"] };
    const x = m[cls]; return x ? ` <span class="schip ${x[1]}" title="${x[2]}">${x[0]}</span>` : "";
  };
  const dupVis = hasSC && hideDupSoft
    ? calls.filter(c => !(c.category === "soft" &&
        (c.soft_class === "dup_run" || c.soft_class === "duplicate"))) : calls;
  const hiddenN = calls.length - dupVis.length;
  // kpi 타일 결과 필터 (2026-07-11) — soft/fail 만 보기. 카운트는 전체 기준
  // 유지. 타일 필터는 dup-hide 를 무시하고 원본(calls)에서 거른다 — soft 266
  // 을 눌렀는데 전부 dup 이라 0행이 나오면 타일 숫자와 표가 모순 (실측).
  const catVis = apiCatFilter === "all" ? dupVis
    : calls.filter(c => c.category === apiCatFilter);
  // 대형 런 행 상한 — 최신 API_ROW_CAP 건만 렌더 (묵살 금지: 생략 수 + 해제
  // 버튼을 표 첫 행으로). 필터를 걸면 자연히 상한 아래로 내려간다.
  const capped = !apiShowAll && catVis.length > API_ROW_CAP;
  const visCalls = capped ? catVis.slice(-API_ROW_CAP) : catVis;
  const cappedN = catVis.length - visCalls.length;
  const apiUnit = c => {
    const k = rowKey(c);
    const isOpen = expandedApi === k;
    const row = `<tr class="apirow ${isOpen ? "open" : ""}" data-k="a:${esc(k)}" data-apik="${esc(k)}">
      <td><span class="caret">${isOpen ? "▾" : "▸"}</span> <span class="mtag ${esc(c.method || "")}">${esc(c.method || "")}</span> <code>${esc(c.path || "")}</code></td>
      <td>${badge(c.category)}${c.category === "soft" ? softChip(c.soft_class) : ""}${c.failNote ? ` <span class="muted small">(${esc(c.failNote)})</span>` : ""}</td>
      <td class="muted">${c.category === "run" && c.poll ? `<span title="폴링 중 — ACTIVE 등 목표 상태 대기">${esc(c.poll.state)}</span>` : (c.status != null ? esc(c.status) : "—")}</td>
      <td class="muted">${c.ms != null ? c.ms + " ms" : (c.category === "run"
        ? (c.poll ? `⏳ ${c.poll.attempt}회차 · ${fmtDur(c.poll.elapsed_s)}${c.poll.timeout_s ? ` / ${fmtDur(c.poll.timeout_s)}` : ""}` : "⏳") : "—")}</td>
    </tr>`;
    const detail = isOpen ? `<tr class="apidetail"><td colspan="4">${apiDetailHtml(c)}</td></tr>` : "";
    return { k: "a:" + k, html: row + detail };   // 열린 상세도 같은 유닛 — 함께 patch
  };
  let units = [];
  if (d.agg) {
    const byLc = {};
    visCalls.forEach(c => (byLc[c._lc || c.lifecycle] = byLc[c._lc || c.lifecycle] || []).push(c));
    Object.keys(byLc).sort().forEach(lc => {
      units.push({ k: "hdr:" + lc, html:
        `<tr class="lc-head" data-k="hdr:${esc(lc)}"><td colspan="4">${esc(lc)} <span class="muted small">${byLc[lc].length} api</span></td></tr>` });
      byLc[lc].forEach(c => units.push(apiUnit(c)));
    });
  } else {
    units = visCalls.map(apiUnit);
  }
  if (capped) units.unshift({ k: "__cap", html:
    `<tr data-k="__cap"><td colspan="4" class="empty">⚡ 최근 ${API_ROW_CAP}건만 표시 —
      이전 ${cappedN}건 생략 (성능 보호). <button class="minibtn" id="api-showall"
      title="전체 ${catVis.length}건 렌더 — 대형 런에서는 느려질 수 있습니다">전체 표시</button>
      <span class="muted small">또는 kpi 타일(ok/soft/fail)·라이프사이클 스코프로 좁히세요</span></td></tr>` });
  if (!units.length) units.push({ k: "__empty", html:
    `<tr data-k="__empty"><td colspan="4" class="empty">${apiCatFilter !== "all"
      ? `'${apiCatFilter}' 필터와 일치하는 호출이 없습니다 — 타일을 다시 눌러 해제`
      : (d.status === "running" ? "API 호출 대기 중 (진행 중)…" : "이 스코프에 API 호출이 없습니다.")}</td></tr>` });
  // 📖 정의 link(s) for the service(s) this scope's calls belong to (lifecycle→service
  // via the model) — jump from "what ran" to "what the definition + knowledge say".
  const defSvcs = [...new Set(calls.map(c => ((MODEL && MODEL.lifecycles || {})[c._lc || c.lifecycle] || {}).service).filter(Boolean))];
  const defLinks = defSvcs.slice(0, 3).map(s =>
    `<button class="deflink" data-defsvc="${esc(s)}" title="📖 ${esc(s)} 정의 — 생애주기·엔드포인트·지식">📖 ${esc(shortName(s))}</button>`).join("")
    + (defSvcs.length > 3 ? `<span class="muted small">+${defSvcs.length - 3}</span>` : "");
  // P2C-24: 셸(제목·kpi·soft 분류·표 골격)은 스코프 전환 시에만 재구축, 그 외에는
  // 부분 setHtmlIfChanged + 행 단위 patch — 클릭(행 펼침·체크박스)은 위임이 처리.
  const body = $("detail-body");
  const shellKey = "api|" + String(runId) + "|" + detailScope;
  if (body._shell !== shellKey) {
    body._shell = shellKey;
    body.innerHTML = `<div id="r3-head"></div><div id="r3-kpi"></div><div id="r3-soft"></div>
    <div class="scroll" style="max-height:560px;margin-top:8px"><table class="tbl apitbl">
      <thead><tr><th>method · path (대상)</th><th>결과</th><th>status</th><th>응답시간</th></tr></thead>
      <tbody id="r3-body"></tbody></table></div>`;
  }
  setHtmlIfChanged($("r3-head"), `<h3 class="detail-h">API <span class="muted small">· ${d.agg ? "런 전체" : "이 라이프사이클"} — 행 클릭 → 요청·응답·파라미터 스키마</span> ${defLinks}</h3>`);
  // kpi 타일 = 결과 필터 (클릭 토글) — 2026-07-11 오너 제보의 기대 동작
  const tile = (cat, n, color, label, tip) =>
    `<div class="s selcat ${apiCatFilter === cat ? "on" : ""}" data-cat="${cat}"
       title="${esc(tip)} — 클릭: 이 결과만 보기 (다시 클릭 = 해제)"><b${color ? ` style="color:var(--${color})"` : ""}>${n}</b><span>${label}</span></div>`;
  setHtmlIfChanged($("r3-kpi"), `<div class="kpi">
      <div class="s selcat ${apiCatFilter === "all" ? "on" : ""}" data-cat="all" title="전체 보기"><b>${calls.length}</b><span>api 호출</span></div>
      ${tile("ok", okN, "ok", "ok", CAT_TIP.ok)}
      ${tile("soft", softN, "soft", "soft", CAT_TIP.soft)}
      ${tile("fail", failN, "fail", "fail", CAT_TIP.fail)}
    </div>`);
  setHtmlIfChanged($("r3-soft"), hasSC ? `<div class="softbrk small">soft 분류:
        ${sc.confirm ? `<span class="schip cfm">삭제확인 ${sc.confirm}</span>` : ""}
        ${(sc.dup_run + sc.duplicate) ? `<span class="schip dup">중복(이번 런) ${sc.dup_run + sc.duplicate}</span>` : ""}
        ${sc.dup_store ? `<span class="schip dups">과거 기록 ${sc.dup_store}</span>` : ""}
        ${sc.gap ? `<span class="schip gapc">갭 ${sc.gap}</span>` : ""}
        ${sc.policy ? `<span class="schip pol">정책 ${sc.policy}</span>` : ""}
        <span class="muted" style="margin-left:6px">이번 런 직접 2xx <b>${okN}</b>${sc.dup_store ? ` · 과거 기록 의존 <b>${sc.dup_store}</b>` : ""}</span>
        <label class="muted" style="margin-left:8px;cursor:pointer">
          <input type="checkbox" id="hidedup-soft" ${hideDupSoft ? "checked" : ""}> 중복(이번 런) 숨기기${hiddenN ? ` (${hiddenN}행)` : ""}</label>
      </div>` : "");
  syncUnits($("r3-body"), units);
}

// the set of param NAMES this call actually SENT — query params (object keys) +
// any name that literally appears in the request-body snippet. Used to mark schema
// params as sent ∈ vs not, so the panel reads as a coverage hint.
function sentParamNames(c) {
  const sent = new Set();
  if (c.params && typeof c.params === "object") Object.keys(c.params).forEach(k => sent.add(k));
  const body = c.req_body || "";
  return { has: name => sent.has(name) || (name && body.indexOf('"' + name + '"') >= 0), querySet: sent };
}

// build the inline detail panel for one api call: actual request/response (event)
// + the endpoint parameter schema (catalog), marking sent vs not.
function apiDetailHtml(c) {
  const sent = sentParamNames(c);
  // (a) actual request params + body
  let reqParams = "<span class='muted small'>없음</span>";
  if (c.params && typeof c.params === "object" && Object.keys(c.params).length) {
    reqParams = Object.entries(c.params).map(([k, v]) =>
      `<code>${esc(k)}=${esc(typeof v === "object" ? JSON.stringify(v) : v)}</code>`).join(" ");
  }
  const reqBody = c.req_body
    ? `<pre class="apipre">${esc(c.req_body)}</pre>`
    : "<span class='muted small'>본문 없음 (GET 또는 빈 바디)</span>";
  // (b) response status + snippet
  const respSnip = c.resp_snippet
    ? `<pre class="apipre">${esc(c.resp_snippet)}</pre>`
    : "<span class='muted small'>응답 본문 스니펫 없음</span>";
  const statusLine = c.status != null
    ? `${badge(c.category)} <b>${esc(c.status)}</b>${c.ms != null ? ` · ${c.ms} ms` : ""}`
    : `${badge(c.category)} <span class="muted">진행 중…</span>`;
  // (c) endpoint parameter SCHEMA from the catalog (coverage hint)
  const schema = endpointParamsFor(c.method, c.path);
  let schemaHtml;
  if (!schema) {
    schemaHtml = "<span class='muted small'>카탈로그에 이 엔드포인트의 파라미터 정의 없음 "
      + "(또는 simulate 경로 — live 실행에서 채워집니다).</span>";
  } else {
    const rows = [];
    (schema.path_params || []).forEach(p => rows.push(schemaRow(p, "path", sent)));
    (schema.query_params || []).forEach(p => rows.push(schemaRow(p, "query", sent)));
    const sentN = rows.filter(r => r.sent).length;
    const tbody = rows.length
      ? rows.map(r => r.html).join("")
      : '<tr><td colspan="4" class="muted small">정의된 path/query 파라미터 없음 (본문 전용 엔드포인트일 수 있음)</td></tr>';
    schemaHtml = `<div class="muted small" style="margin-bottom:4px">카탈로그 키 <code>${esc(schema.key || "")}</code> · 보낸 파라미터 <b>${sentN}/${rows.length}</b></div>
      <table class="tbl schematbl"><thead><tr><th>param</th><th>위치</th><th>required</th><th>보냄?</th></tr></thead>
      <tbody>${tbody}</tbody></table>`;
  }
  return `<div class="apidet">
    <div class="apidet-grid">
      <div><div class="apidet-h">요청 — params</div>${reqParams}</div>
      <div><div class="apidet-h">요청 — body</div>${reqBody}</div>
      <div><div class="apidet-h">응답 — status</div><div>${statusLine}</div></div>
      <div><div class="apidet-h">응답 — 본문 snippet</div>${respSnip}</div>
    </div>
    <div class="apidet-h" style="margin-top:9px">파라미터 스키마 <span class="muted small">· 이 엔드포인트가 받을 수 있는 파라미터 (coverage hint)</span></div>
    ${schemaHtml}
  </div>`;
}

// one schema-param row + whether it was sent (for the sentN tally)
function schemaRow(p, loc, sent) {
  const name = p.name || "?";
  const wasSent = loc === "path" ? true : sent.has(name);    // path params are always supplied
  const req = p.required ? '<span class="phch created">required</span>' : '<span class="muted small">optional</span>';
  const mark = wasSent ? '<span class="tick">✓ 보냄</span>' : '<span class="tickno">—</span>';
  const type = p.type ? ` <span class="muted small">${esc(p.type)}</span>` : "";
  return { sent: wasSent, html: `<tr>
    <td><code>${esc(name)}</code>${type}</td>
    <td class="muted small">${loc}</td>
    <td>${req}</td>
    <td>${mark}</td></tr>` };
}

// endpoint parameter schema lookup: prefer the map shipped with /api/model
// (endpoint_params, keyed "METHOD norm(path)"); the server also exposes
// GET /api/endpoint-params for the same data.
function endpointParamsFor(method, path) {
  const map = (MODEL && MODEL.endpoint_params) || null;
  if (!map) return null;
  const key = (method || "").toUpperCase() + " " + normPathClient(path);
  return map[key] || null;
}

// MUST mirror the server's _ep_norm_path / engine._norm_path: strip leading slash
// + query, collapse {template} segments to '*'.
function normPathClient(p) {
  p = (p || "").split("?")[0].replace(/^\/+|\/+$/g, "");
  return p.split("/").map(s => s.indexOf("{") >= 0 ? "*" : s).join("/");
}

// 로그 — raw run log + cleanup/verify controls.
// FLICKER FIX: the panel SHELL is built once (idempotent — only when #r4-log is
// absent), never on every poll, and the log text is updated IN PLACE only when it
// actually changed (loadLog diffs against lastLogText). A dedicated SLOW (2s)
// poller refreshes the log while the run is running so the fast 0.7s event poll
// never rebuilds this panel. Scroll position is preserved unless the user is at
// the bottom — so they can read mid-log without being yanked down.
function reportR4() {
  const d = scopeData();
  if (!d.agg) { reportR4Scoped(d); stopR4Poll(); return; }   // per-lifecycle structured log
  // ---- 전체 (aggregate) scope: the raw run log + cleanup/verify controls ----
  // shell keyed by run+scope so switching INTO 전체 (from a scoped log) rebuilds it.
  const key = "agg:" + runId;
  const fresh = !$("r4-log") || $("r4-log").dataset.logkey !== key;
  if (fresh) {                   // build the shell ONCE per run/scope; redraws are no-ops
    lastLogText = null;          // force the first paint after a (re)build
    $("detail-body").innerHTML = `<h3 class="detail-h">로그 <span class="muted small">· 런 전체 실행 로그</span></h3>
      <div class="run-ctl">
        <button class="minibtn red" id="btn-cleanup" title="우리(owner)가 만든 자원을 강제 삭제 (reconciler, TTL 무시).">🧹 강제 클린업</button>
        <button class="minibtn" id="btn-verify" title="삭제 없이 남은 우리 자원 수 확인 (read-only).">🔍 클린업 확인</button>
        <button class="minibtn" id="btn-reflog">↻ 로그 새로고침</button>
      </div>
      <pre class="runlog" id="r4-log" data-logkey="${esc(key)}">로그 로딩…</pre>`;
    $("btn-reflog").onclick = () => loadLog(true);   // manual refresh → snap to bottom
    $("btn-cleanup").onclick = () =>
      // blind confirm 대신 fresh /api/owned 스캔이 채운 '삭제 대상 N건' 모달
      cleanupConfirm(j => {
        runId = j.id; runEvents = []; runStatus = "running"; detailTab = "log"; scopeAuto = true;
        drawReport(); startR4Poll();
        watchCleanup(j.id);   // 종료 후 실측 재스캔 + 의존 잠금 힌트 (신규7)
      });
    $("btn-verify").onclick = () => {
      fetch("/api/verify", { method: "POST" }).then(r => r.json()).then(j => {
        if (j.error) { alert(j.error); return; }
        runId = j.id; runEvents = []; runStatus = "running"; detailTab = "log"; scopeAuto = true;
        drawReport(); startR4Poll();
      }).catch(() => alert("서버 연결 실패"));
    };
  }
  // refresh the log only on the initial build or when the run is NOT running
  // (a finished run needs one load). While running, the dedicated 2s poller owns
  // refresh — so the 0.7s event poll calling drawReport() does NOT also fetch the
  // log, keeping the panel quiet (no flicker, no scroll fights).
  if (fresh || runStatus !== "running") loadLog();
  if (runStatus === "running") startR4Poll(); else stopR4Poll();
}

// 로그 (DETAIL · single lifecycle) — a STRUCTURED per-lifecycle log built from this
// lifecycle's events (api calls + status), filtered to the scope (the raw server log
// is whole-run and not cleanly splittable). Rebuilt in place each draw; cheap + no
// flicker (the content is derived, not fetched), and a failed lifecycle surfaces its
// fail line. The 전체 view still owns the raw run log + cleanup controls.
function reportR4Scoped(d) {
  const b = (groupedRun().lcs[d.id]) || { api: [], status: "queued" };
  const lines = b.api.map(c => {
    const cls = c.category === "fail" ? "err" : c.category === "soft" ? "warn" : c.category === "run" ? "dim" : "ok";
    const tag = c.category === "fail" ? (c.failNote ? "FAIL(" + c.failNote + ")" : "FAIL")
      : c.category === "soft" ? "SOFT" : c.category === "run" ? "…" : "ok";
    const code = c.status != null ? c.status : "";
    return `<span class="meth">${esc(c.method || "")}</span> ${esc(c.path || "")} `
      + `<span class="dim">(${esc(c.step || "")})</span> → <span class="${cls}">${esc(code)} ${tag}</span>`
      + (c.ms != null ? ` <span class="dim">${c.ms}ms</span>` : "");
  });
  if (d.status === "fail") lines.push('<span class="err">✕ 이 라이프사이클 실패 — 위 fail 스텝 + 잔존 자원 확인 (전체 로그/클린업은 전체 탭).</span>');
  else if (d.status === "done") lines.push('<span class="ok">✓ 라이프사이클 통과 — 자원 생성→삭제 완료.</span>');
  else if (d.status === "running") lines.push('<span class="dim">⏳ 진행 중…</span>');
  const logHtml = lines.length ? lines.join("\n") : "(이 라이프사이클의 API 이벤트 없음)";
  $("detail-body").innerHTML = `<h3 class="detail-h">로그 <span class="muted small">· 이 라이프사이클(${esc(d.id)})로 필터됨</span></h3>
    <pre class="runlog logbox">${logHtml}</pre>
    <p class="muted small">이 스코프의 API 호출 로그 — 런 전체 원시 로그 + 강제 클린업은 <b>전체</b> 보기에서.</p>`;
}

// near-bottom test: within ~24px of the bottom counts as "following the tail".
function _nearBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 24; }

// fetch the log and write it IN PLACE only if changed; keep scroll unless the user
// was at the bottom (or `force` after a manual refresh / fresh build).
function loadLog(force) {
  if (!runId) return;
  fetch("/api/runs/" + runId).then(r => r.json()).then(j => {
    runStatus = j.status || runStatus;    // P2C-24: r4 tick 의 이중 fetch 제거 — 상태도 여기서
    const pre = $("r4-log"); if (!pre) return;
    const txt = j.log || "(로그 없음)";
    if (txt === lastLogText) return;            // unchanged → don't touch the DOM
    const follow = force || lastLogText === null || _nearBottom(pre);
    const keepTop = pre.scrollTop;
    lastLogText = txt;
    pre.textContent = txt;
    pre.scrollTop = follow ? pre.scrollHeight : keepTop;   // stay put unless following
  }).catch(() => { /* keep last good log on a transient fetch error (no flicker) */ });
}
function startR4Poll() {
  if (r4LogTimer) return;        // single in-flight poller
  // P2C-24: 종전엔 tick 이 /api/runs/{id} 를 fetch 한 뒤 loadLog 가 또 fetch (이중)
  // + loadRunRecords 동승 — 로그 tick 은 loadLog 1회 fetch 로 통합, 3s, 숨은 탭 정지.
  const tick = () => {
    r4LogTimer = null;
    // only while the aggregate (전체) raw-log view is actually visible
    if (screen !== "run" || detailTab !== "log" || !isAggScope() || !$("r4-log")) return;
    if (document.hidden) { r4LogTimer = setTimeout(tick, HIDDEN_RETRY_MS); return; }
    loadLog();
    if (runStatus === "running") r4LogTimer = setTimeout(tick, 3000);
  };
  r4LogTimer = setTimeout(tick, 3000);
}
function stopR4Poll() {
  if (r4LogTimer) { clearTimeout(r4LogTimer); r4LogTimer = null; }
}

// ---- run records list ----
// 기본 필터 = "run만" (신규: owned/sim/클린업 스캔 기록 20여 건이 실제 run 1건을
// 파묻는 도배 방지) — 토글로 전체 표시. sessionStorage 로 선택 유지.
let runHistAll = false;
try { runHistAll = sessionStorage.getItem("c2.histAll") === "1"; } catch (e) { /* private mode */ }
function setRunHistAll(v) {
  runHistAll = !!v;
  try { sessionStorage.setItem("c2.histAll", runHistAll ? "1" : "0"); } catch (e) { /* ignore */ }
  loadRunRecords();
}
// 재스캔 라운드 1건 → 짧은 라벨. 스킵/실패는 0건과 반드시 구분해 표기 (신규).
function rescanChipLabel(e) {
  const t = "+" + Math.round((e.offset_s || 0) / 60) + "m";
  if (e.total != null) return t + " " + e.total + "건";
  if (e.skipped) return t + " 스킵";
  return t + " 실패";
}
// one run-record row (shared by the folded list AND the always-visible 최근 종료
// summary row above the fold).
function runRowHtml(r) {
  const KIND = { simulate: "▶sim", lifecycle: "▶live", cleanup: "🧹", verify: "🔍", owned: "🔍" };
  const icon = r.status === "queued" ? "⌛" : r.status === "running" ? "⏳"
    : r.status === "aborted" ? "⏹"
    : r.status === "done" ? (r.rc === 0 ? "✅" : "⚠️")
    : r.status === "unknown" ? "▪" : "❌";
  const dur = (r.ended && r.started) ? Math.round(r.ended - r.started) + "s"
    : (r.status === "running" ? "실행중…" : r.status === "queued" ? "대기 중…" : "");
  const on = runId === r.id;
  const tag = KIND[r.kind] || esc(r.kind || "");
  // 복원됨 = a rec rehydrated from disk after a server restart (신규2)
  const rehy = r.rehydrated
    ? ' <span class="kindtag rehy" title="서버 재시작 후 디스크 기록에서 복원됨">복원됨</span>' : "";
  // 종료 후 재스캔 상태: 각 라운드를 0건/스킵/실패로 구분해 표기 + 남은 라운드
  const scans = r.rescans || [];
  const planned = (r.rescan_offsets && r.rescan_offsets.length) || (scans.length ? scans.length : 0);
  const scanFull = scans.map(e => rescanChipLabel(e)
    + (e.skipped ? " (" + e.skipped + ")" : e.error ? " (" + e.error + ")" : "")).join(" · ");
  let late = "";
  if (r.late_alert) {
    late = ` <span class="latealert" title="${esc(r.late_alert.msg || "")}">⚠ 종료 후 자원 늦출현 ${r.late_alert.delta}건</span>`;
  } else if (r.kind === "lifecycle" && scans.length) {
    const pend = planned > scans.length ? " · 예정 " + (planned - scans.length) : "";
    late = ` <span class="muted small" title="${esc("종료 후 실측 재스캔 (+0·+5m·+15m): " + scanFull)}">재스캔 ${scans.map(rescanChipLabel).join(" · ")}${pend}</span>`;
  }
  return `<div class="runrow ${on ? "on" : ""}" data-id="${esc(r.id)}">
    <span><span class="kindtag">${tag}</span>${rehy} <b class="small">${icon} ${esc(r.id)}</b>
      <span class="muted small">${esc((r.lifecycle_ids || []).slice(0, 2).join(", "))}${(r.lifecycle_ids || []).length > 2 ? " …" : ""}</span>${late}</span>
    <span class="muted small">${esc(r.summary || r.status)} · ${dur}</span></div>`;
}

// the fold header (count) + the always-visible row: 실행 중이면 히어로가 전면이라
// 생략, 아니면 최근 종료 1건 요약 — 접힌 히스토리 밖에서도 "방금 무엇이 끝났나"는
// 한 줄로 보인다.
function renderHistHead(runsOnly) {
  const hc = $("hist-count");
  if (hc) hc.textContent = runsOnly.length;
  const cur = $("hist-current"); if (!cur) return;
  const inFlight = runId && (runStatus === "running" || runStatus === "queued");
  const lastDone = runsOnly.find(r => r.status !== "running" && r.status !== "queued");
  if (inFlight || !lastDone) { cur.innerHTML = ""; return; }
  cur.innerHTML = `<div class="hist-lastlbl muted small">최근 종료 — 클릭하면 리포트로</div>` + runRowHtml(lastDone);
  els("#hist-current .runrow").forEach(row => row.onclick = () => loadRunIntoReport(row.dataset.id));
}

function loadRunRecords() {
  fetch("/api/runs").then(r => r.json()).then(j => {
    const all = j.runs || [];
    handleLateAlerts(all);    // 종료 후 자원 늦출현 (신규1) — 알림 + 패널 재스캔 (필터와 무관)
    const host = $("report-side"); if (!host) return;
    const runsOnly = all.filter(r => r.kind === "lifecycle");
    renderHistHead(runsOnly);
    const hidden = all.length - runsOnly.length;
    const runs = runHistAll ? all : runsOnly;
    const filterBar = `<div class="histfilter">
      <button class="minibtn ${runHistAll ? "" : "on"}" id="hist-runs" title="실제 실행(run)만 표시">run만</button>
      <button class="minibtn ${runHistAll ? "on" : ""}" id="hist-all" title="스캔·클린업·확인·시뮬레이션 기록 포함">전체</button>
      <span class="muted small">${!runHistAll && hidden ? "스캔·클린업 등 " + hidden + "건 숨김" : ""}</span></div>`;
    if (!runs.length) {
      host.innerHTML = filterBar + '<p class="muted small">' +
        (all.length ? "표시할 run 이 없습니다 — '전체' 로 스캔·클린업 기록을 볼 수 있습니다." : "아직 실행 기록이 없습니다.") + "</p>";
      wireHistFilter();
      return;
    }
    host.innerHTML = filterBar + runs.map(runRowHtml).join("");
    els("#report-side .runrow").forEach(row => row.onclick = () => loadRunIntoReport(row.dataset.id));
    wireHistFilter();
  }).catch(() => { const host = $("report-side"); if (host) host.innerHTML = '<p class="muted small">서버 연결 실패</p>'; });
}
function wireHistFilter() {
  const a = $("hist-runs"), b = $("hist-all");
  if (a) a.onclick = () => setRunHistAll(false);
  if (b) b.onclick = () => setRunHistAll(true);
}

// a NEW late-resource alert (rescan found MORE than the +0 scan): prominent
// toast + banner state + auto-refresh the 남은 자원 panel once per run (신규1).
let lateAlertBanner = null;   // the newest alert text (rendered by drawLeftover)
function handleLateAlerts(runs) {
  (runs || []).forEach(r => {
    if (!r.late_alert || lateAlertSeen[r.id]) return;
    // seen-state survives page reloads (sessionStorage) and stale alerts
    // (rescans done >30min ago) don't re-toast/re-scan on every reload (L2)
    let seenStore = {};
    try { seenStore = JSON.parse(sessionStorage.getItem("lateAlertSeen") || "{}"); } catch (e) {}
    if (seenStore[r.id]) { lateAlertSeen[r.id] = true; return; }
    const rescans = r.rescans || [];
    const lastTs = rescans.length ? (rescans[rescans.length - 1].ts || 0) : 0;
    const stale = lastTs && (Date.now() / 1000 - lastTs) > 1800;
    lateAlertSeen[r.id] = true;
    seenStore[r.id] = true;
    try { sessionStorage.setItem("lateAlertSeen", JSON.stringify(seenStore)); } catch (e) {}
    if (stale) return;   // still visible as a history-row chip, just no toast/scan storm
    lateAlertBanner = `run ${r.id}: ${r.late_alert.msg || ("종료 후 자원 늦출현 " + r.late_alert.delta + "건")}`;
    toast("⚠ " + lateAlertBanner, "fail");
    if (screen === "run") scanOwned();       // 남은 자원 패널 실측 자동 갱신
  });
}

// ================= 📊 스케줄 시뮬 간트 (오너 2026-07-11) ========================
// ① 구성: launchSum 의 [📊 예상 타임라인] → pf 모달에 /api/schedule-sim 간트 +
//    워커/VPC 슬롯 조정·재계산. ② 실행: 리포트의 "예측 vs 실제 타임라인" 접이식
//    패널 — 예측(고스트, 열릴 때 1회 POST) 위에 lifecycle-start/end 실측을 겹친다.
//    재렌더는 기존 폴 사이클(drawReport)에 동승 — 새 폴링 타이머 금지 (P2C-24
//    폴링 스톰 회귀 방지). schedule-sim 은 오프라인 계산이라 라이브 API 호출 없음.
let pvaSim = null;          // 이 run 의 예측 결과 (/api/schedule-sim, run 당 1회)
let pvaSimFor = null;       // pvaSim 이 속한 runId — 재바인딩 시 재요청
let pvaLoading = false;     // 요청 in-flight 가드 (폴마다 중복 POST 방지)

// 예측 공유 로더 — PLAN/ACTUAL 스트립(접목 2)과 '예측 vs 실제' 패널이 같은
// pvaSim 캐시를 쓴다: 같은 화면에 가정이 다른 예측이 둘 뜨지 않게 (단일 소스),
// POST 도 run 당 1회로 유지. 완료 시 두 표면을 모두 재렌더.
function ensurePvaSim() {
  if (!runId || pvaSimFor === runId || pvaLoading) return;
  const ids = (runSelIds && runSelIds.length) ? runSelIds : Object.keys(lifecycleStates());
  // 이 run 의 선택이 아직 안 왔으면 기다린다 (첫 이벤트 응답 전) — 빈 ids 로
  // 보내면 서버가 "전체 플랫폼(124종)" 예측을 돌려줘 이 run 예측처럼 오독된다.
  if (!ids.length) return;
  pvaLoading = true;
  const rid = runId;
  simFetch({ lifecycle_ids: ids }).then(({ ok, j }) => {
    pvaLoading = false;
    if (runId !== rid) return;               // 폴 도중 재바인딩 — 응답 폐기
    pvaSimFor = rid;
    pvaSim = (ok && !j.error) ? j
      : { error: (j && j.error) || "서버가 /api/schedule-sim 을 지원하지 않습니다" };
    renderPva(); renderPlanActual();
  }).catch(e => {
    pvaLoading = false;
    if (runId !== rid) return;
    pvaSimFor = rid; pvaSim = { error: e.message };
    renderPva(); renderPlanActual();
  });
}

function simFetch(body) {
  return fetch("/api/schedule-sim", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) })
    .then(r => r.json().then(j => ({ ok: r.ok, j })));
}
const simMin = s => (s / 60).toFixed(1);
const laneName = w => "w" + String(w).padStart(2, "0");

// ---- 공용 간트 조각: 스케일(분→px) · 분 축 · hover 툴팁 -----------------------
// 가로 넘침은 .sim-gantt(overflow-x:auto)가 소화 — 페이지 가로 스크롤 금지.
function simScale(horizonS) {
  const mins = Math.max(horizonS / 60, 0.5);
  const ppm = Math.max(4, Math.min(60, 720 / mins));   // 분당 px — 총폭 ~720px 목표
  return { ppm, totalW: Math.ceil(mins * ppm) + 4, mins };
}
function simAxisHtml(sc) {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 240];
  const step = steps.find(s => s * sc.ppm >= 64) || 480;   // 눈금 간 ≥64px
  let t = "";
  for (let m = 0; m <= sc.mins; m += step)
    t += `<div class="sim-tick" style="left:${Math.round(m * sc.ppm)}px"><i></i>${m}분</div>`;
  return `<div class="sim-row axis"><span class="sim-lane"></span>
    <div class="sim-track axis" style="width:${sc.totalW}px">${t}</div></div>`;
}
// fixed 툴팁 1개를 공유 — 컨테이너에 mousemove 위임 (기존 title 툴팁보다 즉답).
function simTipEl() {
  let t = $("sim-tip");
  if (!t) { t = document.createElement("div"); t.id = "sim-tip"; t.className = "sim-tip"; document.body.appendChild(t); }
  return t;
}
function wireSimTips(host) {
  if (!host || host._simTip) return;
  host._simTip = true;
  host.addEventListener("mousemove", e => {
    const b = e.target.closest("[data-tip]");
    const t = simTipEl();
    if (!b || !host.contains(b)) { t.style.display = "none"; return; }
    t.textContent = b.dataset.tip;
    t.style.display = "block";
    t.style.left = Math.min(e.clientX + 14, window.innerWidth - t.offsetWidth - 10) + "px";
    t.style.top = Math.min(e.clientY + 18, window.innerHeight - t.offsetHeight - 8) + "px";
  });
  host.addEventListener("mouseleave", () => { simTipEl().style.display = "none"; });
}

// ---- ① 구성: 예상 타임라인 모달 — 행=워커(w00~), 가로=분 ---------------------
function simGanttHtml(sim) {
  const bars = sim.bars || [];
  if (!bars.length) return '<p class="empty">배치할 시나리오가 없습니다.</p>';
  const sc = simScale(sim.makespan_s);
  const lanes = [];
  bars.forEach(b => { (lanes[b.w] = lanes[b.w] || []).push(b); });
  const rows = lanes.map((bs, w) => {
    const cells = (bs || []).map(b => {
      const x = b.s / 60 * sc.ppm, wpx = Math.max((b.e - b.s) / 60 * sc.ppm, 3);
      const tip = `${b.id} · ${simMin(b.s)}→${simMin(b.e)}분 · 소요 ${fmtDur(b.e - b.s)} · ${laneName(w)}`
        + ` · ${b.vpc ? "VPC 자체생성 (슬롯 점유)" : "공유 VPC"}${b.measured ? "" : " · 미측정(클래스 기본값)"}`;
      return `<div class="sim-bar${b.vpc ? " vpc" : ""}${b.measured ? "" : " est"}"
        style="left:${x.toFixed(1)}px;width:${wpx.toFixed(1)}px" data-tip="${esc(tip)}">${
        wpx >= 90 ? `<span>${esc(b.id)}</span>` : ""}</div>`;
    }).join("");
    return `<div class="sim-row"><span class="sim-lane">${laneName(w)}</span>
      <div class="sim-track" style="width:${sc.totalW}px">${cells}</div></div>`;
  }).join("");
  return `<div class="sim-gantt">${simAxisHtml(sc)}${rows}</div>`;
}
function openSimModal() {
  pfOpen("📊 예상 타임라인 — 스케줄 시뮬레이션 (오프라인 계산)");
  $("pf-modal").classList.add("sim-wide");
  simRecalc(null, null);
}
function simRecalc(workers, slots) {
  $("pf-body").innerHTML = '<p class="muted small">예상 배치 계산 중… (/api/schedule-sim)</p>';
  const body = targets.size ? selectionPayload() : {};   // 선택 비면 {} → 서버가 전체 enabled
  if (workers) body.workers = workers;
  if (slots) body.vpc_slots = slots;
  simFetch(body).then(({ ok, j }) => {
    if (!ok || j.error) {
      $("pf-body").innerHTML = '<p class="empty">시뮬레이션 실패: '
        + esc((j && j.error) || "서버가 /api/schedule-sim 을 지원하지 않습니다") + "</p>";
      return;
    }
    renderSimModal(j);
  }).catch(e => {
    $("pf-body").innerHTML = '<p class="empty">시뮬레이션 연결 실패: ' + esc(e.message) + "</p>";
  });
}
function renderSimModal(sim) {
  $("pf-body").innerHTML =
    `<div class="sim-stats">예상 makespan <b>${simMin(sim.makespan_s)}분</b>
       · 시나리오 <b>${(sim.bars || []).length}</b> (${targets.size ? "현재 선택" : "전체 enabled"})
       · 워커 <b>${sim.workers}</b> · VPC 슬롯 <b>${sim.vpc_slots}</b></div>
     <div class="legend sim-legend">${legend([["#2563c9", "일반 (공유 VPC)"], ["#eb6834", "VPC 자체생성 (슬롯 점유)"]])}
       <span>점선 테두리 = 미측정(클래스 기본값) · 막대 hover = 상세</span></div>
     ${simGanttHtml(sim)}`;
  $("pf-foot").innerHTML =
    `<span class="muted small">conftest 와 동일 규칙(실측 LPT greedy) · 미모델: IGW/NAT 1:1 대기·재시도</span>
     <label class="small muted">워커 <input type="number" class="sim-num" id="sim-w" min="1" max="64" value="${sim.workers}"></label>
     <label class="small muted">VPC 슬롯 <input type="number" class="sim-num" id="sim-v" min="1" max="16" value="${sim.vpc_slots}"></label>
     <button class="btn" id="sim-recalc">↻ 재계산</button>`;
  $("sim-recalc").onclick = () => simRecalc(
    parseInt($("sim-w").value, 10) || sim.workers,
    parseInt($("sim-v").value, 10) || sim.vpc_slots);
  wireSimTips($("pf-body"));
}

// ---- ② 실행: 예측 vs 실제 타임라인 (실시간) ----------------------------------
// 행=시나리오(예측 시작순), 고스트=예측(테두리+8% 틴트), 채움=실측 start→(end|now),
// 예측 종료 초과분은 amber. 실측 소스 = runEvents 의 lifecycle-start/end ts,
// t0 = 이 run 의 최소 ts (groupEventsByLifecycle 과 같은 스트림).
function pvaEnsure() {
  const master = $("report-master");
  if (!master) return null;
  let d = $("pva-panel");
  if (!d) {
    d = document.createElement("details");
    d.id = "pva-panel"; d.className = "pva";
    d.innerHTML = `<summary>📊 예측 vs 실제 타임라인
        <span class="muted small">— 고스트=예측(schedule-sim) · 채움=실측 · amber=예측 초과</span></summary>
      <div id="pva-body"></div>`;
    master.appendChild(d);
    d.addEventListener("toggle", () => { if (d.open) renderPva(); });
    wireSimTips(d);
  }
  return d;
}
function renderPva() {
  const d = pvaEnsure(); if (!d) return;
  const body = $("pva-body"); if (!body) return;
  if (!runId) {
    pvaSim = null; pvaSimFor = null;
    setHtmlIfChanged(body, '<p class="muted small pva-empty">실행 중인 런 없음</p>');
    return;
  }
  if (!d.open) return;                       // 접힌 동안은 렌더/페치 생략 (비용 0)
  if (pvaSimFor !== runId) {                 // 열릴 때 / run 재바인딩 시 1회 예측
    setHtmlIfChanged(body, '<p class="muted small pva-empty">예측 스케줄 계산 중…</p>');
    ensurePvaSim();                          // 공유 로더 — 스트립과 같은 캐시/가드
    return;
  }
  if (pvaSim && pvaSim.error) {
    setHtmlIfChanged(body, '<p class="empty">예측 실패: ' + esc(pvaSim.error) + "</p>");
    return;
  }
  setHtmlIfChanged(body, pvaHtml(pvaSim || {}));
}
function pvaHtml(sim) {
  const act = {}; let t0 = null, tLast = null;
  (runEvents || []).forEach(e => {
    if (e.ts == null) return;
    if (t0 === null || e.ts < t0) t0 = e.ts;
    if (tLast === null || e.ts > tLast) tLast = e.ts;
    if (!e.lifecycle) return;
    const a = act[e.lifecycle] = act[e.lifecycle] || {};
    if (e.kind === "lifecycle-start") { if (a.s == null) a.s = e.ts; }
    else if (e.kind === "lifecycle-end") a.e = e.ts;
  });
  const running = runStatus === "running" || runStatus === "queued";
  // 진행 중 = now 까지 자라는 막대 (폴 재렌더로 충분히 부드럽다); 종료 런의
  // 미종결(중단) lifecycle 은 마지막 이벤트 시각까지만 — 영원히 자라지 않게.
  const nowRel = t0 != null ? (running ? Date.now() / 1000 : tLast) - t0 : 0;
  const bars = ((sim && sim.bars) || []).slice()
    .sort((a, b) => a.s - b.s || a.e - b.e || (a.id < b.id ? -1 : 1));
  const known = new Set(bars.map(b => b.id));
  // 예측에 없는 실측 lifecycle(합성 등) 도 행으로 — 고스트 없이 실측만.
  Object.keys(act).sort().forEach(id => { if (!known.has(id)) bars.push({ id, s: null, e: null, vpc: false }); });
  if (!bars.length) return '<p class="muted small pva-empty">표시할 시나리오 없음 — 이벤트 대기 중</p>';
  let hor = 0;
  bars.forEach(b => {
    if (b.e != null) hor = Math.max(hor, b.e);
    const a = act[b.id];
    if (a && a.s != null && t0 != null) hor = Math.max(hor, a.e != null ? a.e - t0 : nowRel);
  });
  const sc = simScale(Math.max(hor, 60));
  const px = s => s / 60 * sc.ppm;
  const rows = bars.map(b => {
    const a = act[b.id] || {};
    let seg = "";
    if (b.s != null) {
      const gtip = `${b.id} · 예측 ${simMin(b.s)}→${simMin(b.e)}분 · ${fmtDur(b.e - b.s)}`
        + ` · ${laneName(b.w || 0)}${b.vpc ? " · VPC 자체생성" : ""}`;
      seg += `<div class="pva-ghost${b.vpc ? " vpc" : ""}" style="left:${px(b.s).toFixed(1)}px;width:${
        Math.max(px(b.e - b.s), 3).toFixed(1)}px" data-tip="${esc(gtip)}"></div>`;
    }
    if (a.s != null && t0 != null) {
      const as = a.s - t0;
      const ae = a.e != null ? a.e - t0 : nowRel;
      const live = a.e == null && running;
      const atip = `${b.id} · 실제 ${simMin(as)}→${a.e != null ? simMin(ae) + "분" : (live ? "진행 중" : "미종료(중단)")}`
        + ` · ${fmtDur(Math.max(ae - as, 0))}`
        + (b.e != null && ae > b.e ? ` · 예측 대비 +${fmtDur(ae - b.e)}` : "");
      if (b.e != null && ae > b.e && as < b.e) {           // 예측 안 구간 + 초과 구간
        seg += `<div class="pva-act${live ? " live" : ""}" style="left:${px(as).toFixed(1)}px;width:${
          Math.max(px(b.e - as), 2).toFixed(1)}px" data-tip="${esc(atip)}"></div>`
          + `<div class="pva-act over${live ? " live" : ""}" style="left:${px(b.e).toFixed(1)}px;width:${
            Math.max(px(ae - b.e), 2).toFixed(1)}px" data-tip="${esc(atip)}"></div>`;
      } else {
        const over = b.e != null && as >= b.e;             // 통째로 예측 종료 이후 시작
        seg += `<div class="pva-act${over ? " over" : ""}${live ? " live" : ""}" style="left:${
          px(as).toFixed(1)}px;width:${Math.max(px(ae - as), 2).toFixed(1)}px" data-tip="${esc(atip)}"></div>`;
      }
    }
    return `<div class="sim-row pva-row"><span class="sim-lane pva-lbl" title="${esc(b.id)}">${esc(b.id)}</span>
      <div class="sim-track pva-track" style="width:${sc.totalW}px">${seg}</div></div>`;
  }).join("");
  const head = sim && sim.makespan_s != null
    ? `<div class="sim-stats small">예측 makespan <b>${simMin(sim.makespan_s)}분</b>
        (워커 ${sim.workers} · VPC 슬롯 ${sim.vpc_slots}) · 실제 경과 <b>${
        t0 != null ? simMin(Math.max(nowRel, 0)) + "분" : "—"}</b>${running ? " · 진행 중" : ""}</div>`
    : "";
  return `${head}<div class="sim-gantt pva-gantt">${simAxisHtml(sc)}${rows}</div>`;
}

// ================= shared helpers =================
// the server resolves node_ids → graph_view targets (and → source lifecycles for a
// run). Sending node_ids keeps the "selection = resources" contract.
// 서비스의 선택 가능한 노드가 전부 켜져 있으면 그 서비스를 "통째로 선택"한 것 —
// services 를 함께 실어 서버의 §2 해석(그 서비스로 태그된 enabled+verify lifecycle
// 전부, 노드가 가리키지 않는 합성 lifecycle 포함)이 발동하게 한다. 부분 선택은
// node_ids 범위 그대로 (서비스 전체로 부풀리지 않음).
function selectionPayload() {
  const services = allSelectableServices().filter(s => svcState(s) === "on");
  return { node_ids: [...targets], services };
}

function legend(items) {
  return items.map(i => `<span><i style="background:${i[0]}"></i>${esc(i[1])}</span>`).join("");
}
// ok/soft/fail 집계 규칙 툴팁 — regression.scenarios.engine.categorize 의 실제
// 규칙을 그대로 설명한다 (2xx=ok · 5xx/HMAC-401=fail · 나머지 4xx=soft).
const CAT_TIP = {
  ok: "ok = 2xx 응답 (정상)",
  soft: "soft = 하드 실패가 아닌 거절: 400·403·404·409·422 등 4xx (파라미터/권한/선행자원 필요) " +
    "+ 게이트웨이 거절 401. 이 계정 조건에서 API가 올바르게 응답한 것으로 집계",
  fail: "fail = 5xx 서버 오류 또는 HMAC 인증 401 — 백엔드/인증 하드 실패 " +
    "(step-end 없이 lifecycle 이 끝난 timeout/중단 스텝 포함)",
  run: "진행 중 — 아직 응답 전",
};
function badge(cat) {
  const m = { ok: "ok", soft: "soft", fail: "fail", run: "run" };
  const c = m[cat] || "queued";
  return `<span class="bdg ${c}" title="${esc(CAT_TIP[cat] || "")}">${esc(cat || "—")}</span>`;
}

})();
