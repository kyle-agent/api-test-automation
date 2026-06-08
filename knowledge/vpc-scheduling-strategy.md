# VPC scheduling strategy — never let runs collide on the 5-VPC cap

> **Why this file exists.** The account caps VPCs at **5**
> (`scp-network.vpc.exceed-max-count`) and private-dns at **3**
> (`scp-network.private-dns.max-count-exceed`). Eight CRUD lifecycles each stand
> up their own VPC. Without a plan they trip the cap — both *within* one run and
> *across* concurrent runs — which silently **skips** later VPC lifecycles
> (notably `heavy-shared-networking`, which has therefore never been validated).
> This file is the durable strategy to **divide the 5 VPCs so runs don't
> overlap**, and a playbook to execute it on the next run.
>
> Source data lives in `regression/scenarios/dependencies.json`
> (`quota_kinds`, `budget_paths`, and the new `vpc_schedule` block). This doc is
> the human-readable rationale + playbook; the JSON is the machine-readable
> schedule.

## The constraint & the eight VPC consumers

`core/budgets.py` → `DEFAULT_LIMITS = {vpc: 5, private-dns: 3}`. Each enabled
lifecycle below creates **exactly one** VPC inline (and tears it down at the
end); `heavy-shared-networking` additionally consumes one `private-dns`:

| Lifecycle | Class | Consumes | Order in scenarios.json |
|-----------|-------|----------|--------------------------|
| `networking-vpc-subnet` | light | vpc | 3 |
| `networking-vpc-internet-gateway` | light | vpc | 13 |
| `container-ske-cluster-nodepool` | heavy | vpc | 14 |
| `compute-virtualserver-full` | heavy | vpc | 15 |
| `database-mysql-cluster` | heavy | vpc | 16 |
| `database-postgresql-cluster` | heavy | vpc | 17 |
| `heavy-shared-dbaas` | heavy | vpc | 18 |
| `heavy-shared-networking` | heavy | **vpc + private-dns** | 24 |

(Source of truth: `dependencies.json:quota_kinds`. Keep this table in sync.)

## Why collisions happen today (root causes)

1. **The budget is never reconciled with reality.** `tests/crud/test_crud_lifecycle.py`
   calls `engine.run_lifecycle(...)` **once per parametrized test with no shared
   `Budget`**, so each lifecycle gets a fresh `Budget(used={})`. `run_all()` does
   share one Budget, but pytest doesn't use `run_all()`. Worse, **nobody ever
   calls `Budget.sync('vpc', live_count)`** from a real VPC list call. Net effect:
   `Budget.available('vpc')` is *always* 5, so the reserve/skip gate only ever
   guards the single VPC the current lifecycle is about to make — it provides
   **zero** protection against (a) sibling lifecycles' VPCs still alive, (b) VPCs
   whose async delete is still draining, or (c) other concurrent runs.
2. **Async VPC deletes linger.** A lifecycle's teardown issues `DELETE /v1/vpcs`
   and moves on; the VPC keeps occupying a slot for a while. The next VPC-creating
   lifecycle starts before the slot frees → live count climbs past 5.
3. **Ordering starves `heavy-shared-networking`.** It is the **last** VPC consumer
   (pos 24), running after five heavy VPC lifecycles. By then the lingering deletes
   have saturated the cap, so its `create-vpc` returns `exceed-max-count` and the
   engine **environmentally skips** it (status=`skipped`, not a CI failure — so the
   gap is silent).
4. **No cross-run mutual exclusion.** `concurrency.group` in
   `.github/workflows/api-test.yml` is keyed on `${{ github.ref }}-${{ github.run_id }}`
   with `cancel-in-progress: false` → **every run is its own group**, so a PR run,
   a scheduled run, and a dispatch run can all hold VPCs simultaneously.

A non-2xx `create-vpc` is *not* a regression — the engine classifies quota caps
as environmental skips (engine.py ~L522-530). So collisions cost **coverage**,
not red CI. That is exactly why they went unnoticed.

## The strategy — partition the 5 VPCs into non-overlapping lanes

Two independent rules. Apply both.

### Rule A — intra-run: serialize VPC lifecycles in small lanes, ≤ `per_run_vpc_cap`

Keep each run's *concurrent live VPC count* at or below **3** (headroom of 2 for
lingering deletes and any stray account VPC). Because pytest runs the suite
sequentially, the practical lever is **which lifecycles a run includes** (via
`crud_filter`) and **their order**. Recommended lanes (`dependencies.json:vpc_schedule.lanes`):

| Lane | `crud_filter` (-k) | VPC lifecycles | Peak VPCs |
|------|--------------------|----------------|-----------|
| **L0 light** | *(blank, `run_heavy=false`)* | vpc-subnet, vpc-internet-gateway (sequential) | 1–2 |
| **L1 compute** | `container-ske-cluster-nodepool or compute-virtualserver-full` | ske, virtualserver-full | 1–2 |
| **L2 database** | `database-mysql-cluster or database-postgresql-cluster or heavy-shared-dbaas` | mysql, postgresql, dbaas | 1–2 |
| **L3 networking** | `heavy-shared-networking` | heavy-shared-networking (alone → guaranteed vpc + private-dns) | 1 |

Run the lanes **as separate dispatches that do not overlap** (see Rule B). Each
lane creates ≤1 VPC at a time and tears it down before the next, so even with
delete-linger a lane peaks well under 5. `heavy-shared-networking` running
**alone** is the fix for its chronic starvation.

> `crud_filter` is fed to `pytest -k`. Multi-lifecycle filters need the value
> quoted in the workflow (`-k "$FILTER"`) — that quoting fix is included in this
> change (workflow ~L311). A blank filter (full run) is unaffected.

