# Handoff — 2026-06-19 (Claude remote): coverage push, per-service agents, live-watcher

Session driven hand-from the Claude remote env (CI auto-trigger OFF). Branch
`claude/adoring-heisenberg-7sem6u` — **all work committed + pushed, tree clean,
account verified 0 owned billable survivors.**

## Headline outcomes
- **Cumulative coverage: C3 47.9% → 50.1%** (633/1264), **C2-called 40.7% →
  53.4%** (733/1372), **+23 newly-verified endpoints**. Published to
  `dashboard-data` → Pages (`index.html`).
- **Heavy DBaaS run: complete + clean.** 8 real cluster creates (mysql/mariadb/
  epas/cachestore, peak concurrency 3, wall≈max), **22 DB sub-op id-GET 2xx**
  newly covered (+9 net on the id-GET counter). **0 survivors verified 3× + a 4th
  independent recheck**; shared VPC reclaimed (DELETE 204).
- **Three new standing designs shipped** (see below): per-service coverage agents,
  the live-watcher anomaly loop, and the record-to-git hard rule.

## Per-service coverage results (ledger: `data/coverage_ledger.json`)
| service | coverage | key lever / blocker |
|---|---|---|
| queueservice | **12/12** | `getqueueattributes` needs `attributes=All`(case-sensitive)+`name=` |
| resourcemanager | **27/27** | **all `{srn}`/`{key}` path segments are base64-encoded** (plain → 400) |
| apigateway | ~47/55 | `listreports` needs ≤30-day window (`{iso_29d_ago}`/`{iso_today}`); 503s transient |
| iam | 27/62 | read-only id-derivation; `createrole` 500 blocks role chain; rest entitlement/empty-collection |
| dns | 6/22 | `createpublicdomainname` 500 (product); rest behind heavy private-dns |
| cloudmonitoring | 6/18 | **`X-ResourceType` header CONFIRMED REQUIRED**; rest = monitoring-enrollment prereq |
| scr | 8/39 | borrow-and-read-by-id; ~16 need docker-push; 1 registry quota |
| data-ops | 5/17 | 12 need a billable Airflow cluster (heavy-prereq) |
| organization | 2/37 | **org-master entitlement wall** (account is a member, not master) |

## New standing designs (baked into the repo)
1. **Per-service coverage agents** — every service has its own agent whose sole
   mandate is to raise that service's coverage by any means (docs/test/logs/
   peer-ask). Spawnable: `.claude/agents/coverage-service.md`. Standing pattern +
   the hard **~6–8 concurrency cap** (14 concurrent saturated the gateway):
   `agents/service-agent.md`. Targeting + resumable state:
   `python -m tools.coverage_headroom` → `data/coverage_ledger.json` (per-service
   `covered/total/gap` + agent-authored `blockers` + `next_levers`).
2. **live-watcher loop** — `.claude/agents/live-watcher.md` + `tools/live_watch.py`
   (deterministic detector: `HEAVY_STALL`, `INFRA_QUIET`, `BILLABLE_SURVIVOR`,
   `WATCH_DEGRADED`; delta-only output, cross-checks live API + results store).
   Run it as a `Monitor` during any in-flight run. Loop: **watcher detects →
   orchestrator confirms → orchestrator contacts the dev/coverage/heavy agent.**
   It proved itself live this session by catching the stalled heavy run.
3. **record-to-git hard rule** — HARNESS #6 + the coverage-service spec: every
   agent commits its findings (knowledge/scenario/ledger/baseline) every run; the
   container is ephemeral.
4. **optimizer agent** (from the prior session, now wired): `tools/analyze_run.py`
   + `conftest.py pytest_sessionfinish` (async, every run) + `.claude/agents/
   log-optimizer.md`; trend rows in `data/optimizer/history.jsonl`.

## Key findings recorded (knowledge/ + baselines)
- **id-GET mechanism works**: create→get-by-id is fine; the ~149 "uncovered"
  id-GETs were almost all blocked on **heavy producer lifecycles being gated off**,
  not a linking/substitution bug. The literal-`{cloud_function_id}` 404 is
  by-design (write recorded, read skipped).
