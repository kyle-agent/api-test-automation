---
status: superseded
for: all
---

# COVERAGE-GAP-REPORT — what is currently NOT covered (C3 gap)

> Status report, 2026-06-18. **Grounded in live numbers re-measured this session**
> (commands + outputs quoted inline), not memory. Per the result contract /
> Hard Rule 5, every remembered number was re-verified before use; observed
> state wins on conflict. The headline metric is **C3 (검증됨 = a real run got a
> 2xx)** per `docs/COVERAGE-CRITERIA.md`. This is a *status* report (what is
> uncovered and why), not a dispatch plan — the plan is `docs/working/plans/COVERAGE-MAX-PLAN.md`.

---

## 1. Headline: 100% reachable vs ~45% verified — the gap is DISPATCH-GATED

| Measure | Value | Source (this session) |
|---|---|---|
| Catalog size | **1,372** endpoints, 0 unresolved | `python -m spec.summary` |
| C1 static reachability (heavy **ON**) | **100.0%** (1372/1372), **GAP = 0** | `python -m spec.coverage_gap` |
| C1 static reachability (heavy **OFF**) | **52.9%** (726/1372), GAP = **646** | `python -m spec.coverage_gap --no-heavy` |
| Last published live coverage | **C3 ≈ 44.79% / cov_op ≈ 36.73%** | `docs/working/CONTEXT.md` (run history, run 27725293499 era) |
| Approved waivers | **249** | `data/baselines/coverage_waivers.json` |

Verbatim `python -m spec.coverage_gap` (heavy ON):

```
Static coverage ceiling (heavy=on):
  endpoints           : 1372
  reachable now       : 1372 (100.0%)
    - smoke GET floor  : 225
    - via scenarios    : 1147
  GAP (need scenarios): 0
    - id-bound GETs    : 0
    - write ops        : 0
```

Verbatim `python -m spec.coverage_gap --no-heavy` (heavy OFF):

```
Static coverage ceiling (heavy=off):
  endpoints           : 1372
  reachable now       : 726 (52.9%)
    - smoke GET floor  : 225
    - via scenarios    : 501
  GAP (need scenarios): 646
    - id-bound GETs    : 123
    - write ops        : 523
```

