---
status: active
for: orchestrator
---

# Shared Context (CONTEXT.md)

> Every agent loads this. It is the single source of *current state* and the
> facts all roles share. Keep it short and current — deep detail lives in
> `knowledge/`. (이 문서는 모든 agent가 공유하는 현재 상태입니다.)

## What we are building

Automated testing for the **SCP Open APIs** (docs:
<https://docs.e.samsungsdscloud.com/apireference/>), organized on **two axes**
over a shared kernel (`core/`):

- **AXIS 1 · Regression** (`regression/`) — *does it work?* Read-only smoke,
  list→show read-chains, and ordered CRUD scenarios that create/delete real
  resources. Records **pass/fail + response time**. **Target = 100% endpoint
  coverage; then widen parameter combinations.**
- **AXIS 2 · Conformance** (`conformance/`) — *is it well designed & AI-usable?*
  **Static** spec analysis + **read-only runtime** probes, emitted as findings
  against a baseline so only NEW defects alarm.

Supports: `spec/` (extract+diff the spec), `dashboard/` (visualize both axes),
`cleanup/` (tag-scoped teardown).

## The catalog at a glance (source of truth: `data/api_catalog.json`)

- **1,372 endpoints**, all resolved. 13 categories present in the catalog.
- By method: GET 527 · POST 383 · PUT 244 · DELETE 209 · PATCH 9.
- Smoke-testability split: **225** GETs are directly testable (no path params);
  **302** GETs need a resource id (reached via CRUD/read-chains); **845** are
  mutating (reached via CRUD lifecycles).
- Re-run the live summary any time: `python -m spec.summary`.

> Coverage is what drives AXIS 1 to 100%: directly-testable GETs are the floor;
> the long tail (id-bound GETs + mutating endpoints) is unlocked by writing more
> CRUD scenarios. See `knowledge/scenario-catalog.md` for what exists today.

## Endpoints, region & auth (essentials)

- **Per-service hosts**, not one gateway:
  - regional: `https://<service>.<region>.<env>.samsungsdscloud.com`
  - global (account-scoped, no region): `https://<service>.<env>.samsungsdscloud.com`
- Global services: `billingplan, budget, cloudcontrol, costexplorer, iam,
  organization, pricing, product, quota, resourcemanager, support`
  (override via `SCP_GLOBAL_SERVICES`).
- Path roots collide across services (`/v1/clusters` ∈ ske, mariadb, mysql…) →
  **always target the service's own host**. Set `SCP_REGION` (+ `SCP_ENV`, default
  `e`); odd subdomains via `SCP_SERVICE_HOSTS` (JSON).
- Auth: **Access Key + HMAC-SHA256** over
  `method + encodeURI(url) + timestamp + accessKey + clientType`, Base64, in
  `Scp-*` headers (`clientType = "Openapi"`). Tunables in `core/auth.py` +
  `SCP_HMAC_*`. On 401/403, adjust the signing string / header env vars.

## Safety gates (NON-NEGOTIABLE)

| Operation | Default | Enable with |
|-----------|---------|-------------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **blocked** | `SCP_ALLOW_MUTATIONS=true` |
| `DELETE` | **blocked** | `SCP_ALLOW_DESTRUCTIVE=true` |
| Heavy/billable lifecycles (VM, K8s, DB) | **skipped** | `SCP_RUN_HEAVY=true` |

Smoke + read-chains only call `GET`s. Mutations happen exclusively through
ordered CRUD scenarios. Never relax these as a shortcut.

**Run sequencing (owner rule, 2026-06-10):** one workflow run at a time — before
pushing anything that triggers `api-test.yml` (any `.github/run-request` touch,
including the consume/delete commit), confirm the previous run has FULLY
concluded, sweep job included (`actions_list` status != in_progress).

## Isolation & teardown (why we don't break other runs)

- Every created resource is stamped by `core.registry` with
  `(owner, run_id, axis, ttl)` and recorded in a per-run manifest →
  deterministic reverse-order teardown.
- `cleanup.reconciler` reclaims account-wide orphans **only when they carry our
  owner tag and are finished/expired**. Name-prefix matching is a *fallback* only.
- Account quotas (vpc=5, private-dns=3, …) are modelled in `core.budgets` +
  `regression/scenarios/dependencies.json`; the engine **reserves** a slot before
  a quota-bound create and **skips** (not fails) when exhausted.
- **VPC scheduling / reuse:** 8 lifecycles touch the 5-VPC cap. The **6 heavy**
  ones now **adopt one session-shared VPC** (`conftest.py shared_vpc` →
  `engine.provision_shared_vpc`; steps marked `{"adopt":"vpc"}`), so heavy runs
  hold 1 shared VPC instead of up to 6 and `heavy-shared-networking` is no longer
  starved (6 creates → 1; no-op fallback to self-create; pending live validation —
  `tests/crud/test_shared_vpc_adopt.py`). The 2 light networking lifecycles still
  self-create for coverage. Cross-run isolation + remaining gaps (the pytest CRUD
  driver still builds a fresh `Budget` per lifecycle and never `sync()`s it live):
  see the lane playbook in
  [`knowledge/vpc-scheduling-strategy.md`](../knowledge/vpc-scheduling-strategy.md)
  (machine-readable: `dependencies.json:vpc_schedule`).

## Where results live (the contract)

One unified store under `reports/results/` (gitignored):

- `observations.jsonl` — AXIS 1 calls (`endpoint_key, method, path, status,
  category∈{ok,soft,fail}, elapsed_ms, source∈{smoke,read_chain,crud_probe}`).
- `findings.jsonl` — AXIS 2 defects (`endpoint_key, rule_id, severity∈{red,
  yellow,green}, detail, source∈{static,runtime}`).

Schema lives in `core/results.py`. The dashboard reads this store first (legacy
flat files are a fallback). Baseline: `data/baselines/known_issues.json`.

## Current state (keep this updated as work progresses)

- **IA/CX phase 1 (2026-07-03, branch `claude/upbeat-ritchie-ieus5u` — owner-approved
  4-item batch, no live mutations, .github/ untouched):** tracker =
  `docs/working/trackers/UIUX-AUDIT-2026-07-03.md` §2 (구현 현황 표 참조).
  (1) 홈 파이프라인 = 확정 IA 4칸(Catalog·Modeling·Testing·Reporting), `/planning`
  구 스테퍼 301→`/planning/resources/map`, planning.html/_plan_steps.html 삭제
  (`1aa96408`); (2) `/runtime` scope=mine 기본 — loggingaudit×oplog origin join
  (local/CI/unknown 배지), hours∈{1,6,24}, deleted 숨김 기본, mine 0건+로컬 실행
  없음 → all 폴백+배너, standalone Testing 셸 (`33bf61f4`; 로컬 run 워커가 이제
  `APITEST_RUN_ID=<rec id>` 를 스탬프); (3) `/testing/resources` = 잔존 자원 단일
  정본 — 실측 owned 스캔(비동기+캐시) 상단, ingest 표 '이력' 강등,
  `known_issues.json` 에 `stuck_resources` 3건 신설(접힘 '기지 항목'), 강제 클린업
  pre-scan 모달 + 사전 409 표기, `/local-run` 301 (`98c3b25f`); (4) console2
  pre-flight blast-radius HTML 모달 — 서비스별 생성~삭제 예상 + 실측 ETA(durations
  .json) + heavy 명명/필수 체크, preflight 실패 = 완전 차단, 강제 클린업 confirm
  2곳도 owned-목록 모달로 (`f686601d`; `_plan` preview 에 est_creates/est_deletes/
  duration_s 추가).
  - **OPEN (IA/CX 후속):** P1-3 ctxbar ctx_snapshot 공유 의존성 · P1-4 잔여
    (`/testing` 서브탭 라벨 "CI 디스패치 · 스케줄" + 경로 대비 배너) · P2 초장문
    페이지 접기 · P2-8 Reporting 서브탭 단일 include · 한/영 정책(P3).
    `local_run.html` 템플릿은 build_local_demo 가 참조해 보존(라우트만 301).
