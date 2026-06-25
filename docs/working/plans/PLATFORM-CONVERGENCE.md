---
status: draft (오너 결정 반영 — 2026-06-25)
for: owner + platform
supersedes-direction: docs/working/plans/PLATFORM-IA-DIRECTION.md (방향 확정분의 실행 계획)
---

# 수렴 계획 — console2 → controlplane (척추 흡수)

> **결정(owner, 2026-06-25):** 면 1 = **controlplane을 척추로** 두고 **console2를 흡수**.
> 면 2 = 공개 커버리지 대시보드 별도 유지. console2가 풀려던 **"실제 실행 연결"**과
> 그 IA/CX를 **보존·이식**하는 게 본질(시각 효과만이 아니라).

## 0. 한 줄

console2는 "실제 실행과 연결"을 위해 새로 정의한 IA/CX다. controlplane은 저작·디스패치·
라이브추적의 척추다. **둘은 같은 엔진(composer · dag_planner · 이벤트 어휘)을 공유**하므로,
재작성이 아니라 **console2의 실행 루프를 controlplane 안으로 배선**하면 된다.

## 1. 왜 "재작성"이 아니라 "수렴"인가 — 이미 공유된 이음매

| 이음매 | 공유 사실 |
|--------|-----------|
| **그래프/폐포** | 양쪽 다 `composer.graph_view / focus_view`(순수 함수). 렌더러: console2 `resource_graph.js`(=graph.js **포트** + `scene()`) · controlplane `graph.js`(코어) — **같은 코어**. (옛 `viz.js`는 죽은 코드 — console2가 로드 안 함) |
| **실행 계획** | 양쪽 다 `dag_planner` 웨이브(provision→free→adopt→self-create) |
| **이벤트 어휘** | 엔진이 `lifecycle-start/step-start/step-end/resource-tracked/lifecycle-end`를 **두 채널**(`console_events` 로컬 · `oplog` 클라우드)에 동일 형태로 emit |
| **이미 구현분** | `PLATFORM-PLAN.md` P0~P4: graph.json·graph.js·정적 catalog/plan/run/report 완료 |

→ 남은 건 **렌더러 1개로 통일** + **console2의 로컬 실행 루프를 controlplane의 실행 모드로 편입** + **CX 이식**.

## 2. 핵심 — "실제 실행 연결"을 어떻게 보존하나

두 앱의 실행 배선이 다르다(이게 console2가 생긴 이유):

| | console2 | controlplane |
|--|----------|--------------|
| 실행 | **로컬 인프로세스**: `shared_infra --provision` → `pytest tests/crud`(선택 lifecycle) → `--teardown` | **원격 디스패치**: `actions`(api-test.yml) / `worker` 큐 |
| 라이브 | `core.console_events` 로컬 JSONL tail | `core.oplog` 클라우드 미러 ingest |
| 성격 | 즉시·인터랙티브·개발 | 비동기·확장·운영 |

**결정(추천): console2의 로컬 러너를 controlplane의 세 번째 실행 모드로 등록한다.**
- `PLATFORM_EXECUTOR`에 이미 `actions | worker`가 있음 → **`local`(=console2 파이프라인) 추가**.
- `local` 모드면 controlplane이 `SCP_CONSOLE_EVENTS`를 per-run 파일로 세팅하고 tail —
  **console2와 동일한 라이브 DAG 뷰**. `actions/worker` 모드면 기존 oplog 추적 그대로.
- Run 단계 = **`simulate | local-live | dispatch(actions) | worker`** 선택. console2의
  "즉시 로컬 실행 + step별 라이브 리포트" CX가 **그대로 보존**된다.

## 3. console2 → controlplane 매핑 (무엇을 어디로)

| console2 (흡수원) | controlplane (목적지) |
|--------------------|------------------------|
| 4-stage: 선택 → Plan | Plan 스테퍼의 **Compose**(이미 선택/합성 있음)에 그래프 캔버스 얹기 |
| 실행(simulate/live, 게이트 confirm) | **Run** 단계 + §2의 `local` 실행 모드 |
| 리포트 R1 진행/R2 리소스/R3 API/R4 로그 | **Report** 단계(현재 dashboard 임베드)에 런별 4탭 추가 |
| `resource_graph.js` `scene()`(줌·그룹·collapse·focus·pan) | `graph.js`에 `scene()` 병합 → **테마 가능한 공용 렌더러 1개** |
| 강제 클린업/소유 인벤토리 검증 | controlplane M2 인벤토리/reconciler와 합치기 |

## 3.1 console2에서 보존할 설계 (검토 완료 — 오너 2026-06-25 재확인)

