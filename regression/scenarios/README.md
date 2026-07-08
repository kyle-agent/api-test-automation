# regression/scenarios/ — declarative CRUD lifecycles (engine + data)

Scenarios are **declarative**: add an entry to `scenarios.json` (or a
per-service fragment in `lifecycles/*.json`) — no new Python — and the engine
(`engine.py`) drives create → read → delete in order. **Composed** lifecycles
(`lifecycles/generated__*.json`, ids `gen-*` / `bundle-*`) are compiled from the
resource model by `composer.py` and run identically — the engine is unmodified
(design: `docs/RESOURCE-MODEL-PLAN.md`; model format:
`knowledge/formal/FORMAT.md`).

Validate after any edit (offline, no credentials):

```bash
python -m regression.scenarios.validate              # scenario gate
python -m regression.scenarios.validate_dag --check  # dependency-DAG drift gate (CI-enforced)
```

## Per-step features (engine contract — `engine.py`)

- `capture` — pull a value from a response (`$.vpc.id`) into a `{placeholder}`;
  `{unique}` / `{region}` are seeded automatically.
- `service` — override the host for that step (a chain can span services).
- `poll` / `wait` — wait for async provisioning
  (`{field, until, timeout, interval}` / seconds).
- `cleanup` — the delete to register for a created resource (reverse-order
  teardown via the per-run registry manifest).
- `group` + `optional` — a multi-engine/family scenario isolates a failing
  group (tears down just that group, keeps the rest) so one bad body costs one
  family, not the whole run.
- `destructive: true` — marks deletes (need `SCP_ALLOW_DESTRUCTIVE`).
- `{"adopt": "<kind>"}` — reuse a session-shared resource (e.g. the shared VPC)
  instead of creating one; falls back to self-create when no shared id exists.

## Light vs heavy · CI opt-ins

**Light** scenarios run in routine opted-in CRUD; **heavy** ones (`heavy: true`
— real billable VM / K8s / DB / shared-networking, ~20–60 min) run ONLY with an
explicit opt-in (`SCP_RUN_HEAVY=true` or a confirmed heavy run selection).
Validate a single heavy scenario with the dispatch `crud_filter` input. In CI,
repo variable `SCP_RUN_CRUD=true` opts a run into CRUD.

`dependencies.json` maps the VPC-creating scenarios to their quota kinds and
carries the **dependency DAG** (`shared_roots` + `adopt_edges`) that the 1.0
scheduler plans topological waves from — full design:
`docs/scheduler-system.md`.

> **Heavy self-trigger:** a committed `.github/heavy.txt` (first non-comment
> line = a `crud_filter` expression) lets a push drive which heavy lifecycle
> runs next — used to chain heavy validations one per run. Empty file = no
> self-trigger.
