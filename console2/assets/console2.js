/* console2 — single-page execution console.
 * Loop: ① 선택(layered-DAG canvas) → ② Plan(real dag_planner waves) →
 *       ③ 실행(Axis × mode) → ④ 리포트(R1 진행 / R2 리소스 / R3 API / R4 로그),
 * all driven by /api/model + /api/plan + /api/run + the live event stream.
 *
 * Vocabulary (locked concept model): category → service → resource → api.
 *   selection (resource) pulls its dependency CLOSURE (auto-ordered) ·
 *   execution unit = lifecycle · reporting unit = api (a lifecycle step) ·
 *   a run = Scope × Axis (axis is per-run, not per-resource). */
(function () {
"use strict";

// ---- tiny DOM helpers ----
const $ = id => document.getElementById(id);
const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const el = (q, r) => (r || document).querySelector(q);
const els = (q, r) => [...(r || document).querySelectorAll(q)];

// ---- global state ----
let MODEL = null;          // raw /api/model payload
let V = null;              // VIZ (after viz.js loads with window.MODEL set)
let N = {};                // VIZ.N (nodes)
let targets = new Set();   // selected resource ids (targets)
let stage = "select";
let lastPlan = null;       // last /api/plan response (selection-driven)
let openExpand = {};       // selector tree expanded cats/services

// run/report state
let runId = null;          // current/last run id
let runMode = "simulate";
let runAxis = "regression-light";
let runEvents = [];        // accumulated events for the open run
let runStatus = "idle";
let pollTimer = null;
let reportSub = "r1";

// ---- bootstrap: fetch model, set window.MODEL, THEN load viz.js ----
fetch("/api/model").then(r => r.json()).then(m => {
  if (m.error) throw new Error(m.error);
  MODEL = m;
  // viz.js reads window.MODEL at load; only nodes+groups are needed.
  window.MODEL = { nodes: m.nodes, groups: m.groups };
  const s = document.createElement("script");
  s.src = "assets/viz.js";
  s.onload = init;
  s.onerror = () => fatal("viz.js 로드 실패");
  document.body.appendChild(s);
}).catch(e => fatal("백엔드 연결 실패 — <code>python tools/console2_server.py</code> 실행 중인가요? (" + esc(e.message) + ")"));

function fatal(html) {
  $("ctxbar").innerHTML = '<span class="seg" style="color:var(--fail)">● ' + html + "</span>";
}

function init() {
  V = window.VIZ; N = V.N;
  // default selection: a small, meaningful target so the canvas isn't empty.
  ["vpc", "subnet"].forEach(id => { if (V.exists(id)) targets.add(id); });
  if (!targets.size) {  // fallback: first node that has a lifecycle
    const id = V.allIds().find(i => N[i].lifecycle);
    if (id) targets.add(id);
  }
  wireTabs();
  ctxBar();
  go("select");
}

// ---- selectability: a resource is standalone-selectable iff it maps to a lifecycle.
// (~20 lookup/pure-dep resources have lifecycle=null → shown dimmed, non-selectable;
// they still appear on the canvas when pulled in as a dependency.) ----
const hasLifecycle = id => !!(N[id] && N[id].lifecycle);

// ---- global context bar ----
function ctxBar() {
  const closure = V.closure([...targets]);
  const svcs = new Set([...closure].map(id => N[id].service));
  const heavy = [...closure].some(id => N[id].heavy);
  const axisLabel = AXES[runAxis] ? AXES[runAxis].label : runAxis;
  const isLive = runMode === "live";
  $("ctxbar").innerHTML =
    `<span class="seg">env <b>local</b></span>
     <span class="seg">· axis <b>${esc(axisLabel)}</b></span>
     <span class="seg">· mode <b>${runMode}</b></span>
     <span class="seg">· 대상 <b>${targets.size}</b></span>
     <span class="seg">· 폐포 <b>${closure.size}</b></span>
     <span class="seg">· 서비스 <b>${svcs.size}</b></span>
     <span class="seg">· heavy <b>${heavy ? "🜂 포함" : "없음"}</b></span>
     <span class="seg">· 모델 <b>${MODEL.node_count}</b>자원 / <b>${MODEL.lifecycle_count}</b>lifecycle</span>
     <span class="badge ${isLive ? "live" : "sim"}">${isLive ? "LIVE" : "SIMULATE"}</span>`;
}

// ================= stage switching =================
const STAGES = ["select", "plan", "run", "report"];
function wireTabs() {
  els(".tabs button").forEach(b => b.onclick = () => go(b.dataset.t));
  els("#report-subtabs button").forEach(b => b.onclick = () => { reportSub = b.dataset.r; drawReport(); });
}
function go(t) {
  stage = t;
  STAGES.forEach(s => $("stage-" + s).classList.toggle("hidden", s !== t));
  els(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  ctxBar();
  if (t === "select") drawSelect();
  else if (t === "plan") drawPlan();
  else if (t === "run") drawRun();
  else if (t === "report") drawReport();
}
window.go = go;

// ================= ① 선택 (Select) =================
// layered-DAG canvas (emulates 02-layered-dag.html: longest-path depth = creation
// order left→right, category swimlanes). targets vs pulled-in deps are distinct;
// lookup/no-lifecycle resources are dimmed + non-selectable.
function drawSelect() {
  selectTree();
  selectCanvas();
  selectSummary();
  $("sel-clear").onclick = () => { targets.clear(); ctxBar(); drawSelect(); };
  $("sel-search").oninput = selectTree;
}

function selectTree() {
  const q = ($("sel-search").value || "").toLowerCase();
  // category → service → resource
  const byCat = {};
  V.allIds().forEach(id => {
    const n = N[id];
    if (q && !(id + " " + n.service).toLowerCase().includes(q)) return;
    ((byCat[n.category] = byCat[n.category] || {})[n.service] =
      (byCat[n.category][n.service] || [])).push(id);
  });
  const cats = Object.keys(byCat).sort();
  let h = "";
  cats.forEach(cat => {
    const open = q ? true : (openExpand["c:" + cat] !== false);  // open by default
    const svcs = Object.keys(byCat[cat]).sort();
    const allRes = svcs.flatMap(s => byCat[cat][s]).filter(hasLifecycle);
    const allSel = allRes.length && allRes.every(id => targets.has(id));
    h += `<div class="cat"><div class="catrow">
        <span class="twirl" data-tw="c:${esc(cat)}">${open ? "▾" : "▸"}</span>
        <span data-tw="c:${esc(cat)}" style="cursor:pointer">${esc(cat)}</span>
        <span class="cnt">${allRes.length}</span>
        <button class="allbtn" data-all="cat:${esc(cat)}" ${allRes.length ? "" : "disabled"}>${allSel ? "해제" : "전체"}</button>
      </div>`;
    if (open) {
      h += `<div class="svcwrap">`;
      svcs.forEach(svc => {
        const sOpen = q ? true : (openExpand["s:" + svc] !== false);
        const res = byCat[cat][svc].slice().sort();
        const selRes = res.filter(hasLifecycle);
        const sAll = selRes.length && selRes.every(id => targets.has(id));
        const short = svc.split("/").pop();
        h += `<div class="svc"><div class="svcrow">
            <span class="twirl" data-tw="s:${esc(svc)}">${sOpen ? "▾" : "▸"}</span>
            <span data-tw="s:${esc(svc)}" style="cursor:pointer">${esc(short)}</span>
            <span class="cnt">${selRes.length}</span>
            <button class="allbtn" data-all="svc:${esc(svc)}" ${selRes.length ? "" : "disabled"}>${sAll ? "해제" : "전체"}</button>
          </div>`;
        if (sOpen) {
          h += `<div class="reswrap">`;
          res.forEach(id => {
            const lc = hasLifecycle(id);
            const isT = targets.has(id);
            h += `<div class="resrow ${lc ? "" : "nolc"} ${isT ? "istarget" : ""}">
              <label>${lc
                ? `<input type="checkbox" data-res="${esc(id)}" ${isT ? "checked" : ""}>`
                : `<input type="checkbox" disabled title="lifecycle 없음 — 의존으로만 포함">`}
                <span class="dot" style="background:${V.provColor(N[id].provenance)}"></span>
                <b>${esc(id)}</b></label>
              ${lc ? "" : `<span class="nolctag" title="순수 의존/룩업 — 단독 선택 불가">dep-only</span>`}
            </div>`;
          });
          h += `</div>`;
        }
        h += `</div>`;
      });
      h += `</div>`;
    }
    h += `</div>`;
  });
  $("sel-tree").innerHTML = h || '<p class="empty">검색 결과 없음</p>';
  // wire twirls
  els("#sel-tree [data-tw]").forEach(e => e.onclick = ev => {
    ev.stopPropagation();
    const k = e.dataset.tw, kk = (k[0] === "c" ? "c:" : "s:") + k.slice(2);
    openExpand[kk] = openExpand[kk] === false ? true : false;
    selectTree();
  });
  // wire "전체/해제"
  els("#sel-tree [data-all]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const [kind, key] = b.dataset.all.split(/:(.+)/);
    let ids;
    if (kind === "cat") ids = V.allIds().filter(id => N[id].category === key && hasLifecycle(id));
    else ids = V.allIds().filter(id => N[id].service === key && hasLifecycle(id));
    const allSel = ids.every(id => targets.has(id));
    ids.forEach(id => allSel ? targets.delete(id) : targets.add(id));
    ctxBar(); drawSelect();
  });
  // wire resource checkboxes
  els("#sel-tree input[data-res]").forEach(cb => cb.onchange = () => {
    cb.checked ? targets.add(cb.dataset.res) : targets.delete(cb.dataset.res);
    ctxBar(); selectCanvas(); selectSummary();
    els("#sel-tree input[data-res]").forEach(x =>
      x.closest(".resrow").classList.toggle("istarget", targets.has(x.dataset.res)));
  });
}

function selectCanvas() {
  if (!targets.size) {
    $("sel-svg").innerHTML = "";
    $("sel-ghint").textContent = "왼쪽 트리에서 자원을 선택하면 의존 폐포가 생성 순서대로 배치됩니다.";
    return;
  }
  const closure = V.closure([...targets]);
  drawSwimlane($("sel-svg"), closure, {
    targets,
    onClick: id => {
      if (!hasLifecycle(id)) return;            // dep-only resources are not toggleable
      targets.has(id) ? targets.delete(id) : targets.add(id);
      if (!targets.size) targets.add(id);       // keep at least the clicked one
      ctxBar(); drawSelect();
    }
  });
  $("sel-legend").innerHTML = legend([
    ["#3b82f6", "대상(target)"], ["#27384b", "폐포(의존)"], ["#0b121c", "dep-only(흐림)"]
  ]) + '<span>🜂 heavy · ⛔ quota · 클릭 = 대상 토글</span>';
  $("sel-ghint").textContent =
    "깊이(왼→오) = 최장경로 생성 순서 · 가로 밴드 = 카테고리(스윔레인) · 의존은 service/category 경계를 넘을 수 있음.";
}

function selectSummary() {
  const closure = V.closure([...targets]);
  const svcs = new Set([...closure].map(id => N[id].service));
  const cats = new Set([...closure].map(id => N[id].category));
  const heavy = [...closure].filter(id => N[id].heavy);
  const lcs = new Set([...closure].map(id => N[id].lifecycle).filter(Boolean));
  const quota = {};
  closure.forEach(id => { const qq = N[id].quota; if (qq) quota[qq] = (quota[qq] || 0) + 1; });
  const depOnly = [...closure].filter(id => !hasLifecycle(id));
  $("sel-right").innerHTML = `<h2>선택 요약</h2>
    <div class="kpi">
      <div class="s"><b>${targets.size}</b><span>대상</span></div>
      <div class="s"><b>${closure.size}</b><span>폐포</span></div>
      <div class="s"><b>${lcs.size}</b><span>lifecycle</span></div>
    </div>
    <div class="kpi" style="margin-top:7px">
      <div class="s"><b>${svcs.size}</b><span>서비스</span></div>
      <div class="s"><b>${cats.size}</b><span>카테고리</span></div>
      <div class="s"><b style="color:${heavy.length ? "var(--heavy)" : "var(--ink)"}">${heavy.length}</b><span>heavy</span></div>
    </div>
    <h3>quota peaks</h3>
    <div class="chiprow">${Object.entries(quota).map(([k, v]) =>
      `<span class="chip">⛔ <b>${esc(k)}</b> ×${v}</span>`).join("") || '<span class="muted small">없음</span>'}</div>
    <h3>대상 (${targets.size})</h3>
    <div class="chiprow">${[...targets].sort().map(id =>
      `<span class="chip"><b>${esc(id)}</b><span class="x" data-rm="${esc(id)}">×</span></span>`).join("")
      || '<span class="muted small">없음</span>'}</div>
    ${depOnly.length ? `<h3>dep-only 자원 (${depOnly.length})</h3>
      <p class="muted small">${depOnly.slice(0, 12).map(esc).join(" · ")}${depOnly.length > 12 ? " …" : ""}</p>` : ""}
    <div class="run-ctl"><button class="btn" id="sel-toplan" ${targets.size ? "" : "disabled"}>Plan으로 →</button></div>`;
  els("#sel-right [data-rm]").forEach(x => x.onclick = () => {
    targets.delete(x.dataset.rm); ctxBar(); drawSelect();
  });
  if ($("sel-toplan")) $("sel-toplan").onclick = () => go("plan");
}

// ================= ② Plan =================
function drawPlan() {
  $("plan-right").innerHTML = '<p class="empty">plan 요청 중…</p>';
  $("plan-waves").innerHTML = "";
  const sel = selectionPayload();
  fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sel) })
    .then(r => r.json()).then(p => {
      if (p.error) { $("plan-right").innerHTML = '<p class="empty">plan 실패: ' + esc(p.error) + "</p>"; return; }
      lastPlan = p;
      renderPlan(p);
    }).catch(e => { $("plan-right").innerHTML = '<p class="empty">plan 연결 실패: ' + esc(e.message) + "</p>"; });
}

