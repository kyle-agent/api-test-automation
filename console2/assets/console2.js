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
let collapsed = null;       // Set of collapsed category names (menu-tree). null = not yet initialised
let ownedScan = null;       // last /api/owned result {status, owned, owned_total} for the run-screen panel

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
let pollTimer = null;
let lastLogText = null;     // last log text written to the 로그 <pre> (in-place diff → no flicker)
let r4LogTimer = null;      // dedicated slow (2s) log poller while running (detail 로그 tab)
let expandedApi = null;     // key of the currently-expanded API row (detail API tab)

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
let r1Scene = null;         // 흐름 (#r1-svg) scene
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
  // a small, meaningful default so the DAG isn't empty
  ["vpc", "subnet"].forEach(id => { if (N[id] && N[id].lifecycle) targets.add(id); });
  if (!targets.size) {
    const id = Object.keys(N).find(i => N[i].lifecycle);
    if (id) targets.add(id);
  }
  // a ?service=<cat>/<svc> deep-link (the dashboard's per-service "console2 →"
  // links) overrides the default and pre-selects that service.
  deepLinkService();
  wireNav();
  wireModal();
  wireLaunch();
  wireSuites();
  go("build");
}

// ---- ?service=<cat>/<svc> deep-link (from the dashboard's per-service links) ----
// If present and resolvable to a selectable service, REPLACE the default selection
// with that whole service so the dashboard "console2 →" links land here focused on
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
}
function go(scr) {
  screen = scr;
  // leaving the run screen: tear down the 흐름 master scene (its window listeners must
  // not dangle on a hidden stage) and stop the log poller. Rebuilt cleanly on return
  // (the scene shell is keyed by runId → a fresh build re-attaches everything).
  if (scr !== "run") {
    if (r1Scene) { r1Scene.destroy(); r1Scene = null; }
    if (stagedScene) { stagedScene.destroy(); stagedScene = null; }
    stopR4Poll();
    stopCapPoll();           // leaving the run screen → stop the capacity poll
  }
  ["build", "run"].forEach(s => $("screen-" + s).classList.toggle("hidden", s !== scr));
  els("#screenToggle button").forEach(b => b.classList.toggle("on", b.dataset.scr === scr));
  ctxBar();
  if (scr === "build") drawBuild();
  else drawRunScreen();
}
window.go = go;

// ---- global context bar ----
function ctxBar() {
  const closureK = lastGraph ? lastGraph.nodes.length : "…";
  const svcs = new Set([...targets].map(id => N[id].service));
  const heavyTargets = [...targets].some(id => N[id].heavy);
  const heavyClosure = lastGraph ? lastGraph.nodes.some(n => n.heavy) : heavyTargets;
  $("ctxbar").innerHTML =
    `<span class="seg">env <b>local</b></span>
     <span class="seg">· 선택 <b>${targets.size}</b> 리소스</span>
     <span class="seg">· 서비스 <b>${svcs.size}</b></span>
     <span class="seg">· 폐포 <b>${closureK}</b></span>
     <span class="seg">· heavy <b>${heavyClosure ? "🜂 포함" : "없음"}</b></span>
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

// the selection readout: 선택: N 서비스 · M 리소스 · 폐포 K (+ heavy flag)
function selReadout() {
  const svcs = new Set([...targets].map(id => N[id].service));
  const K = lastGraph ? lastGraph.nodes.length : "…";
  const heavy = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  $("sel-readout").innerHTML =
    `선택: <b>${svcs.size}</b> 서비스 · <b>${targets.size}</b> 리소스 · 폐포 <b>${K}</b>` +
    `<span class="${heavy ? "hvflag" : ""}">${heavy ? " · 🜂 heavy" : ""}</span>`;
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
        const heavy = all.some(id => N[id].heavy);
        const quota = all.some(id => N[id].quota);
        const onN = sel.filter(id => targets.has(id)).length;
        const cls = st === "on" ? "on" : st === "partial" ? "partial" : "";
        const noLc = !sel.length;
        const fracTxt = !sel.length ? "—" : st === "partial" ? `${onN}/${sel.length}` : `${sel.length}`;
        h += `<div class="trow tsvc-row ${cls} ${noLc ? "nolc" : ""}" data-svc="${esc(svc)}" title="${esc(svc)}${noLc ? " — 생애주기 없음(의존전용)" : " — 클릭하면 서비스 전체 선택"}">
            <span class="tchk svc">${st === "on" ? "✓" : st === "partial" ? "◐" : ""}</span>
            <span class="tname">${esc(shortName(svc))}${heavy ? ' <span class="glyph" title="heavy 포함">🜂</span>' : ""}${quota ? ' <span class="glyph q" title="quota 제약">⛔</span>' : ""}</span>
            <span class="tcount">${fracTxt}</span>
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
    if (ev.target.closest("[data-res-svc]")) return;   // the "리소스…" button has its own handler
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
  selReadout();
}

// any selection change: re-render the tree (state), readout, and re-fetch the DAG
function selectionChanged() {
  if (screen === "build") drawSvcTree();
  selReadout();
  ctxBar();
  refreshGraph();
  if (screen === "build") launchSummary();
}