> 출처: `docs/working/console2-ia-ux-review.md`(IA/UX 리뷰), `docs/working/console-platform-handoff.md`
> (로컬 실행 서버 계보), 실제 `console2/assets/resource_graph.js` · `tools/console2_server.py`.
> 오너 지시: **"console2의 실행은 여러 차례 검토한 것 — 그 내용이 반영돼야 함."**

**북극성(조직 원리): "한 객체, 세 삶".** 합성 DAG는 plan·live-run·coverage에서 *같은* 객체이고 색만
바뀐다 — 고를 때=의도, 돌 때=진행, 분석할 때=커버리지. (S1a `lifecycle_states` + graph.js `overlay()` =
"진행" 색의 배선.)

**(A) DAG 기능 — 이미 구현됨(`resource_graph.js` `scene()`; 리뷰 B2 DONE `6f5ae673`). S1b는 보존이 목표:**
- category별 그룹 + **collapse-to-service**(클수록 기본 접힘) · ▸/▾ 전체 접기/펼치기
- service별 / resource별 / **레벨(생성순서 depth)별** 표시
- **focus-on-click**(의존 경로 강조, 나머지 dim) · **zoom +/−/맞춤/휠** · drag-to-pan
- 그림 ⇄ 표(순서) ⇄ 웨이브 동등 토글 · transitive reduction 유지

**(B) 실행/큐 모델 — 오너가 명시한 흐름 + 현재 구현 상태(정직하게):**
1. 단위 서비스 **또는 서비스 조합** 선택 → **총 의존관계 합성**(closure) — ✅ *구현됨*(composer.graph_view)
2. 합성 결과를 **실행 큐에 담기** — 🔸 *부분*(staged 스냅샷 `{selection, peak_vpcs, …}`); 완전한
   "큐 = Runs 리스트의 상태 + **여러 큐**"는 **리뷰 B5 backlog(미구현)** → 수렴 중 **완성 대상**
3. 큐(=합쳐진 DAG)를 **실행** — ✅ *구현됨*(`console2_server` `_spawn_run`: simulate | live)
4. **다른 큐도 같이 실행** — 🔸 다중 큐 동시 실행은 B5와 함께 설계
5. **전 과정 VPC 개수 제한 확인** — ✅ *구현됨*(선택별 `peak_vpcs` + `_run_peak_vpcs` 예약 게이트가
   simulate·live 모두 차단; `core.budgets` 한도 연동)

**실행 파이프라인(계보, S2가 이식):** `_provision_shared`(shared VPC) → `pytest tests/crud -m crud -n N`
(`SCP_CRUD_IDS`, 게이트는 **사용자 실행 클릭 시 그 요청 한정** opt-in) → `_teardown_shared`(그 VPC만 정확
삭제) → reconciler sweep.

**면 2 관련(나중):** 리뷰 §5에 status.claude.com 스타일 **스케줄 회귀 history 대시보드**(공개·다수 열람
= 면 2)가 이미 스케치됨 — `reports/results/*.jsonl` 투영. **deferred.**

## 4. 단계 (작게 · C4-safe: draft-only, 자동 enable 금지, 한 번에 한 앱)

- **S1 · 렌더러/이벤트 이음매 통일** (위험 낮음, 기반) — **S1a 이벤트 어댑터 ✅완료**
  (`core/events_contract.py`, `9962557d`). **S1b**: `resource_graph.js`의 `scene()`를 `graph.js`로
  병합해 **테마 가능한 공용 렌더러 1개**로, console2를 그걸 쓰게 전환, **죽은 `viz.js` 제거**(§3.1 A 보존).
- **S2 · `local` 실행 모드 추가** (실제 실행 연결의 핵심) — console2의 provision→pytest→teardown를
  controlplane `dispatch.py`에 `local` executor로 이식, `SCP_CONSOLE_EVENTS` tail 배선.
- **S3 · CX 이식** — Compose에 선택/Plan 그래프, Run에 simulate/local-live + 라이브 DAG,
  Report에 R1~R4. (controlplane 셸·네비 유지)
- **S4 · console2 은퇴** — 중복(실행/리포트/렌더러) 제거. 정적 데모 스냅샷만 Pages에 남길지 결정.
- 각 단계 종료 시 **console2와 동작 동치** 확인. 안전 게이트는 console2의 명시 opt-in 그대로 이식.

## 5. 확인 필요 (시작 전)

1. **실행 모델** — §2 "`local` 실행 모드 추가"로 console2의 실제-실행 연결을 보존하는 방향, 맞나?
2. **시작점** — **S1(이음매 통일)부터** 추천(기반이 깔려야 S2/S3가 깨끗). 아니면 S2(`local`
   실행)부터 가서 "실제 실행 연결"을 먼저 손에 잡히게 할지.
