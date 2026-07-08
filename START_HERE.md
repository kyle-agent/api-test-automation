# START_HERE.md — Session bootstrap (read me first)

> **이 파일은 어떤 Claude Code 세션에서 시작하더라도 동일한 지점에서 이어서
> 작업할 수 있도록 하는 진입점입니다.** 새 세션이 열리면 이 파일 →
> `docs/working/CONTEXT.md` → `docs/agent-team.md` 순서로 읽고
> 시작하세요. 도메인 지식은 `knowledge/` 에 누적됩니다.

This repository is the **SCP API Regression Test Platform**: it tests the
**Samsung Cloud Platform (SCP) Open APIs**
(13 categories / ~60 services / **1,372 endpoints**) along two axes —
**regression** ("does it work?") and **conformance** ("is it well designed &
AI-usable?") — and wraps them in a **control plane**
([`controlplane/`](controlplane/README.md): dispatch, schedule, live tracking,
intervention, history/compare, AI seams) plus the **M5 resource-task model**
([`knowledge/formal/resources/`](knowledge/formal/), 275 nodes / 60 service files) from which
scenarios are *composed* (`regression/scenarios/composer.py`). The engineering
is done by **a team of AI agents** (this is a *multi-agent* project) whose
roles, operating model, context and execution harness are documented in
[`docs/agent-team.md`](docs/agent-team.md) (executable agents in
[`.claude/agents/`](.claude/agents/)), and whose shared **SCP domain knowledge**
is accumulated under [`knowledge/`](knowledge/).

## Mission (the two axes)

1. **Regression** — prove each endpoint works; record pass/fail + response time.
   **Goal: 100% of the SCP OpenAPI surface covered.** Once coverage is at 100%,
   widen by exercising more parameter combinations. Evidence comes from **real
   test runs**.
2. **Conformance** — judge whether the API follows good API design (REST/HTTP
   best practices) **and** is easy for an AI agent to consume. Evidence comes
   from **static analysis + real runtime probes**.

See [`README.md`](README.md) and [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the
implementation blueprint, and [`ROADMAP.md`](docs/ROADMAP.md) for the phase plan
(coverage 100% → scheduled regression → dedicated-server runs). This file does
not duplicate them.

## Fresh-container bootstrap (Claude-on-the-web) — read before running anything

A fresh remote container is **not test-ready on clone**. The `SessionStart` hook
(`.claude/hooks/session-start.sh`, registered in `.claude/settings.json`) now does
the first three automatically on every web session; the rest are gotchas to know.
*(이 셋업은 매번 다시 알아내지 말 것 — 훅이 자동 처리하고, 함정은 아래에 고정.)*

1. **Python deps aren't installed** → `python -m pip install -r requirements.txt`.
   **Install/run with `python -m pip` / `python -m pytest`, NOT bare `pip`/`pytest`** —
   bare `pytest` resolves to a *different* interpreter that lacks `requests`
   (`ModuleNotFoundError: requests`). Verified: `python -m pytest` = 9.1.0 (deps
   present) vs bare `pytest` = 9.0.2 (no deps).
2. **The clone is shallow** (`git rev-parse --is-shallow-repository` → `true`) →
   git ancestry/`merge-base`/commit-counts **lie** (truncated history reports
   "unrelated histories / N-way divergence" that don't exist). `git fetch
   --unshallow` before reasoning about history.
3. **Local `main` ref is stale** — it points at the clone-time commit, *not*
   `origin/main` (which may be many commits ahead). Trust `git ls-remote origin`
   / `origin/main` (live) over the local ref; the hook re-points local `main` at
   `origin/main`. **Ground truth = the live remote, never the local ref.**
4. **Live SCP access works from the web env** (creds arrive as env vars:
   `SCP_ACCESS_KEY/SECRET_KEY/REGION/ENV`; **no `.env` file** — never create one).
   Confirm with a single call, not the full smoke:
   `python -c "import requests; print(requests.get('https://resourcemanager.'+__import__('os').environ['SCP_ENV']+'.samsungsdscloud.com', timeout=10).status_code)"`
   → expect `200`. Real host = `<svc>.<region>.<env>.samsungsdscloud.com`
   (regional) / `<svc>.<env>.samsungsdscloud.com` (global) — the `<env>` segment
   is required (omitting it → DNS `gaierror`).
5. **The full read-only smoke is ~6 min** (225 sequential GETs × ~1.7 s, no
   per-request timeout) — it is *slow, not broken*. For a quick liveness check run
   ONE node, or use a hard `timeout` and a scoped `-k`. Don't read a smoke timeout
   as a network failure.

## How a new session should start

1. Read [`docs/working/CONTEXT.md`](docs/working/CONTEXT.md) — shared facts every agent needs
   (goals, current coverage, safety gates, where results live).
2. Read [`docs/agent-team.md`](docs/agent-team.md) — the roster, operating loop, and how the
   orchestrator delegates.
3. **Spot-check the handoff before trusting it.** Before acting on the current
   handoff (`docs/working/handoffs/`) / `docs/working/CONTEXT.md`, verify 1–2 concrete
   references it names — a file/fragment path (Glob/Grep), a run-id, or a coverage
   number (`python -m spec.summary`) — to confirm they still exist / still hold.
   핸드오프는 run-id·SHA·fragment 경로를 인용하는데 이것들은 금방 stale 됩니다. If a
   cited path, run-id, or number is stale, **flag it and trust current observed
   state over the handoff.**
4. Pick your role from the roster in [`docs/agent-team.md`](docs/agent-team.md)
   (executable agents live in `.claude/agents/`).
5. Consult [`knowledge/`](knowledge/) before inventing API call orders or request
   bodies — most of it is already captured (and hard-won). Add what you learn back.

> **Kicking off a fresh session?** The minimum prompt is literally:
> *"Read `START_HERE.md` and continue per its instructions."* For a specific goal,
> tell the session which role from [`docs/agent-team.md`](docs/agent-team.md) to play.

> **Handoff convention (when you write/end a session handoff):** the **TOP /
> current in-progress item** MUST carry a literal, copy-pasteable **resume
> command** — the exact `pytest …` / dispatch / `grep` / `python -m …` to run
> next — not just a prose description of the next candidate. 다음 세션이 핸드오프를
> 열자마자 그 명령어를 그대로 복사·실행할 수 있어야 합니다.

## Golden rules (do not break these)

- **Safety gates are sacred.** Mutations (`POST/PUT/PATCH/DELETE`) default **ON**
  — the project's purpose is real execution; the deliberate opt-in is the run
  **selection** + the console2 pre-flight confirm, not an env flag. Force a
  **read-only** run with `SCP_ALLOW_MUTATIONS=false` (CI's smoke/conformance
  suites set it explicitly) or a profile veto (`SCP_PROFILE_FORBID`);
  heavy/billable lifecycles (VM, K8s, DB) still need an explicit opt-in
  (`SCP_RUN_HEAVY=true` or a confirmed heavy selection). Never flip a gate just
  to make a test pass. (Canonical: `CLAUDE.md` Hard Rules + `docs/agent-team.md`
  safety rails.)
- **Domain knowledge is data, not code.** Call order, dependencies, quotas and
  scenarios live in `knowledge/` + `regression/scenarios/*.json` so a human can
  read and adjust them. Agents generate them; humans review them. The
  formalized, human-editable form is `knowledge/formal/` (YAML + validator).
- **Every created resource must be owned and torn down.** Use `core.registry`
  tagging + reverse-order cleanup. The `cleanup.reconciler` only deletes *our*
  owner tag — never weaken this into cross-run deletion.
- **Persist what you learn.** A fact discovered at runtime (an undocumented field,
  a state machine, a 500-on-delete race) belongs in `knowledge/validated-facts.md`
  and/or the scenario `_note`, committed to git — so the next session starts ahead.
- **Develop on the assigned branch, commit with clear messages, push when done.**
  Do **not** open a PR unless explicitly asked.

## Map

| Path | What |
|------|------|
| `docs/agent-team.md` | The multi-agent team: roster · operating loop · harness · STOP-6 (executable agents in `.claude/agents/`) |
| `docs/working/CONTEXT.md` | Shared **current state** every agent loads (coverage, campaign status, what to advance next) |
| `knowledge/` | Accumulated SCP domain knowledge (human-readable, AI-maintained); `formal/resources/` = the M5 resource-task model (composer input) |
| `core/` | Shared kernel: config·auth·http_client·catalog·registry·results·budgets·suites·profiles·oplog·snapshot·commands·baselines |
| `spec/` | Extract the API spec from the docs + diff versions |
| `regression/` | AXIS 1 — smoke · read_chains · scenarios (declarative CRUD engine + composer + data) |
| `conformance/` | AXIS 2 — static · runtime · baseline · pluggable `rules/` |
| `dashboard/` | Build the unified HTML dashboard from the results store + `ops.html` live ops viewer |
| `controlplane/` | The platform server (FastAPI+htmx): dispatch · scheduler · live runs · intervention · authoring · AI pipelines · static export (Pages `/platform/`) |
| `runner/` | `worker.py` — same-host executor for the M4 deployment cutover |
| `suites/` · `environments/` | Named suites + environment profiles (run = suite × profile) |
| `drafts/` | Composer/AI draft outputs awaiting human review (never auto-enabled) |
| `cleanup/` | Tag-scoped reconciler (guaranteed teardown) |
| `data/` | Catalog, request bodies, docs, baselines (incl. `coverage_waivers.json`); `coordination/ledger.json` = campaign blackboard |
| `reports/` | Per-run output (gitignored): `results/*.jsonl`, dashboard, junit |
| `docs/` | Design specs (ARCHITECTURE · agent-team · scheduler-system · …) + `working/` (handoffs · trackers · plans) + `decisions/` — see `docs/INDEX.md` |
