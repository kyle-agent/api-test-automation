"""v2 용어 상수 — D7 확정: 한국어 1급 + 코드 괄호 보조 (V2-DECISIONS.md).

모든 v2 화면 라벨은 이 모듈에서만 가져다 쓴다. 코드네임·모듈명·env명을
화면 문구에 직접 쓰는 것 금지. "상태" 단독 라벨 금지(모델 상태/런 결과/
커버리지로 분리).
"""
from __future__ import annotations

PRODUCT_NAME = "SCP API Regression"

# 커버리지 사다리 + 핵심 도메인 용어. label(한국어 1급) + code(괄호 보조) + tip(첫 등장 정의 툴팁)
TERMS: dict[str, dict] = {
    "c3": {"label": "검증됨", "code": "C3",
           "tip": "검증(C3) = 실제 2xx 응답으로 동작을 확인함"},
    "c2": {"label": "호출됨", "code": "C2",
           "tip": "호출(C2) = 실제 호출이 이루어짐 (2xx 보장은 없음)"},
    "c1": {"label": "도달가능", "code": "C1",
           "tip": "도달(C1) = 인증·라우팅을 통과해 API에 도달함"},
    "cov_c3": {"label": "검증 커버리지", "code": "C3",
               "tip": "전체 엔드포인트 중 실제 2xx로 동작 확인(검증)된 비율"},
    "cov_op": {"label": "호출 커버리지", "code": None,
               "tip": "전체 엔드포인트 중 실제 호출이 이루어진 비율"},
    "regression_new": {"label": "새 회귀", "code": None,
                       "tip": "직전 발행까지는 없었는데 새로 실패한 항목"},
    "regression_known": {"label": "기지 실패", "code": None,
                         "tip": "이미 알려져 추적 중인 실패 (베이스라인에 등록됨)"},
    "residual": {"label": "잔존 자원", "code": None,
                 "tip": "테스트가 만들었다가 아직 정리되지 않은 클라우드 자원 — 비용·안전 사안"},
    "billable": {"label": "과금", "code": None,
                 "tip": "실행 시 실제 비용이 발생하는 자원/작업"},
    "fold": {"label": "공식 반영", "code": "fold",
             "tip": "이 서버에서 확인된 결과를 검토를 거쳐 공식 집계(발행 기준 데이터)에 넣는 절차"},
    "run_result": {"label": "런 결과", "code": None,
                   "tip": "한 번의 실행이 남긴 성공/실패/스킵 판정"},
    "model_state": {"label": "모델 상태", "code": None,
                    "tip": "테스트 정의(리소스 모델)의 완성도 — 모델됨/검증됨"},
    # 서비스 상세 엔드포인트 표의 커버 열 — 4개 상태 라벨 (L1 계약 §2.2)
    "cov_verified": {"label": "검증(C3)", "code": None,
                     "tip": "검증(C3) = 실제 2xx 응답으로 동작을 확인함"},
    "cov_reached": {"label": "도달", "code": None,
                    "tip": "도달 = 호출은 됐으나 2xx로 확인되지 않음(4xx 등)"},
    "cov_failed": {"label": "실패", "code": None,
                   "tip": "실패 = 5xx 등 하드 실패가 관측됨"},
    "cov_none": {"label": "미관측", "code": None,
                 "tip": "미관측 = 지금까지 어떤 런에서도 호출 기록이 없음"},
    "defect_red": {"label": "결함", "code": None,
                   "tip": "결함 = 계약 위반 등 실제 구현 버그"},
    "defect_yellow": {"label": "개선", "code": None,
                      "tip": "개선 = 설계·문서상 아쉬운 점(구현 버그는 아님)"},
}

# 출처 배지 3종 (L1 계약 §1) — 라벨과 뜻. 배지 마크업은 _badges.html 매크로가 유일한 구현.
SOURCES: dict[str, dict] = {
    "published": {"label": "발행", "tip": "발행된 공식 집계 — 어느 서버에서 열어도 같은 값"},
    "local": {"label": "지금 이 서버", "tip": "이 서버에서만 관측된 값 — 공식 집계와 다를 수 있음"},
    "run": {"label": "이 런", "tip": "특정 실행 1건에 고정된 값 (과거형)"},
}

# 질문 중심 6축 네비 (CX-IA-DESIGN §4.2). 축의 "질문"은 IA 설계 개념일 뿐,
# 화면 문구는 격식 명사형만 쓴다 — 구어체·비유·질문형 금지 (오너 지시 2026-07-10).
NAV: list[dict] = [
    {"key": "situation", "label": "현황", "q": "플랫폼 전체 현황 요약", "path": "/v2"},
    {"key": "services", "label": "서비스", "q": "서비스별 테스트 현황", "path": "/v2/services"},
    {"key": "model", "label": "모델", "q": "테스트 정의 · 리소스 모델", "path": "/v2/model"},
    {"key": "run", "label": "실행", "q": "테스트 실행 · 모니터링", "path": "/v2/run"},
    {"key": "results", "label": "결과", "q": "실행 결과 · 회귀 분석", "path": "/v2/results"},
    {"key": "tools", "label": "도구", "q": "부가 도구 링크 모음", "path": "/v2/tools"},
]