// ---- live composition DAG via /api/graph (debounced) ----
function refreshGraph() {
  if (graphTimer) clearTimeout(graphTimer);
  graphTimer = setTimeout(fetchGraph, 180);
}
function fetchGraph() {
  const body = selectionPayload();
  fetch("/api/graph", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json()).then(g => {
      if (g.error) { renderGraphError(g.error); return; }
      lastGraph = g;
      renderGraph(g);
      ctxBar(); selReadout(); launchSummary();
    }).catch(e => renderGraphError(e.message));
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
  ]) + '<span>그룹 = <b>접힘</b>(클릭=펼치기) · <span style="color:var(--val)">●</span> VALIDATED · <span style="color:var(--docs)">●</span> docs · 🜂 heavy · ⛔ quota</span>';
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
function orderTable(g, focus) {
  const scopeSet = focus && focus.resourceIds ? new Set(focus.resourceIds) : null;
  const inScope = id => !scopeSet || scopeSet.has(id);
  const createOrder = [], seen = new Set();
  (g.order || []).forEach(inst => { const b = baseId(inst); if (!seen.has(b) && inScope(b)) { seen.add(b); createOrder.push(b); } });
  // teardown rank by base node (first occurrence)
  const delRank = {}; let r = 0;
  (g.teardown || []).forEach(inst => { const b = baseId(inst); if (!(b in delRank) && inScope(b)) delRank[b] = ++r; });
  const nodeById = {}; (g.nodes || []).forEach(n => { nodeById[n.id] = n; });
  // scope note (shown in 표 mode; harmless in 그림 mode where the table is hidden)
  const titleEl = $("dag-table-title");
  if (titleEl) {
    const note = scopeSet
      ? `<div class="tab-scope">focus: <b>${esc(focus.label)}</b> 경로 · <b>${createOrder.length}</b> 자원 (전체 ${g.nodes.length})</div>`
      : `<div class="tab-scope">전체 선택 · <b>${createOrder.length}</b> 자원</div>`;
    titleEl.innerHTML = `생성 · 검증 · 삭제 순서표${note}`;
  }
  const rows = createOrder.map((id, i) => {
    const n = nodeById[id] || {};
    const verifyN = (N[id] && N[id].verify_n != null) ? N[id].verify_n : 0;
    const tgt = n.is_target ? '<span class="bdg run" style="border:none;background:none;color:var(--accent);padding:0">★</span>' : "";
    const sh = n.shared ? '<span class="tag amber" title="공유(dedup)">공유</span>' : "";
    return `<tr>
      <td class="ordn">${i + 1}</td>
      <td><b>${esc(id)}</b> ${tgt} ${sh}${n.heavy ? " 🜂" : ""}</td>
      <td class="muted">${esc(shortName(n.service || (N[id] && N[id].service) || ""))}</td>
      <td class="ordn">${verifyN}</td>
      <td class="ordn">${delRank[id] || "—"}</td>
    </tr>`;
  }).join("");
  $("order-tbl").innerHTML =
    `<thead><tr><th>생성#</th><th>리소스</th><th>service</th><th>검증(verify)</th><th>삭제#</th></tr></thead>` +
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
          <b>${esc(id)}</b>${n.heavy ? " 🜂" : ""}</label>
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

// ================= launch bar (carry selection into ②) =================
// 구성 ▶ no longer RUNS — it STAGES (enqueues) the current selection and crosses to
// ② where the deliberate, budget-informed [▶ 실행] commits it. The run-settings panel
// on ② keeps its own direct LIVE button (startRun) for the current selection.
function wireLaunch() {
  const lg = $("launch-go"); if (lg) lg.onclick = stageRun;
  const rg = $("run-go"); if (rg) rg.onclick = startRun;   // drawRunSettings rebuilds + rebinds this too
}

// a small uuid for a staged item key (crypto.randomUUID when available, else a
// timestamp+random fallback so older/file:// contexts still get a unique id).
function uuid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
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
  const heavyGuess = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  // resolve peak_vpcs + heavy from the REAL plan; fall back to the local guess if the
  // pre-flight plan call fails (the staged item is still actionable on ②).
  const add = (peak, heavy) => {
    STAGED.push({ id: uuid(), selection, nServices, nResources,
                  peak_vpcs: peak, heavy: heavy, closure });
    go("run");
  };
  fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) })
    .then(r => r.json()).then(plan => {
      plan = plan || {};
      const peak = plan.peak_vpcs || 0;
      const heavy = Object.values(plan.preview || {}).some(p => p && p.heavy) || heavyGuess;
      add(peak, heavy);
    }).catch(() => add(0, heavyGuess));
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
  const K = lastGraph ? lastGraph.nodes.length : "…";
  const heavy = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  const pq = lastGraph ? Object.values(lastGraph.peak_quota || {}).reduce((a, b) => a + b, 0) : 0;
  $("launchSum").innerHTML =
    `대상 <b>${svcs.size}</b> svc / <b>${targets.size}</b> 리소스 · 폐포 <b>${K}</b> · peak quota <b>${pq}</b> · ` +
    `🜂heavy <span class="${heavy ? "hv" : ""}">${heavy ? "포함" : "없음"}</span>`;
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
  // capacity bar (VPC budget + 진행중/대기 큐) at the TOP — the hero of concurrent
  // execution — then the 실행 대기열(STAGED) where each item is committed with a
  // budget-informed [▶ 실행], then the pre-flight 남은 자원(잔존) panel + run settings.
  $("run-left").innerHTML =
    '<div id="cap-bar"></div><div id="staged-panel"></div><div id="leftover-panel"></div><div id="run-settings"></div>';
  drawCapBar();
  drawStagedPanel();
  startCapPoll();           // poll /api/capacity every ~2s while on the run screen
  drawLeftover();
  drawRunSettings();
  drawReport();
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
    body = `<div class="staged-empty muted small">대기열 비어 있음 — ① 구성에서 선택 후 '실행 대기열에 추가'</div>`;
  } else {
    body = STAGED.map(it => {
      const open = stagedOpen === it.id;
      const over = (it.peak_vpcs || 0) > headroom;
      const summary = `<b>${it.nServices}</b> 서비스 · <b>${it.nResources}</b> 리소스 · VPC <b>${it.peak_vpcs || 0}</b> 필요${it.heavy ? " 🜂" : ""}`;
      const detail = open ? `<div class="staged-detail">
          <div class="staged-facts">필요 VPC <b>${it.peak_vpcs || 0}</b> · 폐포 <b>${it.closure}</b> · 현재 여유 <b>${headroom}</b></div>
          ${over ? `<div class="staged-over">여유 부족 → 대기 큐로 들어갑니다</div>` : ""}
          <div class="staged-act">
            <button class="minibtn go" data-stage-run="${esc(it.id)}">▶ 실행</button>
            <button class="minibtn red" data-stage-del="${esc(it.id)}">✕ 제거</button>
          </div>
        </div>` : "";
      return `<div class="staged-item ${open ? "open" : ""}">
        <button class="staged-row" data-stage-tog="${esc(it.id)}" title="클릭하면 상세 · 실행/제거">
          <span class="staged-sum">${summary}</span>
          <span class="staged-car">${open ? "▾" : "▸"}</span>
        </button>${detail}</div>`;
    }).join("");
  }
  host.innerHTML = `<div class="panel staged-pnl">
    <h2>실행 대기열 <span class="muted small">· 구성에서 추가</span></h2>
    ${body}
  </div>`;
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
  host.dataset.preview = item.id;
  host.innerHTML = `<div class="nowbar"><span class="dot" style="background:var(--accent)"></span>
      <b>대기열 미리보기</b> · <span class="muted small">${item.nServices} 서비스 · ${item.nResources} 리소스 · 폐포 ${item.closure} · VPC ${item.peak_vpcs || 0} 필요${item.heavy ? " 🜂" : ""}</span>
      <span class="muted small" style="margin-left:auto">실행 전 미리보기 — 실제 실행은 좌측 [▶ 실행]</span></div>
    <div class="legend" id="sp-legend"></div>
    <div class="stage-wrap"><div class="stage" id="sp-stage">
        <svg id="sp-svg" class="scene-svg" xmlns="http://www.w3.org/2000/svg"></svg>
        <div class="hint-pill" id="sp-hint"></div>
        <div class="zoomctl"><button id="sp-zin" title="확대">+</button><button id="sp-zout" title="축소">−</button><button id="sp-zfit" class="fit" title="전체 보기">맞춤</button></div>
      </div></div>`;
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

