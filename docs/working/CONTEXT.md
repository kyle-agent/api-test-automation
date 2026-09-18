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

- **1,490 endpoints** (2026-09 spec bump, search-index discovery 2026-09-18), 1,490 resolved / 0 unresolved.
  13 categories · 62 services (신규: messagehub 29 · resourceoptimizer 24 · costnavigator 3 · cnapp 2; 폐기: cssdlan).
- By method: GET 570 · POST 408 · PUT 280 · DELETE 220 · PATCH 12.
- Smoke-testability split: **238** GETs are directly testable (no path params);
  **332** GETs need a resource id (reached via CRUD/read-chains); **920** are
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

- **CURRENT (2026-09-18 — 신규 API 모수 편입, 오너 "신규 API 모수 작업 시작"):** 문서 사이트
  리디자인 대응으로 `spec.extract_catalog`를 search-index 기반으로 교체 → 카탈로그 **1,417→1,490**
  (신규 74: messagehub 29 · resourceoptimizer 24 · ske 5 · servicewatch 4 · costnavigator 3 ·
  organization 3 · budget 2 · cnapp 2 · baremetal 1 · vpc 1; 버전업 881; cssdlan 폐기). 바디
  `--from-index` 재추출 477/477, `spec.refresh_versions` 신설로 버전 핀 29개 상향(다음 런에서 406
  여부 판정 — 뒤지면 PIN=false). http_client 버전 매칭 구체성 규칙 수리. api_docs.json 재스크레이프
  + coverage-service agent 3개(messagehub · resourceoptimizer · 기타 신규 15)로 lifecycle 작성 중.
  다음: 신규 lifecycle 검증 런 · 갭 153→? 재측정 · 버전 핀 실효 판정.
- **CURRENT (2026-09-17 — run 89de 존 핀 stale + 존 가드, 스펙 사이트 리디자인):**
  오늘 런 `20260917-085140-89de`(85 pass / 33 fail, 진행 중) 실패의 대부분이 오너 `.env`의
  stale `SCP_ZONE=kr-west1-a`(8/1 캠페인 잔재) — 계정에서 `-a` 무효(`GET filestorage
  /v1/replications/zones` 실측 `[]`, `-b`는 `['kr-east1-a']`). 조치: `.env` 핀 제거/`-b`.
  **존 가드 신설** `regression/scenarios/zone_guard.py` — read-only 프로브로 `{zone}` 검증,
  형제 존만 유효하면 차단(콘솔2 pre-flight·REAL 런 시작·`shared_infra --provision` 3곳,
  `SCP_ZONE_CHECK=warn|false`). 89de 최종(124 lifecycle, 91 pass / 33 fail) 아티팩트 판정:
  존 클래스 14(서버 `InvalidServerType.Zone`·볼륨 `InvalidAvailabilityZone`/`Invalid.zone`·
  publicip `ip.invalid-zone`·ASG `ZonesSubnetMismatch`) + **DC 2건 회귀**: 8/20 수리가 넣은
  uplink_active/standby_zone을 단일존 VPC가 `active-standby-zone-not-allowed`로 거부 → **수리**:
  엔진 `fallback_on_error_code` 신설, DC create 3곳은 존 없이 보내고 required-zone일 때만 재전송.
  **DBaaS 13건 = 신규 계약**: `Dbaas.ValidationError.
  InvalidBlockStorageDataDiskCount` "ACTIVE instance group must have 1 or more DATA block
  storage (current: 0)" — mysql/postgresql/mariadb/epas create 바디에 DATA 블록스토리지 필수화
  → **수리**: 4엔진 create 32곳에 DATA 56 SSD 추가. **재런 915f 최종: 120 pass / 4 fail** (89de 91/33) —
  존 에러 0 · DBaaS create 14/14 202 + add/resize 정상 · DC 기본 바디 202(fallback 미발동, 단일존). ske 실패 = k8s 목록이 밀려([1]=v1.35.5)
  하드코딩 ubuntu 22.04가 무효(오너 지적) → list-images에서 OS 버전 캡처로 수리. 기타: secretsmanager create 400
  `check-namespace-error` ×2(신규) · scr quota 403(기지) · gen-vpc-endpoint 409 직렬화(기지) ·
  lb-members static-nats 404. 개선 36 / 회귀 55 vs 3e67 (`tools.triage_run --diff`).
  스펙: 문서 사이트 리디자인으로 `spec.extract_catalog --fresh` **금지**(카탈로그 1개로
  덮어씀) — changelog 기반 부분 diff `docs/working/SPEC-DIFF-20260917.md` (cssdlan 폐기,
  messagehub 29 EP 신설, 25 서비스 버전업). 다음: 89de 아티팩트 판정 · discovery 재설계
  (search-index.json) · messagehub 커버리지.
