/* console2 — single-page execution console (LIGHT theme).
 * IA (locked): ① 구성 — category sections + COMPACT service cards (click = select
 * whole service; "리소스…" → modal for specific resources) drive a LIVE composition
 * DAG (composer.graph_view via /api/graph) + a 생성/검증/삭제 순서표. ② 실행 & 리포트 —
 * the existing run (simulate | live) + event-driven report (R1 진행 / R2 리소스 /
 * R3 API / R4 로그), re-themed light, with the ① selection carried into the launch.
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

// run/report state
let runId = null;
let runMode = "simulate";
let runAxis = "regression-light";
let runEvents = [];
let runStatus = "idle";
let pollTimer = null;
let reportSub = "r1";

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
  drawSvcGrid();
  refreshGraph();           // fetch /api/graph for the current selection
  $("sel-search").oninput = drawSvcGrid;
  $("sel-all").onclick = toggleAll;
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

// the controls/readout + count (selected services, resources, closure)
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

// category sections + compact service cards (picker) -----------------------
function drawSvcGrid() {
  const q = ($("sel-search").value || "").toLowerCase();
  const cats = categoryMap();
  let h = "";
  Object.keys(cats).forEach(cat => {
    const svcs = cats[cat].filter(s => !q || (shortName(s) + " " + s).toLowerCase().includes(q));
    if (!svcs.length) return;
    const selectableSvcs = svcs.filter(s => svcSelectable(s).length);
    const onCount = selectableSvcs.filter(s => svcState(s) !== "off").length;
    const catAllOn = selectableSvcs.length && selectableSvcs.every(s => svcState(s) === "on");
    h += `<div class="catsec"><div class="catsec-h">
        <span class="csn">${esc(cat)}</span>
        <button class="csbtn ${catAllOn ? "on" : ""}" data-cat="${esc(cat)}" ${selectableSvcs.length ? "" : "disabled"}>${catAllOn ? "✓ 카테고리 선택" : "카테고리 선택"}</button>
        <span class="cscount">선택 <b>${onCount}</b>/총 ${svcs.length} svc</span>
      </div><div class="svcgrid">`;
    svcs.forEach(svc => {
      const sel = svcSelectable(svc);
      const all = svcNodes(svc);
      const st = svcState(svc);
      const heavy = all.some(id => N[id].heavy);
      const quota = all.some(id => N[id].quota);
      const onN = sel.filter(id => targets.has(id)).length;
      const cls = st === "on" ? "on" : st === "partial" ? "partial" : "";
      const fracTxt = st === "partial" ? `${onN}/${sel.length} 리소스` : `${sel.length} 리소스`;
      h += `<div class="svc-cell"><div class="svc ${cls}" data-svc="${esc(svc)}" title="${esc(svc)} — 클릭하면 서비스 전체(생애주기 보유 리소스) 선택">
          <div class="sh"><span class="sn">${esc(shortName(svc))}</span>
            <span class="schk">${st === "on" ? "✓" : st === "partial" ? "◐" : ""}</span></div>
          <div class="sfrac"><span>${esc(fracTxt)}</span>
            ${heavy ? '<span class="glyph" title="heavy 리소스 포함">🜂</span>' : ""}
            ${quota ? '<span class="glyph q" title="quota 제약 리소스 포함">⛔</span>' : ""}</div>
          ${sel.length ? `<button class="resbtn ${st === "partial" ? "pick" : ""}" data-res-svc="${esc(svc)}">리소스…</button>` : '<span class="resbtn dim" style="cursor:default">생애주기 없음</span>'}
        </div></div>`;
    });
    h += `</div></div>`;
  });
  $("svcWrap").innerHTML = h || '<p class="empty">검색 결과 없음</p>';

  // card click = toggle whole service
  els("#svcWrap .svc[data-svc]").forEach(card => card.onclick = ev => {
    if (ev.target.closest("[data-res-svc]")) return;   // the "리소스…" button has its own handler
    const svc = card.dataset.svc;
    if (!svcSelectable(svc).length) return;
    setSvc(svc, svcState(svc) !== "on");
    selectionChanged();
  });
  // category select toggle
  els("#svcWrap .csbtn[data-cat]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const cat = b.dataset.cat;
    const svcs = (categoryMap()[cat] || []).filter(s => svcSelectable(s).length);
    const allOn = svcs.length && svcs.every(s => svcState(s) === "on");
    svcs.forEach(s => setSvc(s, !allOn));
    selectionChanged();
  });
  // "리소스…" → modal
  els("#svcWrap [data-res-svc]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openModal(b.dataset.resSvc);
  });
  selReadout();
}

// any selection change: re-grid (state), readout, and re-fetch the DAG (debounced)
function selectionChanged() {
  if (screen === "build") drawSvcGrid();
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
  drawRunSettings();
  drawReport();
}
function drawRunSettings() {
  const ax = AXES[runAxis];
  const svcs = new Set([...targets].map(id => N[id].service));
  $("run-left").innerHTML = `<h2>실행 설정</h2>
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
    ${targets.size ? "" : '<p class="muted small">선택이 없습니다 — ① 구성에서 서비스를 고르세요.</p>'}`;
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
function pollEvents() {
  if (!runId) return;
  if (pollTimer) clearTimeout(pollTimer);
  fetch("/api/runs/" + runId + "/events").then(r => r.json()).then(j => {
    runEvents = j.events || [];
    runStatus = j.status || runStatus;
    const ended = runEvents.some(e => e.kind === "run-end") || (runStatus !== "running");
    if (screen === "run") drawReport();
    if (!ended) pollTimer = setTimeout(pollEvents, 1500);
    else { runStatus = runStatus === "running" ? "done" : runStatus; if (screen === "run") drawReport(); }
  }).catch(() => { pollTimer = setTimeout(pollEvents, 2000); });
}

// ================= ④ 리포트 (R1/R2/R3/R4) — kept functional, light theme =================
function drawReport() {
  els("#report-subtabs button").forEach(b => b.classList.toggle("on", b.dataset.r === reportSub));
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

// R1 진행 — composition DAG colored by live lifecycle state + wave progress.
// Re-uses ResourceGraph with an overlay mapping each node's source lifecycle state.
function reportR1() {
  const st = lifecycleStates();
  const FILL = { queued: "#ffffff", running: "#e8f0fd", done: "#eaf7ee", fail: "#fdeaea", skip: "#f6f8fa" };
  const STK = { queued: "#8a93a0", running: "#2563c9", done: "#2da44e", fail: "#cf222e", skip: "#8a93a0" };
  const BDG = { queued: "", running: "⏳", done: "✓", fail: "✕", skip: "–" };
  const nodeState = id => { const lc = N[id] && N[id].lifecycle; return lc && st[lc] ? st[lc] : null; };
  $("report-main").innerHTML = `<h2>R1 진행 <span class="muted small">· DAG 노드 = lifecycle 라이브 상태</span></h2>
    <div class="legend">${legend([["#ffffff", "queued"], ["#e8f0fd", "running"], ["#eaf7ee", "done"], ["#fdeaea", "fail"]])}</div>
    <div class="svgbox"><svg id="r1-svg"></svg></div>
    <div id="r1-prog" style="margin-top:8px"></div>`;
  const g = lastGraph && lastGraph.nodes.length ? lastGraph : null;
  if (g) {
    window.ResourceGraph.render($("r1-svg"), g, {
      overlay: id => {
        const s = nodeState(id);
        if (!s) return null;
        return { fill: FILL[s], stroke: STK[s], badge: BDG[s] };
      }
    });
  } else {
    $("r1-svg").innerHTML = '<text x="12" y="22" fill="#656d76">합성 그래프 없음</text>';
  }
  // wave progress under the canvas
  const waves = {};
  runEvents.forEach(e => { if (e.kind === "wave-start") waves[e.wave] = { kind: e.wave_kind, lcs: e.lifecycles || [] }; });
  const counts = k => Object.values(st).filter(v => v === k).length;
  const total = Object.keys(st).length || (g ? g.nodes.filter(n => n.is_target).length : 0);
  const waveLines = Object.keys(waves).sort((a, b) => a - b).map(i => {
    const w = waves[i];
    const done = w.lcs.filter(l => st[l] === "done").length;
    const running = w.lcs.some(l => st[l] === "running");
    const pct = w.lcs.length ? Math.round(100 * done / w.lcs.length) : 0;
    return `<div class="kv"><span>${running ? "⏳" : done === w.lcs.length && w.lcs.length ? "✓" : "·"}
      웨이브 ${i} <span class="muted small">${esc(w.kind || "")} · ${w.lcs.length}개</span></span><b>${done}/${w.lcs.length}</b></div>
      <div class="pbar ${done === w.lcs.length && w.lcs.length ? "done" : ""}"><i style="width:${pct}%"></i></div>`;
  }).join("");
  $("r1-prog").innerHTML = `<div class="kpi">
      <div class="s"><b>${counts("done")}/${total}</b><span>완료</span></div>
      <div class="s"><b style="color:var(--run)">${counts("running")}</b><span>실행중</span></div>
      <div class="s"><b style="color:var(--fail)">${counts("fail")}</b><span>fail</span></div>
      <div class="s"><b>${esc(runStatus)}</b><span>상태</span></div>
    </div>
    <h3>웨이브 진행 (실행 순서)</h3>${waveLines || '<p class="muted small">웨이브 이벤트 대기 중…</p>'}`;
}

// R2 리소스 — per-resource rows from resource-tracked / resource-deleted
function reportR2() {
  const rows = {};
  const lcVerifyOk = {};
  runEvents.forEach(e => {
    if (e.kind === "resource-tracked")
      rows[e.resource_id] = { id: e.resource_id, type: e.resource_type, lifecycle: e.lifecycle,
        path: e.path, created: true, deleted: false, tested: false };
    if (e.kind === "resource-deleted") {
      const cand = Object.values(rows).filter(r => r.lifecycle === e.lifecycle && r.type === e.resource_type && !r.deleted);
      if (cand.length) cand[cand.length - 1].deleted = true;
    }
    if (e.kind === "step-end" && (e.method || "").toUpperCase() === "GET" && e.category === "ok")
      lcVerifyOk[e.lifecycle] = true;
  });
  Object.values(rows).forEach(r => { r.tested = !!lcVerifyOk[r.lifecycle]; });
  const list = Object.values(rows);
  const simLabel = runMode === "simulate" ? ' <span class="muted small">(simulate: 합성 id)</span>' : "";
  const body = list.length ? list.map(r => `<tr>
      <td>${esc(r.type)}</td>
      <td><code>${esc(r.id)}</code></td>
      <td>${esc(r.lifecycle)}</td>
      <td class="${r.created ? "tick" : "tickno"}">${r.created ? "✓" : "—"}</td>
      <td class="${r.tested ? "tick" : "tickno"}">${r.tested ? "✓" : "—"}</td>
      <td class="${r.deleted ? "tick" : "tickno"}">${r.deleted ? "✓" : "—"}</td>
    </tr>`).join("") : '<tr><td colspan="6" class="empty">리소스 이벤트 없음 (실행 중이거나 create 스텝 없음)</td></tr>';
  $("report-main").innerHTML = `<h2>R2 리소스${simLabel}</h2>
    <p class="muted small">create/delete 스텝마다 추적된 실자원 — resource_type · resource_id · 생성/테스트/삭제.</p>
    <table class="tbl">
      <thead><tr><th>type</th><th>resource_id</th><th>lifecycle</th><th>생성</th><th>테스트</th><th>삭제</th></tr></thead>
      <tbody>${body}</tbody></table>`;
}

// R3 API — api-first table of step-start/step-end (method+path, 결과, 응답시간)
function reportR3() {
  const calls = [];
  const open = {};
  runEvents.forEach(e => {
    if (e.kind === "step-start") {
      const k = e.lifecycle + "|" + e.step;
      open[k] = { lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path, status: null, category: "run", ms: null };
      calls.push(open[k]);
    }
    if (e.kind === "step-end") {
      const k = e.lifecycle + "|" + e.step;
      const c = open[k] || { lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path };
      c.status = e.status; c.category = e.category; c.ms = e.elapsed_ms;
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
    byLc[lc].map(c => `<tr>
        <td><span class="mtag ${esc(c.method || "")}">${esc(c.method || "")}</span> <code>${esc(c.path || "")}</code></td>
        <td>${badge(c.category)}</td>
        <td class="muted">${c.status != null ? esc(c.status) : "—"}</td>
        <td class="muted">${c.ms != null ? c.ms + " ms" : (c.category === "run" ? "⏳" : "—")}</td>
      </tr>`).join("")).join("");
  $("report-main").innerHTML = `<h2>R3 API <span class="muted small">· api 단위 (대상 · 결과 · 응답시간)</span></h2>
    <div class="kpi">
      <div class="s"><b>${calls.length}</b><span>api 호출</span></div>
      <div class="s"><b style="color:var(--ok)">${okN}</b><span>ok</span></div>
      <div class="s"><b style="color:var(--soft)">${softN}</b><span>soft</span></div>
      <div class="s"><b style="color:var(--fail)">${failN}</b><span>fail</span></div>
    </div>
    <div class="scroll" style="max-height:520px;margin-top:8px"><table class="tbl">
      <thead><tr><th>method · path (대상)</th><th>결과</th><th>status</th><th>응답시간</th></tr></thead>
      <tbody>${body || '<tr><td colspan="4" class="empty">api 이벤트 없음</td></tr>'}</tbody></table></div>`;
}

// R4 로그 — raw run log + cleanup/verify controls
function reportR4() {
  $("report-main").innerHTML = `<h2>R4 로그 <span class="muted small">· 원시 실행 로그</span></h2>
    <div class="run-ctl">
      <button class="minibtn red" id="btn-cleanup" title="우리(owner)가 만든 자원을 강제 삭제 (reconciler, TTL 무시).">🧹 강제 클린업</button>
      <button class="minibtn" id="btn-verify" title="삭제 없이 남은 우리 자원 수 확인 (read-only).">🔍 클린업 확인</button>
      <button class="minibtn" id="btn-reflog">↻ 로그 새로고침</button>
    </div>
    <pre class="runlog" id="r4-log">로그 로딩…</pre>`;
  loadLog();
  $("btn-reflog").onclick = loadLog;
  $("btn-cleanup").onclick = () => {
    if (!confirm("강제 클린업: owner=apitest 가 만든 모든 자원을 TTL 무시하고 삭제합니다.\n(우리 소유가 아닌 자원은 절대 건드리지 않습니다.)\n진행할까요?")) return;
    fetch("/api/cleanup", { method: "POST" }).then(r => r.json()).then(j => {
      if (j.error) { alert(j.error); return; }
      runId = j.id; runEvents = []; runStatus = "running"; pollLogRun(); drawReport();
    }).catch(() => alert("서버 연결 실패"));
  };
  $("btn-verify").onclick = () => {
    fetch("/api/verify", { method: "POST" }).then(r => r.json()).then(j => {
      if (j.error) { alert(j.error); return; }
      runId = j.id; runEvents = []; runStatus = "running"; pollLogRun(); drawReport();
    }).catch(() => alert("서버 연결 실패"));
  };
}
function loadLog() {
  if (!runId) return;
  fetch("/api/runs/" + runId).then(r => r.json()).then(j => {
    const pre = $("r4-log"); if (!pre) return;
    pre.textContent = j.log || "(로그 없음)";
    pre.scrollTop = pre.scrollHeight;
  }).catch(() => { const pre = $("r4-log"); if (pre) pre.textContent = "(로그 로드 실패)"; });
}
function pollLogRun() {
  if (!runId) return;
  fetch("/api/runs/" + runId).then(r => r.json()).then(j => {
    runStatus = j.status || runStatus;
    if (screen === "run") { if (reportSub === "r4") loadLog(); loadRunRecords(); }
    if (j.status === "running") setTimeout(pollLogRun, 1500);
  }).catch(() => setTimeout(pollLogRun, 2000));
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
