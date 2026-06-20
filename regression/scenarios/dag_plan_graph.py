"""dag_plan_graph — render a dag_planner ``Plan`` as a self-contained HTML/SVG
*topological preview*, so the FULL test-run schedule can be eyeballed before any
run.

This is a **planning view**, not a live monitor: it is pure offline rendering of
the offline ``dag_planner.plan`` output (no client, no network, no credentials,
no meta-refresh). It lays the plan out as topological LAYERS, top-to-bottom in
wave order:

  * **provision** — the shared roots as boxes, with parent edges (``vpc`` on top;
    ``subnet`` / ``subnet#db`` below, a line up to their ``vpc`` parent).
  * **free**       — the ~157 VPC-independent leaves. We do NOT draw 157 readable
    labelled boxes; they render as a compact GRID of small unlabelled cells (each
    with a hover ``<title>`` of its lifecycle id) plus a one-line summary, so the
    page stays legible.
  * **adopt**      — the adopters as boxes, each with a thin DASHED edge UP to
    every shared root it adopts (from ``deps['adopt_edges']``), exposing the
    dependency structure.
  * **self-create**— one labelled band per self-create wave, its lifecycles as
    boxes, annotated with the wave's ``vpc_slots`` against the cap.

Colors are muted/clean. A small legend names the node + edge kinds.

CLI::

    python -m regression.scenarios.dag_plan_graph [--service X] [--vpc-cap N] [-o OUT.html]

builds the plan (reusing ``dag_planner.plan`` + ``_service_leaf_set`` for
``--service``) and writes the HTML to ``OUT`` (default stdout).
"""
from __future__ import annotations

import argparse
import sys
from html import escape
from math import ceil

from regression.scenarios import dag_planner, validate_dag

# --------------------------------------------------------------------------- #
# layout constants (a planning view: legible over pixel-perfect)
# --------------------------------------------------------------------------- #
_WIDTH = 1180
_MARGIN_L = 200          # left gutter for the per-layer kind+count label
_MARGIN_R = 40
_PAD_TOP = 28
_BOX_H = 30
_BOX_W = 150             # default box width for named lifecycles
_GAP_X = 14              # horizontal gap between boxes
_GAP_Y = 18              # vertical gap between boxes within a wrapped band
_LAYER_GAP = 56          # vertical gap between layers
_FREE_CELL = 11          # side of a small unlabelled free cell
_FREE_GAP = 3            # gap between free cells

# muted palette keyed by node role.
_FILL = {
    "root": "#d7e3f4",
    "adopter": "#dce9dc",
    "self-creator": "#f3e6d2",
    "free": "#e6e6ea",
}
_STROKE = {
    "root": "#4a6fa5",
    "adopter": "#5a8a5a",
    "self-creator": "#b08438",
    "free": "#9a9aa6",
}
_LABEL = {
    "provision": "root",
    "adopt": "adopter",
    "self-create": "self-creator",
    "free": "free",
}


# --------------------------------------------------------------------------- #
# tiny SVG helpers
# --------------------------------------------------------------------------- #
def _box(x, y, w, h, role, label=None, title=None):
    fill = _FILL.get(role, "#eeeeee")
    stroke = _STROKE.get(role, "#888888")
    parts = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="4" ry="4" fill="{fill}" stroke="{stroke}" stroke-width="1.2">'
    ]
    if title:
        parts.append(f"<title>{escape(title)}</title>")
    parts.append("</rect>")
    if label:
        tx = x + w / 2
        ty = y + h / 2 + 4
        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
            f'class="node">{escape(_truncate(label, w))}</text>'
        )
    return "".join(parts)


def _truncate(text, box_w):
    # ~6.4px per char at 11px monospace-ish; keep it inside the box.
    maxc = max(4, int((box_w - 10) / 6.4))
    return text if len(text) <= maxc else text[: maxc - 1] + "…"


def _layer_label(x, y, kind, count):
    role = _LABEL.get(kind, kind)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="end" class="layer-kind">'
        f"{escape(kind)}</text>"
        f'<text x="{x:.1f}" y="{y + 16:.1f}" text-anchor="end" class="layer-count">'
        f"{count} · {escape(role)}</text>"
    )


