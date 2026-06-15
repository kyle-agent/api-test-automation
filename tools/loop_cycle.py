#!/usr/bin/env python3
"""loop_cycle.py — one-shot health snapshot of the 4-track autonomous loop.

The Meta-Orchestrator (lead session) calls this once per cycle to read a
one-page heartbeat covering every track. Output is plain text on stdout,
kept terse on purpose. No commits, no pushes, read-only on everything
outside this file.

Sections:
  1. Header        — branch, last commit, KST timestamp
  2. Gates         — exit code + last line of three offline gates
  3. Coverage      — write_gap / getid_gap / static ceiling %
  4. Ledger        — services-by-status counts
  5. Backlog       — open vs done counts from IMPROVEMENT-BACKLOG.md
  6. fail_new      — last 5 dashboard/history.jsonl entries (if present)
  7. STOP flags    — scan PRODUCT-FINDINGS.md + ledger for the 6 STOP keywords

Run: ``python tools/loop_cycle.py`` from the repo root.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = _dt.timezone(_dt.timedelta(hours=9))

# --- The 6 STOP keywords (AUTONOMOUS-LOOP.md §STOP criteria) ---------------
# 1 credential/license · 2 console-only · 3 product defect · 4 비가역/billable
# 5 engine capability gap · 6 docs vs observation conflict
STOP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "1-license": ("license",),
    "2-credential": ("credential",),
    "3-console-only": ("console", "console-only", "console 전용"),
    "4-irreversible": ("비가역", "irreversible", "billable", "과금"),
    "5-multipart": ("multipart",),
    "6-docs-vs-obs": ("docs vs obs", "docs vs observ", "docs와 관측"),
}


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run cmd from repo root, return (exit_code, last_nonblank_line)."""
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, "(command not found)"
    except subprocess.TimeoutExpired:
        return 124, f"(timeout after {timeout}s)"
    blob = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in blob.splitlines() if ln.strip()]
    last = lines[-1] if lines else "(no output)"
    # keep last line readable
    if len(last) > 160:
        last = last[:157] + "..."
    return proc.returncode, last


def section_header() -> list[str]:
    rc, branch = _run(["git", "branch", "--show-current"], timeout=5)
    rc, commit = _run(["git", "log", "--oneline", "-1"], timeout=5)
    now = _dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    return [
        "=" * 72,
        f"LOOP CYCLE SNAPSHOT  {now}",
        f"branch : {branch}",
        f"commit : {commit}",
        "=" * 72,
    ]


def section_gates() -> list[str]:
    out = ["[GATES]"]
    py = sys.executable
    gates = [
        ("formal R1     ", [py, "knowledge/formal/validate.py"]),
        ("scenarios     ", [py, "-m", "regression.scenarios.validate"]),
        ("pytest offline", [py, "-m", "pytest", "tests/offline", "-q"]),
    ]
    # Run all gates concurrently — they are read-only and independent.
    with ThreadPoolExecutor(max_workers=len(gates)) as pool:
        futs = [(name, pool.submit(_run, cmd, 120)) for name, cmd in gates]
        for name, fut in futs:
            rc, last = fut.result()
            verdict = "PASS" if rc == 0 else f"FAIL({rc})"
            out.append(f"  {name} : {verdict}  | {last}")
    return out


def section_coverage() -> list[str]:
    out = ["[COVERAGE]"]
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "spec.coverage_gap"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        text = proc.stdout
    except Exception as e:
        out.append(f"  ERR: {e!r}")
        return out
    # parse "reachable now : 1259 (91.8%)" + GAP line + id-bound/write
    def grab(pat: str) -> str:
        m = re.search(pat, text)
        return m.group(1) if m else "?"
    ceiling = grab(r"reachable now\s*:\s*\d+\s*\(([\d.]+)%\)")
    gap_total = grab(r"GAP \(need scenarios\):\s*(\d+)")
    gap_id = grab(r"id-bound GETs\s*:\s*(\d+)")
    gap_write = grab(r"write ops\s*:\s*(\d+)")
    out.append(
        f"  ceiling={ceiling}%  gap_total={gap_total}  "
        f"gap_getid={gap_id}  gap_write={gap_write}"
    )
    return out


