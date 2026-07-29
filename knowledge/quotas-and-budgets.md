# Quotas & budgets

Account limits are modelled as **data** so the scenario scheduler respects them
instead of producing false regressions. Runtime source of truth:
`core/budgets.py` (`DEFAULT_LIMITS`) + `regression/scenarios/dependencies.json`.

A quota becomes a *budget kind* only when it is wired into `dependencies.json`
`quota_kinds`/`budget_paths` **and** `core/budgets.py`; documentation-only
quotas are tracked in §"Documentation-only quotas" below and in
`knowledge/formal/cross-service.yaml` (`quotas:` section) until then.

> 2026-07-29 통합: 구 `docs/quotas-and-budgets.md`(IB-038 때 신설)를 이 파일로
> 병합 — 같은 주제의 문서 2개가 docs/와 knowledge/에 흩어져 있던 것을 단일화.

## Budget kinds enforced at runtime (`core/budgets.py` DEFAULT_LIMITS)

| kind | limit | scope | provenance / notes |
|------|-------|-------|--------------------|
| `vpc` | **5** | account | VALIDATED (live error "The number(5) of VPCs ... exceeded", run 27306490231). The big one — many heavy scenarios each stand up their own VPC. |
| `private-dns` | **3** | account | `scp-network.private-dns.max-count-exceed`. Used by DNS / shared-networking flows. |

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
security-group, scr) can run freely alongside. Current lane playbook:
`knowledge/vpc-scheduling-strategy.md` (machine-readable:
`dependencies.json:vpc_schedule`).

## Documentation-only quotas (not yet budget kinds)

These are modeled in `knowledge/formal/cross-service.yaml` and/or per-service
yaml but are NOT wired to a runtime `quota_kind`, so they do not gate runs yet.

### Firewall rule quota (IB-038)

- **Default firewall size `EXSMALL` = 5 rules.** A firewall is never created
  standalone — it is implicitly minted by enabling "Firewall 사용" on an Internet
  Gateway / Transit Gateway / Direct Connect / Load Balancer (see
  `cross-service.yaml` cross_constraint `firewall-implicit-on-gateway-resources`),
  and it starts at the `EXSMALL` size with a **5-rule cap**.
- **Resize before exceeding 5 rules** via `PUT flavor_name`:
  `SMALL=100` / `MEDIUM=200` / `LARGE=500` / `EXLARGE=1000`
  (userguide, validated-facts.md 2026-06-15, docs/UNPROVEN).
- **Current single-rule firewall scenarios are safe** — they create at most one
  rule, well under the `EXSMALL` 5-rule default.
- **NOT yet a budget kind**: there is no `firewall-rule` entry in
  `core/budgets.py` `DEFAULT_LIMITS` nor a `quota_kind`, so the budget guard does
  not track rule count. If a future scenario approaches 5 rules on an `EXSMALL`
  firewall it must resize the firewall (`PUT flavor_name`) first, or it will
  4xx on the 6th rule. Default firewall policy is "Any Deny" (an ALLOW rule is
  required to pass traffic).

### Hosted Zone quota (IB-039)

- 20 zones/account, 100 records/zone (see `cross-service.yaml` `quotas:
  hosted-zone`, inert entry — not wired to a lifecycle `quota_kind`).

### Direct Connect quota

- 5 per service-zone, 1:1 per VPC (see `cross-service.yaml` resource
  `direct-connect` notes; not yet a budget kind — tracked by IB-037).

## When you change quotas

If a service's real cap differs from the default, update `core/budgets.DEFAULT_LIMITS`
**and** this file. If you enable a new VPC- or private-dns-consuming lifecycle,
add its row to `dependencies.json::quota_kinds` (and here) so the scheduler
serializes it correctly. `networking-dns-hosted-zone` is currently disabled (needs
a private-dns activate sub-flow); when re-enabled it should declare `private-dns`.
