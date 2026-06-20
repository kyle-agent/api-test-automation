# Post-run Analysis: Full Heavy DAG Run — 2026-06-20

**Run facts:** 184/184 lifecycles · 156 passed / 25 failed / 3 skipped ·
wall 4118s (68.6 min) · obs 2474 · fail-rate 6.1% · distinct 2xx 1024
**Scheduler:** static-wave `dag_runner.run_plan` via `tools/dag_run_live.py`
(AIMD adaptive: start=10, min=4, ceil=20 — clamped to floor 4 during storm)
**This is the baseline row** — no prior history rows in `data/optimizer/history.jsonl`.

---

## TIER A — Scheduling / Makespan (highest expected return)

### A1. Wire `dag_scheduler.run_dynamic` into `dag_run_live.py` [CRITICAL PATH]

**Problem.** `tools/dag_run_live.py:234` calls `dag_runner.run_plan` (static-wave
scheduler). The static scheduler has two known makespan defects documented in
`regression/scenarios/dag_scheduler.py`:
1. Self-creators are wave-packed alphabetically by VPC-slot count, ignoring
   measured durations — long nodes land late.
2. Wave barriers: wave N+1 waits for ALL nodes of wave N even if fast nodes
   freed their VPC slot earlier.

`vpc-peering` (1267.9s = 21.1 min measured in this run) illustrates both defects.
Alphabetically near the end, it was scheduled in the LAST self-create wave and
started ~52 min into the run. With the dynamic dispatcher it would start in the
FIRST available slot. The `simulate_selfcreate` simulation predicts the self-create
portion drops from ~44.1 min (static) to ~23.0 min (dynamic) — a 48% reduction.

**File / lever:** `tools/dag_run_live.py`, line 234.
Replace:
```python
result = dag_runner.run_plan(plan, executor, provisioner=provisioner,
                             max_workers=mw, on_event=on_event)
```
With:
```python
from regression.scenarios import dag_scheduler
result = dag_scheduler.run_dynamic(plan, executor, provisioner=provisioner,
                                   max_workers=mw, on_event=on_event)
```
(Also import `dag_scheduler` at the top; `dag_scheduler.run_dynamic` emits the
same event vocabulary: `provision_start/done`, `lifecycle_done`, `teardown_done`.)

**Expected effect:** 15–20 min wall-time reduction on full heavy runs (48% of
21-min self-create tail, plus freed-slot pipelining across waves). Confidence: HIGH
(simulation quantified; the scheduler and `simulate_selfcreate` are already
implemented and unit-tested in `tests/offline/test_dag_*.py`).

### A2. Wire `schedule_optimizer.update_durations` after every `dag_run_live` run [DURATION LEARNING GAP]

**Problem.** `dag_runner.main()` (line 270–273) calls
`schedule_optimizer.update_durations(schedule_optimizer.measured_from_result(result))`
after a live run. But `dag_run_live.py` calls `dag_runner.run_plan` directly and
returns immediately without calling `update_durations`. The duration store
(`data/optimizer/durations.json`) therefore contains only single-run values
(all `n:1`). The rolling average — which drives priority ordering in the dynamic
scheduler — never improves across runs.

**File / lever:** `tools/dag_run_live.py`, after line 237 (`by = result.by_status()`).
Add:
```python
try:
    from regression.scenarios import schedule_optimizer
    schedule_optimizer.update_durations(
        schedule_optimizer.measured_from_result(result))
except Exception:  # learning must never fail the run
    pass
```

**Expected effect:** priorities improve monotonically; by run 3+ the dynamic
scheduler schedules `database-postgresql-cluster` (2744s) and
`database-mysql-cluster` (2544s) first, overlapping with the shared-root
provisioning tail. Confidence: HIGH (trivial addition; the function already
exists and the schema is already populated in `durations.json` from this run).

### A3. Dashboard wave model out of sync with `run_dynamic` [LOW RISK, MINOR]

`dag_run_live.py` builds its HTML wave state from `plan.waves` (static wave list).
`dag_scheduler.run_dynamic` emits `lifecycle_done` events but NO `wave_start/done`
events (it uses a flat "dynamic" wave). The live dashboard will show all nodes as
"pending" in the static wave bands while they execute.

**File / lever:** `tools/dag_run_live.py` `on_event` handler (line 201–218).
After switching to `run_dynamic`, either: (a) treat `lifecycle_done` as the sole
progress signal (remove the wave-band animation, show all nodes in a flat grid);
or (b) add a `wave_start/done` emitter to `dag_scheduler.run_dynamic` that fires
a single "dynamic" wave. Option (b) is two lines in `dag_scheduler.py`.

