---
status: live (세션 연속성용 — 매 사이클 갱신)
updated: 2026-07-10
---

# V2 진행 상태 — 재개용 스냅샷

> 세션/컨텍스트가 리셋돼도 이 문서 + V2-DECISIONS + V2-L1-DATA-CONTRACT +
> V2-EXPERIMENT-LOG만 읽으면 이어갈 수 있게 유지한다.

## 완료된 화면 (전부 지정 브랜치에 커밋·푸시됨)

- **/v2 셸**: 6축 네비(Overview/Services/Model/Runs/Results/Tools), 용어 상수
  (terms.py — D7 개정: 이름 영어·설명 한국어), 출처 배지 매크로 3종+노후,
  발행 메타 로더(published.py). app.py 접점은 include 2줄뿐.
- **Overview(현황)**: 판정 헤드라인(발행본 단일, 판정 시각 분리 표기), KPI 4장,
  병합 런 타임라인, Runs on this server 격리 섹션 + fold 안내.
- **Services**: 목록(커버리지 오름차순·검색·Untestable 배지) + 상세(개요 KPI,
  엔드포인트 7컬럼 표+필터, Run this service ↗ prefill 핸드오프, Published
  page ↗ 공유 링크). dashboard.build 순수 함수 재사용(services_data.py).
- **Results**: 회귀 헤드라인, New regressions 상세(발행 배너 파싱 임시 우회 —
  엔진 요청 #1로 대체 예정), 당시/현재 분리, Conformance changes,
  Known issues 접힘 목록(맨 아래).
- 테스트: controlplane/v2/tests_offline.py (15종) + 기존 21종 무손상 유지.

## 진행 중 / 다음 큐 (2026-07-10 병렬 배치 1 완료 반영)

완료 추가: Runs 축 v1(발사 없음) · Model 축 · 런 상세(/v2/runs/{id} + fold
동선) · 전역 검색(/v2/search + 헤더 ⌕) · 용어 가이드(V2-TERMS-GUIDE.md).
6축 전부 실화면(Tools만 링크 모음 스텁). 페르소나 결함 누적 34건 수정.

완료 추가(2026-07-10 후반): 패리티 리디자인 3종(서비스 상세=발행 패리티,
Model=카테고리▸서비스 계층, Runs=선택→DAG→견적→pre-flight) + L2(그래프
표면 2곳: Runs 계획 DAG · 서비스 상세 의존 인스펙터 — D6대로 Model 그래프
없음) + main 병합(6fae7db3). 테스트 30종.

1. **v2 자체 발사([Run live] 활성화) — 오너 검수 게이트** (§2.6, 활성화 1줄).
2. **L3 네비 전환 + 리다이렉트 — G3 오너 게이트** (계획안 문서 작성 후 승인 요청).
3. L4 legacy 흡수(ops.html 등) — 이식 검증 전 제거 금지.
4. 잔존 자원 수집 캐시(M5) — 현재 Overview KPI는 legacy 뷰 링크만.
5. 커버리지 오버레이(색칠지도 대체) — Results 후속 결정 지점.

## 운영 메모

- 서버: `PYTHONPATH=. python3 -m uvicorn controlplane.app:app --port 8800`
  → http://127.0.0.1:8800/v2 (원격 세션이라 오너는 스크린샷/데모로 검수).
- 데모(클릭 가능): https://claude.ai/code/artifact/2f7ba4c0-d9b6-41e0-b03b-ca5c85671528
  — 갱신은 scratchpad의 v2-demo.html 재생성 후 같은 경로로 Artifact 재발행.
- 검수 파이프라인 표준: 계약 확인 → 위임 구현(Sonnet) → offline 테스트 →
  스크린샷 셀프체크 → 페르소나 3인(P1/P2/P3) → PM 통합 → 커밋 → 데모 갱신.
- 오너 확정 결정: V2-DECISIONS.md (D2·D4보강·D6·D7개정·pre-flight).
- 기존 세션 요청: V2-REQUESTS-TO-ENGINE.md (#1 fail_new.json, #2 로컬 run-id,
  #4 발행 산출물 판정 일관성, #3 발행 대시보드 표기 정렬은 오너 재확인 후).
- 실험 기록(포스트모템 원료): V2-EXPERIMENT-LOG.md (E0~E3 기입됨).
