/* controlplane/v2/static/runs_plan.js — Plan(⑥) test-planning 경험, console2
 * 패리티 이식 (오너 판정 2026-07-10: "선택하고 DAG 확인하는 게 이 플랫폼의
 * 핵심인데 v2에서 다 없어졌다").
 *
 * 원형: console2/assets/console2.js 의 drawSvcTree()/svcState()/setSvc()/
 * selectionPayload() (선택 트리) + makeDagScene()/refreshGraph() (조합 DAG,
 * window.ResourceGraph.scene 계약 — /testing/console/assets/resource_graph.js
 * 를 그대로 로드해 재사용한다. 복붙하지 않는다: 씬 렌더러는 이 페이지가 아니라
 * console2가 원 소유자다) + preflightRun()/pfRender() (pre-flight 모달).
 *
 * v2 경계: 이 스크립트는 어떤 실행도 발사하지 않는다. [Review & run] 은
 * plan+capacity+preflight 3-fetch pre-flight 모달까지만 열고, 그 안의
 * [Run live]는 오너 승인(2026-07-10)으로 활성화됨 — pre-flight 3-fetch를
 * 통과한 모달에서만 노출되며, heavy(과금) 선택 시 확인 체크박스가 잠금을
 * 푼다. 이전 비활성 정책 설명(참고용):
 * "Continue in legacy console" 딥링크로 legacy 콘솔(console2, /testing)에서
 * 같은 선택으로 실행한다. postRun()의 실제 POST /api/run 호출은 주석으로만
 * 남겨둔다(활성화 결정 시 1줄 해제).
 */
