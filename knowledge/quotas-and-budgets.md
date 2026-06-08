# Quotas & budgets

Account limits are modelled as **data** so the scenario scheduler respects them
instead of producing false regressions. Source: `core/budgets.py`
(`DEFAULT_LIMITS`) + `regression/scenarios/dependencies.json`.

## Capped resource kinds

| Kind | Default cap | Notes |
|------|-------------|-------|
| `vpc` | **5** | The big one — many heavy scenarios each stand up their own VPC. |
| `private-dns` | **3** | Used by DNS / shared-networking flows. |

(Re-check `core/budgets.DEFAULT_LIMITS` for the live list.)

## Which scenarios consume quota (`dependencies.json::quota_kinds`)

| Lifecycle | Consumes |
|-----------|----------|
| `networking-vpc-subnet` | vpc |
| `networking-vpc-internet-gateway` | vpc |
| `container-ske-cluster-nodepool` | vpc |
| `compute-virtualserver-full` | vpc |
| `database-mysql-cluster` | vpc |
| `heavy-shared-dbaas` | vpc |
| `heavy-shared-networking` | vpc, private-dns |

`budget_paths` maps the create PATH that consumes a kind → the kind
(`/v1/vpcs → vpc`, `/v1/private-dns → private-dns`), mirroring
`engine._budget_kind_for_path` so the mapping has one source of truth.

## Reserve / skip behavior (the anti-false-regression rule)

- The engine **reserves** a slot in `core.budgets` **before** a quota-bound create
  and **releases** it on teardown.
- When the cap is reached, the scenario **environmentally skips** (not fails) —
  quota pressure must never surface as a regression.
- A multi-process scheduler should `Budget.sync()` each kind from a live `list`
  call first, then gate concurrency on `Budget.available(kind)`.

## Scheduling consequence

Because there are 5 VPC slots and several heavy lifecycles each need their own
VPC (plus the two light VPC lifecycles), a scheduler must **serialize** VPC-creating
lifecycles once live usage + reservations reach the cap, rather than run them all
concurrently. Light, non-VPC lifecycles (keypair, filestorage, queue, cert,
security-group, scr) can run freely alongside.

## When you change quotas

If a service's real cap differs from the default, update `core/budgets.DEFAULT_LIMITS`
**and** this file. If you enable a new VPC- or private-dns-consuming lifecycle,
add its row to `dependencies.json::quota_kinds` (and here) so the scheduler
serializes it correctly. `networking-dns-hosted-zone` is currently disabled (needs
a private-dns activate sub-flow); when re-enabled it should declare `private-dns`.
