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
 *   ResourceGraph.render(svgEl, data, {onClick, overlay}) -> layout
 *
 * data = {nodes:[{id,service,provenance,quota,heavy,options,level,is_target,
 *                 shared,is_dependent}], edges:[{from,to}]}
 * overlay(id) -> {fill,stroke,badge} | null   (per-node status/result colors) */
(function (global) {
  // LIGHT palette (Primer)
  const C = {
    fillPlain: "#ffffff", fillTarget: "#e6effd", fillDep: "#f3eefc", fillShared: "#fffaf0",
    text: "#1f2328", sub: "#656d76",
    target: "#2563c9", shared: "#b5740b", dependent: "#8250df",
    val: "#2da44e", docs: "#b5740b",
    edge: "#8a93a0", band: "#2563c91a", bandText: "#656d76",
  };
  const PROV = p => (p === "VALIDATED" ? C.val : C.docs);

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
    const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
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
      const sw = n.is_target ? 2.6 : (n.shared ? 2.2 : 1.6);
      const badge = (ov && ov.badge) || (n.is_target ? "★" : n.is_dependent ? "↓" : "");
      s += `<g class="rg-node" data-id="${esc(n.id)}" style="cursor:${opt.onClick ? "pointer" : "default"}">
        <title>${esc(n.id)} — ${esc(n.service)}\nprovenance ${esc(n.provenance)}${n.quota ? "\nquota " + esc(n.quota) : ""}${n.shared ? "\nshared (dedup)" : ""}${(n.options || []).length ? "\noptions: " + esc((n.options || []).join(", ")) : ""}</title>
        <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>
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

  global.ResourceGraph = { render, load, layout, PROV, transitiveReduction };
})(window);