// [▶ 실행] — commit ONE staged item: POST /api/run for its selection (the server
// admits it under the cap or auto-queues it), drop it from STAGED, and drive the
// existing report flow (set runId, drawReport / wait banner, pollEvents) exactly as
// the direct startRun→postRun path does. No confirm — staging WAS the deliberation.
function runStaged(item) {
  const body = Object.assign({ mode: "live" }, item.selection);
  $("report-main").innerHTML = '<p class="muted small">실행 요청 중…</p>';
  fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json()).then(j => {
      if (j.error) { $("report-main").innerHTML = '<p class="empty">실행 실패: ' + esc(j.error) + "</p>"; return; }
      // succeeded → remove the committed item from the queue and re-render the panel
      STAGED = STAGED.filter(x => x.id !== item.id);
      if (stagedOpen === item.id) stagedOpen = null;
      drawStagedPanel();
      runId = j.id; runEvents = []; runStatus = j.status || "running";
      detailScope = "*"; detailTab = "res"; scopeAuto = true; expandedApi = null;
      if (runStatus === "queued") {
        $("report-main").innerHTML =
          '<div class="nowbar"><span class="dot"></span><b>대기 큐에서 대기 중</b> — 여유가 생기면 자동 실행</div>';
        $("lc-picker").innerHTML = "";
      } else {
        drawReport();
      }
      pollEvents();
      drawCapBar();           // reflect the new run in the capacity bar immediately
    }).catch(e => { $("report-main").innerHTML = '<p class="empty">실행 연결 실패: ' + esc(e.message) + "</p>"; });
}

// ---- capacity bar (GET /api/capacity, polled ~2s while on the 실행 screen) ------
// The visible surface of the cross-run admission model: VPC budget (used/cap +
// headroom) + a 진행중 chip per running run and a 대기 chip per queued run. Clicking
// a running chip loads that run into the report. Light theme, compact; reuses the
// chip/kindtag styles. The poll timer is cleared in go() when leaving the screen.
let capTimer = null;
let lastCapacity = null;    // last /api/capacity payload (for the 강제 클린업 disable)
function startCapPoll() {
  stopCapPoll();
  const tick = () => {
    if (screen !== "run") { capTimer = null; return; }
    fetch("/api/capacity").then(r => r.json()).then(c => {
      if (c.error) return;
      lastCapacity = c;
      if (screen === "run") { drawCapBar(); drawStagedPanel(); drawLeftover(); }
    }).catch(() => { /* transient — keep last good capacity */ })
      .finally(() => { if (screen === "run") capTimer = setTimeout(tick, 2000); });
  };
  capTimer = setTimeout(tick, 2000);
}
function stopCapPoll() { if (capTimer) { clearTimeout(capTimer); capTimer = null; } }

function drawCapBar() {
  const host = $("cap-bar"); if (!host) return;
  const c = lastCapacity;
  if (!c) {
    host.innerHTML = `<div class="panel cap-panel"><h2>실행 용량 <span class="muted small">· /api/capacity — VPC 동시 실행 한도</span></h2>
      <div class="muted small">용량 확인 중…</div></div>`;
    return;
  }
  const cap = c.cap || 0;
  const used = (c.baseline || 0) + (c.reserved || 0);
  const headroom = c.headroom != null ? c.headroom : Math.max(0, cap - used);
  const running = c.running || [], queued = c.queued || [];
  const idTail = id => (id || "").slice(-6);
  // a cap-cell meter: one cell per VPC slot, filled = used (baseline + reserved).
  const cells = [];
  for (let i = 0; i < cap; i++) cells.push(`<i class="${i < used ? "on" : ""}"></i>`);
  const runChips = running.length
    ? running.map(r => `<button class="capchip run" data-runid="${esc(r.id)}" title="${esc(r.id)} — 리포트 열기">
        <span class="kindtag">${esc(idTail(r.id))}</span> ${r.peak_vpcs || 0} VPC${r.heavy ? " 🜂" : ""}</button>`).join("")
    : '<span class="muted small">없음</span>';
  const queChips = queued.length
    ? queued.map(r => `<span class="capchip que" title="${esc(r.id)} — 여유가 생기면 자동 실행">
        <span class="kindtag">${esc(idTail(r.id))}</span> ${r.peak_vpcs || 0} VPC 필요 · 여유 ${headroom}</span>`).join("")
    : '<span class="muted small">없음</span>';
  host.innerHTML = `<div class="panel cap-panel">
    <h2>실행 용량 <span class="muted small">· VPC 동시 실행 한도 (cap) — ADMIT/대기 큐</span></h2>
    <div class="cap-head"><b>VPC ${used}/${cap}</b> <span class="muted">· 여유 ${headroom}</span></div>
    <div class="cap-meter">${cells.join("")}</div>
    <div class="cap-grp"><span class="cap-lbl">진행중 (${running.length})</span><span class="cap-chips">${runChips}</span></div>
    <div class="cap-grp"><span class="cap-lbl">대기 (${queued.length})</span><span class="cap-chips">${queChips}</span></div>
  </div>`;
  els("#cap-bar .capchip[data-runid]").forEach(b => b.onclick = () => loadRunIntoReport(b.dataset.runid));
}

// load a run (by id) into the master→detail report — shared by the cap-bar chips
// and the run-records list. Fetches the run's events, resets scope, and draws.
function loadRunIntoReport(id) {
  runId = id; runEvents = []; runStatus = "running";
  detailScope = "*"; scopeAuto = true; expandedApi = null;
  fetch("/api/runs/" + id + "/events").then(r => r.json()).then(j => {
    runEvents = j.events || []; runStatus = j.status || "done";
    if (runStatus === "running" || runStatus === "queued") pollEvents();
    drawReport();
  }).catch(() => drawReport());
}

