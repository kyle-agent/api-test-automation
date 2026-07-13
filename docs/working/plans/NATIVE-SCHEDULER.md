---
status: draft
for: orchestrator
date: 2026-07-13
---

<!-- 진행: V1 구현 완료(opt-in), 라이브 검증 진행 중 → 통과하면 status: active로 승격 -->


# 목적특화 스케줄러 (native_runner) — xdist 대체

> 오너 지시(2026-07-13): "xdist 워크라운드 말고 제대로 고민해. xdist는 가져다 쓴
> 거지?" → 맞다. pytest-xdist는 CPU-bound 테스트 병렬용 범용 라이브러리인데 우리
> 워크로드는 I/O-bound 라이프사이클(의존성·쿼터·async 대기)이라 미스매치.
> engine.run_lifecycle이 이미 pytest와 분리돼 있어, 얇은 스레드풀 스케줄러로 직접
> 구동한다.

## 왜 (xdist의 4가지 한계 = 우리가 우회하던 것)
| 한계 | 우리가 하던 워크라운드 | native가 네이티브 해결 |
|---|---|---|
| 수집순서 디스패치(LPT 무지) | conftest _order_for_load/interleave | 동적 LPT pop |
| MIN_PENDING 버퍼 + 유휴 워커 종료 → **꼬리 붕괴** | strand 제거, MIN_PENDING=1 제안 | 큐 빌 때까지 워커 유지 |
| 쿼터 per-worker(프로세스) → **400 레이스** | (못 고침) | **공유 Budget** = 계정-전역 조율 |
| 의존성 무지 | dependent 후미 인코딩 | dependent 후미 + (V2) 하드 게이트 |

## 시뮬레이션 검증 (`python -m tools.scheduler_sim`, 실 durations)
| 스케줄러 | makespan | 쿼터400 | 활성워커 25%/90% |
|---|---|---|---|
| **native** | **70.1분** | **0** | 30/4 |
| xdist-load | 89.9분 | 4 | 15/1 (조기 붕괴) |

native가 ~20분 빠르고, 쿼터400=0, 꼬리에서 워커 안 죽음.

## 설계 (V1)
- `regression/scenarios/native_runner.py`
  - `priority_order(lcs)` = (dependent asc, duration desc) — no-dep 무거운 것 먼저.
  - `run(ids, workers)` — cfg/client + `provision_shared_vpc`(env-aware) + **공유
    Budget(스레드-안전)** + 공유 ResourceRegistry. N 스레드가 LPT 큐에서 pop,
    `engine.run_lifecycle(..., budget=공유, resource_registry=공유, shared_ctx=...)`
    호출. 큐 빌 때만 워커 종료.
  - `main()` = CLI (SCP_CRUD_IDS·게이트·SCP_CONSOLE_EVENTS env 계약, pytest와 동일).
- `core/budgets.py` Budget에 **RLock** 추가 (reserve check-then-increment 원자화).
- **opt-in**: `SCP_NATIVE_RUNNER=true`면 local_run/console2가 pytest-xdist 대신
  `python -m regression.scenarios.native_runner`를 Popen(별도 프로세스라 중단버튼
  kill 동일). **xdist 경로는 폴백 유지** — 라이브 검증 후 기본화.
- 정렬 해킹(_order_for_load/interleave/roundrobin)은 **xdist 전용** — native엔 불필요
  (동적 pop이 버퍼-갇힘을 원천 제거). xdist 폴백용으로 conftest에 보존.

## 검증 상태
- ✅ 시뮬레이션 (scheduler_sim)
- ✅ offline (`tests/offline/test_native_runner.py` 3종: LPT 순서·전량완료+동시성
  유지(붕괴 없음)·공유 Budget 쿼터 조율 스레드-안전) + Budget 스레드-안전 스모크
- ⏳ **라이브 검증 대기** (오너): `SCP_NATIVE_RUNNER=true`로 콘솔 런 → makespan·
  꼬리 병렬성·쿼터400·자원 정합성 확인. 통과하면 기본화 검토.

## V2 백로그 (V1엔 미포함)
- **하드 의존성 게이트**: dependent를 provider 자원 kind가 실제 생길 때까지 dispatch
  보류 (공유 registry 조회). 지금은 순서(soft)만.
- **duration-learning 패리티**: pytest_sessionfinish가 하던 durations fold를 러너가
  측정한 per-task elapsed로 대체 (LPT 정확도 유지). analyze_run Popen도.
- **asyncio 전환**: 스레드풀 대신 asyncio (동시성 100+도 가볍게 — I/O-bound라 적합).
- **live quota sync**: run 시작 시 계정 실사용을 Budget.sync로 시드 (잔재 반영).
