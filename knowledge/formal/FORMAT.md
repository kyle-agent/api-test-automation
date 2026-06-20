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

## Probe-read completion (owner principle, 2026-06-15)

> **Every created resource runs its COMPLETE lifecycle:
> create → ALL id-bound reads → ALL id-bound writes → delete.** (owner, 2026-06-15)

This is a durable, repo-wide rule for **every** resource — not a per-service
afterthought, not apigw-only. The resource-task model
(`resources/<category>__<service>.yaml`) is the place it lives. A node is "done"
only when, off the id its `create` captures, it exercises:
1. **all id-bound GET reads** (per-id `show` + `list-children`) — closes `gap_getid`;
2. **all id-bound writes** (setters / updates / PUT-PATCH / action sub-ops on that
   id) — closes `gap_write` (the write-coverage campaign drove this to 0; keep it
   there as new write ops appear);
3. **delete** (reverse-order teardown) — every created resource is owned and torn down.

The read side (this section's focus + `docs/working/plans/PROBE-READS-PLAN.md`, gap_getid=80) is
the remaining open axis; the write side is structurally in place via the
`*-write-coverage` lifecycles. `agents/validation-agent.md` uses this complete
4-stage shape as each node's **done-when**.

**Rule.** Every node whose `create` captures an id MUST attach, as `verify`
read steps, the **id-bound GET endpoints that key off that id** — the per-id
`show`, and the `list-children` endpoints rooted at it. A `verify` read keys off
the node's own `capture` (and its direct `requires`' captures, which the
validator already enforces). This turns every such GET from a `gap_getid`
endpoint (unreachable from any enabled scenario) into one that is **auto-covered
the moment the node is composed into an enabled lifecycle and run** — no
separate read-chain, no extra scenario authoring.

```yaml
  <node>:
    create: { endpoint: "POST /v1/things", ... }
    capture: { thing_id: "$.id" }            # ← the id this create yields
    verify:                                   # ← ALL id-bound GETs off thing_id
      - {name: read,        endpoint: "GET /v1/things/{thing_id}",          expect_status: [200]}
      - {name: list-kids,   endpoint: "GET /v1/things/{thing_id}/children", expect_status: [200]}
```

**Conventions (keep these tight):**

- **`expect_status: [200]` by default.** Only widen to **`[200, 404]`** when the
  child collection is *legitimately absent on a freshly-created parent* (e.g. a
  policy that was never set, a history that is empty right after create). Carry a
  one-line `note:` saying *why* the 404 is tolerated — never use `[200,404]` to
  paper over a real 4xx/5xx. (See the apigw `resource-policies` read below for a
  worked example of a justified-404 with a PF cross-reference.)
- **Mark required query params.** If a read needs a query param to avoid a 400
  (period/range for `reports`/`logs`/`metrics`, paging, etc.) and the docs give
  no model, carry a `note:` flagging it; on a live 400 **add the param** rather
  than widening `expect_status`.
- **Address-by-name reads** (no id in the response, e.g. apigw `stage`) reuse the
  create body's `{unique}`/`{ualpha}` literal (a run-constant) in the read path.
- **Prerequisite/`docs` parents:** do **not** add the verify until the node's
  `create` is `VALIDATED` — a verify on an UNPROVEN create is dead weight (the
  composer must not run unproven creates). Validate the create first, then add
  the one-line read (C5 promotion order).

**Worked example — the apigw tree** (`resources/application-service__apigateway.yaml`):
each create carries its id-bound reads, so the whole API sub-tree is covered on
one composed run.

| node | id captured | verify reads attached (id-bound GETs) |
|------|-------------|----------------------------------------|
| `apigw-api` | `api_id` | `apis/{api_id}/connected-endpoints` · `/reports` (note: may need period param) · `/resource-policies` (`[200,404]`, PF-19 — PUT 500 means no policy to read) |
| `apigw-root-resource` (lookup) | `root_resource_id` | `apis/{api_id}/resources` |
| `apigw-resource` | `resource_id` | `apis/{api_id}/resources/{resource_id}` |
| `apigw-method` | `method_type` | `.../methods` (list) · `.../methods/{method_type}` |
| `apigw-deployment` | `deployment_id` | `apis/{api_id}/deployments` |
| `apigw-stage` | (name-addressed) | `apis/{api_id}/stages` · `apis/{api_id}/stages/stg{unique}` |
| `apigw-usage-plan` | `usage_plan_id` | `.../usage-plans` · `usage-plans/{up}/api-keys` |
| `apigw-api-key` | `api_key_id` | per-id (`[200,403]` — IAM action undefined, PF) · list |

**Where it still needs applying:** the repo-wide map of `gap_getid` endpoints not
yet probe-read-covered — parent node, service, already-wired status, and the
cheapest (already-VALIDATED-parent) wins — lives in
[`docs/working/plans/PROBE-READS-PLAN.md`](../../docs/working/plans/PROBE-READS-PLAN.md). Regenerate the gap
total any time with `python -m spec.coverage_gap` (the `gap_getid` line). The
validation agent (`agents/validation-agent.md`) closes each row by validating the
parent create, then adding the one-line verify read.

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

Long-term direction (see `docs/ROADMAP.md`): the formal files become the source of
truth from which `dependencies.json` entries are **generated**; until then the
validator keeps the two consistent.
