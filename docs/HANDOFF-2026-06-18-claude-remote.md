# Handoff — 2026-06-18 (→ next session: Claude remote, env vars set)

> Pick-up note for a fresh session that has the SCP env vars configured. This
> session moved live execution **off CI onto the Claude remote environment**.
> Read `CLAUDE.md` → `START_HERE.md` → `agents/CONTEXT.md` first; this file is the
> session-specific delta + the literal commands to run the suite by hand.

## 0. Headline decision (DONE, on `main`)

**Automatic CI execution is DISABLED.** `.github/workflows/api-test.yml` now has
only `workflow_dispatch` (manual) — the `push:[branches:main, paths:.github/run-request]`
auto-trigger is commented out (commit `1e3d90b`). Editing/pushing
`.github/run-request` **no longer starts a run**. Live runs are executed from the
Claude remote env instead (env vars + live API calls already verified in a
separate session). To re-enable file-triggered CI, un-comment the `push:` block
(it's preserved verbatim in the `on:` comment).

## 1. How to run the suite by hand (Claude remote) — canonical sequence

Mirror what the workflow did. **Safety gates are still non-negotiable** — set them
explicitly per run, never "to make a test pass":

```bash
# --- gates (light CRUD) ---
export SCP_ALLOW_MUTATIONS=true        # POST/PUT/PATCH
export SCP_ALLOW_DESTRUCTIVE=true      # DELETE (needed for teardown)
# export SCP_RUN_HEAVY=true            # ONLY for heavy (VM/DB/K8s/billable)

# --- 0. validate scenario data ---
python -m regression.scenarios.validate

# --- 1. pre-run reclaim (clear our own leftover tags before provisioning) ---
python -m cleanup.reconciler            # tag-scoped; deletes only our owner tag

# --- 2. provision the shared VPC + subnet ONCE (emits SCP_SHARED_*= on stdout) ---
python -m regression.scenarios.shared_infra --provision > shared_ids.txt
grep -E '^SCP_SHARED_[A-Za-z0-9_]+=.+' shared_ids.txt    # sanity check
set -a; . ./shared_ids.txt; set +a                        # export SCP_SHARED_VPC_ID / _SUBNET_ID

# --- 3. lane filters (PARALLEL_K = adopt-class, VPC_CRUD_K = self-create class) ---
eval "$(python -m regression.scenarios.shared_infra --print-filters)"

# --- 4. read-only smoke + read-chains ---
python -m pytest tests/smoke -m smoke

# --- 5a. ADOPT-class CRUD in PARALLEL (now -n 6, raised from -n 2 this session) ---
python -m pytest tests/crud -m crud -n 6 -k "$PARALLEL_K"
# --- 5b. VPC-CRUD class SERIAL (must self-create/mutate/peer VPCs) ---
python -m pytest tests/crud -m crud -k "$VPC_CRUD_K"

# --- 6. teardown shared infra + final sweep ---
python -m regression.scenarios.shared_infra --teardown
python -m cleanup.reconciler

# --- 7. post-run audit analysis (time/cost) ---
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ); START=$(date -u -d '6 hours ago' +%Y-%m-%dT%H:%M:%SZ)
python -m audit.harvest --start "$START" --end "$NOW" --out reports/audit/manual.jsonl --service loggingaudit --max-pages 50
python -m audit.optimizer reports/audit/manual.jsonl --out reports/audit/manual.md --json
```

Notes:
- `-n 6`: independent lifecycles (each DBaaS engine `requires=None`, all adopt the
  same shared VPC) fan out so total = max(engine), not sum. The provision race is
  guarded by IB-049 (xdist-gated adopter skip); adopters adopt the SAME VPC so more
  workers don't consume more VPC cap; billable quota stays protected by
  `core.budgets` (reserve-or-skip). **Still needs first real validation on a heavy
  run** — confirm DBaaS phase wall-time drops sum→max via the audit-optimizer.
- For a **heavy** run also `export SCP_RUN_HEAVY=true` and expect billable spend;
  reserve quota first and skip (not fail) when exhausted.
- One run at a time (owner rule) — don't start a second mutating run while one is
  live, and finish with the teardown + sweep so leftovers don't poison the VPC cap.

## 2. What changed this session (all merged to `main`)

| Area | Change | Commit |
|---|---|---|
| **Parallelism** | adopt-class `pytest -n 2 → 6`; principle made explicit | merge `dab8a41` |
| **CI** | auto file-trigger disabled (manual `workflow_dispatch` only) | `1e3d90b` |
| **Audit feature** | `audit/harvest.py` (read-only `/v1/logs` paginator) + `audit/optimizer.py` (durations, redundancy, cost proxy, still-live leak flags) | merged |
| **Create→show coverage** | `spec/enrich_catalog.py` → `data/api_catalog_params.json` (per-endpoint path/query param + producer); engine auto-probe seeded from full ctx + `_PARAM_ALIASES`, bounded by `timeout=8/retry=False/cap` | merged |
| **Heavy de-dup** | disabled 5 redundant composed heavy-DBaaS drafts (~5 clusters/run) | `3390d72` |
| **http client** | `request(... timeout=, retry=)` so best-effort probes cost one short deadline | merged |
| **Docs** | `docs/PARALLEL-EXECUTION-PLAN.md`, `docs/COVERAGE-GETID-PLAN.md`, `agents/CONTEXT.md`, `docs/IMPROVEMENT-BACKLOG.md` (IB-050 note) | merged |

## 3. Key facts learned this session

1. **`vars.SCP_RUN_HEAVY=true` was the heavy-gate leak** — deleting the repo var
   fixed it; the Tier-0 run stayed LIGHT (heavy adopters didn't fire). Confirmed.
2. **IB-050's real cap-poisoning fix = pre-run reclaim + concurrency-group constant**
   (both still in force), NOT the `-n 6→2` lowering (that was a conservative
   co-change). Hence re-raising `-n` is safe.
3. **DBaaS engine lifecycles are already independent** (`requires=None`, only adopt
   shared vpc/subnet) — parallelism is purely a runtime `-n` concern; there is no
   per-scenario "parallel group" to declare.
4. **Silent-merge technique** (kept for reference, now moot with CI off): a push to
   `main` only fires the workflow when the aggregate `before..after` diff touches
   `.github/run-request`; keep that file byte-identical and any merge stays silent.
5. **`/v1/logs` (loggingaudit) is intermittently 503** ("upstream connect …
   connection timeout") — transient gateway flakiness, NOT a creds problem (creds
   verified working earlier). Retry with backoff; the harvester stops cleanly on 503.
6. api_docs `endpoints` is a FLAT dict keyed `"category/service/name"` (== catalog
   key, 1:1 join). dotenv is NOT installed — `core.config.settings` loads `.env` itself.

## 4. Open items / next steps

- [ ] **Run 27735741382 (Tier-0 LIGHT) was still in progress** at handoff — adopt-class
      CRUD since 04:50Z (unusually long). **Check its conclusion + triage fails**
      (expect archivestorage-401 fix → that fail cleared; iam-policy ReadTimeout was a
      transient flake). It runs the OLD `-n 2` (commit df8fb87, pre-merge).
- [ ] **Validate `-n 6` speedup** on the next heavy run via the audit-optimizer
      (DBaaS phase sum→max(engine)).
- [ ] **Promote Wave-A docs→VALIDATED** on 2xx evidence once a clean light run lands
      (quick-query-validate, alert, cm-account-resource, gpu-node-image, cloudml-image,
      volume-type) — see `docs/COVERAGE-MAX-PLAN.md` tiers.
- [ ] **Walk Tiers 1→4** of `docs/COVERAGE-MAX-PLAN.md` from the remote env now that
      runs are hand-driven.
- [ ] **Still-live billable resources** from the earlier heavy run (e.g.
      postgresql/regrpgjfofmpmd) — confirm reaped by the sweep; reconcile if not.
- [ ] TODO (tracked in `_note`): fold replica sync/reset/promote into subops-guarded.

## 5. Security / hygiene (carry forward)

- **`.env` holds the user's live SCP creds** (gitignored). User said remove them
  **when done** ("끝나면 지워드립니다"). Next session has its OWN env vars configured,
  so this session's `.env` creds can be cleared. Never read/log/commit `.env`.
- Safety gates (`SCP_ALLOW_MUTATIONS` / `_DESTRUCTIVE` / `SCP_RUN_HEAVY`) are
  deliberate opt-ins — never set to force a green test.
- Branch: develop on `claude/adoring-cori-zp47nl`; it is currently in sync with
  `main` (`1e3d90b`). Commit trailers required (Co-Authored-By + Claude-Session).
