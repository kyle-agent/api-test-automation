# HANDOFF — VALIDATION-QUEUE Wave A.1 light-batch dispatch prep

> Prepared OFFLINE by the coverage-validator (2026-06-17). No live run was
> dispatched, nothing committed. A CI run was in_progress at prep time, so the
> resume command below MUST wait for the owner-rule (one run at a time) to clear.
>
> **Ground truth re-verified this session (not from memory):**
> - `python knowledge/formal/validate.py` → **275 resource task nodes / 59 files, 0 errors** (131 VALIDATED / 144 docs).
> - `python -m regression.scenarios.validate` → **193 lifecycles, 0 errors**.
> - `python -m spec.coverage_gap` → **static gap 0** (id-bound GET 0 / write 0).
> - Repo for dispatch = **kyle-agent/api-test-automation**, workflow `api-test.yml`.

---

## RESUME COMMAND (run ONCE, after the in-flight CI run fully concludes — sweep included)

The Wave A.1 READY batch is one **light, non-heavy** CRUD run. It needs
`SCP_ALLOW_MUTATIONS=true` (servicewatch / quick-query-validate / network-logging /
resource-group POSTs) and `SCP_ALLOW_DESTRUCTIVE=true` (network-logging /
resource-group / alert / dashboard / custom-log DELETEs). **No `SCP_RUN_HEAVY`.**

`crud_filter` is a pytest `-k` expression matched against the composed lifecycle id.

```
crud_filter=gen-pricing-reads or gen-cost-reads or gen-quota-request or gen-support-inquiry or gen-support-service-request or gen-quick-query-list or gen-quick-query-image-versions or gen-quick-query-validate or gen-network-logging-storage or gen-resource-group-bulk or gen-sw-metric-catalog or gen-alert or gen-dashboard or gen-sw-custom-metric-meta or gen-sw-custom-log-collect or gen-cm-account-resource
mutations=true  destructive=true  heavy=false
```

> **Hard prerequisite — DONE (2026-06-17):** the 16 READY `gen-*` lifecycles are
> now **materialized into `regression/scenarios/lifecycles/generated__waveA1.json`**
> (composed via `composer.compose([node])`, `enabled` flipped to `true`).
> `python -m regression.scenarios.validate` → **209 lifecycles, 0 errors** (16 new,
> 0 new warnings); the loader resolves every id, so the `crud_filter` above now
> matches. The batch is **dispatchable** the moment the owner-rule lane clears
> (one run at a time — confirm the in-flight conformance run + its sweep have
> concluded before pushing the run-request to `main`).

---

## READY to dispatch (one light run) — 16 nodes / 1 run

All compose cleanly and pass the composer's embedded scenario-validator invariants.
For each, the composer's `_validate_composed` ran with 0 errors.

