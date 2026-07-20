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

- **1,416 endpoints** (2026-07 spec bump), 1,415 resolved / 1 unresolved.
  13 categories present in the catalog.
- By method: GET 546 · POST 392 · PUT 257 · DELETE 211 · PATCH 9.
- Smoke-testability split: **231** GETs are directly testable (no path params);
  **315** GETs need a resource id (reached via CRUD/read-chains); **869** are
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

Mutations default **ON** (`core/config.py` — the project's purpose is real
execution; the deliberate opt-in is the run **selection** + the console2
pre-flight confirm, not an env flag; canonical wording: `CLAUDE.md` Hard Rules):

| Operation | Default | Gate |
|-----------|---------|------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **allowed** | force read-only: `SCP_ALLOW_MUTATIONS=false` (CI smoke/conformance set it explicitly) or profile veto `SCP_PROFILE_FORBID` |
| `DELETE` | **allowed** | disable: `SCP_ALLOW_DESTRUCTIVE=false` or profile veto |
| Heavy/billable lifecycles (VM, K8s, DB) | **skipped** | explicit opt-in: `SCP_RUN_HEAVY=true` or a confirmed heavy selection |

Smoke + read-chains only call `GET`s. Mutations happen exclusively through
ordered CRUD scenarios / composed lifecycles. Never flip a gate just to make a
test pass.

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
  [`knowledge/vpc-scheduling-strategy.md`](../../knowledge/vpc-scheduling-strategy.md)
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

- **CURRENT (2026-07-20 — 오류 프론티어 130키 전수 트리아지+라이브 격파 세션 완결,
  branch `claude/error-404-401-testing-3vr67s`, 오너 지시 "404/401 직접 테스트
  해결 + 멀티에이전트·VPC캡 자율 커버리지"):** 5-에이전트 트리아지(이력감사
  130키 전수·라이브프로브 18키·DB 26키·backup 10키·privatelink 12키) + 리드
  직접 프로브 + **로컬 라이브 검증 런 6회**(light 3·heavy dns 1·heavy DB 레인
  8-lifecycle 3:13:38 — 전부 green, watcher HEALTHY, 종료 후 VPC 0/5 클린).
  ① **라이브 격파 15+키**: log-export 4엔진 16키 전 202("register 500 PF"는
  빈 access_key 오판 — known_issues 4건 해제), scf setcloudfunctioncodefile 202
  (Java 17, 차단판정 뒤집음), dns activateprivatedns 202(크로스리전 의미론
  실증+엔진 귀속), apigw requestprivatelinkendpoint 200×2, epas patchminor 202,
  + 회수(createprivatelinkservice IP버그·scf create/request PLE). ② **PF 신규
  5건**: PF-49 certmanager 키파서(EC P-256도 거부)·PF-50 SW OTLP ingest·PF-51
  sts objstore 500·PF-52 apigw approve 500(settle 후에도)·PF-53 scf approval
  lookup-scope 분열. ③ **원인 확정+수리 반영(다음 heavy 런 판정)**: createrestore
  400 = backup_recovery_time:"" (가짜-id 프로브 실증, 4엔진 제거) · pg patchminor
  = ExistInprogress(어휘 아님 — verbatim+400사다리) · backup bk-vm-policy 13스텝
  (direct-uuid) · privatelink 티어다운 인터락(CANCEL-선행, 좌초 복구 절차 검증).
  ④ 전수 분류: PF 35 · 엔타이틀 40 · 오너결정 45 · aimlops 검증대기 6 —
  **waiver 후보 제안서 `docs/working/trackers/WAIVER-CANDIDATES-2026-07-20.md`
  (오너 승인 대기)**. 상세: validated-facts 2026-07-20 블록(애드엔덤 3개 포함).
  **▶ 다음 착수(리터럴)**: ⓐ vs-netops 레인 판정 —
  `SCP_RUN_HEAVY=true SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true python -m pytest tests/crud -m crud -k gen-heavy-vs-netops -q`
  (bk-vm-policy 13스텝 = backup 9키 + makespan +15~75분), ⓑ 다음 DB 레인에서
  createrestore 4키+pg patchminor 실 2xx 판정(수리 반영분), ⓒ waiver 제안서
  오너 리뷰, ⓓ apigw approve 500(PF-52)·scf 404(PF-53) SDS 문의.
- **PRIOR (2026-07-20 — 리포 전체 정리, branch `claude/repository-cleanup-bqil1u`,
  오너 지시 "흩어진 정보 모으고 미사용 소스/문서 정리"):** ① **`docs/archive/`
  tier 신설** — superseded 문서 33건 이동(핸드오프 11 · 플랜 7 · 트래커 9 ·
  ROADMAP/M6-DESIGN/IA 3 · working 낱개 3) + **CONTEXT.md 다이어트 1,138→~220줄** (과거 블록은
  `docs/archive/CONTEXT-history.md`로 verbatim, 새 블록 추가 시 밀려나는 블록을
  그 파일 상단으로 옮기는 규약). ② **죽은 소스 삭제 ~170파일**: `poc/` 전체
  (+`api-test.yml` `/platform` 발행 스텝 제거 — Pages 기발행 사본은 동결,
  오너 확인 후 purge; `scenario-viz-PLATFORM-PLAN.md`만 archive 이관) ·
  `tools/{sample_data,gantt_sim,loop_cycle}.py` · `drafts/compose_wave5/
  recompose_ib042.py` · `controlplane/static_export.py` · `console2/mockups/`.
  ③ **정보 단일화**: `docs/quotas-and-budgets.md`→`knowledge/quotas-and-budgets.md`
  병합, START_HERE/README/ARCHITECTURE stale 수치 실측 갱신(1,416 EP · 283노드/
  59파일), SPEC-DIFF-2026-07-09 superseded 처리, `spec/read_reachability.py`
  MD_OUT 경로 드리프트 수리. ④ **재판정 2건**: tracked `reports/runtime_*.json`
  유지(conformance/static.py의 live-confirmed 입력 — 구감사 R5 반려) ·
  `scr_docker_probe.py` 유지(CI gated 스텝 활성). 결정 기록:
  `docs/decisions/2026-07-20-repo-cleanup-archive-tier.md`, 집행 대장:
  REPO-AUDIT-2026-07-04.md §6. **다음 작업 큐는 아래 07-16 블록 그대로**
  (a690 실패 분석 + lb/asg/fw 판정 + durations 재확인).