**Expected effect:** dashboard is accurate during dynamic runs. Confidence: HIGH.

---

## TIER B — Resilience to the 503 Gateway Storm

### B1. AIMD floor and back-off profile are appropriate — do NOT tighten further

**Evidence.** 117 of 152 obs-level failures (77%) are Envoy
`upstream connect error … connection timeout` — server-side gateway-to-upstream
saturation, not our request bug. The storm was concentrated 12:07–12:25 at peak
concurrency. AIMD clamped to the configured floor of 4, then eased. The 8 heavy
failures that overlap this window are all optional-step groups (503s caused them
to skip, not fail the lifecycle spine).

**Recommendation:** Keep `SCP_ADAPTIVE_MIN=4` and `SCP_ADAPTIVE_INTERVAL=15s`.
The AIMD already backed off correctly. Tightening the floor further (to 2–3)
would increase the stall cost; raising it risks more storm pressure.

### B2. Heavy-create burst stagger: spread the 5 concurrent heavy creators

**Problem.** 5 heavy self-creators (`database-mysql-cluster` 2544s,
`database-postgresql-cluster` 2744s, `compute-virtualserver-full` 1826s,
`container-ske-cluster-nodepool` 1789s, `gen-heavy-aimlops` 2963s) all dispatch
simultaneously from the same self-create wave. Each fires a `POST /v1/clusters`
within seconds of the others, creating a burst that likely contributed to the
12:07 storm onset.

**File / lever:** `dag_scheduler.py` `run_dynamic` dispatch loop (lines 139–153).
Add a configurable inter-dispatch sleep (e.g., `SCP_HEAVY_STAGGER_S`, default 0)
between consecutive slot acquisitions — e.g., 5s between each heavy node submission.
This costs `(n_heavy - 1) * stagger_s` wall time (~20s for 5 nodes at 5s) but
distributes the provisioning load across the upstream gateway.

**Expected effect:** reduces peak upstream connection bursts during heavy phases.
Confidence: MEDIUM (stagger will help; optimal value needs measurement across runs).

### B3. Required-step 503 retry: add one retry for required steps that are connection timeouts

**Problem.** Currently, 503 `upstream connect error / connection timeout` on a
REQUIRED step (not optional-group) causes the lifecycle to FAIL immediately. The
`SCP_MAX_RETRIES=3` in `dag_run_live.py` is an HTTP-client transport retry, but
at the engine step level, a required step that 503s causes the lifecycle to be
marked `failed`. For transient gateway saturation events, a single lifecycle-level
retry after a short backoff (e.g., 15–30s) would recover most cases.

**File / lever:** `regression/scenarios/engine.py` required-step failure handling.
This is a larger change and the exact hook location should be confirmed against
the engine code. Route to orchestrator as a recommendation: add a
`retry_on_503: true` lifecycle-level flag (not a global default) gated on the
error being specifically the Envoy connection-timeout pattern.

**Confidence:** MEDIUM. Recommend routing to orchestrator before implementing.

---

## TIER C — The 8 Heavy Lifecycle Failures: Storm-Transient vs Real

All 8 heavy failures occurred within the 12:07–12:25 503 storm window.
Classification from log evidence:

### STORM-TRANSIENT (safe to re-run without any fix):

| Lifecycle | Evidence | Duration (s) |
|-----------|----------|-------------|
| `gen-heavy-aimlops` | All groups skipped via 503; `create-aimlops-platform`, `list-images`, etc. all "upstream connect error". Lifecycle structure intact but every optional step hit the storm. | 2963 |
| `gen-heavy-ske-upgrade` | Transport timeout on `create-ske-cluster` (30s read timeout); retried once, also failed. A re-run outside the storm window should succeed — SKE upgrade was LIVE-PROVEN on 2026-06-14. | 1401 |
| `gen-heavy-vs-netops` | Required LB/LB-healthcheck/privatelink steps hit `{lb_health_check_id}` not-found (dependent on a failed upstream LB create); the LB create itself failed during the storm. Cascade failure from storm-killed parent step. | 1052 |
| `container-ske-cluster-nodepool` | `create-ske-cluster` hit a 30s transport timeout; the cluster never started. Same root cause as `gen-heavy-ske-upgrade`. | 1789 |
| `compute-virtualserver-full` | Listed as failed with teardown attempted; log shows teardown of keypair/security-groups (partial create occurred). Likely hit storm during server-create polling. Check for orphan cleanup. | 1826 |