| node | gen lifecycle | compose | body source / proven? | gates | endpoints exercised | promotion note |
|------|---------------|---------|----------------------|-------|---------------------|----------------|
| pricing-reads | `gen-pricing-reads` | OK (3 steps) | read-only, no body — VALIDATED (run 27583285457) | none (GET) | GET /v1/reports/offerings · /v1/reports/billing-item-ids · /v1/reports/prices | already VALIDATED; run re-confirms IB-041 evidence |
| cost-reads | `gen-cost-reads` | OK (3) | read-only — VALIDATED (27583285457) | none | GET /v1/bills · /v1/usages · /v1/payments/monthly | already VALIDATED |
| quota-request | `gen-quota-request` | OK (2) | read-only, `capture_soft` — VALIDATED | none | GET /v1/quota-requests · /v1/quota-requests/{request_id} | already VALIDATED |
| support-inquiry | `gen-support-inquiry` | OK (3) | read-only; known_inquiry_id `sr-260613-00001-nuri-scp` (owner) — VALIDATED | none | GET /v1/inquiries · /v1/inquiries/{known} · /v1/inquiries/{id} | already VALIDATED |
| support-service-request | `gen-support-service-request` | OK (3) | read-only; known_sr_id `SR0003870935` (owner) — VALIDATED | none | GET /v1/service-requests (+known +id) | already VALIDATED |
| quick-query-list | `gen-quick-query-list` | OK (2) | read-only `?size=20&page=0` (smoke-400 fix) — VALIDATED (27593608514, IB-045) | none | GET /v1/quick-query?size=20&page=0 (×2) | already VALIDATED |
| quick-query-image-versions | `gen-quick-query-image-versions` | OK (2) | read-only, no params — VALIDATED (27583285457) | none | GET /v1/quick-query/image-versions (×2) | already VALIDATED |
| **quick-query-validate** | `gen-quick-query-validate` | **OK (2) — fixed this session** | docs/UNPROVEN; real `QuickQueryValidateResourceRequest` (api_docs) dry-run preflight, no id created | mutations | POST /v1/quick-query/validate-resources (create + verify revalidate) | **docs→ promote candidate.** Dry-run, non-billable. 2xx of the POST lands in IB-041 evidence (create step is unmasked). |
| network-logging-storage | `gen-network-logging-storage` | OK (4) | body `{bucket_name, resource_type}` (real DTO) — VALIDATED (27583285457). bucket `apitest-logsink` ensured by workflow | mutations + destructive | POST /v1/network-logging/storages · GET storages?rt= · GET configurations?rt= · DELETE storages/{id} | already VALIDATED |
| resource-group-bulk | `gen-resource-group-bulk` | OK (3) | body `{name, description}` (proven on `resource-group`) — VALIDATED (27583285457) | mutations + destructive | POST /v1/resource-groups · GET /v1/resource-groups · DELETE /v1/resource-groups `{ids:[...]}` | already VALIDATED |
| sw-metric-catalog | `gen-sw-metric-catalog` | OK (3) | POST `/v1/metrics {}` lookup + metric-data/image (docs, but VALIDATED 27583285457) | mutations | POST /v1/metrics · /v1/metrics/data · /v1/metrics/data/download/image | already VALIDATED (lookup, no delete — expected warning) |
| **alert** | `gen-alert` | OK (7) | docs/UNPROVEN; `AlertCreateRequest` w/ metric_id+namespace_id wired from `sw-metric-catalog` lookup; RANGE w/ lower/upper_bound (threshold removed, run 27395331657) | mutations + destructive | POST /v1/metrics · POST /v1/alerts · GET /v1/alerts/{id} · PATCH /v1/alerts/{id}(+/activated +/description) · DELETE /v1/alerts `{ids}` | **docs→ promote candidate.** Depends on metric-catalog capturing a real namespace/metric — fail-fast if account has no metric-emitting resource (note below). |
| dashboard | `gen-dashboard` | OK (4) | body from `api_bodies createdashboard`; create 201 live (27396649009) — VALIDATED; delete field `dashboard_ids` (not `ids`) | mutations + destructive | POST /v1/dashboards · GET /v1/dashboards/{id} · PUT /v1/dashboards/{id} · DELETE /v1/dashboards `{dashboard_ids}` | already VALIDATED |
| sw-custom-metric-meta | `gen-sw-custom-metric-meta` | OK (3) | docs verbatim — VALIDATED (27583285457); ingest tolerates [202,400] (SWT_CUSTOM_NAMESPACE routing unknown) | mutations | POST /v1/metrics/custom/meta · /v1/metrics/custom · /v1/metrics/data | already VALIDATED (lookup, no delete — expected warning) |
| sw-custom-log-collect | `gen-sw-custom-log-collect` | OK (6) | docs verbatim — VALIDATED (27583285457); rides log-group+log-stream | mutations + destructive | POST /v1/log-groups · …/log-streams · …/collect/custom · …/log-events · DELETE log-streams · log-groups | already VALIDATED |
| cm-account-resource | `gen-cm-account-resource` | OK (2) | read-only lookup; bare GET 200 (stale X-ResourceType header removed) | none (GET) | GET /v1/cloudmonitorings/product/v2/accounts/products (×2) | **docs→ promote candidate**, but capture `product_resource_id` (where_prefix INSTANCE-) fail-fasts if no Running VM exists (note below). The GET itself 2xx's regardless. |

**Why these 16 are one run:** all non-heavy, none pulls a heavy prereq into its
closure, none needs `SCP_RUN_HEAVY`. The read-only nodes warm the IB-041 evidence
path; the light creates (network-logging, resource-group-bulk, servicewatch,
quick-query-validate) all have a destructive teardown so nothing is left behind.

---

## Blocked / gated (do NOT include in the run) — 7 nodes

