#!/usr/bin/env python3
"""Live driver for a catalog_run / full-plan execution with a self-refreshing
DAG-run dashboard (./dag-run.html) + a VPC-count / adaptive-limit poller.

This is the ADAPTIVE-CONCURRENCY experiment harness: it runs the dependency-DAG
plan live with the AIMD AdaptiveLimiter enabled (SCP_ADAPTIVE), starting mid and
probing UP to CATRUN_MAX_WORKERS while the gateway stays healthy, halving on any
new 502/503/504. The dashboard surfaces the live ``adaptive limit`` + ``503``
counter so you can watch WHERE THE LIMIT SETTLES — that settling point is the
sustainable / optimal concurrency we're hunting.

Usage (from repo root):
  python3 tools/dag_run_live.py ALL              # full enabled plan (~184 lifecycles)
  python3 tools/dag_run_live.py ske-cluster ...  # catalog targets -> their closure

Tunables (env, all have defaults below; override to sweep):
  SCP_ADAPTIVE_START   AIMD starting limit            (10)
  SCP_ADAPTIVE_MIN     AIMD floor                     (4)
  SCP_ADAPTIVE_INTERVAL  seconds between adjustments  (15)
  CATRUN_MAX_WORKERS   AIMD ceiling == thread-pool size (20)

Publish the dashboard to GitHub Pages with a loop that copies ./dag-run.html
onto the dashboard-data branch (see tools/publish_dashboard.sh for the pattern);
the page carries <meta refresh> so an open browser tracks it live.

SAFETY: sets the mutation/destructive/heavy gates ON (this is a live CRUD run).
Always reconcile survivors after stopping early:
  SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(
    subprocess.run(["git", "rev-parse", "--show-toplevel"],
                   capture_output=True, text=True).stdout.strip()
    or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
HTML = ROOT / "dag-run.html"

for k, v in {
    "SCP_ALLOW_MUTATIONS": "true", "SCP_ALLOW_DESTRUCTIVE": "true",
    "SCP_RUN_HEAVY": "true", "SCP_DAG_RUNNER": "true",
    "SCP_TIMEOUT": "30", "SCP_MAX_RETRIES": "3",
    # adaptive concurrency experiment: start mid, probe UP to a high ceiling
    # (CATRUN_MAX_WORKERS), back off on gateway 503s. Watch where `limit` settles
    # — that's the sustainable / optimal concurrency.
    "SCP_ADAPTIVE": "true",
    "SCP_ADAPTIVE_START": "10", "SCP_ADAPTIVE_MIN": "4",
    "SCP_ADAPTIVE_INTERVAL": "15",
    "CATRUN_MAX_WORKERS": "20",
}.items():
    os.environ.setdefault(k, v)

from regression.scenarios import (  # noqa: E402 — must follow sys.path + env setup
    catalog_run, dag_planner, dag_runner, dag_runner_live)

# --schema-diff: ALSO run the conformance schema-live drift probe over THIS DAG
# run (reusing the platform's provisioner + dependency-ordered, slot-gated parallel
# scheduler) instead of conformance.schema_live's serial loop. The schema-diff hook
# is fanned across the executor threads; drift is emitted as runtime Findings.
_SCHEMA_DIFF = "--schema-diff" in sys.argv[1:]
argv = [a for a in (sys.argv[1:] or ["ALL"]) if a != "--schema-diff"] or ["ALL"]
if argv == ["ALL"]:
    leaf = None
    plan = dag_planner.plan()
    title = "ALL (full enabled catalog)"
else:
    leaf, plan = catalog_run.plan_for(argv)
    title = "targets: " + ", ".join(argv)

waves = [{"i": i, "kind": w.kind, "ids": list(w.lifecycles),
          "done": {}, "active": False} for i, w in enumerate(plan.waves)]
state = {"title": title, "started": time.time(), "phase": "starting",
         "vpc": None, "cap": plan.vpc_cap, "waves": waves, "log": [],
         "limit": None, "limit_max": int(os.environ.get("CATRUN_MAX_WORKERS", "8")),
         "err503": 0, "limhist": []}
lock = threading.Lock()
_C = {"passed": "#1a7f37", "failed": "#cf222e", "skipped": "#6e7781", "planned": "#8c959f"}


def log(msg):
    with lock:
        state["log"].append(f"{datetime.now():%H:%M:%S} {msg}")
    render()


_SC = {"passed": "#1a7f37", "failed": "#cf222e", "skipped": "#8c959f",
       "running": "#0969da", "pending": "#d0d7de"}


def render():
    with lock:
        s = json.loads(json.dumps(state))
    el = int(time.time() - s["started"])
    tot = sum(len(w["ids"]) for w in s["waves"] if w["kind"] != "provision")
    done = sum(len(w["done"]) for w in s["waves"])
    cnt = {"passed": 0, "failed": 0, "skipped": 0, "running": 0}
    for w in s["waves"]:
        for st in w["done"].values():
            cnt[st] = cnt.get(st, 0) + 1
        if w["active"]:
            cnt["running"] += len(w["ids"]) - len(w["done"])
    vpc = s["vpc"]
    vcol = "#cf222e" if (vpc or 0) > s["cap"] else "#1a7f37"

    # the DAG graph: one band per wave, each lifecycle a node-cell colored by state;
    # the currently-running nodes pulse.
    bands = []
    for w in s["waves"]:
        if w["kind"] == "provision":
            roots = ", ".join(w["ids"])
            bands.append(f'<div class="bl">provision</div>'
                         f'<div class="band"><span class="cell root" title="{roots}">◆ {roots}</span></div>')
            continue
        # order: running first, then failed, passed, skipped, pending — so the
        # active nodes are easy to spot; each chip shows the lifecycle id (= what
        # kind of resource: networking-vpc-subnet, container-ske-cluster, …).
        def stat(lid):
            return w["done"].get(lid) or ("running" if w["active"] else "pending")
        rank = {"running": 0, "failed": 1, "passed": 2, "skipped": 3, "pending": 4}
        cells = []
        for lid in sorted(w["ids"], key=lambda x: (rank[stat(x)], x)):
            st = stat(lid)
            cls = "chip pulse" if st == "running" else "chip"
            cells.append(f'<span class="{cls}" style="--c:{_SC[st]}" title="{lid} — {st}">{lid}</span>')
        d = len(w["done"])
        n = len(w["ids"])
        bands.append(f'<div class="bl">{w["kind"]} · {d}/{n}</div>'
                     f'<div class="band">{"".join(cells)}</div>')
    graph = "".join(bands)
    # tiny sparkline of the adaptive limit over time (▁▂▃▄▅▆▇█ scaled to ceiling)
    hist = s["limhist"][-60:]
    if hist:
        blk = "▁▂▃▄▅▆▇█"
        hi = max(s["limit_max"], 1)
        spark = "".join(blk[min(len(blk) - 1, int(v / hi * (len(blk) - 1)))] for v in hist)
    else:
        spark = ""
    loglines = "<br>".join(s["log"][-14:])
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<title>DAG run — {s['title']}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:22px;color:#1f2328}}
h1{{font-size:19px;margin:0 0 2px}} .k{{color:#57606a;min-width:90px;display:inline-block}}
.bl{{color:#57606a;font-size:12px;font-weight:600;margin:10px 0 3px}}
.band{{display:flex;flex-wrap:wrap;gap:4px;align-items:center;max-width:1180px;margin-bottom:6px}}
.chip{{font-size:10px;padding:1px 6px 1px 4px;border-radius:5px;border:1px solid #0000001a;background:#f6f8fa;color:#1f2328;white-space:nowrap;line-height:1.55}}
.chip::before{{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--c);margin-right:4px;vertical-align:0}}
.chip.pulse{{background:#ddf4ff;border-color:#0969da55;font-weight:600}}
.cell.root,.chip.root{{padding:2px 8px;background:#dbeafe;border-radius:6px;font-size:12px;color:#0b5}}
@keyframes pl{{0%,100%{{opacity:1}}50%{{opacity:.45}}}} .pulse{{animation:pl 1s ease-in-out infinite}}
.lg span{{margin-right:12px;font-size:12px;color:#57606a}}
.lg i{{width:11px;height:11px;border-radius:3px;display:inline-block;vertical-align:-1px;margin-right:3px}}</style></head><body>
<h1>🚀 DAG run — {s['title']}</h1>
<div style="color:#57606a;margin-bottom:8px">catalog topology → cap-safe waves → dag_runner · 노드=lifecycle, 색=실행상태</div>
<div><span class="k">phase</span> <b>{s['phase']}</b> · <span class="k">elapsed</span> {el//60}m{el%60:02d}s ·
 <span class="k">live VPCs</span> <b style="color:{vcol}">{vpc if vpc is not None else '—'}/{s['cap']}</b></div>
<div><span class="k">adaptive limit</span> <b style="color:#0969da">{(f"{s['limit']:.1f}" if s['limit'] is not None else '—')}</b>
 / ceil {s['limit_max']} · <span class="k">503/502/504</span> <b style="color:{'#cf222e' if s['err503'] else '#1a7f37'}">{s['err503']}</b>
 · <span style="font-family:ui-monospace,monospace;font-size:11px">{spark}</span></div>
<div><span class="k">progress</span> <b>{done}/{tot}</b> ·
 <span style="color:#1a7f37">{cnt['passed']}P</span> <span style="color:#cf222e">{cnt['failed']}F</span>
 <span style="color:#8c959f">{cnt['skipped']}S</span> <span style="color:#0969da">{cnt['running']}▶</span></div>
<div class="lg" style="margin:8px 0">
 <span><i style="background:#0969da"></i>실행중</span><span><i style="background:#1a7f37"></i>passed</span>
 <span><i style="background:#cf222e"></i>failed</span><span><i style="background:#8c959f"></i>skipped</span>
 <span><i style="background:#d0d7de"></i>대기</span></div>
{graph}
<h2 style="font-size:13px;margin:14px 0 4px">log</h2>
<div style="font-family:ui-monospace,monospace;font-size:11px;color:#57606a">{loglines}</div>
<div style="margin-top:12px;color:#8c959f;font-size:11px">auto-refresh 3s · updated {datetime.now():%H:%M:%S}</div>
</body></html>"""
    HTML.write_text(html)