### MIXED / STRUCTURAL (may need a fix before re-running):

| Lifecycle | Evidence | Recommended action |
|-----------|----------|-------------------|
| `gen-heavy-lb-members` | `lb-healthcheck-create` → 400 `SubnetNotAssociatedWithLoadBalancer` (not a 503). The subnet `ddcfcc23a22546aab8fa16d7e1d8a2fe` (shared) does not contain a Load Balancer. This is a structural prerequisite issue: the LB must be created in that subnet BEFORE the health-check. Recurs in `heavy-shared-networking` as the same 400. | Fix: add `lb-create-in-shared-subnet` step before `lb-healthcheck-create`, or use a dedicated LB subnet. Route to orchestrator. |
| `gen-wave4-asg` | `create-auto-scaling-group` → 400 `InvalidAutoScalingGroupLaunchConfigurationId` (LC ID `c2aba278...` is not valid). The launch configuration was created (teardown deleted it) but its ID was stale. Possible race in the step that creates and captures the LC ID. Not a 503 storm failure. | Fix: confirm `create-launch-configuration` capture step fires before `create-auto-scaling-group` and that the `{launch_configuration_id}` is populated. |
| `gen-wave5-apigw-privatelink` | `create-privatelink-service` → 400 `ip-address-overlap` (IP `10.163.8.5` does not overlap with the shared subnet CIDR). The PrivateLink Service IP must be within the subnet CIDR range. The shared subnet is `10.124.0.0/24`; the hardcoded IP `10.163.8.5` is outside it. | Fix: parameterize the PrivateLink Service IP to a value inside the shared subnet CIDR (e.g. `10.124.0.10`). File: the `gen-wave5-apigw-privatelink` lifecycle JSON. |

### Re-run priority list (storm-transient, no code change needed):
1. `gen-heavy-aimlops` (all steps skipped by 503 — full re-run worthwhile)
2. `gen-heavy-ske-upgrade` (SKE proven on 2026-06-14; this was a transport timeout)
3. `container-ske-cluster-nodepool` (same root cause)
4. `compute-virtualserver-full` (check orphan cleanup first via reconciler)
5. `gen-heavy-vs-netops` (after confirming LB creates cleanly outside the storm)

---

## TIER D — Recurring Fails and Slowest Nodes

### D1. Recurring fails — classified

| Endpoint / step | Count | Root cause | Action |
|-----------------|-------|-----------|--------|
| `networking/security-group/createsecuritygroup` | 4 | All 4 are 503 upstream-connect during the storm. Security-group creates are fast (no polling) so these are pure storm casualties. | Re-run; expect clean pass outside storm. |
| `application-service/apigateway/setresourcepolicy` | 2+1 | Known product bug: `ContactAdminForAssistance` 500 (PF-19, already baselined in `known_issues.json`). NOT a storm failure. | Already baselined. Do not re-run expecting a different result. |
| `storage/filestorage/createvolume` | 2 | Log: `filestorage.BadRequest.Invalid.volume.purpose` — "Cannot delete volume because replication is in use". The delete step fires while the replication policy still exists. Teardown ordering issue. | Fix teardown order in `filestorage-volume` lifecycle: delete replication policy before deleting the volume. File: `regression/scenarios/lifecycles/<filestorage lifecycle>.json`. |
| `compute/scf/createcloudfunction` | 2 | 503 storm during create. SCF is `kr-west1/east1 only`. | Re-run outside storm window. |
| `data-analytics/quick-query/getquickquerylist` | 2 | 503 storm. quick-query requires a running SKE cluster; without one, the list hits a gateway that has no upstream. | Re-run outside storm window. |
| `security/kms/createkey` | 2 | Slowest endpoint: `security-kms-key` duration 7s with a 15257ms p95 spike — the slow observation is due to the AIMD limit-clamp (connection queued, not a true create-failure). | No fix; monitor in next run. |

### D2. Slowest nodes by measured duration (from `durations.json`)

| Lifecycle | Duration (s) | Flag |
|-----------|-------------|------|
| `gen-heavy-aimlops` | 2963 | Heavy; failed this run (storm). Critical path candidate. |
| `database-postgresql-cluster` | 2744 | Heavy PASSED. Longest successful node. |
| `database-mysql-cluster` | 2544 | Heavy PASSED. |
| `compute-virtualserver-full` | 1826 | Heavy; failed (storm+timeout). |
| `container-ske-cluster-nodepool` | 1789 | Heavy; failed (transport timeout). |
| `gen-heavy-ske-upgrade` | 1401 | Heavy; failed (transport timeout). |
| `heavy-shared-networking` | 1379 | PASSED. LB/DNS/privatelink cross-dep issues skipped (soft). |
| `vpc-peering` | 1267 | FAILED (503 during approve). Self-creator placed in LAST wave under static scheduler. Primary scheduling-tail target (A1). |
| `gen-heavy-vs-netops` | 1052 | Heavy; failed (cascade from storm-killed LB). |
| `heavy-shared-dbaas` | 929 | PASSED. 503 hit cachestore/mariadb sub-ops (optional groups). |

