# knowledge/formal/ — formalized domain knowledge (DRAFT)

> **이 디렉토리가 "사람이 읽고 조정하는" 도메인 지식의 공식 형식입니다.**
> 도메인 지식은 세 층위로 구조화됩니다 — **① 서비스별 지식/제약**,
> **② 연관 서비스 간 제약**, **③ 시나리오 흐름 지식** — 그리고 조합
> 시나리오는 **시나리오 단위 검토(review 블록)** 를 거칩니다.
> 워크플로: **AI가 초안 작성 → 사람이 YAML 수정/승인 → `validate.py` → 엔진 데이터에 반영.**

Domain knowledge is structured in **three layers**, plus a reviewed scenario
catalog. AI agents draft it; **humans edit and approve it**; the validator
keeps it consistent with the engine data (`regression/scenarios/*.json`).

| Layer | File(s) | Question it answers |
|-------|---------|---------------------|
| **1 · Per-service** | [`services/<category>__<service>.yaml`](services/) | 이 서비스 자체의 지식/제약 — constraints, id capture shapes, state machines, quirks of ONE service |
| **2 · Cross-service** | [`cross-service.yaml`](cross-service.yaml) | 연관 서비스 간 제약 — the resource dependency graph (what must exist before what), constraints spanning services, account quotas |
| **3 · Scenario-flow** | [`flows.yaml`](flows.yaml) | 시나리오 흐름 지식 — rules that only exist in a flow (teardown order, delete races, shared-VPC adoption, scheduling) + canonical per-resource call orders |
| **Review** | [`combo-scenarios.yaml`](combo-scenarios.yaml) | 조합 시나리오 + 시나리오 기반 검토 — multi-service combos with a per-scenario `review:` block a human approves |

Validate after every edit (offline, no credentials needed):

```bash
python knowledge/formal/validate.py
```

The validator checks YAML structure per layer, that every `requires:`/resource
reference exists, that the dependency graph is acyclic, that quota limits match
`regression/scenarios/dependencies.json`, that `encoded_in` lifecycle ids exist
in the merged scenario data, that service names exist in the catalog, and that
every non-encoded combo carries a `review:` block with a valid `decision`.

## Provenance (applies to every entry, all layers)

- `provenance: VALIDATED` — confirmed by a real 2xx at runtime. Trust it.
- `provenance: docs` — taken from the API Reference, not yet runtime-confirmed.

Never promote `docs` → `VALIDATED` without a real successful run.

## Layer 1 — `services/<category>__<service>.yaml`

One file per service (same naming as `regression/scenarios/lifecycles/`
fragments — one owner per file, no collisions). Knowledge that is true of the
service regardless of any scenario:

```yaml
version: 1
service: <category>/<service>     # must match the filename
constraints:                      # hard rules this service imposes
  - id: <kebab-id>
    rule: <text a human can act on>
    provenance: VALIDATED|docs
captures:                         # where each resource's id lives in the response
  <resource-key>: <jsonpath>      # e.g. server: $.servers[0].id   (ARRAY!)
states:                           # state machines (poll field -> ready values)
  <resource-key>: {field: <jsonpath>, ready: [<values>]}
quirks:                           # everything else the docs don't tell you
  - id: <kebab-id>
    note: <text>
    provenance: VALIDATED|docs
```

## Layer 2 — `cross-service.yaml`

The dependency **graph** (which resource must exist before which) plus
constraints that span services:

```yaml
version: 1
cross_constraints:                # rules involving >1 service
  - id: <kebab-id>
    services: [<category/service>, ...]
    rule: <text>
    provenance: VALIDATED|docs
resources:                        # the graph nodes
  <resource-key>:
    service: <category/service>   # owning service
    requires: [<resource-key>]    # MUST exist first
    lookups:  [<name>]            # read-only finds needed (no resource created)
    quota: <quota-key>            # optional: capped kind this create consumes
    provenance: VALIDATED|docs
quotas:
  <quota-key>: {limit: <int>, scope: account|region, provenance: ...}
```

## Layer 3 — `flows.yaml`

Knowledge that only exists **in the context of a flow** — no single endpoint
exhibits it:

```yaml
version: 1
defaults:                         # global flow rules unless overridden
  teardown: reverse-order
  delete_poll: {until_status: [404]}
  delete_retry_on: [409, 500]
flow_rules:                       # named, citable flow-level rules
  - id: <kebab-id>
    rule: <text>
    provenance: VALIDATED|docs
call_orders:                      # canonical pattern per resource family
  <resource-key>:                 # must exist in cross-service.yaml resources
    provenance: VALIDATED|docs
    encoded_in: [<lifecycle-id>]  # scenario ids realizing this pattern
    create: {api: <METHOD /path>, capture: <jsonpath>, poll: {...}}
    delete: {api: <METHOD /path>}
    notes: <gotchas — field-level detail stays in services/ or validated-facts.md>
```

## Combos + scenario-based review — `combo-scenarios.yaml`

```yaml
version: 1
combos:
  - id: <kebab-id>                # == lifecycle id once encoded
    status: encoded|draft|idea
    heavy: true|false
    services: [<category/service>]
    flow: [<resource-key or action>]
    value: <why this combination is worth testing>
    encoded_in: <path>            # required when status: encoded
    review:                       # REQUIRED for draft/idea (scenario-based review)
      decision: pending|approved|rejected   # ← 사람이 바꿔서 승인/반려
      checks: [<what to verify before approving>]
      risks:  [<cost / quota / blast-radius accepted by approving>]
      notes:  <reviewer free text>
```

**Review flow:** `idea` → `draft` (agent fills `checks`/`risks`) → human sets
`decision: approved` (optionally editing `flow`) → Domain-Knowledge agent
encodes it as a lifecycle in `regression/scenarios/` and flips
`status: encoded` (the `review:` block stays as the audit record). `rejected`
entries stay in the file as institutional memory of what NOT to build.

## Relationship to the rest of the repo

```
knowledge/*.md            narrative (why / history / field-level detail)
knowledge/formal/         ← THE editable formal model (this directory)
  services/*.yaml           layer 1: per-service
  cross-service.yaml        layer 2: between services
  flows.yaml                layer 3: scenario flows
  combo-scenarios.yaml      combos + per-scenario review
regression/scenarios/*.json   what the engine actually executes
core/budgets.py               quota limits enforced at runtime
```

Long-term direction (see `ROADMAP.md`): the formal files become the source of
truth from which `dependencies.json` entries are **generated**; until then the
validator keeps the two consistent.
