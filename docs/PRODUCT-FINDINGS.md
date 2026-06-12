# PRODUCT-FINDINGS — consolidated ledger of product/API findings

- Date: 2026-06-12 · Status: **active** (append-only — new findings get the next id)
- Scope: things the R3 verification waves found that are **about the product,
  not about our tests** — backend bugs, spec/docs defects, IAM-policy gaps,
  service quirks, and live confirmations of userguide facts. One row per
  finding. The per-node detail (exact bodies, retry semantics) stays in the
  node `notes` of `knowledge/formal/resources/*.yaml` and in
  `data/baselines/known_issues.json`; this file is the cross-service index.
- Relationship to other docs: wave-level narrative in
  `docs/RESOURCE-MODEL-PLAN.md` §6; baselined entries (muted on the dashboard)
  in `data/baselines/known_issues.json`; AXIS-2 conformance findings
  (`reports/results/findings.jsonl`) are a separate, automated stream — this
  ledger is the curated, human-triaged one.

## Ledger

| id | service / endpoint | symptom | evidence (run id) | class | status |
|----|--------------------|---------|-------------------|-------|--------|
| PF-01 | management/servicewatch · `GET /v1/log-groups/{id}/log-streams` (LIST) | 403 `Action definition is not found` — the LIST is unauthorizable while the per-id GET works | 27394211896 | missing-IAM-action-definition | open — verify rerouted to per-id GET (node note) |
| PF-02 | application-service/apigateway · `GET …/api-keys/{key_id}` (per-id) | 403 `Action definition is not found` — per-id GET unauthorizable while LIST works | 27394211896 | missing-IAM-action-definition | open — verify keeps `[200,403]` to track it |
| PF-03 | storage/filestorage · `GET /v1/snapshots/{id}` (per-id) | 403 `Action definition is not found` — third case of the same class | 27399448835 | missing-IAM-action-definition | open (node note) |
| PF-04 | financial-management/budget · `POST /v1/budgets/account` | 500 `ContactAdminForAssistance` on a complete-looking body — a validation problem must not 500 | 27395331657 | product-bug (5xx on valid-shaped input) | **baselined** Product Bug in `known_issues.json`; node out of composed waves |
| PF-05 | devops-tools/devopsservice · `POST /v1/devops-services` | 400 ValidationError with `["Field required"]`×3 **without field names** — the error schema hides which fields are missing | 27394211896 | error-schema-defect (also an AI-usability case) | open — node disabled pending userguide research |
| PF-06 | compute/scf · `GET /v1/cloud-functions/{id}/logs` · `/metrics` | `must select either time or period` — docs mark **all** params optional, but one of time/period is required (format undocumented) | 27399448835 | undiscoverable-params | worked around (`?time=1h`); docs fix needed |
| PF-07 | management/cloudmonitoring · `GET /v1/cloudmonitorings/accounts/products…` | 400 `InvalidHeaderValue 'X-ResourceType'` — a **required header that appears nowhere in the docs** | 27399448835 | undocumented-required-header (new defect class) | worked around — model carries `headers: {X-ResourceType: INSTANCE}`; header accepted in rev 2 |
| PF-08 | container/scr · `PUT /v1/container-registries/{id}/private-acl` | 500 `ContactAdminForAssistance` on a doc-valid body — reproduced 3× | 27401527554 · 27417986669 · 27421363609 | product-defect candidate (5xx, possibly an Editing-state race) | open — verify removed from the composed chain; retry-style verify is an R3 follow-up |
| PF-09 | security/kms + security/secretsmanager · `DELETE` key/secret | Deletion is **scheduled**, not immediate: deleted (2xx) items stay in lists for their pending-deletion window (gone from console) | 27401527554 (sweep log, 5 rounds) | service-quirk (scheduled deletion) | recorded in service quirks; sweep no longer counts re-deletes as progress |
| PF-10 | container/scr · `POST /v1/container-registries` | 403 quota `CONTAINER_REGISTRY.NON_VISIBILITY.MAX` **1EA** — live confirmation of the userguide's max-2-per-account (1 per visibility type) rule | 27421363609 (docker probe) | userguide-fact-confirmed (not a defect) | closed — probe now borrows an existing Running registry on quota-403 |
| PF-11 | compute/scf · `DELETE /v1/triggers/{id}` body `trigger_type` | API requires `'cron'`; the hand-written probe sent `'cronjob'` and had been **silently 400ing for weeks** behind a tolerant `expect [.., 400]` | 27417986669 (unmasked) | masked-defect (harness) | fixed — model sends `cron`; lesson below |
| PF-12 | compute/virtualserver · `POST …/interfaces/{port_id}/static-nats` body | Real field is `publicip_id`; the probe's `public_ip_id` had been silently 400ing behind a tolerant expect — the live ValidationError handed us the field name | 27421363609 | masked-defect (harness) | fixed — model sends `publicip_id` (heavy rev 3 dispatched) |
| PF-13 | compute/virtualserver · `POST …/interfaces/{port_id}/static-nats` | 400 `VirtualServer.InvalidVpcPublicIp` "The VPC should have at least one **Internet Gateway** when attaching Internet NAT" — precondition not in the userguide/API docs for this endpoint | 27424991237 | undiscoverable-params (undocumented prerequisite) | fixed — `internet-gateway` added to `server-static-nat` requires; heavy rev 4 |
| PF-14 | container/scr · `GET /v1/repositories/check-duplication/name` | `name` alone → 400 `ValidationError ["Field required"]` without naming the field; repo names are registry-scoped so `registry_id` is required — neither the requirement nor the field name is discoverable from the error or docs | 27424991237 | error-schema-defect + undiscoverable-params | fixed — model sends `registry_id` query param |

## The masked-defect lesson (PF-11 · PF-12)

Tolerant expectations (`expect_status: [200, 400]` etc.) on hand-written
"coverage probes" can hide that a step has **never once succeeded** — the row
stays green while the call 400s forever. Two such cases surfaced only when the
composed (model-driven) chains demanded a real 2xx:

- scf trigger delete `trigger_type: cronjob` → the API wants `cron` (PF-11);
- VS static-NAT `public_ip_id` → the API wants `publicip_id` (PF-12).

Rules of thumb adopted: ① composed verify/teardown steps expect success —
widen an expect list only WITH a finding note explaining why (the PF-02
pattern); ② when a live error names a field/enum, encode it in the model
immediately with the run id; ③ treat any long-green tolerant-expect step as
unverified until a 2xx is on record.

## Conventions

- **id** — `PF-NN`, append-only, never reused.
- **class** — missing-IAM-action-definition · product-bug ·
  error-schema-defect · undiscoverable-params · undocumented-required-header ·
  service-quirk · userguide-fact-confirmed · masked-defect.
- **status** — open · worked around · baselined · fixed · closed.
- A finding that gets muted on the dashboard must ALSO be a row in
  `data/baselines/known_issues.json` (PF-04 is the model case).