### Rule B — cross-run: never let two VPC-mutating runs overlap

Pick one (in order of robustness):

1. **Single concurrency group for mutating runs** *(recommended, not yet applied
   to avoid disturbing an in-flight baseline)*: change `concurrency.group` to a
   constant for runs that mutate (e.g. `scp-api-test-mutating`) with
   `cancel-in-progress: false`, so mutating runs **queue** instead of overlapping.
   Trade-off: serializes the two daily cron slots + PR runs; acceptable because
   they share one account.
2. **Manual spacing**: only ever trigger one VPC-consuming run at a time; wait for
   the previous run's **regression job to complete** (that's the "VPCs no longer
   being created" signal — the run stays `in_progress` through
   conformance/dashboard long after its VPCs are swept) before starting the next.
3. **Per-run VPC lane budget**: cap each run to ≤2 of the 5 VPCs and only allow two
   such runs at once. Needs the live-sync engine work below to be enforceable.

## IMPLEMENTED: shared-VPC adoption for heavy lifecycles

> Status: built (offline-tested), **pending live validation on the next heavy
> run**. See `tests/crud/test_shared_vpc_adopt.py`.

The six **heavy** VPC lifecycles no longer each create their own VPC. A
session-scoped pytest fixture (`shared_vpc` in `conftest.py`) provisions **one**
VPC (`10.124.0.0/20`, owner/run/ttl-tagged) via `engine.provision_shared_vpc`,
and each heavy lifecycle's `create-vpc`/`delete-vpc` steps carry
`{"adopt": "vpc"}`: the engine seeds `vpc_id` from the shared VPC and **skips
both the create and the delete** (the fixture tears the VPC down once at session
end; the tag-scoped sweep is the backstop). Net: **6 heavy VPC creates → 1.**

- **Scope decision:** heavy-only. The two *light* networking lifecycles
  (`networking-vpc-subnet`, `networking-vpc-internet-gateway`) keep self-creating
  so the VPC create/delete endpoints stay exercised for coverage.
- **Share VPC only (not the subnet):** each heavy lifecycle still creates its
  *own* subnet **under** the shared VPC, re-homed into the shared `/20`
  (`10.124.1.0/24` … `10.124.6.0/24`), preserving each service's known-good
  subnet config. (`heavy-shared-networking`'s in-subnet host IPs were moved to
  `10.124.6.100/200` to match.) This avoids the risk of one shared subnet not
  satisfying every service.
- **Safety / no-regression:** adoption is a **no-op when no shared VPC exists**
  (mutations off, or provisioning failed) — the lifecycle then self-creates
  exactly as before. So the fixture failing can't redden heavy CRUD.
- **Effect on the cap:** heavy runs now hold **1** shared VPC + at most the light
  lifecycles' own, instead of up to 6 — `heavy-shared-networking` no longer gets
  starved. The lane sharding below is still useful for *cross-run* isolation but
  is no longer required to fit the heavy suite intra-run.

## Other durable fixes (still recommended)

The lane sharding above is executable **today** with zero code change. To make a
single, un-sharded `run_heavy=true` run safe (no manual sharding), also close the
remaining accounting gap:

1. **Share one `Budget` for the whole pytest session and seed it from reality.**
   In a `tests/crud` fixture (module/session scope), build one `Budget`, call
   `budget.sync('vpc', <live count from GET /v1/vpcs>)` and
   `budget.sync('private-dns', <live count>)`, and pass it into every
   `run_lifecycle(...)`. Then `reserve`/`available` reflect the true account state
   and the existing environmental-skip gate (engine.py ~L470-491) actually works.
2. **Drain-wait after a VPC teardown.** After releasing a `vpc` budget slot, poll
   `GET /v1/vpcs` until the deleted VPC is gone (or a timeout) before the next
   VPC-creating lifecycle proceeds — removes the async-linger overshoot.
3. **Reorder so `heavy-shared-networking` is not last.** Move it ahead of the
   VPC-hungry db/compute lifecycles in `scenarios.json` (or rely on (1)+(2) so
   order stops mattering). *Deliberately not reordered here* so this change does
   not alter the pending baseline run.
4. **Best long-term: adopt a shared VPC.** Rework the heavy lifecycles to *reuse*
   one VPC+subnet created once per run instead of each calling `create-vpc`
   (handoff rec 2a). Collapses 6 VPC creates → 1 and makes the cap a non-issue.

## Next-run playbook (copy-paste)

Run these as **separate, non-overlapping** `workflow_dispatch` runs of
`api-test.yml` on the working branch. All set `allow_mutations=true`,
`allow_destructive=true`. Wait for each run's **regression job** to finish before
starting the next.

```
# Lane L0 — all light lifecycles (no heavy, minimal VPC pressure)
run_heavy=false   crud_filter=(blank)

# Lane L1 — heavy compute
run_heavy=true    crud_filter=container-ske-cluster-nodepool or compute-virtualserver-full

# Lane L2 — heavy database
run_heavy=true    crud_filter=database-mysql-cluster or database-postgresql-cluster or heavy-shared-dbaas

# Lane L3 — heavy shared networking (runs alone → its vpc + private-dns are free)
run_heavy=true    crud_filter=heavy-shared-networking
```

Conformance/dashboard rebuild on each run; the tag-scoped sweep reclaims each
run's own VPCs. Because the lanes don't overlap and each peaks ≤2 VPCs, the
5-VPC cap is never tripped and `heavy-shared-networking` finally gets exercised.

> Once the durable fix (engine live-sync + drain-wait, or shared-VPC adoption) is
> implemented and validated, a single full `run_heavy=true` run becomes safe and
> the lane sharding is no longer required — update this file when that lands.