(function () {
  "use strict";

  const $ = id => document.getElementById(id);
  const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const els = (q, r) => [...(r || document).querySelectorAll(q)];

  const root = $("rp-root");
  if (!root) return;   // this page has no Plan panel — nothing to wire

  const prefillService = (root.dataset.prefillService || "").trim();

  // Gate status 패널과 같은 출처(runs_data.get_gate_status()) — 재계산하지
  // 않는다. 서버가 read-only로 기동됐다면 전부 false로 성립.
  let gate = { mutations: false, heavy: false, destructive: false };
  try {
    const raw = $("rp-gate-data");
    if (raw) gate = Object.assign(gate, JSON.parse(raw.textContent || "{}"));
  } catch (e) { /* corrupt/absent -> conservative false defaults */ }

  // ---- state ----
  let MODEL = null;            // raw /api/model payload
  let N = {};                  // MODEL.nodes (by id)
  const targets = new Set();   // selected resource node ids
  let collapsed = null;        // Set of collapsed category names (lazy init)
  let dagScene = null;         // window.ResourceGraph.scene() controller
  let selTimer = null;         // 180ms selection-change debounce (graph + estimate)

  const TEMPLATE = `
    <div class="rp-cols">
      <div class="rp-tree-panel">
        <div class="rp-tree-controls">
          <input type="text" id="rp-search" placeholder="서비스 검색…" autocomplete="off">
          <button type="button" class="rp-allbtn" id="rp-toggle-all">전체 선택</button>
        </div>
        <div class="rp-tree" id="rp-tree"></div>
        <div class="rp-sel-readout" id="rp-sel-readout"></div>
      </div>
      <div class="rp-dag-panel">
        <div class="rp-toolbar">
          <div class="rp-tgroup" id="rp-gran-group">
            <button type="button" data-gran="category">카테고리</button>
            <button type="button" data-gran="service">서비스</button>
            <button type="button" data-gran="resource">리소스</button>
          </div>
          <button type="button" class="rp-allbtn" id="rp-collapse-all">전체 접기</button>
          <button type="button" class="rp-allbtn" id="rp-expand-all">전체 펼치기</button>
          <span class="rp-gran-note" id="rp-gran-note"></span>
          <span class="rp-statchip" id="rp-stat"></span>
        </div>
        <div class="rp-stage-wrap" id="rp-stage-wrap">
          <div class="rp-stage" id="rp-stage">
            <svg id="rp-dag-svg"></svg>
            <div class="rp-hint-pill" id="rp-dag-hint"></div>
            <div class="rp-zoomctl">
              <button type="button" id="rp-zoom-in" title="확대">+</button>
              <button type="button" id="rp-zoom-out" title="축소">−</button>
              <button type="button" class="fit" id="rp-zoom-fit" title="화면에 맞춤">맞춤</button>
            </div>
          </div>
        </div>
        <div class="rp-legend" id="rp-legend"></div>
      </div>
    </div>
    <div class="rp-estimate" id="rp-estimate">
      <span class="empty-state">선택된 리소스가 없습니다 — 왼쪽에서 서비스를 선택하면 견적이 표시됩니다.</span>
    </div>
    <div class="rp-review-row">
      <button type="button" class="rp-review-btn" id="rp-review-btn" disabled>Review &amp; run →</button>
      <span class="panel-note" id="rp-review-note">리소스를 선택하면 사전 점검(pre-flight)으로 진행할 수 있습니다.</span>
    </div>
    <div class="rp-scrim" id="rp-scrim"></div>
    <div class="rp-modal" id="rp-modal" role="dialog" aria-modal="true" aria-labelledby="rp-modal-title">
      <div class="rp-mh"><h3 id="rp-modal-title">⚠ Pre-flight — blast radius</h3></div>
      <div class="rp-mbody" id="rp-mbody"></div>
      <div class="rp-mfoot" id="rp-mfoot"></div>
    </div>`;

  // ---- bootstrap ----
  fetch("/api/model").then(r => r.json()).then(m => {
    if (m.error) throw new Error(m.error);
    MODEL = m; N = m.nodes;
    init();
  }).catch(e => fatal("모델 로딩 실패 — 백엔드(console_api)가 응답하지 않습니다: " + esc(e.message)));

  function fatal(msg) {
    root.innerHTML = '<p class="empty-state">' + msg + "</p>";
  }

  function init() {
    root.innerHTML = TEMPLATE;
    wireStatic();
    restoreSelection();
    deepLinkService();      // ?service= 프리필 — 저장된 선택을 덮어쓴다 (console2 원형과 동일 우선순위)
    selectionChanged();
  }

  // ---- selection persistence (탭/새로고침 생존 — console2 c2.selection 패턴) ----
  const SEL_KEY = "rp.selection.v1";
  function restoreSelection() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(SEL_KEY) || "[]");
      if (Array.isArray(saved)) saved.forEach(id => { if (N[id] && N[id].lifecycle) targets.add(id); });
    } catch (e) { /* corrupt/absent -> empty default */ }
  }
  function persistSelection() {
    try { sessionStorage.setItem(SEL_KEY, JSON.stringify([...targets])); } catch (e) { /* quota */ }
  }

  // ---- ?service=<cat>/<svc> deep-link (services_list/service_detail 딥링크 승계) ----
  function deepLinkService() {
    if (!prefillService) return false;
    const q = prefillService.trim().toLowerCase();
    if (!q) return false;
    const slugs = [...new Set(Object.values(N).map(n => n.service).filter(Boolean))];
    const svc = slugs.find(s => s.toLowerCase() === q)
             || slugs.find(s => s.toLowerCase().split("/").pop() === q.split("/").pop());
    if (!svc || !svcSelectable(svc).length) return false;
    targets.clear();
    setSvc(svc, true);
    return true;
  }

  // ---- selection helpers (console2 svcState/setSvc/svcSelectable port) ----
  const hasLifecycle = id => !!(N[id] && N[id].lifecycle);
  const svcNodes = svc => Object.keys(N).filter(id => N[id].service === svc);
  const svcSelectable = svc => svcNodes(svc).filter(hasLifecycle);
  const shortName = svc => (svc || "").split("/").pop();
  const catName = c => (window.ResourceGraph && window.ResourceGraph.catLabel) ? window.ResourceGraph.catLabel(c) : c;

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
  function toggleAllServices() {
    const svcs = allSelectableServices();
    const allOn = svcs.length && svcs.every(s => svcState(s) === "on");
    svcs.forEach(s => setSvc(s, !allOn));
    selectionChanged();
  }

  // the server resolves node_ids/services -> lifecycle closure (core.registry-style
  // ownership of that logic lives in tools.console2_server._resolve_lifecycle_ids /
  // _graph_targets) — this client NEVER re-implements that expansion, it only ever
  // sends the raw selection (함정 1).
  function selectionPayload() {
    const services = allSelectableServices().filter(s => svcState(s) === "on");
    return { node_ids: [...targets], services };
  }

  // ---- tree (category ▸ service, console2 drawSvcTree port) ----
  function initCollapse() {
    if (collapsed) return;
    collapsed = new Set();
    const cats = categoryMap();
    Object.keys(cats).forEach(cat => {
      const hasSel = cats[cat].some(s => svcState(s) !== "off" && svcState(s) !== "none");
      if (!hasSel) collapsed.add(cat);
    });
  }

  function drawTree() {
    initCollapse();
    const q = ($("rp-search").value || "").toLowerCase();
    const cats = categoryMap();
    let h = "";
    Object.keys(cats).forEach(cat => {
      const svcs = cats[cat].filter(s => !q || (shortName(s) + " " + s).toLowerCase().includes(q));
      if (!svcs.length) return;
      const selectableSvcs = svcs.filter(s => svcSelectable(s).length);
      const onCount = selectableSvcs.filter(s => svcState(s) !== "off").length;
      const catAllOn = selectableSvcs.length && selectableSvcs.every(s => svcState(s) === "on");
      const catPartial = !catAllOn && onCount > 0;
      const open = q ? true : !collapsed.has(cat);
      const catCls = catAllOn ? "on" : catPartial ? "partial" : "";
      h += `<div class="rp-cat">
        <div class="rp-row rp-cat-row ${catCls}" data-cat="${esc(cat)}">
          <span class="rp-car">${open ? "▾" : "▸"}</span>
          <span class="rp-chk" data-catchk="${esc(cat)}" title="카테고리 전체 선택/해제">${catAllOn ? "✓" : catPartial ? "◐" : ""}</span>
          <span class="rp-name">${esc(catName(cat))}</span>
          <span class="rp-meta">${onCount}/${svcs.length}</span>
        </div>`;
      if (open) {
        h += `<div class="rp-svcs">`;
        svcs.forEach(svc => {
          const sel = svcSelectable(svc);
          const st = svcState(svc);
          const quota = svcNodes(svc).some(id => N[id].quota);
          const onN = sel.filter(id => targets.has(id)).length;
          const cls = st === "on" ? "on" : st === "partial" ? "partial" : "";
          const noLc = !sel.length;
          const fracTxt = !sel.length ? "—" : st === "partial" ? `${onN}/${sel.length}` : `${sel.length}`;
          h += `<div class="rp-row rp-svc-row ${cls} ${noLc ? "nolc" : ""}" data-svc="${esc(svc)}"
              title="${esc(svc)}${noLc ? " — 생애주기 없음(의존전용)" : " — 클릭하면 서비스 전체 선택"}">
              <span class="rp-chk">${st === "on" ? "✓" : st === "partial" ? "◐" : ""}</span>
              <span class="rp-name">${esc(shortName(svc))}${quota ? ' <span class="rp-glyph" title="quota 제약">⛔</span>' : ""}</span>
              <span class="rp-count">${fracTxt}</span>
              ${noLc ? '<span class="rp-dep">의존전용</span>' : ""}
            </div>`;
        });
        h += `</div>`;
      }
      h += `</div>`;
    });
    $("rp-tree").innerHTML = h || '<p class="rp-empty">검색 결과 없음</p>';

    els("#rp-tree .rp-cat-row[data-cat]").forEach(row => row.onclick = ev => {
      if (ev.target.closest("[data-catchk]")) return;
      const cat = row.dataset.cat;
      collapsed.has(cat) ? collapsed.delete(cat) : collapsed.add(cat);
      drawTree();
    });
    els("#rp-tree [data-catchk]").forEach(chk => chk.onclick = ev => {
      ev.stopPropagation();
      const cat = chk.dataset.catchk;
      const svcs = (categoryMap()[cat] || []).filter(s => svcSelectable(s).length);
      const allOn = svcs.length && svcs.every(s => svcState(s) === "on");
      svcs.forEach(s => setSvc(s, !allOn));
      selectionChanged();
    });
    els("#rp-tree .rp-svc-row[data-svc]").forEach(row => row.onclick = () => {
      const svc = row.dataset.svc;
      if (!svcSelectable(svc).length) return;   // 의존전용 row — not selectable
      setSvc(svc, svcState(svc) !== "on");
      selectionChanged();
    });
    selReadout();
  }

  function selReadout() {
    const svcs = new Set([...targets].map(id => N[id].service));
    $("rp-sel-readout").innerHTML = targets.size
      ? `선택: <b>${svcs.size}</b> 서비스 · <b>${targets.size}</b> 리소스`
      : '<span class="rp-empty" style="padding:0">선택 없음 — 위에서 서비스를 선택하세요.</span>';
    const svcsAll = allSelectableServices();
    const allOn = svcsAll.length && svcsAll.every(s => svcState(s) === "on");
    const btn = $("rp-toggle-all");
    if (btn) { btn.classList.toggle("on", !!allOn); btn.textContent = allOn ? "전체 해제" : "전체 선택"; }
  }

  // any selection change: re-render tree + readout + review-button state, then
  // (debounced) refresh the DAG + estimate bar.
  function selectionChanged() {
    persistSelection();
    drawTree();
    updateReviewButton();
    scheduleRefresh();
  }

  function scheduleRefresh() {
    if (selTimer) clearTimeout(selTimer);
    selTimer = setTimeout(() => { fetchGraph(); fetchEstimate(); }, 180);
  }

  // ---- 우: 조합 DAG (window.ResourceGraph.scene 계약 — /api/graph 이식) ----
  function fetchGraph() {
    const body = selectionPayload();
    fetch("/api/graph", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(r => r.json()).then(g => {
        if (g.error) { renderGraphError(g.error); return; }
        renderGraph(g);
      }).catch(e => renderGraphError(e.message));
  }
  function renderGraphError(msg) {
    $("rp-dag-svg").innerHTML = `<text x="12" y="24" fill="#cf222e">graph: ${esc(msg)}</text>`;
  }
  function renderGraph(g) {
    const svg = $("rp-dag-svg");
    if (!g.nodes.length) {
      if (dagScene) { dagScene.destroy(); dagScene = null; }
      svg.removeAttribute("style");
      svg.innerHTML = '<text x="12" y="24" fill="#64748b">리소스를 선택하면 조합 배포 DAG가 생성 순서대로 표시됩니다.</text>';
      svg.setAttribute("viewBox", "0 0 420 40"); svg.setAttribute("width", 420); svg.setAttribute("height", 40);
      $("rp-dag-hint").innerHTML = ""; $("rp-stat").innerHTML = "";
      $("rp-gran-note").textContent = ""; $("rp-legend").innerHTML = "";
      return;
    }
    $("rp-legend").innerHTML = legend([
      ["#e6effd", "★ 대상"], ["#fffaf0", "■ 공유(dedup)"], ["#f3eefc", "↓ 의존"],
    ]);
    if (!window.ResourceGraph || !window.ResourceGraph.scene) {
      renderGraphError("resource_graph.js 렌더러를 불러오지 못했습니다 (/testing/console/assets/resource_graph.js)");
      return;
    }
    // scene() 계약: 최초 1회만 만들고 start(); 이후로는 같은 컨트롤러의
    // update(g) 만 호출한다 — 매번 새로 만들면 zoom/펼침 상태가 리셋된다(함정 3).
    if (!dagScene) {
      dagScene = window.ResourceGraph.scene(svg, $("rp-stage"), g, {
        hint: $("rp-dag-hint"), stat: $("rp-stat"), granNote: $("rp-gran-note"),
        isSelectable: id => hasLifecycle(id),
        onToggleTarget: id => {                 // ＋/✓ corner = toggle this target
          if (!hasLifecycle(id)) return;
          targets.has(id) ? targets.delete(id) : targets.add(id);
          selectionChanged();
        },
        onFocus: () => { /* order table not carried over in this iteration */ },
      });
      dagScene.start();
    } else {
      dagScene.update(g);
    }
    syncGranButtons();
  }
  function syncGranButtons() {
    if (!dagScene) return;
    els("#rp-gran-group button").forEach(b => b.classList.toggle("on", b.dataset.gran === dagScene.gran));
  }
  function legend(items) {
    return items.map(i => `<span><i style="background:${i[0]}"></i>${esc(i[1])}</span>`).join("");
  }

  // ---- 하단: Estimate 바 (/api/plan + /api/capacity) ----
  function fetchEstimate() {
    const body = selectionPayload();
    if (!body.node_ids.length && !body.services.length) { renderEstimateEmpty(); return; }
    Promise.all([
      fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json()),
      fetch("/api/capacity").then(r => r.json()),
    ]).then(([plan, capacity]) => {
      if (plan.error || capacity.error) { renderEstimateError(plan.error || capacity.error); return; }
      renderEstimate(plan, capacity);
    }).catch(e => renderEstimateError(e.message));
  }
  function renderEstimateEmpty() {
    $("rp-estimate").innerHTML = '<span class="empty-state">선택된 리소스가 없습니다 — 왼쪽에서 서비스를 선택하면 견적이 표시됩니다.</span>';
  }
  function renderEstimateError(msg) {
    $("rp-estimate").innerHTML = `<span class="rp-warn">견적 조회 실패: ${esc(msg)}</span>`;
  }
  function renderEstimate(plan, capacity) {
    const nLc = plan.runnable ? plan.runnable.length : (plan.lifecycle_ids || []).length;
    const peak = plan.peak_vpcs || 0;
    const headroom = capacity.headroom != null ? capacity.headroom : 0;
    let creates = 0, deletes = 0, dur = 0, measured = 0, total = 0, heavyN = 0;
    Object.values(plan.preview || {}).forEach(p => {
      total++;
      creates += p.est_creates || 0;
      deletes += p.est_deletes || 0;
      if (p.duration_s != null) { dur += p.duration_s; measured++; }
      if (p.heavy) heavyN++;
    });
    // NOTE: /api/plan은 라이프사이클별 실측 평균(duration_s)만 준다 — 병렬
    // makespan p50~p90 견적은 /api/preflight 전용 필드(est.p50_s/p90_s)라 여기
    // 상시 바에서는 순차 합산으로 근사 표시하고, 실제 p50~p90은 pre-flight
    // 모달에서 보여준다(불확실성 — 최종 보고 참고).
    const etaTxt = measured
      ? "~" + fmtDur(dur) + (measured < total ? ` <span class="panel-note">(미측정 ${total - measured})</span>` : "")
      : '<span class="panel-note">미측정</span>';
    const over = peak > headroom;
    $("rp-estimate").innerHTML =
      `<span>Lifecycles <b>${nLc}</b></span>` +
      `<span>생성 ~<b>${creates}</b> · 삭제 ~<b>${deletes}</b></span>` +
      `<span>ETA(측정 평균 순차합산) ${etaTxt}</span>` +
      `<span>Peak VPC <b>${peak}</b> vs 여유 <b>${headroom}</b>${over ? ' <span class="rp-warn">⚠ 초과 — 대기 큐</span>' : ""}</span>` +
      (heavyN ? `<span class="rp-amber">⚠ 과금 라이프사이클 ${heavyN}개</span>` : "");
  }
  function fmtDur(s) {
    if (s == null) return "미측정";
    if (s < 90) return Math.round(s) + "초";
    if (s < 5400) return (s / 60).toFixed(1) + "분";
    return (s / 3600).toFixed(1) + "시간";
  }

  // ---- Review & run -> pre-flight 모달 ----
  function updateReviewButton() {
    const btn = $("rp-review-btn"), note = $("rp-review-note");
    if (!btn) return;
    // read-only 기동(mutations OFF) = 열람 모드 — 사전 점검 진입 자체를 잠근다
    if (!gate.mutations) {
      btn.disabled = true;
      btn.setAttribute("aria-disabled", "true");
      note.textContent = "이 서버는 열람용(read-only)으로 기동되어 실행 준비가 비활성화되어 있습니다.";
      return;
    }
    btn.disabled = targets.size === 0;
    note.textContent = targets.size
      ? "선택을 확정하고 사전 점검(plan · capacity · preflight)으로 진행합니다."
      : "리소스를 선택하면 사전 점검(pre-flight)으로 진행할 수 있습니다.";
  }

  function pfOpen() {
    $("rp-modal").classList.add("open");
    $("rp-scrim").classList.add("open");
  }
  function pfClose() {
    $("rp-modal").classList.remove("open");
    $("rp-scrim").classList.remove("open");
  }

  function openPreflight() {
    if (!targets.size) return;
    const sel = selectionPayload();
    pfOpen();
    $("rp-mbody").innerHTML = '<p class="empty-state">사전 점검 중… (plan + capacity + preflight)</p>';
    $("rp-mfoot").innerHTML = "";
    // 함정 2: plan+capacity+preflight 3-fetch를 거치지 않은 발사 UI는 만들지
    // 않는다 — 이 모달이 그 유일한 경로다(그리고 v2에서는 여기까지만 연다).
    Promise.all([
      fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sel) }).then(r => r.json()),
      fetch("/api/capacity").then(r => r.json()),
      fetch("/api/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sel) })
        .then(r => (r.ok ? r.json() : null)).catch(() => null),
    ]).then(([plan, capacity, pf]) => {
      plan = plan || {}; capacity = capacity || {};
      if (plan.error || capacity.error || capacity.headroom == null) {
        pfFail(plan.error || capacity.error || "capacity 응답이 불완전합니다 (headroom 없음)", sel);
        return;
      }
      pfRender(plan, capacity, (pf && !pf.error) ? pf : null, sel);
    }).catch(e => pfFail(e.message, sel));
  }

  function pfFail(msg, sel) {
    $("rp-mbody").innerHTML =
      '<p><b style="color:var(--bad-ink)">사전 점검(plan/capacity) 실패 — 다음 단계로 진행할 수 없습니다.</b></p>' +
      `<p class="panel-note">${esc(msg || "")}</p>` +
      '<p class="panel-note">서버가 계획/용량을 답하지 못하면 blast radius를 알 수 없어 실행 판단을 진행하지 않습니다 (우회 없음).</p>';
    $("rp-mfoot").innerHTML =
      '<button type="button" class="rp-allbtn" id="rp-pf-retry">↻ 다시 점검</button>' +
      '<button type="button" class="rp-allbtn" id="rp-pf-cancel">닫기</button>';
    $("rp-pf-retry").onclick = () => openPreflight();
    $("rp-pf-cancel").onclick = pfClose;
  }

  function legacyDeepLink(sel) {
    // console2(legacy)의 ?service= 프리필은 서비스 1개만 받는다 — 여러 서비스가
    // 선택된 경우 최선은 legacy 콘솔에서 수동 재선택(plain /testing)뿐이다.
    const svcs = sel.services || [];
    if (svcs.length === 1) return "/testing?service=" + encodeURIComponent(shortName(svcs[0]));
    return "/testing";
  }

  function pfRender(plan, capacity, pf, sel) {
    const nLc = plan.runnable ? plan.runnable.length : (plan.lifecycle_ids || []).length;
    const peak = plan.peak_vpcs || 0;
    const headroom = capacity.headroom != null ? capacity.headroom : 0;
    const bySvc = {}; const heavyIds = [];
    let tCreates = 0, tDeletes = 0, tDur = 0, tMeasured = 0, tN = 0;
    Object.keys(plan.preview || {}).sort().forEach(lid => {
      const p = plan.preview[lid] || {};
      const svc = shortName(p.service || "?");
      const a = bySvc[svc] = bySvc[svc] || { n: 0, creates: 0, deletes: 0, dur: 0, measured: 0, heavy: 0 };
      a.n++; tN++;
      a.creates += p.est_creates || 0; tCreates += p.est_creates || 0;
      a.deletes += p.est_deletes || 0; tDeletes += p.est_deletes || 0;
      if (p.duration_s != null) { a.dur += p.duration_s; a.measured++; tDur += p.duration_s; tMeasured++; }
      if (p.heavy) { a.heavy++; heavyIds.push(lid); }
    });
    const etaOf = a => !a.measured ? '<span class="panel-note">미측정</span>'
      : "~" + fmtDur(a.dur) + (a.measured < a.n ? ` <span class="panel-note">(미측정 ${a.n - a.measured})</span>` : "");
    const rows = Object.keys(bySvc).sort().map(svc => {
      const a = bySvc[svc];
      return `<tr><td><b>${esc(svc)}</b></td><td>${a.n}</td><td>생성 ~${a.creates} · 삭제 ~${a.deletes}</td><td>${etaOf(a)}</td>` +
        `<td>${a.heavy ? `<span class="rp-warn">⚠️과금 ${a.heavy}</span>` : "—"}</td></tr>`;
    }).join("");
    const heavy = heavyIds.length > 0;

    // 게이트 칩 — Gate status 패널과 같은 실효 게이트 값(gate.*) 재사용, 이 선택의
    // 과금 라이프사이클 수는 별도 칩으로 분리 표시(서버 게이트와 혼동 방지).
    const gateChips =
      `<div>` +
      `<span class="rp-gatechip ${gate.mutations ? "on" : "off"}">Mutations ${gate.mutations ? "ON" : "OFF"}</span>` +
      `<span class="rp-gatechip ${gate.destructive ? "on" : "off"}">Destructive ${gate.destructive ? "ON" : "OFF"}</span>` +
      `<span class="rp-gatechip ${gate.heavy ? "on" : "off"}">Heavy(서버) ${gate.heavy ? "ON" : "OFF"}</span>` +
      `<span class="rp-gatechip ${heavy ? "on" : "off"}">이 선택 과금 라이프사이클 ${heavyIds.length}개</span>` +
      `</div>`;

    const queueNote = peak > headroom
      ? `<p class="panel-note" style="color:var(--stale)">→ VPC 여유(${headroom}) 초과: 즉시 실행되지 않고 대기 큐에 들어갑니다.</p>` : "";
    const skipped = (plan.skipped_disabled || []).length
      ? `<p class="panel-note">disabled로 건너뜀: ${plan.skipped_disabled.map(esc).join(", ")}</p>` : "";
    const warningsHtml = (pf && pf.warnings && pf.warnings.length)
      ? `<p class="panel-note">${pf.warnings.map(esc).join(" · ")}</p>` : "";
    const heavyBlock = heavy
      ? `<div class="rp-heavy-note"><b>과금 라이프사이클 ${heavyIds.length}개:</b> ${heavyIds.map(id => `<code>${esc(id)}</code>`).join(" · ")}` +
        `<br><label style="display:inline-flex;gap:6px;align-items:center;margin-top:7px;cursor:pointer">` +
        `<input type="checkbox" id="rp-heavy-ok"> <b>과금 실행임을 확인했습니다</b></label>` +
        `<p class="panel-note" style="margin:6px 0 0">과금 실행 확인에 체크해야 [Run live]가 열립니다.</p></div>`
      : "";
    const etaTotal = (pf && pf.est && pf.est.p50_s != null)
      ? "~" + fmtDur(pf.est.p50_s) + ` <span class="panel-note">~ ${fmtDur(pf.est.p90_s)} (병렬 makespan · ${esc(pf.est.basis || "?")})</span>`
      : (tMeasured ? "~" + fmtDur(tDur) + (tMeasured < tN ? ` <span class="panel-note">(미측정 ${tN - tMeasured})</span>` : "") : '<span class="panel-note">미측정</span>');

    $("rp-mbody").innerHTML =
      '<p class="panel-note">실제 클라우드 자원을 만들고 삭제하는 실행의 사전 점검입니다 — [Run live]를 누르면 실제 클라우드에 작용합니다.</p>' +
      gateChips +
      '<div class="tbl-scroll"><table class="tbl"><thead><tr><th>service</th><th>lifecycle</th><th>생성·삭제 예상</th><th>실측 ETA</th><th>과금</th></tr></thead>' +
      `<tbody>${rows}</tbody>` +
      `<tfoot><tr><td><b>합계</b></td><td>${nLc}</td><td>생성 ~${tCreates} · 삭제 ~${tDeletes}</td><td>${etaTotal}</td>` +
      `<td>${heavy ? `<span class="rp-warn">⚠️ ${heavyIds.length}</span>` : "—"}</td></tr></tfoot></table></div>` +
      '<p class="panel-note" style="margin-top:7px">행 ETA = 라이프사이클 실측 평균의 순차 합산 · <b>합계 ETA = 병렬 makespan 추정</b>' +
      (pf ? "" : " (견적 API 미응답 — 순차 합산 표시)") + ` · VPC 소모(peak) <b>${peak}</b> vs 현재 여유 <b>${headroom}</b></p>` +
      queueNote + skipped + warningsHtml + heavyBlock;

    const legacyHref = legacyDeepLink(sel);
    $("rp-mfoot").innerHTML =
      '<span class="panel-note" style="margin-right:auto">취소해도 선택은 유지됩니다.</span>' +
      '<button type="button" class="rp-allbtn" id="rp-pf-cancel">취소</button>' +
      `<a class="run-cta" href="${esc(legacyHref)}" target="_blank" rel="noopener"
          title="같은 선택으로 legacy 콘솔(console2)에서 사전 확인 후 실행">Continue in legacy console ↗</a>` +
      '<button type="button" class="rp-review-btn" id="rp-pf-go"' + (heavy ? ' disabled' : '') +
      ' title="사전 점검을 확인했으면 실행합니다 — 실제 클라우드에 작용합니다">Run live ▶</button>';
    $("rp-pf-cancel").onclick = pfClose;

    // 발사 배선 (오너 승인 2026-07-10). 과금(heavy) 선택이면 확인 체크박스가
    // [Run live] 잠금을 푼다.
    const go = $("rp-pf-go");
    if (go) go.onclick = () => postRun(sel);
    const bill = $("rp-heavy-ok");
    if (bill && go) bill.onchange = () => { go.disabled = !bill.checked; };
  }

  // 실제 LIVE 실행 트리거 — 오너 승인(2026-07-10)으로 배선. console2.js
  // postRun()과 같은 계약(POST /api/run, mode:"live"). 성공 시 v2 실행 뷰로 이동.
  function postRun(sel) {
    const go = $("rp-pf-go");
    if (go) { go.disabled = true; go.textContent = "Starting…"; }
    fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ mode: "live" }, sel)) })
      .then(r => r.json().then(j => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok || j.error) {
          if (go) { go.disabled = false; go.textContent = "Run live ▶"; }
          alert("실행을 시작하지 못했습니다: " + (j.error || "unknown") +
                (j.detail ? "\n" + j.detail : ""));
          return;
        }
        window.location.href = "/v2/runs/local-" + j.id;  // 실행 뷰(running)로
      })
      .catch(e => {
        if (go) { go.disabled = false; go.textContent = "Run live ▶"; }
        alert("실행 요청 실패: " + e);
      });
  }

  // ---- static wiring (once, after TEMPLATE is injected) ----
  function wireStatic() {
    $("rp-search").oninput = drawTree;
    $("rp-toggle-all").onclick = toggleAllServices;
    els("#rp-gran-group button").forEach(b => b.onclick = () => {
      if (dagScene) dagScene.setGranularity(b.dataset.gran);
      syncGranButtons();
    });
    $("rp-collapse-all").onclick = () => { if (dagScene) dagScene.collapseAll(); };
    $("rp-expand-all").onclick = () => { if (dagScene) dagScene.expandAll(); };
    $("rp-zoom-in").onclick = () => { if (dagScene) dagScene.zoomIn(); };
    $("rp-zoom-out").onclick = () => { if (dagScene) dagScene.zoomOut(); };
    $("rp-zoom-fit").onclick = () => { if (dagScene) dagScene.zoomToFit(); };
    $("rp-review-btn").onclick = openPreflight;
    $("rp-scrim").onclick = pfClose;
    document.addEventListener("keydown", e => { if (e.key === "Escape") pfClose(); });
  }
})();