_LIM = {"ref": None}   # set once the executor is built; poller samples it


def poller(stop):
    from core.config import settings
    from core.http_client import ApiClient, retry_status_count
    c = ApiClient(settings)
    while not stop.is_set():
        try:
            r = c.request("GET", "/v1/vpcs", service="vpc", timeout=10, retry=False)
            d = r.body if isinstance(r.body, dict) else json.loads(r.raw_text or "{}")
            with lock:
                state["vpc"] = len(d.get("vpcs", []))
        except Exception:
            pass
        with lock:
            state["err503"] = retry_status_count()
            lim = _LIM["ref"]
            if lim is not None:
                state["limit"] = lim.limit
                state["limhist"].append(lim.limit)
        render()
        stop.wait(3)


def on_event(kind, payload):
    with lock:
        if kind == "provision_start":
            state["phase"] = "provisioning shared roots"
        elif kind == "provision_done":
            state["phase"] = "running waves"
        elif kind == "wave_start":
            state["waves"][payload["index"]]["active"] = True
        elif kind == "wave_done":
            state["waves"][payload["index"]]["active"] = False
        elif kind == "lifecycle_done":
            for w in state["waves"]:
                if payload["lifecycle_id"] in w["ids"]:
                    w["done"][payload["lifecycle_id"]] = payload["status"]
                    break
    if kind == "lifecycle_done":
        log(f"  {payload['lifecycle_id']} → {payload['status']}")
    else:
        render()