| node | gen lifecycle | why blocked | STOP-6 / IB |
|------|---------------|-------------|-------------|
| **sts-token** | `gen-sts-token` | **does NOT compose** — `role_indicator` is `required` with no default (needs a real role SRN). Composer raises `required option 'role_indicator' has no value`. This is the **intended Planning-form (M5) gate**: iam-role create is itself 500-blocked, so the SRN must be a console-issued role fed via the planning form. | STOP-6 ① credential. Owner must supply a real assumable role SRN. |
| **trail** | `gen-trail` | **does NOT compose** — create body field `account_id` defaults to `{account_id}`, which is **not an engine builtin** (engine seeds only unique/ualpha/region/today/today_plus_5y/cert_* /shared_*). Composer's validator rejects: `references undefined placeholders ['account_id']`. Also needs a pre-existing Object Storage bucket whose region matches `bucket_region` (IMMUTABLE). | STOP-6 ① credential/precondition. Needs the caller account id wired (engine builtin or credential context) + the ensured `apitest-logsink` bucket; not a body typo. |
| **account-budget** | `gen-account-budget` | composes, but create is a **confirmed product bug**: `POST /v1/budgets/account` → 500 ContactAdminForAssistance (run 27395331657), already baselined in `data/baselines/known_issues.json` (PF-04). Will never 2xx until backend fix. | STOP-6 ③ product defect. Stays out of waves; retest after backend fix. |
| **diagnosis** | `gen-diagnosis` | composes (3 steps) but declares credential `inspectable-account-auth-key`; create body uses doc-sample account/auth_key/diagnosis ids → KNOWN-UNSATISFIABLE 4xx. Needs a throwaway inspectable account + real auth key (console-issued). | STOP-6 ① credential. |
| **secretvault-vault** | `gen-secretvault-vault` | composes (4 steps) but declares credential `iam-temp-auth-key`; `access_key_id` default is a doc placeholder → 4xx. Needs a console-issued IAM temp-mode auth key (unexpired, ≥30d, unbound). | STOP-6 ① credential. |
| **certificate-import** | `gen-certificate-import` | composes (3 steps) but **KNOWN-UNSATISFIABLE on this account**: SCP check-validation/import rejects the engine's runtime-generated self-signed key as "not a PEM format", and no real CA-signed material is available. No real 2xx possible. (NOTE: the self-sign sibling `certificate` is already VALIDATED — the queue's rank-15 "import a self-signed cert body" wording points at `certificate-import`, which is the unsatisfiable IMPORT path, not the self-sign create.) | STOP-6 ① credential (CA-signed cert material) / product-class rejection. |
| **cm-event-policy** | `gen-cm-event-policy` | composes (6 steps) and is light/non-billable, so it COULD ride the run, but: (a) cloudmonitoring is **EOL after 2026-09 → ServiceWatch** (IB-034) — terminal, do not deepen; (b) the create body is docs/UNPROVEN (never 2xx'd) and depends on `cm-account-resource` capturing a real `product_resource_id`, which fail-fasts unless the account has a Running VM. **Excluded from the READY filter** to keep the masked-defect risk out of this batch; revisit only with a live VM in the account and an explicit IB-034 decision. | STOP-6 (EOL/terminal) — not owner-blocking, just de-prioritized. |

---

## Cross-checks performed (bodies / required options vs knowledge + notes)

- **No UNPROVEN body was promoted to `strict`** — all docs-node creates rely on the
  IB-041 per-endpoint evidence path, not blanket strictness (masked-defect rule).
- **sts-token `role_indicator`** confirmed as a Planning-form gate (real SRN
  required, no default) — matches the node's own note and validation-agent STOP-6 ①.
- **trail `account_id`** confirmed unfillable: engine `_fill` leaves `{account_id}`
  literal (not in `ctx`, not an `env:` token); `BUILTINS` has no `account_id`.
  Verified there is no runtime seeder for it in `core/` or `regression/scenarios/engine.py`.
- **account-budget** matches PF-04 (baselined 500); left blocked, not "fixed to pass".
- **alert** body cross-checks `knowledge/validated-facts.md` servicewatch entry
  (RANGE uses lower/upper_bound, threshold must be absent — run 27395331657);
  metric_id/namespace_id correctly wired from the `sw-metric-catalog` lookup.
- **dashboard** delete uses `dashboard_ids` (not `ids`) — matches the run 27398084089 fact.
- Servicewatch lookup nodes (`sw-metric-catalog`, `sw-custom-metric-meta`) have no
  delete by design → the R1 "create without delete" warning is expected, not an error.

## Edits made (across the prep + materialization)

1. `knowledge/formal/resources/data-analytics__quick-query.yaml` — added an explicit
   `verify` (idempotent dry-run re-POST of `/v1/quick-query/validate-resources`) to
   the `quick-query-validate` node so the composer no longer falls back to read-path
   derivation and raises. After the edit: `python knowledge/formal/validate.py` →
   **0 errors** (warnings 84→79); `compose(["quick-query-validate"])` → OK (2 steps).
2. `docs/working/handoffs/HANDOFF-waveA1-dispatch-prep.md` — this file.
3. **`regression/scenarios/lifecycles/generated__waveA1.json` (materialization,
   2026-06-17)** — the 16 READY nodes composed and `enabled: true`. Regenerate with:
   `compose([node]) for node in <16 READY>` → flip `enabled` → write fragment →
   `python -m regression.scenarios.validate` (209 lifecycles, 0 errors).

## Tally

- **Wave A.1 in-scope nodes: 23.** READY to dispatch in one light run: **16**
  (13 already VALIDATED + re-confirm; 3 docs→promote candidates: quick-query-validate,
  alert, cm-account-resource — of which cm-account-resource's GET 2xx's regardless).
- **Blocked/gated: 7** (sts-token, trail, account-budget, diagnosis, secretvault-vault,
  certificate-import, cm-event-policy).
- **Docs→VALIDATED promotable from this single run (best case):** quick-query-validate,
  alert, cm-account-resource (alert + cm-account-resource conditional on a
  metric-emitting / Running-VM account; quick-query-validate is unconditional).