- **PRIOR (2026-08-01 — s2 오퍼링 업그레이드 전/후 캠페인, 오너 콘솔 반복 런):**
  오퍼링(west1, 존 `-a`, 서버타입 s2/db2/ess2 세대) 부분런 반복 중 — env 레시피
  정본은 `environments/README.md` 전환 체크리스트 (oplog 3종 · PIN=false ·
  SCP_ZONE · SCP_VS_SERVER_TYPE_PREFIX=s2 · DB 이름 핀). 금일 main 반영 2건:
  ① `31bf5e78` 서버타입/존 이식성 — VS 캡처 8곳 min_by(첫 매치가 s2v10m120을
  집던 결함), `{vs_server_type}` 토큰 신설(리터럴 create 6곳+billingplan 견적 2+
  resize 제외목록 1; PREFIX에서 s2→s2v1m2 유도), `{region}-b` 준-리터럴 14곳→
  `{zone}`, 리터럴 가드 테스트 2종. (VM 400 InvalidServerType.Zone "모순 메시지"는
  오너 .env 존 값 이상으로 판명 — PF 미등재.) ② run 3ebe 실측 **PF-53**: 같은 초
  병렬 POST /v1/clusters 5발 중 2발만 400 `Dbaas.RbacCreateError` "Try again."
  (서버측 RBAC 프로비저닝 동시성 레이스의 400 오분류) → 엔진
  `retry_on_error_code` 신설(바디 코드 일치시만 재시도, ±25% 지터로 재충돌 방지)
  + DBaaS-패밀리 create 19곳 장착. **다음 런 판정 큐**: RbacCreateError 사다리
  실효 · database-postgresql-cluster 실패 원인(생성 1 후 ~4분 내 cleanup —
  artifact 대기) · PF-49/50/51 지속 여부 · scr borrow 사다리 · hc 500 사다리 ·
  TGW fw 사다리 · gen-vpc-endpoint 409 직렬화(미수리). 업그레이드 전/후 비교
  리포트는 양쪽 풀런 완료 시 (계정 픽스처 클래스 제외: gen-wave3-support 404 ×2 ·
  rm tag-rg 403).
- **PRIOR (2026-07-29 — 리포 전체 정리, branch `claude/repository-cleanup-bqil1u`,
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
  `docs/decisions/2026-07-29-repo-cleanup-archive-tier.md`, 집행 대장:
  REPO-AUDIT-2026-07-04.md §6. ⑤ **런 bd2a(20260729-081847, 124 lifecycle
  121pass) 트리아지**: fail 3 원인 확정 — (a) scr-repo-borrow 쿼터 레이스의
  진짜 뿌리 = 형제 container-scr-registry의 **public-endpoint PUT이
  NON_VISIBILITY 레지스트리를 VISIBILITY 레인으로 편입**(가시성 레인 분리 붕괴,
  실측 타임라인) → 사다리 45s×6→×11(8.3분) + 최종 403 expect 편입(쿼터=skip)
  수리; (b) gen-newapi-addressgroup·security-kms-transit-crypto의 create 500
  ContactAdmin = 2026-07 버전업 신설 API의 서버측 미개통 의심 → **PF-49/50**
  (동종: quick-query 500·PLE approval 500); (c) DBaaS kernel/major-upgrade
  PUT 405 ×7 = 과거 PUT 2xx였던 op의 메서드 회귀 → **PF-51** (문서는 여전히
  PUT). 부수 관측: heavy-shared-networking private-dns 400 max-count(캡 3 소진
  — 계정 잔존 사전 확인 필요) · filestorage-volume setaccessrule 404는 배치①
  편입 전 잔재 스텝. **다음 작업 큐는 아래 07-16 블록 그대로**
  (a690 실패 분석 + lb/asg/fw 판정 + durations 재확인) + bd2a 수리/PF 3건
  다음 런 재확인.
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
- **(과거 히스토리는 아카이브로)** 2026-06-10 ~ 2026-07-11 의 세션 로그 블록
  전부는 [`docs/archive/CONTEXT-history.md`](../archive/CONTEXT-history.md)로
  **verbatim 이동** (2026-07-29 리포 정리). 이 섹션은 "최신 CURRENT + 직전
  핸드오프 2~3개"만 유지한다 — 새 블록을 추가할 때 밀려나는 오래된 블록은
  히스토리 파일 **상단**에 옮겨 붙여라 (역시 verbatim, 최신이 위).

> When you finish a unit of work that changes any of the above, update this
> section (and the relevant `knowledge/` file) in the same commit.
