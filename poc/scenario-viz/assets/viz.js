/* Shared PoC library — resource-task-model graph + composition helpers.
 * Pure client-side, no deps. Mirrors (a simplified slice of)
 * regression/scenarios/composer.py: closure -> topo order -> reverse teardown.
 * Data: window.MODEL (built by build_data.py). */
(function (global) {
  const M = global.MODEL || { nodes: {}, groups: {} };
  const N = M.nodes;

  const provColor = p => p === "VALIDATED" ? "#3fb27f" : "#e0922f";
  const node = id => N[id];
  const exists = id => Object.prototype.hasOwnProperty.call(N, id);

  // plain AND refs that exist in the model
  function andRefs(id) {
    const n = N[id]; if (!n) return [];
    return (n.and || []).filter(d => exists(d.ref)).map(d => d.ref);
  }
  function andCount(id) {
    const n = N[id]; const m = {};
    (n.and || []).forEach(d => { if (exists(d.ref)) m[d.ref] = d.count || 1; });
    return m;
  }
  // chosen branch ref for each one_of group (default = first existing branch)
  function branchRefs(id, choices) {
    const n = N[id]; if (!n) return [];
    choices = choices || {};
    const out = [];
    (n.one_of || []).forEach((g, gi) => {
      const key = (g.bind || ("g" + gi));
      let pick = choices[id + ":" + key];
      const valid = g.branches.filter(exists);
      if (!pick || !valid.includes(pick)) pick = valid[0];
      if (pick) out.push(pick);
    });
    return out;
  }
  function depRefs(id, choices) { return andRefs(id).concat(branchRefs(id, choices)); }

  // transitive closure of a target set (Set of ids), following AND + chosen branch
  function closure(targets, choices) {
    const seen = new Set();
    const stack = targets.slice();
    while (stack.length) {
      const id = stack.pop();
      if (!exists(id) || seen.has(id)) continue;
      seen.add(id);
      depRefs(id, choices).forEach(r => stack.push(r));
    }
    return seen;
  }

  // longest-path depth within an induced node set
  function depths(setIds, choices) {
    const inSet = id => setIds.has(id);
    const memo = {};
    function d(id, stack) {
      if (id in memo) return memo[id];
      if (stack.has(id)) return 0;
      stack.add(id);
      const deps = depRefs(id, choices).filter(inSet);
      const v = deps.length ? 1 + Math.max(...deps.map(r => d(r, stack))) : 0;
      stack.delete(id); memo[id] = v; return v;
    }
    setIds.forEach(id => d(id, new Set()));
    return memo;
  }

  // topological create order (deps before dependents), deterministic
  function topoOrder(setIds, choices) {
    const dep = depths(setIds, choices);
    return [...setIds].sort((a, b) =>
      (dep[a] - dep[b]) || (N[a].category < N[b].category ? -1 : N[a].category > N[b].category ? 1 : 0)
      || (a < b ? -1 : 1));
  }

  // layered layout -> {nodes:[{id,x,y,w,h,col,depth}], edges:[{from,to,...}], w,h}
  function layout(setIds, choices, opt) {
    opt = opt || {};
    const colGap = opt.colGap || 210, rowGap = opt.rowGap || 64;
    const bw = opt.bw || 168, bh = opt.bh || 44, padX = 26, padY = 26;
    const dep = depths(setIds, choices);
    const cols = {};
    [...setIds].forEach(id => { (cols[dep[id]] = cols[dep[id]] || []).push(id); });
    const maxCol = Math.max(0, ...Object.keys(cols).map(Number));
    const pos = {};
    let maxRows = 0;
    for (let c = 0; c <= maxCol; c++) {
      const ids = (cols[c] || []).sort((a, b) =>
        N[a].category < N[b].category ? -1 : N[a].category > N[b].category ? 1 : (a < b ? -1 : 1));
      maxRows = Math.max(maxRows, ids.length);
      ids.forEach((id, i) => {
        pos[id] = { id, col: c, depth: dep[id], x: padX + c * colGap,
          y: padY + i * rowGap, w: bw, h: bh };
      });
    }
    const edges = [];
    setIds.forEach(id => {
      depRefs(id, choices).forEach(r => {
        if (pos[r]) edges.push({ from: r, to: id });
      });
    });
    const w = padX * 2 + (maxCol) * colGap + bw;
    const h = padY * 2 + Math.max(1, maxRows) * rowGap;
    return { nodes: Object.values(pos), edges, w, h, byId: pos };
  }

  // render a layered DAG into an <svg>; opts: {highlight:Set, shared:Set,
  // targets:Set, onClick:fn(id), choices} -> returns layout
  function renderGraph(svg, setIds, opts) {
    opts = opts || {};
    const choices = opts.choices;
    const L = layout(setIds, choices, opts);
    const NS = "http://www.w3.org/2000/svg";
    svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
    svg.setAttribute("width", L.w); svg.setAttribute("height", L.h);
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML = `<marker id="ar" markerWidth="9" markerHeight="9" refX="8" refY="3"
      orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="#5b7088"/></marker>`;
    svg.appendChild(defs);
    // edges
    L.edges.forEach(e => {
      const a = L.byId[e.from], b = L.byId[e.to];
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", `M${x1},${y1} C${x1 + 50},${y1} ${x2 - 50},${y2} ${x2},${y2}`);
      p.setAttribute("fill", "none");
      const dim = opts.highlight && !(opts.highlight.has(e.from) && opts.highlight.has(e.to));
      p.setAttribute("stroke", dim ? "#2b3b4d" : "#5b7088");
      p.setAttribute("stroke-width", "1.4");
      p.setAttribute("marker-end", "url(#ar)");
      svg.appendChild(p);
    });
    // nodes
    L.nodes.forEach(p => {
      const n = N[p.id];
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", "node");
      const isTarget = opts.targets && opts.targets.has(p.id);
      const isShared = opts.shared && opts.shared.has(p.id);
      const dim = opts.highlight && !opts.highlight.has(p.id);
      const r = document.createElementNS(NS, "rect");
      r.setAttribute("x", p.x); r.setAttribute("y", p.y);
      r.setAttribute("width", p.w); r.setAttribute("height", p.h);
      r.setAttribute("rx", 8);
      r.setAttribute("fill", isTarget ? "#11314f" : "#16212e");
      r.setAttribute("stroke", isShared ? "#ffd166" : isTarget ? "#5aa9ff" : provColor(n.provenance));
      r.setAttribute("stroke-width", isTarget || isShared ? 2.4 : 1.4);
      r.setAttribute("opacity", dim ? .32 : 1);
      g.appendChild(r);
      const t1 = document.createElementNS(NS, "text");
      t1.setAttribute("x", p.x + 10); t1.setAttribute("y", p.y + 18);
      t1.setAttribute("font-size", "12.5"); t1.setAttribute("font-weight", "600");
      t1.setAttribute("opacity", dim ? .4 : 1);
      t1.textContent = (n.heavy ? "🜂 " : "") + p.id + (andCount(p.id), "");
      const mult = []; (N[p.id].and || []).forEach(d => { if (d.count > 1) mult.push("×" + d.count + " " + d.ref); });
      g.appendChild(t1);
      const t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", p.x + 10); t2.setAttribute("y", p.y + 34);
      t2.setAttribute("font-size", "10.5"); t2.setAttribute("fill", "#90a4ba");
      t2.setAttribute("opacity", dim ? .4 : 1);
      t2.textContent = n.service + (n.quota ? "  ⛔" + n.quota : "");
      g.appendChild(t2);
      const title = document.createElementNS(NS, "title");
      title.textContent = `${p.id} — ${n.service}\nprovenance: ${n.provenance}` +
        (n.options.length ? `\noptions: ${n.options.map(o => o.name).join(", ")}` : "") +
        (mult.length ? `\nmultiplicity: ${mult.join(", ")}` : "");
      g.appendChild(title);
      if (opts.onClick) { g.style.cursor = "pointer"; g.onclick = () => opts.onClick(p.id); }
      svg.appendChild(g);
    });
    return L;
  }

  // compose a plan (create order + verify + reverse teardown + dedup + quota)
  function plan(targets, choices) {
    const cl = closure(targets, choices);
    const order = topoOrder(cl, choices);
    const steps = [];
    order.forEach(id => {
      const n = N[id];
      steps.push({ phase: "create", id, action: "create", detail: n.endpoint || "" });
      if (n.ready_timeout) steps.push({ phase: "ready", id, action: "poll ready", detail: `≤${n.ready_timeout}s` });
      for (let i = 0; i < n.verify_n; i++) steps.push({ phase: "verify", id, action: "verify", detail: `verify #${i + 1}` });
    });
    [...order].reverse().forEach(id => {
      if (N[id].has_delete) steps.push({ phase: "delete", id, action: "delete", detail: "reverse teardown" });
    });
    // dedup: nodes shared across >1 target's individual closure
    const perTarget = targets.map(t => closure([t], choices));
    const sharedCount = {};
    cl.forEach(id => { sharedCount[id] = perTarget.filter(s => s.has(id)).length; });
    const shared = new Set([...cl].filter(id => sharedCount[id] > 1));
    const quota = {};
    cl.forEach(id => { const q = N[id].quota; if (q) quota[q] = (quota[q] || 0) + 1; });
    const naive = perTarget.reduce((a, s) => a + s.size, 0);
    return { closure: cl, order, steps, shared, quota,
      dedup_saved: naive - cl.size, naive_creates: naive, deduped_creates: cl.size,
      heavy: order.some(id => N[id].heavy) };
  }

  global.VIZ = {
    M, N, node, exists, andRefs, andCount, branchRefs, depRefs,
    closure, depths, topoOrder, layout, renderGraph, plan, provColor,
    allIds: () => Object.keys(N),
    // direct dependents: nodes whose requires reference `id`
    dependents: (id, choices) => Object.keys(N).filter(o => depRefs(o, choices).includes(id)),
    // depth-grouped levels for a node set (level k = all nodes at topo depth k)
    levels(setIds, choices) {
      const dep = depths(setIds, choices); const lv = {};
      setIds.forEach(id => { (lv[dep[id]] = lv[dep[id]] || []).push(id); });
      return Object.keys(lv).map(Number).sort((a, b) => a - b)
        .map(d => ({ depth: d, ids: lv[d].sort() }));
    },
    // deterministic simulated durations (Run + Report share these, in seconds)
    dur(id) {
      const n = N[id];
      const create = n.heavy ? 20 : 5;
      const ready = n.ready_timeout ? Math.round(n.ready_timeout * 0.5) : (n.heavy ? 45 : 0);
      const verify = (n.verify_n || 0) * 3;
      const del = n.heavy ? 15 : 8;
      return { create, ready, verify, del, total: create + ready + verify };
    },
    targetsOnly: () => Object.keys(N),
  };
})(window);
