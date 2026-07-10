"""v2 용어 상수 — D7 개정(2026-07-10): 이름은 영어, 설명은 한국어
(V2-DECISIONS.md D7).

모든 v2 화면 라벨은 이 모듈에서만 가져다 쓴다. 코드네임·모듈명·env명을
화면 문구에 직접 쓰는 것 금지. "상태" 단독 라벨 금지(모델 상태/런 결과/
커버리지로 분리). 라벨(label)은 영어 원어, 정의 툴팁(tip)은 한국어 유지.
"""
from __future__ import annotations

PRODUCT_NAME = "SCP API Regression"

# 커버리지 사다리 + 핵심 도메인 용어. label(영어 원어) + code(괄호 보조, 있는 경우) + tip(한국어 정의 툴팁)
TERMS: dict[str, dict] = {
    "c3": {"label": "Verified", "code": "C3",
           "tip": "검증(C3) = 실제 2xx 응답으로 동작을 확인함"},
    "c2": {"label": "Called", "code": "C2",
           "tip": "호출(C2) = 실제 호출이 이루어짐 (2xx 보장은 없음)"},
    "c1": {"label": "Reachable", "code": "C1",
           "tip": "도달(C1) = 인증·라우팅을 통과해 API에 도달함"},
    "cov_c3": {"label": "Verified coverage", "code": "C3",
               "tip": "공식 커버리지 지표 — 테스트 불가 서비스·면제를 제외한 분모 기준으로, 실제 2xx 응답으로 동작 확인(검증)된 비율"},
    "cov_called": {"label": "Called coverage", "code": "C2",
                   "tip": "전체 엔드포인트 중 실제 호출이 이루어진(응답을 받은, 4xx 포함) 비율"},
    "regression_new": {"label": "New regressions", "code": None,
                       "tip": "직전 발행까지는 없었는데 새로 실패한 항목"},
    "regression_known": {"label": "Known issues", "code": None,
                         "tip": "이미 알려져 추적 중인 실패 (베이스라인에 등록됨)"},
    "residual": {"label": "Leftover resources", "code": None,
                 "tip": "테스트가 만들었다가 아직 정리되지 않은 클라우드 자원 — 비용·안전 사안"},
    "billable": {"label": "Billable", "code": None,
                 "tip": "실행 시 실제 비용이 발생하는 자원/작업"},
    "fold": {"label": "Fold", "code": None,
             "tip": "이 서버에서 확인된 결과를 검토를 거쳐 공식 집계(발행 기준 데이터)에 넣는 절차"},
    "run_result": {"label": "Run result", "code": None,
                   "tip": "한 번의 실행이 남긴 성공/실패/스킵 판정"},
    "model_state": {"label": "Model state", "code": None,
                    "tip": "테스트 정의(리소스 모델)의 완성도 — 모델됨/검증됨"},
    # 서비스 상세 엔드포인트 표의 커버 열 — 4개 상태 라벨 (L1 계약 §2.2)
    "cov_verified": {"label": "Verified (C3)", "code": None,
                     "tip": "검증(C3) = 지금까지의 런에서 2xx로 동작이 확인됨 (누적 판정 — 최근 status와 다를 수 있음)"},
    "cov_reached": {"label": "Reached", "code": None,
                    "tip": "도달 = 호출은 됐으나 2xx로 확인되지 않음(4xx 등)"},
    "cov_failed": {"label": "Failed", "code": None,
                   "tip": "실패 = 5xx 등 하드 실패가 관측됨"},
    "cov_none": {"label": "Not observed", "code": None,
                 "tip": "미관측 = 지금까지 어떤 런에서도 호출 기록이 없음"},
    "defect_red": {"label": "Defect", "code": None,
                   "tip": "결함 = 계약 위반 등 실제 구현 버그"},
    "defect_yellow": {"label": "Improvement", "code": None,
                      "tip": "개선 = 설계·문서상 아쉬운 점(구현 버그는 아님)"},
    # 결과 축(⑤) — 정합성(축2) 변화 (L1 계약 §2.5)
    "conformance_new": {"label": "New", "code": None,
                        "tip": "직전 발행 기준선(초록)에서 이번 발행에 새로 결함(빨강/노랑)으로 바뀐 엔드포인트"},
    "conformance_regressed": {"label": "Regressed", "code": None,
                              "tip": "이미 결함이던 항목이 더 심각한 등급으로 나빠짐(노랑→빨강)"},
    "conformance_fixed": {"label": "Fixed", "code": None,
                          "tip": "이전에 결함이던 항목이 이번 발행에서 개선되어 등급이 좋아짐"},
    # 서비스 목록/상세의 "기능 테스트 제외" 배지 — 사유는 호출부가 title에 동적으로 채움
    "untestable": {"label": "Untestable", "code": None,
                   "tip": "기능 테스트가 불가능한 서비스 — 사유는 배지 안내(title)에 표시"},
}

# 출처 배지 3종 (L1 계약 §1) — 라벨과 뜻. 배지 마크업은 _badges.html 매크로가 유일한 구현
# (Jinja 매크로가 context 없이 import돼 이 dict를 직접 참조하지 못해 동일 값을 그 안에도 유지한다).
SOURCES: dict[str, dict] = {
    "published": {"label": "Published", "tip": "발행된 공식 집계 — 어느 서버에서 열어도 같은 값"},
    "local": {"label": "This server", "tip": "이 서버에서만 관측된 값 — 공식 집계와 다를 수 있음"},
    "run": {"label": "This run", "tip": "특정 실행 1건에 고정된 값 (과거형)"},
}

# 병합 런 타임라인의 진행 상태 라벨 (situation.html 두 표에서 공유 — 하드코딩 승격).
RUN_STATUS: dict[str, str] = {
    "done": "Done", "failed": "Failed", "running": "Running",
    "dispatched": "Dispatched", "archived": "Archived",
}

# 질문 중심 6축 네비 (CX-IA-DESIGN §4.2). 축의 "질문"은 IA 설계 개념일 뿐,
# 화면 문구는 격식 명사형만 쓴다 — 구어체·비유·질문형 금지 (오너 지시 2026-07-10).
# label은 D7 개정으로 영어 원어. q(툴팁)는 한국어 유지.
NAV: list[dict] = [
    {"key": "situation", "label": "Overview", "q": "플랫폼 전체 현황 요약", "path": "/v2"},
    {"key": "services", "label": "Services", "q": "서비스별 테스트 현황", "path": "/v2/services"},
    {"key": "model", "label": "Model", "q": "테스트 정의 · 리소스 모델", "path": "/v2/model"},
    {"key": "run", "label": "Runs", "q": "테스트 실행 · 모니터링", "path": "/v2/run"},
    {"key": "results", "label": "Results", "q": "실행 결과 · 회귀 분석", "path": "/v2/results"},
    {"key": "tools", "label": "Tools", "q": "부가 도구 링크 모음", "path": "/v2/tools"},
]