def section_ledger() -> list[str]:
    out = ["[LEDGER]"]
    path = ROOT / "agents" / "coordination" / "ledger.json"
    if not path.exists():
        out.append("  (missing)")
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        out.append(f"  ERR parse: {e!r}")
        return out
    services = data.get("services", [])
    counts = Counter(s.get("status", "?") for s in services)
    parts = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    out.append(f"  services={len(services)}  {parts}")
    return out


def section_backlog() -> list[str]:
    """Count IB rows by status. Each row looks like:
    | IB-NNN | area | problem | fix | size | open|in-progress|done|waived ... |"""
    out = ["[BACKLOG]"]
    path = ROOT / "docs" / "IMPROVEMENT-BACKLOG.md"
    if not path.exists():
        out.append("  (missing)")
        return out
    counts: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("| IB-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        status = cells[-1].split()[0] if cells and cells[-1] else "?"
        counts[status] += 1
    parts = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    out.append(f"  total={sum(counts.values())}  {parts}")
    return out


def section_history() -> list[str]:
    out = ["[fail_new TREND  (last 5 history.jsonl entries)]"]
    path = ROOT / "dashboard" / "history.jsonl"
    if not path.exists():
        out.append("  (no dashboard/history.jsonl yet)")
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        out.append(f"  ERR read: {e!r}")
        return out
    tail = [ln for ln in lines if ln.strip()][-5:]
    if not tail:
        out.append("  (empty)")
        return out
    for ln in tail:
        try:
            row = json.loads(ln)
        except Exception:
            out.append(f"  {ln[:120]}")
            continue
        rid = row.get("run_id") or row.get("id") or "?"
        fn = row.get("fail_new", row.get("failNew", "?"))
        cov = row.get("cov_op", row.get("covOp", "?"))
        when = row.get("when") or row.get("timestamp") or ""
        out.append(f"  run={rid}  fail_new={fn}  cov_op={cov}  {when}")
    return out


def section_stop_flags() -> list[str]:
    """Scan for ACTIVE STOP blockers — rows that look like they're waiting on
    owner / credential / license / console-only / multipart / docs-vs-obs etc.
    A keyword appearing inside a policy paragraph is not an active blocker —
    we only flag *open IB rows* and *ledger service notes* that contain the
    keyword."""
    out = ["[STOP CRITERIA SCAN]"]
    hits: list[str] = []

    # 1) IMPROVEMENT-BACKLOG: open/in-progress rows whose status mentions 대기
    #    or whose problem mentions the STOP keywords.
    backlog_path = ROOT / "docs" / "IMPROVEMENT-BACKLOG.md"
    if backlog_path.exists():
        text = backlog_path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if not line.startswith("| IB-"):
                continue
            low = line.lower()
            # only rows still open / in-progress
            if not ("open" in low or "in-progress" in low):
                continue
            for key, kws in STOP_KEYWORDS.items():
                if any(kw.lower() in low for kw in kws):
                    ib_id = line.split("|")[1].strip()
                    hits.append(f"  {key} : {ib_id} (BACKLOG)")
                    break

    # 2) ledger.json: services whose notes carry a STOP keyword AND are not
    #    yet integrated/live-validated.
    ledger_path = ROOT / "agents" / "coordination" / "ledger.json"
    if ledger_path.exists():
        try:
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for svc in data.get("services", []):
            status = svc.get("status", "")
            if status in ("integrated", "live-validated"):
                continue
            notes = (svc.get("notes") or "").lower()
            if not notes:
                continue
            for key, kws in STOP_KEYWORDS.items():
                if any(kw.lower() in notes for kw in kws):
                    name = f"{svc.get('category','?')}/{svc.get('service','?')}"
                    hits.append(f"  {key} : {name} (ledger:{status})")
                    break

    if hits:
        out.extend(hits)
    else:
        out.append("  (no active STOP blockers — open backlog + ledger clean)")
    return out


def main() -> int:
    # gates + coverage are the only slow sections; run them in parallel and
    # build the fast (read-only file) sections inline while they execute.
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_gates = pool.submit(section_gates)
        f_cov = pool.submit(section_coverage)
        header = section_header()
        ledger = section_ledger()
        backlog = section_backlog()
        history = section_history()
        stops = section_stop_flags()
        gates = f_gates.result()
        cov = f_cov.result()
    for sec in (header, gates, cov, ledger, backlog, history, stops, ["=" * 72]):
        for line in sec:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