// 남은 자원(잔존) — pre-flight panel: list owned (leftover) resources + force cleanup.
// "🔍 남은 자원 확인" → POST /api/owned, renders the returned list (service · path ·
// count) with a 없음 ✅ / N건 ⚠️ headline; 🧹 강제 클린업 → POST /api/cleanup; re-check.
function drawLeftover() {
  const host = $("leftover-panel");
  if (!host) return;
  const s = ownedScan;
  let head, list = "";
  if (!s) {
    head = '<span class="muted small">아직 확인하지 않음 — 실행 전 남은 자원을 점검하세요.</span>';
  } else if (s.status === "running") {
    head = '<span class="muted small">⏳ 스캔 중… (read-only LIST)</span>';
  } else if (s.status === "error") {
    head = `<span class="lo-warn">스캔 실패: ${esc(s.error || "")}</span>`;
  } else {
    const n = s.owned_total != null ? s.owned_total : (s.owned || []).length;
    head = n === 0
      ? '<span class="lo-ok">없음 ✅ — 남은 자원 0건</span>'
      : `<span class="lo-warn">⚠️ ${n}건 — 실행 전 정리 권장</span>`;
    if (n > 0) {
      // group by service for a service · path · count rollup
      const bySvc = {};
      (s.owned || []).forEach(o => {
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
  }
  // 강제 클린업 is account-wide (reaps by owner-tag) so the server BLOCKS it (409)
  // while any run is running/queued — grey it out with a tooltip while busy, and
  // surface any non-OK {error} inline (no alert/crash).
  const busy = !!(lastCapacity && ((lastCapacity.running || []).length || (lastCapacity.queued || []).length));
  host.innerHTML = `<div class="panel lo-panel">
    <h2>남은 자원(잔존) <span class="muted small">· 실행 전 점검 (read-only) + 강제 클린업</span></h2>
    <div class="lo-head">${head}</div>
    ${list}
    <div class="run-ctl">
      <button class="btn ghost" id="lo-scan">🔍 남은 자원 확인</button>
      <button class="minibtn red" id="lo-cleanup" ${busy ? "disabled" : ""}
        title="${busy ? "진행 중 실행이 있어 비활성화" : "owner=apitest 자원을 TTL 무시하고 삭제"}">🧹 강제 클린업</button>
      ${s && s.owned_total != null ? '<button class="minibtn" id="lo-recheck">↻ 다시 확인</button>' : ""}
    </div>
    <div class="lo-err" id="lo-err" style="display:none"></div>
  </div>`;
  $("lo-scan").onclick = scanOwned;
  if ($("lo-recheck")) $("lo-recheck").onclick = scanOwned;
  $("lo-cleanup").onclick = () => {
    if (busy) return;
    const errEl = $("lo-err"); if (errEl) errEl.style.display = "none";
    if (!confirm("강제 클린업: owner=apitest 가 만든 모든 자원을 TTL 무시하고 삭제합니다.\n(우리 소유가 아닌 자원은 절대 건드리지 않습니다.)\n진행할까요?")) return;
    fetch("/api/cleanup", { method: "POST" }).then(r => r.json().then(j => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok || j.error) {                 // 409 (busy) or any error → show inline, no crash
        const el = $("lo-err");
        if (el) { el.textContent = j.error || "강제 클린업 실패"; el.style.display = ""; }
        return;
      }
      runId = j.id; runEvents = []; runStatus = "running"; detailTab = "log"; scopeAuto = true;
      drawReport(); startR4Poll();
      // after a force cleanup, auto re-scan so the panel reflects the new state
      setTimeout(scanOwned, 1200);
    }).catch(() => { const el = $("lo-err"); if (el) { el.textContent = "서버 연결 실패"; el.style.display = ""; } });
  };
}

// trigger the owned-resource scan (POST /api/owned) and poll its record for the list
function scanOwned() {
  ownedScan = { status: "running" };
  drawLeftover();
  fetch("/api/owned", { method: "POST" }).then(r => r.json()).then(j => {
    if (j.error) { ownedScan = { status: "error", error: j.error }; drawLeftover(); return; }
    pollOwned(j.id);
  }).catch(e => { ownedScan = { status: "error", error: e.message }; drawLeftover(); });
}
function pollOwned(id) {
  fetch("/api/runs/" + id).then(r => r.json()).then(j => {
    ownedScan = { status: j.status, owned: j.owned || [], owned_total: j.owned_total, error: j.error };
    if (screen === "run") drawLeftover();
    if (j.status === "running") setTimeout(() => pollOwned(id), 800);
  }).catch(() => { ownedScan = { status: "error", error: "연결 실패" }; if (screen === "run") drawLeftover(); });
}

function drawRunSettings() {
  const svcs = new Set([...targets].map(id => N[id].service));
  const heavy = lastGraph ? lastGraph.nodes.some(n => n.heavy) : [...targets].some(id => N[id].heavy);
  // Gates are DERIVED from the selection now (no axis/mode UI): a LIVE run always
  // sends mutations+destructive; heavy auto iff the selection pulls a heavy
  // lifecycle. The panel just SHOWS what will be applied + the LIVE run button.
  $("run-settings").innerHTML = `<div class="panel" style="margin-top:14px"><h2>실행 설정 <span class="muted small">· 항상 LIVE — 게이트는 선택에서 파생</span></h2>
    <p class="muted small">실제 클라우드 자원을 만들고 삭제합니다. 게이트는 선택(폐포)에서 자동으로 결정됩니다 — 별도 토글 없음. VPC 동시 실행 한도(cap) 아래에서 ADMIT 되거나 대기 큐에 들어갑니다.</p>
    <h3>적용 게이트 <span class="muted small">(선택에서 파생)</span></h3>
    <div class="chiprow">
      <span class="chip" style="border-color:var(--red)">✔ mutations</span>
      <span class="chip" style="border-color:var(--red)">✔ destructive</span>
      <span class="chip" style="border-color:${heavy ? "var(--red)" : "var(--line)"}">${heavy ? "✔" : "✕"} heavy</span>
    </div>
    <div class="kv"><span>선택</span><b>${svcs.size} svc / ${targets.size} 리소스</b></div>
    <div class="run-ctl">
      <button class="btn warn" id="run-go" ${targets.size ? "" : "disabled"}>⚠ LIVE 실행 ▶</button>
      <button class="btn ghost" id="run-toconf" title="① 구성으로 돌아가 선택 변경">← 구성</button>
    </div>
    ${targets.size ? "" : '<p class="muted small">선택이 없습니다 — ① 구성에서 서비스를 고르세요.</p>'}</div>`;
  $("run-go").onclick = startRun;
  $("run-toconf").onclick = () => go("build");
}

// Runs are always LIVE. Before posting, fetch the plan + capacity (parallel) and
// show a pre-flight confirm spelling out lifecycles, heavy count, VPC peak vs the
// current headroom, and whether it will QUEUE. On confirm, POST /api/run (mode
// live; the server derives the gates) and drive the existing report flow.
function startRun() {
  if (!targets.size) return;
  const sel = selectionPayload();
  Promise.all([
    fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sel) }).then(r => r.json()),
    fetch("/api/capacity").then(r => r.json()),
  ]).then(([plan, capacity]) => {
    plan = plan || {}; capacity = capacity || {};
    const N_lc = plan.runnable ? plan.runnable.length : (plan.lifecycle_ids ? plan.lifecycle_ids.length : 0);
    const peak = plan.peak_vpcs || 0;
    const headroom = capacity.headroom != null ? capacity.headroom : 0;
    const heavyM = Object.values(plan.preview || {}).filter(p => p && p.heavy).length;
    const heavy = heavyM > 0;
    const lines = [
      "실행 — 실제 클라우드 자원을 만들고 삭제합니다.",
      "",
      `라이프사이클: ${N_lc}개`,
      heavy ? `⚠️ heavy(billable): ${heavyM}개` : "heavy: 없음",
      `VPC 소모(peak): ${peak} · 현재 여유: ${headroom}`,
    ];
    if (peak > headroom) lines.push("→ 여유보다 커서 대기 큐에 들어갑니다.");
    lines.push("진행할까요?");
    if (confirm(lines.join("\n"))) postRun(sel);
  }).catch(e => {
    // plan/capacity pre-flight failed → still allow the run, but tell the user.
    if (confirm("사전 점검(plan/capacity) 실패: " + e.message + "\n그래도 LIVE 실행할까요?")) postRun(sel);
  });
}

