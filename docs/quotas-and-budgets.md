# Quotas and Budgets

Reference for service quotas and how they relate to the runtime budget guard
(`core/budgets.py` `DEFAULT_LIMITS`). A quota becomes a *budget kind* only when
it is wired into `dependencies.json` `quota_kinds`/`budget_paths` and
`core/budgets.py`; documentation-only quotas are tracked here and in
`knowledge/formal/cross-service.yaml` (`quotas:` section) until then.

## Budget kinds enforced at runtime (`core/budgets.py` DEFAULT_LIMITS)

| kind | limit | scope | provenance |
|------|-------|-------|------------|
| `vpc` | 5 | account | VALIDATED (live error "The number(5) of VPCs ... exceeded", run 27306490231) |
| `private-dns` | 3 | account | scp-network.private-dns.max-count-exceed |

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