function renderPlan(p) {
  const plan = p.plan || {};
  const waves = plan.waves || [];
  // (a) resource DAG (closure of the selection), depth = 생성 순서
  const closure = V.closure([...targets]);
  drawSwimlane($("plan-svg"), closure, { targets });
  $("plan-legend").innerHTML = legend([["#3b82f6", "대상"], ["#27384b", "의존"]]) +
    `<span>shared roots: ${(plan.shared_roots || []).map(r => `<code>${esc(r)}</code>`).join(" ") || "—"}</span>`;
  $("plan-gtitle").innerHTML = `실행 DAG <span class="muted small">· 폐포 ${closure.size} · leaf ${(plan.leaf_set || []).length}</span>`;

  // (b) dag_planner waves
  $("plan-waves").innerHTML = waves.length ? waves.map((w, i) => {
    const kind = (w.kind || "").replace("-", "");
    return `<div class="wave">
      <div class="wh"><span>웨이브 ${i}</span>
        <span class="wk ${esc(kind)}">${esc(w.kind)}</span>
        <span class="muted small">동시 ${w.lifecycles.length}개${w.vpc_slots ? ` · VPC 슬롯 ${w.vpc_slots}` : ""}</span></div>
      <div class="ll">${w.lifecycles.map(l => `<span class="chip"><b>${esc(l)}</b></span>`).join("")}</div>
    </div>`;
  }).join("") : '<p class="empty">웨이브 없음 (실행 가능한 lifecycle이 선택에 없음)</p>';

  // right: per-lifecycle step preview (which APIs each leaf calls) + counts
  const preview = p.preview || {};
  const leaves = Object.keys(preview).sort();
  const stepRows = leaves.map(lid => {
    const pv = preview[lid];
    const steps = (pv.steps || []).filter(s => s.method);  // HTTP steps only
    return `<tr class="lc-head"><td colspan="2">${esc(lid)} <span class="muted small">${esc(pv.service)}${pv.heavy ? " · 🜂" : ""} · ${steps.length} api</span></td></tr>` +
      steps.map(s => `<tr><td><span class="mtag ${esc(s.method)}">${esc(s.method)}</span></td>
        <td><code>${esc(s.path)}</code> <span class="muted small">${esc(s.kind)}</span></td></tr>`).join("");
  }).join("");
  const skipped = p.skipped_disabled || [];
  $("plan-right").innerHTML = `<h2>Plan 요약</h2>
    <div class="kpi">
      <div class="s"><b>${(plan.leaf_set || []).length}</b><span>leaf lifecycle</span></div>
      <div class="s"><b>${waves.length}</b><span>웨이브</span></div>
      <div class="s"><b>${p.peak_vpcs != null ? p.peak_vpcs : "—"}</b><span>peak VPCs</span></div>
    </div>
    <div class="kv"><span>요청 lifecycle</span><b>${(p.requested || []).length}</b></div>
    <div class="kv"><span>실행 가능</span><b>${(p.runnable || []).length}</b></div>
    <div class="kv"><span>공유 루트</span><b>${(plan.shared_roots || []).length}</b></div>
    ${skipped.length ? `<div class="note small"><b>skipped (disabled):</b> ${skipped.map(esc).join(", ")}</div>` : ""}
    <h3>lifecycle별 API 미리보기</h3>
    <div class="scroll" style="max-height:420px"><table class="tbl">${stepRows ||
      '<tr><td class="empty">미리보기 없음</td></tr>'}</table></div>
    <div class="run-ctl"><button class="btn" id="plan-torun" ${(plan.leaf_set || []).length ? "" : "disabled"}>실행으로 →</button></div>`;
  if ($("plan-torun")) $("plan-torun").onclick = () => go("run");
}