// POST /api/run (always mode live) and drive the existing report flow. Tolerates a
// "queued" status (pollEvents shows the wait banner until it flips to running).
function postRun(sel) {
  const body = Object.assign({ mode: "live" }, sel);
  if (screen !== "run") go("run");
  $("report-main").innerHTML = '<p class="muted small">실행 요청 중…</p>';
  fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json()).then(j => {
      if (j.error) { $("report-main").innerHTML = '<p class="empty">실행 실패: ' + esc(j.error) + "</p>"; return; }
      runId = j.id; runEvents = []; runStatus = j.status || "running";
      detailScope = "*"; detailTab = "res"; scopeAuto = true; expandedApi = null;   // fresh run → reconcile auto-selects
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
    }).catch(e => { $("report-main").innerHTML = '<p class="empty">실행 연결 실패: ' + esc(e.message) + "</p>"; });
}

// ---- poll the live event stream until run-end / status done ----
// While running we poll FAST (~0.7s) so the user can SEE the order happen — the
// 흐름 DAG highlights the active node advancing 생성→테스트→삭제 and 자원 steps each
// resource through create→test→delete, rather than jumping to a final state.
function pollEvents() {
  if (!runId) return;
  if (pollTimer) clearTimeout(pollTimer);
  fetch("/api/runs/" + runId + "/events").then(r => r.json()).then(j => {
    runEvents = j.events || [];
    runStatus = j.status || runStatus;
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
      pollTimer = setTimeout(pollEvents, 1500);
      return;
    }
    const ended = runEvents.some(e => e.kind === "run-end")
      || (runStatus !== "running" && runStatus !== "queued");
    if (screen === "run") drawReport();
    if (!ended) pollTimer = setTimeout(pollEvents, 700);
    else { runStatus = runStatus === "running" ? "done" : runStatus; if (screen === "run") drawReport(); }
  }).catch(() => { pollTimer = setTimeout(pollEvents, 1000); });
}

// ================= 리포트 — master(흐름) → detail(자원·API·로그) + 전체 ===========
// The report is a master→detail drill-down: 흐름 is the PERSISTENT master (the B2
// live scene + a compact lifecycle list); the DETAIL pane (자원·API·로그) is scoped
// to the currently-selected lifecycle, or to the cross-run aggregate (전체). Both
// the master and the open detail refresh in place on every poll — no flicker, and
// the user's selected lifecycle / sub-tab / open API row survive.
function drawReport() {
  if (!runId) {
    if (r1Scene) { r1Scene.destroy(); r1Scene = null; }
    $("lc-picker").innerHTML = "";
    $("md-report") && $("md-report").classList.remove("has-detail");
    $("scopebar").innerHTML = "";
    $("detail-body").innerHTML = '<p class="empty">실행이 시작되면 라이프사이클을 선택해 상세를 봅니다.</p>';
    stopR4Poll();
    renderStagedPreview();   // 흐름 area shows the OPEN 대기열 item's DAG (else placeholder)
    loadRunRecords();
    return;
  }
  if (stagedScene) { stagedScene.destroy(); stagedScene = null; }   // a run owns the 흐름 area now
  reconcileScope();        // auto-select for a single-lifecycle run; validate scope
  reportR1();              // MASTER: the 흐름 scene (B2) — persistent, refresh in place
  renderLcPicker();        // MASTER: compact lifecycle list (collapsed-group / dense escape)
  renderDetail();          // DETAIL: scope bar + 자원/API/로그 for the current scope
  loadRunRecords();
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
    if (e.kind === "run-meta") (e.runnable || []).forEach(ensure);
    else if (e.kind === "wave-start") (e.lifecycles || []).forEach(ensure);
    else if (e.kind === "lifecycle-start") { const b = ensure(id); b.status = "running";
      if (e.service) b.service = e.service; if (e.heavy) b.heavy = true; }
    else if (e.kind === "lifecycle-end") { const b = ensure(id);
      b.status = e.status === "passed" ? "done" : e.status === "skipped" ? "skip" : "fail"; }
    else if (e.kind === "step-start") { const b = ensure(id); const k = e.step;
      const c = { key: k, lifecycle: id, step: k, method: e.method, path: e.path,
        status: null, category: "run", ms: null, params: null, req_body: null, resp_snippet: null };
      b._apiByKey[k] = c; b.api.push(c); }
    else if (e.kind === "step-end") { const b = ensure(id); const k = e.step;
      let c = b._apiByKey[k];
      if (!c) { c = { key: k, lifecycle: id, step: k, method: e.method, path: e.path }; b._apiByKey[k] = c; b.api.push(c); }
      c.status = e.status; c.category = e.category;
      c.ms = e.elapsed_ms != null ? Math.round(e.elapsed_ms) : null;   // integer ms (drop the long float)
      if (e.params != null) c.params = e.params;
      if (e.req_body != null) c.req_body = e.req_body;
      if (e.resp_snippet != null) c.resp_snippet = e.resp_snippet;
      if (e.category === "soft") b.softN++; else if (e.category === "fail") b.failN++;
    }
    else if (e.kind === "resource-tracked") { const b = ensure(id);
      const r = { id: e.resource_id, type: e.resource_type, lifecycle: id, path: e.path,
        created: true, deleted: false }; b.resources.push(r); b.createN++; }
    else if (e.kind === "resource-deleted") { const b = ensure(id);
      const cand = b.resources.filter(r => r.type === e.resource_type && !r.deleted);
      if (cand.length) cand[cand.length - 1].deleted = true; }
  });
  return { lcs, order };
}

