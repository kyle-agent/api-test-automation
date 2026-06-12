"""Current dashboard + coverage data, served from the dashboard-data branch.

"1차: 현재만큼의 커버리지를 플랫폼에서 보여준다" — the existing pipeline
already publishes the authoritative dashboard (index.html, services/*.html)
and the coverage trend (history.jsonl) to the dashboard-data branch. Rather
than recompute any of it, the platform serves those artifacts directly:
`git fetch` (cached, 60s) + `git show origin/dashboard-data:<file>`.

Best-effort like everything else: no branch / no git -> empty results and the
UI degrades gracefully.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "origin/dashboard-data"
_FETCH_TTL = 60.0
_last_fetch = [0.0]

_CTYPES = {".html": "text/html", ".json": "application/json",
           ".jsonl": "application/x-ndjson", ".tsv": "text/tab-separated-values",
           ".css": "text/css", ".js": "text/javascript"}


def _refresh() -> None:
    if time.time() - _last_fetch[0] < _FETCH_TTL:
        return
    _last_fetch[0] = time.time()
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "dashboard-data"],
                       cwd=ROOT, timeout=30, capture_output=True)
    except Exception:
        pass


def file(rel: str) -> tuple[bytes, str] | None:
    """One file from the dashboard-data branch -> (body, content_type)."""
    if ".." in rel or rel.startswith("/") or not rel:
        return None
    _refresh()
    try:
        out = subprocess.run(["git", "show", f"{BRANCH}:{rel}"],
                             cwd=ROOT, timeout=15, capture_output=True)
        if out.returncode != 0:
            return None
        ctype = _CTYPES.get(Path(rel).suffix, "application/octet-stream")
        return out.stdout, ctype
    except Exception:
        return None


def history(limit: int = 30) -> list[dict]:
    """Coverage trend rows (newest LAST), e.g. cov_op/cov_get/cov_c3/fail_new."""
    got = file("history.jsonl")
    if not got:
        return []
    rows = []
    for line in got[0].decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:]


def latest_coverage() -> dict | None:
    rows = history(limit=1)
    return rows[-1] if rows else None


def conformance_summary(systemic_limit: int = 8) -> dict | None:
    """conformance.json (published by the conformance axis) -> the Report
    tab's summary: green/yellow/red counts + top systemic findings."""
    got = file("conformance.json")
    if not got:
        return None
    try:
        data = json.loads(got[0].decode(errors="replace"))
    except ValueError:
        return None
    summary = data.get("summary") or {}
    systemic = sorted((data.get("systemic") or []),
                      key=lambda s: -(s.get("count") or 0))[:systemic_limit]
    return {"summary": summary, "systemic": systemic}


def category_coverage() -> list[dict]:
    """Cumulative verified(2xx) per category from endpoint_status.json
    (key 'category/service/op' -> [status, latency, sha]); ascending = the
    work backlog. Counts are over OBSERVED endpoints, labelled as such."""
    got = file("endpoint_status.json")
    if not got:
        return []
    try:
        status = json.loads(got[0].decode(errors="replace")).get("status") or {}
    except ValueError:
        return []
    totals: dict[str, list[int]] = {}
    for key, val in status.items():
        cat = key.split("/", 1)[0]
        code = val[0] if isinstance(val, (list, tuple)) and val else 0
        tot = totals.setdefault(cat, [0, 0])
        tot[1] += 1
        if isinstance(code, int) and 200 <= code < 300:
            tot[0] += 1
    rows = [{"name": cat, "ok": ok, "total": tot,
             "pct": round(ok * 100 / tot) if tot else 0}
            for cat, (ok, tot) in totals.items()]
    return sorted(rows, key=lambda r: r["pct"])
