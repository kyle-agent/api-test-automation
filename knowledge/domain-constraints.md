# SCP account domain constraints (confirmed) — networking & DBaaS

> Confirmed, load-bearing account facts the test harness must obey. Recorded in
> git so every run/agent honours them. Machine-readable mirror lives in
> `regression/scenarios/dependencies.json` (`vpc_schedule`, `quota_kinds`); the
> hard limits also live in `core/budgets.py` (`DEFAULT_LIMITS`).

## Quotas (hard account caps)

| Resource   | Max | Error on exceed                          |
|------------|-----|------------------------------------------|
| **VPC**    | **3** | `scp-network.vpc.exceed-max-count`     |
| private-dns| 3   | `scp-network.private-dns.max-count-exceed` |

- **VPC max = 3** (NOT 5 — corrected). `core/budgets.py:DEFAULT_LIMITS["vpc"]=3`
  and `dependencies.json:vpc_schedule.vpc_limit=3`.
- A run must never hold more than 3 live VPCs at once, *including* lingering
  async-deletes. The shared VPC counts as 1 while alive.

## CIDR rules (confirmed)

1. **VPCs must not overlap.** Any two VPCs that exist at the same time (the
   shared VPC + any self-created VPC, and self-created VPCs among themselves)
   MUST have non-overlapping CIDR blocks. Each VPC-creating lifecycle is
   assigned a unique `/20` (see allocation table below); the shared VPC owns
   `10.124.0.0/20`.
2. **A subnet's CIDR must be a sub-range of its VPC's CIDR.** Every subnet is
   carved from inside its parent VPC's `/20` (e.g. the shared subnet
   `10.124.0.0/24` is the first `/24` of the shared VPC `10.124.0.0/20`).

### VPC `/20` allocation (one unique block per VPC-creating lifecycle)

| Lifecycle | VPC CIDR | Class |
|-----------|----------|-------|
| *(shared VPC, engine.provision_shared_vpc)* | `10.124.0.0/20` | shared |
| networking-vpc-subnet | `10.123.0.0/20` | vpc-crud |
| container-ske-cluster-nodepool | `10.125.0.0/20` | adopt (fallback) |
| networking-vpc-internet-gateway | `10.126.0.0/20` | vpc-crud |
| vpc-cidr-secondary | `10.127.0.0/20` (primary) + `10.200.0.0/20` (secondary, same VPC) | vpc-crud |
| vpc-privatelink-service | `10.128.0.0/20` | adopt (fallback) |
| vpc-endpoint | `10.129.0.0/20` | adopt (fallback) |
| vpc-peering | `10.130.0.0/20` (VPC-A) + `10.141.0.0/20` (VPC-B) | vpc-crud |
| vpc-transit-gateway-children | `10.131.0.0/20` (+ unique `/20` per child VPC) | vpc-crud |
| vpc-subnet-vip-nat | `10.132.0.0/20` | vpc-crud |
| database-postgresql-cluster | `10.133.0.0/20` | adopt (fallback) |
| heavy-shared-dbaas | `10.134.0.0/20` | adopt (fallback) |
| compute-virtualserver-full | `10.135.0.0/20` | adopt (fallback) |
| database-mysql-cluster | `10.136.0.0/20` | adopt (fallback) |
| networking-direct-connect-routing | `10.137.0.0/20` | adopt (fallback) |
| networking-loadbalancer-members-nat | `10.138.0.0/20` | adopt (fallback) |
| networking-vpn-gateway-tunnel | `10.139.0.0/20` | adopt (fallback) |
| heavy-shared-networking | `10.140.0.0/20` | vpc-crud (+ private-dns) |

> "adopt (fallback)" lifecycles ADOPT the shared VPC+subnet at runtime (they
> create no VPC); their own CIDR is used only in the degraded self-create
> fallback when no shared VPC is present. They are still given a unique block so
> even the fallback path never overlaps.

Each lifecycle's subnet(s) are the first `/24`(s) of its VPC `/20`
(e.g. `10.13X.0.0/24`). The shared subnet is `10.124.0.0/24`. ADOPT lifecycles
that pin a fixed host IP re-home it into the shared subnet (`10.124.0.x`,
distinct hosts — see `dependencies.json:vpc_schedule.fixed_ip_map`).

## Parallelism (shared-infra + adopt)

- Provision ONE shared VPC + ONE shared subnet once; ADOPT-class lifecycles
  adopt them and create only their own child resource, so they run in PARALLEL
  (pytest-xdist `-n`). They add no VPCs → no VPC-quota pressure.
- **DBaaS per-engine parallelism:** all DB engine lifecycles (mysql, postgresql,
  heavy-shared-dbaas, and the cachestore/epas/mariadb/sqlserver cluster-subops
  lifecycles) are in the parallel pass, so the DB clusters provision concurrently
  in the ONE shared subnet — this is where the wall-clock win comes from.
- VPC-CRUD lifecycles (self-create a VPC/subnet, peer, or need >1 VPC) run
  SERIALLY. The shared VPC is torn down BEFORE the VPC-CRUD pass so those
  lifecycles get the full 3-VPC budget (e.g. vpc-peering needs 2 at once).

## Cross-run rule

Never let two VPC-mutating runs overlap (they compete for the same 3 VPCs).
Trigger one CRUD run at a time; wait for the prior run's regression job to finish.
