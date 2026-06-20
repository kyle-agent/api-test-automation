"""Offline tests for the dag_plan_graph topological-preview renderer.

Hermetic: a small SYNTHETIC ``Plan`` (built by hand) drives the unit cases for
render_svg/render_html structure + well-formedness, plus ONE integration case on
the REAL full plan (dag_planner.plan()). No client, no network.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from regression.scenarios import dag_plan_graph, dag_planner
from regression.scenarios.dag_planner import Plan, Wave

# synthetic deps: just enough for parent edges + adopt edges.
SYNTH_DEPS = {
    "shared_roots": {
        "vpc": {"parent": None},
        "subnet": {"parent": "vpc"},
        "subnet#db": {"parent": "vpc"},
    },
    "adopt_edges": {
        "adopter-vs": ["subnet", "vpc"],
        "adopter-db": ["subnet#db", "vpc"],
    },
}


def _synthetic_plan() -> Plan:
    return Plan(
        leaf_set=["adopter-vs", "adopter-db", "free-a", "free-b", "self-vpc-a"],
        shared_roots=["vpc", "subnet", "subnet#db"],
        adopters=["adopter-db", "adopter-vs"],
        self_creators={"self-vpc-a": ["vpc"]},
        free=["free-a", "free-b", "free-c"],
        vpc_cap=5,
        shared_vpc_count=1,
        waves=[
            Wave(kind="provision", lifecycles=["vpc", "subnet", "subnet#db"],
                 vpc_slots=1),
            Wave(kind="free", lifecycles=["free-a", "free-b", "free-c"]),
            Wave(kind="adopt", lifecycles=["adopter-db", "adopter-vs"]),
            Wave(kind="self-create", lifecycles=["self-vpc-a"], vpc_slots=1),
        ],
    )


# --------------------------------------------------------------------------- #
# render_svg structure
# --------------------------------------------------------------------------- #
def test_render_svg_contains_svg_root_and_wave_labels():
    svg = dag_plan_graph.render_svg(_synthetic_plan(), deps=SYNTH_DEPS)
    assert isinstance(svg, str)
    assert "<svg" in svg and svg.rstrip().endswith("</svg>")
    # every wave-kind label is present
    for kind in ("provision", "free", "adopt", "self-create"):
        assert kind in svg


def test_render_svg_contains_root_names():
    svg = dag_plan_graph.render_svg(_synthetic_plan(), deps=SYNTH_DEPS)
    for root in ("vpc", "subnet", "subnet#db"):
        assert root in svg


def test_render_svg_has_adopt_and_parent_edges():
    svg = dag_plan_graph.render_svg(_synthetic_plan(), deps=SYNTH_DEPS)
    # at least one line element overall, and specifically an adopt edge.
    assert "<line" in svg
    assert "adopt-edge" in svg
    assert "parent-edge" in svg


def test_render_svg_balanced_and_xml_parseable():
    svg = dag_plan_graph.render_svg(_synthetic_plan(), deps=SYNTH_DEPS)
    # balanced single <svg>...</svg>
    assert svg.count("<svg") == 1
    assert svg.count("</svg>") == 1
    # the svg fragment parses as XML
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")


def test_free_layer_rendered_as_compact_grid_not_labelled_boxes():
    # build a plan with many free leaves; assert we don't emit a named box per
    # free leaf (the grid uses <title> for ids, summarized text otherwise).
    free = [f"free-{i:03d}" for i in range(157)]
    p = Plan(
        leaf_set=list(free),
        shared_roots=[],
        free=list(free),
        waves=[Wave(kind="free", lifecycles=list(free))],
    )
    svg = dag_plan_graph.render_svg(p, deps={})
    # summary line present
    assert "157 lifecycles" in svg
    # ids carried as hover titles, not as <text> node labels
    assert svg.count("<title>") >= 157
    # no per-free <text class="node"> labels (grid cells are unlabelled)
    assert 'class="node">free-' not in svg


# --------------------------------------------------------------------------- #
# render_html
# --------------------------------------------------------------------------- #
def test_render_html_is_self_contained_doc_with_legend_no_refresh():
    html = dag_plan_graph.render_html(_synthetic_plan(), deps=SYNTH_DEPS)
    assert "<!doctype html>" in html.lower()
    assert "DAG Plan — topological preview" in html
    assert "<svg" in html
    # legend names every node + edge kind
    for term in ("root", "adopter", "self-creator", "free",
                 "adopt edge", "parent edge"):
        assert term in html
    # header summary numbers
    assert "vpc cap 5" in html
    assert "self-create budget 4" in html
    # static preview: NO meta-refresh, no external asset references
    assert "http-equiv" not in html.lower()
    assert "refresh" not in html.lower()
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html


def test_render_html_svg_fragment_parses():
    html = dag_plan_graph.render_html(_synthetic_plan(), deps=SYNTH_DEPS)
    m = re.search(r"<svg.*?</svg>", html, re.DOTALL)
    assert m, "no <svg> fragment in HTML"
    root = ET.fromstring(m.group(0))
    assert root.tag.endswith("svg")


# --------------------------------------------------------------------------- #
# integration: the REAL full plan
# --------------------------------------------------------------------------- #
def test_render_html_on_real_full_plan():
    p = dag_planner.plan()
    html = dag_plan_graph.render_html(p)
    assert isinstance(html, str) and html.strip()
    for kind in ("provision", "free", "adopt", "self-create"):
        assert kind in html
    for root in ("vpc", "subnet", "subnet#db"):
        assert root in html
    # the svg fragment of the real plan is XML-parseable too
    m = re.search(r"<svg.*?</svg>", html, re.DOTALL)
    assert m
    ET.fromstring(m.group(0))
