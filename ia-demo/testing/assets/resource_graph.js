/* console2 — composition-DAG renderer (LIGHT theme).
 * A port of controlplane/static/graph.js (ResourceGraph.render/load/
 * transitiveReduction) recolored to the GitHub Primer LIGHT palette. The LOGIC is
 * intact: transitive reduction of the *displayed* edge set, longest-path (level)
 * layout, level bands, and the click handler. ONLY the colors changed:
 *   node fill light (#f6f8fa / white), text dark #1f2328;
 *   target stroke blue #2563c9 (★ badge), shared/dedup stroke amber #b5740b,
 *   dependent purple #8250df (↓), provenance VALIDATED green / docs amber border
 *   for plain nodes; level bands a faint blue wash with L0/L1… labels;
 *   heavy 🜂, quota ⛔.
 *
 *   ResourceGraph.render(svgEl, data, {onClick, overlay}) -> layout     (flat, legacy)
 *   ResourceGraph.scene(svgEl, stageEl, data, opt) -> controller        (DAG-at-scale)
 *
 * data = {nodes:[{id,service,provenance,quota,heavy,options,level,is_target,
 *                 shared,is_dependent}], edges:[{from,to}], order, teardown, peak_quota}
 * overlay(id) -> {fill,stroke,badge,pulse} | null   (per-node status/result colors)
 *
 * ----- DAG-at-scale (B2) ------------------------------------------------------
 * `scene()` is the productionized B2 mockup (console2/mockups/b2-dag-scale.html):
 * group-by-category & collapse-by-default when large, an OBVIOUS expand⇄collapse
 * TOGGLE (▸/▾ chevron, click group again or 전체 접기/펼치기 to collapse), focus
 * on a resource node (dependency path highlit, rest dimmed),
 * zoom +/−/맞춤/wheel, and drag-to-pan. It renders the SAME graph object the flat
 * renderer does, so 구성(selection) and 흐름(live-run) share one navigable scene;
 * an `overlay` keeps run-state coloring PRIMARY on 흐름. */
