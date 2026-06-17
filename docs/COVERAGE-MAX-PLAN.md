# COVERAGE-MAX-PLAN — how to drive C3 coverage to its ceiling

> Written 2026-06-17 (autonomous session). Grounded in **live** numbers
> re-measured this session, not memory. The headline metric is **C3 (검증됨 =
> a real run got a 2xx)** per `docs/COVERAGE-CRITERIA.md`. This plan is the
> prioritized, costed dispatch roadmap; each tier carries a copy-paste
> `.github/run-request` block.

## The one thing to understand first

**Offline coverage is already maxed. Every further gain is dispatch-gated.**

| Measure | Value | Source (this session) |
|---|---|---|
| C1 static reachability (heavy ON) | **100.0%** (1372/1372, gap 0) | `python -m spec.coverage_gap` |
| C1 static reachability (heavy OFF) | **52.9%** (726/1372) | `python -m spec.coverage_gap --no-heavy` |
| Last published live coverage | **C3 ≈ 44.79% / cov_op ≈ 36.73%** | `agents/CONTEXT.md` (run history) |
| Resource model | 275 nodes, **131 VALIDATED / 144 docs** | `python knowledge/formal/validate.py` |

So: *authoring* (writing scenarios that can reach an endpoint) is finished —
every endpoint is reachable by an enabled or heavy-gated lifecycle. The gap
between **100% reachable** and **~45% verified** is **endpoints that have never
been executed against the live API with a 2xx**. Closing it requires running
the lifecycles, triaging the failures, and fixing bodies — there is no offline
shortcut, and adding more scenarios for already-reachable endpoints does not
move C3.

**Why this plan instead of an autonomous dispatch:** firing a run means pushing
`.github/run-request` to **`main`** (the only branch the workflow triggers on)
with mutation/destructive/heavy gates that create **real, billable** cloud
resources. That is an outward-facing, hard-to-reverse, spend-incurring action
on a different branch than the assigned one — it needs an explicit human go.
Everything below is teed up so each tier is a single commit-to-main away.

**Owner-rule (non-negotiable):** one workflow run at a time. Before pushing any
run-request, confirm the previous run — **sweep job included** — has fully
concluded (`actions_list status != in_progress`). As of writing the lane is
**CLEAR** (0 in-progress).

---

## The C3 frontier — where the uncovered endpoints actually are

`coverage_gap --no-heavy` says **646 endpoints become unreachable without
heavy** — i.e. they are *only* exercised by heavy/billable lifecycles. That set
**is** the bulk of the remaining C3 gap, concentrated in:

| service | heavy-only gap | of which write | note |
|---|---|---|---|
| networking/vpc (endpoint/nat/tgw/privatelink) | 49 | 38 | IB-012/013 frontier; pulls TGW/FS closure |
| database/epas · postgresql · mariadb · mysql · sqlserver · cachestore | 39·39·38·37·31·24 | ~190 writes | the single biggest block; PG cluster already VALIDATED |
| storage/baremetal-blockstorage | 36 | 31 | rides a bare-metal server |
| networking/loadbalancer | 29 | 20 | LB chain |
| management/organization | 28 | 23 | **blast-radius / billable** — owner decision |
| compute/virtualserver (remainder) | 26 | 17 | ASG family + image registration |
| management/iam-identity-center | 24 | 19 | reachability-only (owner override) |
| storage/backup | 22 | 12 | agent + policy/restore chain |
| data-analytics/searchengine·vertica·eventstreams | 21·18·17 | ~50 | DBaaS clusters; vertica/searchengine license-gated reachability-only |
| container/ske | 17 | 15 | cluster + nodepool (upgrade already proven) |
| compute/baremetal · multinodegpucluster | 13·11 | 21 | physical/GPU provisioning |
| management/iam | 14 | 13 | user/role family (some Planning-form gated) |
| networking/dns · vpn · direct-connect | 12·8·7 | 19 | dns superseded-private; direct-connect billable |
| data-analytics/data-flow · data-ops · quick-query | 8·8·7 | 23 | NiFi/Airflow/Trino = SKE-on-k8s (IB-018, bodies UNPROVEN) |
| financial/billingplan · storage/parallel-fs | 4·3 | 5 | billingplan 500 baselined; pfs reachability-only |

---

## Dispatch tiers (cheapest ROI first)

### Tier 0 — LIGHT run (cheapest, ~15–25 min, low blast radius). DO THIS FIRST.
Exercises the enabled light set + the freshly-materialized Wave A batch
(`generated__waveA1.json` 16 + `generated__waveA-lookups.json` 3). Creates only
small config resources (resource-groups, network-logging storage, servicewatch
alerts/dashboards/log-groups, a quick-query dry-run) — all torn down. **No heavy
spend.** Promotes **quick-query-validate, alert, cm-account-resource,
gpu-node-image, cloudml-image, volume-type** docs→VALIDATED on a 2xx.