// ================= ③ 실행 (Run) =================
const AXES = {
  "smoke":             { label: "smoke", desc: "읽기 전용 (다음 빌드)", enabled: false, gates: {} },
  "regression-light":  { label: "회귀-light", desc: "CRUD · mutations+destructive", enabled: true,
                         gates: { mutations: true, destructive: true, heavy: false } },
  "regression-heavy":  { label: "회귀-heavy", desc: "CRUD+billable · heavy 포함", enabled: true,
                         gates: { mutations: true, destructive: true, heavy: true } },
  "conformance":       { label: "conformance", desc: "설계 적합성 (다음 빌드)", enabled: false, gates: {} }
};

function drawRun() {
  const closure = V.closure([...targets]);
  const lcs = new Set([...closure].map(id => N[id].lifecycle).filter(Boolean));
  const ax = AXES[runAxis];
  $("run-left").innerHTML = `<h2>실행 설정</h2>
    <h3>Axis <span class="muted small">(run 단위 — resource별 아님)</span></h3>
    <div class="axisgrid" id="axisgrid">${Object.entries(AXES).map(([k, a]) =>
      `<label class="axisopt ${runAxis === k ? "on" : ""} ${a.enabled ? "" : "disabled"}">
        <input type="radio" name="axis" value="${k}" ${runAxis === k ? "checked" : ""} ${a.enabled ? "" : "disabled"}>
        <span><span class="t">${esc(a.label)}</span><br><span class="d">${esc(a.desc)}</span></span>
      </label>`).join("")}</div>
    <h3>mode</h3>
    <div class="seg-ctl" id="modeseg">
      <button data-m="simulate" class="${runMode === "simulate" ? "on" : ""}">simulate</button>
      <button data-m="live" class="${runMode === "live" ? "on" : ""}">live</button>
    </div>
    <p class="muted small" style="margin-top:6px">simulate = 플랜을 결정론적으로 재생(클라우드 호출 없음, 합성 id). live = 실제 pytest + 안전 게이트.</p>
    <h3>적용 게이트</h3>
    <div class="chiprow" id="gatechips"></div>
    <div class="kv"><span>실행 lifecycle</span><b>${lcs.size}</b></div>
    <div class="run-ctl"><button class="btn ${runMode === "live" ? "warn" : ""}" id="run-go" ${lcs.size ? "" : "disabled"}>
      ${runMode === "live" ? "⚠ LIVE 실행 ▶" : "▶ simulate 실행"}</button></div>
    ${lcs.size ? "" : '<p class="muted small">선택에 실행 가능한 lifecycle이 없습니다 — ① 선택에서 자원을 고르세요.</p>'}`;
  gateChips();
  els("#axisgrid input").forEach(r => r.onchange = () => {
    if (!AXES[r.value].enabled) return;
    runAxis = r.value; ctxBar(); drawRun();
  });
  els("#modeseg button").forEach(b => b.onclick = () => { runMode = b.dataset.m; ctxBar(); drawRun(); });
  $("run-go").onclick = startRun;
  // keep the live area if a run is in flight / done
  $("run-live").innerHTML = runId ? liveProgressHtml() : "";
}

