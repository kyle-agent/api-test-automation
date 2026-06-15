/* Shared resource-graph renderer (R-platform P0).
 * Consumes the /planning/resources/graph.json contract (composer.graph_view /
 * focus_view) and draws a layered dependency DAG into an <svg>. No build, no
 * deps — works offline. If window.cytoscape is present it can be swapped in
 * later for the interactive screens; this SVG path is the static/default one.
 *
 *   ResourceGraph.render(svgEl, data, {onClick, overlay}) -> layout
 *
 * data = {nodes:[{id,service,provenance,quota,heavy,level,is_target,shared,
 *                 is_dependent}], edges:[{from,to}], focus?}
 * overlay(id) -> {fill,stroke,badge} | null   (per-node status/result colors)
 */
(function (global) {
  const PROV = p => (p === "VALIDATED" ? "#3fb27f" : "#e0922f");

  function layout(data, opt) {
    const colGap = opt.colGap || 200, rowGap = opt.rowGap || 56;
    const bw = opt.bw || 168, bh = opt.bh || 44, padX = 24, padY = 24;
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
    const L = layout(data, opt);
    svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
    svg.setAttribute("width", L.w); svg.setAttribute("height", L.h);
    const esc = s => (s + "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
    let s = `<defs><marker id="rg-ar" markerWidth="9" markerHeight="9" refX="8" refY="3"
      orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="#5b7088"/></marker></defs>`;
    // level bands
    const lvls = [...new Set(data.nodes.map(n => n.level))].sort((a, b) => a - b);
    lvls.forEach(l => {
      const any = data.nodes.find(n => n.level === l);
      if (!any) return;
      const x = L.pos[any.id].x - 12;
      s += `<rect x="${x}" y="0" width="${(opt.bw || 168) + 24}" height="${L.h}" fill="#10314d18"/>
        <text x="${x + 6}" y="14" font-size="10" fill="#5b7088">L${l}</text>`;
    });
    data.edges.forEach(e => {
      const a = L.pos[e.from], b = L.pos[e.to]; if (!a || !b) return;
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
      s += `<path d="M${x1},${y1} C${x1 + 46},${y1} ${x2 - 46},${y2} ${x2},${y2}"
        fill="none" stroke="#4b5e72" stroke-width="1.3" marker-end="url(#rg-ar)"/>`;
    });
    data.nodes.forEach(n => {
      const p = L.pos[n.id];
      const ov = opt.overlay ? opt.overlay(n.id) : null;
      const fill = (ov && ov.fill) || (n.is_target ? "#11314f" : n.is_dependent ? "#241a33" : "#1c2a3a");
      const stroke = (ov && ov.stroke) || (n.shared ? "#ffd166"
        : n.is_target ? "#5aa9ff" : n.is_dependent ? "#b48cff" : PROV(n.provenance));
      const sw = n.is_target ? 2.6 : (n.shared ? 2.2 : 1.5);
      const badge = (ov && ov.badge) || (n.is_target ? "★" : n.is_dependent ? "↓" : "");
      s += `<g class="rg-node" data-id="${n.id}" style="cursor:${opt.onClick ? "pointer" : "default"}">
        <title>${esc(n.id)} — ${esc(n.service)}\nprovenance ${esc(n.provenance)}${n.quota ? "\nquota " + esc(n.quota) : ""}${(n.options || []).length ? "\noptions: " + esc((n.options || []).join(", ")) : ""}</title>
        <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>
        <text x="${p.x + 9}" y="${p.y + 18}" font-size="12.5" font-weight="600" fill="#e7eef6">${n.heavy ? "🜂 " : ""}${esc(n.id)}</text>
        <text x="${p.x + 9}" y="${p.y + 33}" font-size="10" fill="#90a4ba">${esc((n.service || "").split("/").pop())}${n.quota ? " ⛔" + esc(n.quota) : ""}</text>
        ${badge ? `<text x="${p.x + p.w - 8}" y="${p.y + 17}" font-size="12" text-anchor="end">${badge}</text>` : ""}
      </g>`;
    });
    svg.innerHTML = s;
    if (opt.onClick) svg.querySelectorAll("g.rg-node").forEach(g =>
      g.addEventListener("click", () => opt.onClick(g.dataset.id)));
    return L;
  }

  // fetch + render helper for the common case
  async function load(svg, url, opt) {
    const r = await fetch(url);
    const data = await r.json();
    if (data.error) { svg.innerHTML = `<text x="12" y="24" fill="#ff8585">${data.error}</text>`; return null; }
    render(svg, data, opt);
    return data;
  }

  global.ResourceGraph = { render, load, layout, PROV };
})(window);
