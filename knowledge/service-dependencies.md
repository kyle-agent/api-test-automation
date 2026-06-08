# Service dependencies & call order

What must exist before what. Encoded as data in
`regression/scenarios/dependencies.json` (`prerequisites`) and realized step-by-step
in `scenarios.json`. This file is the human-readable view.

> Note on "prerequisites": in the scenario engine these are created **inline** by
> the lifecycle's own steps (not external preconditions). The graph below tells
> you the order; the scenario builds the whole chain and tears it down in reverse.

## Dependency graph (create order →)

```
vpc ──► subnet ──► port
  │        │
  │        ├──► (security-group is account/region-scoped — NO vpc needed)
  │        │
  │        └──► server (virtualserver):
  │               needs vpc + subnet + security-group + keypair + image + server-type
  │
  ├──► internet-gateway (attached to vpc)
  ├──► public-ip (type IGW; standalone)
  └──► ske cluster:
         needs vpc + subnet + security-group + keypair
              + kubernetes-version (lookup) + server-type (lookup)
              + filestorage volume
         └──► nodepool (needs cluster)

filestorage volume      — standalone (no vpc)
virtualserver keypair   — standalone (no vpc), zero-cost
virtualserver volume    — standalone (no vpc) ──► snapshot (needs volume)
scr registry            — standalone ──► repository (needs registry)
certificatemanager      — standalone (self-sign)
queueservice queue      — standalone
resourcemanager rg      — standalone (global service)
mysql cluster           — needs vpc + subnet
```

## Canonical orders (from validated scenarios)

**Networking (vpc + subnet + port)** — `networking-vpc-subnet`:
1. create vpc (`cidr` e.g. `10.123.0.0/20`) → capture `$.vpc.id` → poll `$.vpc.state == ACTIVE`
2. create subnet (`type: GENERAL`, `vpc_id`) → `$.subnet.id` → poll `$.subnet.state == ACTIVE`
3. create port (`subnet_id`, `security_groups: []`) → `$.port.id`
4. teardown reverse: **port → subnet → vpc**, each with `409` retry (dependency
   still releasing). Wait for subnet 404 before deleting vpc.

**Virtual server (full VM)** — `compute-virtualserver-full` (heavy):
vpc → subnet → security-group → keypair → find-image → find-server-type →
create-server → (attach extra volume, rename, stop/start, image-create) →
teardown reverse. See `validated-facts.md` for the field-level gotchas.

**Kubernetes (ske cluster + nodepool)** — `container-ske-cluster-nodepool` (heavy):
vpc → subnet → security-group → keypair → find k8s-version → find server-type →
filestorage volume → create cluster → create nodepool → (scale, label) →
teardown reverse (nodepool → cluster → volume → keypair → sg → subnet → vpc).

**Container registry (scr)** — `container-scr-registry`:
create registry → poll `$.state == Running` → create repository (`registry_id`) →
teardown: repository → registry (registry DELETE 500-races for minutes — retry).

## Async polling

Every create that returns before the resource is usable is followed by a `poll`
on a state field (`poll: {field, until, timeout, interval}`) or a status code
(`until_status`). Ready values vary by service (`ACTIVE`, `Running`, `available`,
`in-use`, …) — collected in `validated-facts.md`. Deletes poll `until_status: [404]`.

## Teardown rule

Always reverse-order, registry-owned. Deletes that race dependency release retry
on `409`/`500`. A multi-group scenario (`group` + `optional`) isolates a failing
family: it tears down just that group and keeps the rest, so one bad body costs
one family, not the whole run.
