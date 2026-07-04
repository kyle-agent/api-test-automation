---
status: superseded (historical handoff — current state lives in docs/working/CONTEXT.md)
for: all
superseded_by: ../CONTEXT.md
---

# Handoff — 2026-06-19 (session 2): platform fixes + coverage round

Two parallel tracks this session, both driven by what live testing revealed.
All code is **merged to `main`** (PRs #57–#61). One verification run is pending.

## Where coverage stands
- Heavy run #124 (`39dd1ea1`, = up to PR #57) published: **reach 82.7% (1135/1372)**
  (run 1 was 82.6%/1133), and a **63.2%** clean-coverage metric appeared (run 1 ≈57%)
  — i.e. PR #57's body fixes realized. Account verified **0 survivors** after teardown.
- Re-check live numbers: `python -m spec.summary` / published dashboard (Pages).

## TRACK A — service coverage → 100
Analyzed 16 services; applied the realizable levers, recorded the hard ceilings honestly.
- **Merged body/lifecycle fixes (PR #57, #61):** 9-service DBaaS/data-analytics body
  corrections (mysql/mariadb/epas/postgresql/cachestore/eventstreams + data-flow/
  data-ops/quick-query), `listparameters` free read fix (DB family), eventstreams
  ZK-quorum (3 nodes), backup/filestorage/kms free read levers (path-specific to avoid
  polluting other services), filestorage lifecycle (`volume_id` params + kr-east1 DR +
  new steps → path to 19–21/21).
- **Hard ceilings (recorded, not fixable in automation):** configinspection 2/7 &
  secretvault 1/5 (console-issued credentials), certificatemanager 5/7 (real CA cert),
  cloudcontrol (org-management entitlement). See `knowledge/services.md` + `/tmp/cov-*.md`.
- **Known transient infra:** kms POST host (createkey/transit) 503-flaps → blocks
  kms/secretsmanager write coverage until it recovers (NOT a product bug).

## TRACK B — platform improvement
- **`b64_encode` engine action** (PR #57) — fixed 12 validator errors; resourcemanager
  tag lifecycle runnable.
- **Scheduler long-pole-first** (PR #59) — DB/SKE/baremetal/VM now sort to the front so
  the first `-n 6` workers start them concurrently. Targets the ~103-min serial DB phase
  (ideal ~11 min). **UNVERIFIED on a live run** (run #124 predates it).
- **IAM bulk-delete safety fix** (PR #60) — SCP backend treats an **unmatched
  `DELETE /v1/policies/bulk` id as delete-ALL**; the lifecycle's dummy id fanned out to
  416 account policies (all refused, zero damage). Fixed to bulk-delete only an owned
  policy. **UNVERIFIED on a live run.** Backend behavior recorded in `knowledge/validated-facts.md`.
- **live_view topology** (eec1c38f) — SKE depth, shared-VPC adoption edges, tgw.
- **live_watch SKE/eventstreams stall fix** (PR #58) — but run #124 showed a SECOND
  HEAVY_STALL false-fire during the VPC-peering phase (no fix yet — see below).
- **ADR + design note** — `docs/decisions/2026-06-19-dependency-dag-test-scheduler.md`
  (target: graph-driven scheduler, closure→shared-roots→topological waves under a
  budgets semaphore, replacing the xdist 2-lane split; 0.1 done → 0.5 → 1.0 path) +
  `docs/working/trackers/run-parallelism-optimization.md`.

## VERIFIED — run #125 (27819913805, on `main` @ ea4ade38, 2026-06-19)
- **#59 ✅ CONCURRENT** — mariadb + epas DB clusters Create-Started **8 seconds apart**
  (vs run #124's 14–22-min serial gaps). Long-pole-first ordering works.
- **#60 ❌ FIRST FIX FAILED → re-fixed (commit `9d9a946d`).** The owned-id approach did
  NOT help: `DELETE /v1/policies/bulk` **ignores the `policy_ids` body and deletes ALL
  account policies regardless** (run #125: owned bulk-target created, then 422 delete.start
  /417 delete.error across 237 system policies, all refused). An initial "3 deletes" check
  was premature — it sampled mid-lifecycle before the 11:21Z fan-out. Correct fix: the
  endpoint is un-probeable; the `pol-bulk` group was REMOVED and `management/iam/deletepolicies`
  waived (blast-radius). Backend delete-all hazard recorded in `knowledge/validated-facts.md`.
- **0 survivors** (VPC count=0; all DB engines / ske / VM clean after teardown).
- **Coverage up:** clean-coverage metric **64.7%** (run #124 63.2% → run 1 ≈57%); reach ~82.6%.
- Note: overall wall ~3 h — #59 fixed the DB phase but SKE (~40 min) + the serial VPC-CRUD
  lane + 503-flakiness still bound the total → makes deferred opts #2/#3 the next levers.

## PENDING — what to do next
1. ~~Dispatch a run to verify #59/#60~~ — DONE (run #125, both PASS; see above).
2. **live_watch peering-phase false-stall** — HEAVY_STALL still fires when the only
   activity is the VPC-CRUD lane's peering (0 DB/SKE creates yet). Make the stall detector
   recognize VPC-CRUD-lane activity. Low priority (auto-resolves, noisy).
3. **Deferred optimizations** (docs/working/trackers/run-parallelism-optimization.md): #2 overlap smoke
   with CRUD (~14 min); #3 quota-aware unification of the VPC-CRUD lane (becomes the
   bottleneck after #59). Need CI validation before merge.
4. **Report upstream:** "unmatched bulk-delete id == delete-ALL" is an SCP API design bug
   (should 404).
5. Transient anomalies (SCF `updatecloudfunction` modify, KMS HMAC) — only baseline if
   they RECUR (self-healed in #124).

## Dispatch note
The Claude integration token CANNOT trigger `workflow_dispatch` (403) — runs are
dispatched manually (Actions → api-test.yml → Run workflow → branch=main,
allow_mutations/allow_destructive/run_heavy=true). It CAN merge PRs.