function gateChips() {
  const g = AXES[runAxis].gates || {};
  const chips = [
    ["mutations", g.mutations], ["destructive", g.destructive], ["heavy", g.heavy]
  ].map(([k, v]) => `<span class="chip" style="border-color:${v ? "var(--heavy)" : "var(--line)"}">
    ${v ? "✔" : "✕"} ${k}</span>`).join("");
  if ($("gatechips")) $("gatechips").innerHTML = runMode === "live"
    ? chips
    : '<span class="muted small">simulate — 게이트 무관 (클라우드 호출 없음)</span>';
}

function startRun() {
  const ax = AXES[runAxis];
  const sel = selectionPayload();
  const body = Object.assign({ mode: runMode }, sel);
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
  $("run-go").disabled = true;
  $("run-live").innerHTML = '<p class="muted small">실행 요청 중…</p>';
  fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) })
    .then(r => r.json()).then(j => {
      if (j.error) { $("run-live").innerHTML = '<p class="empty">실행 실패: ' + esc(j.error) + "</p>"; $("run-go").disabled = false; return; }
      runId = j.id; runEvents = []; runStatus = "running";
      pollEvents();
      go("report");
    }).catch(e => { $("run-live").innerHTML = '<p class="empty">실행 연결 실패: ' + esc(e.message) + "</p>"; $("run-go").disabled = false; });
}

