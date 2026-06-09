# knowledge/formal/ — formalized domain knowledge (DRAFT)

> **이 디렉토리가 "사람이 읽고 조정하는" 도메인 지식의 공식 형식입니다.**
> AI 에이전트가 초안을 생성하고, 사람이 YAML을 직접 수정하면, 검증기가
> 엔진 데이터(`regression/scenarios/*.json`)와의 정합성을 확인합니다.
> 워크플로: **AI가 초안 작성 → 사람이 수정 → `validate.py` → 엔진 데이터에 반영.**

This directory is the **formal, human-editable** form of the SCP domain
knowledge that the narrative docs (`knowledge/*.md`) describe and the engines
(`regression/scenarios/*.json`) consume. Three YAML files, one schema each:

| File | Question it answers | Edited by |
|------|--------------------|-----------|
| [`service-graph.yaml`](service-graph.yaml) | 무엇이 무엇을 필요로 하는가 — which resource must exist before which | human + Domain-Knowledge agent |
| [`call-orders.yaml`](call-orders.yaml) | 한 리소스를 어떤 순서/규칙으로 호출하는가 — canonical create→poll→teardown pattern per resource | human + Domain-Knowledge agent |
| [`combo-scenarios.yaml`](combo-scenarios.yaml) | 어떤 API 조합을 함께 테스트할 가치가 있는가 — multi-service combination test scenarios | **human approves drafts** |

Validate after every edit (offline, no credentials needed):

```bash
python knowledge/formal/validate.py
```

The validator checks YAML structure, that every `requires:` reference exists,
that the dependency graph is acyclic, that quota kinds match
`regression/scenarios/dependencies.json`, and that every `status: encoded`
combo points at a real lifecycle id in the merged scenario data.

## Provenance (applies to every entry)

- `provenance: VALIDATED` — confirmed by a real 2xx at runtime. Trust it.
- `provenance: docs` — taken from the API Reference, not yet runtime-confirmed.

Never promote `docs` → `VALIDATED` without a real successful run.

## Schemas

### service-graph.yaml

```yaml
version: 1
resources:
  <resource-key>:                # short stable key, e.g. vpc, subnet, server
    service: <category/service>  # which SCP service owns the API
    requires: [<resource-key>]   # MUST exist first (created inline by scenarios)
    lookups:  [<name>]           # read-only finds needed (image, server-type, …)
    quota: <quota-key>           # optional: capped kind this create consumes
    provenance: VALIDATED|docs
    notes: <free text>
quotas:
  <quota-key>: {limit: <int>, scope: account|region, provenance: ...}
```

### call-orders.yaml

One entry per resource family = the **canonical pattern**, not a copy of every
scenario step (steps live in `regression/scenarios/`). `encoded_in` links to
the lifecycle id(s) that realize the pattern.

```yaml
version: 1
defaults:
  teardown: reverse-order            # global rule unless overridden
  delete_poll: {until_status: [404]}
call_orders:
  <resource-key>:
    provenance: VALIDATED|docs
    encoded_in: [<lifecycle-id>]     # scenario ids realizing this pattern
    create:
      api: <METHOD /path>
      capture: <jsonpath>            # where the id comes back
      poll: {field: <jsonpath>, until: <ready-value>}   # async readiness
    delete:
      api: <METHOD /path>
      retry_on: [409, 500]           # dependency-release races
    notes: <gotchas — keep field-level detail in validated-facts.md>
```

### combo-scenarios.yaml

```yaml
version: 1
combos:
  - id: <kebab-id>                  # == lifecycle id once encoded
    status: encoded|draft|idea      # encoded = exists in scenarios; draft = awaiting human review
    heavy: true|false               # billable / long-running
    services: [<category/service>]
    flow: [<resource-key or action>]   # human-readable order of the combo
    value: <why this combination is worth testing>
    encoded_in: <path>              # required when status: encoded
```

**`status` lifecycle:** `idea` (agent suggestion) → `draft` (fleshed out,
awaiting human approval) → `encoded` (a lifecycle exists in
`regression/scenarios/` and runs). Humans approve by editing `status` and/or
the flow; the Domain-Knowledge agent then encodes approved drafts.

## Relationship to the rest of the repo

```
knowledge/*.md          narrative (why / history / field-level gotchas)
knowledge/formal/*.yaml ← THE editable formal model (this directory)
regression/scenarios/*.json   what the engine actually executes
core/budgets.py         quota limits enforced at runtime
```

Long-term direction (see `ROADMAP.md`): the formal files become the source of
truth from which `dependencies.json` entries are **generated**; until then the
validator keeps the two consistent.
