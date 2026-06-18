# Handoff — 2026-06-18 session 2 (Claude remote, hand-driven runs)

> Pick-up note for a fresh session with the SCP env vars configured. This session
> **extends** `docs/HANDOFF-2026-06-18-claude-remote.md` (CI auto-trigger already
> disabled there). It lands the engine **identity-based create→show probe**, an
> **enrichment residual pass** (producer-match 90.9%→98.1%), a **FAST sweep mode**,
> and fixes a **cosmetic `-n 6` CI bug**. Read `CLAUDE.md` → `START_HERE.md` →
> `agents/CONTEXT.md` first; this file is the session delta + literal resume
> commands. All work is on branch `claude/adoring-heisenberg-7sem6u`.

## 0. Headline

**Execution is now fully hand-driven from the Claude remote env — runs AND
cleanup, NOT CI.** (CI auto-trigger was disabled the prior session, `1e3d90b`;
`api-test.yml` is `workflow_dispatch`-only.) Read-only smoke + live API calls are
verified working from this env. This session's substantive change is the engine
**stage-2 IDENTITY auto-probe** (`d2c3b25`): an id-bound GET resolves its
path-param by *which create produced the id* (enrichment sidecar `produced_by`),
not by capture-var string name — retiring the hand-maintained `_PARAM_ALIASES`
map (8/9 entries now redundant; proven OFFLINE only — **needs live proof next**).
Fed by an enrichment residual pass that raised producer-match to **98.1%
(960/979) + 19 honest waivers, 0 unexplained null** (`3a1c3c7`). Also landed: a
FAST `SCP_SWEEP_NOWAIT` sweep mode for the hand-driven cleanup, and a fix for a
**cosmetic-merge bug** where the `-n 2 → 6` parallelism lever was never actually
applied to the CI adopt-class pass (`c24a321`, `api-test.yml:587`).

> WARNING (cost incident this session): CI run **27735741382**, dispatched as a
> Tier-0 "LIGHT" run, actually went **HEAVY** because `vars.SCP_RUN_HEAVY=true`
> leaked at dispatch time (df8fb87) — it built billable DB/SKE clusters. It was
> cancelled and swept. This is *why* execution moved off CI: a stray repo var can
> silently flip the heavy gate. Set gates explicitly per hand-run, never as repo
> vars.

## 1. Resume commands (copy-paste)

### 1a. TOP / NEXT ACTION — prove the identity probe fires live

The identity probe is proven offline (`tests/offline/test_probe_identity.py`,
5 tests) but has **never fired against the live gateway**. The literal next step
is a small mutating run that creates id-bearing resources and confirms the probe
exercises their id-bound GETs by identity. Run a light CRUD slice with probe
tracing on:

```bash
# --- gates (light CRUD only; do NOT export SCP_RUN_HEAVY) ---
export SCP_ALLOW_MUTATIONS=true
export SCP_ALLOW_DESTRUCTIVE=true
# probe runtime knobs (defaults shown; tune live if reads are slow)
export SCP_PROBE_TIMEOUT_S=8 SCP_PROBE_MAX_PER_STEP=60

# 0. sanity: offline identity proof still green
python -m pytest tests/offline/test_probe_identity.py -q

# 1. pre-run reclaim (FAST), then a LIGHT lifecycle that produces ids + shows them
SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler
python -m regression.scenarios.validate

# 2. run ONE light create→show lifecycle and watch the probe line
#    grep the stdout for: "probe-reads[<service>]: N path-param GET(s) exercised"
python -m pytest tests/crud -m crud -k "iam or resourcemanager or kms" 2>&1 \
  | tee reports/probe-live.log
grep -E "probe-reads\[" reports/probe-live.log    # N>0 with NO _PARAM_ALIASES dependency = identity path fired

# 3. teardown (FAST)
SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler
```

Success criterion: an id-bound GET whose param was previously only reachable via
`_PARAM_ALIASES` (e.g. `registry_id`/`repository_id`/`certificate_id`) gets a 2xx
in the probe with the alias map effectively unused. Once seen, do item 3a below
(delete the 8 redundant alias entries).

### 1b. Full hand-driven suite run (mirrors the old workflow)

Unchanged from the prior handoff §1 except the cleanup now uses FAST mode. Gates
are non-negotiable — set per run, never to force a green test:

```bash
export SCP_ALLOW_MUTATIONS=true        # POST/PUT/PATCH
export SCP_ALLOW_DESTRUCTIVE=true      # DELETE (teardown)
# export SCP_RUN_HEAVY=true            # ONLY for billable VM/DB/K8s lifecycles

python -m regression.scenarios.validate
SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler   # pre-run reclaim (FAST)

python -m regression.scenarios.shared_infra --provision > shared_ids.txt
grep -E '^SCP_SHARED_[A-Za-z0-9_]+=.+' shared_ids.txt
set -a; . ./shared_ids.txt; set +a
eval "$(python -m regression.scenarios.shared_infra --print-filters)"

python -m pytest tests/smoke -m smoke                          # read-only smoke + read-chains
python -m pytest tests/crud  -m crud -n 6 -k "$PARALLEL_K"     # ADOPT-class CRUD, parallel (-n 6)
python -m pytest tests/crud  -m crud      -k "$VPC_CRUD_K"     # VPC-CRUD class, SERIAL

python -m regression.scenarios.shared_infra --teardown
SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler   # final sweep (FAST)
```

### 1c. FAST hand-driven cleanup (the canonical sweep command)

```bash
SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_IGNORE_TTL=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler
# tunables: SCP_SWEEP_ROUNDS (default 8 in nowait), SCP_SWEEP_ROUND_SLEEP_S (default 12)
```

`SCP_SWEEP_NOWAIT=true` skips the per-resource blocking `_wait_gone` (150–900s
each, serial) — it issues EVERY owned delete and lets the fixed-point round loop
retry whatever still 409s (dependency) next pass. Owner-tag scoping is UNCHANGED
(`_select`-gated). `SCP_SWEEP_IGNORE_TTL=true` is for an explicit cleanup when you
KNOW no other mutating run is live.

## 2. What landed this session (all on `claude/adoring-heisenberg-7sem6u`)

| Item | Commit | Status |
|---|---|---|
| Engine stage-2 IDENTITY auto-probe (`_resolve_param`/`_probe_reads`, `produced`/`produced_rtype` index; `_PARAM_ALIASES` demoted to legacy fallback) | `d2c3b25` | Landed; proven OFFLINE (5 tests) — needs LIVE proof |
| (A)/(B) enrichment residual pass → producer-match 90.9%→98.1% (960/979) + 19 waivers, 0 null (`spec/enrich_catalog.py`) | `3a1c3c7` | Landed; feeds the identity probe |
| `-n 2 → 6` cosmetic-merge bug FIXED (`api-test.yml:587`; `dab8a41` only updated comments/echo, hardcoded `-n 2` survived) | `c24a321` | Landed (CI only; hand-recipe already used `-n 6`) |
| FAST sweep mode `SCP_SWEEP_NOWAIT=true` in reconciler (issue-all-no-wait + round retry) | `d2c3b25`/sweep | Landed; this is the hand-driven cleanup |
| Execution model: runs + cleanup hand-driven from Claude remote (smoke + live API verified) | prior `1e3d90b` + this session | In force |
| Cost incident: CI run 27735741382 (Tier-0 "LIGHT") went HEAVY via `vars.SCP_RUN_HEAVY` leak (df8fb87) — billable DB/SKE clusters | n/a | Cancelled + swept |

SHAs (verify with `git log --oneline -6`): `3a1c3c7` enrich residual · `d2c3b25`
identity probe · `c24a321` `-n 6` fix · `b0b4075` prior handoff · `1e3d90b` CI
disabled · `dab8a41` merge (the one with the cosmetic `-n` bug).

## 3. Open items & next steps (prioritized)

1. **[TOP] Prove the identity probe fires LIVE** — run §1a. The probe is offline-
   proven only. Confirm an id-bound GET resolves by identity (sidecar `produced_by`)
   with the alias map unused before touching `_PARAM_ALIASES`.
2. **Replace `_PARAM_ALIASES` after live proof** — once §1a shows the identity path
   firing 2xx, delete the **8 redundant entries** (`engine.py` `_PARAM_ALIASES`);
   keep only `srn` (name-addressed, no producer in the catalog). Update the
   validated-facts entry (`knowledge/validated-facts.md` ~L652).
3. **Validate the `-n 6` speedup on the next heavy run** — at `-n 2` (run
   27735741382) the 4 DB engine families (epas 24m / mariadb 31m / mysql 43m /
   postgresql 44m) ran staggered at peak concurrency 2 → DB-phase wall **120 min**.
   `-n 6` should fan them to ≈ max(single engine ~44m), ~76m saved. Confirm via
   `audit.optimizer` that DBaaS wall drops sum→max.