# --------------------------------------------------------------------------- #
# per-layer renderers — each returns (svg_fragment, new_y, box_centers)
# box_centers maps a lifecycle/root id -> (cx, cy_top, cy_bottom) for edges.
# --------------------------------------------------------------------------- #
def _content_x():
    return _MARGIN_L + 20


def _content_w():
    return _WIDTH - _content_x() - _MARGIN_R


def _wrap_boxes(ids, y, role, box_w=_BOX_W):
    """Lay ``ids`` as a left-aligned wrapped band of named boxes. Returns
    (svg, bottom_y, centers) where centers[id]=(cx, top_y, bottom_y)."""
    x0 = _content_x()
    avail = _content_w()
    per_row = max(1, int((avail + _GAP_X) // (box_w + _GAP_X)))
    svg = []
    centers = {}
    cur_y = y
    for i, lid in enumerate(ids):
        col = i % per_row
        if col == 0 and i:
            cur_y += _BOX_H + _GAP_Y
        x = x0 + col * (box_w + _GAP_X)
        svg.append(_box(x, cur_y, box_w, _BOX_H, role, label=lid, title=lid))
        centers[lid] = (x + box_w / 2, cur_y, cur_y + _BOX_H)
    bottom = cur_y + _BOX_H
    return "".join(svg), bottom, centers


def _render_provision(roots, deps, y):
    """Roots as boxes with parent edges (vpc on top, children below, lines up)."""
    shared_meta = (deps or {}).get("shared_roots", {})
    x0 = _content_x()
    svg = []
    centers = {}
    # split into parent-less (top) and children (below) by parent chain.
    tops = [r for r in roots if not (shared_meta.get(r) or {}).get("parent")]
    children = [r for r in roots if (shared_meta.get(r) or {}).get("parent")]

    box_w = 130
    # top row
    top_y = y
    for i, r in enumerate(tops):
        x = x0 + i * (box_w + _GAP_X)
        svg.append(_box(x, top_y, box_w, _BOX_H, "root", label=r, title=r))
        centers[r] = (x + box_w / 2, top_y, top_y + _BOX_H)
    child_y = top_y + (_BOX_H + 34 if tops else 0)
    for i, r in enumerate(children):
        x = x0 + i * (box_w + _GAP_X)
        svg.append(_box(x, child_y, box_w, _BOX_H, "root", label=r, title=r))
        centers[r] = (x + box_w / 2, child_y, child_y + _BOX_H)
    # parent edges: child top -> parent bottom
    for r in children:
        parent = (shared_meta.get(r) or {}).get("parent")
        if parent in centers:
            cx, ctop, _ = centers[r]
            px, _, pbot = centers[parent]
            svg.append(
                f'<line x1="{cx:.1f}" y1="{ctop:.1f}" x2="{px:.1f}" y2="{pbot:.1f}" '
                f'class="parent-edge"/>'
            )
    bottom = (child_y if children else top_y) + _BOX_H
    return "".join(svg), bottom, centers


def _render_free(ids, y):
    """Compact grid of small unlabelled cells + a one-line summary."""
    x0 = _content_x()
    avail = _content_w()
    per_row = max(1, int((avail + _FREE_GAP) // (_FREE_CELL + _FREE_GAP)))
    svg = []
    # summary line above the grid
    svg.append(
        f'<text x="{x0:.1f}" y="{y - 6:.1f}" class="summary">'
        f"free · {len(ids)} lifecycles · fully parallel (no shared-root dependency)"
        f"</text>"
    )
    for i, lid in enumerate(ids):
        col = i % per_row
        row = i // per_row
        x = x0 + col * (_FREE_CELL + _FREE_GAP)
        yy = y + row * (_FREE_CELL + _FREE_GAP)
        svg.append(
            f'<rect x="{x:.1f}" y="{yy:.1f}" width="{_FREE_CELL}" '
            f'height="{_FREE_CELL}" rx="2" fill="{_FILL["free"]}" '
            f'stroke="{_STROKE["free"]}" stroke-width="0.7">'
            f"<title>{escape(lid)}</title></rect>"
        )
    rows = ceil(len(ids) / per_row) if ids else 0
    bottom = y + rows * (_FREE_CELL + _FREE_GAP)
    return "".join(svg), bottom


def _render_self_create(wave, idx, y, cap):
    """One labelled band, lifecycles as boxes, annotated with vpc_slots vs cap."""
    x0 = _content_x()
    note = (
        f"self-create wave {idx} · {wave.vpc_slots} VPC slot(s)"
        f"{f' ≤ cap {cap}' if cap else ''} · {len(wave.lifecycles)} lifecycle(s)"
    )
    svg = [
        f'<text x="{x0:.1f}" y="{y - 6:.1f}" class="summary">{escape(note)}</text>'
    ]
    body, bottom, centers = _wrap_boxes(wave.lifecycles, y, "self-creator")
    svg.append(body)
    return "".join(svg), bottom, centers


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def render_svg(plan, deps=None) -> str:
    """Render ``plan`` as a self-contained ``<svg>...</svg>`` topological layout."""
    if deps is None:
        try:
            deps = validate_dag._load_deps()
        except Exception:
            deps = {}
    adopt_edges = (deps or {}).get("adopt_edges", {})

    body = []
    y = _PAD_TOP
    root_centers: dict = {}

    sc_idx = 0
    for wave in plan.waves:
        label_y = y + 4
        if wave.kind == "provision":
            frag, bottom, centers = _render_provision(wave.lifecycles, deps, y)
            root_centers.update(centers)
            body.append(_layer_label(_MARGIN_L, label_y, wave.kind,
                                     len(wave.lifecycles)))
            body.append(frag)
            y = bottom + _LAYER_GAP
        elif wave.kind == "free":
            grid_y = y + 14  # leave room for the summary line
            frag, bottom = _render_free(wave.lifecycles, grid_y)
            body.append(_layer_label(_MARGIN_L, label_y, wave.kind,
                                     len(wave.lifecycles)))
            body.append(frag)
            y = bottom + _LAYER_GAP
        elif wave.kind == "adopt":
            frag, bottom, centers = _wrap_boxes(wave.lifecycles, y, "adopter")
            body.append(_layer_label(_MARGIN_L, label_y, wave.kind,
                                     len(wave.lifecycles)))
            # dashed adopt edges UP to each adopted shared root
            edges = []
            for lid, (cx, top_y, _) in centers.items():
                for root in adopt_edges.get(lid, []):
                    if root in root_centers:
                        rx, _, rbot = root_centers[root]
                        edges.append(
                            f'<line x1="{cx:.1f}" y1="{top_y:.1f}" '
                            f'x2="{rx:.1f}" y2="{rbot:.1f}" class="adopt-edge"/>'
                        )
            body.append("".join(edges))  # edges under boxes
            body.append(frag)
            y = bottom + _LAYER_GAP
        elif wave.kind == "self-create":
            band_y = y + 14
            frag, bottom, _ = _render_self_create(wave, sc_idx, band_y,
                                                  plan.vpc_cap)
            sc_idx += 1
            body.append(_layer_label(_MARGIN_L, label_y, wave.kind,
                                     len(wave.lifecycles)))
            body.append(frag)
            y = bottom + _LAYER_GAP
        else:  # unknown kind — render as plain named boxes, fail-soft
            frag, bottom, _ = _wrap_boxes(wave.lifecycles, y, "free")
            body.append(_layer_label(_MARGIN_L, label_y, wave.kind,
                                     len(wave.lifecycles)))
            body.append(frag)
            y = bottom + _LAYER_GAP

    height = int(y + 10)
    css = (
        ".node{font:11px ui-monospace,Menlo,Consolas,monospace;fill:#1d2733;}"
        ".layer-kind{font:600 14px system-ui,sans-serif;fill:#243b53;}"
        ".layer-count{font:11px system-ui,sans-serif;fill:#627d98;}"
        ".summary{font:11px system-ui,sans-serif;fill:#52606d;}"
        ".parent-edge{stroke:#4a6fa5;stroke-width:1.4;}"
        ".adopt-edge{stroke:#5a8a5a;stroke-width:0.9;stroke-dasharray:4 3;opacity:0.7;}"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" '
        f'height="{height}" viewBox="0 0 {_WIDTH} {height}" '
        f'font-family="system-ui,sans-serif">'
        f"<style>{css}</style>"
        f'<rect x="0" y="0" width="{_WIDTH}" height="{height}" fill="#fbfcfe"/>'
        + "".join(body)
        + "</svg>"
    )


def _legend_html() -> str:
    def swatch(role, text):
        return (
            f'<span class="lg"><span class="sw" style="background:{_FILL[role]};'
            f'border-color:{_STROKE[role]}"></span>{escape(text)}</span>'
        )

    return (
        '<div class="legend">'
        + swatch("root", "root")
        + swatch("adopter", "adopter")
        + swatch("self-creator", "self-creator")
        + swatch("free", "free")
        + '<span class="lg"><span class="edge parent"></span>parent edge</span>'
        + '<span class="lg"><span class="edge adopt"></span>adopt edge</span>'
        + "</div>"
    )


def render_html(plan, deps=None) -> str:
    """Wrap ``render_svg`` in a minimal, self-contained HTML doc (no refresh)."""
    if deps is None:
        try:
            deps = validate_dag._load_deps()
        except Exception:
            deps = {}
    svg = render_svg(plan, deps=deps)

    n_waves = len(plan.waves)
    roots = ", ".join(plan.shared_roots) or "(none)"
    summary = (
        f"{len(plan.leaf_set)} leaves · {n_waves} waves · "
        f"roots: {roots} · vpc cap {plan.vpc_cap} · "
        f"self-create budget {plan.self_create_budget} (= cap "
        f"{plan.vpc_cap} − shared {plan.shared_vpc_count})"
    )

    css = (
        "body{margin:0;background:#eef1f5;color:#243b53;"
        "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;}"
        "header{padding:18px 24px 10px;}"
        "h1{margin:0 0 6px;font-size:18px;}"
        ".sub{color:#52606d;font-size:13px;}"
        ".legend{margin:10px 0 4px;display:flex;flex-wrap:wrap;gap:14px;"
        "align-items:center;font-size:12px;color:#52606d;}"
        ".lg{display:inline-flex;align-items:center;gap:6px;}"
        ".sw{width:14px;height:14px;border-radius:3px;border:1px solid #888;"
        "display:inline-block;}"
        ".edge{width:22px;height:0;display:inline-block;}"
        ".edge.parent{border-top:2px solid #4a6fa5;}"
        ".edge.adopt{border-top:2px dashed #5a8a5a;}"
        ".canvas{padding:8px 24px 32px;overflow:auto;}"
        ".canvas svg{background:#fff;border:1px solid #d9e2ec;border-radius:8px;"
        "box-shadow:0 1px 3px rgba(0,0,0,.06);}"
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>DAG Plan — topological preview</title>"
        f"<style>{css}</style></head><body>"
        "<header><h1>DAG Plan — topological preview</h1>"
        f'<div class="sub">{escape(summary)}</div>'
        + _legend_html()
        + "</header>"
        f'<div class="canvas">{svg}</div>'
        "</body></html>"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a dag_planner Plan as a self-contained HTML/SVG "
                    "topological preview (offline; renders nothing live).")
    ap.add_argument("--service", help="restrict the leaf set to one service "
                    "(full 'category/name' or trailing 'name' segment)")
    ap.add_argument("--vpc-cap", type=int, default=None,
                    help="override the account VPC cap (default = vpc_limit)")
    ap.add_argument("-o", "--out", default=None,
                    help="output HTML path (default: stdout)")
    args = ap.parse_args(argv)

    deps = validate_dag._load_deps()
    lifecycles = validate_dag._load_lifecycles()

    leaf_set = None
    if args.service:
        leaf_set = dag_planner._service_leaf_set(args.service, lifecycles)
        if not leaf_set:
            print(f"no enabled lifecycle matches service '{args.service}'",
                  file=sys.stderr)
            return 1

    p = dag_planner.plan(leaf_set=leaf_set, deps=deps, lifecycles=lifecycles,
                         vpc_cap=args.vpc_cap)
    html = render_html(p, deps=deps)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"wrote {args.out} ({len(html)} bytes)", file=sys.stderr)
    else:
        print(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
