# HEAVY-PREMISE CONTRACT — Testing 단순화 (Model B) 공유 계약

> **Status: LOCKED 2026-07-08 (owner GO).** 이 문서가 WP1–WP4 + lead 통합의 단일
> 계약이다. 여기 정의된 이름·스키마·규칙과 다르게 구현하지 말 것. 변경이 필요하면
> lead가 이 파일을 먼저 고치고 전 WP에 전파한다. (배경: `docs/working/CONTEXT.md`
> 핸드오프의 "Testing 단순화 재설계 — heavy-전제 모델".)

## 0. 목표 (한 문장)

대화형 Testing = **항상 실물 풀 테스트**: 사용자 축은 "무엇을(서비스/리소스)" 하나,
선택 → (프로브 제외) 검증형 풀 DAG → **pre-flight(자원·과금·시간) confirm 1회** → 실행.
light/heavy 어휘는 UI에서 사라지고, 도달 프로브는 CI 스윕 전용이 된다.

## 1. role 파생 (WP1) — 파일 무수정, 로드 시점 계산

`role ∈ {"verify", "probe"}` 를 **로더가 파생**한다 (fragment/scenarios.json 어디에도
필드를 쓰지 않는다 — scenarios.json 쓰기 금지 규칙 준수).

정의:
- step이 **tolerant** := `expect_status`(또는 엔진의 동등 필드)에 4xx(400/401/403/404/409/422)
  또는 5xx가 하나라도 포함.
- step이 **mutating** := method ∈ {POST, PUT, PATCH, DELETE}.
- lifecycle이 **probe** := (mutating step이 1개 이상) AND (strict-2xx mutating step이 0개)
  AND (전체 step 중 tolerant 비율 ≥ 0.5).
  즉 "실패해도 되는 쓰기들만 있는" = 쓰기-도달 프로브.
- 그 외 전부 **verify** (all-GET read 라이프사이클 포함 — license-gated tolerant read도
  2xx를 딸 수 있는 검증형이다: sqlserver/vertica/searchengine의 green 소스).

검산 기준 (이 결과와 다르면 규칙이 아니라 구현이 틀린 것):
`probe` = vs-server-action-coverage · vs-image-write-coverage · vs-volume-transfer-coverage ·
*-subops-guarded 계열 · idc-delete-policies-probe / `verify` = compute-virtualserver-full ·
virtualserver-keypair · vs-autoscaling-coverage · *-read-coverage · *-light-reads · gen-* reads.
예외가 필요하면 **override 상수 dict** `ROLE_OVERRIDES: dict[lifecycle_id, role]` 를 loader에
두고 여기(계약)에 사유와 함께 기록한다. 현재 등록분:
- `vs-autoscaling-coverage → verify` (WP1, 2026-07-08): 스텝 모양이 쓰기-프로브와 기계적으로
  동일(전부 tolerant mutating)하지만, keypair·launch-configuration create가 **라이브 2xx로
  증명된**(2026-06-18) 무과금 실자원 + 자체 teardown — 규칙만으로는 vs-image-write류와 분리
  불가하여 override. (사유 주석: `regression/scenarios/loader.py` ROLE_OVERRIDES.)

노출: `loader.load_lifecycles()`가 각 lifecycle dict에 `"role"` 키를 추가. `_model()`의
lifecycles에도 그대로 실린다 (UI/선택/CI가 공통 소비).

## 2. 선택 의미론 (WP1)

- **서비스/카테고리/그룹 선택** → 그 범위의 `enabled AND role=="verify"` lifecycle 전부.
  heavy 여부로 거르지 **않는다** (heavy-전제).
- **개별 lifecycle을 명시 선택**하면 role 무관 그대로 포함 (명시가 이긴다 — 프로브를
  일부러 돌리고 싶은 경우).
- 기존 `_selection_is_heavy` 파생은 유지하되 **admission 차단이 아니라 pre-flight 표기용**
  메타로만 쓴다. 대화형 콘솔 경로의 opt-in = pre-flight confirm (Hard Rule 1 문구 그대로).
  `SCP_RUN_HEAVY` env 게이트는 headless/CI 경로 안전망으로만 존속 (콘솔 경로는 confirm이 게이트).

## 3. pre-flight payload (WP2 모듈 + lead 배선)

`GET/POST /api/preflight` (lead가 배선; WP2는 순수 함수만) 응답 스키마 — **키 이름 고정**:

