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

// run/report state
let runId = null;
let runMode = "simulate";
let runAxis = "regression-light";
let runEvents = [];
let runStatus = "idle";
let pollTimer = null;
let reportSub = "r1";
let lastLogText = null;     // last log text written to the 로그 <pre> (in-place diff → no flicker)
let r4LogTimer = null;      // dedicated slow (2s) log poller while on the 로그 tab during a run
let expandedApi = null;     // key of the currently-expanded API row (API tab detail)

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
  wireNav();
  wireModal();
  buildAxisCtl();
  go("build");
}

// ---- a resource node is standalone-selectable iff it maps to a lifecycle.
// (lookup / pure-dep resources have lifecycle=null → never selectable; they
// still appear on the composition DAG when pulled in as a dependency.) ----
const hasLifecycle = id => !!(N[id] && N[id].lifecycle);
const svcNodes = svc => Object.keys(N).filter(id => N[id].service === svc);          // all nodes of a service
const svcSelectable = svc => svcNodes(svc).filter(hasLifecycle);                       // its lifecycle-bearing nodes
const shortName = svc => svc.split("/").pop();

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
  els("#report-subtabs button").forEach(b => b.onclick = () => { reportSub = b.dataset.r; drawReport(); });
}
function go(scr) {
  screen = scr;
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
  const axisLabel = AXES[runAxis] ? AXES[runAxis].label : runAxis;
  const isLive = runMode === "live";
  $("ctxbar").innerHTML =
    `<span class="seg">env <b>local</b></span>
     <span class="seg">· axis <b>${esc(axisLabel)}</b></span>
     <span class="seg">· mode <b>${esc(runMode)}</b></span>
     <span class="seg">· 선택 <b>${targets.size}</b> 리소스</span>
     <span class="seg">· 서비스 <b>${svcs.size}</b></span>
     <span class="seg">· 폐포 <b>${closureK}</b></span>
     <span class="seg">· heavy <b>${heavyClosure ? "🜂 포함" : "없음"}</b></span>
     <span class="seg">· 모델 <b>${MODEL.node_count}</b> 자원 / <b>${MODEL.lifecycle_count}</b> lifecycle</span>
     <span class="badge ${isLive ? "live" : "sim"}">${isLive ? "LIVE" : "SIMULATE"}</span>`;
}

// ================= ① 구성 =================
function drawBuild() {
  initCollapse();
  drawSvcTree();
  refreshGraph();           // fetch /api/graph for the current selection
  $("sel-search").oninput = drawSvcTree;
  $("sel-all").onclick = toggleAll;
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
        <span class="tname">${esc(cat)}</span>
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
    window.ResourceGraph.render(svg, { nodes: [], edges: [] }, {});
    svg.innerHTML = '<text x="12" y="24" fill="#656d76">서비스를 선택하면 합성 배포 DAG가 생성 순서대로 표시됩니다.</text>';
    svg.setAttribute("viewBox", "0 0 420 40"); svg.setAttribute("width", 420); svg.setAttribute("height", 40);
    $("dag-readout").innerHTML = "";
    $("order-tbl").innerHTML = "";
    $("dag-legend").innerHTML = "";
    return;
  }
  window.ResourceGraph.render(svg, g, {
    onClick: id => {                       // click a node on the DAG = toggle that target
      if (!hasLifecycle(id)) return;       // dep-only nodes are not toggleable
      targets.has(id) ? targets.delete(id) : targets.add(id);
      selectionChanged();
    }
  });
  $("dag-legend").innerHTML = legend([
    ["#e6effd", "★ 대상"], ["#fffaf0", "■ 공유(dedup)"], ["#f3eefc", "↓ 의존"]
  ]) + '<span><span style="color:var(--val)">●</span> VALIDATED · <span style="color:var(--docs)">●</span> docs · 🜂 heavy · ⛔ quota</span>';
  graphReadout(g);
  orderTable(g);
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

