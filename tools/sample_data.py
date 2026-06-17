"""Generate SAMPLE (synthetic) results for a populated design/CX preview.

NOT real run data — deterministic synthetic observations/findings/history derived
from data/api_catalog.json so the static dashboard + catalog Report render with
realistic-looking coverage / API-health / conformance content for design review.

    python -m tools.sample_data            # writes reports/results/*.jsonl + history

Distribution is seeded (reproducible). Real runs overwrite these via core.results.
"""
from __future__ import annotations
import json, os, random, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "api_catalog.json"
OUT = ROOT / "reports" / "results"


def _endpoints():
    d = json.loads(CATALOG.read_text())
    return d if isinstance(d, list) else d.get("endpoints", d.get("items", []))


def gen(seed: int = 7):
    rng = random.Random(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    eps = _endpoints()
    obs, finds = [], []
    now = time.time()
    for e in eps:
        method = e.get("method", "GET")
        write = method in ("POST", "PUT", "PATCH", "DELETE")
        # ~80% of reads covered, ~62% of writes covered (writes are gated/heavier)
        if rng.random() > (0.62 if write else 0.82):
            continue
        r = rng.random()
        if write:
            cat, status = (("ok", rng.choice([200, 201, 202])) if r < 0.62
                           else ("soft", rng.choice([400, 403, 409])) if r < 0.85
                           else ("fail", rng.choice([500, 502])))
        else:
            cat, status = (("ok", 200) if r < 0.80
                           else ("soft", rng.choice([400, 403, 404])) if r < 0.93
                           else ("fail", rng.choice([500, 503])))
        elapsed = round(min(8000, rng.lognormvariate(5.6, 0.7)), 1)  # ~270ms median
        obs.append({
            "endpoint_key": e["key"], "method": method, "http_path": e.get("http_path", ""),
            "path": e.get("http_path", ""), "status": status, "category": cat,
            "elapsed_ms": elapsed,
            "source": ("crud_probe" if write else rng.choice(["smoke", "read_chain"])),
            "note": "", "run": "sample", "ts": now,
        })
        # ~6% of endpoints carry a conformance finding
        if rng.random() < 0.06:
            sev = rng.choices(["red", "yellow", "green"], [0.18, 0.5, 0.32])[0]
            rule = rng.choice(["status.wrong_code", "naming.snake_case",
                               "error.unstructured", "pagination.missing",
                               "auth.inconsistent", "field.undocumented"])
            finds.append({"endpoint_key": e["key"], "rule_id": rule, "severity": sev,
                          "detail": f"[sample] {rule} on {e['key']}", "source": "static",
                          "issue": "", "run": "sample", "ts": now})

    (OUT / "observations.jsonl").write_text("".join(json.dumps(o) + "\n" for o in obs))
    (OUT / "findings.jsonl").write_text("".join(json.dumps(f) + "\n" for f in finds))

    # trend history: ~10 rows climbing toward the current sample totals
    cov_now = sum(1 for o in obs if o["category"] == "ok")
    rows = []
    for i in range(10):
        frac = 0.55 + 0.045 * i
        rows.append({"ts": now - (9 - i) * 86400, "run": f"sample-{i}",
                     "verified": int(cov_now * frac), "called": int(len(obs) * (0.6 + 0.04 * i)),
                     "reachable": len(eps), "total": len(eps),
                     "ok": int(cov_now * frac), "fail": max(0, 40 - 3 * i)})
    (ROOT / "reports" / "dashboard").mkdir(parents=True, exist_ok=True)
    (OUT / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"sample: {len(obs)} observations, {len(finds)} findings, {len(rows)} history rows "
          f"(ok={cov_now}, of {len(eps)} endpoints)")
    return OUT / "observations.jsonl"


if __name__ == "__main__":
    gen()
