---
status: active
for: human-ops
---

# SCP API 품질점검 결과 리포트 — 생성·재검증 런북 (`conformance.quality_report`)

> 오너 2026-09-02: "report 생성하는 부분을 기능으로… 검증계를 대상으로 돌려서
> report 뽑아서 해결되었는지를 보려고 해." 이 문서는 그 절차 전부다 — 환경
> 지정 → API 명세 재수집 → 실측 프로브 → 리포트 3종 → 이전 리포트와 비교.

## 산출물

`python -m conformance.quality_report` 는 `reports/quality/<날짜>[-<라벨>]/` 에 다음 3종을 만든다
(gitignored — 전달본은 `docs/working/` 에 복사해 커밋).

| 파일 | 용도 |
|---|---|
| `SCP-API-품질점검-<날짜>[-<라벨>].html` | 개발팀 전달용 (통계 → 공통 항목 → 유형 → RED → 실측 원문 → 전체 목록 → 부록) |
| `SCP-API-품질점검-<날짜>[-<라벨>].md` | 같은 내용의 Markdown (diff 로 이전 리포트와 비교하기 좋다) |
| `…-항목목록.csv` | 전체 항목 로데이터 (utf-8-sig, 엑셀 바로 열림) — `cls`(규격/문서/기능) · `tier`(본문/부록) 컬럼 |

입력은 전부 리포지토리 안의 데이터다 — `data/conformance.json`(정적+프로브 폴드),
`data/api_catalog.json`(method/path), `reports/runtime_*.json`(실측 원문 — §5.5 엔벨로프
현황, 부재-id 403/401 부록 판정), `data/quality_report_cases.json`(§5 요청/응답 원문 예시,
없으면 §5 생략). 네트워크 호출 없음.

## 검증계 재검증 절차 (전체)

```bash
# 0. 환경 — .env 는 커밋 금지. 검증계 자격증명·호스트를 env 로 지정
export SCP_REGION=kr-west1 SCP_ENV=<검증계 env 코드>       # 또는 SCP_SERVICE_HOSTS / SCP_BASE_URL
export SCP_ACCESS_KEY=… SCP_SECRET_KEY=…
export SCP_ENV_LABEL=검증계        # 리포트 헤더/파일명/출력 디렉터리에 표기 (--env-label 로도 가능)

# 1~4 한 번에: 명세 재수집 → 프로브 8종(실호출) → 정적 폴드 → 리포트
python -m conformance.quality_report --refresh-spec --probes --static --env-label 검증계
```

단계별로 나누면:

1. **API 명세 재수집** — `python -m spec.extract_catalog --fresh` (검증계에 배포된 문서 기준으로
   `data/api_catalog.json` 갱신. `--fresh` 없이는 cache-hit no-op 라 변경을 못 잡는다).
   변경분만 보려면 재수집 전에 `cp data/api_catalog.json /tmp/cat_before.json` 해 두고
   `python -m spec.diff /tmp/cat_before.json data/api_catalog.json --mark`.
2. **실측 프로브** — `SCP_PROBE_RUNTIME=true SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=false python -m conformance.runtime --probe all`.
   자원 생성 없음(status/l10n 프로브가 빈 바디 `{}` 로 400 을 측정하므로 MUTATIONS 게이트는 열어야
   한다 — 닫으면 `checked=0` 으로 전멸). 과금 `schema-live` 는 별도 이중 게이트라 포함되지 않는다.
   결과: `reports/runtime_*.json`.
3. **정적 폴드** — `python -m conformance.static` → `data/conformance.json`
   (정적 규칙 + 프로브 결과 + `conformance/rules/live_confirmed.py` 의 시나리오 확인 항목).
4. **리포트** — `python -m conformance.quality_report --env-label 검증계`.

콘솔2(제어평면 Testing 화면)의 **📐 컨포먼스** 버튼은 2→3→4 를 그대로 수행하고 산출물을
`reports/quality/<run-id>/` + op 버킷 `runs/<run-id>/artifact/` 에 올린다 (LIVE 런 진행 중엔 409 거부).
명세 재수집(1)은 버튼에 포함되지 않으니 배포 직후엔 먼저 CLI 로 돌린다.

## 해결 여부 비교

같은 형식으로 두 리포트를 만들었으면 항목 단위 비교는 CSV, 본문 비교는 MD diff:

```bash
python - <<'EOF'
import csv
def keys(p): return {(r['category'],r['service'],r['endpoint'],r['rule']) for r in csv.DictReader(open(p,encoding='utf-8-sig'))}
a = keys('docs/working/API-품질점검-항목목록-2026-08-20.csv')          # 이전 전달본
b = keys('reports/quality/2026-09-02-검증계/SCP-API-품질점검-2026-09-02-검증계-항목목록.csv')
print('해결', len(a-b), '| 신규', len(b-a), '| 잔존', len(a&b))
for k in sorted(a-b): print('  해결', '/'.join(k))
EOF
```

`live_confirmed.py` 의 항목(시나리오 실행으로 확인한 RED)은 프로브가 아니라 **코드에 등록된
사실**이라 재검증에서 자동으로 사라지지 않는다 — 검증계에서 해당 시나리오가 통과하면 그 항목을
목록에서 제거(또는 `resolved` 처리)하는 커밋이 필요하다.

## 문구·분류 규칙 (오너 지시 — 바꾸지 말 것)

* 독자는 우리 테스트 시스템을 모르는 서비스 개발팀 — run-id·시나리오명·oplog 등 내부 용어는
  `sanitize()` 가 근거 문장에서 지운다.
* 조사자 어투 금지: **결함 · 확정 · 소행 · 오탐 · 재실시** 는 쓰지 않는다
  (`BANNED_WORDS`, `tests/offline/test_quality_report.py` 가 검사). "확인된 사항 / 조치 방안" 만.
* 본문/부록: 부재-id 에 403/401(권한·미청약으로 해석 가능), WAF HTML 차단, CORS, Accept-Language 는
  부록 §7 로 분리. 일시-상태 거절은 409 통일을 가이드(플랫폼 자체 선례 SCR/VIP 인용).
* 유형별 문구는 `RULE_KR`/`SYS_KR`, 구분은 `CLS`(기본 규격) — 새 규칙을 `conformance/rules/` 에
  추가하면 여기에도 한 줄 추가해야 리포트에 영문 rule 명이 그대로 나오지 않는다.

## 관련

* `conformance/static.py` · `conformance/runtime.py` — 입력 생성. `conformance/report.py` 는 내부용
  MASTER_REPORT(다른 독자) — 혼동 주의.
* 이전 전달본: `docs/working/API-QUALITY-DEVTEAM-REPORT-2026-08-20.md` (+ CSV).