- **PRIOR (2026-07-16 — 신규 테스트 계정, main=`c5393399`):** 오너가 계정을
  수동 정리 후 **전체 런 a690 진행 중** (127 lifecycles, 01:54Z~). 이 세션에서
  main 반영 완료: ① fe88 12건 실패 전원 원인확정+수리 (406 핀 클래스 3 ·
  zone 3 · resource_types 3 · scr 1 · fw 202-빈바디 1 · asg user 하드코딩 1 —
  lb/asg 2건은 다음 런 판정), ② durations 재보정 (성공 112건 fe88 실측 스팬
  대체, makespan 예측 55.9분), ③ 스윕 대개편: ledger-reclaim 레코드단위 프룬,
  로그그룹 병렬 전량 삭제, **블라스트**(프리스캔 캐시 소유 리프 전체 동시
  DELETE → 409 생존자만 의존순서 패스, SCP_SWEEP_BLAST=false로 끔), ④ a690
  조기 실패 2건 수리: lb-listener ACTIVE settle, vpn publicip zone 누락
  (라이브 201/204/404 검증), ⑤ gen-cloudml-chain waiver(blocked-owner, 오너
  지시). 카탈로그 1,416 (위 at-a-glance 갱신됨). 다음: a690 종료 시 실패
  분석 + lb/asg/fw 수리 판정 + durations 재확인.
- **HANDOFF (2026-07-13 — api-test-coverage-gzukh0 세션, 배치 ① 착수):** 인계된
  "①배치 코드로 즉시 노려볼 4xx"를 착수. **코드 수리 4종 반영**(전부 heavy라
  실 2xx는 다음 SCP_RUN_HEAVY 콘솔 런 판정; validate 0 err, offline 550 pass —
  기존 실패 2건 test_console2.fold/test_docs_index는 무관·기존):
  ① **mysql·postgresql setblockstoragesize** — create body가 OS 롤 block-storage-group
  만 만들어 resize 시 400 InvalidBlockStorageRoleType. subops-full 검증 패턴 이식:
  add-block-storages(DATA)→settle→bsg_data_id 재캡처→DATA 그룹 resize (scenarios.json
  database-mysql-cluster/database-postgresql-cluster; mysql엔 instance_group_id 캡처
  추가). ② **eventstreams showrequest** — es-create 202의 request_id를 es-wait
  직전에 `GET /v1/requests/{request_id}`로 즉시 read (async-FAIL해도 request 레코드
  존재 → 2xx; 유일 경로, list 미노출). ③ **cachestore set-commands** — modifiable
  아닌 maxmemory-policy 하드코딩 제거, listcommands에서 `where_prefix modifiable=true`
  실 커맨드 캡처→applied_value no-op 되돌림. ④ **filestorage setaccessrule**
  (오너 지시 "VM dependency 걸어 테스트") — 단독 lifecycle은 VM 0대라 404
  VirtualServerNotFound; **gen-heavy-vs-netops(ACTIVE VM 상비)에 filestorage NFS
  볼륨+setaccessrule(add/remove) optional 그룹 `fs-vm-access` 편입**, object_id=
  {server_id} 실 VM. 모든 filestorage 스텝 `service:filestorage` 필수(볼륨 경로가
  virtualserver와 충돌), 토큰 fs_volume_id. **라이브 read-only 재확인 → 블로커 확정
  2종**(코드 수리 불가): kms managed key `GET /v1/managed-kms/transit`→count:0(영구,
  create API 없음) · cloudmonitoring event-policy 400+등록 리소스 0(2026-09 단종,
  투자 금지). **미착수(사유별)**: DB patch-minor 5엔진=heavy create body 변경+C4
  오너 컨펌 대기 · apigw/scf privatelink 5키=AUTO-approval 상태머신 라이브 런 관찰
  트리아지 필요(블라인드 코드 불가). 상세: `knowledge/validated-facts.md` 2026-07-13
  배치① 블록. ⚠️ knowledge/validated-facts.md:2453 고아 병합마커(`>>>>>>>`) 제거함.
- **(과거 히스토리는 아카이브로)** 2026-06-10 ~ 2026-07-11 의 세션 로그 블록
  전부는 [`docs/archive/CONTEXT-history.md`](../archive/CONTEXT-history.md)로
  **verbatim 이동** (2026-07-20 리포 정리). 이 섹션은 "최신 CURRENT + 직전
  핸드오프 2~3개"만 유지한다 — 새 블록을 추가할 때 밀려나는 오래된 블록은
  히스토리 파일 **상단**에 옮겨 붙여라 (역시 verbatim, 최신이 위).

> When you finish a unit of work that changes any of the above, update this
> section (and the relevant `knowledge/` file) in the same commit.
