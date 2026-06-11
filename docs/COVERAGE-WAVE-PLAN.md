# COVERAGE-WAVE-PLAN — the remaining static gap, prioritized

- Date: 2026-06-11 · Branch: `worktree-agent-ad779d783a70c6b94` · Status: **active**
- Inputs: `PYTHONPATH=. python3 -m spec.coverage_gap`, `knowledge/scenario-catalog.md`,
  `docs/HANDOFF-fail-new-triage.md`, `data/baselines/coverage_waivers.json`,
  run #6 27329026254 (full heavy) + smoke 27332251482 (ok 188 / soft 66 / fail_new 0).
- Baseline at session start: ceiling 85.57% (1174/1372) · gap_write 32 · gap_getid 166
  · live C3 43.27% (분모 1130).
- **After this session's edits: ceiling 86.3% (1184/1372) · gap_write 32 · gap_getid 156.**

## 1 · The 32 remaining GAP-write ops — all explained, none authorable

Every one of the 32 belongs to a **disabled** lifecycle family and **all 32 carry an
approved waiver** (2026-06-10 owner decisions). They are not "missing scenarios" —
the scenarios exist (`management__iam-identity-center.json`,
`storage__archivestorage.json`) and were deliberately switched off. Closing them is
an *owner decision*, not authoring work.

### management/iam-identity-center — 19 writes (+5 getid)

Waiver class **blast-radius** (SSO instance/users/groups/permission-sets/account-
assignments are account-structural on a shared account). Lifecycles `idc-*` exist,
fully coverage-only (every write optional + broad expect_status), `enabled:false`
since 2026-06-10.

| Endpoint key (method · path) | Lifecycle (disabled) |
|---|---|
| createinstance POST /v1/instances · setinstance PATCH /v1/instances/{id} · deleteinstance DELETE /v1/instances/{id} | idc-instance |
| createuser POST /v1/users · setuser PATCH /v1/users/{uuid} · deleteuser DELETE /v1/users/{uuid} · deletebulkusers DELETE /v1/users | idc-user |
| creategroup-family: setgroup PATCH /v1/groups/{id} · createbulkgroupusers POST /v1/groups/{id}/users · deletebulkgroupusers DELETE /v1/groups/{id}/users · deletebulkgroups DELETE /v1/groups | idc-group |
| createpermissionset POST /v1/permission-sets · setpermissionset PATCH · deletepermissionset DELETE · setpermissionsetpolicies PUT .../policies · deletepermissionsetpolicies DELETE .../policies | idc-permission-set |
| createaccountassignment POST /v1/account-assignments · deleteaccountassignment DELETE .../{id} · deletebulkaccountassignments DELETE /v1/account-assignments | idc-account-assignment |

### storage/archivestorage — 13 writes (+6 getid)