function liveProgressHtml() {
  return `<div class="note small">run <code>${esc(runId)}</code> · ${esc(runStatus)} —
    <a href="#" onclick="go('report');return false">리포트에서 보기 →</a></div>`;
}

// ---- poll the live event stream until run-end / status done ----
function pollEvents() {
  if (!runId) return;
  if (pollTimer) clearTimeout(pollTimer);
  fetch("/api/runs/" + runId + "/events").then(r => r.json()).then(j => {
    runEvents = j.events || [];
    runStatus = j.status || runStatus;
    const ended = runEvents.some(e => e.kind === "run-end") || (runStatus !== "running");
    if (stage === "report") drawReport();
    if (stage === "run") $("run-live").innerHTML = liveProgressHtml();
    if (!ended) pollTimer = setTimeout(pollEvents, 1500);
    else { runStatus = runStatus === "running" ? "done" : runStatus; if (stage === "report") drawReport(); }
  }).catch(() => { pollTimer = setTimeout(pollEvents, 2000); });
}

// ================= ④ 리포트 (Report) =================
function drawReport() {
  els("#report-subtabs button").forEach(b => b.classList.toggle("on", b.dataset.r === reportSub));
  if (!runId) {
    $("report-main").innerHTML = '<p class="empty">아직 실행이 없습니다 — ③ 실행에서 <b>실행 ▶</b>을 누르세요.</p>';
    $("report-side").innerHTML = runRecordsPlaceholder();
    loadRunRecords();
    return;
  }
  if (reportSub === "r1") reportR1();
  else if (reportSub === "r2") reportR2();
  else if (reportSub === "r3") reportR3();
  else reportR4();
  $("report-side").innerHTML = runRecordsPlaceholder();
  loadRunRecords();
}

