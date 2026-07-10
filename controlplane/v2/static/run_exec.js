/* controlplane/v2/static/run_exec.js — 실행 뷰(§2.9: /v2/runs/{id}의
 * running/queued 상태) 폴링 + 무깜빡 렌더.
 *
 * 레이아웃/문구/색은 오너 승인 목업(docs/working/plans/V2-EXEC-VIEW-MOCKUP.html)
 * 그대로 — 이 스크립트는 그 목업이 정적으로 보여준 것을 실제 폴링 데이터로
 * 채운다. 이식 원형(주석에 라인 각주):
 *   - groupEventsByLifecycle()        console2/assets/console2.js:2160-2213
 *   - renderNowPlaying()/liveProgress() console2/assets/console2.js:2031 / 2486
 *   - runProgress()                   console2/assets/console2.js:354-373
 *   - P2C-24 폴링 다이어트(2s 증분 + capacity 30s + 숨은 탭 정지)
 *                                     console2/assets/console2.js:291-303
 *   - skip-lifecycle/abort 확인 UX    console2/assets/console2.js:375-385, 2093-2122
 *
 * v2 경계: 이 스크립트는 어떤 실행도 발사하지 않는다. POST 하는 엔드포인트는
 * 이미 서버가 지원하는 실행 제어 API 둘뿐 — ⏸ POST /api/runs/{id}/skip-lifecycle,
 * ⏹/✕ POST /api/runs/{id}/abort (새 발사 기능이 아니다, 작업 지시 §4).
 */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const root = $("rx-root");
  if (!root) return;
  const rid = root.dataset.rid;
  const status = root.dataset.status;   // "running" | "queued" — this page's server-rendered skeleton

  let init = {};
  try { init = JSON.parse((($("rx-init-data") || {}).textContent) || "{}"); } catch (e) { /* corrupt/absent */ }

  let MODEL = { durations: {} };
  let events = [];
  let evOffset = 0;
  let evTimer = null, capTimer = null;
  let railFilter = "all";
  let activeTab = "resources";
  let lastCapacity = init.capacity || null;
  let done = false;

  fetch("/api/model").then(r => r.json()).then(m => { if (!m.error) MODEL = m; }).catch(() => { /* keep default */ });

  // ---- setIfChanged: skip a re-render when the HTML is identical (원형:
  // console2.js setHtmlIfChanged — 여기서는 key 기반 unit-patch까지는 포팅하지
  // 않고 "바뀐 패널만 교체"로 단순화해 깜빡임을 줄인다) ----------------------
  function setIfChanged(el, html) {
    if (!el || el._h === html) return false;
    el._h = html; el.innerHTML = html;
    return true;
  }

  function fmtElapsed(s) {
    if (s == null || !isFinite(s) || s < 0) return "";
    s = Math.round(s);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s";
    return Math.floor(s / 3600) + "h" + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
  }
  function fmtDur(s) {
    if (s == null) return "미측정";
    if (s < 90) return Math.round(s) + "초";
    if (s < 5400) return (s / 60).toFixed(1) + "분";
    return (s / 3600).toFixed(1) + "시간";
  }

  // ---- groupEventsByLifecycle port (origin: console2.js:2160-2213) --------
  function groupEventsByLifecycle(evs) {
    const lcs = {}, order = [];
    const ensure = id => {
      if (!lcs[id]) {
        lcs[id] = { id, status: "queued", service: "", reason: "",
          api: [], apiByKey: {}, resources: [], createN: 0, deleteN: 0, failN: 0 };
        order.push(id);
      }
      return lcs[id];
    };
    (evs || []).forEach(e => {
      const id = e.lifecycle;
      if (!id && e.kind !== "run-meta" && e.kind !== "wave-start") return;
      if (e.kind === "run-meta") (e.runnable || []).forEach(ensure);
      else if (e.kind === "wave-start") (e.lifecycles || []).forEach(ensure);
      else if (e.kind === "lifecycle-start") {
        const b = ensure(id); b.status = "running"; if (e.service) b.service = e.service;
      } else if (e.kind === "lifecycle-end") {
        const b = ensure(id);
        b.status = e.status === "passed" ? "done" : e.status === "skipped" ? "skip" : "fail";
        b.reason = e.reason || "";
        b.api.forEach(c => { if (c.open) { c.open = false; c.category = "fail"; b.failN++; } });
      } else if (e.kind === "step-start") {
        const b = ensure(id);
        const c = { step: e.step, method: e.method, path: e.path, ts: e.ts, open: true, category: "run" };
        b.apiByKey[e.step] = c; b.api.push(c);
      } else if (e.kind === "step-end") {
        const b = ensure(id);
        let c = b.apiByKey[e.step];
        if (!c) { c = { step: e.step, method: e.method, path: e.path }; b.apiByKey[e.step] = c; b.api.push(c); }
        c.open = false; c.status = e.status; c.category = e.category; c.ts = e.ts;
        c.ms = e.elapsed_ms != null ? Math.round(e.elapsed_ms) : null;
        if (e.category === "fail") b.failN++;
      } else if (e.kind === "resource-tracked") {
        const b = ensure(id); b.createN++;
        b.resources.push({ id: e.resource_id, type: e.resource_type });
      } else if (e.kind === "resource-deleted") {
        const b = ensure(id); b.deleteN++;
      }
    });
    return { lcs, order };
  }

  // 세마포어/budget 쿼터 스킵 판정 — 서버측(controlplane/v2/run_detail_data.py
  // _skip_reason_is_quota)과 같은 패턴 기준(engine.py 사유 문자열 실측).
  function isQuotaSkip(reason) {
    return /budget '.*' exhausted|VPC quota semaphore/.test(reason || "");
  }
  // VPC 생성 스텝 판정 — engine.py:127(_VPC_CREATE_PATH) + :1129-1139(세마포어는
  // 이벤트 없이 BLOCKING 대기) 근거로, 지연 의심 판정에서 제외한다(§2.9 명시:
  // 세마포어 대기 오탐 방지 — 엔진이 대기 이벤트를 emit하기 전까지의 보수적 우회).
  function isVpcCreateStep(step) {
    return (step.method || "").toUpperCase() === "POST" && (step.path || "") === "/v1/vpcs";
  }

  // ---- runProgress port (origin: console2.js:354-373) ----------------------
  const ETA_PARALLEL = 6;
  function runProgress(grouped) {
    const ids = grouped.order;
    const total = ids.length;
    const doneN = ids.filter(i => ["done", "fail", "skip"].includes(grouped.lcs[i].status)).length;
    let firstTs = null;
    for (const e of events) if (e.ts) { firstTs = e.ts; break; }
    const elapsed = firstTs ? Math.max(0, Date.now() / 1000 - firstTs) : null;
    const durs = (MODEL && MODEL.durations) || {};
    let rem = 0, known = 0;
    ids.forEach(i => {
      const st = grouped.lcs[i].status;
      if (st === "queued" || st === "running") {
        const d = durs[i];
        if (d && d.avg_s) { rem += d.avg_s; known++; }
      }
    });
    const eta = known ? rem / Math.min(ETA_PARALLEL, known) : null;
    return { total, done: doneN, pct: total ? Math.round(doneN / total * 100) : 0, elapsed, eta };
  }

  function findActiveStep(grouped) {
    for (const id of grouped.order) {
      const b = grouped.lcs[id];
      if (b.status !== "running") continue;
      const open = b.api.filter(c => c.open);
      if (open.length) return { lc: id, service: b.service, step: open[open.length - 1] };
    }
    return null;
  }

  // ---- slot meter (VPC capacity — mockup .slotmeter) -----------------------
  function slotMeterHtml(cap) {
    if (!cap) return "";
    const total = cap.cap || 0;
    const base = Math.max(0, cap.baseline || 0);
    const minePeak = (init.plan && init.plan.peak_vpcs) || 0;
    const other = Math.max(0, (cap.reserved || 0) - minePeak);
    const kinds = [];
    for (let i = 0; i < base; i++) kinds.push("base");
    for (let i = 0; i < minePeak; i++) kinds.push("mine");
    for (let i = 0; i < other; i++) kinds.push("other");
    while (kinds.length < total) kinds.push("free");
    const cells = kinds.slice(0, total).map(k => `<i class="sl ${k}"></i>`).join("");
    return `<span class="slotmeter" title="계정 VPC ${total}슬롯 — 기존 ${base}(회색) · 이 런 ${minePeak}(파랑) · 다른 런 ${other} · 여유 ${cap.headroom != null ? cap.headroom : "?"}">VPC ${cells} ${Math.min(base + minePeak + other, total)}/${total}</span>`;
  }

  // ============================ polling ====================================
  const EV_TICK_MS = 2000, CAP_MS = 30000;

  function pollEvents() {
    fetch(`/api/runs/${encodeURIComponent(rid)}/events?offset=${evOffset}`)
      .then(r => r.json()).then(j => {
        if (j.error) { evTimer = setTimeout(pollEvents, EV_TICK_MS); return; }
        events = (j.offset === 0) ? (j.events || []) : events.concat(j.events || []);
        evOffset = j.next_offset != null ? j.next_offset : events.length;
        if (j.status !== status) {
          // 상태 전이 — 이 페이지는 상태별로 다른 골격을 서버에서 렌더하므로
          // (§2.9: running/queued/done 레이아웃이 각각 다름) DOM을 그 자리에서
          // 재구성하지 않고 정직하게 새로고침한다(요구사항 6의 "리로드 허용"과
          // 같은 취지의 자동 소프트 전환).
          if (j.status === "running" || j.status === "queued") { location.reload(); return; }
          onDone();
          return;
        }
        renderAll();
        if (!document.hidden) evTimer = setTimeout(pollEvents, EV_TICK_MS);
      }).catch(() => { evTimer = setTimeout(pollEvents, EV_TICK_MS); });
  }

  function pollCapacity() {
    fetch("/api/capacity").then(r => r.json()).then(j => {
      if (!j.error) { lastCapacity = j; renderCapacityDependent(); }
    }).catch(() => { /* best-effort */ });
    if (!done) capTimer = setTimeout(pollCapacity, CAP_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden || done) return;
    pollEvents();
    pollCapacity();
  });

  // ---- done transition (§2.9 item 6) ---------------------------------------
  // 실측: LIVE(pytest) 경로는 이벤트 스트림에 "run-end" 종류를 절대 emit하지
  // 않는다(regression/scenarios/engine.py 전체에 없음) — simulate 경로만
  // emit한다(regression/scenarios/local_run.py:110, console2_server.py:2304).
  // 그래서 "run-end 수신"의 실제로 신뢰 가능한 신호는 이벤트 종류가 아니라
  // /api/runs/{id}/events 응답의 최상위 status 필드다(live/simulate 공통) —
  // 위 pollEvents()의 status 비교가 곧 그 신호다.
  function onDone() {
    if (done) return;
    done = true;
    if (evTimer) clearTimeout(evTimer);
    if (capTimer) clearTimeout(capTimer);
    const b = $("rx-done-banner");
    if (b) b.style.display = "";
  }

  // ============================ render ======================================
  function renderAll() {
    const grouped = groupEventsByLifecycle(events);
    renderPlan(grouped);
    renderCapacityDependent(grouped);
    if (status !== "queued") {
      renderNowPlaying(grouped);
      renderAlerts(grouped);
      renderRail(grouped);
      renderTicker();
      renderTabs(grouped);
    }
  }

  function renderPlan(grouped) {
    const el = $("rx-plan-v");
    if (!el || !init.plan) return;
    const p = init.plan;
    let creates = 0, deletes = 0, dur = 0, measured = 0, total = 0;
    Object.values(p.preview || {}).forEach(pv => {
      total++; creates += pv.est_creates || 0; deletes += pv.est_deletes || 0;
      if (pv.duration_s != null) { dur += pv.duration_s; measured++; }
    });
    const etaTxt = measured ? "~" + fmtDur(dur) + (measured < total ? ` (측정 ${measured}/${total})` : "") : "미측정";
    setIfChanged(el, `생성 ~<b>${creates}</b> · 삭제 ~${deletes} · peak VPC <b>${p.peak_vpcs || 0}</b> · ETA <b>${etaTxt}</b> (p50 순차합산 근사)`);
  }

  function renderCapacityDependent(grouped) {
    if (status === "queued") { renderQueuedMeta(); return; }
    const el = $("rx-actual-v");
    if (!el) return;
    const g = grouped || groupEventsByLifecycle(events);
    const created = g.order.reduce((n, i) => n + g.lcs[i].createN, 0);
    const deleted = g.order.reduce((n, i) => n + g.lcs[i].deleteN, 0);
    const prog = runProgress(g);
    const meter = slotMeterHtml(lastCapacity);
    setIfChanged(el, `생성 <b>${created}</b> · 삭제 ${deleted} · 경과 <b>${prog.elapsed != null ? fmtElapsed(prog.elapsed) : "—"}</b>${meter ? "<br>" + meter : ""}`);
  }

  function renderQueuedMeta() {
    const el = $("rx-actual-v");
    const q = init.queued || {};
    const cap = lastCapacity || init.capacity;
    if (el) {
      const meter = slotMeterHtml(cap);
      const headroom = cap ? cap.headroom : q.headroom;
      setIfChanged(el, `${meter}<br><span class="mut" style="font-size:12px">여유 ${headroom != null ? headroom : "?"} &lt; 내 요구 peak ${q.peak_vpcs || 0} — 다른 실행이 ${cap ? cap.reserved : "?"}슬롯 점유</span>`);
    }
    const meta = $("rx-queue-meta");
    if (meta) {
      const posTxt = init.queue_position ? `대기 ${init.queue_position}번째` : "대기열";
      const etaTxt = q.blocking_eta_s != null
        ? `앞선 실행(${esc(q.blocking_run_id || "?")}) 잔여 ~${fmtDur(q.blocking_eta_s)} → 예상 시작 ~${fmtDur(q.blocking_eta_s)} 내`
        : "앞선 실행의 예상 잔여 시간을 계산할 수 없습니다 (측정 이력 부족 — 근사치)";
      setIfChanged(meta, `${posTxt} — ${etaTxt}`);
    }
  }

  function renderNowPlaying(grouped) {
    const host = $("rx-nowplay");
    if (!host) return;
    const active = findActiveStep(grouped);
    const prog = runProgress(grouped);
    const progHtml = `<div class="prog"><i style="width:${prog.pct}%"></i></div>` +
      `<span class="meta">전체 ${prog.pct}%${prog.elapsed != null ? " · 경과 " + fmtElapsed(prog.elapsed) : ""}${prog.eta != null ? " · 잔여 ~" + fmtElapsed(prog.eta) : ""}</span>`;
    let html;
    if (!active) {
      html = `<span class="dotp"></span><span class="step">실행 중</span><span class="meta">다음 step 대기…</span>${progHtml}<button class="abort" id="rx-abort" type="button">⏹ Abort</button>`;
    } else {
      const elapsed = active.step.ts ? (Date.now() / 1000 - active.step.ts) : null;
      const durs = (MODEL && MODEL.durations) || {};
      const avg = (durs[active.lc] && durs[active.lc].avg_s) || null;
      html = `<span class="dotp"></span>
        <span class="step">${esc(active.lc)} : ${esc(active.step.step || "")}</span>
        <span class="meta">${esc(active.step.method || "")} ${esc(active.step.path || "")} · 경과 ${elapsed != null ? fmtElapsed(elapsed) : "—"}${avg ? " / 이 lifecycle 평균 ~" + fmtElapsed(avg) : " / 평균 미측정"}</span>
        ${progHtml}<button class="abort" id="rx-abort" type="button">⏹ Abort</button>`;
    }
    if (setIfChanged(host, html)) wireAbort();
  }

  function renderAlerts(grouped) {
    const host = $("rx-alerts");
    if (!host) return;
    const banners = [];
    const active = findActiveStep(grouped);
    if (active && active.step.ts && !isVpcCreateStep(active.step)) {
      const durs = (MODEL && MODEL.durations) || {};
      const avg = durs[active.lc] && durs[active.lc].avg_s;
      const elapsed = Date.now() / 1000 - active.step.ts;
      if (avg && elapsed > avg * 3) {
        banners.push(`<div class="alert-strip"><b>⚠ 지연 의심</b> ${esc(active.step.step || "")}의 응답 대기가 실측 평균(${fmtElapsed(avg)})의 <b>${(elapsed / avg).toFixed(1)}배</b>입니다 — run-end 자동수리 룰에는 해당 없음.
          <div class="act"><button type="button" data-skip="${esc(active.lc)}">⏸ 이 라이프사이클 스킵</button><button type="button" data-dismiss="1">계속 관찰</button></div></div>`);
      }
    }
    // 실패 군집 — 같은 서비스에서 lifecycle-end(status=failed) 연속 ≥2
    const failedEnds = events.filter(e => e.kind === "lifecycle-end" && e.status === "failed");
    if (failedEnds.length >= 2) {
      const last2 = failedEnds.slice(-2);
      const svc1 = (grouped.lcs[last2[0].lifecycle] || {}).service;
      const svc2 = (grouped.lcs[last2[1].lifecycle] || {}).service;
      if (svc1 && svc1 === svc2) {
        const clusterN = failedEnds.filter(e => (grouped.lcs[e.lifecycle] || {}).service === svc1).length;
        banners.push(`<div class="alert-strip" style="border-left-color:#cf222e"><b style="color:#cf222e">⚠ 실패 군집</b> ${esc(svc1)}에서 연속 실패 ${clusterN}건입니다.
          <div class="act"><button type="button" data-skip="${esc(last2[1].lifecycle)}">⏸ 다음 실패 방지</button><button type="button" data-dismiss="1">계속 관찰</button></div></div>`);
      }
    }
    if (setIfChanged(host, banners.join(""))) {
      wireSkipButtons(host);
      host.querySelectorAll("[data-dismiss]").forEach(b => b.onclick = () => { host.innerHTML = ""; host._h = ""; });
    }
  }

  function renderRail(grouped) {
    const rows = $("rx-rail-rows");
    const ring = $("rx-ring-mini");
    if (!rows) return;
    const glyphOf = st => ({ done: "✓", running: "⏳", fail: "✗", queued: "·" }[st] || "·");
    const filtered = grouped.order.filter(id => {
      const st = grouped.lcs[id].status;
      if (railFilter === "all") return true;
      if (railFilter === "running") return st === "running";
      if (railFilter === "fail") return st === "fail";
      if (railFilter === "queued") return st === "queued";
      return true;
    });
    const rowsHtml = filtered.map(id => {
      const b = grouped.lcs[id];
      const quota = b.status === "skip" && isQuotaSkip(b.reason);
      const glyph = b.status === "skip" ? (quota ? "⊘" : "⏸") : glyphOf(b.status);
      const glyphColor = quota ? "color:#9a6700" : (b.status === "fail" ? "color:var(--bad-ink)" : "");
      const chip = b.status === "fail" ? '<span class="failp">fail</span>'
        : (quota ? `<span class="failp" style="background:#fff8c5;color:#9a6700" title="${esc(b.reason)}">쿼터 스킵</span>` : "");
      const skipBtn = (b.status === "running" || b.status === "queued")
        ? `<button class="skipbtn" type="button" data-skip="${esc(id)}" title="⏸ ${esc(id)} 스킵">⏸</button>` : "";
      return `<div class="lc-row" data-lc="${esc(id)}"><span class="g" style="${glyphColor}">${glyph}</span><span class="nm">${esc(id)}</span>${chip}${skipBtn}</div>`;
    }).join("") || '<p class="empty-state">표시할 라이프사이클이 없습니다(필터를 확인하세요).</p>';
    if (setIfChanged(rows, rowsHtml)) wireSkipButtons(rows);

    if (ring) {
      const prog = runProgress(grouped);
      const failN = grouped.order.filter(id => grouped.lcs[id].status === "fail").length;
      const runningN = grouped.order.filter(id => grouped.lcs[id].status === "running").length;
      const waitN = grouped.order.filter(id => grouped.lcs[id].status === "queued").length;
      setIfChanged(ring, `<div class="ring-sm" style="background:conic-gradient(var(--loc) calc(${prog.pct}*1%), var(--line) 0)"><i>${prog.pct}%</i></div>
        <div style="font-size:12.5px">전체 집계<br><b>${prog.done}</b> 완료 · <b>${runningN}</b> 실행 중 · <b>${waitN}</b> 대기${failN ? ` · <span style="color:var(--bad-ink)"><b>${failN}</b> fail</span>` : ""}</div>`);
    }
    wireRailFilters();
  }

  let _railWired = false;
  function wireRailFilters() {
    const host = $("rx-rail-filters");
    if (!host || _railWired) return;
    _railWired = true;
    host.addEventListener("click", ev => {
      const chip = ev.target.closest(".fchip");
      if (!chip) return;
      railFilter = chip.dataset.f;
      [...host.querySelectorAll(".fchip")].forEach(c => c.classList.toggle("on", c === chip));
      renderRail(groupEventsByLifecycle(events));
    });
  }

  function renderTicker() {
    const host = $("rx-ticker-rows");
    if (!host) return;
    const ends = events.filter(e => e.kind === "step-end").slice(-5).reverse();
    const html = ends.length
      ? ends.map(e => {
          const cls = (e.status || 0) >= 500 ? "st5" : "st2";
          const ago = e.ts ? fmtElapsed(Date.now() / 1000 - e.ts) + " 전" : "";
          return `<div class="tick-row"><span class="${cls}">${e.status != null ? e.status : "—"}</span><span>${esc(e.method || "")} ${esc(e.path || "")}</span><span>${e.elapsed_ms != null ? (e.elapsed_ms / 1000).toFixed(1) + "s" : ""}</span><span class="mut">${ago}</span></div>`;
        }).join("")
      : '<p class="empty-state">아직 호출 없음</p>';
    setIfChanged(host, html);
  }

  function renderTabs(grouped) {
    const tabsHost = $("rx-tabs");
    if (!tabsHost) return;
    if (!tabsHost._wired) {
      tabsHost._wired = true;
      tabsHost.addEventListener("click", ev => {
        const b = ev.target.closest("[data-tab]");
        if (!b) return;
        activeTab = b.dataset.tab;
        [...tabsHost.querySelectorAll("[data-tab]")].forEach(x => x.classList.toggle("on", x === b));
        renderTabBody(groupEventsByLifecycle(events));
      });
    }
    const resN = grouped.order.reduce((n, i) => n + grouped.lcs[i].resources.length, 0);
    const apiN = grouped.order.reduce((n, i) => n + grouped.lcs[i].api.length, 0);
    const rc = $("rx-cnt-res"); if (rc) rc.textContent = resN;
    const ac = $("rx-cnt-api"); if (ac) ac.textContent = apiN;
    renderTabBody(grouped);
  }

  function renderTabBody(grouped) {
    const host = $("rx-tab-body");
    if (!host) return;
    let html;
    if (activeTab === "resources") {
      const rows = [];
      grouped.order.forEach(id => grouped.lcs[id].resources.forEach(r =>
        rows.push(`<tr><td class="mono">${esc(id)}</td><td>${esc(r.type || "")}</td><td class="mono">${esc(r.id || "")}</td></tr>`)));
      html = rows.length
        ? `<div class="tbl-scroll"><table class="tbl"><thead><tr><th>Lifecycle</th><th>Type</th><th>Resource id</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
        : '<p class="empty-state">아직 추적된 자원이 없습니다.</p>';
    } else if (activeTab === "api") {
      const rows = [];
      grouped.order.forEach(id => grouped.lcs[id].api.forEach(c => {
        const cls = c.open ? "" : ((c.status || 0) >= 500 ? "st5" : ((c.status || 0) < 400 ? "st2" : ""));
        rows.push(`<tr><td class="mono">${esc(id)}</td><td>${esc(c.method || "")}</td><td class="mono">${esc(c.path || "")}</td>` +
          `<td class="${cls}">${c.open ? "⏳" : (c.status != null ? c.status : "—")}</td><td>${c.ms != null ? c.ms + "ms" : ""}</td></tr>`);
      }));
      html = rows.length
        ? `<div class="tbl-scroll"><table class="tbl"><thead><tr><th>Lifecycle</th><th>Method</th><th>Path</th><th>Status</th><th>Elapsed</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
        : '<p class="empty-state">아직 호출이 없습니다.</p>';
    } else {
      html = '<p class="panel-note">실행 로그 tail은 기존 콘솔(<a href="/testing" target="_blank" rel="noopener">/testing</a>)에서 확인하세요 — 이 뷰는 이벤트 스트림(자원·API)만 다룹니다.</p>';
    }
    setIfChanged(host, html);
  }

  // ---- ⏸ skip-lifecycle: hover 버튼 + 확인 팝오버(목업 문구 그대로) --------
  let _skipPopEl = null;
  function closeSkipPop() { if (_skipPopEl) { _skipPopEl.remove(); _skipPopEl = null; } }
  function openSkipConfirm(btn) {
    closeSkipPop();
    const lc = btn.dataset.skip;
    const pop = document.createElement("div");
    pop.className = "skip-pop";
    pop.innerHTML = `<b>⏸ ${esc(lc)} 스킵?</b><br>진행 중 스텝은 경계에서 멈추고, 이 라이프사이클만 건너뜁니다 — 런 전체는 계속됩니다. 이미 만든 자원은 정리 단계가 수거합니다.
      <div class="row"><button type="button" class="warn" id="rx-skip-go">스킵 확정</button><button type="button" id="rx-skip-cancel">취소</button></div>`;
    document.body.appendChild(pop);
    const r = btn.getBoundingClientRect();
    pop.style.top = (window.scrollY + r.bottom + 4) + "px";
    pop.style.left = (window.scrollX + Math.max(8, r.left - 200)) + "px";
    _skipPopEl = pop;
    $("rx-skip-cancel").onclick = closeSkipPop;
    $("rx-skip-go").onclick = () => {
      closeSkipPop();
      fetch(`/api/runs/${encodeURIComponent(rid)}/skip-lifecycle`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle: lc }),
      }).catch(() => { /* surfaced by the next poll's lifecycle status */ });
    };
    setTimeout(() => document.addEventListener("click", onOutsideSkipClick, { once: true }), 0);
  }
  function onOutsideSkipClick(ev) {
    if (_skipPopEl && !_skipPopEl.contains(ev.target)) closeSkipPop();
  }
  function wireSkipButtons(container) {
    if (!container || container._wiredSkip) return;
    container._wiredSkip = true;
    container.addEventListener("click", ev => {
      const btn = ev.target.closest("[data-skip]");
      if (!btn) return;
      ev.stopPropagation();
      openSkipConfirm(btn);
    });
  }

  // ---- ⏹/✕ abort: 확인 모달 -----------------------------------------------
  let _abortModalEl = null;
  function closeAbortModal() { if (_abortModalEl) { _abortModalEl.remove(); _abortModalEl = null; } }
  function openAbortConfirm() {
    closeAbortModal();
    const scrim = document.createElement("div");
    scrim.style.cssText = "position:fixed;inset:0;background:rgba(15,23,42,.4);z-index:79";
    const m = document.createElement("div");
    m.className = "skip-pop";
    m.style.cssText = "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:300px;z-index:80";
    m.innerHTML = `<b style="color:var(--bad-ink)">⏹ 이 실행을 중단할까요?</b><br>진행 중인 프로세스 트리를 종료하고, teardown 스윕 후 '중단됨(aborted)'으로 기록됩니다.
      <div class="row"><button type="button" class="warn" id="rx-abort-go">중단 실행</button><button type="button" id="rx-abort-cancel">취소</button></div>`;
    document.body.appendChild(scrim);
    document.body.appendChild(m);
    _abortModalEl = m;
    scrim.onclick = () => { closeAbortModal(); scrim.remove(); };
    $("rx-abort-cancel").onclick = () => { closeAbortModal(); scrim.remove(); };
    $("rx-abort-go").onclick = () => {
      closeAbortModal(); scrim.remove();
      fetch(`/api/runs/${encodeURIComponent(rid)}/abort`, { method: "POST" })
        .then(() => pollEvents()).catch(() => { /* surfaced by the next poll */ });
    };
  }
  function wireAbort() {
    const btn = $("rx-abort");
    if (btn) btn.onclick = openAbortConfirm;
  }

  // queued: 대기 취소 — 실측(tools/console2_server.py:_abort_run)으로 확인:
  // status=="queued"인 rec은 abort가 즉시 대기열에서 제거 + status=aborted로
  // 기록한다(같은 엔드포인트가 running과 queued 둘 다 지원, 새 API 아님).
  const cancelBtn = $("rx-cancel");
  if (cancelBtn) {
    cancelBtn.onclick = () => {
      cancelBtn.disabled = true;
      cancelBtn.textContent = "취소 요청 중…";
      fetch(`/api/runs/${encodeURIComponent(rid)}/abort`, { method: "POST" })
        .then(() => { location.reload(); })
        .catch(() => { cancelBtn.disabled = false; cancelBtn.textContent = "✕ 대기 취소"; });
    };
  }

  // ============================ bootstrap ===================================
  renderAll();
  pollEvents();
  pollCapacity();
})();
