# V2 용어 표기 가이드 (1페이지)

> 근거: `docs/working/plans/V2-DECISIONS.md` D7(2026-07-10 개정) ·
> `controlplane/v2/terms.py`(정본) · `CX-IA-DESIGN-2026-07-09.md` §4.0 원칙 2.

## 1. 원칙

1. **이름은 영어, 설명은 한국어** — 라벨(label)은 영어 원어, 정의 툴팁(tip)은 한국어 유지 (D7 개정).
2. **코드네임·모듈명·env명 화면 노출 금지** — 예: R2c, composer.graph_view, ANTHROPIC_API_KEY.
3. **정의 툴팁 의무** — 은어(약어·코드)가 처음 등장하는 요소에는 한 줄 한국어 정의를 붙인다.
4. **구어체·질문형·비유 금지** — 화면 문구는 격식 명사형만("상황실"X → "현황"O).
5. **"상태" 단독 한국어 라벨 금지** — 모델 상태 / 런 결과 / 커버리지로 분리해 쓴다.
6. **용어는 `controlplane/v2/terms.py`에서만** — 화면이 라벨 문자열을 직접 하드코딩하지 않는다.

## 2. 용어 사전 — `TERMS`

| Label(영어) | 코드 | 한국어 정의(툴팁 원문) | 사용 위치 예 |
|---|---|---|---|
| Verified | C3 | 검증(C3) = 실제 2xx 응답으로 동작을 확인함 | 커버리지 사다리 핵심 개념 (Model·Services 전반) |
| Called | C2 | 호출(C2) = 실제 호출이 이루어짐 (2xx 보장은 없음) | 커버리지 사다리 핵심 개념 (Model·Services 전반) |
| Reachable | C1 | 도달(C1) = 인증·라우팅을 통과해 API에 도달함 | 커버리지 사다리 핵심 개념 (Model·Services 전반) |
| Verified coverage | C3 | 공식 커버리지 지표 — 테스트 불가 서비스·면제를 제외한 분모 기준으로, 실제 2xx 응답으로 동작 확인(검증)된 비율 | Overview/Situation 공식 KPI |
| Called coverage | C2 | 전체 엔드포인트 중 실제 호출이 이루어진(응답을 받은, 4xx 포함) 비율 | Overview/Situation 보조 KPI |
| New regressions | — | 직전 발행까지는 없었는데 새로 실패한 항목 | Results 헤드라인 KPI |
| Known issues | — | 이미 알려져 추적 중인 실패 (베이스라인에 등록됨) | Results 헤드라인 KPI |
| Leftover resources | — | 테스트가 만들었다가 아직 정리되지 않은 클라우드 자원 — 비용·안전 사안 | Run/Situation 잔존 자원 패널 |
| Billable | — | 실행 시 실제 비용이 발생하는 자원/작업 | Run 사전 확인(pre-flight)/Heavy 선택 |
| Fold | — | 이 서버에서 확인된 결과를 검토를 거쳐 공식 집계(발행 기준 데이터)에 넣는 절차 | Results/Run "공식 반영" 동선 |
| Run result | — | 한 번의 실행이 남긴 성공/실패/스킵 판정 | Results/Run 상세 |
| Model state | — | 테스트 정의(리소스 모델)의 완성도 — 모델됨/검증됨 | Model 화면 |
| Verified (C3) | — | 검증(C3) = 지금까지의 런에서 2xx로 동작이 확인됨 (누적 판정 — 최근 status와 다를 수 있음) | Services 상세 엔드포인트 표의 커버 열 (L1 §2.2) |
| Reached | — | 도달 = 호출은 됐으나 2xx로 확인되지 않음(4xx 등) | Services 상세 엔드포인트 표의 커버 열 (L1 §2.2) |
| Failed | — | 실패 = 5xx 등 하드 실패가 관측됨 | Services 상세 엔드포인트 표의 커버 열 (L1 §2.2) |
| Not observed | — | 미관측 = 지금까지 어떤 런에서도 호출 기록이 없음 | Services 상세 엔드포인트 표의 커버 열 (L1 §2.2) |
| Defect | — | 결함 = 계약 위반 등 실제 구현 버그 | Services 상세 정합성(축2) 등급 |
| Improvement | — | 개선 = 설계·문서상 아쉬운 점(구현 버그는 아님) | Services 상세 정합성(축2) 등급 |
| New | — | 직전 발행 기준선(초록)에서 이번 발행에 새로 결함(빨강/노랑)으로 바뀐 엔드포인트 | Results 결과 축(⑤) 정합성 변화 (L1 §2.5) |
| Regressed | — | 이미 결함이던 항목이 더 심각한 등급으로 나빠짐(노랑→빨강) | Results 결과 축(⑤) 정합성 변화 (L1 §2.5) |
| Fixed | — | 이전에 결함이던 항목이 이번 발행에서 개선되어 등급이 좋아짐 | Results 결과 축(⑤) 정합성 변화 (L1 §2.5) |
| Untestable | — | 기능 테스트가 불가능한 서비스 — 사유는 배지 안내(title)에 표시 | Services 목록/상세 "기능 테스트 제외" 배지 |

