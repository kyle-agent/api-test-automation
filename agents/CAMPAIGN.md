# Coverage Campaign — multi-agent operating model

> Goal: drive AXIS-1 endpoint coverage toward **100%**, heavy resources included.
> This file is the durable definition of *how the agent team runs the campaign*.
> The live to-do state is the machine-readable blackboard
> [`coordination/ledger.json`](coordination/ledger.json).

## The gap we are closing

`python -m spec.coverage_gap` computes the **static coverage ceiling** — the % of
the 1,372 endpoints the current scenario surface could reach in a perfect run
(this is the dashboard's exact model, minus live read-chain discovery):

- **GET, no path params** → reachable by the read-only smoke floor (always).
- **GET, with path params** → reachable by read-chains (list→show) at runtime, and
  by CRUD `probe_reads`. Mostly auto-covered; not the campaign's focus.
- **non-GET (write)** → reachable **only** if an enabled lifecycle has a step with
  the same `(method, normalized-path)`. **This is the campaign's target.**

As of campaign start: ceiling **43.0%** static / **~59.5%** last live run. The
addressable surface is **547 uncovered write operations across 53 services**
(`coordination/ledger.json`). Closing them (and their freed id-bound GETs via
`probe_reads`) is the path to ~100%.

## Roles

| Role | Who | Responsibility |
|------|-----|----------------|
| **Coordinator** | the lead session | Owns `ledger.json`. Computes the gap, assigns service slices, spawns service-agents in parallel waves, integrates fragments, runs static validation, commits/pushes, keeps `CONTEXT.md` current. Mediates shared-resource contracts (e.g. the shared VPC). |
| **Service-agent** | a subagent (`Agent` tool), one per service | Owns exactly **one** service and exactly **one** fragment file `regression/scenarios/lifecycles/<category>__<service>.json`. Authors CRUD lifecycles for that service's uncovered writes, records facts, returns a structured report. |

## How agents communicate (the blackboard, in git)

Subagents cannot message each other directly, so all cross-agent communication
goes through **git-committed shared state** — read before acting, written after:

1. **`coordination/ledger.json`** — the blackboard. Per service: `write_gap`,
   `status` (`todo|claimed|authored|integrated|live-validated`), `owner`,
   `lifecycles` authored, `notes`. Also `shared_contracts` (e.g. the shared VPC
   cidr/owner). The coordinator updates `status` as work lands.
2. **`knowledge/`** — durable shared memory. `service-dependencies.md`,
   `validated-facts.md`, `quotas-and-budgets.md`, `services.md`,
   `scenario-catalog.md`. An agent reads these before inventing call orders and
   appends what it learns.
3. **Coordinator relay** — when agent A establishes something agent B needs
   (e.g. vpc agent finalizes the shared-VPC contract), the coordinator carries it
   into B's spawn prompt and into `shared_contracts`.

## No-collision rule (why fragments exist)

Each service-agent writes **only** its own fragment file. It never edits
`scenarios.json` or another agent's fragment. `regression/scenarios/loader.py`
merges base + all fragments; a duplicate lifecycle `id` is a hard error. This is
what makes a wide parallel fan-out safe.

## Service-agent contract (definition of done)

A service-agent's fragment is "authored" (ready for the coordinator to integrate)
when ALL hold:

1. **File**: `regression/scenarios/lifecycles/<category>__<service>.json`, shape
   `{"lifecycles":[ ... ]}`, valid JSON, lifecycle ids globally unique and
   prefixed with the service.
2. **Coverage**: targets this service's uncovered **write** ops (verify with
   `python -m spec.coverage_gap --service <svc>` before/after — the GAP-write
   count must drop). Each write endpoint appears as a step with the correct
   `(method, path)` matching the catalog (`norm_path` equality).
3. **Shape**: lifecycles follow create→read→(update)→delete with `capture` /
   `poll` / `wait` / `cleanup` / `group`+`optional` / `destructive` flags as the
   engine expects (mirror an existing lifecycle in `scenarios.json`). Request
   bodies come from `data/api_bodies.json` + docs; mark fields not runtime-proven
   in a `_note`.
4. **Dependencies & quotas**: reuse shared prerequisites (shared VPC via
   `{"adopt":"vpc"}`; don't self-create VPCs in heavy lifecycles). Declare any new
   quota kind in `dependencies.json`. Tag billable/slow lifecycles `"heavy": true`.
5. **Gates**: never weaken safety gates. New mutating lifecycles are `enabled:true`
   only if light + non-destructive-by-default-safe; otherwise gate appropriately.
6. **Static validation passes**: `python -m regression.scenarios.validate`
   (loader merges cleanly, no dup ids, every step path resolves to a catalog key,
   referenced captures exist). Engine import succeeds.
7. **Recorded**: append validated facts to `knowledge/`, update the ledger row
   (`status:"authored"`, list `lifecycles`, `notes`), and the fragment is committed.

Live validation is **deferred** (no creds in-session; a full live regression
exceeds the 300-min CI cap). Authored fragments are candidates the next live CI
run validates; the coordinator flips `status` to `live-validated` after a green run.

## Coordinator loop (per wave)

1. Pick the highest-`write_gap` `todo` services (`ledger.json`). Mark `claimed`.
2. Spawn one service-agent per service **in parallel** (one message, many `Agent`
   calls) using the prompt scaffold in `PROMPTS.md` + the contract above.
3. Integrate each returned fragment: run `regression.scenarios.validate` +
   `spec.coverage_gap` (confirm ceiling rose), resolve any dup-id/path issues.
4. Commit per wave with the coverage delta in the message; push.
5. Update `ledger.json` + `CONTEXT.md`; start the next wave. Stop when
   `write_gap_total == 0` (then move to live validation + parameter widening).