- **Heavy-run env-propagation gotcha**: the first heavy run created 0 clusters
  because `SCP_RUN_HEAVY` + `SCP_SHARED_*_ID` were **not inherited by the pytest
  subprocess** → every DB lifecycle hit the heavy gate and *skipped*. Fix: export
  the gates + shared-VPC ids in the **same shell** as `python -m pytest`. (DNS
  503s on eventstreams/searchengine were a red herring.)
- **New product-500s baselined** (`data/baselines/known_issues.json`): PF-23
  apigateway `createprivatelinkendpoint` (body docs-verified correct), 10 DBaaS
  sub-op 500s (set-archive/register-log-export-config/upgrade-kernel × 4 engines),
  postgresql `createcluster` 500 (4 other engines create fine w/ identical body),
  dns `createpublicdomainname` 500.
- **base64-SRN** (resourcemanager) is likely **cross-cutting** — iam's stuck
  `srn`-targeted ops (setresourcepolicy/addpermission, currently "needs real srn")
  may just need the same b64 encoding. **Untested — high-value next probe.**

## Live-view (`audit/live_view.py`) fixes this session
- DB clusters now render at **stage 2 (under subnet)**, not the overloaded-kind
  depth 6; hyphenated names (cache-store/search-engine/event-streams) mapped.
- `--mode exec` (test-log execution view), instance relationship lines +
  click-to-highlight chains, kms/secret `삭제예정` via `--live-state`, failed-create
  `생성실패`, ours-only filtering.
- **Resilient harvest**: a flaky empty loggingaudit pull no longer blanks a good
  page (writes temp, keeps last good data, retries 3×).
- Publish helpers: `tools/publish_live.sh` (live.html loop, pass `--live-state`),
  `tools/publish_dashboard.sh` (cumulative dashboard).

## What to advance next (ranked)
1. **Full heavy batch (billable) — the biggest remaining lever.** This session's
   heavy run was **DB-engines only**; the rest of the id-GETs need the
   compute/network/storage heavy lifecycles (servers/volumes/loadbalancers/
   baremetal/ske). Run with `SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true
   SCP_RUN_HEAVY=true SCP_SHARED_VPC_ID=… …` **exported in the same shell as
   pytest** (the gotcha above). Reuse the shared-VPC adopt pattern; arm the
   live-watcher Monitor; verify 0 survivors 3×.
2. **Cheap batch 3** (free): iam-identity-center, cloudcontrol, billingplan,
   aimlops-platform, data-flow, direct-connect, quick-query — `python -m
   tools.coverage_headroom --cheap-only --exclude <done>` for the worst-gap-first
   list. Spawn `coverage-service` agents, capped ≤6–8.
3. **base64-SRN cross-probe**: re-run the iam srn-targeted ops with b64-encoded
   `srn` (resourcemanager finding) — may recover ~5 iam endpoints for free.
4. **Optimizer windowing**: `tools/analyze_run.py` efficiency metric is noisy
   because it spans the cumulative store; add per-`--label` windowing so per-run
   parallelism is honest.
5. **Watcher polish** (optional): tolerate a single transient 503 before
   `WATCH_DEGRADED`; clear `reports/audit/heavy_batch_start.txt` at batch end
   (done manually this session) so it auto-resets.

## Operating notes for the next session
- Re-verify everything (memory discipline): `python -m spec.summary`,
  `python -m tools.coverage_headroom`, current `git log`. Numbers above are
  2026-06-19 snapshots.
- Safety gates are opt-ins; never set them "to make a test pass". DELETE needs
  BOTH `SCP_ALLOW_MUTATIONS` AND `SCP_ALLOW_DESTRUCTIVE` (the sweep's first pass
  this session deleted 0 with DESTRUCTIVE-only — mutation-blocked).
- One heavy run at a time. Owner-tag teardown only; never name-guess.
- Account is **clean** as of 2026-06-19T02:08Z (0 owned VPC/subnet/cluster).
  Pre-existing foreign `ske` nodepool `regrnp8621dcdc` is NOT ours — leave it.