4. **Implement the deletion-efficiency findings (audit agent) — NOT yet done**
   (only NOWAIT landed). In priority order from the audit:
   - **#1 parallelize teardown waits** (the per-resource serial waits are the
     residual bottleneck even with NOWAIT — NOWAIT skips them, true parallelism
     would also confirm completion).
   - **#2 PF-09: KMS/secret cross-process re-deletes** — pending-deletion items
     stay listed for the whole window so a round re-deletes already-deleted keys.
     `_DELETED_THIS_SWEEP` dedups WITHIN a process; cross-process (xdist) re-deletes
     are still wasted. (`cleanup/reconciler.py` ~L221-244.)
   - **#4 fixed-point re-list waste** — every round re-lists every collection from
     scratch; collections that reached 0-deletable should be skipped on later rounds.
5. **Review the 19 enrichment waivers** (the handoff brief calls this the "89-waiver
   review"; the 89 null self-params were resolved → 19 remain as honest waivers,
   `producer_kind="waiver"` in the sidecar / `_RESIDUAL_WAIVERS` in
   `spec/enrich_catalog.py`): confirm each is genuinely unproduceable (name-addressed
   resourcemanager key/resource_identifier, cloudmonitoring addrbookId EOL, scr
   tags_id docker-pushed) and not a missed producer.
6. **Carry-overs from prior handoff** (still open): promote Wave-A docs→VALIDATED on
   a clean light run; walk Tiers 1→4 of `docs/COVERAGE-MAX-PLAN.md`; confirm any
   still-live billable resources from the cancelled heavy run (27735741382) were
   reaped by the sweep.

## 4. Key facts

- **Execution is hand-driven from the Claude remote env** — runs AND cleanup. CI
  (`api-test.yml`) is `workflow_dispatch`-only (auto file-trigger disabled
  `1e3d90b`). Smoke + live API verified from this env. A stray `vars.SCP_RUN_HEAVY`
  flipped a "LIGHT" CI dispatch to HEAVY (run 27735741382) — never set gates as
  repo vars; export them per hand-run.
- **Identity probe (design A, stage 2):** `_resolve_param` priority = exact
  capture-var name → IDENTITY (sidecar `produced_by` → id recorded in
  `produced`/`produced_rtype`) → legacy `_PARAM_ALIASES`. Records read-only, never
  fails a lifecycle. Runtime guards: `SCP_PROBE_TIMEOUT_S=8`, `SCP_PROBE_MAX_PER_STEP=60`,
  `retry=False`. Sidecar: `data/api_catalog_params.json` (1186 endpoints).
  Offline proof: `tests/offline/test_probe_identity.py` (5 tests, all `produced_by`
  params incl. 8/9 alias targets resolve from an empty seed).
- **`_PARAM_ALIASES` is now legacy fallback** — 8 of 9 entries redundant
  (registry_id, repository_id, dbaas_engine_version_id, certificate_id,
  resource_group_id, security_group_id, security_group_rule_id, service_account_id);
  only `srn` (name-addressed, no producer) still needed. Delete the 8 after LIVE proof.
- **Enrichment producer-match 90.9%→98.1%:** 960/979 `produced_by` + 19 honest
  waivers = 0 unexplained null. Residual layer in `spec/enrich_catalog.py`
  (`_DBAAS_SERVICES`/`_RESIDUAL_EXPLICIT`/`_RESIDUAL_WAIVERS`), applied ONLY to null
  self-params so it can't regress the 890 mechanical matches. 4 parallel agents found
  the producers (evidence in `data/api_docs.json` + `knowledge/formal/resources/*.yaml`).
  Patterns: DBaaS instance_group_id/block_storage_group_id born in cluster DETAIL read
  `showcluster`; cross-service cluster_id ← `container/ske/createcluster` `$.resource_id`;
  pseudo-resource ops (kms key_id←createkey, secretvault). NOTE: `Endpoint.service` is
  bare (`mysql`), not `database/mysql`.
- **`-n 6` is the real lever** (`api-test.yml:587`); `dab8a41` only changed the
  comments/echo. IB-049 (xdist-gated adopter-skip) + IB-050 (pre-run reclaim +
  concurrency-group) are the cap-poisoning guards — NOT the `-n` value — so raising
  it is safe. Pending live validation.
- **FAST sweep (`SCP_SWEEP_NOWAIT=true`):** skips serial `_wait_gone`, retries 409s
  across rounds (`SCP_SWEEP_ROUNDS` default 8, `SCP_SWEEP_ROUND_SLEEP_S` default 12).
  Owner-tag scoping unchanged.
- **Security/hygiene:** never read/log/commit `.env`; gates are deliberate opt-ins;
  one mutating run at a time (owner rule); commit trailers required on this branch.
