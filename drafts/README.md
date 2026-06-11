# drafts/ — AI 파이프라인 검토용 초안

`controlplane/ai_pipelines.py`(M3 §4-A1/A2/A3)가 생성하는 **사람 검토용
산출물**이 쌓이는 디렉토리. 커밋해도 되는 리뷰 아티팩트이며, 어떤 파일도
플랫폼이 자동으로 활성화·머지하지 않는다.

| 파일 패턴 | 파이프라인 | 내용 |
|---|---|---|
| `spec-impact-<ts>.json` | A1 spec-diff 영향 분석 | mechanical diff + AI 영향 분석/재실행 범위 제안 |
| `lifecycle-<service>-<ts>.json` | A2 시나리오 초안 | lifecycle JSON 초안(`enabled:false` 고정) + 기계 검증 결과 + 불확실성 notes |
| `facts-<run_id>.json` | A3 fact 추출 | run의 2xx observation에서 뽑은 fact 후보 + formal YAML 제안 |

리뷰 후 반영 위치:

- A2 lifecycle → `regression/scenarios/lifecycles/<category>__<service>.json`
  의 `lifecycles` 배열에 복사, 검증이 끝난 뒤에만 `enabled: true`.
- A3 facts → `knowledge/validated-facts.md` / YAML 제안 →
  `knowledge/formal/services/` (`python knowledge/formal/validate.py` 통과 필수).
- A1 재실행 범위 → Testing 화면에서 suite/필터로 실행.

UI: `GET /ai` (생성 + 초안 목록), `GET /ai/drafts/<name>` (개별 보기).