```json
{
  "lifecycles": ["..."],                    // 실행될 최종 목록 (role 필터 반영)
  "resources": [ {"node": "server", "service": "compute/virtualserver",
                   "count": 1, "billable": true} ],
  "peak_quota": {"vpc": 1, "...": 0},      // composer.graph_view 그대로
  "billable_count": 9,
  "est": {"p50_s": 2200, "p90_s": 3300,
           "basis": "measured|default|mixed",
           "per_lifecycle": {"<id>": {"p50_s": 0, "basis": "measured"}}},
  "warnings": ["<한글 문장>"]
}
```

- `billable` 판정: 노드/lifecycle의 heavy 플래그 또는 quota class가 과금형(현행 메타).
- 시간 추정 (WP2 `tools/duration_stats.py`):
  - 소스 = `reports/console2-runs/*.events.jsonl` — **step-end 이벤트 타임스탬프 간격**으로
    스텝 wall 시간을 접는다 (observations의 elapsed_ms는 콜 지연이라 쓰지 않는다 — wait
    스텝이 1.2s로 왜곡됨, 2026-07-08 확인).
  - lifecycle별 p50/p90 (런 단위 합산), 미측정은 클래스 기본값: read/config류 30s ·
    자원 1–2개 create류 120s · 실서버/클러스터급 2400s. basis 필드로 측정/기본값 구분.
  - makespan = 선택 lifecycle들의 DAG wave 병렬성을 고려한 critical path 근사
    (parallel=N 가정은 admission의 현행 값 사용, 단순 합산 상한도 함께 반환 가능).
- 함수 시그니처(고정): `estimate(lifecycle_ids: list[str], model: dict|None) -> dict`
  (위 `est`+`per_lifecycle` 부분을 반환). 자원/quota 부분은 lead가 composer.plan에서 조립.

## 4. soft 3분류 (WP3 모듈 + lead 배선)

`regression/soft_classify.py` — 순수 함수, **키 이름 고정**:

```python
classify(observations: list[dict], *, verified: dict, waivers: list[dict],
         run_endpoint_2xx: set[str]) -> dict[obs_index|key, str]
# 반환 클래스: "duplicate" | "gap" | "policy"
```

우선순위 **policy > duplicate > gap**:
- **policy**: endpoint의 catalog key가 waiver class=="reachability" (coverage_waivers.json).
- **duplicate**: 같은 런에서 동일 endpoint(method+정규화 path 또는 catalog key)가 2xx를
  이미 기록했거나, `verified_endpoints.json`에 2xx 증거가 있음.
- **gap**: 위 둘 다 아님 = 어떤 verify-role lifecycle에도 이 endpoint를 2xx로 딸 스텝이
  아직 없음 (레시피 숙제).
- 정규화: `derive_verified.norm_path` 재사용 (경로 파라미터 `*` 접기). 새 정규화 만들지 말 것.

## 5. UI 어휘 (lead 통합)

- 선택 화면: "heavy/light/경량/대형" 단어 제거. Δ 뱃지는 pre-flight 안에서만
  "과금 자원 포함" 의미로 유지.
- pre-flight confirm 다이얼로그: `생성 자원 N개 (과금 M개) · 예상 p50~p90 (basis)` + 자원 목록.
- 리포트: soft를 단일 주황이 아니라 chip 3종 — `중복`(회색·접힘 기본) / `갭`(주황·레시피
  숙제 링크) / `정책`(파랑·"만점=도달").

## 6. 파일 소유권 (병렬 충돌 방지 — 엄수)

| WP | 소유 (이 파일들만 수정) |
|---|---|
| WP1 | `regression/scenarios/loader.py` · `tools/console2_server.py`(선택 해석·model 노출부만) |
| WP2 | `tools/duration_stats.py`(신규) · `tests/offline/test_duration_stats.py`(신규) |
| WP3 | `regression/soft_classify.py`(신규) · `tests/offline/test_soft_classify.py`(신규) |
| WP4 | `data/baselines/coverage_waivers.json` · `.github/workflows/api-test.yml`(주석만) · `regression/scenarios/lifecycles/compute__virtualserver-actions.json`(신규) |
| lead | `console2/*`(js/html) · `console2_server.py` preflight/rec_view 배선 · 이 문서 · CONTEXT |

규칙: agent는 **커밋하지 않는다** (git index 경합 방지 — lead가 WP별로 커밋).
`python -m regression.scenarios.validate` + `validate_dag --check` + 소유 테스트 green을
완료 조건으로 보고한다.

## 7. 검증 (lead)

offline 스위트 → TestClient E2E(선택→preflight→run) → Playwright 스크린샷 →
무과금 라이브 증명(read류 선택) → **과금 E2E: VS 서비스 선택 풀 런 (~40분, owner 사전 승인
2026-07-08)** → verify-clean/owned==0 → derive fold → 커밋.