Waiver class **entitlement** ("separate auth keys" — the archive-storage data plane
is not reachable with the CI account's keys). Lifecycles `archivestorage-bucket` /
`archivestorage-archiving-policy` exist (heavy, docs-derived), `enabled:false`.

createbucket · deletebucket · setbucketversioning · setbucketencryption ·
recoverobjects · recoverobjectversions · deletebucketobjects ·
deletebucketobjectversions · createarchivingpolicy · setarchivingpolicy ·
setarchivingpolicystate · cancelarchiving · cancelrecovery.

### ⚠ Decision point for the owner — the C2 rule for waivers

`COVERAGE-CRITERIA.md` says a waived endpoint **must still be C2** (called; the 4xx
is the evidence the gate works). With both families *disabled*, these 32 are never
called at all — the waiver ledger and the criteria doc are currently inconsistent.
Two honest options:

1. Re-enable the idc-* lifecycles **as-is** (they are already coverage-only: every
   create uses doc-placeholder ids, expects 4xx, never builds real SSO structure)
   → satisfies the C2 rule with zero blast-radius change. Recommended.
2. Amend the criteria doc to add a `not-callable` waiver subclass for
   archivestorage (auth keys make even a 4xx call meaningless) and idc if the
   owner prefers silence.

Either way this is a 1-line `enabled` flip / doc edit, not new authoring.

## 2 · The 156 remaining GAP-getid — what they are and the next wave

These are id-bound GETs with **no static GET step**, most of which the engine
already exercises at runtime via `probe_reads`/read-chains (the static analyzer
only counts explicit steps). Two kinds of work close them:

### Wave A — cheap explicit-step conversions (offline-authorable, no new resources)

The pattern proven this session (servicewatch `get-group`, DBaaS `show-request`):
give a runtime-probed GET an explicit step with a `{placeholder}` token so it
counts statically AND records C2/C3 live. Closed this session (−10):
`/v1/requests/{request_id}` (shared path: mysql, postgresql, mariadb, epas,
cachestore, sqlserver, searchengine, vertica, eventstreams — one step closes all)
and servicewatch `/v1/log-groups/{log_group_id}`.

Top remaining candidates, biggest first (counts = GAP-getid per service):

| Service | getid | What's needed |
|---|---|---|
| compute/virtualserver 21 | explicit GETs for sub-resources of the heavy VM (nics, volumes-attach state, console, metrics) — add to `compute-virtualserver-full` window, same pattern as DBaaS | heavy window steps |
| application-service/apigateway 16 | the api/stage/method/deployment read-backs — apigateway lifecycles already capture all ids; add explicit GETs | light |
| networking/vpc 12 | peering/nat/endpoint/tgw show endpoints — ids exist in the vpc-extra lifecycles | light/heavy mix |
| compute/scf 11 | function sub-reads (versions/aliases/triggers shows) | light |
| management/iam 11 | role/policy/saml show+binding reads (note: createrole 500s — BODY-FIX backlog) | light |
| networking/loadbalancer 8 · storage/backup 8 | child shows under existing heavy/guarded lifecycles | heavy window |
| ai-ml/aimlops-platform 6 · cloud-ml 4 | guarded reads | light |
| management/resourcemanager 6 · cloudmonitoring 5 · dns 4 | misc shows; cloudmonitoring is C2-waived | light |
| data-flow/data-ops/quick-query/devops 2 each + singletons | `check-duplication`-style GETs (blocked on the **query-string HMAC 401**, see triage A.5–6 — fix in core/auth.py first) | harness fix |

Estimated Wave A yield: ~60–80 getid closable with explicit steps alone; the rest
need the same live windows as their parent writes.

### Wave B — live-window levers (needs scheduled runs, prep done where possible)

| Lever | Status after this session |
|---|---|
| ① DBaaS 서브옵 윈도우 (~139 endpoint family) | **PREPPED**: `mysql-subop-window`/`mysql-restart` + `pg-subop-window` groups added inside the existing heavy lifecycles (read-only GETs + no-body sync-state + restart only; upgrades/promotes/restores excluded). Validate via a heavy run scoped `crud_filter="database-mysql-cluster or database-postgresql-cluster"`. Extending the same window pattern to mariadb/epas/cachestore = piggyback on `heavy-shared-dbaas` wait phase (next session; needs scheduling so guarded sub-op lifecycles run DURING the shared-cluster window, not after teardown). Also retry the 401 backup family inside the window (triage B + A.1–4). |
| ② SCR 이미지 push (19 endpoints) | **BLOCKED on console + 500-fix**: needs IAM 인증키 인증 설정 (console, owner action) and the `updatepublicendpointenabled` PUT 500 resolved (try `{enabled:true}` body in the next mutation run), then a skopeo step in CI. No offline work possible beyond what triage already records. |
| ③ servicewatch 메트릭 POST 바디 | **DONE (docs-derived)**: listmetricdata/listmetricinfos now send the real catalog namespace/metric ('Virtual Server', 'CPU Usage/Core[Basic]'); explicit showloggroup step added. Expect both metric POSTs to flip 400→200 (C3) in the next light run. |
| ④ eventstreams topology | **PARTIAL**: all guarded sub-op bodies re-derived from the correct api_docs models (add-instances/maintenance/security-group-rules/parameters were all using wrong models); sync-state/parameters-sync/unset-maintenance added; read-coverage literal-uuid bug fixed (closed the shared /v1/requests/* static gap). The CREATE topology itself remains a DOMAIN-HUNT: valid Kafka role_type combinations are undocumented (facts recorded in `knowledge/formal/services/data-analytics__eventstreams.yaml`); needs console inspection of a manually-created cluster or vendor docs. |

## 3 · Scoped verification run for this session's changes

```
crud_filter = "servicewatch-loggroup-logstream or eventstreams-cluster-subops-guarded or eventstreams-read-coverage or database-mysql-cluster or database-postgresql-cluster"
```

(servicewatch + eventstreams-read parts run light; the two DBaaS lifecycles and the
eventstreams guarded lifecycle need `heavy=true`. The DBaaS pair is the expensive
half — drop them for a light-only smoke of levers ③/④.)

Expected if green: servicewatch +2 C3 writes (metric POSTs) +1 C3 getid
(showloggroup); eventstreams sub-ops flip from wrong-model 400s to correct-model
calls (C2 guaranteed, C3 only when a live cluster exists); mysql/pg window adds
~13 C3 reads + 2 writes (sync-state, mysql restart) recorded under their own
services; static C1 already +10 (86.3%).