**The finding.** *Authoring* is finished: every one of the 1,372 endpoints is
reachable by an enabled or heavy-gated lifecycle, so the offline ceiling is
**100%** with GAP 0 (getid 0, write 0). The gap between **100% reachable** and
**~45% verified (C3)** is therefore **not a missing-scenario gap** — it is
endpoints that have **never been executed against the live API with a 2xx**.
Closing it is **DISPATCH-GATED**: it requires actually *running* the lifecycles
on `main` with the mutation/destructive/heavy safety gates opened (which create
real, billable cloud resources), triaging failures, and fixing bodies. Adding
more offline scenarios does not move C3. This matches the grounded conclusion in
`docs/working/plans/COVERAGE-MAX-PLAN.md` ("offline coverage is already maxed; every further
gain is dispatch-gated").

**The heavy-only frontier.** The single biggest structural driver of the gap is
the **646 endpoints that become unreachable without `SCP_RUN_HEAVY`** (100% → 52.9%
when heavy is turned off). That 646 set **is** the bulk of the remaining C3 gap:
**123 id-bound GETs + 523 write ops** that are only exercised by heavy/billable
lifecycles. Until a heavy run actually executes them, they stay unverified.

Live evidence floor: `reports/results/observations.jsonl` currently holds **107
observations**, all `source=smoke` (79 ok / 21 soft / 7 fail), covering **85
distinct endpoint keys, 66 with a 2xx**. This is only the latest-run smoke
residue on disk (the unified store is gitignored and overwritten per run); the
published C3 ≈ 44.79% reflects the full Tier-0 light run + heavy history, not
just this file.

---

## 2. Per-service uncovered table

Below: every service that still has a **light (no-heavy) coverage gap**, sorted
by gap size. `uncovered (no-heavy)` = endpoints unreachable without heavy =
`coverage_gap --no-heavy` per-service `gap`. `heavy-only` is that same number
(by construction, with heavy ON every service has gap 0, so the entire no-heavy
gap is heavy-only). `id-bound GET` and `write` decompose it. `waivers` = entries
in `coverage_waivers.json` for that service.

| service | uncovered (no-heavy) | of which id-bound GET | of which write | waivers | note |
|---|---:|---:|---:|---:|---|
| networking/vpc | 49 | 11 | 38 | 0 | endpoint/nat/tgw/privatelink frontier (IB-012/013); pulls TGW/FS closure |
| database/epas | 39 | 5 | 34 | 0 | heavy DB cluster + ~15 sub-ops |
| database/postgresql | 39 | 5 | 34 | 0 | **cluster already VALIDATED** — sub-ops cheapest heavy (Tier 1) |
| database/mariadb | 38 | 5 | 33 | 0 | heavy DB cluster + sub-ops |
| database/mysql | 37 | 5 | 32 | 0 | heavy DB cluster + sub-ops |
| storage/baremetal-blockstorage | 36 | 5 | 31 | **39** (billing-prohibitive) | rides a bare-metal server; waived = billable |
| database/sqlserver | 31 | 4 | 27 | **33** (reachability) | license-gated; reachability-only (owner override 2026-06-16) |
| networking/loadbalancer | 29 | 9 | 20 | 0 | LB lifecycle |
| management/organization | 28 | 5 | 23 | **23** (blast-radius) | writes can sever account hierarchy — reads only, owner decision |
| compute/virtualserver | 26 | 9 | 17 | 0 | ASG family + image registration remainder |
| database/cachestore | 24 | 3 | 21 | 0 | heavy DB cluster + sub-ops |
| management/iam-identity-center | 24 | 5 | 19 | **32** (reachability) | reachability-only, synthetic/all-zero-id safety rail |
| storage/backup | 22 | 10 | 12 | **8** (unsatisfiable-flow) | agent + policy/restore chain; some flows unsatisfiable |
| data-analytics/searchengine | 21 | 2 | 19 | **22** (reachability) | license-gated; reachability-only |
| storage/archivestorage | 19 | 6 | 13 | **25** (reachability) | reachability-only (owner override) — called regardless of 4xx |
| data-analytics/vertica | 18 | 2 | 16 | **19** (reachability) | license-gated; reachability-only |
| container/ske | 17 | 2 | 15 | 0 | cluster + nodepool (upgrade already LIVE-PROVEN) |
| data-analytics/eventstreams | 17 | 2 | 15 | 0 | DBaaS-style cluster |
| management/iam | 14 | 1 | 13 | 0 | user/role family (some Planning-form gated) |
| compute/baremetal | 13 | 1 | 12 | **14** (billing-prohibitive) | physical provisioning, billable |
| networking/dns | 12 | 4 | 8 | 0 | superseded-private records |
| compute/multinodegpucluster | 11 | 2 | 9 | **13** (billing-prohibitive) | GPU provisioning, billable |
| management/cloudcontrol | 11 | 2 | 9 | 0 | control-plane writes |
| storage/filestorage | 10 | 2 | 8 | 0 | volume + replication |
| ai-ml/aimlops-platform | 9 | 6 | 3 | 0 | mostly id-bound GETs |
| data-analytics/data-flow | 8 | 0 | 8 | 0 | NiFi = SKE-on-k8s (IB-018, bodies UNPROVEN) |
| data-analytics/data-ops | 8 | 0 | 8 | 0 | Airflow = SKE-on-k8s (IB-018, bodies UNPROVEN) |
| networking/vpn | 8 | 2 | 6 | 0 | VPN gateway/tunnel chain |
| ai-ml/cloud-ml | 7 | 4 | 3 | 0 | gated on SCR auth key + heavy |
| data-analytics/quick-query | 7 | 0 | 7 | 0 | Trino = SKE-on-k8s (IB-018); validate dry-run is light |
| networking/direct-connect | 7 | 2 | 5 | 0 | billable physical interconnect |
| financial-management/billingplan | 4 | 1 | 3 | 0 | create body corrected; one 500 baselined (server bug) |
| storage/parallel-filestorage | 3 | 1 | 2 | 0* | reachability-only (owner override); *7 reachability waivers, gap = 2 writes + 1 getid |
| **TOTAL (no-heavy gap)** | **646** | **123** | **523** | — | |

Notes:
- Every service NOT in this table has **no-heavy gap = 0** — fully reachable by
  light (non-heavy) lifecycles already and so verifiable without billable spend.
- Waivers total **249** across the whole catalog. By class:
  **reachability 138** (`searchengine 22, vertica 19, sqlserver 33,
  archivestorage 25, iam-identity-center 32, parallel-filestorage 7`),
  **billing-prohibitive 66** (`baremetal-blockstorage 39, baremetal 14,
  multinodegpucluster 13`), **blast-radius 23** (`organization` writes),
  **entitlement 14** (`management/cloudmonitoring` — note: cloudmonitoring has
  no-heavy gap 0, so its 14 waivers are not in the table above),
  **unsatisfiable-flow 8** (`storage/backup`). Source: `coverage_waivers.json`.

---

## 3. Categorizing the uncovered

### (A) Light-reachable but not-yet-run
Endpoints that are reachable **without** heavy and have no waiver — a light
dispatch (`mutations=true destructive=true heavy=false`) can verify them with
**zero billable spend**. These are exactly the **726 light-reachable** endpoints
(225 smoke GET floor + 501 via light scenarios) minus those already 2xx-verified.
The freshly-materialized **Wave A.1 batch** (`generated__waveA1.json`, 16 nodes +
`generated__waveA-lookups.json`, 3 lookups) lives here. This is the cheapest C3
to close and the reason Tier 0 is "DO THIS FIRST" in `COVERAGE-MAX-PLAN.md`.
*Status note (CONTEXT.md):* the last Tier-0 light run (27725293499) passed
smoke+read-chains and 134 light CRUD, but a workflow heavy-gate leak meant
`heavy=false` did not reach `SCP_RUN_HEAVY`; the archivestorage 401 fix has
landed and a clean re-run is the next action.

### (B) Heavy-only (DB / SKE / VM / networking frontier)
The **646 heavy-only endpoints** — unreachable without `SCP_RUN_HEAVY` because
they ride a real, billable cluster/server. This is the structural majority of
the C3 gap and is concentrated in:
- **DB engines** (`epas 39, postgresql 39, mariadb 38, mysql 37, sqlserver 31,
  cachestore 24, searchengine 21, vertica 18, eventstreams 17`) — **~190 writes**,
  the single biggest block. Each engine builds one cluster and validates ~15
  sub-ops in the same run. **PostgreSQL cluster is already VALIDATED**, so its 7
  sub-ops are the cheapest heavy endpoints in the catalog.
- **networking** (`vpc 49, loadbalancer 29, dns 12, vpn 8, direct-connect 7`).
- **compute** (`virtualserver 26, baremetal 13, multinodegpucluster 11`).
- **storage** (`baremetal-blockstorage 36, backup 22, filestorage 10,
  parallel-filestorage 3`).
- **container/ske 17** (upgrade chain already LIVE-PROVEN, run 27492496266).
These require live dispatch on `main` with heavy gates open and cannot be closed
offline.

### (C) Waiver / unsatisfiable (license / console / EOL / blast-radius)
**249 approved waivers** that can never 2xx on the shared test account by design.
They stay C2-covered (called; the 4xx is the evidence) but leave the C3 verified
denominator — **except** the 138 `reachability` waivers, which (per the 2026-06-16
re-add) STAY in the C3 target as "covered-when-reached" (any response, incl. 4xx,
= access confirmed; never folded into verified-2xx). Breakdown:
- **blast-radius (23)** — `organization` writes (sever/deny account hierarchy,
  largely irreversible); reads stay in target.
- **billing-prohibitive (66)** — `baremetal-blockstorage 39, baremetal 14,
  multinodegpucluster 13` (physical/GPU spend).
- **entitlement (14)** — `cloudmonitoring` (license/console).
- **unsatisfiable-flow (8)** — `backup` flows with no satisfiable precondition.
- **reachability (138)** — `searchengine 22, vertica 19, sqlserver 33,
  archivestorage 25, iam-identity-center 32, parallel-filestorage 7`: license-gated
  or owner-excluded services now run **access-check-only** (tolerant expect, 4xx
  tolerated, no real license/mutation), reachability-TERMINAL not held.

### (D) id-bound GETs auto-covered at runtime by the identity probe
**123 of the 646 heavy-only uncovered** are **id-bound GETs** (need a resource
id from a prior create). They are NOT separately authored — once the create in
their lifecycle runs, the new identity probe / read-chain captures the id and
fires the GET automatically. So heavy ON shows getid gap **0**: with the cluster
up, these GETs cover themselves. This is why live `cov_op` runs higher than the
static no-heavy figure would suggest — they convert from "uncovered" to "covered"
the moment their owning create gets a 2xx, at no extra dispatch cost.

---

## 4. What closes the gap, by lever

**What a light hand-driven run would close (no billable spend):**
A single Tier-0 light dispatch (`mutations=true destructive=true heavy=false`)
verifies the **light-reachable, not-yet-2xx** surface — category (A): the 726
light-reachable endpoints (small config resources — resource-groups,
network-logging storage, servicewatch alerts/dashboards/log-groups, quick-query
dry-run, the Wave A.1 batch) — all created and torn down. It promotes
**quick-query-validate, alert, cm-account-resource, gpu-node-image, cloudml-image,
volume-type** docs→VALIDATED on a 2xx. No heavy/billable resources.

**What needs heavy (billable, owner-go, serial):**
Category (B): the **646 heavy-only endpoints** (523 writes + 123 id-bound GETs).
These require live heavy runs that build real clusters/servers — DB engine
families (~190 writes), networking/vpc closure (49), baremetal-blockstorage (36),
loadbalancer (29), virtualserver remainder (26), ske (17), GPU/baremetal compute.
Owner-rule serial, one engine/service per dispatch. Cheapest first: PostgreSQL
sub-ops on the already-VALIDATED cluster (Tier 1).

**What stays waived (never a 2xx by design):**
Category (C): **111 of the 249 waivers** in the EXCLUDED classes (blast-radius 23,
billing-prohibitive 66, entitlement 14, unsatisfiable-flow 8) leave the C3
denominator permanently. The **138 reachability-class** endpoints stay in the
target as covered-when-reached (4xx = access evidence), so a reachability run
"closes" them without ever claiming a verified 2xx.

---

## Sources cited
- `python -m spec.coverage_gap` / `--no-heavy` (run this session — outputs quoted §1)
- `python -m spec.summary` (run this session — 1372 endpoints, method split)
- `docs/working/CONTEXT.md` (published C3 ≈ 44.79% / cov_op ≈ 36.73; Tier-0 run 27725293499 triage)
- `docs/working/plans/COVERAGE-MAX-PLAN.md` (heavy-only frontier table; dispatch tiers)
- `data/baselines/coverage_waivers.json` (249 waivers; class breakdown)
- `reports/results/observations.jsonl` (107 smoke obs, 66 distinct 2xx keys — latest-run residue)
- `docs/COVERAGE-CRITERIA.md` (C2/C3 definitions, waiver classes)