**The 25070ms single-call spike on `gen-wave5-scf-triggers:create-apigateway-trigger`:**
This is the worst single-endpoint latency observation (from `analyze_run` report).
SCF triggers attach API Gateway backends; the delay is likely API Gateway
provisioning under load. Not a timeout misconfiguration — the operation
succeeded. No action needed.

### D3. Structural optionals that reveal real issues (not storm, not product bugs)

**`filestorage-volume` delete conflict (recurs):** The `delete-volume` step hits
`filestorage.BadRequest.Invalid.volume.purpose` because the replication policy
was created and not deleted first. This is deterministic regardless of 503 load.
Fix the teardown order.

**`gen-wave5-apigw-privatelink` IP overlap (deterministic):** The hardcoded
PrivateLink Service IP `10.163.8.5` is outside the shared subnet CIDR
(`10.124.0.0/24`). Will fail on every run using the shared subnet.

**`vs-autoscaling-coverage` LC ID invalid:** Launch configuration ID was created
but the auto-scaling group create rejected it as invalid. Investigate whether the
`create-launch-configuration` step completes before `create-auto-scaling-group`
attempts to use the ID, and whether the capture is correctly populated.

**`idc-group/idc-user/idc-account-assignment/idc-permission-set` all 400
`Field required`:** The `instance_id` required for all IAM Identity Center
operations is not being supplied. These lifecycles need either a real `instance_id`
from a prior `list-instances` step, or explicit skip gates. Structural — not storm.

---

## TIER D — Under-Parallelization Root Cause

**Observed concurrency: 1.48 with efficiency 0.0** (from `analyze_run` report).

The AIMD clamped to floor 4 during the storm (12:07–12:25) means many threads
were idle even as 4 were active. After the storm eased, concurrency recovered
but by then many lifecycles were complete.

The static-wave barrier is the structural amplifier: even when AIMD allowed more
concurrency, the self-create track was serialized at the wave boundary —
wave N+1 did not start until the slowest node of wave N finished. Under the static
scheduler, `vpc-peering` (21 min) was in the LAST self-create wave and ran alone
after all other self-creators finished.

**Fix is A1 (dynamic dispatcher)** — this alone would have eliminated the serial
tail. The concurrency metric is expected to improve significantly on the next run
with `run_dynamic`.

---

## Summary: Ranked Improvement List

| Rank | Change | File | Expected effect | Confidence |
|------|--------|------|-----------------|-----------|
| 1 | Wire `dag_scheduler.run_dynamic` | `tools/dag_run_live.py:234` | ~15–20 min wall-time reduction | HIGH |
| 2 | Wire `update_durations` after run | `tools/dag_run_live.py` (after line 237) | Duration learning improves priority ordering each run | HIGH |
| 3 | Fix PrivateLink IP overlap | `gen-wave5-apigw-privatelink` lifecycle JSON | Deterministic 400 eliminated | HIGH |
| 4 | Fix filestorage teardown order | filestorage lifecycle JSON | Deterministic createvolume delete-conflict eliminated | HIGH |
| 5 | Re-run 5 storm-transient heavy failures | None (no code change) | 8 failures → ~3 expected clean | HIGH |
| 6 | Fix IDC `instance_id` prerequisite | `idc-*` lifecycle JSONs | 4 structural 400s eliminated | HIGH |
| 7 | Fix ASG launch-config capture order | `vs-autoscaling-coverage` lifecycle | Cascade 400 eliminated | MEDIUM |
| 8 | Fix LB-in-shared-subnet prereq | `gen-heavy-lb-members` + `heavy-shared-networking` | `SubnetNotAssociatedWithLoadBalancer` 400 eliminated | MEDIUM |
| 9 | Align `run_dynamic` dashboard events | `tools/dag_run_live.py on_event` | Live dashboard accurate during dynamic run | LOW |
| 10 | Add heavy-create stagger option | `dag_scheduler.py run_dynamic` | Reduces burst pressure during heavy phases | MEDIUM |