// 생성/검증/삭제 순서표: create order (graph.order) · verify count (model.verify_n) · delete order (graph.teardown)
function orderTable(g) {
  const createOrder = [], seen = new Set();
  (g.order || []).forEach(inst => { const b = baseId(inst); if (!seen.has(b)) { seen.add(b); createOrder.push(b); } });
  // teardown rank by base node (first occurrence)
  const delRank = {}; let r = 0;
  (g.teardown || []).forEach(inst => { const b = baseId(inst); if (!(b in delRank)) delRank[b] = ++r; });
  const nodeById = {}; (g.nodes || []).forEach(n => { nodeById[n.id] = n; });
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
const AXES = {
  "smoke":             { label: "smoke", desc: "읽기 전용 (다음 빌드)", enabled: false, gates: {} },
  "regression-light":  { label: "회귀-light", desc: "CRUD · mutations+destructive", enabled: true,
                         gates: { mutations: true, destructive: true, heavy: false } },
  "regression-heavy":  { label: "회귀-heavy", desc: "CRUD+billable · heavy 포함", enabled: true,
                         gates: { mutations: true, destructive: true, heavy: true } },
  "conformance":       { label: "conformance", desc: "설계 적합성 (다음 빌드)", enabled: false, gates: {} }
};
function buildAxisCtl() {
  $("axisCtl").innerHTML = Object.entries(AXES).map(([k, a]) =>
    `<button data-ax="${k}" class="${runAxis === k ? "on" : ""}" ${a.enabled ? "" : "disabled"} title="${esc(a.desc)}">${esc(a.label)}</button>`).join("");
  els("#axisCtl button").forEach(b => b.onclick = () => {
    if (!AXES[b.dataset.ax].enabled) return;
    runAxis = b.dataset.ax;
    els("#axisCtl button").forEach(x => x.classList.toggle("on", x.dataset.ax === runAxis));
    ctxBar(); launchSummary();
  });
  els("#modeCtl button").forEach(b => b.onclick = () => {
    runMode = b.dataset.md;
    els("#modeCtl button").forEach(x => x.classList.toggle("on", x.dataset.md === runMode));
    ctxBar(); launchSummary();
  });
  $("launch-go").onclick = startRun;
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
    go.className = "btn" + (runMode === "live" ? " warn" : "");
    go.textContent = runMode === "live" ? "⚠ LIVE 실행 ▶" : "▶ simulate 실행";
  }
}

