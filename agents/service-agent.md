# Service agent (per-service expert) — template

**Role.** A domain expert for **one SCP service** (e.g. `virtualserver`,
`filestorage`, `vpc`, `ske`, `mysql`). It owns that service's endpoints,
scenarios, request bodies, state machines, and quirks. Instantiate one per
service as needed; they all follow this template.

> This is a template + index. Per-service durable facts live in
> `knowledge/services.md` (and the scenario `_note`s). Add a section there for
> each service you become expert in, rather than creating many tiny files.

## Standing pattern — one coverage agent per service, every run

Coverage is advanced **service-by-service, max-parallel**: every service has its
own agent whose single mandate is to raise THAT service's coverage by any
legitimate means — read the API **docs**, run live **tests**, dig the **logs**,
**ask a peer** agent for a prerequisite. This is not a one-off; each coverage push
fans these agents out again, and each resumes from its own state.

- **Spawnable role:** `.claude/agents/coverage-service.md` (subagent
  `coverage-service`). The orchestrator spawns one per service, passing the
  service name + its ledger row.
- **Targeting + resumable state:** `python -m tools.coverage_headroom` ranks
  services worst-gap-first and maintains `data/coverage_ledger.json` — one row per
  service with live `covered`/`total`/`gap` PLUS each agent's `blockers`
  (entitlement / product-bug / heavy-prereq) and `next_levers`. An agent reads its
  row to continue where it left off and writes it back each run.
- **Concurrency cap (hard, from the 503 lesson):** run at most **~6–8** service
  agents concurrently — 14 concurrent saturated the gateway with transient 503s
  that look like failures. Dispatch in capped batches, cheap (read/config)
  services together, and run the **heavy/billable** services (`HEAVY_SVCS` in
  `tools.coverage_headroom`: virtualserver, vpc, the DBaaS engines, loadbalancer,
  baremetal…) in a **separate gated batch** with `SCP_RUN_HEAVY=true`, never mixed
  into the cheap fan-out.
- **Each agent leaves behind:** new Observations, an updated ledger row, durable
  facts in `knowledge/services.md`, a clean account (teardown verified), and a
  commit. It classifies every remaining gap as a fixable lever or a named blocker
  so the surface converges instead of re-litigating the same misses.

## Objective

Maximize correct, safe coverage of the assigned service's endpoints across both
axes, and keep that service's knowledge current.

## Inputs

- Catalog slice for the service: filter `data/api_catalog.json` (or
  `python -m spec.summary --category <cat> --service <svc>` style scoping via
  pytest `--category/--service`).
- The service's request bodies in `data/api_bodies.json`.
- Existing scenarios for the service in `scenarios.json` (+ `_note`s) and its
  `knowledge/services.md` section.

## Process

1. **Inventory** the service's endpoints by method; mark which are directly
   testable GETs, which need an id (read-chain/CRUD), which are mutating.
2. **Reuse dependencies** from `knowledge/service-dependencies.md` (e.g. need a
   VPC+subnet first). Don't rebuild prerequisite flows — reference the shared ones.
3. **Author/extend scenarios** declaratively in `scenarios.json`: create → read →
   (update) → delete, with `capture`, `poll`/`wait`, `cleanup`, `group/optional`,
   and `destructive` flags as needed.
4. **Validate** read-only first (smoke), then mutations behind the gates, then
   teardown. Confirm undocumented fields against a real 2xx before trusting them.
5. **Record** validated facts into `knowledge/services.md` + the `_note`, and
   observations into the results store.

## Outputs

- New/updated scenarios + validated facts for the service, committed.
- Observations (and any conformance findings) in the results store.

## Tools

Read/Grep/Glob (catalog/bodies/scenarios), Edit/Write (scenarios + knowledge),
Bash (scoped smoke / CRUD with gates), Task (escalate to Domain-Knowledge or
Conformance when work crosses the boundary).

## Guardrails

- Target the service's **own host** (path roots collide across services).
- Respect quota kinds the service consumes (reserve via budgets; skip when full).
- Reverse-order teardown for everything created; never leave orphans.

## Done-when

The service's covered endpoint count increased (or scenarios deepened),
teardown verified, facts persisted, observations recorded.

---

## Worked examples (most-developed services)

These two are the reference implementations — study them before building a new
service. Full step lists live in `regression/scenarios/scenarios.json`; the
distilled facts are in `knowledge/services.md` and `knowledge/validated-facts.md`.

- **`virtualserver` (compute).** Owns keypair (zero-cost, synchronous), block
  volume + snapshot (no VPC), and the **full VM** lifecycle (heavy/billable:
  vpc→subnet→sg→keypair→image→server-type→server, attach volume, rename,
  stop/start, image-create). Key facts: server create captures
  `$.servers[0].id`; `volume_type` not `type`; server-type id must start with `s`
  (not `g`); rename requires `name` matching `^[a-zA-Z0-9-_ ]*$`.
- **`filestorage` (storage).** Owns the NFS volume lifecycle: create
  (`protocol:NFS`, `type_name:HDD`) → poll `$.state` to available → delete →
  poll until 404. Volume id is `$.volume_id` (note: virtualserver block volumes
  use `$.id` — different services, different shapes).
