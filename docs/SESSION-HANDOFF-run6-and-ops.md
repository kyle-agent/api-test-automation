# SESSION HANDOFF — 측정 런 #6 재개 + ops 대시보드 (2026-06-11)

- Status: **active** · 모든 코드/지식은 main에 푸시됨 (HEAD `0463fbb`+)
- 이 세션의 전말: `knowledge/validated-facts.md`의 2026-06-11 섹션,
  `docs/HANDOFF-fail-new-triage.md`, `docs/OPS-DASHBOARD.md`

## 세션 종료 시점 상태

- **런 #5 (27323979921, sha 22a3b22)**: 본체 종료, **sweep만 마무리 중**이었음
  (05:27 시작). 결과: heavy/adopt 10개가 또 VPC 캡 스킵 — 최종 근원은
  **peering approval 바디 오류**({action:APPROVE} → 정답 {type:CREATE_APPROVE},
  잘못된 바디 때문에 peering이 CREATING 고착 → VPC 2개씩 영구 잠금 → sweep
  무력 → 런마다 캡 오염). 근원 수정은 `4bcd0bb`로 main에 반영 완료
  (lifecycle approve/wait-active + **reconciler vpc-peerings 회수 단계** 신설).
- 잡 A의 adopt 패스에 캡 스킵 외 **하드 실패 1건** 있었음 — 원인 미확인
  (#5 job A junit에서 확인할 것).

## 다음 세션이 바로 할 일 (순서대로)

1. **#5 완전 종료 확인** (sweep 포함; `actions_list` in_progress=0 또는 oplog
   버킷의 sweep 마일스톤 — emit 버그는 `a66c9b1`에서 수정돼 이제 찍힘).
2. **force-cleanup 런**: run-request에 `destructive=true` + `sweep_force=true`
   (+ category=management service=servicewatch로 smoke 축소). 이번 sweep은
   peering 회수 코드가 처음 적용되는 sweep — 잠긴 peering들과 VPC 4개+가
   풀리는지 로그 확인 (`vpc-peering <id> delete -> 2xx`).
3. **풀 런 #6** (`mutations=true destructive=true heavy=true`) — 진짜 측정 런.
   확인 포인트: ① heavy 10개 복구 + dbaas 슬리밍 실측(~60-75분 목표)
   ② peering CREATE_APPROVE→ACTIVE→정상 삭제 ③ refire 첫 발동(실패상태 enum
   확정→knowledge 기록) ④ fail-note로 iam/rm 500 + check-dup 401 원인 채집
   ⑤ ops.html 라이브 자원 트리(즉시 플러시 첫 풀런).
4. 시퀀싱 규칙 준수: **런(스윕 포함) 완전 종료 전에 run-request를 건드리지 말 것**
   (CONTEXT.md 안전 게이트 섹션).

## 그 다음 커버리지 후보 (분석 완료, 착수 대기)

- DBaaS 서브옵 클러스터-윈도우 스케줄링 (~139 endpoints, 최대 단일 레버)
- SCR 이미지 push (enable-public-endpoint true 토글이 #6에서 시도됨 →
  성공 시 skopeo 스텝 추가, 19 endpoints; 콘솔 선행: 인증키 인증 설정)
- servicewatch 메트릭 POST 바디 / eventstreams topology 도메인 헌트
- 트리아지 문서의 BODY-FIX 잔여 (iam createrole 등 — #6의 note가 원인 제공)

## ops 대시보드 (이 세션 신규)

- 영구 버킷 `apitest-oplog-permanent` + `core/oplog.py` + Pages `/ops.html`
  (기본 주소 내장, 선택 불필요). 모든 잡 마일스톤 + 엔진 자원 이벤트(즉시
  플러시, 이벤트당 1객체) → 계층 트리(상태 점 + 수행시간 숫자) + 런 이력.
- GitHub API 불능 시(토큰 만료/rate limit) **버킷 직접 조회가 런 상태 확인의
  대체 채널** — 이 세션에서 실제로 그렇게 운용함.
