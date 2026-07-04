---
status: ACTIVE campaign (2026-07-04, owner-directed autonomous run)
for: orchestrator + all campaign agents (다른 세션이 이어받을 때 이 문서가 진입점)
---

# CAMPAIGN — C3 100% · 플랫폼 dogfood 개선 · 리포 정비 (3 워크스트림 병렬)

> 오너 지시 (2026-07-04): ① C3 커버리지 100% (수단 자율 — SCP 문서, 축적 지식,
> 서비스별 병렬), ② 테스트는 **플랫폼 자체 기능으로** 실행하며 (VPC cap 전략 필요)
> 발견되는 결함/불편을 플랫폼 개선에 반영 (테스터+개선자 페르소나), ③ 문서·소스
> 정비 — 1차: 일관성 + 미사용 정리, 2차: 내용 정비 (과상세→목적 중심 추상화로 AI
> 자율성 확보, 부족한 곳은 상세 보강). 최대 병렬, 오케스트레이터가 조율, 방법론을
> 문서로 남길 것.

## 조율 규칙 (오케스트레이터 계약 — 세션 불문 유지)

1. **라이브 레인은 하나** — 로컬(플랫폼 콘솔) 또는 CI(chat-heavy) 중 한 시점에 한
   run. 디스패치 게이트: owned==0 + ~5min audit 정적 (+ 이전 run 스윕 종결).
2. **fold 직후 owned==0 재검증** (2026-07-04 규칙 — leak 1일 방치 재발 방지).
3. **VPC cap 5**: shared-adopt 1 + 자체생성 lifecycle 최악 조합 ≤ cap.
   heavy DB/VM 계열은 클러스터/서버 quota도 감안 — 배치당 병렬 4 이하 권장.
4. **증거 원칙**: 2xx만 verified (`tools/derive_verified`) → 자동 승격
   (`tools/promote_validated --apply`, service-스코프). 4xx 도달은 soft — 승격 불가.
5. **계정상 불가는 쫓지 말 것**: gated 34 노드 + 표준 waiver 절차 (아래 D). C3
   100% = "달성가능 전부 2xx + 나머지는 심사된 waiver"로 정의 (분모 조작 금지).
6. **에이전트 파일 규율**: 명시 경로 커밋 (`git add -A` 금지), push 전
   `pull --rebase --autostash`, .github/ 은 오케스트레이터 전용.
7. **워크스트림 간 파일 경계**: A=regression/scenarios/lifecycles/<service별 fragment>
   + knowledge/formal/resources/<service별> + data/baselines. B=console2/ +
   controlplane/ + tools/console2_server. C=docs/ (+ 소스 retire는 목록만 제안,
   삭제 실행은 오케스트레이터 승인 후). knowledge/validated-facts.md 와 CONTEXT.md
   는 append-only 충돌 규칙 (rebase 로 해소).

## 워크스트림 A — C3 100% (커버리지)

- **A0 기준선** (선행): C3 정의·분모·현재값을 dashboard 파이프라인으로 실측,
  남은 갭을 서비스×난이도로 분류: `L`(light, 콘솔 dogfood 대상) / `H`(heavy,
  CI chat-heavy 대상, VPC/quota 슬롯 명시) / `W`(window — 선행 자원 필요, 어느
  heavy run 에 동승할지) / `G`(gated/waiver 심사). 산출물: 이 문서의 §A 레저
  갱신 + 서비스별 실행 순서(슬롯 스케줄).
- **A1..An 서비스 에이전트**: coverage-service 표준 패턴 (독립 fragment 저작,
  compose→validate→오케스트레이터에 디스패치 블록 제출; 직접 디스패치 금지).
- 실행: L 배치는 플랫폼 콘솔로 (B 가 관찰), H 배치는 chat-heavy 로. fold →
  promote → 레저 갱신 매 배치.

## 워크스트림 B — dogfood 테스트 + 플랫폼 개선

- A 의 L 배치를 **콘솔 UI로 실행** (Playwright 페르소나 or API+UI 혼합), 매 run
  마다 마찰 일지 → 트래커(UIUX-AUDIT) 추가 → 소배치 수정 → main 반영 반복.
- 렌즈: default 적절성 · 0클릭 가시성 · 실시간 문제 인지 · 속도 체감(느림이
  설명되는가) · 라벨 오해 소지 (오너 2026-07-04 지정 5렌즈).

## 워크스트림 C — 리포 정비

- **C1 (1차)**: 전수 인벤토리 → (a) 죽은 문서/소스 retire 후보 목록 (증거:
  참조 0 + 최근 미변경 + 기능 대체됨), (b) 문서 간 모순 목록 (정본 우선),
  (c) INDEX/진입점 정합. 삭제는 오케스트레이터 승인 후 별도 커밋.
  - **C1 감사 DONE (2026-07-04)** →
    `docs/working/trackers/REPO-AUDIT-2026-07-04.md`: retire 후보 9묶음
    (핵심: poc/scenario-viz — 단 api-test.yml:1121 발행 스텝이 아직 소비, 교체
    선행 필요 · console_server.py · build_local_demo+local_run.html · 1회성
    publish *.sh · tracked reports/ 산출물 10파일), 모순 14건 중 10건 직접 수정
    (게이트 기본값 표 README/START_HERE/CONTEXT, README 트리거 절, controlplane
    README dispatch 주의, skills README 2건, 구 핸드오프 4+2건 supersede 헤더,
    INDEX 재생성). C2 목표 트리 표 = 감사 문서 §4. **삭제 실행 대기: §2 승인.**
- **C2 (2차)**: 내용 정비 — 과상세 문서를 "목적+계약+포인터"로 추상화 (AI 가
  자율 판단할 여지 확보), 부족한 곳 보강 (예: 운영 runbook 류). 정본 체계:
  PLATFORM-IA-DIRECTION(§확정 IA) > CONTEXT.md(현재 상태) > knowledge/(사실) >
  트래커/플랜(작업).

## 진행 로그 (오케스트레이터가 갱신)

- 2026-07-04: 캠페인 개시. A0 + C1 병렬 가동 (라이브 레인은 페르소나 2차 점유 중).
