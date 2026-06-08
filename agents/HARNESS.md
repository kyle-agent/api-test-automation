# Execution Harness (HARNESS.md)

> How agents actually *run* in this repo: the runtime, the tools, the commands,
> the safety rails, and the result contract. Read this before executing anything.

## Execution model

These "agents" are **roles played by a Claude Code session** (the lead session)
and by **subagents it spawns via the `Task` tool**. There is no separate daemon:

- The **lead session** acts as the **orchestrator** (see `orchestrator.md`): it
  reads `CONTEXT.md`, decides what to advance, and either does the work or
  delegates a slice to a subagent.
- A **subagent** is launched with the `Task` tool, given (a) a pointer to the
  relevant `agents/<agent>.md` + `knowledge/` files, and (b) a concrete,
  bounded goal. It returns a concise result; only that result re-enters the lead
  context, so delegate read-heavy or exploratory work.
- Long-running test executions run as **GitHub Actions** (`.github/workflows/
  api-test.yml`) — see "CI" below. Locally, drive them with the commands here.

When spawning subagents for independent work, launch them in parallel
(one message, multiple `Task` calls).

## Environment / setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill SCP_REGION + credentials (never commit .env)
python -m spec.extract_catalog   # build/refresh data/api_catalog.json (resumable)
python -m spec.summary           # coverage summary
```

Required env: `SCP_REGION` (+ `SCP_ENV`, default `e`), `SCP_ACCESS_KEY`,
`SCP_SECRET_KEY`, optional `SCP_PROJECT_ID`. Host/auth overrides:
`SCP_SERVICE_HOSTS`, `SCP_GLOBAL_SERVICES`, `SCP_HMAC_*`, `SCP_AUTH_SCHEME`.
Full list + meanings: `.env.example` and `core/config.py`.

> In a no-credentials / sandbox session you can still do everything that doesn't
> hit the live API: catalog/spec work, scenario authoring, static conformance,
> dashboard build from existing results, and all knowledge/doc curation. Live
> regression + runtime probes need credentials and network reachability to the
> gateway (use a self-hosted runner if it's behind a VPN).

## Commands (the canonical entrypoints)

```bash
# AXIS 1 — read-only smoke (no resource changes)
pytest tests/smoke -m smoke
pytest tests/smoke -m smoke --category compute --service virtualserver   # scoped

# AXIS 1 — CRUD lifecycles (creates/deletes REAL resources — opt in explicitly)
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud
SCP_RUN_HEAVY=true ... pytest tests/crud -m crud      # also run billable heavy ones

# AXIS 2 — conformance
python -m conformance.static                  # static spec analysis + rule lens
python -m conformance.runtime --probe all     # runtime probes (gated; non-destructive)
python -m conformance.baseline --init-if-missing   # only NEW defects alarm

# Supports
python -m spec.diff old.json new.json         # diff two catalog snapshots
python -m dashboard.build                      # render dashboard from results store
SCP_ALLOW_DESTRUCTIVE=true python -m cleanup.reconciler   # reclaim leftovers (tag-scoped)
```

## Tools available to an agent

- **Filesystem + search** (Read/Glob/Grep/Edit/Write) — primary tools for
  catalog, scenarios, knowledge, code.
- **Bash** — run the commands above; run `python -m spec.summary` for coverage.
- **Task** — spawn subagents (see roster in `README.md`).
- **GitHub MCP** (`mcp__github__*`) — PRs, CI status/logs, issues. Do NOT open a
  PR unless the user asks. Scope is restricted to the configured repo.
- **Web** (WebFetch/WebSearch) — for the spec-intel agent to pull doc/service
  facts (load via ToolSearch first).

## Result contract (what a run must leave behind)

Write through `core.results` so the dashboard and baselines stay consistent:

- `core.results.record(Observation(...))` for AXIS 1 calls (status + category +
  `elapsed_ms` + source).
- `core.results.record_finding(Finding(...))` for AXIS 2 defects (rule_id +
  severity + detail + source).

Files: `reports/results/observations.jsonl`, `reports/results/findings.jsonl`.
Baseline of known/muted backend bugs: `data/baselines/known_issues.json`.

## CI (GitHub Actions)

`.github/workflows/api-test.yml` is one orchestrator job graph:
**spec** (refresh catalog) → **regression** (smoke + read-chains, opt-in CRUD) →
**sweep** (`cleanup.reconciler`) + **conformance** (static + runtime + baseline)
→ **dashboard** (build + publish to the `dashboard-data` branch). Read-only
smoke runs daily on a schedule; conformance has its own daily slot; CRUD/heavy/
destructive steps run only via `workflow_dispatch` with the safety gates set, or
when repo var `SCP_RUN_CRUD=true`. Every job exports `APITEST_RUN_ID` so
`core.registry` can attribute and reclaim per run.

## Safety rails an agent must honor

1. Never set `SCP_ALLOW_MUTATIONS` / `SCP_ALLOW_DESTRUCTIVE` / `SCP_RUN_HEAVY`
   "to make a test pass". They are deliberate, explicit opt-ins.
2. Never delete by name-guessing in live code paths — go through `core.registry`
   ownership. The reconciler deletes only our tag.
3. Reserve quota via `core.budgets` before a capped create; **skip** (not fail)
   when exhausted, so quota pressure isn't a false regression.
4. Persist hard-won facts to `knowledge/` + scenario `_note`s and commit them.
5. Commit to the assigned branch with clear messages; push when done; no PR
   unless asked.