def main():
    log(f"plan: {len(plan.waves)} waves, {sum(len(w.lifecycles) for w in plan.waves if w.kind!='provision')} lifecycles, roots={plan.shared_roots}")
    stop = threading.Event()
    threading.Thread(target=poller, args=(stop,), daemon=True).start()
    state["phase"] = "building executor"
    render()
    mw = int(os.environ.get("CATRUN_MAX_WORKERS", "8"))
    on_response = _finalize = None
    if _SCHEMA_DIFF:
        from conformance.runtime import _docs
        from conformance.schema_live import make_schema_diff_hook
        on_response, _finalize = make_schema_diff_hook(_docs())
        log("schema-diff=ON — conformance schema-live drift folded into this DAG run")
    executor, provisioner = dag_runner_live.build(plan, max_workers=mw,
                                                  on_response=on_response)
    _LIM["ref"] = getattr(executor, "limiter", None)
    log(f"adaptive={'on' if _LIM['ref'] is not None else 'off'} "
        f"start={os.environ.get('SCP_ADAPTIVE_START')} ceil={mw} "
        f"min={os.environ.get('SCP_ADAPTIVE_MIN')} interval={os.environ.get('SCP_ADAPTIVE_INTERVAL')}s")
    # A1: dispatch via the DYNAMIC scheduler (longest-job-first, slot-gated, no wave
    # barrier) by default — set SCP_DAG_DYNAMIC=false to fall back to the static-wave
    # run_plan. The dynamic path projects ~50-52 min vs ~64 min static at healthy
    # concurrency (regression.scenarios.dag_scheduler.simulate_full). PENDING first
    # live validation on a clean (storm-free) heavy run.
    use_dynamic = os.environ.get("SCP_DAG_DYNAMIC", "true") == "true"
    if use_dynamic:
        from regression.scenarios import dag_scheduler
        # the dashboard bands pulse from wave "active"; the dynamic runner has no
        # static waves, so mark every non-provision band active up front (per-node
        # lifecycle_done still overrides each chip to its final colour).
        for w in state["waves"]:
            if w["kind"] != "provision":
                w["active"] = True
        stagger = float(os.environ.get("SCP_HEAVY_STAGGER_S", "0"))
        log(f"dispatch=DYNAMIC (longest-first, slot-gated; SCP_DAG_DYNAMIC) "
            f"workers={mw} heavy_stagger={stagger}s")
        result = dag_scheduler.run_dynamic(plan, executor, provisioner=provisioner,
                                           max_workers=mw, heavy_stagger_s=stagger,
                                           on_event=on_event)
    else:
        log(f"dispatch=STATIC (run_plan, wave-barrier) workers={mw}")
        result = dag_runner.run_plan(plan, executor, provisioner=provisioner,
                                     max_workers=mw, on_event=on_event)
    state["phase"] = "DONE"
    by = result.by_status()
    log(f"DONE — {by}")
    if _finalize is not None:
        summ = _finalize(via="dag_run_live", include_heavy=True)
        log(f"schema-diff: responses checked={summ['checked']} "
            f"with-drift={summ['with_drift']}")
    # A2: fold this run's measured wall-times into the duration store so the next
    # schedule's critical-path / longest-first priority improves (dag_run_live never
    # did this before — every node stayed n:1). Learning must never fail the run.
    try:
        from regression.scenarios import schedule_optimizer
        schedule_optimizer.update_durations(schedule_optimizer.measured_from_result(result))
    except Exception:  # noqa: BLE001
        pass
    time.sleep(2)
    stop.set()
    render()
    print(json.dumps(by))


if __name__ == "__main__":
    main()
