---
status: draft
for: orchestrator
date: 2026-07-13
---

<!-- 진행: 설계 확정(오너 검토), 미구현. 라이브 런 종료 후 착수 예정(Rule #4). -->

# 런 중 시나리오 동적 주입 (native_runner) — 설계

> 오너 지시(2026-07-13): "run 중에 중간에 시나리오를 추가해서 돌리는 게 가능할지
> 설계 확인. 공유 VPC 활용이면 큐 마지막에, 신규 VPC면 큐에 넣었다 세마포어 타면
> 되고 등등." → **가능. VPC 로직은 손 안 대고(세마포어가 이미 정답), 핵심은
> ①closeable 워커 풀 ②주입 채널 두 개.**

## 결론

| 부분 | 상태 | 작업량 |
|---|---|---|
| 공유 VPC 시나리오 주입 | ✅ 그냥 됨 (append) | 0 |
| 신규 VPC 시나리오 주입 (세마포어 게이트) | ✅ 이미 게이트됨 | 0 |
| 워커 풀이 빈 큐를 견디게 (closeable) | ⚠️ 리팩터 필요 | 소~중 (리스크: close 조건) |
| 주입 채널 (`add_scenario`) | 🔌 기존 `core.commands` 확장 | 소 |
| 콘솔 주입 UI | 별도(콘솔측) | 소 |

## 이미 되는 것 — 오너 모델 그대로

`native_runner.run()`은 이미 **공유 Budget(kind별 세마포어) + 공유 shared_ctx +
공유 ResourceRegistry**를 모든 워커가 나눠 쓴다. 그래서:

- **공유 VPC 시나리오 → 큐에 append**: 워커가 비면 pop해서 실행. adopt는
  `run_lifecycle(..., shared_ctx=shared_ctx)`로 이미 동작 → **추가 코드 0**.
- **신규 VPC(self-create) 시나리오 → 큐에 넣고 세마포어**: 주입만 하면 시드된
  Budget 세마포어 + `reserve` + **대기-재실행**이 슬롯을 게이트한다. 여유 없으면
  대기했다 실행. 세마포어는 워크로드-무관이라 "런 시작 배치"든 "중간 주입"이든
  동일하게 계정 VPC 캡(5)을 지킴 → **VPC 전용 코드 0**.

즉 오너가 말한 "공유면 끝에, 신규면 세마포어 타면 됨"은 **VPC 특수 처리 없이 그냥
큐에 넣기만 하면** 성립한다.

## 유일한 블로커 — 워커가 빈 큐에 종료

```python
def worker(wid):
    while True:
        with lock:
            if not queue:
                return          # ← 배치 모드 가정: 드레인되면 워커 죽음
            lc = queue.pop(0)
        ...
```

지금은 "큐가 처음에 다 차 있다"는 전제라, 주입이 오기 전에 큐가 비면 워커가 전부
종료해 집을 데가 없다. 주입을 받으려면 **워커 풀이 일시적 빈 큐를 견뎌야** 한다.

**해결**: closeable 큐로 전환 —
- `list + lock` → `queue.Queue`(+ 워커당 poison-sentinel), 또는 `list +
  threading.Condition`.
- 빈 큐면 워커는 **종료가 아니라 대기**. `closed` 플래그가 서고 **AND** 큐가 빌
  때만 종료.
- **주의(리스크 포인트)**: close 조건을 정확히. 틀리면 (a) 런이 안 끝남(영구 대기)
  또는 (b) 주입 전 조기 종료로 작업 유실. offline로 close-semantics를 반드시 검증.

## 주입 채널 — 기존 `core.commands` 확장

콘솔→런 명령 채널이 이미 HTTP pull로 있다(`GET
{APITEST_PLATFORM_URL}/api/runs/{id}/commands` → abort/skip/stop_polling, ack).
여기에 액션 하나 추가:

- 콘솔이 `{action:"add_scenario", target:"<lc-id>"}` post.
- 러너의 injector(메인 루프 또는 전용 스레드)가 폴 시 `core.commands.pending_
  additions()`로 받아 → `active_lifecycles()`로 resolve → `queue.put`.
- `APITEST_PLATFORM_URL` 필요(이미 콘솔 메커니즘).

**로컬 간이 대안**: `data/coordination/inject.jsonl`을 injector 스레드가 tail →
새 줄의 id를 enqueue. 채널 재활용이 일관적이지만, 로컬 검증엔 파일 감시가 더 쉽다.

## 그 외 고려사항 (다 작음)

- 진행 카운터 `[i/len(ordered)]`가 stale → 동적 total(표시만).
- 주입 id는 `active_lifecycles`로 resolve + 이미 돈 것과 dedup(중복 주입 방지).
- `run_lifecycle`은 라이프사이클당 stateless(공유 budget/registry/ctx) → 임의
  시나리오 동시 실행 안전.
- teardown은 모든 워커 join 후 → 주입이 런을 자연스럽게 연장, teardown 타이밍 정상.
- 주입 실패(잘못된 id 등)는 개별 result로 기록하고 러너를 죽이지 않음(기존 except 패턴).

## 구현 스케치 (V1)

1. **native_runner**: `queue = list(ordered)` → `q = queue.Queue()`에 초기 배치
   put. worker는 `q.get()`(빈 큐면 블록). `close()`는 워커 수만큼 sentinel put.
   메인은 (초기 배치 소진 + 주입 채널 닫힘)일 때 close.
2. **injector**: 별도 스레드가 주기적으로 `pending_additions()` 폴 → resolve →
   `q.put`. 채널이 "런 종료 의사"를 주면 메인에 신호.
3. **core.commands**: `add_scenario` 액션 파싱 + `pending_additions()` predicate
   (consumed 관리, 기존 should_skip과 동형).
4. **게이트**: 없음 — VPC는 세마포어, mutations/heavy는 env 게이트가 이미 강제.
5. **플래그**: `SCP_NATIVE_DYNAMIC=true` 뒤에 두고 기본 off(배치 모드 유지).

## 테스트 계획 (offline)

- inject-then-drain: 초기 배치 소진 후 주입 → 워커가 집어 실행, 전량 완료.
- close-semantics: 채널 닫힘 + 큐 빔 → 모든 워커 정상 종료(행 없음, 조기 종료 없음).
- 신규 VPC 주입 → 세마포어 여유 없으면 대기, 슬롯 나면 실행(기존 대기-재실행 재사용).
- 중복 주입 dedup.

## 리스크 / 주의

- close 조건 정확성이 유일한 실질 리스크(행/유실). offline 필수.
- 라이브 런 도중 구현/배포 금지 — Rule #4(한 번에 워크플로 하나). 런 종료 후 착수.
- 새 기능이므로 자체 플래그로 격리, xdist 폴백/배치 모드는 무변경.