(function (global) {
  // LIGHT palette (Primer)
  const C = {
    fillPlain: "#ffffff", fillTarget: "#e6effd", fillDep: "#f3eefc", fillShared: "#fffaf0",
    text: "#1f2328", sub: "#656d76",
    target: "#2563c9", shared: "#b5740b", dependent: "#8250df",
    val: "#2da44e", docs: "#b5740b",
    edge: "#8a93a0", band: "#2563c91a", bandText: "#656d76",
    amber: "#b5740b",
  };
  const PROV = p => (p === "VALIDATED" ? C.val : C.docs);

  // category accent colors (distinct, muted) for group stripes/chips — mirrors the
  // B2 mockup's CAT_COLOR so the collapsed-by-category view reads the same.
  const CAT_COLOR = {
    "networking": "#2563c9", "security": "#cf222e", "management": "#8250df", "storage": "#0a7ea3",
    "compute": "#1a7f37", "container": "#9a6700", "database": "#bc4c00", "data-analytics": "#6e40c9",
    "ai-ml": "#bf3989", "application-service": "#0969da", "platform": "#656d76",
    "financial-management": "#b5740b", "devops-tools": "#1f883d",
  };
  const catColor = c => CAT_COLOR[c] || "#656d76";
  // Short English labels + a stable left-to-right (foundation-first) order.
  const CAT_LABEL = {
    "networking": "Networking", "security": "Security", "management": "Management", "storage": "Storage",
    "compute": "Compute", "container": "Container", "database": "Database", "data-analytics": "Data Analytics",
    "ai-ml": "AI/ML", "application-service": "App Service", "platform": "Platform",
    "financial-management": "Financial Mgmt", "devops-tools": "DevOps Tools",
  };
  const CAT_ORDER = ["networking", "security", "management", "storage", "compute", "container",
    "database", "data-analytics", "ai-ml", "application-service", "platform",
    "financial-management", "devops-tools"];
  const catLabel = c => CAT_LABEL[c] || c;
  const catOrderIdx = c => { const i = CAT_ORDER.indexOf(c); return i < 0 ? 99 : i; };
  const catOf = svc => (svc || "").split("/")[0];
  const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

  /* Transitive reduction of the *displayed* edge set.
   * Drops edge A->C when C is also reachable from A via a strictly-longer path
   * (A->..->B->C) that lies entirely within the displayed edge set. The data
   * model is untouched — only the drawn edges change. one_of branch edges are
   * just ordinary edges here, so a chosen branch is preserved unless it is
   * itself genuinely redundant. Edges to/from nodes not in the layout are kept
   * as-is (we never invent or drop edges whose longer path isn't fully present).
   * Returns a new edge array; never mutates the input.
   */
  function transitiveReduction(edges) {
    // adjacency from the displayed edges only
    const succ = {};
    edges.forEach(e => { (succ[e.from] = succ[e.from] || []).push(e.to); });
    // reachable(u, v, skip): is v reachable from u WITHOUT using edge (skipFrom->skipTo)?
    function reachableExcept(u, target, skipFrom, skipTo) {
      const stack = [u], seen = new Set([u]);
      while (stack.length) {
        const x = stack.pop();
        for (const y of (succ[x] || [])) {
          if (x === skipFrom && y === skipTo) continue;   // ignore the direct edge under test
          if (y === target) return true;
          if (!seen.has(y)) { seen.add(y); stack.push(y); }
        }
      }
      return false;
    }
    // dedup identical edges first so a duplicate doesn't "cover" its twin
    const seenKey = new Set(), uniq = [];
    edges.forEach(e => {
      const k = e.from + " " + e.to;
      if (!seenKey.has(k)) { seenKey.add(k); uniq.push(e); }
    });
    return uniq.filter(e =>
      e.from === e.to || !reachableExcept(e.from, e.to, e.from, e.to));
  }

  function layout(data, opt) {
    const colGap = opt.colGap || 200, rowGap = opt.rowGap || 56;
    const bw = opt.bw || 168, bh = opt.bh || 44, padX = 26, padY = 26;
    const byLevel = {};
    data.nodes.forEach(n => { (byLevel[n.level] = byLevel[n.level] || []).push(n); });
    const maxL = Math.max(0, ...data.nodes.map(n => n.level));
    const pos = {}; let maxRows = 0;
    for (let l = 0; l <= maxL; l++) {
      const col = (byLevel[l] || []).sort((a, b) =>
        (a.service < b.service ? -1 : a.service > b.service ? 1 : (a.id < b.id ? -1 : 1)));
      maxRows = Math.max(maxRows, col.length);
      col.forEach((n, i) => {
        pos[n.id] = { n, x: padX + l * colGap, y: padY + i * rowGap, w: bw, h: bh };
      });
    }
    const w = padX * 2 + maxL * colGap + bw;
    const h = padY * 2 + Math.max(1, maxRows) * rowGap;
    return { pos, w, h };
  }

  function render(svg, data, opt) {
    opt = opt || {};
    if (!data || !data.nodes || !data.nodes.length) {
      svg.removeAttribute("viewBox");
      svg.setAttribute("width", 0); svg.setAttribute("height", 0);
      svg.innerHTML = "";
      return { pos: {}, w: 0, h: 0 };
    }
    // Transitive reduction is the default for the displayed edges (the data
    // model in CATALOG is unchanged). Pass {reduce:false} to draw every direct
    // requires edge ("모든 직접 의존 표시").
    if (opt.reduce !== false && data && data.edges) {
      data = Object.assign({}, data, { edges: transitiveReduction(data.edges) });
    }
    const L = layout(data, opt);
    svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
    svg.setAttribute("width", L.w); svg.setAttribute("height", L.h);
    let s = `<defs><marker id="rg-ar" markerWidth="9" markerHeight="9" refX="8" refY="3"
      orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="${C.edge}"/></marker></defs>`;
    // level bands (faint blue wash + L0/L1… labels)
    const lvls = [...new Set(data.nodes.map(n => n.level))].sort((a, b) => a - b);
    lvls.forEach(l => {
      const any = data.nodes.find(n => n.level === l);
      if (!any) return;
      const x = L.pos[any.id].x - 13;
      s += `<rect x="${x}" y="0" width="${(opt.bw || 168) + 26}" height="${L.h}" fill="${C.band}"/>
        <text x="${x + 7}" y="15" font-size="10" font-weight="700" fill="${C.bandText}">L${l}</text>`;
    });
    data.edges.forEach(e => {
      const a = L.pos[e.from], b = L.pos[e.to]; if (!a || !b) return;
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
      s += `<path d="M${x1},${y1} C${x1 + 46},${y1} ${x2 - 46},${y2} ${x2},${y2}"
        fill="none" stroke="${C.edge}" stroke-width="1.3" marker-end="url(#rg-ar)"/>`;
    });
    data.nodes.forEach(n => {
      const p = L.pos[n.id];
      const ov = opt.overlay ? opt.overlay(n.id) : null;
      const fill = (ov && ov.fill) || (n.is_target ? C.fillTarget
        : n.is_dependent ? C.fillDep : n.shared ? C.fillShared : C.fillPlain);
      const stroke = (ov && ov.stroke) || (n.shared ? C.shared
        : n.is_target ? C.target : n.is_dependent ? C.dependent : PROV(n.provenance));
      const pulse = ov && ov.pulse;
      const sw = pulse ? 3 : (n.is_target ? 2.6 : (n.shared ? 2.2 : 1.6));
      const badge = (ov && ov.badge) || (n.is_target ? "★" : n.is_dependent ? "↓" : "");
      // active-node pulse: an animated stroke-width so the eye tracks the step
      // that is running right now (흐름 view). Pure SVG SMIL — no CSS dependency.
      const pulseAnim = pulse
        ? `<animate attributeName="stroke-width" values="3;5;3" dur="1.1s" repeatCount="indefinite"/>`
        : "";
      s += `<g class="rg-node${pulse ? " rg-active" : ""}" data-id="${esc(n.id)}" style="cursor:${opt.onClick ? "pointer" : "default"}">
        <title>${esc(n.id)} — ${esc(n.service)}\nprovenance ${esc(n.provenance)}${n.quota ? "\nquota " + esc(n.quota) : ""}${n.shared ? "\nshared (dedup)" : ""}${(n.options || []).length ? "\noptions: " + esc((n.options || []).join(", ")) : ""}</title>
        <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="${sw}">${pulseAnim}</rect>
        <text x="${p.x + 9}" y="${p.y + 18}" font-size="12.5" font-weight="700" fill="${C.text}">${n.heavy ? "🜂 " : ""}${esc(n.id)}</text>
        <text x="${p.x + 9}" y="${p.y + 33}" font-size="10" fill="${C.sub}">${esc((n.service || "").split("/").pop())}${n.quota ? " ⛔" + esc(n.quota) : ""}</text>
        ${badge ? `<text x="${p.x + p.w - 8}" y="${p.y + 17}" font-size="12" text-anchor="end" fill="${stroke}">${badge}</text>` : ""}
      </g>`;
    });
    svg.innerHTML = s;
    if (opt.onClick) svg.querySelectorAll("g.rg-node").forEach(g =>
      g.addEventListener("click", () => opt.onClick(g.dataset.id)));
    return L;
  }

  // fetch + render helper for the common case
  async function load(svg, url, opt, body) {
    const r = await fetch(url, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    const data = await r.json();
    if (data.error) { svg.innerHTML = `<text x="12" y="24" fill="#cf222e">${data.error}</text>`; return null; }
    render(svg, data, opt);
    return data;
  }

  /* ===========================================================================
   * grouping helpers (pure) — exported for tests. groupNodes() partitions the
   * graph's resource nodes by category (or service) into collapsible units; it
   * is the productionized port of the mockup's buildUnits(), but works on the
   * live /api/graph node list ({id, service, level, is_target, shared, heavy,
   * quota, provenance}) instead of the embedded model.
   * ===========================================================================*/

  // longest-path level over the FULL graph edge set (used to rank collapsed
  // groups left→right so a group sits LEFT of any group depending on it).
  function depthMap(nodes, edges) {
    const deps = {}; nodes.forEach(n => { deps[n.id] = []; });
    edges.forEach(e => { if (deps[e.to]) deps[e.to].push(e.from); });   // to requires from
    const memo = {};
    function d(id) {
      if (memo[id] != null) return memo[id];
      memo[id] = 0;                                   // cycle guard
      let m = 0; (deps[id] || []).forEach(p => { m = Math.max(m, d(p) + 1); });
      return (memo[id] = m);
    }
    nodes.forEach(n => d(n.id));
    return memo;
  }

  // ancestors / descendants over the graph edges (for focus closure + 표 scope).
  function closures(nodes, edges) {
    const pred = {}, succ = {};
    nodes.forEach(n => { pred[n.id] = []; succ[n.id] = []; });
    edges.forEach(e => { if (succ[e.from]) succ[e.from].push(e.to); if (pred[e.to]) pred[e.to].push(e.from); });
    function up(id, acc) { (pred[id] || []).forEach(p => { if (!acc[p]) { acc[p] = true; up(p, acc); } }); return acc; }
    function down(id, acc) { (succ[id] || []).forEach(c => { if (!acc[c]) { acc[c] = true; down(c, acc); } }); return acc; }
    return { up, down };
  }

  /* Build the list of visible UNITS given the granularity + which groups are
   * expanded. A unit is either a collapsed group (category | service) or an
   * individual resource node. Returns {units, byKey, ofRes} where a unit =
   *   {key, kind:'cat'|'svc'|'res', cat, svc, res, node, members:[ids], glevel}
   * glevel is the longest-path rank over the UNIT graph (so collapsed groups
   * still flow left→right). gran: 'category' | 'service' | 'resource'. */
  function groupNodes(data, gran, expanded) {
    gran = gran || "category"; expanded = expanded || {};
    const nodes = data.nodes || [], edges = data.edges || [];
    const byId = {}; nodes.forEach(n => { byId[n.id] = n; });
    const dm = depthMap(nodes, edges);

    // index resources by category + by service
    const byCat = {}, bySvc = {};
    nodes.forEach(n => {
      const cat = catOf(n.service);
      (byCat[cat] = byCat[cat] || []).push(n.id);
      (bySvc[n.service] = bySvc[n.service] || []).push(n.id);
    });

    const units = [], byKey = {};
    const minLvl = ids => Math.min.apply(null, ids.map(id => dm[id] || 0));
    function addRes(id) {
      const u = { key: "res:" + id, kind: "res", res: id, node: byId[id], members: [id], cat: catOf(byId[id].service) };
      units.push(u); byKey[u.key] = u;
    }
    function addSvc(svc) {
      if (expanded["svc:" + svc]) { bySvc[svc].slice().sort().forEach(addRes); return; }
      const mem = bySvc[svc].slice();
      const u = { key: "svc:" + svc, kind: "svc", svc, members: mem, cat: catOf(svc) };
      units.push(u); byKey[u.key] = u;
    }
    if (gran === "resource") {
      nodes.map(n => n.id).sort((a, b) => (dm[a] - dm[b]) || a.localeCompare(b)).forEach(addRes);
    } else if (gran === "service") {
      Object.keys(bySvc).sort().forEach(svc => {
        if (expanded["svc:" + svc]) bySvc[svc].slice().sort().forEach(addRes);
        else addSvc(svc);
      });
    } else { // category (default)
      Object.keys(byCat).sort((a, b) => catOrderIdx(a) - catOrderIdx(b) || a.localeCompare(b)).forEach(cat => {
        if (!byCat[cat].length) return;
        if (expanded["cat:" + cat]) {
          // expand category -> its services (each may itself be expanded -> resources)
          const svcs = [...new Set(byCat[cat].map(id => byId[id].service))].sort();
          svcs.forEach(addSvc);
        } else {
          const mem = byCat[cat].slice();
          const u = { key: "cat:" + cat, kind: "cat", cat, members: mem };
          units.push(u); byKey[u.key] = u;
        }
      });
    }

    // unit-level longest path (rank groups so a dependency sits to the LEFT).
    const ofRes = {};
    units.forEach(u => u.members.forEach(id => { ofRes[id] = u.key; }));
    const upred = {};
    units.forEach(u => { upred[u.key] = []; });
    const eseen = {};
    units.forEach(u => u.members.forEach(id => {
      (edges.filter(e => e.to === id)).forEach(e => {
        const from = ofRes[e.from], to = u.key;
        if (from && to && from !== to) {
          const k = from + ">" + to;
          if (!eseen[k]) { eseen[k] = 1; upred[to].push(from); }
        }
      });
    }));
    const umemo = {};
    function ulvl(k) {
      if (umemo[k] != null) return umemo[k];
      umemo[k] = 0;
      (upred[k] || []).forEach(p => { umemo[k] = Math.max(umemo[k], ulvl(p) + 1); });
      return umemo[k];
    }
    // fall back to member min-level so a single-column graph still ranks sensibly
    units.forEach(u => { u.glevel = Math.max(ulvl(u.key), 0); u._minlvl = minLvl(u.members); });
    return { units, byKey, ofRes, depth: dm, edges, nodes, byId };
  }

  /* ===========================================================================
   * scene() — the DAG-at-scale interactive controller.
   * ===========================================================================*/
  function scene(svg, stage, data, opt) {
    opt = opt || {};
    // DOM the scene drives (all optional except svg+stage). The caller wires the
    // toolbar buttons to controller methods; the scene owns zoom/pan/focus.
    const dom = {
      hint: opt.hint || null,            // hint pill
      stat: opt.stat || null,            // stat chip
      granNote: opt.granNote || null,    // granularity note
    };
    const collapseThreshold = opt.collapseThreshold || 25;

    let GRAPH = data || { nodes: [], edges: [] };
    let GRAN = "category";
    let EXPANDED = {};                    // unit key -> true (groups the user expanded)
    let FOCUS = null, FOCUSSET = null;    // focused resource unit + its in-path unit keys
    let LAYOUT = { pos: {}, w: 0, h: 0, units: [], byKey: {}, edges: [] };
    const T = { x: 0, y: 0, k: 1 };
    let started = false;                  // has the scene picked an initial collapse state?

    // ---- layout: column per glevel, rows within ----
    function computeLayout() {
      const g = groupNodes(GRAPH, GRAN, EXPANDED);
      // edges between visible units (dedup), then transitive-reduce for clean lines
      const ofRes = g.ofRes, eset = {}, uedges = [];
      g.edges.forEach(e => {
        const from = ofRes[e.from], to = ofRes[e.to];
        if (from && to && from !== to) {
          const k = from + ">" + to; if (!eset[k]) { eset[k] = 1; uedges.push({ from, to }); }
        }
      });
      const edges = transitiveReduction(uedges);
      const colGap = 212, rowGap = 58, padX = 30, padY = 28;
      const byLvl = {};
      g.units.forEach(u => { (byLvl[u.glevel] = byLvl[u.glevel] || []).push(u); });
      const maxL = Math.max(0, ...g.units.map(u => u.glevel));
      const pos = {}; let maxRows = 0;
      for (let l = 0; l <= maxL; l++) {
        const col = (byLvl[l] || []).sort((a, b) =>
          (catOrderIdx(a.cat || "") - catOrderIdx(b.cat || "")) || (a.key < b.key ? -1 : 1));
        maxRows = Math.max(maxRows, col.length);
        col.forEach((u, i) => {
          const w = u.kind === "res" ? 172 : 188, h = u.kind === "res" ? 46 : 54;
          pos[u.key] = { u, x: padX + l * colGap, y: padY + i * rowGap, w, h };
        });
      }
      const w = padX * 2 + maxL * colGap + 192;
      const h = padY * 2 + Math.max(1, maxRows) * rowGap;
      LAYOUT = { pos, w, h, edges, units: g.units, byKey: g.byKey, ofRes, depth: g.depth, nodes: g.nodes, byId: g.byId };
    }

    // focus closure: a resource unit's ancestors+descendants → the units that hold them
    function computeFocusSet(key) {
      const u = LAYOUT.byKey[key]; if (!u) return null;
      const { up, down } = closures(LAYOUT.nodes, GRAPH.edges || []);
      const resSet = {};
      u.members.forEach(id => { resSet[id] = true; up(id, resSet); down(id, resSet); });
      const set = {}; set[key] = true;
      LAYOUT.units.forEach(v => { if (v.members.some(id => resSet[id])) set[v.key] = true; });
      return set;
    }

    function unitFill(u, ov) {
      if (ov && ov.fill) return ov.fill;
      if (u.kind === "res") {
        const n = u.node;
        return n.is_target ? C.fillTarget : n.is_dependent ? C.fillDep : n.shared ? C.fillShared : C.fillPlain;
      }
      return "#fbfcfd";
    }
    function unitStroke(u, ov) {
      if (ov && ov.stroke) return ov.stroke;
      if (u.kind === "res") {
        const n = u.node;
        return n.shared ? C.shared : n.is_target ? C.target : n.is_dependent ? C.dependent : PROV(n.provenance);
      }
      return "#3a424b";
    }

    function draw() {
      computeLayout();
      svg.setAttribute("viewBox", `0 0 ${LAYOUT.w} ${LAYOUT.h}`);
      svg.setAttribute("width", LAYOUT.w); svg.setAttribute("height", LAYOUT.h);
      const pos = LAYOUT.pos, inFocus = FOCUSSET;
      let s = `<defs>
        <marker id="rg-ar" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="${C.edge}"/></marker>
        <marker id="rg-ar-hot" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="${C.target}"/></marker></defs>`;

      // level bands
      const lvls = {}; LAYOUT.units.forEach(u => { lvls[u.glevel] = true; });
      Object.keys(lvls).map(Number).sort((a, b) => a - b).forEach(l => {
        const any = LAYOUT.units.find(u => u.glevel === l); if (!any) return;
        const x = pos[any.key].x - 15;
        s += `<rect x="${x}" y="0" width="202" height="${LAYOUT.h}" fill="${C.band}"/>
          <text x="${x + 7}" y="15" font-size="10" font-weight="700" fill="${C.sub}">L${l}</text>`;
      });

      // edges
      LAYOUT.edges.forEach(e => {
        const a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
        const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
        let cls = "rg-edge";
        if (inFocus) cls = (inFocus[e.from] && inFocus[e.to]) ? "rg-edge hot" : "rg-edge dim";
        const mk = cls === "rg-edge hot" ? "url(#rg-ar-hot)" : "url(#rg-ar)";
        s += `<path class="${cls}" d="M${x1},${y1} C${x1 + 48},${y1} ${x2 - 48},${y2} ${x2},${y2}" marker-end="${mk}"/>`;
      });

      // nodes / groups
      LAYOUT.units.forEach(u => {
        const p = pos[u.key];
        const dim = inFocus && !inFocus[u.key];
        const ov = (u.kind === "res" && opt.overlay) ? opt.overlay(u.res) : null;
        const fill = unitFill(u, ov), stroke = unitStroke(u, ov);
        let g = `<g class="rg-unit${dim ? " dimmed" : ""}${ov && ov.pulse ? " rg-active" : ""}" data-key="${esc(u.key)}" style="cursor:pointer">`;
        if (u.kind === "res") {
          const n = u.node;
          const pulse = ov && ov.pulse;
          const sw = pulse ? 3 : (n.is_target ? 2.6 : n.shared ? 2.2 : 1.6);
          const badge = (ov && ov.badge) || (n.is_target ? "★" : n.is_dependent ? "↓" : "");
          const pulseAnim = pulse ? `<animate attributeName="stroke-width" values="3;5;3" dur="1.1s" repeatCount="indefinite"/>` : "";
          g += `<title>${esc(n.id)} — ${esc(n.service)}\nprovenance ${esc(n.provenance)}${n.quota ? "\nquota " + esc(n.quota) : ""}${n.shared ? "\nshared (dedup)" : ""}</title>`;
          g += `<rect class="nrect" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="${sw}">${pulseAnim}</rect>`;
          g += `<text x="${p.x + 26}" y="${p.y + 19}" font-size="12.5" font-weight="700" fill="${C.text}">${n.heavy ? "🜂 " : ""}${esc(n.id)}</text>`;
          g += `<text x="${p.x + 26}" y="${p.y + 35}" font-size="10" fill="${C.sub}">${esc((n.service || "").split("/").pop())}${n.quota ? " ⛔" : ""}</text>`;
          // selection corner control (✓ when target, ＋ otherwise). Click body = focus,
          // click this = toggle target. Only when the caller wires onToggleTarget +
          // the node is selectable.
          if (opt.onToggleTarget && opt.isSelectable && opt.isSelectable(n.id)) {
            const on = n.is_target;
            g += `<g class="rg-selbox" data-sel="${esc(n.id)}">
              <rect x="${p.x + 6}" y="${p.y + p.h / 2 - 8}" width="16" height="16" rx="4" fill="${on ? C.target : "#fff"}" stroke="${on ? C.target : C.sub}" stroke-width="1.3"/>
              <text x="${p.x + 14}" y="${p.y + p.h / 2 + 4}" font-size="${on ? 11 : 13}" font-weight="700" text-anchor="middle" fill="${on ? "#fff" : C.sub}">${on ? "✓" : "+"}</text></g>`;
          } else {
            // non-selectable / no-toggle scenes: a static provenance dot in the corner
            g += `<circle cx="${p.x + 14}" cy="${p.y + p.h / 2}" r="4" fill="${PROV(n.provenance)}"/>`;
          }
          if (badge) g += `<text x="${p.x + p.w - 8}" y="${p.y + 17}" font-size="12" text-anchor="end" fill="${stroke}">${badge}</text>`;
        } else {
          // collapsed group container: chevron + label + count badge. A groupOverlay
          // (흐름) can tint the card + add a progress badge so run-state stays visible
          // even on a collapsed group (run-state PRIMARY on the live DAG).
          const gov = opt.groupOverlay ? opt.groupOverlay(u) : null;
          const accent = catColor(u.cat);
          const gfill = (gov && gov.fill) || fill;
          const gstroke = (gov && gov.stroke) || stroke;
          const nm = u.kind === "cat" ? catLabel(u.cat) : (u.svc || "").split("/").pop();
          const nres = u.members.length;
          const nsvc = u.kind === "cat" ? new Set(u.members.map(id => LAYOUT.byId[id].service)).size : 1;
          const heavyN = u.members.filter(id => LAYOUT.byId[id].heavy).length;
          const quotaN = u.members.filter(id => LAYOUT.byId[id].quota).length;
          const badge = u.kind === "cat" ? `${nsvc} svc · ${nres} res` : `${nres} res`;
          g += `<title>${esc(nm)} — ${nres} 자원 (그룹 클릭 = 펼치기)${gov && gov.title ? "\n" + esc(gov.title) : ""}</title>`;
          g += `<rect class="nrect grp" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="9" fill="${gfill}" stroke="${gstroke}" stroke-width="2"/>`;
          g += `<rect x="${p.x}" y="${p.y}" width="5" height="${p.h}" rx="2" fill="${accent}"/>`;
          g += `<text x="${p.x + 13}" y="${p.y + 21}" font-size="12.5" font-weight="800" fill="${C.text}">▸ ${esc(nm)}</text>`;
          g += `<text x="${p.x + 13}" y="${p.y + 38}" font-size="10" fill="${C.sub}">${esc(badge)}${heavyN ? "  🜂" + heavyN : ""}${quotaN ? "  ⛔" + quotaN : ""}</text>`;
          // right chip: the groupOverlay progress label (e.g. "3/8") if any, else the count
          const chipTxt = (gov && gov.badge) || String(nres);
          const chipW = chipTxt.length > 2 ? 34 : 28;
          g += `<rect x="${p.x + p.w - chipW - 8}" y="${p.y + 9}" width="${chipW}" height="18" rx="9" fill="${gov && gov.chipFill ? gov.chipFill : accent + "22"}" stroke="${gov && gov.chipStroke ? gov.chipStroke : accent}" stroke-width="1"/>`;
          g += `<text x="${p.x + p.w - chipW / 2 - 8}" y="${p.y + 22}" font-size="10" font-weight="700" text-anchor="middle" fill="${gov && gov.chipText ? gov.chipText : accent}">${esc(chipTxt)}</text>`;
        }
        g += `</g>`;
        s += g;
      });
      svg.innerHTML = s;

      // click wiring: selection corner first (stop), then unit body.
      svg.querySelectorAll("g.rg-selbox[data-sel]").forEach(el => el.addEventListener("click", ev => {
        ev.stopPropagation();
        if (opt.onToggleTarget) opt.onToggleTarget(el.dataset.sel);
      }));
      svg.querySelectorAll("g.rg-unit").forEach(g => g.addEventListener("click", ev => {
        ev.stopPropagation(); onUnitClick(g.dataset.key);
      }));
      updateHint();
      updateStat();
      if (opt.onDraw) opt.onDraw();
    }

    // ---- interaction: click a unit ----
    function onUnitClick(key) {
      const u = LAYOUT.byKey[key]; if (!u) return;
      // while focused, clicking OUTSIDE the path clears focus (intuitive escape)
      if (FOCUS && FOCUSSET && !FOCUSSET[key]) { clearFocus(); return; }
      if (u.kind === "cat" || u.kind === "svc") {
        // EXPAND the group (the collapsed card IS a toggle: clicking it expands;
        // the expanded container's header chevron / 전체 접기 collapses it back).
        EXPANDED[key] = true; clearFocusState(); draw(); fit(); return;
      }
      // resource node -> TOGGLE focus (re-clicking the same node clears it)
      if (FOCUS === key) { clearFocus(); return; }
      FOCUS = key; FOCUSSET = computeFocusSet(key); draw();
      if (opt.onFocus) opt.onFocus(focusInfo());
    }
    function clearFocusState() { FOCUS = null; FOCUSSET = null; }
    function clearFocus() { clearFocusState(); draw(); if (opt.onFocus) opt.onFocus(null); }

    function focusInfo() {
      if (!FOCUS) return null;
      const u = LAYOUT.byKey[FOCUS];
      const ids = {};
      LAYOUT.units.forEach(v => { if (FOCUSSET && FOCUSSET[v.key]) v.members.forEach(id => { ids[id] = true; }); });
      return { key: FOCUS, label: u ? (u.kind === "res" ? u.res : u.kind === "cat" ? catLabel(u.cat) : u.svc) : "",
        unitCount: FOCUSSET ? Object.keys(FOCUSSET).length : 0, resourceIds: Object.keys(ids) };
    }

    // ---- collapse / expand-all + granularity ----
    function setGranularity(g) {
      GRAN = g; EXPANDED = {}; clearFocusState(); draw(); fit();
      if (opt.onFocus) opt.onFocus(null);
    }
    function collapseAll() { EXPANDED = {}; clearFocusState(); draw(); fit(); if (opt.onFocus) opt.onFocus(null); }
    function expandAll() {
      // 전체 펼침 = switch to per-resource granularity (every node visible, flat).
      GRAN = "resource"; EXPANDED = {}; clearFocusState(); draw(); fit();
      if (opt.onFocus) opt.onFocus(null);
    }

    // ---- zoom + pan (transform on the svg; stage clips) ----
    function applyT() { svg.style.transformOrigin = "0 0"; svg.style.transform = `translate(${T.x}px,${T.y}px) scale(${T.k})`; updateStat(); }
    const clampK = k => Math.max(0.1, Math.min(2.4, k));
    function zoomAt(cx, cy, factor) {
      const nk = clampK(T.k * factor), r = nk / T.k;
      T.x = cx - (cx - T.x) * r; T.y = cy - (cy - T.y) * r; T.k = nk; applyT();
    }
    function fit() {
      const sw = stage.clientWidth || 800, sh = stage.clientHeight || 520, pad = 34;
      let k = Math.min((sw - pad * 2) / LAYOUT.w, (sh - pad * 2) / LAYOUT.h);
      k = clampK(k); T.k = k;
      T.x = (sw - LAYOUT.w * k) / 2; T.y = (sh - LAYOUT.h * k) / 2;
      if (T.y < pad) T.y = pad;
      applyT();
    }
    function zoomIn() { zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, 1.25); }
    function zoomOut() { zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, 0.8); }

    // ---- hint + stat ----
    function updateHint() {
      const hp = dom.hint; if (!hp) return;
      if (FOCUS) {
        const fi = focusInfo();
        hp.innerHTML = `<span class="focusinfo">focus: ${esc(fi.label)}</span> — 의존 경로 ${fi.unitCount} 단위 강조 · 노드/빈 곳 클릭 = 해제`;
      } else {
        const nExp = Object.keys(EXPANDED).length;
        hp.innerHTML = `<b>${nExp ? nExp + "개 펼침" : "접힌 그래프"}</b> — 그룹 클릭 = 펼치기 · 펼친 그룹 다시 클릭/전체 접기 = 접기 · 노드 클릭 = focus · ＋/✓ = 대상 선택`;
      }
    }
    function updateStat() {
      const sc = dom.stat; if (!sc) return;
      const nUnits = LAYOUT.units.length;
      const nGroups = LAYOUT.units.filter(u => u.kind !== "res").length;
      const nRes = LAYOUT.units.filter(u => u.kind === "res").length;
      const total = (GRAPH.nodes || []).length;
      const maxL = Math.max(0, ...LAYOUT.units.map(u => u.glevel));
      sc.innerHTML = `표시 <b>${nUnits}</b> (그룹 ${nGroups} · 자원 ${nRes}) / 전체 ${total} 자원 · L0–L${maxL} · 줌 <b>${Math.round(T.k * 100)}%</b>`;
      const note = dom.granNote;
      if (note) {
        if (GRAN === "category") note.textContent = `카테고리 단위로 접음 (${total} → ${nGroups || nUnits}). 그룹 클릭으로 펼침.`;
        else if (GRAN === "service") note.textContent = `서비스 단위로 접음 (${total}자원).`;
        else note.textContent = `${total}개 자원 전부 펼침 — 확대·focus로 탐색.`;
      }
    }

    // ---- pointer handlers (drag-to-pan, wheel-zoom) ----
    let drag = null;
    function onWheel(e) {
      e.preventDefault();
      const rect = stage.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.12 : 0.89);
    }
    function onDown(e) {
      if (e.target.closest(".zoomctl")) return;
      drag = { x: e.clientX, y: e.clientY, tx: T.x, ty: T.y, moved: false };
      stage.classList.add("grabbing");
    }
    function onMove(e) {
      if (!drag) return;
      T.x = drag.tx + (e.clientX - drag.x); T.y = drag.ty + (e.clientY - drag.y);
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
      applyT();
    }
    function onUp(e) {
      if (drag) {
        // click empty space (no drag, not on a unit) clears focus
        if (!drag.moved && !e.target.closest("g.rg-unit") && !e.target.closest(".rg-selbox") && FOCUS) clearFocus();
        drag = null; stage.classList.remove("grabbing");
      }
    }

    stage.addEventListener("wheel", onWheel, { passive: false });
    stage.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);

    function destroy() {
      stage.removeEventListener("wheel", onWheel);
      stage.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }

    // pick a sensible initial collapse state: collapse-by-category when large,
    // flat (per-resource) when small — exactly the B2 "기본은 접기" rule.
    function chooseInitial() {
      const n = (GRAPH.nodes || []).length;
      GRAN = n > collapseThreshold ? "category" : "resource";
      EXPANDED = {}; clearFocusState();
    }

    // update the graph (e.g. selection changed, or live overlay refresh). Preserve
    // the user's gran/expanded/focus when the node SET is unchanged (live overlay);
    // re-choose the initial collapse state when the set changes (new selection).
    function update(newData, keepView) {
      const prevIds = new Set((GRAPH.nodes || []).map(n => n.id));
      const hadFocus = !!FOCUS;
      GRAPH = newData || { nodes: [], edges: [] };
      const nextIds = new Set((GRAPH.nodes || []).map(n => n.id));
      const sameSet = prevIds.size === nextIds.size && [...nextIds].every(id => prevIds.has(id));
      if (!keepView && (!started || !sameSet)) {
        // new selection: reset to the collapse-by-default baseline. Notify the caller
        // that focus is cleared so any focus-scoped UI (the 표) resets too.
        chooseInitial(); started = true; draw(); fit();
        if (hadFocus && opt.onFocus) opt.onFocus(null);
      } else {
        // node set unchanged: re-validate focus against the new layout, redraw in place
        if (FOCUS && !GRAPH.nodes.some(n => "res:" + n.id === FOCUS)) { clearFocusState(); if (opt.onFocus) opt.onFocus(null); }
        draw();
      }
    }

    function start() { chooseInitial(); started = true; draw(); fit(); }

    // expose a small controller API. `refresh()` redraws in place (re-evaluating
    // overlay/groupOverlay against current caller state) WITHOUT touching zoom /
    // focus / expand — used by the 흐름 live poll for flicker-free state updates.
    return {
      start, draw, update, destroy, fit,
      refresh: draw,
      setGranularity, collapseAll, expandAll,
      zoomIn, zoomOut, zoomToFit: fit,
      clearFocus, getFocus: focusInfo,
      get gran() { return GRAN; },
      get expandedCount() { return Object.keys(EXPANDED).length; },
      get unitCount() { return LAYOUT.units.length; },
    };
  }

  global.ResourceGraph = { render, load, layout, PROV, transitiveReduction, scene, groupNodes,
    catLabel, catColor, catOf, CAT_ORDER };
})(window);
