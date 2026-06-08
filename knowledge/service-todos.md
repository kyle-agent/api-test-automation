# Service TODOs — the durable cross-session backlog

This file is the **per-service, pick-up-and-go backlog** for advancing AXIS 1
coverage toward 100%. It complements the *triage table* in
[`scenario-catalog.md`](scenario-catalog.md): the catalog table is the snapshot of
the uncovered surface by endpoint count; this file is the **checklist any future
session/agent works against**. The two must not contradict — the scenario ideas
here are copied from the catalog triage.

## How agents use this file

1. **Claim a service** — pick a `- [ ]` item (prefer the cheapest open one; see
   "Recommended order" below). Announce it so concurrent sessions don't collide.
2. **Add its lifecycle** — encode it as a declarative lifecycle in
   `regression/scenarios/scenarios.json` (no new Python — the engine drives it),
   declare any `quota_kinds`/`prerequisites` in `dependencies.json`, and put the
   *why* in the lifecycle `_note`. Model `*-reads` lifecycles on
   `platform-product-reads` / `quota-reads` / `support-reads`.
3. **Record validated facts** — every runtime-confirmed truth (capture path,
   state machine, delete race) goes into
   [`validated-facts.md`](validated-facts.md) **and** the scenario `_note`, kept
   in sync, committed.
4. **Check it off** — flip `- [ ]` → `- [x]`, add the lifecycle id, and update the
   triage table + `agents/CONTEXT.md` "Current state" in the same commit.

> **Safety gates are non-negotiable** (see `agents/CONTEXT.md`): `GET` always
> runs; `POST/PUT/PATCH` need `SCP_ALLOW_MUTATIONS=true`; `DELETE` needs
> `SCP_ALLOW_DESTRUCTIVE=true`; **heavy/billable** lifecycles (cluster/VM/DB) are
> skipped unless `SCP_RUN_HEAVY=true`. Read-class lifecycles touch only GETs.

### Re-derive the uncovered set (keep this backlog honest)

This is the same snippet used to build the triage table; it lists every service
with **no lifecycle** in `scenarios.json`, sorted by endpoint count, with its
direct-GET (smoke-floor) count and category:

```bash
python3 -c "import json,collections;cat=json.load(open('data/api_catalog.json'));sc=json.load(open('regression/scenarios/scenarios.json'))['lifecycles'];cov={l['service'].split('/',1)[1] for l in sc if '/' in l.get('service','')};bs=collections.defaultdict(collections.Counter);cc={};dg=collections.Counter();[ (bs[e['service']].update([e['method']]), cc.__setitem__(e['service'],e['category']), dg.update([e['service']] if e['method']=='GET' and '{' not in e['http_path'] else [])) for e in cat];rows=sorted(((sum(c.values()),dg[s],cc[s],s) for s,c in bs.items() if s not in cov),reverse=True);[print(f'{t:4d} dG={d:3d} {cat:18s} {s}') for t,d,cat,s in rows]"
```

When a service drops out of the snippet output, its lifecycle exists — move its
item to "Done" below.

---

## READ-class TODOs (zero-cost — pure GET surface, write a `*-reads` lifecycle)

Cheapest wins. Each adds a read-only lifecycle modeled on `platform-product-reads`
and unlocks both direct and id-bound GETs with **no mutation risk and no billing**.

> **In progress / may already be done — verify against `scenarios.json` first.**
> `pricing`, `costexplorer`, `billingplan`, and `product` may be getting `*-reads`
> lifecycles in a parallel change. Before claiming any of these, re-run the
> snippet above; if the service no longer appears, it's done — move it to "Done".

- [ ] financial-management/`pricing` (3 endpoints, 3 direct GETs) — read — read-only: list offerings/prices/billing-item-ids (all direct GET). *(in progress / may be done — verify against scenarios.json)*
- [ ] financial-management/`costexplorer` (3 endpoints, 3 direct GETs) — read — read-only: list bills/usages + show monthly payment. *(in progress / may be done — verify against scenarios.json)*
- [ ] financial-management/`billingplan` (10 endpoints, 6 direct GETs) — read — read-only: list planned-computes/server-types/... + probe showplannedcompute. *(in progress / may be done — verify against scenarios.json)*
- [ ] management/`cloudmonitoring` (18 endpoints, 8 direct GETs) — read — read-only: list metrics/event-policies + probe id-bound GETs.
- [ ] management/`organization` (37 endpoints, 9 direct GETs) — read — read-only: list orgs/accounts/OUs + probe id-bound GETs (mutations risky — defer the write side).