// the current scope's grouped buckets (recomputed each call from the live stream).
function groupedRun() { return groupEventsByLifecycle(runEvents); }
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

// ---- MASTER: compact lifecycle list + 전체(aggregate) toggle ----------------
// The escape hatch for a dense / collapsed graph: a one-row-per-lifecycle list
// (status · service · API/자원 counts) where a click drills into that lifecycle's
// detail, plus a clearly-labeled 전체 pill that switches the detail to the cross-run
// aggregate (the CURRENT flat behavior). Highlights the current scope.
function renderLcPicker() {
  const host = $("lc-picker"); if (!host) return;
  const { lcs, order } = groupedRun();
  const agg = aggregateBucket(lcs, order);
  const rows = order.map(id => {
    const b = lcs[id];
    const cls = lcStatusClass(b.status);
    return `<button class="lcitem ${detailScope === id ? "sel" : ""}" data-lc="${esc(id)}" title="${esc(id)} — 상세 열기">
      <span class="lctop"><span class="st ${cls}">${lcStatusGlyph(b.status)}</span>
        <span class="lcname">${b.heavy ? "🜂 " : ""}${esc(id)}</span></span>
      <span class="lcmeta">${b.service ? `<span class="lcsvc">${esc(b.service)}</span>` : ""}
        <span class="pill l">${b.api.length} API</span>
        <span class="pill">${b.resources.length} 자원</span>
        ${b.softN ? `<span class="pill soft">${b.softN} soft</span>` : ""}
        ${b.failN ? `<span class="pill fail">${b.failN} fail</span>` : ""}</span>
    </button>`;
  }).join("");
  host.innerHTML =
    `<div class="lcp-h">라이프사이클 <span class="muted small">· 클릭 = 상세 (밀집/접힌 그래프용)</span></div>
     <div class="lclist">${rows || '<p class="muted small">라이프사이클 대기 중…</p>'}</div>
     <button class="aggitem ${isAggScope() ? "sel" : ""}" id="agg-toggle" title="크로스-런 집계 — 평면 탭의 기존 동작">
       <span class="ico">🗂️</span><span class="aggtxt"><b>전체 (집계)</b>
         <span class="muted small">런 전체 자원/API/로그 합산</span></span>
       <span class="sub">${agg.resources.length} 자원 · ${agg.api.length} API</span>
     </button>`;
  els("#lc-picker .lcitem[data-lc]").forEach(b => b.onclick = () => selectScope(b.dataset.lc));
  $("agg-toggle").onclick = () => selectScope("*");
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
    bar.innerHTML = `<span class="lbl">스코프</span>
      <span class="cur agg">🗂️ 전체 (집계)</span>
      <span class="crumb">— ${d.lcCount} lifecycle 합산 · ${d.resources.length} 자원 · ${d.api.length} API</span>`;
    return;
  }
  bar.innerHTML = `<span class="lbl">스코프</span>
    <span class="cur"><span class="st ${lcStatusClass(d.status)}">${lcStatusGlyph(d.status)}</span> ${d.heavy ? "🜂 " : ""}${esc(d.id)}</span>
    <span class="crumb">— ${d.service ? esc(d.service) + " · " : ""}${lcStatusLabel(d.status)} · ${d.api.length} API · ${d.resources.length} 자원</span>
    <button class="clear" id="scope-clear" title="전체 집계로">전체 집계로 ↺</button>`;
  $("scope-clear") && ($("scope-clear").onclick = () => selectScope("*"));
}

function renderDetailCounts() {
  const d = scopeData();
  $("d-nres").textContent = d.resources.length;
  $("d-napi").textContent = d.api.length;
}

// route the detail body to the scoped 자원 / API / 로그 view.
function renderDetailBody() {
  if (!runId) return;
  if (detailTab === "res") reportR2();
  else if (detailTab === "api") reportR3();
  else reportR4();
}