// derive live lifecycle state from events: queued/running/done/fail
function lifecycleStates() {
  const st = {};
  (lastPlan && lastPlan.plan && lastPlan.plan.leaf_set || []).forEach(l => st[l] = "queued");
  runEvents.forEach(e => {
    if (e.kind === "run-meta") (e.runnable || []).forEach(l => { if (!st[l]) st[l] = "queued"; });
    if (e.kind === "lifecycle-start") st[e.lifecycle] = "running";
    if (e.kind === "lifecycle-end") st[e.lifecycle] =
      e.status === "passed" ? "done" : e.status === "skipped" ? "skip" : "fail";
  });
  return st;
}

// R1 진행 — DAG/wave graph colored by live lifecycle state + wave progress
function reportR1() {
  const st = lifecycleStates();
  const waves = (lastPlan && lastPlan.plan && lastPlan.plan.waves) || [];
  // map lifecycle state onto its source resource node(s) for the swimlane color
  const lcOfNode = {};
  V.allIds().forEach(id => { if (N[id].lifecycle) (lcOfNode[N[id].lifecycle] = lcOfNode[N[id].lifecycle] || []).push(id); });
  const nodeState = id => {
    const lc = N[id] && N[id].lifecycle;
    return lc && st[lc] ? st[lc] : null;
  };
  const closure = V.closure([...targets]);
  const COL = { queued: "#1c2a3a", running: "#0f2033", done: "#0f2419", fail: "#2a1311", skip: "#1c2a3a" };
  const STK = { queued: "#5b7088", running: "#3b82f6", done: "#2fb673", fail: "#e0574c", skip: "#6b8099" };
  const BDG = { queued: "", running: "⏳", done: "✓", fail: "✕", skip: "–" };
  drawSwimlane($("report-main-svg-host") || makeR1Host(), closure, {
    targets,
    colorOf: id => COL[nodeState(id)] || null,
    strokeOf: id => STK[nodeState(id)] || null,
    badgeOf: id => BDG[nodeState(id)] || ""
  });
  // wave progress under the canvas
  const counts = k => Object.values(st).filter(v => v === k).length;
  const total = Object.keys(st).length;
  const waveLines = waves.map((w, i) => {
    const lcs = w.lifecycles;
    const done = lcs.filter(l => st[l] === "done").length;
    const running = lcs.some(l => st[l] === "running");
    const pct = lcs.length ? Math.round(100 * done / lcs.length) : 0;
    return `<div class="kv"><span>${running ? "⏳" : done === lcs.length && lcs.length ? "✓" : "·"}
      웨이브 ${i} <span class="muted small">${esc(w.kind)} · ${lcs.length}개</span></span>
      <b>${done}/${lcs.length}</b></div>
      <div class="pbar"><i style="width:${pct}%"></i></div>`;
  }).join("");
  $("report-main-prog").innerHTML = `
    <div class="kpi">
      <div class="s"><b>${counts("done")}/${total}</b><span>완료</span></div>
      <div class="s"><b style="color:var(--run)">${counts("running")}</b><span>실행중</span></div>
      <div class="s"><b style="color:var(--fail)">${counts("fail")}</b><span>fail</span></div>
      <div class="s"><b>${esc(runStatus)}</b><span>상태</span></div>
    </div>
    <h3>웨이브 진행 (계획 순서)</h3>${waveLines || '<p class="muted small">웨이브 정보 없음</p>'}`;
}
function makeR1Host() {
  $("report-main").innerHTML = `<h2>R1 진행 <span class="muted small">· DAG 노드 = lifecycle 라이브 상태</span></h2>
    <div class="legend">${legend([["#5b7088", "queued"], ["#3b82f6", "running"], ["#2fb673", "done"], ["#e0574c", "fail"]])}</div>
    <div class="svgbox"><svg id="report-main-svg-host"></svg></div>
    <div id="report-main-prog" style="margin-top:8px"></div>`;
  return $("report-main-svg-host");
}