```
# .github/run-request  (commit to main)
mutations=true
destructive=true
heavy=false
conformance=false
```
Expected: lifts C2/C3 across the 726 light-reachable surface; 0 billable
resources. After it concludes, triage `reports/results` / oplog, promote nodes,
strike VALIDATION-QUEUE Wave A.1 rows.

### Tier 1 — HEAVY, cheapest: PG sub-ops on the ALREADY-VALIDATED cluster
VALIDATION-QUEUE rank 36. The PostgreSQL cluster is VALIDATED, so its 7 heavy
sub-ops (add/resize block-storage, resize-server-type, switchover, restore,
patch, kernel-upgrade) validate **without building a new cluster** — the
cheapest heavy endpoints in the catalog (~34 pg writes + 5 getid).

```
mutations=true
destructive=true
heavy=true
crud_filter=gen-heavy-pg or postgresql
```
(Note: `gen-heavy-pg-restore` / `gen-heavy-pg-upgrade` are currently
`enabled:false` — flip them on in `lifecycles/generated__heavy-pg.json` first,
or widen the filter to the enabled pg sub-op lifecycles. Re-validate: `python -m
regression.scenarios.validate`.)

### Tier 2 — HEAVY DB cluster families (mysql · mariadb · epas)
The biggest single chunk (~190 DB writes). Each run **builds one cluster** and
validates its ~15 heavy sub-ops in the same run (VALIDATION-QUEUE ranks 37–39).
Run **one engine per dispatch** (VPC-capped, serial, billable). Order by
deepest-modeled first.

```
mutations=true
destructive=true
heavy=true
service=mysql        # then a separate run each for mariadb, epas
```

### Tier 3 — HEAVY networking / storage / compute / analytics frontier
One service (or tight closure) per dispatch, owner-rule serial:
- `service=loadbalancer` (LB chain, 29) · `service=baremetal-blockstorage` (36,
  rides bare-metal) · `service=baremetal` (13) · `service=virtualserver`
  (ASG family remainder, 26) · `service=ske` (17) · `service=vpn` (8).
- networking/vpc IB-012/013 frontier (endpoint/nat/tgw/privatelink, 49) — the
  READY-FOR-LIVE set; pulls a heavy TGW/FS closure, schedule on the shared VPC.
- data-analytics DBaaS: `service=searchengine|vertica|eventstreams`
  (license-gated ones are reachability-only — endpoints CALLED, 4xx/5xx
  tolerated, no license consumed).

```
mutations=true
destructive=true
heavy=true
service=<one service from the list above>
```

### Tier 4 — gated / billable singletons (explicit owner decision each)
Do **not** dispatch without a per-item call — these need a credential, a license,
or carry blast-radius/cost the shared account shouldn't absorb:
- **Credential-gated:** scr-image/scr-tag/cloud-ml (`scr-auth-key`),
  secretvault (`iam-temp-auth-key`), diagnosis (`inspectable-account-auth-key`),
  sqlserver Always-On (`sqlserver-license`, IB-017), sts-token (real role SRN —
  Planning form), trail (`account_id` wiring), iam-user/group-member/
  resource-policy/role-policy (Planning-form ids).
- **Blast-radius / billable provisioning:** management/organization (28,
  irreversible account-wide), management/cloudcontrol (needs org-master),
  networking/cdn · gslb · direct-connect (provision real billable circuits/
  distributions despite not being model-`heavy` — treat as heavy).
- **Waiver candidates (can never 2xx — keep at C2):** certificate-import
  (unsatisfiable PEM), account-budget (PF-04 backend 500), billingplan create
  (backend 500), cm-event/cm-event-policy/cm-addrbook (cloudmonitoring EOL,
  IB-034). For these, the honest 100% path is `coverage_waivers.json` (C2 +
  waived), not a green C3.

---

## After every run (the triage→promote loop)
1. Confirm the run + sweep concluded; pull `reports/results/*.jsonl` (or oplog).
2. `fail_new` must stay 0 — triage each new fail via the L0→L3 ladder
   (`agents/orchestrator.md`): body fix → re-dispatch, or baseline/waiver a
   confirmed product bug (never relax a gate to make a test pass).
3. Promote docs→VALIDATED on a real 2xx (IB-041 evidence), strike the
   VALIDATION-QUEUE row, surface unblocked dependents.
4. Replace the matching hand-written lifecycle with the validated `gen-*` once
   green (M5 goal), update `agents/CONTEXT.md` + `knowledge/`.

## What "100%" honestly looks like
`C3 + waived(C2) == 1372`. A realistic terminal state is **C3 on every
non-gated endpoint + a reviewed waiver list** for the unsatisfiable/
blast-radius/license-gated tail (organization writes, certificate-import,
license-only engines, EOL cloudmonitoring). Driving C3 up is Tiers 0–3;
closing the rest is the Tier-4 waiver review.