부가 딕셔너리(`terms.py` 내 동일 정본, 형식만 다름):

| 딕셔너리 | 항목(영어 라벨) | 비고 |
|---|---|---|
| `RUN_STATUS` | Done · Failed · Running · Dispatched · Archived | 병합 런 타임라인 진행 상태. **tip(한국어 정의) 필드 없음** — §5 불일치 참고 |
| `NAV` | Overview · Services · Model · Runs · Results · Tools | 6축 네비. `q` 필드는 IA 설계용 한국어 "질문" 개념일 뿐 화면에 노출하지 않음 |

## 3. 출처 배지 3종 + stale 규칙 — `SOURCES`

| 배지 라벨(영어) | 색상(원칙1) | 한국어 정의(툴팁 원문) |
|---|---|---|
| Published | 파랑, `발행 @sha·시각` | 발행된 공식 집계 — 어느 서버에서 열어도 같은 값 |
| This server | 초록 점, `지금 이 서버` | 이 서버에서만 관측된 값 — 공식 집계와 다를 수 있음 |
| This run | 보라, `이 런 @run-id` | 특정 실행 1건에 고정된 값 (과거형) |

**Stale 규칙**: `Published` 배지의 발행본이 24h 이상 지나면 배지를 노랑으로 바꾸고 옆의 값도 회색화한다(값 자체는 생생한 검정으로 두지 않는다).

## 4. 신규 용어 추가 절차

1. `controlplane/v2/terms.py`의 해당 딕셔너리(`TERMS`/`SOURCES`/`RUN_STATUS`/`NAV`)에 `label`(영어)·`code`(있으면)·`tip`(한국어)을 추가한다.
2. 같은 커밋에서 이 가이드(`V2-TERMS-GUIDE.md`)의 표를 갱신한다.
3. 오너 검수를 통과한 뒤에만 실제 화면에 노출한다 — 검수 전 노출 금지.

## 5. terms.py와 대조해 발견한 불일치 (수정하지 않음, 보고만)

- `CX-IA-DESIGN-2026-07-09.md` §4.0 원칙 2-1이 "1급 화면 용어는 한국어(검증됨(C3) 등, 한국어 우선 + 코드 괄호 보조)"라는 **D7 개정 이전** 정책을 그대로 서술하고 있다. `V2-DECISIONS.md` D7은 이를 개정해 "이름=영어"로 뒤집었지만, CX-IA-DESIGN 문서 본문(§4.0)에는 개정 반영이나 각주가 없어 이 문서만 읽으면 구정책으로 오인할 수 있다.
- `V2-DECISIONS.md` D7이 예시로 든 KPI 라벨 "Coverage"가 `terms.py` `TERMS`에 독립 항목으로 없다(`cov_c3`="Verified coverage", `cov_called`="Called coverage"만 존재).
- `c3`/`c2`/`c1`(코드 필드에 "C3" 등을 담고 label은 "Verified"만)과 `cov_verified`/`cov_reached`/`cov_failed`/`cov_none`(label에 "(C3)"를 인라인하고 code는 None)이 사실상 같은 4단계 개념을 서로 다른 표기 방식으로 이중 정의하고 있다.
- `RUN_STATUS`의 5개 라벨(Done/Failed/Running/Dispatched/Archived)에는 `tip`(한국어 정의 툴팁) 필드가 없다 — "은어 첫 등장 정의 툴팁 의무" 원칙과 형식상 어긋난다(단, 통상어라 은어가 아니라는 반론 가능 — 오너 판단 필요).

---

**정본은 `controlplane/v2/terms.py`** — 이 문서는 그 스냅샷이다. (갱신일: 2026-07-10)
