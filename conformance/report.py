"""AXIS 2 — consolidated conformance MASTER_REPORT (static + runtime).

Ports ``tools/build_findings_report.py``: where the legacy tool rendered the
uncapped STATIC finding lists into ``reports/findings/*.md`` from
``data/findings.json`` + ``data/validation_findings.json``, this module produces a
single **consolidated** report that merges *both* lenses — static design/doc
findings AND runtime behaviour findings — prioritised by severity.

It reads the unified results store (:func:`core.results.load_findings`, both
``source="static"`` and ``source="runtime"``) and the per-endpoint conformance
roll-up (``data/conformance.json``, incl. the systemic issues), and assembles an
in-memory ``MASTER_REPORT`` dict. It can also render that to Markdown
(:func:`render_markdown`) and dual-write the legacy ``reports/findings/*.md``
breakdowns + index (:func:`write_legacy_breakdowns`) so existing links keep
working.

Read-only analysis: no network I/O. The only writes are report artifacts under
``reports/findings/`` and they happen only inside the explicit write functions /
``main()``.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from core.results import load_findings

ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "data"
R = ROOT / "reports"
CONFORMANCE = F / "conformance.json"
FINDINGS_JSON = F / "findings.json"
VALIDATION_JSON = F / "validation_findings.json"
OUTDIR = R / "findings"

# severity ordering for prioritisation (most severe first)
_SEV_ORDER = {"red": 0, "yellow": 1, "green": 2}

BANNER = ("> **Consolidated conformance report** — STATIC design/doc findings + "
          "RUNTIME behaviour findings, prioritised by severity.\n")


def _load(p: Path, default):
    p = Path(p)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except ValueError:
        return default


def _sev_key(sev: str) -> int:
    return _SEV_ORDER.get(sev, 1)


def build_master_report(*, findings: list[dict] | None = None,
                        conformance: dict | None = None) -> dict:
    """Assemble the consolidated MASTER_REPORT from the unified results store.

    Args:
        findings: unified Findings (defaults to ``core.results.load_findings()``).
        conformance: the legacy per-endpoint roll-up (defaults to loading
            ``data/conformance.json``); supplies the systemic issues + status mix.

    Returns a dict with::

        {
          "summary": {"total", "by_severity", "by_source", "endpoints"},
          "systemic": [...],                 # from conformance.json
          "findings": [ {sorted finding} ],  # red first, then yellow, green
          "by_endpoint": { key: [findings...] },
        }
    """
    findings = list(findings if findings is not None else load_findings())
    conformance = conformance if conformance is not None else _load(CONFORMANCE, {})

    by_sev: Counter = Counter()
    by_source: Counter = Counter()
    by_endpoint: dict[str, list] = defaultdict(list)
    for f in findings:
        by_sev[f.get("severity", "yellow")] += 1
        by_source[f.get("source", "static")] += 1
        by_endpoint[f.get("endpoint_key", "")].append(f)

    ordered = sorted(
        findings,
        key=lambda f: (_sev_key(f.get("severity", "yellow")),
                       f.get("source", ""), f.get("endpoint_key", ""),
                       f.get("rule_id", "")),
    )

    return {
        "summary": {
            "total": len(findings),
            "by_severity": dict(by_sev),
            "by_source": dict(by_source),
            "endpoints": len(by_endpoint),
        },
        "systemic": conformance.get("systemic", []),
        "findings": ordered,
        "by_endpoint": {k: v for k, v in by_endpoint.items()},
    }


def render_markdown(report: dict) -> str:
    """Render the consolidated MASTER_REPORT to a single Markdown document."""
    s = report["summary"]
    lines = ["# Conformance — consolidated findings (master report)", "", BANNER, ""]
    lines += [
        "## Summary", "",
        f"- total findings: **{s['total']}** across **{s['endpoints']}** endpoints",
        f"- by severity: {s['by_severity']}",
        f"- by source: {s['by_source']}",
        "",
    ]

    if report.get("systemic"):
        lines += ["## Systemic issues", "",
                  "| Type | Issue | Scope | Count | Detail |",
                  "|---|---|---|---|---|"]
        for it in report["systemic"]:
            lines.append(
                f"| {it.get('type')} | {it.get('issue')} | {it.get('scope')} "
                f"| {it.get('count')} | {it.get('detail')} |")
        lines.append("")

    lines += ["## Findings (severity-prioritised)", "",
              "| Severity | Source | Endpoint | Rule | Detail |",
              "|---|---|---|---|---|"]
    for f in report["findings"]:
        lines.append(
            f"| {f.get('severity')} | {f.get('source')} | `{f.get('endpoint_key')}` "
            f"| {f.get('rule_id')} | {f.get('detail')} |")
    lines.append("")
    return "\n".join(lines)


def _w(name: str, title: str, lines: list[str]) -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"{name}.md").write_text(
        f"# {title}\n\n{BANNER}\n" + "\n".join(lines) + "\n", encoding="utf-8")


def write_legacy_breakdowns() -> None:
    """Dual-write the legacy uncapped STATIC breakdowns to ``reports/findings/``.

    Faithful port of ``tools/build_findings_report.py`` so any existing links to
    the per-group ``.md`` files keep resolving. Driven by ``data/findings.json``
    + ``data/validation_findings.json`` (the dual-written STATIC artifacts).
    """
    f = _load(FINDINGS_JSON, {})
    if not f:
        return
    OUTDIR.mkdir(parents=True, exist_ok=True)

    er = f.get("no-error-response-schema", {}).get("items", {})
    if er:
        _w("no-error-response-schema",
           "Endpoints whose 4xx/5xx responses document no schema",
           [f"{er.get('endpoints_without_any_error_schema', 0)} of "
            f"{er.get('endpoints_without_any_error_schema', 0) + er.get('endpoints_with_some_error_schema', 0)} "
            "endpoints document no error body schema. Full list:", ""]
           + [f"- `{k}`" for k in er.get("full", er.get("sample", []))])

    ssg = f.get("no-success-response-schema", {})
    ss = ssg.get("items", [])
    if ss:
        _w("no-success-response-schema",
           "Operations whose 2xx response documents no schema",
           [f"by method: {ssg.get('by_method', {})}", "",
            "| Method | Endpoint | Doc |", "|---|---|---|"]
           + [f"| {m} | `{k}` | {u} |" for k, m, u in ss])

    mf = f.get("model-fields-no-description", {}).get("items", {})
    if mf:
        lines = [f"{mf.get('total_fields_missing_desc', 0)} fields across "
                 f"{mf.get('models_affected', 0)} models have an empty Description. "
                 "Full list:", ""]
        for k, miss in mf.get("full", mf.get("sample", [])):
            lines.append(f"- `{k}`: {', '.join('`' + x + '`' for x in miss)}")
        _w("model-fields-no-description", "Model fields with an empty Description", lines)

    mvm = f.get("method-verb-mismatch", {}).get("items", [])
    if mvm:
        _w("method-verb-mismatch", "HTTP method / operation-verb mismatches",
           ["| Endpoint | Method | Path | Issue |", "|---|---|---|---|"]
           + [f"| `{a}` | {b} | `{c}` | {d} |" for a, b, c, d in mvm])

    pc = f.get("path-collisions", {}).get("items", {})
    if pc:
        lines = ["| method+path | # services | services |", "|---|---|---|"]
        for mp, ks in sorted(pc.items(),
                             key=lambda kv: -len({k.split('/')[1] for k in kv[1]})):
            svcs = sorted({k.split("/")[1] for k in ks})
            lines.append(f"| `{mp}` | {len(svcs)} | {', '.join(svcs)} |")
        _w("path-collisions", "Identical method+path reused across services", lines)

    v = _load(VALIDATION_JSON, {})
    if v.get("operations"):
        lines = [f"summary: {json.dumps(v['summary'], ensure_ascii=False)}", "",
                 "Required request-body fields with **no discoverable validation "
                 "criteria** (no enum, no pattern/length/charset in the Description), "
                 "per operation:", ""]
        for r in v["operations"]:
            fns = ", ".join(f"`{x['field']}`" for x in r["undiscoverable_required_fields"])
            lines.append(
                f"- **{r['method']} `{r['endpoint']}`** "
                f"({len(r['undiscoverable_required_fields'])}): {fns}")
        _w("undiscoverable-validation",
           "Parameters with undiscoverable validation criteria (STATIC)", lines)

    idx = ["# Conformance findings (full breakdowns)", "", BANNER,
           "The uncapped STATIC lists behind the consolidated master report. The "
           "RUNTIME counterparts are merged into `master.md` (and the unified "
           "results store).", "",
           "| Report | What |", "|---|---|",
           "| `master.md` | consolidated static+runtime, severity-prioritised |",
           "| `method-verb-mismatch.md` | reads via POST, create via GET |",
           "| `path-collisions.md` | same method+path across services |",
           "| `no-error-response-schema.md` | 4xx/5xx with no body schema |",
           "| `no-success-response-schema.md` | 2xx with no body schema |",
           "| `model-fields-no-description.md` | model fields lacking a description |",
           "| `undiscoverable-validation.md` | required fields with no documented constraint |"]
    (OUTDIR / "README.md").write_text("\n".join(idx) + "\n", encoding="utf-8")


def write_master_report(report: dict | None = None) -> Path:
    """Render + write the consolidated master report to ``reports/findings/master.md``."""
    report = report if report is not None else build_master_report()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / "master.md"
    out.write_text(render_markdown(report), encoding="utf-8")
    return out


def main() -> None:
    report = build_master_report()
    out = write_master_report(report)
    write_legacy_breakdowns()
    s = report["summary"]
    print(f"master report: {s['total']} findings "
          f"(severity={s['by_severity']}, source={s['by_source']}) -> {out}")


if __name__ == "__main__":
    main()