- **MODEL-PROMOTION pass (2026-07-03, same branch — offline only, no dispatch/live calls):**
  - **18 docs→VALIDATED promoted** off service-scoped 2xx evidence via the NEW
    `python -m tools.promote_validated` (dry-run default; `--apply` = targeted
    provenance-line edit + `# evidence: <key> (run <id>)`; join mechanics + the
    /v1/clusters cross-service collision guard in `knowledge/validated-facts.md`
    2026-07-03 block). Model now **149 VALIDATED / 126 docs / 0 incomplete** (was
    131/144/2 — `no_api` nodes now count complete in the Modeling UI, the honest fix).
  - **34 docs nodes carry `gated: <reason>`** (license/entitlement-403/org-master/
    credential — validate.py GATED_VALUES + FORMAT.md convention): the Modeling
    map/worklist now separate 게이트(할 수 없음, 34) from the actionable docs queue (92).
  - **light-batch-2 DISPATCH-READY (NOT dispatched — owner rule):** 9-lifecycle
    light batch (5 newly composed in `generated__light-batch2.json`: gen-vpc-endpoint ·
    gen-private-nat(+tgw-vpc-connection) · gen-direct-connect · gen-cm-event-policy ·
    gen-lb-members-light; + enabled gen-alert · gen-quick-query-validate · gen-wave4-asg
    (ASG desired 0 = no VM) · gen-wave5-apigw-privatelink). validate 243/0 err ·
    validate_dag 0 gaps (adopt_edges +4) · collect-only = exactly 9. Request block:
    `action=run` `mutations=true` `destructive=true` `heavy=false`
    `crud_ids=gen-vpc-endpoint,gen-private-nat,gen-direct-connect,gen-cm-event-policy,gen-lb-members-light,gen-alert,gen-quick-query-validate,gen-wave4-asg,gen-wave5-apigw-privatelink`
    → expected yield up to 15 promotions (run `python -m tools.derive_verified` on the
    evidence, then `python -m tools.promote_validated --apply`).
  - **OPEN:** (1) dispatch light-batch-2 when the chat-heavy lane is clear (owned==0 +
    ~5min audit silence — batch-2 lesson); (2) **heavy-60 breakdown pending** — the
    remaining ~57 actionable docs nodes are heavy-parented (DB log-export ×4,
    bm/pfs snapshots, lb-static-nat, subops tails …): bank them onto their engines'
    next heavy windows; (3) env-conditional light leftovers need owner input:
    devops-service (iam-member id), secretvault-vault (iam-temp-auth-key), trail
    (account_id + OBS bucket), iam-group-member/iam-user-policy-binding (user_id),
    iam-resource-policy (srn); blocked: iam-role(+binding) blocked-owner,
    iam-saml-provider blocked-engine, image-registration (no real image source, 4xx
    by design), fs-replication (DR-region kr-east1 teardown — supervised run only),
    asg-notification (user_id + IB-026 list envelope), cm-event (data-dependent lookup).