// ================= ② 실행 & 리포트 =================
function drawRunScreen() {
  // pre-flight 남은 자원(잔존) panel at the TOP, then the run settings below it.
  $("run-left").innerHTML = '<div id="leftover-panel"></div><div id="run-settings"></div>';
  drawLeftover();
  drawRunSettings();
  drawReport();
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
  host.innerHTML = `<div class="panel lo-panel">
    <h2>남은 자원(잔존) <span class="muted small">· 실행 전 점검 (read-only) + 강제 클린업</span></h2>
    <div class="lo-head">${head}</div>
    ${list}
    <div class="run-ctl">
      <button class="btn ghost" id="lo-scan">🔍 남은 자원 확인</button>
      <button class="minibtn red" id="lo-cleanup" title="owner=apitest 자원을 TTL 무시하고 삭제">🧹 강제 클린업</button>
      ${s && s.owned_total != null ? '<button class="minibtn" id="lo-recheck">↻ 다시 확인</button>' : ""}
    </div>
  </div>`;
  $("lo-scan").onclick = scanOwned;
  if ($("lo-recheck")) $("lo-recheck").onclick = scanOwned;
  $("lo-cleanup").onclick = () => {
    if (!confirm("강제 클린업: owner=apitest 가 만든 모든 자원을 TTL 무시하고 삭제합니다.\n(우리 소유가 아닌 자원은 절대 건드리지 않습니다.)\n진행할까요?")) return;
    fetch("/api/cleanup", { method: "POST" }).then(r => r.json()).then(j => {
      if (j.error) { alert(j.error); return; }
      runId = j.id; runEvents = []; runStatus = "running"; reportSub = "r4";
      drawReport(); startR4Poll();
      // after a force cleanup, auto re-scan so the panel reflects the new state
      setTimeout(scanOwned, 1200);
    }).catch(() => alert("서버 연결 실패"));
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
  const ax = AXES[runAxis];
  const svcs = new Set([...targets].map(id => N[id].service));
  $("run-settings").innerHTML = `<div class="panel" style="margin-top:14px"><h2>실행 설정</h2>
    <h3>Axis <span class="muted small">(run 단위)</span></h3>
    <div class="axisgrid" id="axisgrid">${Object.entries(AXES).map(([k, a]) =>
      `<label class="axisopt ${runAxis === k ? "on" : ""} ${a.enabled ? "" : "disabled"}">
        <input type="radio" name="axis2" value="${k}" ${runAxis === k ? "checked" : ""} ${a.enabled ? "" : "disabled"}>
        <span><span class="t">${esc(a.label)}</span><br><span class="d">${esc(a.desc)}</span></span>
      </label>`).join("")}</div>
    <h3>mode</h3>
    <div class="pill-ctl mode" id="modeseg" style="width:fit-content">
      <button data-m="simulate" class="${runMode === "simulate" ? "on" : ""}">simulate</button>
      <button data-m="live" class="${runMode === "live" ? "on" : ""}">live</button>
    </div>
    <p class="muted small" style="margin-top:7px">simulate = 플랜을 결정론적으로 재생(클라우드 호출 없음, 합성 id). live = 실제 pytest + 안전 게이트.</p>
    <h3>적용 게이트</h3>
    <div class="chiprow" id="gatechips"></div>
    <div class="kv"><span>선택</span><b>${svcs.size} svc / ${targets.size} 리소스</b></div>
    <div class="run-ctl">
      <button class="btn ${runMode === "live" ? "warn" : ""}" id="run-go" ${targets.size ? "" : "disabled"}>
        ${runMode === "live" ? "⚠ LIVE 실행 ▶" : "▶ simulate 실행"}</button>
      <button class="btn ghost" id="run-toconf" title="① 구성으로 돌아가 선택 변경">← 구성</button>
    </div>
    ${targets.size ? "" : '<p class="muted small">선택이 없습니다 — ① 구성에서 서비스를 고르세요.</p>'}</div>`;
  gateChips();
  els("#axisgrid input").forEach(r => r.onchange = () => {
    if (!AXES[r.value].enabled) return;
    runAxis = r.value; ctxBar(); drawRunSettings();
  });
  els("#modeseg button").forEach(b => b.onclick = () => { runMode = b.dataset.m; ctxBar(); drawRunSettings(); });
  $("run-go").onclick = startRun;
  $("run-toconf").onclick = () => go("build");
}
function gateChips() {
  const g = AXES[runAxis].gates || {};
  const chips = [["mutations", g.mutations], ["destructive", g.destructive], ["heavy", g.heavy]]
    .map(([k, v]) => `<span class="chip" style="border-color:${v ? "var(--red)" : "var(--line)"}">${v ? "✔" : "✕"} ${k}</span>`).join("");
  if ($("gatechips")) $("gatechips").innerHTML = runMode === "live"
    ? chips : '<span class="muted small">simulate — 게이트 무관 (클라우드 호출 없음)</span>';
}

function startRun() {
  if (!targets.size) return;
  const ax = AXES[runAxis];
  const body = Object.assign({ mode: runMode }, selectionPayload());
  if (runMode === "live") {
    Object.assign(body, ax.gates || {});
    const g = ax.gates || {};
    const msg = `LIVE 실행 — 실제 pytest 가 클라우드 자원을 만들고 삭제합니다.\n\n` +
      `axis: ${ax.label}\n` +
      `mutations(POST/PUT/PATCH): ${g.mutations ? "ON" : "off"}\n` +
      `destructive(DELETE): ${g.destructive ? "ON" : "off"}\n` +
      `heavy(billable lifecycle): ${g.heavy ? "ON" : "off"}\n\n진행할까요?`;
    if (!confirm(msg)) return;
  }
  if (screen !== "run") go("run");
  $("report-main").innerHTML = '<p class="muted small">실행 요청 중…</p>';
  fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(r => r.json()).then(j => {
      if (j.error) { $("report-main").innerHTML = '<p class="empty">실행 실패: ' + esc(j.error) + "</p>"; return; }
      runId = j.id; runEvents = []; runStatus = "running"; reportSub = "r1";
      drawReport();
      pollEvents();
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
    const ended = runEvents.some(e => e.kind === "run-end") || (runStatus !== "running");
    if (screen === "run") drawReport();
    if (!ended) pollTimer = setTimeout(pollEvents, 700);
    else { runStatus = runStatus === "running" ? "done" : runStatus; if (screen === "run") drawReport(); }
  }).catch(() => { pollTimer = setTimeout(pollEvents, 1000); });
}

// ================= 리포트 (흐름 · 자원 · API · 로그) — light theme =================
function drawReport() {
  els("#report-subtabs button").forEach(b => b.classList.toggle("on", b.dataset.r === reportSub));
  // leaving the 로그 tab: stop its dedicated poller so it can't fight another tab
  if (reportSub !== "r4") stopR4Poll();
  if (!runId) {
    $("report-main").innerHTML = '<p class="empty">아직 실행이 없습니다 — 위에서 <b>실행 ▶</b>을 누르세요.</p>';
    loadRunRecords();
    return;
  }
  if (reportSub === "r1") reportR1();
  else if (reportSub === "r2") reportR2();
  else if (reportSub === "r3") reportR3();
  else reportR4();
  loadRunRecords();
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

// 흐름 — composition DAG colored by live lifecycle state + the ACTIVE node pulsed,
// so the user watches the order advance (생성→테스트→삭제). + wave progress below.
function reportR1() {
  const st = lifecycleStates();
  const prog = liveProgress();
  const FILL = { queued: "#ffffff", running: "#e8f0fd", done: "#eaf7ee", fail: "#fdeaea", skip: "#f6f8fa" };
  const STK = { queued: "#8a93a0", running: "#2563c9", done: "#2da44e", fail: "#cf222e", skip: "#8a93a0" };
  const BDG = { queued: "", running: "⏳", done: "✓", fail: "✕", skip: "–" };
  const nodeState = id => { const lc = N[id] && N[id].lifecycle; return lc && st[lc] ? st[lc] : null; };
  const activeLc = prog.activeLifecycle;
  // banner: 현재 무엇을 하고 있는지 (생성 중 / 테스트 중 / 삭제 중 / 완료)
  const banner = prog.running
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <span class="mono">${esc(activeLc || "")}</span>
        ${prog.active ? `<span class="muted small">${esc((prog.active.method || "") + " " + (prog.active.path || ""))}</span>` : ""}</div>`
    : `<div class="nowbar done"><span class="dot"></span><b>완료</b> · 상태 ${esc(runStatus)}</div>`;
  $("report-main").innerHTML = `<h2>흐름 <span class="muted small">· DAG 진행 순서 — 노드 = lifecycle 라이브 상태</span></h2>
    ${banner}
    <div class="legend">${legend([["#ffffff", "대기"], ["#e8f0fd", "진행 중"], ["#eaf7ee", "완료"], ["#fdeaea", "실패"]])}</div>
    <div class="svgbox big"><svg id="r1-svg"></svg></div>
    <div id="r1-prog" style="margin-top:8px"></div>`;
  const g = lastGraph && lastGraph.nodes.length ? lastGraph : null;
  if (g) {
    window.ResourceGraph.render($("r1-svg"), g, {
      overlay: id => {
        const lc = N[id] && N[id].lifecycle;
        const s = nodeState(id);
        // the ACTIVE lifecycle's nodes pulse blue + carry the phase glyph so the
        // eye tracks the advancing step even before the lifecycle flips to done.
        if (prog.running && lc && lc === activeLc) {
          const glyph = prog.phase === "create" ? "⊕" : prog.phase === "delete" ? "⊖" : "⏳";
          return { fill: "#dbe8fd", stroke: "#1a56c4", badge: glyph, pulse: true };
        }
        if (!s) return null;
        return { fill: FILL[s], stroke: STK[s], badge: BDG[s] };
      }
    });
  } else {
    $("r1-svg").innerHTML = '<text x="12" y="22" fill="#656d76">합성 그래프 없음</text>';
  }
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

// 자원 — per-resource rows (생성·테스트·삭제 + id) from resource-tracked/-deleted.
// While running, the most-recent resource shows a live phase (생성→테스트→삭제) so
// the user watches each resource step through its lifecycle, not just a final state.
function reportR2() {
  const rows = {};
  const lcVerifyOk = {};
  const order = [];
  runEvents.forEach(e => {
    if (e.kind === "resource-tracked") {
      rows[e.resource_id] = { id: e.resource_id, type: e.resource_type, lifecycle: e.lifecycle,
        path: e.path, created: true, deleted: false, tested: false };
      order.push(e.resource_id);
    }
    if (e.kind === "resource-deleted") {
      const cand = Object.values(rows).filter(r => r.lifecycle === e.lifecycle && r.type === e.resource_type && !r.deleted);
      if (cand.length) cand[cand.length - 1].deleted = true;
    }
    if (e.kind === "step-end" && (e.method || "").toUpperCase() === "GET" && e.category === "ok")
      lcVerifyOk[e.lifecycle] = true;
  });
  Object.values(rows).forEach(r => { r.tested = !!lcVerifyOk[r.lifecycle]; });
  const prog = liveProgress();
  // the live "cursor" resource = newest tracked-not-deleted in the active lifecycle
  let cursorId = null;
  if (prog.running) {
    const live = order.map(id => rows[id]).filter(r => r && !r.deleted
      && (!prog.activeLifecycle || r.lifecycle === prog.activeLifecycle));
    cursorId = live.length ? live[live.length - 1].id : null;
  }
  // per-row live phase chip
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
  const list = order.map(id => rows[id]).filter(Boolean);
  const simLabel = runMode === "simulate" ? ' <span class="muted small">(simulate: 합성 id)</span>' : "";
  // TYPE = the resource KIND derived from the create/delete PATH (vpc/subnet/port),
  // NOT the service name — the path is the source of truth for what was actually
  // created. Fall back to the service short-name only when the path can't be parsed.
  const rowKind = r => kindFromPath(r.path) || shortName(r.type || "") || "?";
  const body = list.length ? list.map(r => `<tr class="${r.id === cursorId ? "rowact" : ""}">
      <td>${esc(rowKind(r))}</td>
      <td><code>${esc(r.id)}</code></td>
      <td>${esc(r.lifecycle)}</td>
      <td class="${r.created ? "tick" : "tickno"}">${r.created ? "✓" : "—"}</td>
      <td class="${r.tested ? "tick" : "tickno"}">${r.tested ? "✓" : "—"}</td>
      <td class="${r.deleted ? "tick" : "tickno"}">${r.deleted ? "✓" : "—"}</td>
      <td>${phaseChip(r)}</td>
    </tr>`).join("") : '<tr><td colspan="7" class="empty">자원 이벤트 없음 (실행 중이거나 create 스텝 없음)</td></tr>';
  const nowLine = prog.running && cursorId
    ? `<div class="nowbar phase-${prog.phase || "test"}"><span class="dot"></span>
        <b>${esc(prog.phaseLabel)}</b> · <code>${esc(cursorId)}</code></div>` : "";
  $("report-main").innerHTML = `<h2>자원${simLabel} <span class="muted small">· 생성 · 테스트 · 삭제 + id</span></h2>
    <p class="muted small">create/delete 스텝마다 추적된 실자원 — type · resource_id · 생성/테스트/삭제(+ 현재 단계).</p>
    ${nowLine}
    <table class="tbl">
      <thead><tr><th>type</th><th>resource_id</th><th>lifecycle</th><th>생성</th><th>테스트</th><th>삭제</th><th>단계</th></tr></thead>
      <tbody>${body}</tbody></table>`;
}

// API — api-first table of step-start/step-end (method+path, 결과, 응답시간). Rows
// are CLICKABLE: an inline detail panel shows the actual 요청 params/body + 응답
// status/snippet (from the enriched step-end event) AND the endpoint's parameter
// SCHEMA (from /api/model endpoint_params) marking which params were actually sent
// — a coverage hint ("what COULD be tested" vs "what WAS").
function reportR3() {
  const calls = [];
  const open = {};
  runEvents.forEach(e => {
    if (e.kind === "step-start") {
      const k = e.lifecycle + "|" + e.step;
      open[k] = { key: k, lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path,
                  status: null, category: "run", ms: null, params: null, req_body: null, resp_snippet: null };
      calls.push(open[k]);
    }
    if (e.kind === "step-end") {
      const k = e.lifecycle + "|" + e.step;
      const c = open[k] || { key: k, lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path };
      c.status = e.status; c.category = e.category; c.ms = e.elapsed_ms;
      // enriched detail (additive; present only for live runs with the new engine)
      if (e.params != null) c.params = e.params;
      if (e.req_body != null) c.req_body = e.req_body;
      if (e.resp_snippet != null) c.resp_snippet = e.resp_snippet;
      if (!open[k]) calls.push(c);
    }
  });
  const byLc = {};
  calls.forEach(c => (byLc[c.lifecycle] = byLc[c.lifecycle] || []).push(c));
  const okN = calls.filter(c => c.category === "ok").length;
  const softN = calls.filter(c => c.category === "soft").length;
  const failN = calls.filter(c => c.category === "fail").length;
  const body = Object.keys(byLc).sort().map(lc =>
    `<tr class="lc-head"><td colspan="4">${esc(lc)} <span class="muted small">${byLc[lc].length} api</span></td></tr>` +
    byLc[lc].map(c => {
      const isOpen = expandedApi === c.key;
      const row = `<tr class="apirow ${isOpen ? "open" : ""}" data-apik="${esc(c.key)}">
        <td><span class="caret">${isOpen ? "▾" : "▸"}</span> <span class="mtag ${esc(c.method || "")}">${esc(c.method || "")}</span> <code>${esc(c.path || "")}</code></td>
        <td>${badge(c.category)}</td>
        <td class="muted">${c.status != null ? esc(c.status) : "—"}</td>
        <td class="muted">${c.ms != null ? c.ms + " ms" : (c.category === "run" ? "⏳" : "—")}</td>
      </tr>`;
      const detail = isOpen
        ? `<tr class="apidetail"><td colspan="4">${apiDetailHtml(c)}</td></tr>` : "";
      return row + detail;
    }).join("")).join("");
  $("report-main").innerHTML = `<h2>API <span class="muted small">· 호출 결과 (행 클릭 → 요청·응답·파라미터 스키마)</span></h2>
    <div class="kpi">
      <div class="s"><b>${calls.length}</b><span>api 호출</span></div>
      <div class="s"><b style="color:var(--ok)">${okN}</b><span>ok</span></div>
      <div class="s"><b style="color:var(--soft)">${softN}</b><span>soft</span></div>
      <div class="s"><b style="color:var(--fail)">${failN}</b><span>fail</span></div>
    </div>
    <div class="scroll" style="max-height:560px;margin-top:8px"><table class="tbl apitbl">
      <thead><tr><th>method · path (대상)</th><th>결과</th><th>status</th><th>응답시간</th></tr></thead>
      <tbody>${body || '<tr><td colspan="4" class="empty">api 이벤트 없음</td></tr>'}</tbody></table></div>`;
  // row click → toggle the inline detail (collapse if it was already open)
  els("#report-main .apirow[data-apik]").forEach(row => row.onclick = () => {
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
  const fresh = !$("r4-log");
  if (fresh) {                   // build the shell ONCE; repeated draws are no-ops
    lastLogText = null;          // force the first paint after a (re)build
    $("report-main").innerHTML = `<h2>로그 <span class="muted small">· 실행 로그</span></h2>
      <div class="run-ctl">
        <button class="minibtn red" id="btn-cleanup" title="우리(owner)가 만든 자원을 강제 삭제 (reconciler, TTL 무시).">🧹 강제 클린업</button>
        <button class="minibtn" id="btn-verify" title="삭제 없이 남은 우리 자원 수 확인 (read-only).">🔍 클린업 확인</button>
        <button class="minibtn" id="btn-reflog">↻ 로그 새로고침</button>
      </div>
      <pre class="runlog" id="r4-log">로그 로딩…</pre>`;
    $("btn-reflog").onclick = () => loadLog(true);   // manual refresh → snap to bottom
    $("btn-cleanup").onclick = () => {
      if (!confirm("강제 클린업: owner=apitest 가 만든 모든 자원을 TTL 무시하고 삭제합니다.\n(우리 소유가 아닌 자원은 절대 건드리지 않습니다.)\n진행할까요?")) return;
      fetch("/api/cleanup", { method: "POST" }).then(r => r.json()).then(j => {
        if (j.error) { alert(j.error); return; }
        runId = j.id; runEvents = []; runStatus = "running"; reportSub = "r4";
        drawReport(); startR4Poll();
      }).catch(() => alert("서버 연결 실패"));
    };
    $("btn-verify").onclick = () => {
      fetch("/api/verify", { method: "POST" }).then(r => r.json()).then(j => {
        if (j.error) { alert(j.error); return; }
        runId = j.id; runEvents = []; runStatus = "running"; reportSub = "r4";
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
    if (screen !== "run" || reportSub !== "r4") return;     // only while visible
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
      const icon = r.status === "running" ? "⏳" : r.status === "done" ? (r.rc === 0 ? "✅" : "⚠️") : "❌";
      const dur = (r.ended && r.started) ? Math.round(r.ended - r.started) + "s" : (r.status === "running" ? "실행중…" : "");
      const on = runId === r.id;
      const tag = KIND[r.kind] || esc(r.kind || "");
      return `<div class="runrow ${on ? "on" : ""}" data-id="${esc(r.id)}">
        <span><span class="kindtag">${tag}</span> <b class="small">${icon} ${esc(r.id)}</b>
          <span class="muted small">${esc((r.lifecycle_ids || []).slice(0, 2).join(", "))}${(r.lifecycle_ids || []).length > 2 ? " …" : ""}</span></span>
        <span class="muted small">${esc(r.summary || r.status)} · ${dur}</span></div>`;
    }).join("");
    els("#report-side .runrow").forEach(row => row.onclick = () => {
      runId = row.dataset.id; runEvents = []; runStatus = "running";
      fetch("/api/runs/" + runId + "/events").then(r => r.json()).then(j2 => {
        runEvents = j2.events || []; runStatus = j2.status || "done";
        if (runStatus === "running") pollEvents();
        drawReport();
      }).catch(() => drawReport());
    });
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
