#!/usr/bin/env python3
"""Regenerate the per-service sweep report from _progress.tsv (durable checkpoint).
Reads reports/sweep-logs/_progress.tsv and rewrites the progress table + rollup
into reports/per-service-sweep-2026-07-13.md (between BEGIN/END markers)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROG = ROOT / "reports/sweep-logs/_progress.tsv"
REPORT = ROOT / "reports/per-service-sweep-2026-07-13.md"
BEGIN = "<!--PROGRESS-BEGIN-->"
END = "<!--PROGRESS-END-->"

rows = []
if PROG.exists():
    for ln in PROG.read_text().splitlines():
        p = ln.split("\t")
        if len(p) < 8:
            continue
        svc, closure, lc, rc, errs, surv, summ, verdict = p[:8]
        rows.append(dict(svc=svc, closure=closure.replace("closure=", ""),
                         lc=lc.replace("LC=", ""), rc=rc.replace("rc=", ""),
                         errs=errs, surv=surv, summ=summ, verdict=verdict))

n = len(rows)
ok = sum(1 for r in rows if "✅" in r["verdict"])
warn = sum(1 for r in rows if "⚠️" in r["verdict"])
bad = sum(1 for r in rows if "❌" in r["verdict"])
leak = sum(1 for r in rows if not r["surv"].startswith("surv=1;"))

lines = [BEGIN,
         f"\n**진행: {n}/56 완료** — ✅{ok} · ⚠️{warn} · ❌{bad} · (teardown 미복귀 잔존: {leak})\n",
         "| # | 서비스 | 폐포/LC | 라이브(passed/skip) | 4xx;5xx;fail | teardown surv;recon | 판정 |",
         "|---|--------|---------|---------------------|--------------|---------------------|------|"]
for i, r in enumerate(rows, 1):
    summ = r["summ"].replace("summary:", "").strip() or "-"
    errs = r["errs"]
    lines.append(f"| {i} | {r['svc']} | {r['closure']}/{r['lc']} | {summ} | {errs} | {r['surv']} | {r['verdict']} |")
lines.append("")
lines.append(END)
table = "\n".join(lines)

txt = REPORT.read_text()
if BEGIN in txt and END in txt:
    txt = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), table, txt, flags=re.S)
else:
    txt = txt.rstrip() + "\n\n## 진행 현황 (자동 갱신)\n\n" + table + "\n"
REPORT.write_text(txt)
print(f"report updated: {n} rows (ok={ok} warn={warn} bad={bad} leak={leak})")