// R2 리소스 — per-resource rows from resource-tracked / resource-deleted
function reportR2() {
  const rows = {};  // resource_id → {type, lifecycle, created, deleted}
  // per-lifecycle: did its verify (read) steps pass? (api-level ok)
  const lcVerifyOk = {};
  runEvents.forEach(e => {
    if (e.kind === "resource-tracked")
      rows[e.resource_id] = { id: e.resource_id, type: e.resource_type, lifecycle: e.lifecycle,
        path: e.path, created: true, deleted: false, tested: false };
    if (e.kind === "resource-deleted") {
      // mark the most recent same-type+lifecycle row deleted (synthetic teardown)
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
      <tr><th>type</th><th>resource_id</th><th>lifecycle</th><th>생성</th><th>테스트</th><th>삭제</th></tr>
      ${body}</table>`;
}

// R3 API — api-first table of step-start/step-end (method+path, 결과, 응답시간)
function reportR3() {
  const calls = [];  // {lifecycle, step, method, path, status, category, ms}
  const open = {};
  runEvents.forEach(e => {
    if (e.kind === "step-start") {
      const k = e.lifecycle + "|" + e.step;
      open[k] = { lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path,
        status: null, category: "run", ms: null };
      calls.push(open[k]);
    }
    if (e.kind === "step-end") {
      const k = e.lifecycle + "|" + e.step;
      const c = open[k] || { lifecycle: e.lifecycle, step: e.step, method: e.method, path: e.path };
      c.status = e.status; c.category = e.category; c.ms = e.elapsed_ms;
      if (!open[k]) calls.push(c);
    }
  });
  // group by lifecycle but api-first within
  const byLc = {};
  calls.forEach(c => (byLc[c.lifecycle] = byLc[c.lifecycle] || []).push(c));
  const okN = calls.filter(c => c.category === "ok").length;
  const softN = calls.filter(c => c.category === "soft").length;
  const failN = calls.filter(c => c.category === "fail").length;
  const body = Object.keys(byLc).sort().map(lc => {
    return `<tr class="lc-head"><td colspan="4">${esc(lc)} <span class="muted small">${byLc[lc].length} api</span></td></tr>` +
      byLc[lc].map(c => `<tr>
        <td><span class="mtag ${esc(c.method || "")}">${esc(c.method || "")}</span> <code>${esc(c.path || "")}</code></td>
        <td>${badge(c.category)}</td>
        <td class="muted">${c.status != null ? esc(c.status) : "—"}</td>
        <td class="muted">${c.ms != null ? c.ms + " ms" : (c.category === "run" ? "⏳" : "—")}</td>
      </tr>`).join("");
  }).join("");
  $("report-main").innerHTML = `<h2>R3 API <span class="muted small">· api 단위 (대상 · 결과 · 응답시간)</span></h2>
    <div class="kpi">
      <div class="s"><b>${calls.length}</b><span>api 호출</span></div>
      <div class="s"><b style="color:var(--ok)">${okN}</b><span>ok</span></div>
      <div class="s"><b style="color:var(--soft)">${softN}</b><span>soft</span></div>
      <div class="s"><b style="color:var(--fail)">${failN}</b><span>fail</span></div>
    </div>
    <div class="scroll" style="max-height:520px;margin-top:8px"><table class="tbl">
      <tr><th>method · path (대상)</th><th>결과</th><th>status</th><th>응답시간</th></tr>
      ${body || '<tr><td colspan="4" class="empty">api 이벤트 없음</td></tr>'}</table></div>`;
}

// R4 로그 — raw run log + cleanup/verify controls
function reportR4() {
  $("report-main").innerHTML = `<h2>R4 로그 <span class="muted small">· 원시 실행 로그</span></h2>
    <div class="run-ctl">
      <button class="btn warn" id="btn-cleanup" title="우리(owner)가 만든 자원을 강제 삭제 (reconciler, TTL 무시).">🧹 강제 클린업</button>
      <button class="btn ghost" id="btn-verify" title="삭제 없이 남은 우리 자원 수 확인 (read-only).">🔍 클린업 확인</button>
      <button class="btn ghost" id="btn-reflog" style="font-size:11px">↻ 로그 새로고침</button>
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
// cleanup/verify runs have no event stream — poll the record's log/status instead
function pollLogRun() {
  if (!runId) return;
  fetch("/api/runs/" + runId).then(r => r.json()).then(j => {
    runStatus = j.status || runStatus;
    if (stage === "report") { if (reportSub === "r4") loadLog(); loadRunRecords(); }
    if (j.status === "running") setTimeout(pollLogRun, 1500);
  }).catch(() => setTimeout(pollLogRun, 2000));
}

// ---- run records list (right side of report) ----
function runRecordsPlaceholder() {
  return `<h2>실행 기록 <span class="muted small">· /api/runs</span></h2>
    <div id="runs-list"><p class="muted small">로딩…</p></div>`;
}
function loadRunRecords() {
  fetch("/api/runs").then(r => r.json()).then(j => {
    const runs = j.runs || [];
    const host = $("runs-list"); if (!host) return;
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
    els("#runs-list .runrow").forEach(row => row.onclick = () => {
      runId = row.dataset.id; runEvents = []; runStatus = "running";
      // reopen: pull events (simulate/live) and log
      fetch("/api/runs/" + runId + "/events").then(r => r.json()).then(j2 => {
        runEvents = j2.events || []; runStatus = j2.status || "done";
        const running = runStatus === "running";
        if (running) pollEvents();
        drawReport();
      }).catch(() => drawReport());
    });
  }).catch(() => { const host = $("runs-list"); if (host) host.innerHTML = '<p class="muted small">서버 연결 실패</p>'; });
}

// ================= shared: selection payload + canvas + helpers =================
// the server resolves node_ids → source lifecycles; we send the TARGET resources
// (it pulls the closure server-side via dag_planner). Sending node_ids keeps the
// "selection = resources" contract; services/categories are equivalent shortcuts.
function selectionPayload() { return { node_ids: [...targets] }; }

// layered swimlane DAG (depth = creation order, band = category). A self-contained
// renderer (viz.js layout() gives x/y/depth; we re-shelf into category bands and
// add the colorOf/strokeOf/badgeOf overlay hooks the report stages need).
function drawSwimlane(svg, setIds, opt) {
  opt = opt || {};
  if (!setIds.size) { svg.innerHTML = ""; return; }
  const dep = V.depths(setIds, opt.choices);
  const bands = [...new Set([...setIds].map(id => N[id].category))]
    .sort((a, b) => (opt.targets && [...setIds].some(id => opt.targets.has(id) && N[id].category === a) ? 0 : 1)
      - (opt.targets && [...setIds].some(id => opt.targets.has(id) && N[id].category === b) ? 0 : 1)
      || (a < b ? -1 : 1));
  const colGap = 196, rowGap = 56, bw = 162, bh = 42, padX = 116, padY = 16, bandPad = 18;
  const byId = {}; let y = padY, maxCol = 0;
  bands.forEach(cat => {
    const cols = {};
    [...setIds].filter(id => N[id].category === cat).forEach(id => { (cols[dep[id]] = cols[dep[id]] || []).push(id); });
    const rows = Math.max(1, ...Object.values(cols).map(a => a.length));
    const bandTop = y, bandH = rows * rowGap + bandPad;
    Object.keys(cols).map(Number).forEach(c => {
      maxCol = Math.max(maxCol, c);
      cols[c].sort().forEach((id, i) => {
        byId[id] = { id, x: padX + c * colGap, y: bandTop + bandPad / 2 + i * rowGap, w: bw, h: bh, band: cat, bandTop, bandH };
      });
    });
    y += bandH + 10;
  });
  const W = padX + maxCol * colGap + bw + 40, H = y + 10;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H);
  let s = `<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 z" fill="#5b7088"/></marker></defs>`;
  // band backgrounds + labels
  const drawn = new Set();
  Object.values(byId).forEach(p => {
    if (drawn.has(p.band)) return; drawn.add(p.band);
    s += `<rect x="2" y="${p.bandTop}" width="${W - 4}" height="${p.bandH}" rx="9" fill="#11202f" stroke="#27384b"/>`;
    s += `<text x="11" y="${p.bandTop + 17}" font-size="11.5" font-weight="700" fill="#6b8099">${esc(p.band)}</text>`;
  });
  // edges
  [...setIds].forEach(id => V.depRefs(id, opt.choices).forEach(r => {
    const a = byId[r], b = byId[id]; if (!a || !b) return;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    s += `<path d="M${x1},${y1} C${x1 + 50},${y1} ${x2 - 50},${y2} ${x2},${y2}" fill="none" stroke="#33485e" stroke-width="1.3" marker-end="url(#ar)"/>`;
  }));
  // nodes
  Object.values(byId).forEach(p => {
    const n = N[p.id];
    const isT = opt.targets && opt.targets.has(p.id);
    const lc = hasLifecycle(p.id);
    const fill = (opt.colorOf && opt.colorOf(p.id)) || (isT ? "#13314f" : lc ? "#1c2a3a" : "#0b121c");
    const stroke = (opt.strokeOf && opt.strokeOf(p.id)) || (isT ? "#3b82f6" : lc ? V.provColor(n.provenance) : "#33485e");
    const badge = opt.badgeOf ? opt.badgeOf(p.id) : "";
    const sw = isT ? 2.4 : 1.4;
    const op = lc ? 1 : 0.5;
    s += `<g style="cursor:${opt.onClick && lc ? "pointer" : "default"}" data-id="${esc(p.id)}" opacity="${op}">
      <title>${esc(p.id)} — ${esc(n.service)}\nprovenance ${esc(n.provenance)}\ndepth ${dep[p.id]}${lc ? "\nlifecycle: " + esc(n.lifecycle) : "\n(dep-only — lifecycle 없음)"}</title>
      <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>
      <text x="${p.x + 9}" y="${p.y + 17}" font-size="12" font-weight="600" fill="#e7eef6">${n.heavy ? "🜂 " : ""}${esc(p.id)}</text>
      <text x="${p.x + 9}" y="${p.y + 32}" font-size="10" fill="#90a4ba">${esc(n.service.split("/").pop())}${n.quota ? " ⛔" + esc(n.quota) : ""}</text>
      ${badge ? `<text x="${p.x + p.w - 9}" y="${p.y + 16}" font-size="12" text-anchor="end">${esc(badge)}</text>` : ""}
    </g>`;
  });
  svg.innerHTML = s;
  if (opt.onClick) els("g[data-id]", svg).forEach(g => g.onclick = () => opt.onClick(g.dataset.id));
}

function legend(items) {
  return items.map(i => `<span><i style="background:${i[0]}"></i>${esc(i[1])}</span>`).join("");
}
function badge(cat) {
  const m = { ok: "ok", soft: "soft", fail: "fail", run: "run" };
  const c = m[cat] || "queued";
  return `<span class="bdg ${c}">${esc(cat || "—")}</span>`;
}

})();
