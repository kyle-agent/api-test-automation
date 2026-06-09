# SCP Domain Knowledge (knowledge/)

> Accumulated, **human-readable** knowledge about the SCP platform that the test
> agents need. AI agents generate and maintain it; **humans read and adjust it.**
> It is the durable memory that lets any new session start where the last left off.
> (이 디렉토리는 AI가 생성/유지하지만 사람이 읽고 수정하는 SCP 도메인 지식입니다.)

The same facts exist in two forms and must stay in sync:

- **Here (`knowledge/*.md`)** — narrative, reviewable by a human.
- **As data (`regression/scenarios/scenarios.json`, `dependencies.json`,
  `core/budgets.py`, `core/config.py`)** — consumed by the engines.

The Domain-Knowledge agent (`agents/domain-knowledge-agent.md`) owns keeping
these two in sync. When you learn something, write it here **and** encode it in
the scenario/budget data, in the same commit.

## Map

| File | Contents |
|------|----------|
| [`formal/`](formal/FORMAT.md) | **The formalized, human-editable form** (YAML), structured in 3 layers: `services/` (per-service constraints), `cross-service.yaml` (dependency graph + cross-service constraints), `flows.yaml` (scenario-flow rules + call orders), plus `combo-scenarios.yaml` (combos with per-scenario `review:` blocks a human approves). Offline validator: `python knowledge/formal/validate.py`. Edit here first. |
| [`domain-model.md`](domain-model.md) | SCP concepts: categories, services, per-service hosts (regional vs global), region/env, auth (HMAC). |
| [`service-dependencies.md`](service-dependencies.md) | Which resources must exist before which — the dependency graph + canonical create/teardown orders. |
| [`quotas-and-budgets.md`](quotas-and-budgets.md) | Account caps (vpc=5, private-dns=3, …), which scenarios consume them, reserve/skip behavior. |
| [`validated-facts.md`](validated-facts.md) | Runtime-confirmed truths the docs don't tell you (id fields, undocumented required fields, state machines, delete races). The highest-value file. |
| [`scenario-catalog.md`](scenario-catalog.md) | The CRUD lifecycles that exist today, light vs heavy, and the coverage gap list. |
| [`services.md`](services.md) | Per-service notes (request bodies, captures, quirks). Add a section per service as you become expert. |

## Provenance discipline

Every non-obvious fact is tagged:

- **VALIDATED** — confirmed by a real 2xx at runtime (trust it).
- **from docs** — taken from the API Reference, not yet runtime-confirmed
  (best-effort; verify before relying on it for mutations).

Never silently promote a "from docs" guess to VALIDATED — only a real success does.
