---
status: superseded (2026-06-17 측정 스냅샷 — 수치 stale; 재생성: python -m tools.catalog_status)
for: coverage
superseded_by: ../plans/CAMPAIGN-C3-100.md
---

> **⚠️ SUPERSEDED (2026-07-04, 일자 스냅샷).** 아래 수치는 2026-06-17 측정분으로
> **stale**하다 (감사 C11 — 예: VALIDATED 131/275는 현재 165 VALIDATED, CONTEXT
> 07-04). 최신 롤업은 `python -m tools.catalog_status`로 **재생성**하고, 현행
> 커버리지 작업 정본은 [`../plans/CAMPAIGN-C3-100.md`](../plans/CAMPAIGN-C3-100.md)다.

# CATALOG-VALIDATION-STATUS.md — 검증 되었는지 보고 (verification track)

> **Scope:** measured, reproducible status of the catalog (resource-task model)
> against the goal **"test ONLY via catalog composition."** Two axes:
> (1) is the catalog **secured + validated** for all services, and
> (2) is the **hand-written → composed migration** complete?
> All numbers below were produced by the exact commands shown; re-run
> `python -m tools.catalog_status` to regenerate the rollup.
> Measured 2026-06-17 on branch `claude/zealous-heisenberg-irf3xt`.

## Verdict (one line)

**The catalog is structurally secured (275 tasks, 0 schema errors) and ~48%
runtime-validated (131/275 nodes, 15/59 services fully VALIDATED); the
composition engine works end-to-end, but composition-only is NOT yet reached —
144 docs nodes are unproven and 103 of 164 lifecycles are still hand-written.**

The blocker to "composition-only" is **not** the engine (it works); it is the
**144 docs-provenance nodes** (27 services with zero validated nodes), almost
all gated on **live heavy/billable windows or owner action**, not on missing code.

## Secured vs Validated (summary)

| Dimension | What it means | Status | Source |
|---|---|---|---|
| **Secured (structural)** | every service has a model file; schema/cross-constraints pass | ✅ **0 errors** (84 warnings) | `python knowledge/formal/validate.py` → "R1 275 resource task(s) in 59 file(s) … 0 error(s)" |
| **Validated (runtime)** | node body/capture is runtime-proven (`provenance: VALIDATED`) | ⚠️ **131 / 275 nodes (≈48%)** | `python -m tools.catalog_status` |
| **Composable** | `composer.compose(...)` emits a runnable multi-step lifecycle | ✅ works | `compose(['vpc','subnet'])` → 9 steps |
| **Composition adoption** | lifecycles built from the model vs hand-written | ⚠️ **61 / 164 composed (≈37%)** | `python -m tools.catalog_status` (lifecycles) |

**Provenance totals (275 nodes):** `VALIDATED 131 · docs 144`.
Reproduce: `python -m tools.catalog_status` → `provenance={'docs': 144, 'VALIDATED': 131}`.

## Per-service 3-way split (59 services)

Rollup key: a service is **full** if every node is VALIDATED, **zero** if no node
is VALIDATED, else **partial**. Reproduce: `python -m tools.catalog_status`.

| Bucket | Count | Services |
|---|---|---|
| **full** (all nodes VALIDATED) | **15** | application-service/queueservice, container/ske, database/cachestore, financial-management/costexplorer, financial-management/pricing, management/network-logging, management/quota, management/resourcemanager, management/support, networking/dns, networking/firewall, networking/security-group, platform/product, security/kms, security/secretsmanager |
| **partial** (some VALIDATED) | **17** | application-service/apigateway, compute/multinodegpucluster, compute/scf, compute/virtualserver, container/scr, data-analytics/quick-query, database/epas, database/mariadb, database/mysql, database/postgresql, database/sqlserver, management/iam, management/servicewatch, networking/loadbalancer, networking/vpc, security/certificatemanager, storage/filestorage |
| **zero** (no VALIDATED node) | **27** | see classified list below |

### Zero-validated services (27) + likely blocker

Blocker is a **best-effort heuristic** mined from each yaml's
`notes` / `_disabled_reason` / inline comments by `tools/catalog_status.py`
(`BLOCKER_RULES`). It is a starting hint, not a verdict — spot-confirm before
acting (e.g. `management/organization` keyword-matches *heavy* but is really
**owner-action**: master-account only, 403 elsewhere, IRREVERSIBLE).

Distribution: **heavy-billable 17 · unproven-body 4 · license-gated 3 · console-only-id 3.**