// derive live lifecycle state from events: queued/running/done/fail/skip
function lifecycleStates() {
  const st = {};
  runEvents.forEach(e => {
    if (e.kind === "run-meta") (e.runnable || []).forEach(l => { if (!st[l]) st[l] = "queued"; });
    if (e.kind === "wave-start") (e.lifecycles || []).forEach(l => { if (!st[l]) st[l] = "queued"; });
    if (e.kind === "lifecycle-start") st[e.lifecycle] = "running";
    if (e.kind === "lifecycle-end") st[e.lifecycle] =
      e.status === "passed" ? "done" : e.status === "skipped" ? "skip" : "fail";
  });
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
  runEvents.forEach(e => {
    if (e.kind === "step-start") { openSteps[e.lifecycle + "|" + e.step] = e; lastStart = e; }
    if (e.kind === "step-end") delete openSteps[e.lifecycle + "|" + e.step];
    if (e.kind === "resource-tracked") lastTrack = e;
    if (e.kind === "resource-deleted") lastDelete = e;
  });
  // the active step = the most recent still-open step-start (fallback: lastStart)
  const openList = Object.values(openSteps);
  const active = running ? (openList[openList.length - 1] || lastStart) : null;
  let phase = null, phaseLabel = "";
  if (active) {
    const m = (active.method || "").toUpperCase();
    if (m === "POST") { phase = "create"; phaseLabel = "생성 중"; }
    else if (m === "DELETE") { phase = "delete"; phaseLabel = "삭제 중"; }
    else if (m === "PUT" || m === "PATCH") { phase = "update"; phaseLabel = "설정 중"; }
    else { phase = "test"; phaseLabel = "테스트 중"; }
  } else if (!running) {
    phaseLabel = "완료";
  }
  return { running, active, phase, phaseLabel,
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
function reportR1() {
  const prog = liveProgress();
  const activeLc = prog.activeLifecycle;
  const g = lastGraph && lastGraph.nodes.length ? lastGraph : null;
  const banner = prog.running
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <span class="mono">${esc(activeLc || "")}</span>
        ${prog.active ? `<span class="muted small">${esc((prog.active.method || "") + " " + (prog.active.path || ""))}</span>` : ""}</div>`
    : `<div class="nowbar done"><span class="dot"></span><b>완료</b> · 상태 ${esc(runStatus)}</div>`;
  // (re)build the shell only when missing or the run changed (keeps the scene alive).
  const shell = $("r1-stage-wrap");
  const fresh = !shell || shell.dataset.run !== String(runId);
  if (fresh) {
    if (r1Scene) { r1Scene.destroy(); r1Scene = null; }
    $("report-main").innerHTML = `<div id="r1-banner">${banner}</div>
      <div class="legend">${legend([["#ffffff", "대기"], ["#e8f0fd", "진행 중"], ["#eaf7ee", "완료"], ["#fdeaea", "실패"]])}
        <span>접힌 그룹 = done/total · 그룹 클릭=펼치기 · <b>노드 클릭 = focus + 그 라이프사이클 상세 열기</b></span></div>
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
      <div id="r1-prog" style="margin-top:8px"></div>`;
    $("r1-stage-wrap").dataset.run = String(runId);
    if (g) {
      r1Scene = window.ResourceGraph.scene($("r1-svg"), $("r1-stage"), g, {
        hint: $("r1-hint"), stat: $("r1-stat"),
        overlay: r1Overlay, groupOverlay: r1GroupOverlay,
        // node focus = DRILL into that lifecycle's detail (master→detail). The focus
        // gesture IS the drill, reconciling cleanly with B2: focusing a node both
        // highlights its dependency path AND opens its detail. We only DRILL IN here
        // (focus set); clearing focus does NOT reset the scope (the user changes scope
        // via the 전체 pill or another lifecycle) — so a granularity/collapse change,
        // which fires onFocus(null), never wipes the user's selected detail.
        onFocus: info => {
          if (!info) return;
          const lc = N[info.label] && N[info.label].lifecycle;
          if (lc) selectScope(lc, { fromScene: true });
        },
        // the report is READ-ONLY: no target selection here, so we DON'T wire
        // onToggleTarget/isSelectable — the scene then renders a static provenance dot
        // in the node corner instead of the ＋/✓ selection box. Node-click = focus+drill.
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
      $("r1-svg").innerHTML = '<text x="12" y="22" fill="#656d76">합성 그래프 없음</text>';
    }
  } else {
    // same run, subsequent poll: refresh the banner + overlay in place (no rebuild)
    $("r1-banner").innerHTML = banner;
    if (r1Scene) r1Scene.refresh();
  }
  const st = lifecycleStates();
  // wave progress under the canvas. SIMULATE emits explicit wave-start events; a
  // LIVE run does NOT (it emits lifecycle-start/-end only) — so when there are no
  // wave-start events we DERIVE the structure from the lifecycles that actually
  // started, one row per lifecycle, in start order. Either way the section reflects
  // live progress and never sits on "대기 중…" once the run has events.
  const waves = {};
  let derived = false;
  runEvents.forEach(e => { if (e.kind === "wave-start") waves[e.wave] = { kind: e.wave_kind, lcs: e.lifecycles || [] }; });
  if (!Object.keys(waves).length) {
    // derive from lifecycle-start (live). Group each started lifecycle as its own
    // row so a single-lifecycle run shows that one lifecycle with its live state.
    derived = true;
    const seen = [];
    runEvents.forEach(e => { if (e.kind === "lifecycle-start" && !seen.includes(e.lifecycle)) seen.push(e.lifecycle); });
    // fall back to the known lifecycle set if a wave shows before any start arrives
    const lcs = seen.length ? seen : Object.keys(st);
    lcs.forEach((lc, i) => { waves[i] = { kind: (N[lc] && N[lc].lifecycle) || "lifecycle", lcs: [lc], single: true }; });
  }
  const counts = k => Object.values(st).filter(v => v === k).length;
  const total = Object.keys(st).length || (g ? g.nodes.filter(n => n.is_target).length : 0);
  const waveLines = Object.keys(waves).sort((a, b) => a - b).map(i => {
    const w = waves[i];
    const done = w.lcs.filter(l => st[l] === "done").length;
    const running = w.lcs.some(l => st[l] === "running");
    const failed = w.lcs.some(l => st[l] === "fail");
    const pct = w.lcs.length ? Math.round(100 * done / w.lcs.length) : 0;
    // for a derived single-lifecycle row, label it with the lifecycle id itself.
    const label = w.single ? esc(w.lcs[0]) : `웨이브 ${i} <span class="muted small">${esc(w.kind || "")} · ${w.lcs.length}개</span>`;
    const mark = running ? "⏳" : failed ? "✕" : (done === w.lcs.length && w.lcs.length) ? "✓" : "·";
    return `<div class="kv"><span>${mark} ${label}</span><b>${done}/${w.lcs.length}</b></div>
      <div class="pbar ${done === w.lcs.length && w.lcs.length ? "done" : ""}"><i style="width:${pct}%"></i></div>`;
  }).join("");
  const waveHdr = derived ? "라이프사이클 진행 (실행 순서)" : "웨이브 진행 (실행 순서)";
  $("r1-prog").innerHTML = `<div class="kpi">
      <div class="s"><b>${counts("done")}/${total}</b><span>완료</span></div>
      <div class="s"><b style="color:var(--run)">${counts("running")}</b><span>실행중</span></div>
      <div class="s"><b style="color:var(--fail)">${counts("fail")}</b><span>fail</span></div>
      <div class="s"><b>${esc(runStatus)}</b><span>상태</span></div>
    </div>
    <h3>${waveHdr}</h3>${waveLines || (runEvents.length
      ? '<p class="muted small">진행 정보 집계 중…</p>'
      : '<p class="muted small">실행 시작을 기다리는 중…</p>')}`;
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
  const body = list.length ? list.map(r => `<tr class="${r.id === cursorId ? "rowact" : ""}">
      <td>${esc(rowKind(r))}</td>
      <td><code>${esc(r.id)}</code></td>
      ${d.agg ? `<td>${esc(r._lc || r.lifecycle || "")}</td>` : ""}
      <td class="${r.created ? "tick" : "tickno"}">${r.created ? "✓" : "—"}</td>
      <td class="${r.tested ? "tick" : "tickno"}">${r.tested ? "✓" : "—"}</td>
      <td class="${r.deleted ? "tick" : "tickno"}">${r.deleted ? "✓" : "—"}</td>
      <td>${phaseChip(r)}</td>
    </tr>`).join("")
    : `<tr><td colspan="${ncol}" class="empty">${d.agg ? "추적된 자원 없음"
        : (d.status === "running" ? "이 라이프사이클은 아직 자원을 만들지 않았습니다 (진행 중)…"
           : "이 라이프사이클에는 추적된 자원이 없습니다.")}</td></tr>`;
  const nowLine = prog.running && cursorId
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <code>${esc(cursorId)}</code></div>` : "";
  $("detail-body").innerHTML = `<h3 class="detail-h">자원 <span class="muted small">· ${d.agg ? "런 전체" : "이 라이프사이클"} — 생성 · 테스트 · 삭제 + id</span></h3>
    ${nowLine}
    <table class="tbl">
      <thead><tr><th>type</th><th>resource_id</th>${lcCol}<th>생성</th><th>테스트</th><th>삭제</th><th>단계</th></tr></thead>
      <tbody>${body}</tbody></table>`;
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
  const apiRow = c => {
    const k = rowKey(c);
    const isOpen = expandedApi === k;
    const row = `<tr class="apirow ${isOpen ? "open" : ""}" data-apik="${esc(k)}">
      <td><span class="caret">${isOpen ? "▾" : "▸"}</span> <span class="mtag ${esc(c.method || "")}">${esc(c.method || "")}</span> <code>${esc(c.path || "")}</code></td>
      <td>${badge(c.category)}</td>
      <td class="muted">${c.status != null ? esc(c.status) : "—"}</td>
      <td class="muted">${c.ms != null ? c.ms + " ms" : (c.category === "run" ? "⏳" : "—")}</td>
    </tr>`;
    const detail = isOpen ? `<tr class="apidetail"><td colspan="4">${apiDetailHtml(c)}</td></tr>` : "";
    return row + detail;
  };
  let body;
  if (d.agg) {
    const byLc = {};
    calls.forEach(c => (byLc[c._lc || c.lifecycle] = byLc[c._lc || c.lifecycle] || []).push(c));
    body = Object.keys(byLc).sort().map(lc =>
      `<tr class="lc-head"><td colspan="4">${esc(lc)} <span class="muted small">${byLc[lc].length} api</span></td></tr>` +
      byLc[lc].map(apiRow).join("")).join("");
  } else {
    body = calls.map(apiRow).join("");
  }
  $("detail-body").innerHTML = `<h3 class="detail-h">API <span class="muted small">· ${d.agg ? "런 전체" : "이 라이프사이클"} — 행 클릭 → 요청·응답·파라미터 스키마</span></h3>
    <div class="kpi">
      <div class="s"><b>${calls.length}</b><span>api 호출</span></div>
      <div class="s"><b style="color:var(--ok)">${okN}</b><span>ok</span></div>
      <div class="s"><b style="color:var(--soft)">${softN}</b><span>soft</span></div>
      <div class="s"><b style="color:var(--fail)">${failN}</b><span>fail</span></div>
    </div>
    <div class="scroll" style="max-height:560px;margin-top:8px"><table class="tbl apitbl">
      <thead><tr><th>method · path (대상)</th><th>결과</th><th>status</th><th>응답시간</th></tr></thead>
      <tbody>${body || `<tr><td colspan="4" class="empty">${d.status === "running" ? "API 호출 대기 중 (진행 중)…" : "이 스코프에 API 호출이 없습니다."}</td></tr>`}</tbody></table></div>`;
  // row click → toggle the inline detail (collapse if it was already open)
  els("#detail-body .apirow[data-apik]").forEach(row => row.onclick = () => {
    const k = row.dataset.apik;
    expandedApi = expandedApi === k ? null : k;
    reportR3();
  });
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
    $("btn-cleanup").onclick = () => {
      if (!confirm("강제 클린업: owner=apitest 가 만든 모든 자원을 TTL 무시하고 삭제합니다.\n(우리 소유가 아닌 자원은 절대 건드리지 않습니다.)\n진행할까요?")) return;
      fetch("/api/cleanup", { method: "POST" }).then(r => r.json()).then(j => {
        if (j.error) { alert(j.error); return; }
        runId = j.id; runEvents = []; runStatus = "running"; detailTab = "log"; scopeAuto = true;
        drawReport(); startR4Poll();
      }).catch(() => alert("서버 연결 실패"));
    };
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
    const tag = c.category === "fail" ? "FAIL" : c.category === "soft" ? "SOFT" : c.category === "run" ? "…" : "ok";
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
  const tick = () => {
    r4LogTimer = null;
    // only while the aggregate (전체) raw-log view is actually visible
    if (screen !== "run" || detailTab !== "log" || !isAggScope() || !$("r4-log")) return;
    fetch("/api/runs/" + runId).then(r => r.json()).then(j => {
      runStatus = j.status || runStatus;
      loadLog();
      loadRunRecords();
      if (runStatus === "running") r4LogTimer = setTimeout(tick, 2000);
    }).catch(() => { r4LogTimer = setTimeout(tick, 2500); });
  };
  r4LogTimer = setTimeout(tick, 2000);
}
function stopR4Poll() {
  if (r4LogTimer) { clearTimeout(r4LogTimer); r4LogTimer = null; }
}

// ---- run records list ----
function loadRunRecords() {
  fetch("/api/runs").then(r => r.json()).then(j => {
    const runs = j.runs || [];
    const host = $("report-side"); if (!host) return;
    if (!runs.length) { host.innerHTML = '<p class="muted small">아직 실행 기록이 없습니다.</p>'; return; }
    const KIND = { simulate: "▶sim", lifecycle: "▶live", cleanup: "🧹", verify: "🔍" };
    host.innerHTML = runs.map(r => {
      const icon = r.status === "queued" ? "⌛" : r.status === "running" ? "⏳"
        : r.status === "done" ? (r.rc === 0 ? "✅" : "⚠️") : "❌";
      const dur = (r.ended && r.started) ? Math.round(r.ended - r.started) + "s"
        : (r.status === "running" ? "실행중…" : r.status === "queued" ? "대기 중…" : "");
      const on = runId === r.id;
      const tag = KIND[r.kind] || esc(r.kind || "");
      return `<div class="runrow ${on ? "on" : ""}" data-id="${esc(r.id)}">
        <span><span class="kindtag">${tag}</span> <b class="small">${icon} ${esc(r.id)}</b>
          <span class="muted small">${esc((r.lifecycle_ids || []).slice(0, 2).join(", "))}${(r.lifecycle_ids || []).length > 2 ? " …" : ""}</span></span>
        <span class="muted small">${esc(r.summary || r.status)} · ${dur}</span></div>`;
    }).join("");
    els("#report-side .runrow").forEach(row => row.onclick = () => loadRunIntoReport(row.dataset.id));
  }).catch(() => { const host = $("report-side"); if (host) host.innerHTML = '<p class="muted small">서버 연결 실패</p>'; });
}

// ================= shared helpers =================
// the server resolves node_ids → graph_view targets (and → source lifecycles for a
// run). Sending node_ids keeps the "selection = resources" contract.
function selectionPayload() { return { node_ids: [...targets] }; }

function legend(items) {
  return items.map(i => `<span><i style="background:${i[0]}"></i>${esc(i[1])}</span>`).join("");
}
function badge(cat) {
  const m = { ok: "ok", soft: "soft", fail: "fail", run: "run" };
  const c = m[cat] || "queued";
  return `<span class="bdg ${c}">${esc(cat || "—")}</span>`;
}

})();