## READ→LIGHT-class TODOs (start read-only; add CRUD later)

- [ ] storage/`backup` (31 endpoints, 9 direct GETs) — read→light — read-only first: list backup policies/vaults + probe; later create policy.
- [ ] management/`loggingaudit` (10 endpoints, 2 direct GETs) — read→light — read-only: list audit logs/trails.
- [ ] security/`configinspection` (7 endpoints, 3 direct GETs) — read→light — read-only: list inspection results + probe.
- [ ] financial-management/`budget` (5 endpoints, 1 direct GET) — read→light — read-only: list account budgets + probe showaccountbudget.
- [ ] management/`network-logging` (4 endpoints, 2 direct GETs) — read→light — read-only: list logs.

## LIGHT-class TODOs (creatable without a billable cluster/VM/DB)

CRUD lifecycles that create real resources but no billable compute. Gate writes
behind `SCP_ALLOW_MUTATIONS` / `SCP_ALLOW_DESTRUCTIVE` (the engine does this).

- [ ] storage/`baremetal-blockstorage` (41 endpoints, 2 direct GETs) — light — volume-group + volume create→show→delete. *(NOTE: flagged red in conformance — 5xx-on-bad-input; capture that as a validated fact.)*
- [ ] networking/`loadbalancer` (34 endpoints, 5 direct GETs) — light — needs a vpc+subnet: create LB → listener → show → delete.
- [ ] management/`iam-identity-center` (32 endpoints, 5 direct GETs) — light — create group/permission-set → show → delete. *(red in conformance.)*
- [ ] storage/`archivestorage` (25 endpoints, 6 direct GETs) — light — bucket/vault create→show→delete.
- [ ] data-analytics/`data-ops` (17 endpoints, 3 direct GETs) — light — read-only first (list image-versions etc.).
- [ ] data-analytics/`data-flow` (17 endpoints, 3 direct GETs) — light — read-only first (list flows).
- [ ] management/`cloudcontrol` (15 endpoints, 4 direct GETs) — light — resource-control create→show→delete.
- [ ] data-analytics/`quick-query` (12 endpoints, 2 direct GETs) — light — query create→run→show→delete.
- [ ] storage/`parallel-filestorage` (11 endpoints, 2 direct GETs) — light — parallel volume create→show→delete (mirror filestorage).
- [ ] networking/`vpn` (10 endpoints, 2 direct GETs) — light — vpn-gateway/tunnel (needs vpc).
- [ ] networking/`gslb` (10 endpoints, 2 direct GETs) — light — gslb create→show→delete.
- [ ] networking/`cdn` (9 endpoints, 1 direct GET) — light — cdn distribution create→show→delete.
- [ ] networking/`firewall` (8 endpoints, 2 direct GETs) — light — firewall rule (needs vpc).
- [ ] networking/`direct-connect` (8 endpoints, 1 direct GET) — light — direct-connect connection create→show→delete.
- [ ] devops-tools/`devopsservice` (6 endpoints, 2 direct GETs) — light — devops project create→show→delete.
- [ ] security/`secretvault` (5 endpoints, 1 direct GET) — light — vault create→show→delete (mirror secretsmanager).
- [ ] platform/`sts` (3 endpoints, 0 direct GETs) — light — token mint (POST-only; no GET to read back, so coverage is the POST itself).

## HEAVY-class TODOs (billable cluster/DB/VM — gate behind `SCP_RUN_HEAVY=true`)

Do **not** run these in routine sessions. Each provisions a real billable
cluster/server; only attempt in an explicit heavy session with `SCP_RUN_HEAVY=true`
(and the usual mutation/destructive gates). Several share `POST /v1/clusters`, so
watch for the path-collision false-✓ noted in the dashboard handoff.