| Service | nodes | Blocker (heuristic) |
|---|---|---|
| ai-ml/aimlops-platform | 1 | heavy-billable |
| ai-ml/cloud-ml | 2 | heavy-billable |
| compute/baremetal | 1 | heavy-billable |
| data-analytics/data-flow | 2 | heavy-billable |
| data-analytics/data-ops | 2 | heavy-billable |
| data-analytics/eventstreams | 1 | heavy-billable |
| data-analytics/searchengine | 1 | license-gated (Elasticsearch BYOL) |
| data-analytics/vertica | 1 | license-gated |
| devops-tools/devopsservice | 1 | heavy-billable |
| financial-management/billingplan | 1 | heavy-billable |
| financial-management/budget | 1 | heavy-billable |
| management/cloudcontrol | 1 | heavy-billable |
| management/cloudmonitoring | 4 | license-gated |
| management/iam-identity-center | 5 | heavy-billable / owner (SSO setup) |
| management/loggingaudit | 1 | unproven-body |
| management/organization | 6 | heavy-billable → **owner-action** (master acct) |
| networking/cdn | 1 | unproven-body |
| networking/direct-connect | 1 | unproven-body |
| networking/gslb | 1 | heavy-billable |
| networking/vpn | 2 | heavy-billable |
| platform/sts | 1 | console-only-id (assume-role SRN) |
| security/configinspection | 1 | console-only-id |
| security/secretvault | 1 | console-only-id |
| storage/archivestorage | 2 | unproven-body (no dedicated auth key) |
| storage/backup | 6 | heavy-billable |
| storage/baremetal-blockstorage | 4 | heavy-billable |
| storage/parallel-filestorage | 2 | heavy-billable |

## Hand-written vs composed lifecycle gap

`regression/scenarios/lifecycles/*.json` (70 files, 164 ids):
**composed 61 (`gen-*`/`bundle-*`) · hand-written 103.**
Reproduce: `python -m tools.catalog_status` → `composed=61 handwritten=103 total=164`.

Full-suite validation (lifecycles dir + `scenarios.json`'s 29) passes:
`python -m regression.scenarios.validate` → **"193 lifecycle(s) checked · 0 error(s) · 7 warning(s)"**
(164 + 29 = 193; 18 disabled lifecycles tagged: blocked-engine 3 / blocked-owner 4 / done-modeled 2 / stale 4 / timing-gated 5).

## Composition works end-to-end (confirmed)

- **Engine:** `from regression.scenarios.composer import compose; compose(['vpc','subnet'])`
  → `id=bundle-subnet-vpc`, `service=networking/vpc`, **9 steps**
  (`create-vpc → wait-vpc → verify-vpc-add-cidr → create-subnet → wait-subnet →
  verify-subnet-read-vips → verify-subnet-read-sap-secondary-subnets →
  delete-subnet → delete-vpc`) — a runnable, dependency-ordered lifecycle with teardown.
- **Selector layer:** `regression/scenarios/targets.py` —
  `expand_targets` / `compose_service` (`gen-svc-*`) / `compose_group` (`gen-grp-*`) /
  `compose_theme` (`gen-theme-*`).
- **Dispatch:** composed lifecycles run via `crud_filter=gen-*`
  (e.g. `gen-heavy-pg`, `gen-cloudml-chain`, `gen-heavy-ske` — see
  `_comment`/`crud_filter` in `regression/scenarios/lifecycles/generated__*.json`
  and `regression/scenarios/dependencies.json`).

## Remaining work to reach composition-only (checklist)

**A. Validate the 144 docs nodes (the runtime-validation gap).**
- [ ] **Live heavy/billable windows (≈17 zero services + heavy nodes in partials):**
      ai-ml (cloud-ml, aimlops), DBaaS heavy chains, SKE-on-cloudml, backup,
      baremetal/parallel storage, eventstreams, vpn, gslb, data-flow/data-ops,
      org/SSO. Each needs a **dedicated `SCP_RUN_HEAVY` window** (one workflow run
      at a time; isolate to avoid the 4th-concurrent-VPC limit) to flip docs→VALIDATED.
- [ ] **Owner action (no amount of code unblocks these):**
      `management/organization` (master account), `iam-identity-center` (SSO enable),
      `platform/sts` (console-issued role SRN), `security/configinspection` &
      `security/secretvault` (console-only ids), `data-analytics/searchengine`
      Elasticsearch BYOL license, `storage/archivestorage` dedicated auth key.
- [ ] **Unproven-body, validate-then-promote:** loggingaudit, cdn, direct-connect,
      archivestorage — bodies are docs-derived; confirm on first live 2xx.

**B. Replace hand-written lifecycles with composed equivalents (103 → 0).**
- [ ] Migrate the 103 hand-written ids to `compose_*` / `bundle-*` outputs where a
      model node already covers the same create/verify/delete (today only 61/164 are composed).
- [ ] For each migrated lifecycle, re-run `python -m regression.scenarios.validate`
      (must stay **0 errors**) and confirm the composed steps match the retired
      hand-written steps (the model's `source.lifecycle` back-links the origin).

**Definition of done (composition-only):** provenance totals show `docs 0`,
all 59 services **full**, and `composed == total` in the lifecycle rollup.

---
*Regenerate every number here:* `python -m tools.catalog_status`
(add `--json` for the machine rollup); structural gate
`python knowledge/formal/validate.py`; lifecycle gate
`python -m regression.scenarios.validate`.
