# CLAUDE.md

> Thin index Claude Code auto-loads each session. It does **not** duplicate the
> docs — it pins the few rules you must never bend and points at the canonical
> sources. Read `START_HERE.md` next. (이 파일은 매 세션 자동 로드되는 얇은
> 진입 인덱스입니다. 상세는 아래 문서들에 있습니다.)

## What this repo is

The **SCP API Regression Test Platform** — tests the Samsung Cloud Platform Open
APIs (13 categories / ~60 services / 1,372 endpoints) on two axes (regression =
"does it work?", conformance = "is it well-designed & AI-usable?"). Engineered by
a **team of AI agents**. Full picture: `START_HERE.md`, `README.md`,
`docs/ARCHITECTURE.md`.

## Start here (entry path)

1. `START_HERE.md` — session bootstrap (do the stale-reference spot-check there).
2. `docs/working/CONTEXT.md` — shared **current state** (coverage numbers, campaign status).
3. `docs/agent-team.md` — the team: roster, operating loop, harness (commands · safety
   rails · result contract), STOP-6. Executable agents live in `.claude/agents/`.
4. `knowledge/` — accumulated SCP domain facts (`knowledge/validated-facts.md`).

## Quick Ref (canonical commands — see `docs/agent-team.md` for the full set)

```bash
pip install -r requirements.txt           # setup; cp .env.example .env (never commit .env)
python -m spec.summary                     # live coverage summary (trust this over remembered numbers)
python -m spec.coverage_gap                # static coverage ceiling + gaps
pytest tests/smoke -m smoke                # AXIS 1 read-only smoke (no resource changes)
python -m conformance.static               # AXIS 2 static analysis
python -m regression.scenarios.validate    # validate composed lifecycle fragments
```

CRUD/heavy/destructive runs require explicit safety-gate opt-ins — see Hard Rules.

## Hard Rules (never bend)

1. **Safety gates.** Mutations (`POST/PUT/PATCH/DELETE`) default **ON** — the project's
   purpose is real execution; the deliberate opt-in is the run **selection** + the
   console2 **pre-flight confirm**, not an env flag. Force a **read-only** run with
   `SCP_ALLOW_MUTATIONS=false` (CI's smoke/conformance suites set it explicitly) or a
   profile veto (`SCP_PROFILE_FORBID`). **Heavy/billable** lifecycles still need an
   explicit opt-in — `SCP_RUN_HEAVY=true`, or a heavy selection (console2 auto-derives
   + confirms it); never flip heavy on just "to make a test pass" (`docs/agent-team.md`).
2. **No secrets in git.** Never read/log/commit `.env`; `.env.example` /
   `.env.platform.example` are the only committed templates. Credentials → env vars.
3. **Never delete by name-guessing** in live code paths — go through `core.registry`
   ownership; the reconciler deletes only our owner tag (`docs/agent-team.md`).
4. **One workflow run at a time.** Before pushing anything that triggers
   `api-test.yml`, confirm the previous run (sweep included) has fully concluded
   (owner rule, `docs/agent-team.md`).
5. **Memory is a hint, not ground truth.** Any remembered file path, env flag,
   endpoint, run-id, or coverage number MUST be re-verified before you act on it;
   current observed state wins on conflict (`docs/agent-team.md` → Memory discipline).
6. **Reserve quota before a capped create; skip (not fail) when exhausted** so quota
   pressure isn't a false regression (`core.budgets`).
7. **Commit to the assigned branch with clear messages; push when done. No PR unless asked.**
   Persist hard-won facts to `knowledge/` in the same commit that changes behavior.

## Result contract

Write through `core.results` — `record(Observation(...))` for AXIS 1,
`record_finding(Finding(...))` for AXIS 2. Stores: `reports/results/*.jsonl`
(gitignored). Baseline of known/muted backend bugs: `data/baselines/known_issues.json`.

## On compaction (preserve these)

When context is compressed, carry forward: (1) the **Hard Rules** above;
(2) the **active branch + uncommitted/in-flight files** and the literal next
command to resume; (3) **pending tasks** — see `docs/working/CONTEXT.md` "What to advance
next" + `data/coordination/ledger.json`; (4) any **open decision** not yet
recorded. Do not drop a safety gate or a run-sequencing constraint to save tokens.