- [ ] database/`epas` (47 endpoints, 5 direct GETs) — heavy — epas create→show→delete cluster (mirror mysql/postgresql heavy).
- [ ] database/`mariadb` (46 endpoints, 5 direct GETs) — heavy — mariadb create→show→delete cluster (mirror mysql).
- [ ] database/`sqlserver` (38 endpoints, 5 direct GETs) — heavy — sqlserver create→show→delete cluster.
- [ ] database/`cachestore` (32 endpoints, 5 direct GETs) — heavy — cachestore create→show→delete cluster.
- [ ] data-analytics/`searchengine` (26 endpoints, 3 direct GETs) — heavy — searchengine create→show→delete cluster.
- [ ] data-analytics/`eventstreams` (24 endpoints, 5 direct GETs) — heavy — eventstreams create→show→delete cluster.
- [ ] data-analytics/`vertica` (23 endpoints, 3 direct GETs) — heavy — vertica create→show→delete cluster.
- [ ] compute/`multinodegpucluster` (16 endpoints, 5 direct GETs) — heavy — GPU cluster, billable, defer.
- [ ] compute/`baremetal` (16 endpoints, 3 direct GETs) — heavy — bare-metal server, billable, defer.
- [ ] ai-ml/`aimlops-platform` (12 endpoints, 3 direct GETs) — heavy — platform create→show→delete (cluster-backed).
- [ ] ai-ml/`cloud-ml` (9 endpoints, 2 direct GETs) — heavy — cloud-ml cluster, billable, defer.

## Recommended order (cheapest coverage first)

1. **read** class — `pricing`, `costexplorer`, `billingplan`, `cloudmonitoring`,
   `organization` (read-only slice). Zero cost, zero mutation risk.
2. **read→light** read-only slices — `backup`, `loggingaudit`, `configinspection`,
   `budget`, `network-logging`.
3. small **light** CRUD — `secretvault`, `archivestorage`, `cloudcontrol`, `gslb`,
   `cdn`, `direct-connect`, then the vpc-dependent ones (`loadbalancer`, `vpn`,
   `firewall`).
4. **heavy** DB/cluster services — only in a dedicated `SCP_RUN_HEAVY` session.

## Done (lifecycle exists — kept for the record)

- [x] platform/`product` — covered by `platform-product-reads`.

When you complete an item above, move it here with its lifecycle id and remove it
from the triage table in `scenario-catalog.md`.

---

## Confirmed facts to keep documenting

The durable home for runtime-discovered facts is
[`validated-facts.md`](validated-facts.md) — capture paths, async state machines,
delete races, undocumented fields, conformance quirks (e.g. the
`baremetal-blockstorage` / `iam-identity-center` reds above).

**Rule (from `START_HERE.md` golden rules):** a fact discovered at runtime belongs
in `validated-facts.md` **and** the scenario `_note`, kept in sync and **committed**
in the same change as the scenario — so the next session starts ahead. Mark
provenance: **VALIDATED** (confirmed by a real 2xx) vs **from docs** (best-effort).

---

## Cross-cutting TODOs (non-service engineering follow-ups)

From [`docs/HANDOFF-dashboard-write-coverage.md`](../docs/HANDOFF-dashboard-write-coverage.md)
— surfaced here so they're visible in one backlog. (Note: `conformance/runtime.py`
may be under concurrent edit; coordinate before touching it.)

- [ ] **Conformance runtime probes don't record Observations.** `conformance/runtime.py`
      (`probe_status`, `probe_validation`, `probe_l10n`) calls real POST/PUT endpoints
      with a status + response time but records only `Finding`s, so those endpoints
      get no status/time on the dashboard. Add an `_observe(...)` that records an
      `Observation` (source e.g. `runtime_probe`) per real call, mirroring
      `regression/smoke.py::_record`.
- [ ] **Response-code donut double-counts writes.** The engine records each write
      step twice (synthetic `lifecycle:step` key + catalog key); `compute()`'s
      `dist`/pass-rate count both. De-dupe by source or key for the distribution.
- [ ] **CI artifact merge.** Both `regression-reports` and `conformance` artifacts
      ship `reports/results` and the dashboard job downloads both; if conformance
      ever emits observations, the second download could overwrite the first. Make
      the dashboard merge multiple obs/findings files instead of last-writer-wins.