- **▶ SESSION HANDOFF / RESUME HERE (2026-07-02, branch `claude/upbeat-ritchie-ieus5u`).**
  All committed & pushed; **live owned == 0** (verified post-phase-3 + stop-sweep). Durable
  `verified_endpoints.json` **1250 → 1464 (+214)** — the D2–D7 DB depth campaign (+150) plus the
  next-batch α/β runs (+64: epas partial +13 run 28628073176, pg full +51 run 28631983945), all on
  the **chat-heavy lane** (`.github/chat-heavy-request` push → `chat-heavy.yml`; note
  `api-test.yml` push automation stays owner-disabled, so "CI heavy lane" = chat-heavy).
  **Batch-2 lessons (2026-07-03):** (a) settle-polls must also end on terminal-bad states —
  epas pinned at `service_state=UNKNOWN` after a Parameter Modify Error and 200+UNKNOWN
  defeats `give_up_status`; all 94+23 wait-after polls now carry FAILED/ERROR/UNKNOWN in
  `until`. (b) **Never dispatch while a prior run's sweep is still converging** — β retry-1
  lost its shared VPC to the α stop-run's sweep 2s after provision (audit 01:06:03→01:06:05);
  gate every dispatch on owned==0 AND ~5min audit silence. (c) pg backend was FAST today
  (create 8min, full chain 22min); Parameter Modify → async Error is common to epas+pg
  (recorded as 2xx accept then errors — triage material, not a blocker). epas tail
  (archive/log-export/patch/kernel/stop/start/resize) remains open — backend flaky.
  - **D2–D7 in 3 phases:** P1 (run 28595785223) proved `*-cluster-subops-guarded` bank NOTHING
    dispatched alone (window-only design; only `database-mysql-cluster` ran real depth; its
    observations were LOST — the lane had no artifact upload). P2 (28599889165): new
    **self-sufficient `*-cluster-subops-full`** lifecycles (`database__subops-full.json`,
    create→wait→subops→delete from proven blocks; replica/restore excluded as leak-unsafe)
    → **+73**. P3 (28602725440): **ExistInprogress pacing fix** (settle-poll after every
    mutating sub-op) opened the serialized tail (archive/audit-log/backup/stop/start/
    kernel-upgrade/add-block-storages/…) → **+77**. mysql/mariadb/cachestore sub-op depth
    is now MAXED to what the account allows; details in `knowledge/validated-facts.md`
    (2026-07-02 block).
  - **Evidence pipeline (NEW, load-bearing):** chat-heavy now uploads `reports/results/` as
    an artifact AND mirrors it to the oplog bucket (`runs/<APITEST_RUN_ID>/artifact/`) —
    sessions cannot download GitHub artifacts (proxy blocks api.github.com). For older runs:
    push-triggered `fetch-results.yml` bridge. Fold with
    `GITHUB_RUN_ID=<id> python -m tools.derive_verified --observations <file> --out data/baselines/verified_endpoints.json`.
  - **Engine:** new `poll.give_up_status` (end a settle-poll on 4xx instead of burning its
    timeout — P3's epas create was rejected and its ~15 wait-polls each burned 900s; run was
    action=stop'ed after 3/4 engines passed; evidence survived via always() mirror).
  - **Offline fixes landed:** 3 `test_compose_*` failures (real cause = e2be5356 WS4 refactor,
    not b5a8295c), dag_runner/dag_scheduler "flake" root-caused (= `cleanup/verify_clean.py`
    no-op'ing `time.sleep` PROCESS-WIDE at import; stub now scoped inside `scan_owned`) —
    **full offline suite 390/390**. `build_ia_demo` loaders memoized (build-scoped).
  - **OPEN / next:** (1) **epas** subops-full single-engine retry (create 500'd this round —
    transient/capacity; body is proven-good from P2); (2) backup-agent/backup-job need a live
    VM window (stale `server_uuid` 404) — bank during a compute-virtualserver-full run;
    (3) replica/restore depth belongs to `gen-heavy-*-replica/-restore` (disabled, need
    capture/teardown validation); (4) sqlserver stays license-gated reachability-only;
    (5) SCF stranded pair auto-expires 2026-07-31 (PLS state still 403-unreadable);
    (6) GitHub MCP token expired mid-session — re-auth needed for run-status/log API
    (oplog-bucket evidence path unaffected); (7) `.github/chat-heavy-request` left at
    `action=noop`.
- **PLATFORM pass (2026-07-02, same branch — remaining platformization work executed):**
  - **M4 worker executor VERIFIED in-process (read-only E2E):** uvicorn
    `PLATFORM_EXECUTOR=worker` → `/runs/trigger` (smoke × service=quota) queued a
    `dispatched` record → `python -m runner.worker --once` claimed it
    (`local-1783030980`), ran validate → smoke (47 live GETs pass; mutation gates
    forced false — worker `build_env` override beats a gate-arming host `.env`) →
    dashboard → snapshot (64 files to `runs/local-…/snapshot/` on the real oplog
    bucket) → finalize; DB milestones + final status `done`. Zero worker-code fixes
    needed. **Still needs docker/owner:** Docker build + compose up (server+worker
    containers) and a worker-path mutation run (docs/PLATFORM-PLAN.md M4).
  - **Scheduler 1.0-d first step (flag-gated, non-disruptive):** `chat-heavy.yml`
    now understands request-file keys `dag=true` (+ optional `dag_targets=`) — runs
    `tools/dag_run_live.py` (SCP_DAG_RUNNER + AIMD) instead of pytest-xdist; the
    default path is untouched (`dag` absent → xdist exactly as before). Evidence
    upload/oplog-mirror/sweep steps apply to both paths. Full 1.0-d cutover (making
    dag the default) still needs a validated heavy dag run — owner-gated.
  - **Validator debt cleared (offline):** `requires_env` registered as a known
    lifecycle key (engine reads it — engine.py:714; clears the generated__cloudml
    warning), and the 10 untagged disabled lifecycles got IB-030 `_status`
    (quota/support-reads, dns-hosted-zone, 4× gen-heavy-*-replica,
    gen-heavy-mariadb-upgrade → `stale` [retired/superseded/de-dup notes];
    certificatemanager-import, iam-role → `blocked-owner`). Validator now
    237 lifecycles / 0 errors / **5 warnings** (was 16; rest are path-typo infos).
  - **IB-019 verified done** (backlog row: billingplan + devopsservice bodies already
    re-derived; no drift work left). `controlplane/tests_offline.py` hermeticity fix:
    host `.env` re-armed `SCP_ALLOW_DESTRUCTIVE` via `_load_dotenv` setdefault after
    the pop → gate now pinned `"false"` (18/18 again). All offline suites green:
    tests/offline 392/392, controlplane 18+18+16+16, runner 16/16.
- **PRIOR (2026-06-29 close, branch `claude/ecstatic-tesla-fo1g3b`).**
  Everything is committed, pushed, and FF-merged — **`main` = feature = `origin` = `101e08c2`,
  working tree CLEAN, live owned == 0** (last verify post-D1 cleanup; re-run `POST /api/cleanup`
  on resume to reconfirm). Nothing is mid-execution.
  - **Done this session:** (1) platform convergence — console2 → ONE unified 4-stage console
    (Catalog · Modeling · Testing · Reporting), console2 retired; Reporting `lifecycle:step`
    attribution bug fixed. (2) Coverage campaign **13 → 55 / 59 services (93%)** (read-only batch
    +20, config-create batches +9, consequential +3, 4 parallel modeling agents + light harvest
    +6, smoke +2, backup/pfs +2); durable `verified_endpoints.json` **1035 → 1250**. (3) **Modeling
    UX reworked** (user-driven): table view grouped by **category ▸ service** with an authoring
    tally (✓/⚠/docs), **full-page** node edit (no cramped side-panel), a Catalog-vs-Modeling
    explainer, + deps aids (reverse-deps list, unresolved-ref warning; autocomplete already
    existed). (4) **Static demo now has per-node detail** — `build_ia_demo` bakes 275
    `node-<id>.html` recipe pages; Modeling/Catalog links open them (was 404). Republished to
    `dashboard-data:/ia-demo/` → **https://kyle-agent.github.io/api-test-automation/ia-demo/**
    (needs repo Settings→Pages = dashboard-data root). (5) `cloudcontrol` + `archivestorage`
    added as **reachability-only** waivers (261 total).
  - **Coverage ceiling — 4 services stay `modeled`, NOT fixable in this account (don't retry):**
    `ai-ml/cloud-ml` (SCR-credential-gated 404), `platform/sts` (all-POST; IAM `createrole` 500s),
    `management/cloudcontrol` + `storage/archivestorage` (reachability-only by design → 403/401 =
    access evidence, never a verified 2xx). Light/read coverage is MAXED.
  - **OPEN / DEFERRED (what a next session could pick up):**
    · **Heavy deep-coverage D2–D7** (mysql/backup/epas/mariadb/sqlserver/cachestore) — **HALTED**.
      D1(postgresql) timed out at ~40% in-session, cluster CREATE went `FAILED` + leaked → recovered
      via force `POST /api/cleanup` (owned==0 confirmed). Each run is ~1.5–2h, **billable**, leak-prone,
      and adds **endpoint-DEPTH only (0 new service coverage — DBs already green)**. → **Run via the
      CI heavy lane (`api-test.yml`), NOT an in-session TestClient poll** (the in-process run + my
      1h poll timeout is what caused the leak). Partial PG depth (+16 endpoints) is already banked.
    · `build_ia_demo` is now ~5 min (275 node pages + console2 bundle); optimise with a cached
      `load_model()` if it matters. Its Playwright `verify()` step is slow — for a quick rebuild use
      `python -c "from controlplane.build_ia_demo import build; build()"` (skips verify).
    · Pre-existing (not this session, flagged): 3 `test_compose_*` failures in
      `controlplane/tests_resources_offline.py` (from the b5a8295c rename); flaky-timing
      `dag_runner/dag_scheduler` tests (pass in isolation, fail under full-suite load); the ~69
      `scenarios.json`-backed nodes whose lifecycle enabled/heavy is read-only (parallel-campaign rule).
  - **Resume commands:** `python -m spec.summary` (coverage) · `git log --oneline -6 main` ·
    live app `uvicorn controlplane.app:app` then `/planning/resources/map` (Modeling) ·
    verify clean `POST /api/cleanup`. Safety gates unchanged (CLAUDE.md Hard Rules).
- **PRIOR (2026-06-29, parallel-agent coverage campaign detail — branch `claude/ecstatic-tesla-fo1g3b`):**
  drove service coverage **42 → 55 / 59 (93%)** on the unified Testing console, measured on
  the (now-fixed) Reporting surface; durable `verified_endpoints.json` **1187 → 1234**.
  Sequence: B2 consequential live batch (cdn/gslb/filestorage green; dns create-public-domain
  500s — backend defect) → **4 parallel modeling agents** (firewall/sts · net-infra · storage ·
  billing/cloudcontrol/org), no live mutations, disjoint per-service fragments → light-ready
  harvest (+6: billingplan, direct-connect, firewall, loadbalancer, vpn, baremetal-blockstorage)
  → read-only smoke refresh (+2: organization, dns) → 2 borderline light read lifecycles
  (+2: backup, parallel-filestorage). Every batch verify-clean, **owned == 0** (no leaks).
  Notable fix: `core/config.py` adds `sts` to `DEFAULT_GLOBAL_SERVICES` (sts is GLOBAL —
  was routing regional → portal HTML 404). **Hard ceiling = 4 services unreachable in THIS
  account, not a modeling gap:** cloud-ml (SCR-credential-gated 404), cloudcontrol (403 —
  needs org-master + Landing Zone), sts (all-POST; IAM `createrole` 500s, no self-assumable
  role), archivestorage (401 — `scp-archivestorage_hmac` not in this account's service catalog).
  Light/read coverage is now MAXED; remaining gains are endpoint-DEPTH inside the 55 green
  services via **heavy** (billable) lifecycles, or backend/entitlement fixes outside our control.
- **PRIOR (2026-06-28, platform convergence + live coverage push — branch
  `claude/ecstatic-tesla-fo1g3b`; reporting fix FF-merged to `main` at `8237e153`):**
  console2 absorbed into the controlplane as ONE unified 4-stage console
  (**Catalog · Modeling · Testing · Reporting**); console2 app retired (S4). **Fixed a
  convergence bug**: the Reporting coverage surface keyed only on the smoke sweep's
  `category/service/op` endpoint_key and was BLIND to the engine's `<lifecycle>:<step>`
  keys — so every live Testing run showed +0 coverage. Now resolves lifecycle→service
  via `loader.load_lifecycles` (`controlplane/reporting_routes.py`; regression test
  `tests/offline/test_reporting_coverage_key.py`; fact in `knowledge/validated-facts.md`).
  **Coverage push (read-only, zero-resource):** ran the 37 read-only VPC-free non-heavy
  lifecycles via Testing `/api/run` (mut OFF) → **service-level tested 13→33 / 59 (+20
  newly green)**, e.g. all 6 database read services (mysql/postgresql/mariadb/epas/
  sqlserver/cachestore), data-analytics (vertica/searchengine/eventstreams/data-flow/
  data-ops), ske, quota, support, loggingaudit, cloudmonitoring, pricing, product,
  configinspection, iam-identity-center. `cloud-ml` stays modeled (secret/SCR-gated →
  `GET /v1/cloud-ml/images` 404). **Durable:** `data/baselines/verified_endpoints.json`
  **1035→1167 (+132)** — folds this session's read-only batch + the earlier heavy-VS run
  (`compute-virtualserver-full:*` creates/deletes) + light-validate, which derive had
  never captured (it defaulted to the singular `observations.jsonl`). Local runs ⇒ empty
  `first_run`/`last_run` (backfilled by future CI). Verify clean: only the known
  IAM-undeletable log-group survives (read-only ⇒ no new resources).
- **PRIOR (2026-06-25, hand-driven heavy coverage session — committed DIRECTLY to
  `main` at user request, NOT a feature branch):** three HEAVY live runs landed on
  `main` (origin/main tip `44db7896` at handoff; verify with `git log --oneline -1
  origin/main`). Coverage gains: **filestorage 8→17/21** (snapshot+schedule CRUD +
  restore; replication teardown must go via DR region `kr-east1`); **virtualserver
  17→29/113** — ALL 24 autoscaling-group endpoints now 2xx (new lifecycle
  `regression/scenarios/lifecycles/compute__virtualserver-autoscaling.json`,
  `heavy-asg-full-coverage`, 38 steps); **vpc 46→58/95** (all 14 PrivateLink
  endpoints 2xx — IP-type PLS works without an LB: `connected_resource_type=IP`),
  **scf 13→17/36** (PL service/endpoint). **Dashboard republished: C3 62.6→63.6%**
  (804/1264, verified-2xx 709, C2-called 89.7%). console2 fix also merged: detail
  pane preserves scroll across live-poll re-renders (`keepDetailScroll`). Heavy runs
  used isolated git worktrees (now removed) → merged to main.
  - **⚠️ OPEN — 2 stranded SCF cloud-functions, un-teardownable (product deadlock).**
    `regrw5trg57f68be7` (`9e231b01e2394d7aaa8dcca218e770cb`) + `regrw5trgd7ff680d`
    (`3aac2e34203a42cab56b089336bbd18d`). Their PrivateLink **Service config is stuck
    in CREATING** (`pls_id=null`, hours+): PUT disable → 400
    `privatelink-service-not-allow-state-error` ("not allowed when Creating");
    DELETE function → 400 `function-not-deletable-error` ("only when PLS disabled &
    no PLE"). Deadlock. Documented in `data/baselines/known_issues.json`
    (`compute/scf/updateprivatelinkservice+deletecloudfunction`) + scf
    `coverage_ledger` `stranded_resources`. **Note:** SCF PLS is a *config field on
    the function* (no standalone id) — NOT the same as a **VPC** privatelink-service
    (standalone, deletes fine; all VPC PLS now 0). Function body shows
    `eots_date: 2026-07-31` (platform auto-expiry) — likely auto-reclaimed then.
    **Next-session lever:** poll the two PLS states; the instant either leaves
    CREATING, `PUT .../privatelink-services {privatelink_service_enabled:false}` then
    `DELETE /v1/cloud-functions/{id}` (needs `SCP_ALLOW_DESTRUCTIVE=true`).
  - **Teardown otherwise CLEAN:** live infra all 0 residual (VPC/subnet/PLS/PLE/LB/
    ASG/VM/volume) — verified post-run. Earlier in the session 4 stranded shared
    VPCs (heavy-wave leftovers) were also reclaimed via local-registry ownership;
    one VPC NOT in our registry was left untouched (cross-env shared account — same
    rule that protects the other env's `kyuh.choi+areg1@samsung.com` resources).
  - **What to advance next (2026-06-25, supersedes 2026-06-19):** (1) retry the 2
    SCF teardowns once PLS un-sticks (above); (2) remaining privatelink gaps are
    cross-side/entitlement — scf `approve/connect` are baselined product bugs (404
    with valid ids), apigateway 7 PL gaps are 403-entitlement/500; (3) virtualserver
    still 84-gap: the big lever is a **HEAVY VM lifecycle** (createserver→...→delete,
    desired_count low) to unlock ~60 server/volume/snapshot endpoints + ~20 id-GETs;
    (4) filestorage 4 gaps = replication DR-side (`filestorage-dr`/`kr-east1`) + 1
    needs-VM. Earlier 2026-06-19 ranked plan still valid for the broader campaign.
- **LATEST (2026-06-20, session 2b — live FULL-HEAVY run + makespan scheduler work,
  `claude/start-here-review-5z8jt2`):** ran the full heavy DAG live via
  `tools/dag_run_live.py ALL` (dynamic AIMD, all gates on). **184/184 · 156P/25F/3S ·
  wall 68.6 min · all 3 big DBs (mysql/postgres/dbaas) PASSED.** A load-induced 503
  gateway storm (12:07–12:25) caused **8/20 heavy failures** (77% of all fails were
  Envoy `upstream connect error … connection timeout`); AIMD clamped to floor 4 and
  rode it. Survivors reconciled to **0** (took 3 passes — DELETE needs BOTH
  `SCP_ALLOW_MUTATIONS`+`SCP_ALLOW_DESTRUCTIVE`; the run-end "shared VPC deleted" was
  premature, reconciler backstopped it). Analysis: `docs/working/trackers/
  POSTRUN-2026-06-20-fullheavy.md` + facts in `knowledge/validated-facts.md`.
  **Makespan finding (data-grounded):** floor = 50.7 min (postgres single create);
  actual 68.9 min ⇒ **18.2 min (26%) recoverable**. Dominant waste was NOT the
  vpc-peering tail but `heavy-shared-dbaas` (45-min critical create) starting **22.9
  min late** — starved by 162 light free-wave nodes under the storm-clamped slots.
  **New module `regression/scenarios/dag_scheduler.py`** (`run_dynamic`): dynamic,
  duration-prioritized (longest-job-first via `schedule_optimizer` tail-length),
  VPC-slot-semaphore-gated dispatch — applies priority to BOTH self-create AND adopt,
  no wave barrier. `simulate_full` DES projects 64.3→51.8 min (19%, ~floor) at
  workers=8. 7 offline tests. **A1/A2/A3 NOW WIRED** (commit 26ad0241): `dag_run_live`
  dispatches via `run_dynamic` by default (`SCP_DAG_DYNAMIC=true`, set false for static
  fallback), folds measured wall-times via `update_durations` (was never done — nodes
  stayed n:1), and marks dashboard bands active for the dynamic path. **Remaining =
  FIRST live validation on a clean storm-free heavy run + 1.0-d CI cutover — DEFERRED
  (heavy lane busy, owner rule; not run here).** Also **merged origin/main coverage
  waves 3–5** (25 commits): 222 lifecycles / 0 errors; reconciled `validate_dag` (+4
  adopt_edges: vpc-internet-gateway/nat-gateway/port/privatelink-endpoint) → green.
  Static ceiling now 1370/1372; the 2 gaps = waived iam `deletepolicies` + a NEW
  `budget` id-GET from main's coverage work (informational, not a gate, budget-owner item).
  Optimizer TIER-D structural fixes VERIFIED: servicewatch-201 + filestorage-teardown
  were **mis-diagnoses** (code already correct); apigw-privatelink IP is a real
  **dual-mode** issue (adopt-fallback → own-block IP vs shared subnet) needing the
  R3 dynamic-IP fix, not a swap (deferred).
- **LATEST (2026-06-20, session 2 — `claude/start-here-review-5z8jt2`, bootstrap
  review + scheduler-state verification, NO live run):** START_HERE spot-check
  done; corrected two stale references (current observed state wins):
  (1) **static ceiling is 99.9%, not 100%** — `python -m spec.coverage_gap` →
  **1371/1372 reachable, GAP=1** = the blast-radius-**waived** iam `deletepolicies`
  (`DELETE /v1/policies/bulk`, un-probeable, correctly excluded — NOT a backlog
  item). The "static ceiling 100.0% (1372/1372) / gap 0" wording in the 2026-06-17
  blocks below is superseded by this. (2) **The ADR-1.0 dependency-DAG scheduler is
  already BUILT & merged on main** — `regression/scenarios/dag_planner.py` (1.0-b
  closure + topological-wave planner), `dag_runner.py` + `dag_runner_live.py`
  (1.0-c, flag-gated `SCP_DAG_RUNNER`, with AIMD adaptive concurrency),
  `dag_plan_graph.py`, `schedule_optimizer.py`; **93 offline tests green**
  (`pytest tests/offline/test_dag_*.py`). So the 2026-06-20 cutover handoff's
  "Path to 1.0 → start at 1.0-a/b" is STALE: 1.0-a (`validate_dag --check`, COMPLETE
  DAG / 0 gaps), 1.0-b and 1.0-c are all DONE. **The only remaining scheduler step
  is 1.0-d:** the CI workflow (`api-test.yml`) is still on the **v0.5 cutover**
  (one `-n 6` xdist pool + `SCP_VPC_SEMAPHORE` throttle, serial vpc-crud job
  dropped); switching it from xdist to `dag_runner_live` needs a validated heavy CI
  run and is **dispatch-gated** (Claude token can't `workflow_dispatch` → 403).
  **Do NOT rebuild the planner/runner** — verify, then drive 1.0-d.
- **PRIOR LATEST (2026-06-20, hand-driven from Claude remote):** closed the last
  id-bound GET **reachability** gap — wired `showresourcebycomponents`
  (`GET /v1/resources/{region}/{service}/{resource_type}/{resource_identifier}`)
  into `resourcemanager-tag-lifecycle` and **LIVE-VERIFIED 200** (read-only probe,
  no mutations). Static ceiling now **1371/1372 reachable (99.9%)**; the single
  remaining static gap is the **blast-radius-waived** iam `deletepolicies` (DELETE
  /v1/policies/bulk — un-probeable, correctly excluded, NOT a backlog item). So
  reachability is effectively MAXED. Fact recorded (`knowledge/validated-facts.md`:
  segments PLAIN not b64, 4th seg = `$.resources[].id`, list `resource_identifier`
  is null). **The frontier from here is LIVE C3 (~50%), which is dispatch-gated**
  (free cheap batch vs billable heavy batch — see "What to advance next").
  **Full per-service C3 analysis done** (13 parallel read-only coverage agents):
  `docs/working/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md`. Raw 2xx = 566/1372 (41.3%). 560
  non-waived uncovered sort into 4 tiers: **Tier 0 FREE** (~11 read-only param-fix/
  read-chain edits), **Tier 1 LIGHT** (~78 non-billable mutations — gslb/cdn +8 each,
  vs-image +11, vpc-light +11, iam b64-SRN +9, scf +5, loggingaudit +6, …),
  **Tier 2 HEAVY** (~120+ billable: DB engines biggest, then compute/network/storage),
  **Tier 3 BLOCKED** (~90: entitlement/product-bug/console-only → waive, don't chase).
  Recommended order free→light→heavy. CI lane confirmed clear (owner-rule OK).
- **PRIOR (2026-06-19, hand-driven from Claude remote — full handoff:
  `docs/working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md`):** published **C3 47.9% →
  50.1%** (633/1264), C2-called **53.4%** (733/1372). Heavy DBaaS run complete +
  clean (8 cluster creates, +22 DB sub-op id-GET 2xx, 0 survivors verified). New
  STANDING designs: **per-service coverage agents** (`.claude/agents/
  coverage-service.md`, `docs/agent-team.md` standing pattern, ledger
  `data/coverage_ledger.json` via `tools.coverage_headroom`, ~6–8 concurrency
  cap); **live-watcher loop** (`.claude/agents/live-watcher.md` +
  `tools/live_watch.py`: watcher→orchestrator→dev-agent); **record-to-git hard
  rule** (HARNESS #6). Coverage so far: queueservice 12/12, resourcemanager 27/27,
  apigateway ~47/55, iam 27/62, cloudmonitoring 6/18 (X-ResourceType CONFIRMED
  required), dns 6/22, scr 8/39, data-ops 5/17, organization 2/37 (org-master
  wall). **Next = full heavy batch (compute/network/storage id-GETs — DB done
  only); cheap batch 3; base64-SRN cross-probe for iam.** Heavy-run gotcha:
  export gates + `SCP_SHARED_*_ID` in the SAME shell as pytest or the heavy
  lifecycles skip. Account clean (0 owned billable).
- Catalog: extracted, 1,372 endpoints, 0 unresolved.
- **29 base (hand-written) CRUD lifecycles** (full list + flags in
  `knowledge/scenario-catalog.md`). Light: resourcemanager resource-group, quota/
  support reads, vpc+subnet+port, scr registry+repo, filestorage volume,
  certificatemanager self-sign, queueservice queue, security-group(+rule),
  virtualserver keypair, virtualserver volume+snapshot, vpc public-ip, vpc
  internet-gateway, kms key, secretsmanager secret, apigateway api+resource, scf
  function+trigger, iam group, iam policy, servicewatch loggroup+stream.
  **Heavy** (`SCP_RUN_HEAVY`): ske cluster+nodepool, virtualserver full VM, mysql
  cluster, postgresql cluster, shared dbaas, shared networking. Disabled:
  dns-hosted-zone, iam-role, certificatemanager-import. Many lifecycles also carry
  **write-setter / in-place-update** steps (coverage expansion) — see
  `docs/working/handoffs/HANDOFF-crud-setter-validation.md`.
- Auth/host resolution: implemented & configurable; confirm against a live `200`.
- **Coverage campaign (multi-agent) — RUNNING.** `docs/agent-team.md` is the
  operating model; `data/coordination/ledger.json` is the blackboard. Per-service
  CRUD fragments now live in `regression/scenarios/lifecycles/*.json` (merged by
  `regression/scenarios/loader.py`; validate with
  `python -m regression.scenarios.validate`). Real target = the **547 uncovered
  write ops / 53 services** from `python -m spec.coverage_gap` (id-bound GETs are
  auto-covered by read-chains). **Wave 1 done** (6 fragments, 13 new lifecycles):
  iam, organization, iam-identity-center, servicewatch, baremetal-blockstorage,
  apigateway → 151 writes closed. **Wave 2 done** (7 cluster-agents, 30 fragments,
  49 lifecycles): networking/{vpc,loadbalancer,dns,cdn,gslb,vpn,firewall,direct-
  connect}, compute/virtualserver, the 6 database engines, storage/{archive,backup,
  file,parallel-file}, security/{kms,secrets,vault,configinspection,certmgr},
  data-analytics ×6 → +302 writes. **Wave 3 done** (4 cluster-agents, +88 writes):
  compute/{baremetal,multinodegpucluster,scf}, container/{scr,ske}, management/
  {cloudcontrol,resourcemanager,loggingaudit,cloudmonitoring,network-logging}, ai-ml
  ×2, financial ×2, platform/sts, devops, networking/security-group.
- **WRITE-COVERAGE CAMPAIGN COMPLETE.** All **547 catalog write ops reachable**
  (write-gap = 0 across all 53 services); **113 lifecycles** (29 base + 84 in 53
  fragments), validator 0 errors, offline tests pass. **Static ceiling 43.0% →
  85.6%**; residual 198-endpoint gap is exclusively id-bound GETs (read-chain /
  probe_reads auto-covered at runtime, so live `cov_op` runs higher). All bodies
  docs-derived, **PENDING LIVE VALIDATION**.
- **Run-time/ops infra (2026-06-11):** full-run wall 3h49m → 51m~1h21m (A∥B split,
  retry caps, slimmed shared-dbaas, provision-first, own-run sweep reap — the
  leftover→VPC-cap poisoning chain is closed). Persistent ops log on Object
  Storage (`apitest-oplog-permanent`, core/oplog.py) + static viewer
  `ops.html` on Pages: live per-event resource tree + run history,
  independent of GitHub. Fail/soft-write response bodies now recorded in
  observation notes (self-diagnosing artifacts). Facts: knowledge/validated-facts.md.
- **Full heavy run landed (2026-06-10, run 27258520218):** cov_op 35.4 / C3 37.5,
  **fail_new 52 → triaged** in `docs/working/handoffs/HANDOFF-fail-new-triage.md` (27 unique:
  6×401 incl. a suspected query-string HMAC signing bug, 8 DBaaS sub-op 500s
  needing a live-cluster window, 5 bulk-body fixes, 8 create/setter fixes).
  Run-time levers since then: optional-step 4xx retries are now capped
  (placeholder paths never retry; `SCP_OPT_RETRY_BUDGET_S`, default 240s per
  lifecycle) and the CRUD passes are **A∥B split** — the serial VPC-CRUD class
  runs in its own `regression-vpc-crud` job in parallel with the adopt-class job
  (wall-clock = max(A,B); A's 1 shared VPC + B's worst 2 = the validated 3-VPC cap).
- **Platform (2026-06-12): the repo is now the SCP API Regression Test
  Platform** (`docs/PLATFORM-PLAN.md`). **M0–M3 DONE** — `controlplane/`
  FastAPI+htmx server (Overview→Plan→Run→Report+Knowledge IA): suite × profile
  dispatch, cron scheduler, live oplog ingest, **M2 command channel**
  (abort/skip/stop-polling polled by the engine at step boundaries), resource
  inventory + single delete, run history/snapshot restore/compare, authoring
  pipeline (validator-gated saves + local git commit), AI seams
  (`ai_pipelines.py` — triage/spec-impact/drafts/fact-extraction, draft-only).
  **M4 built, cutover LAST** — `runner/worker.py` + Docker Compose
  (`PLATFORM_EXECUTOR=actions|worker`), awaiting live/docker verification.
  Pages now also carries `ops.html` (dependency-ordered live resource view
  from the model via `dashboard/gen_dep_map.py`; run-finished
  cleanup-integrity verdict 테스트중/잔존/정리실패/삭제; in-flight-only run
  pills + history-row selection; paginated S3 listing; `oplog-test-*`
  dev-prefix filter; KST timestamps everywhere incl. the
  `dashboard/build.py` header) and `/platform/` (~199-page static export of
  the platform UI, all nav clickable; Plan screen carries the
  model→scenario→suite flow strip with a 합성(gen) filter).
  Sweep/reconciler: `SCP_SWEEP_EXTRA_NAMES` one-shot reclaim for named
  orphans (used for the pre-platform 'selftest' VPC), dashboards bulk
  delete keyed on `dashboard_ids`, and scheduled-deletion re-deletes no
  longer count as progress — a sweep converges in ~2–3 min instead of 5
  full rounds (KMS/Secrets deletion is SCHEDULED: pending-deletion items
  stay listed, PF-09).
- **M5 resource-task model (2026-06-12): R1·R2 DONE, R3 waves LIVE.**
  `knowledge/formal/resources/*.yaml` = **275 nodes / 59 files / 51 groups**
  (2026-06-17, `python knowledge/formal/validate.py` → 275 resource tasks, 0 errors)
  (codes `<cat>-<group>-<resource>`; lookup-node pattern now ~7: image,
  server-type, kubernetes-version, apigw-root-resource, cm-account-resource,
  cloudml-image + fs replication-regions candidate);
  `regression/scenarios/composer.py` compiles them to `gen-*`/`bundle-*`
  lifecycles (engine unmodified; new passthroughs: step `headers`, delete
  `retry_on_status/retries/retry_interval`, delete `json`/non-DELETE
  teardown, lifecycle `credentials` surfacing). Waves 1·2 + heavy window
  (runs 27394211896 … 27421363609): composed roster **~10 stable greens**
  (pilot, vslight 9/9 ×3, apigw 20/20 incl. all deletes, dashboard, queue,
  sec=kms+secret, rg, iam, scf, volume-snapshot; scr/fs recomposed, next run
  should green) → **131 nodes VALIDATED / 144 docs** (2026-06-17). Heavy VS chain proven
  through server creation + port attach/detach; static NAT one field from
  done (`publicip_id`, live-derived — rev 3 dispatched). Product findings
  now live in the **consolidated ledger `docs/working/trackers/PRODUCT-FINDINGS.md`** (12
  rows: 3× 403 missing-IAM-action-definition, budget 500 baselined, devops
  unnamed-fields 400, scf time-or-period, undocumented header
  X-ResourceType, scr private-acl 500 ×3, KMS/Secrets scheduled deletion,
  SCR quota 1EA/visibility confirmed, 2 masked-defect lessons).
  Composition-blocked classes in node notes (console-only ids → M5 Planning
  form; cloud-ml chain composed, gated on SCR auth key + heavy — the
  `regression/scr_docker_probe.py` experiment tests whether SCP keys
  satisfy it; first verdict INCONCLUSIVE on quota-403, registry-borrow
  fallback added). Wave narrative: `docs/RESOURCE-MODEL-PLAN.md` §6.
- **Coverage now (2026-06-17):** static ceiling **100.0%** (1,372/1,372,
  `python -m spec.coverage_gap`; GAP=0 — gap_getid **0**, gap_write **0** —
  `docs/working/plans/COVERAGE-GETID-PLAN.md`); latest published run **C3 44.79%**
  (cov_op 36.73), **fail_new 0 policy holding**, 249 approved waivers
  (incl. 7 PFS `owner-exclusion`). Owner scope: **archivestorage = reachability-only
  coverage (owner override 2026-06-16)** — every endpoint called regardless of 4xx
  (access-tested, 25 owner-exclusion waivers dropped, gen archivestorage-bucket/
  archiving-policy enabled heavy reachability-only, gap 19→0); parallel-filestorage
  + iam-identity-center also reachability-only (owner override 2026-06-16: pfs gap→0;
  IdC 32/32 with synthetic/all-zero-id safety rail = no real account-level mutation;
  +39 waivers dropped → 185 total). **searchengine/vertica/sqlserver also
  reachability-only (owner override 2026-06-16: license-gated engines, access-check
  only — endpoints CALLED with license/4xx/5xx tolerated, synthetic bodies, no real
  license consumed; reachability-TERMINAL, NOT held; 74 entitlement waivers dropped
  → 111 total; gap=0 each; IB-017 → deferred functional-HA, not blocking coverage).**
  **Repo write-op gap now 0; static ceiling 100.0%
  (1372/1372), remaining gap 0 — getid 0, write 0 (2026-06-17,
  `python -m spec.coverage_gap`; id-bound GETs now all reachable via composed
  read-chains/scenarios).** per-profile baselines file-suffixed
  (`core/baselines.py`); multi-tenancy confirmed required.
- **SKE upgrade LIVE-PROVEN (run 27492496266, 2026-06-14):** the
  `gen-heavy-ske-upgrade` chain passed end-to-end (35m real cluster
  v1.33.5→v1.34.3 control-plane + node roll). Nodes **ske-image /
  ske-cluster-upgrade / ske-nodepool-upgrade → VALIDATED**
  (`scp_original_image_type=k8s` listimages query confirmed required; nodepool
  upgrade takes `os_version`, not k8s version). Triaged green via the escalation
  ladder (rev1→rev2 ske-image query fixes → rev3 green). fail_new 0 held.
- **Autonomous-loop hardening + P1 ingestion complete (2026-06-14):** the
  self-driving loop now has explicit **Stop-when** — an L0→L3 **escalation
  ladder** (L2 = userguide WebFetch fallback) with pre-set limits (3 rev/window,
  no-progress detector) and **6 human-needed STOP criteria**, plus a **3-lane
  parallel pipeline** (A result-wait/triage · B guide/domain · C compose/prep)
  with read-before-claim on the shared VPC — codified in
  `docs/agent-team.md` (IB-015/016). Lane B ran **3 parallel waves (15
  services)** that **closed the entire P1 `knowledge/formal/INGESTION.md`
  backlog** (now 0 `—` P1 rows): mariadb·epas·sqlserver·VS-volume ·
  searchengine·vertica·quick-query·data-flow·data-ops·cloudmonitoring·
  loggingaudit·resourcemanager·queueservice · mngc·apigateway·devopsservice·
  billingplan·pricing·budget·costexplorer. Several smoke-4xx/5xx root-caused
  via the ladder: quick-query/cloudmonitoring 400 = missing required query
  params; loggingaudit/devopsservice(PF-05)/billingplan create bodies corrected
  to the real request models; billingplan 500 confirmed = server bug (baselined,
  not retried). New tickets: **IB-017** (sqlserver Always On needs SQL Server
  license — owner credential), **IB-018** (analytics NiFi/Airflow/Trino are
  SKE-on-k8s, create bodies UNPROVEN), **IB-019** (model↔lifecycle JSON drift:
  billingplan/devops lifecycles still carry old invented bodies). All R1
  0-errors; resource model now **275 task nodes / 59 service files**
  (2026-06-17, `python knowledge/formal/validate.py`).
- **Wave A.1 light batch MATERIALIZED & dispatch-ready (2026-06-17):** the 16
  cheapest VALIDATION-QUEUE Wave A.1 READY nodes are composed into
  `regression/scenarios/lifecycles/generated__waveA1.json` (`enabled:true`;
  validator 209 lifecycles / 0 errors). **Awaiting a single light dispatch**
  (`mutations=true destructive=true heavy=false`, scoped `crud_filter` in
  `docs/working/handoffs/HANDOFF-waveA1-dispatch-prep.md`) once the owner-rule lane clears —
  3 docs→VALIDATED promotion candidates (quick-query-validate unconditional;
  alert + cm-account-resource conditional on a metric-emitting/Running-VM
  account). 7 Wave A.1 nodes stay blocked/gated (sts-token, trail,
  account-budget PF-04, diagnosis, secretvault-vault, certificate-import,
  cm-event-policy — see handoff).
- **Coverage-max roadmap (2026-06-17): `docs/working/plans/COVERAGE-MAX-PLAN.md`.** Grounded
  finding: **offline coverage is MAXED** (C1 100% heavy-on / 52.9% light-only);
  every further C3 gain (last published ~44.79%) is **dispatch-gated**. The 646
  heavy-only endpoints are the frontier (DB engines ~190, networking/vpc 49,
  baremetal-blockstorage 36, loadbalancer 29, organization 28, virtualserver 26,
  …). Tiered dispatch plan with copy-paste run-request blocks: Tier 0 LIGHT
  (Wave A batch, cheapest, lane CLEAR) → Tier 1 PG sub-ops (already-VALIDATED
  cluster) → Tier 2 DB cluster families → Tier 3 heavy networking/storage/compute
  → Tier 4 gated/billable/waiver singletons. Also materialized 3 safe read-only
  lookup promotions (`generated__waveA-lookups.json`: gpu-node-image, cloudml-image,
  volume-type). Validator 212 lifecycles / 0 errors.
- **Tier 0 LIGHT run DISPATCHED + TRIAGED (run 27725293499, 2026-06-18):**
  smoke+read-chains ✅, light CRUD **134 passed / 2 failed / 27 skipped**. No
  billable infra (heavy adopters self-skipped w/o shared VPC). 2 fails triaged:
  (a) **archivestorage 401** reachability tolerance gap → FIXED (401 added to the
  4 reachability GETs, `storage__archivestorage.json`); (b) **iam-policy-extra-writes
  ReadTimeout** = transient flake (no fix). **Finding:** run-request `heavy=false`
  did NOT reach `SCP_RUN_HEAVY` (env was true) — non-billable only thanks to the
  no-shared-VPC adopter skip; truly-light dispatch needs the workflow heavy gate
  fixed (`knowledge/validated-facts.md`). Re-run Tier 0 after the fix to clear
  fail_new → 0 and capture the archivestorage/Wave-A 2xx evidence (promotions).
- **What to advance next (2026-06-19, supersedes the 2026-06-17 plan below):**
  see `docs/working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md` → "What to advance next".
  Ranked: (1) **full heavy batch** (billable; compute/network/storage heavy
  lifecycles to recover the rest of the id-GETs — this session only did DB;
  remember the same-shell env gotcha + arm the live-watcher); (2) **cheap batch
  3** (`tools.coverage_headroom --cheap-only --exclude <done>`, spawn
  `coverage-service` agents ≤6–8); (3) **base64-SRN cross-probe** on iam's
  srn-targeted ops; (4) optimizer per-label windowing; (5) watcher polish.
  --- earlier (2026-06-17) plan retained for reference: ---
- **What to advance next:** **re-dispatch Tier 0 (LIGHT) per `docs/working/plans/COVERAGE-MAX-PLAN.md`**
  once the 27725293499 sweep concludes (lane free) — should land fail_new 0 with the
  archivestorage fix; then promote Wave A docs→VALIDATED and walk Tiers 1→4.
  Confirm heavy rev 3 (static-NAT
  `publicip_id`, scr/fs recompose, docker-probe borrow), then continue R3
  verification waves over the **144 remaining docs nodes** (131/275 VALIDATED
  as of 2026-06-17) (compose → scoped
  `crud_filter=gen-*` run → triage → re-compose), promote VALIDATED nodes
  and progressively replace hand-written lifecycles, then M4 cutover
  verification. Earlier backlog (query-signing fix, DBaaS sub-op windows,
  corrupt `api_bodies.json` entries, servicewatch orphan log groups)
  remains tracked in `docs/working/plans/COVERAGE-WAVE-PLAN.md` /
  `data/coordination/ledger.json`.

- **프로세스/하니스 도구 추가 (2026-06-17):** repo 루트에 자동 로드 `CLAUDE.md`
  (얇은 진입 인덱스 — Hard Rules + Quick Ref + Compact 지침). `.claude/skills/`에
  user-invocable 스킬 6종(`/adr /brief /freeze /pre-push /retro /token-audit`,
  AlexZio00 MIT vendored). 기존 하니스에 백포트된 9개: HARNESS.md "Memory discipline",
  PROMPTS.md subagent STATUS enum(`DONE|DONE_WITH_CONCERNS|NEEDS_CONTEXT|BLOCKED`)
  +changed-files, orchestrator.md severity→머지 액션 표 + output-drift 체크,
  START_HERE.md stale-reference 점검 + 핸드오프 resume-command 규칙,
  `knowledge/validated-facts.md` conf·seen·obs 메타, `docs/working/trackers/harness-tests.md`
  안전레일 적대 테스트. 출처/미채택 목록: `.claude/skills/README.md`.

- **Parallel-scheduling principle (2026-06-18, audit-confirmed):** scenarios with
  **no inter-dependency must run with MAXIMUM parallelism** — optimize for
  **total completion = max(independent lifecycles), not sum**. Shared prerequisites
  (VPC/subnet/lookups) are built ONCE then adopted, so the dependent lifecycles fan
  out concurrently. Audit log (`/v1/logs`) confirmed the independent DBaaS engines
  were running ~serially (postgresql started only after the others finished —
  matching the field report in `engine.active_lifecycles()`). **Root cause = the
  adopt-class `pytest -n 2`** (lowered from `-n 6` in IB-050); the provision RACE
  itself is separately guarded by IB-049 (xdist-gated adopter skip), so with a
  pre-provisioned shared VPC raising `-n` is safe. **DONE (2026-06-18):** adopt-class
  raised `-n 2 → 6` in `api-test.yml` (the IB-050 cap-poisoning fix was really the
  pre-run reclaim step + concurrency-group constant, both still in force; reclaim
  observed working in run 27735741382). **Validate on the next heavy run** — the
  audit-optimizer measures per-run wall-time, so DBaaS phase sum→max(engine) is
  directly verifiable. Full plan + end-state multi-VPC lane model:
  `docs/working/plans/PARALLEL-EXECUTION-PLAN.md`. **DAG gate (1.0-a):**
  `dependencies.json` now carries the dependency DAG (`shared_roots` +
  `adopt_edges`); `python -m regression.scenarios.validate_dag --check` is the
  CI gate (in `validate.yml`) that keeps it in sync with the lifecycles, the
  precondition for the closure→shared-roots→topological-waves 1.0 scheduler.

> When you finish a unit of work that changes any of the above, update this
> section (and the relevant `knowledge/` file) in the same commit.
