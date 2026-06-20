---
status: active
for: all
---

# The Agent Team — design & operating model

This repo is **built and operated by a team of AI agents** — roles played by a
Claude Code *lead session* and the subagents it spawns via the `Task` tool. There
is no separate daemon. This document is the team's **durable definition**: any
fresh session reconstitutes the team from here + `knowledge/`. The live, changing
state is **not** here — it is `docs/working/CONTEXT.md` (current coverage/campaign
status) and `data/coordination/ledger.json` (the machine blackboard).

> Consolidates the former `agents/` directory (README · AUTONOMOUS-LOOP · CAMPAIGN
> · HARNESS · PROMPTS · the orchestrator loop-essays · the per-role files). The
> **executable** agents live in `.claude/agents/` (Claude Code's real convention);
> everything else is a role the lead session plays or spawns ad hoc.

## 1. The roster

| Role | What it does | Runs as |
|------|--------------|---------|
| **Orchestrator** | Plans the next slice, delegates it, integrates results, keeps shared state current | **the lead session** (not a spawned worker) |
| **Spec-Intel** | Keeps `data/` (catalog/bodies/docs) fresh; diffs versions; flags affected endpoints | `.claude/agents/spec-intel` |
| **Domain-Knowledge** | Curates `knowledge/` + declarative scenario data (call order, deps, quotas) | `Task` (ad hoc) |
| **Service agent** | Per-service expert; raises one service's coverage by any legitimate means | `.claude/agents/coverage-service` |
| **Regression** (AXIS 1) | Smoke + CRUD lifecycles; widen coverage toward 100%, record observations | activity (pytest), lead-driven |
| **Conformance** (AXIS 2) | Static + runtime defect detection vs a baseline (only NEW defects alarm) | `.claude/agents/conformance` |
| **AI-Evaluator** | "Can a third-party AI use this API?" lens → feeds conformance | `.claude/agents/ai-evaluator` |
| **Dashboard** | Build + publish the unified dashboard from the results store | `.claude/agents/dashboard` |
| **Coverage-Validator** | Standing loop: promote `docs`→`VALIDATED` nodes on a real 2xx (masked-defect-safe) | `.claude/agents/coverage-validator` |
| **Optimizer** | After every run, mine the logs → ranked improvements (never relaxes a gate) | `.claude/agents/log-optimizer` |
| **Live-Watcher** | Watches in-flight runs for anomalies (stall/leak/orphan); reports, never fixes | `.claude/agents/live-watcher` |

## 2. Execution model

- The **lead session** is the orchestrator: it reads current state, decides what to
  advance, and either does the work or delegates a bounded slice.
- A **subagent** is launched with `Task`, given a pointer to its role + the relevant
  `knowledge/` files and a concrete bounded goal. Only its concise result re-enters
  the lead context — so delegate read-heavy/exploratory work. Launch independent
  units in parallel (one message, many `Task` calls).
- **Long test executions** run as GitHub Actions (`.github/workflows/api-test.yml`);
  locally, drive them with the commands in §6.

## 3. Operating loop — 4 tracks, always concurrent

The lead acts as **Meta-Orchestrator**: it owns the shared index, commits/pushes,
gates live-run dispatch serially, health-checks the tracks, and calls the owner
**only** on a STOP-6 hit (§4). It keeps **≥3 tracks busy** every round (top up the
ready-queue with conflict-free units on each wake).

| Track | Purpose | Sub-agents (ephemeral `Task` workers) |
|-------|---------|----------------------------------------|
| **① Platform** | Improve the platform itself | `platform-improver`, `tools-developer` |
| **② Coverage** | Widen test coverage | `coverage-coordinator`, `docs-mapper`×N, `dependency-resolver`, `lifecycle-composer`, `live-verifier` |
| **③ Watcher** | Meta-verify other tracks' output (separate checker) | `output-reviewer` (never reviews its own track), `drift-detector` |
| **④ Problem-Finder** | Actively hunt defects/debt | `product-defect-finder`, `failure-pattern-clusterer`, `debt-finder` |

**Round (~30–60 min):** dispatch (fan out to all tracks) → work (each subagent in
its own file-ownership lane, reports changes but does **not** commit) → integrate
(Meta-Orch merges, **re-runs all gates**, commits/pushes) → record (update shared
index) → schedule next.

**Three things can't be parallelized** (serial through Meta-Orch): commit/push ·
edits to shared-index files (`docs/working/CONTEXT.md`, `data/coordination/ledger.json`,
`docs/working/trackers/IMPROVEMENT-BACKLOG.md`, `…/PRODUCT-FINDINGS.md`) · live-run
dispatch (`.github/run-request`, one run at a time).

**Round entry = low-verification first:** fill each round's ticket pool Tier-L
(offline-gate-only) → Tier-M (compose + dry-run) → Tier-H (needs a live run) and
climb only as the lower tier empties; Watcher demotes the pool back to Tier-L if
integration-failure ≥ 20%.

## 4. Escalation ladder (L0–L3) + STOP-6 — the single source

Each failing unit (chain/node/run) climbs this ladder; the limits are fixed
**before** entry (no improvised "one more try" → blocks self-justification bias):

- **L0 attempt** — compose → run/validate with current knowledge.
- **L1 re-diagnose** — classify from the artifact (oplog / response body / status
  family), apply a knowledge-based fix (model/compose), retry **once**.
- **L2 userguide fallback** — WebFetch the service userguide (`knowledge/formal/INGESTION.md`
  path) → extract constraints/preconditions/state-machine → update
  `knowledge/formal/resources/*.yaml` → recompose → retry.
- **L3 self-judge** — compare against STOP-6 below. If one matches → **STOP +
  escalate** (raise an IB; if a product defect, log to `…/trackers/PRODUCT-FINDINGS.md`),
  disable/waive that unit, and **advance to the next slice** (never block the
  pipeline). Otherwise one final retry, then stop.

**Limits (whichever first):** ≤ 3 revisions per unit per window · **no-progress
stop** (last 2 revs leave `fail_new` / `cov_op` / error-class unchanged).

**STOP-6 — the only conditions that call the owner:**
1. **credential / license** needed (2nd account, dedicated auth key, console-only token).
2. **console-only step** (a prerequisite has no Open API).
3. **confirmed product defect** (API bug, not our usage) → baseline/waive, never re-try.
4. **billing / irreversible gate** unapproved by owner.
5. **engine capability gap** forces a design decision (multipart, nested capture, …).
6. **docs vs observation contradict** with no safe default.

If none of the 6 applies, **do not call the owner** — stay in the loop.

## 5. Coordination — the git blackboard

Subagents never message each other directly; all cross-agent communication is
**git-committed shared state**, read before acting and written after. The
Meta-Orch relays anything one agent establishes that another needs.

- **File ownership (no-collision):** each subagent edits only its assigned files
  — `knowledge/formal/resources/<svc>.yaml` is per-service exclusive; lifecycle
  JSON + validator is one agent; `controlplane/`·`dashboard/` is one Platform agent;
  the shared-index files (§3) are **Meta-Orch only**; read-only scans are unlimited.
- **Subagent report contract:** every report **starts with a STATUS line** —
  `DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED` — and **includes a
  `changed-files:` list** (empty if none). Missing either → Meta-Orch re-requests
  (max 2×) then treats it as `BLOCKED`. Before merge, Meta-Orch diffs the reported
  `changed-files:` against the agent's assigned slice; out-of-slice edits are
  **FLAGGED** (justify or revert), never silently merged.
- **Severity → merge action:** CRITICAL/HIGH = block (fix before merge) · MEDIUM =
  cheap-fix or track in backlog · LOW/INFO = advisory, merge and record.

## 6. How agents run (the harness)

**Setup:** `pip install -r requirements.txt`; `cp .env.example .env` (never commit
`.env`); `python -m spec.extract_catalog` then `python -m spec.summary`. Required
env: `SCP_REGION`, `SCP_ACCESS_KEY`, `SCP_SECRET_KEY` (+ optional `SCP_PROJECT_ID`,
host/auth overrides — see `core/config.py`). A no-credential session can still do
everything offline: catalog/spec, scenario authoring, static conformance, dashboard
build, knowledge curation.

**Canonical commands:**
```bash
pytest tests/smoke -m smoke                                  # AXIS 1 read-only smoke
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud   # CRUD (real resources)
SCP_RUN_HEAVY=true … pytest tests/crud -m crud              # also billable/heavy
python -m conformance.static                                 # AXIS 2 static
python -m conformance.runtime --probe all                    # AXIS 2 runtime (gated, non-destructive)
python -m conformance.baseline --init-if-missing             # only NEW defects alarm
python -m spec.diff old.json new.json                        # diff catalog snapshots
python -m dashboard.build                                    # render dashboard
SCP_ALLOW_DESTRUCTIVE=true python -m cleanup.reconciler      # reclaim leftovers (tag-scoped)
```

**Result contract** — write through `core.results` so dashboard + baselines stay
consistent: `record(Observation(...))` for AXIS 1 (status · category · `elapsed_ms`
· source), `record_finding(Finding(...))` for AXIS 2 (rule_id · severity · detail ·
source). Stores: `reports/results/observations.jsonl`, `…/findings.jsonl`; baseline
of known/muted backend bugs: `data/baselines/known_issues.json`.

**After every run — optimize (async, never skipped):** `conftest.py` auto-fires
`python -m tools.analyze_run` (API-free, read-only; writes `reports/optimizer/report-<ts>.md`
+ a trend row); the orchestrator then spawns `log-optimizer` in the background to
reason over trends and deliver a ranked improvement list.

**CI:** `api-test.yml` is one job graph — **spec** → **regression** → **sweep**
(`cleanup.reconciler`) + **conformance** → **dashboard** (publish to `dashboard-data`).
Live CRUD/heavy/destructive only via `workflow_dispatch` with gates set. One run
at a time; every job exports `APITEST_RUN_ID` for owner-tagged reclaim.

**Safety rails (never bend):**
1. Never set `SCP_ALLOW_MUTATIONS` / `SCP_ALLOW_DESTRUCTIVE` / `SCP_RUN_HEAVY` to
   make a test pass — they are deliberate opt-ins.
2. Never delete by name-guessing — go through `core.registry` ownership; the
   reconciler deletes only our tag.
3. Reserve quota via `core.budgets` before a capped create; **skip** (not fail)
   when exhausted.
4. Persist hard-won facts to `knowledge/` + scenario `_note`s; commit them.
5. Commit to the assigned branch; push when done; **no PR unless asked**.
6. **Record findings to git every run before ending** — the container is ephemeral;
   an uncommitted finding is lost. A chat report summarizes what was committed.

**Memory discipline:** memory/notes are **hints, not ground truth**. Any remembered
path, env flag, endpoint, run-id, or coverage number MUST be re-verified (Glob/Grep,
re-run `spec.summary`, check the catalog). On conflict, **current observed state
wins**.

## 7. Coverage campaign (AXIS 1 → 100%)

`python -m spec.coverage_gap` computes the static ceiling. Three kinds of endpoint:
- **GET, no path params** → reachable by the read-only smoke floor (auto).
- **GET, with path params** → reachable by read-chains (list→show) + CRUD `probe_reads` (mostly auto).
- **non-GET (write)** → reachable **only** if an enabled lifecycle has a step with
  the same `(method, normalized-path)`. **This is the campaign's target.**

**Service-agent contract (definition of done)** — a service's fragment file
`regression/scenarios/lifecycles/<category>__<service>.json` is "authored" when:
valid JSON with globally-unique service-prefixed ids; targets that service's
uncovered **write** ops (GAP-write count drops; `(method,path)` matches the catalog);
follows create→read→(update)→delete with the right `capture`/`poll`/`cleanup`/
`destructive` flags; reuses shared prerequisites (adopt the shared VPC, don't
self-create) and declares quota kinds; never weakens a gate; passes
`python -m regression.scenarios.validate`; and the validated facts + ledger row are
recorded. Live validation is deferred to a CI run; the coordinator flips the ledger
`status` to `live-validated` after a green run.

## 8. Crosswalk — role ↔ executable agent ↔ track

| Role | `.claude/agents/` worker | Track |
|------|--------------------------|-------|
| Orchestrator | — (the lead session) | Meta-Orchestrator |
| Service agent | `coverage-service` | ② Coverage |
| Spec-Intel | `spec-intel` | ② Coverage |
| Coverage-Validator | `coverage-validator` | ② Coverage (consumer of `live-verifier`) |
| Conformance | `conformance` | ④ Problem-Finder / AXIS 2 |
| AI-Evaluator | `ai-evaluator` | ④ Problem-Finder / AXIS 2 |
| Dashboard | `dashboard` | ① Platform |
| Optimizer | `log-optimizer` | post-run (cross-cutting) |
| Live-Watcher | `live-watcher` | ③ Watcher |
| Domain-Knowledge | (`Task`, ad hoc) | ② Coverage |
| Regression | pytest activity (lead-driven) | ② Coverage |

> Only roles that genuinely dispatch as bounded autonomous workers earn a
> `.claude/agents/` file; the rest are hats the lead session wears. New executable
> agents are added there (frontmatter + a system prompt that **points to this doc**,
> not a re-paste). **8 today:** `coverage-service` · `spec-intel` · `conformance` ·
> `coverage-validator` · `dashboard` · `ai-evaluator` · `log-optimizer` ·
> `live-watcher`.
