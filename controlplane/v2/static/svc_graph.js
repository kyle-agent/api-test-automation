/* controlplane/v2/static/svc_graph.js — 서비스 상세(/v2/services/{slug})의
 * "의존 미니그래프 인스펙터" (D6 확정: "의존이 가치 있는 순간은 개별 서비스를
 * 눌렀을 때" — L2 그래프 단일화의 서비스 축).
 *
 * 렌더러는 console2 소유 원본(/testing/console/assets/resource_graph.js)을
 * 그대로 로드해 재사용한다(복붙하지 않는다) — controlplane/v2/static/
 * runs_plan.js와 동일한 window.ResourceGraph.scene() 계약, 컨트롤러 1개만
 * 만들고 이후로는 scene.update()만 호출한다.
 *
 * 데이터 출처: POST /api/graph {services:["<category>/<service>"]} —
 * tools.console2_server._graph()의 계약. 실측(2026-07-10, 127.0.0.1:8800):
 * 이 엔드포인트가 돌려주는 노드에는 is_target(이 서비스 자신의 lifecycle
 * 노드)만 있고 is_dependent 플래그는 없다(그건 별도 API인
 * /planning/resources/graph.json?focus= · regression.scenarios.composer.
 * focus_view() 전용 필드다 — 불확실성/최종 보고 참고). 그래도 렌더러 기본이
 * is_target 강조(★ + 파란 테두리)는 이미 하므로 "이 서비스 노드는 강조"
 * 요건은 그대로 충족된다.
 *
 * 지연 로드: <details id="dep-panel"> 최초 펼침(toggle, open=true) 때 1회만
 * fetch한다 — 페이지 기본 무게에 영향 없음.
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };

  var panel = $("dep-panel");
  if (!panel) return;   // this page has no Dependencies panel

  var svcKey = (panel.dataset.service || "").trim();   // "<category>/<service>"
  var fetched = false;
  var scene = null;
  var GNODES = {};   // last /api/graph fetch: node id -> node

  // dashboard.build.slug(category, service) port — services_data.py:get_service
  // 계약과 동일한 슬러그: f"{category}__{service}".replace("/", "-").replace(" ", "-")
  function nodeSlug(svcField) {
    var parts = String(svcField || "").split("/");
    var cat = parts.shift() || "";
    var rest = parts.join("/");
    return (cat + "__" + rest).replace(/\//g, "-").replace(/\s+/g, "-");
  }

  function out() { return $("dep-graph-root"); }

  function showEmpty() {
    out().innerHTML = '<p class="empty-state">이 서비스의 리소스 모델이 아직 없습니다 — Model 축 참조.</p>';
  }
  function showError(msg) {
    out().innerHTML = '<p class="empty-state">의존 그래프 로딩 실패: ' + esc(msg || "") + '</p>';
  }
  function showLoading() {
    out().innerHTML = '<p class="empty-state">의존 그래프를 불러오는 중…</p>';
  }

  function inspect(node) {
    var box = $("dep-insp");
    if (!box) return;
    if (!node) { box.classList.remove("on"); box.innerHTML = ""; return; }
    var slug = nodeSlug(node.service);
    box.classList.add("on");
    box.innerHTML =
      '<div class="dep-insp-row"><span class="k">id</span><span class="v mono">' + esc(node.id) + '</span></div>' +
      '<div class="dep-insp-row"><span class="k">service</span><span class="v mono">' + esc(node.service) + '</span></div>' +
      '<div class="dep-insp-row"><a href="/v2/services/' + esc(slug) + '">' + esc((node.service || "").split("/").pop() || node.service) + ' 서비스로 이동 →</a></div>';
  }

  function render(g) {
    if (!g || !g.nodes || !g.nodes.length) { showEmpty(); return; }
    GNODES = {};
    g.nodes.forEach(function (n) { GNODES[n.id] = n; });
    out().innerHTML =
      '<div class="dep-stage-wrap" id="dep-stage-wrap"><div class="dep-stage" id="dep-stage"><svg id="dep-svg"></svg></div></div>' +
      '<div class="dep-insp" id="dep-insp"></div>';
    if (!window.ResourceGraph || !window.ResourceGraph.scene) {
      showError("resource_graph.js 렌더러를 불러오지 못했습니다 (/testing/console/assets/resource_graph.js)");
      return;
    }
    // read-only inspector — no onToggleTarget/isSelectable, so the renderer
    // never draws the +/✓ selection corner (이 화면은 선택하지 않는다).
    scene = window.ResourceGraph.scene($("dep-svg"), $("dep-stage"), g, {
      onFocus: function (fi) {
        if (!fi) { inspect(null); return; }
        inspect(GNODES[fi.label] || null);
      },
    });
    scene.start();
  }

  function load() {
    if (fetched) return;
    fetched = true;
    if (!svcKey) { showError("service 식별자 없음"); return; }
    showLoading();
    fetch("/api/graph", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ services: [svcKey] }),
    }).then(function (r) { return r.json(); }).then(function (g) {
      if (g && g.error) { showError(g.error); return; }
      render(g);
    }).catch(function (e) { showError(e && e.message); });
  }

  panel.addEventListener("toggle", function () {
    if (panel.open) load();
  });
})();
