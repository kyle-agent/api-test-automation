---
status: active
for: design-improvements-v1 세션 (v2 → v1 접목)
basis: V2-WRAP-AND-PIVOT.md (branch claude/v2-redesign-planning-aufboo — 오너 선회 결정 2026-07-10)
---

# V1 접목 트래커 — v2에서 검증된 것만 v1에 얹는다

> 오너 결정(2026-07-10): v2가 v1을 대체하는 방향은 **중단**. v1을 본체로 두고
> v2에서 검증된 디자인·사상만 골라 이식한다. 접목 후보·우선순위의 정본은
> `V2-WRAP-AND-PIVOT.md` §3 (v2 브랜치 `claude/v2-redesign-planning-aufboo`에
> 존치 — donor 코드 `controlplane/v2/**` 포함, 접목 완료 후 정리 권고).
> 데이터 계약 정본: 같은 브랜치의 `V2-L1-DATA-CONTRACT.md`.

## 접목 상태 (우선순위순 — V2-WRAP-AND-PIVOT §3)

| # | 후보 | 상태 | 비고 |
|---|---|---|---|
| 1 | 출처 배지 3종 + empty-state 규율 | **완료 (2026-07-11, 이 브랜치)** | 아래 상세 |
| 2 | 계획↔실행 연속성 (PLAN vs ACTUAL 스트립) | **완료 (2026-07-11, 이 브랜치)** | 아래 상세 |
| 3 | 종료 후 다음 행동 카드 | **완료 (2026-07-11, 이 브랜치)** | 아래 상세 |
| 4 | 실행 중 이상 감지 (지연 의심·실패 군집) | 대기 | **엔진 요청 #5(세마포어 대기 이벤트) 선행** |
| 5 | 판정 시각 분리 표기 (발행 시각 ≠ 판정 런 시각) | **완료 (2026-07-13, 이 브랜치)** | 아래 상세 — 배지가 '판정 @… · 발행 @…' 로 병기 |
| 6 | (검토) 용어 툴팁·정의 노출 | **정책안 제출 (2026-07-13)** | `docs/TERMINOLOGY.md` (draft) — 문구별 오너 검수(§5 체크리스트) 후 화면 반영 |
| 6a | v2 셸 헤더 (네비 스타일·전역 검색·헤더 Published 배지) | **완료 (2026-07-11, 오너 지시)** | 아래 상세 |

## 접목 5 상세 — 판정 런 시각 ≠ 발행 갱신 시각 분리 병기

- **문제**: Published 배지의 `@ts` 는 판정 런 시각(history.jsonl `ts`) 하나뿐이라,
  발행본(dashboard-data)이 결과 없이 재발행돼 갱신 시각이 더 늦은 경우 그 어긋남이
  화면에 드러나지 않았다 (v2 published.py 실측 교훈 — V1-GRAFT §접목 1 각주).
- **소스 2종**: 판정 런 시각 = `common.snap_ts_short(snap)` (history ts). 발행 갱신
  시각 = `common.dd_ts_short()` = dashboard-data HEAD **committer date**
  (`git show -s --format=%cd --date=…` UTC → KST 짧은 라벨). dd_sha 와 같은
  `_dd_head()` 60s 캐시에서 한 번에 조회 (subprocess 1회).
- **배지 매크로** `_badges.html`: `pub_ts` 인자 추가. `pub_ts` 가 있고 `ts` 와
  **다를 때만** `판정 @… · 발행 @…` 로 분리 병기(발행 시각은 `.badge-ts2` 로
  살짝 흐리게 — 판정 시각이 1급), 같거나 없으면 기존 `@ts` 단일 표기 유지
  (노이즈 억제). 툴팁에 두 시각의 의미 명기.
- **호출부**: 헤더(base.html)·홈 판정 배너(home.html)·리포팅 요약(reporting.html)
  의 Published 배지에 `pub_ts=dd_ts_label` 전달. `base_ctx` 가 `dd_ts_label` 주입.
  타일 5장 배지는 ts 없는 출처 마커라 그대로.
- reporting.html:173 의 fail_new "판정 런과 발행물의 시점 차이" 칩과 정합 (배지가
  이제 그 어긋남을 시각으로 직접 보여줌).
- 검증: `test_verdict_vs_publish_time_split` (분리/단일/부재/best-effort 4케이스) +
  라이브 확인 (`dd_ts_short()` → '07-10 13:35', dd_sha 041884a1).

## 접목 6a 상세 — v2 셸 헤더 이식 (오너 지시 2026-07-11)

- **네비**: Overview 첫 메뉴 신설(`/`) + active = 다크 pill(v2 `.axis.on`).
  **메뉴명은 v1 유지** (Modeling→Testing→Reporting — 오너 지시). 화살표(→)는
  파이프라인 의미가 있어 유지 (제거 원하면 마크업 2줄).
- **헤더 우측**: 전역 검색폼(GET `/search`) + Published 배지
  (`Published @시각 · dd:sha · 노후`) — 발행 식별자는 v2 계약대로
  dashboard-data HEAD sha(`common.dd_sha()`, 60s 캐시). 발행본 접근 불가 시
  `badge-none` empty-state.
- **ctxbar 정리**: 발행 시각·노후 칩을 헤더 배지로 흡수(중복 제거).
  ctxbar에는 v2에 없던 v1 강점 — env·suite·코드 sha·LIVE/SNAPSHOT 표면
  모드 — 만 유지.
- **전역 검색** `/search` (donor: v2 search_data/search.html, 계약 §2.8):
  Services(카탈로그 그룹 집계 — v1엔 서비스 상세 화면이 없어 "저장소(카탈로그)
  기준"으로 정직 표기 + 카탈로그/Modeling/실행 prefill 링크) ·
  Endpoints(상한 50+총계) · Runs(이 서버, 행별 local/CI 배지, 상한 20).
  2자 미만 안내. 카탈로그 `?q=` 딥링크 신설(기존 클라 필터 프리필).
- **이식 안 한 것 (의견)**: v2 "v2" 브랜드 칩(제품명 단일 원칙) · v2 푸터
  legacy 링크(불필요) · v2 축 이름(Services/Model/Runs/Results/Tools — 오너
  지시로 v1 이름 유지).
- 검증: tests_offline `test_v2_shell_header_and_global_search` (23/23).

## D8 상세 — 잔존 자원 홈 승격 + 단일 표면 (CX-IA-DESIGN §4.3 D8)

- **왜**: 잔존 자원은 비용·안전 사안인데 실행 화면 깊숙이 숨어 있었다 (CX-IA W2·D8).
- **단일 표면 (선행 IA 페이즈에서 이미 완료 — 이 세션이 확인)**: `/testing/resources`
  가 잔존의 정본 표면. 마지막 스캔 결과 캐시 + `POST /testing/resources/scan` "새로
  수집" 버튼(백그라운드, 열 때 자동 수집 안 함). 구 표면은 전부 리다이렉트 —
  `/local-run`·구 inventory → 301 `/testing/resources`. console2 는 `/runtime?scope=mine`
  링크 + DETAIL 탭 iframe 임베드로 이 표면을 재사용(별도 스캔 경로 발명 금지).
  (`/runtime` = 활동 토폴로지(loggingaudit×oplog)로 잔존 스캔과는 별개 관심사.)
- **홈 승격 (이 세션의 미완분)**: 홈 KPI 행에 '잔존 자원' 타일 신설.
  - `resources.owned_summary()` — `owned_state()` 캐시를 **읽기만** 하고 스캔은
    트리거하지 않는다. `actionable`(기지 제외 = 우리가 치워야 할 잔존, normal 수)
    을 헤드라인으로, `stuck`(문서화된 기지 항목)은 '+기지 N' 보조 표기.
  - 잔존은 라이브 **이 서버** 관측이라 배지는 `local`(발행 스냅샷 아님 — 다른 5
    타일의 published 와 구분). empty-state 규율: 미스캔은 '미확인 · 0 아님'
    (0 으로 위장 금지) + [🔍 지금 확인 →] 로 정본 표면 유도.
  - 색: actionable 0 이면 초록, >0 이면 빨강. '마지막 확인 HH:MM (N분 전)' 병기.
- 검증: `test_residual_resources_home_kpi_d8` (owned_summary 스키마·미스캔 None·
  /local-run 리다이렉트·홈 타일 local 배지+정본 링크+미확인 유도) + 라이브 렌더 확인.

## 콘솔 실행 화면 개선 (오너 지시 2026-07-13~14)

오너 실사용 제보 6건 — 전부 console2(`console2/assets/console2.js`·`.css`).

- **① 사전 점검(blast radius) 모달 — 요약 1급 + 세부 접힘** ("목록이 잘 보이지도
  않고 · 세부는 접혀있고 확인하면 실행"). 120 lifecycle 표 + 과금 30개 목록이 좁은
  스크롤 영역에서 경쟁해 표 헤더가 잘리고 확인 체크박스·실행 버튼까지 한참
  스크롤해야 했다. 재구성(`pfRender`):
  - **요약줄 `.pf-sum`**(1급): `N lifecycle · 생성 ~ · 삭제 ~ · ETA p50~p90 ·
    VPC peak/여유 · ⚠️과금 N`(비과금이면 '과금 없음' 초록) — blast radius 한눈에.
  - 서비스별 표 + 과금 라이프사이클 목록은 **`<details class="pf-det">` 기본 접힘**.
    경고(dropped=P2C-26 선택 누락·queued)는 **접지 않음**(안전 정보).
  - 확인 게이트 `.pf-confirm`(heavy만) + 실행 버튼은 접힘 밖 — 스크롤 없이 즉시
    보임. 실행은 여전히 명시 체크→버튼(Hard Rule 1, 자동 실행 아님).
- **② 실행 직후 성공 창 제거** ("이 창은 없어도 될 듯 · 필요하면 내가 알아서
  리포트 볼게"). `postRun` 이 이미 run 화면 전환+리포트 렌더+스크롤을 하므로,
  성공 시 중간 확인 창("✅ LIVE 실행 시작 — 리포트 보기/활동 흐름")은 그 리포트를
  가리는 중복이었다 → 성공 시 `pfClose()` + `toast("✅ LIVE 실행 시작 — run …")`.
  실패(409 중복 등)는 모달에 사유 유지(안전).
- 검증: `node --check` + `pfRender` 헤드리스 하네스(요약·접힘·확인 게이트·실행버튼
  구조 / 비과금 케이스 / 성공 시 pfClose+toast·옛 창 부재) 전부 PASS. 기존 console2
  오프라인 테스트가 변경 문자열을 검사하지 않아 회귀 없음.
- **③ '예측 vs 실제 타임라인' 패널 — 현재 경과 재생헤드** ("현재 라인이 세로로
  한 줄 있어서 움직이도록 하면 이해가 쉬울 듯"). 간트에 전체 높이 세로선(`.pva-now`)
  을 `nowRel`(현재 경과) 위치에 그린다. `nowRel` 은 진행 중이면 `Date.now` 기반이라
  매 폴 재렌더마다 갱신 → **별도 타이머 없이(신규 타이머 금지 원칙) 라인이 스스로
  이동**. x = 트랙 원점 오프셋(레인 190 + 컨테이너 패딩 8) + `px(경과)`; 종료 런은
  마지막 경과에 고정(회색 점선 + '종료 N분'). `pointer-events:none` 로 아래 막대
  hover 를 막지 않고, `z-index:1` 로 스티키 레인 뒤에 숨는다.
  - 검증: `pvaHtml` 헤드리스 하네스(진행 중 존재·위치 8+190+px(600)·1분 뒤 left
    증가·종료 고정·시작 전 부재·pointer-events) 전부 PASS.
- **④ 좌측 시나리오 = 정보 카드 + 런타임 탭 시나리오별 제외** (오너 2026-07-14:
  "기존 mockup 처럼 왼쪽 시나리오에 정보를 더 표시하고 공간을 더 차지"·"런타임
  계정 실측은 시나리오마다 있는 게 아니라서 각 시나리오별 탭에서는 제외").
  - **rail 확장 + 카드화**: `.md-report` grid 230/260 → **300/340px**. `.lcitem`
    을 1줄 압축행 → **세로 카드**(헤더=글리프+이름+🜂heavy 태그 · 서비스 라벨 ·
    지표 배지행). 배지 = `N API · N 자원 · N created · N soft · N fail`(0 은 생략),
    created=초록·soft=amber·fail=빨강. 값은 `groupedRun` 버킷(`createN`·`softN`·
    `failN` 등)에 이미 계산돼 있어 추가 계산 없음. (P2C-22 "1줄 압축행+툴팁" 계약을
    오너 지시로 대체 — 툴팁은 hover 요약으로 유지.)
  - **런타임 탭 스코프 게이트**: `런타임(계정 실측)`은 계정 전체 뷰라 시나리오별
    스코프에선 탭을 숨기고(집계 스코프에서만 노출), 스코프 진입 시 rt 선택 중이면
    `detailTab`을 `res`로 리셋 (`renderDetail`).
  - 검증: 실소스 추출 헤드리스 하네스(카드 헤더/서비스/배지·created·heavy·0값 생략 /
    rt 탭 집계-노출·시나리오-숨김·리셋) 전부 PASS · `test_console2.py` 39/39
    (grid·카드·rt 스코프 계약 갱신).
  - **후속 (오너 2026-07-14, 같은 rail)**: ⓐ **완료 필터 칩** 추가(전체·진행·**완료**·
    실패·대기 — `match`/`fc` done 절) ⓑ **시나리오 이름 검색** 입력(`#lc-search`,
    부분일치 `hit(id)`, verify+pending 양쪽 필터, 무매치 '검색 결과 없음', 셸
    재구축 후 값 복원 — 위임 `input`이라 재배선 불요) ⓒ **2행 레이아웃**: 세로가
    길어지지 않게 지표 배지를 서비스와 같은 행 **오른쪽**으로(`.lcitem-b`: 서비스
    좌 flex:1 + `.lcmeta` 우측 정렬). 검증: 헤드리스 하네스 16/16 + test_console2 39/39.
- **⑤ 클린업(teardown) 국면 표시** (오너 2026-07-14: "클린업 중인 게 표시 안 됨 —
  라인도, 위 표시도"). 모든 lifecycle 종료 후 run 이 아직 running 인 정리 스윕 구간이
  아무 데도 안 드러났다. **뿌리**: `liveProgress` 의 `active` 가 열린 스텝이 없으면
  `lastStart` 로 폴백해 항상 non-null → 마지막 스텝이 '테스트 중'으로 굳어 보였다.
  - **판정**: `cleaning = running && allLcEnded && !trulyActive`(열린 스텝 0 + 시작된
    모든 lifecycle 종료). phase 체인에서 `active` 폴백보다 **먼저** 판정 → phase
    `cleanup`·"정리 중".
  - **표시 3면**: ⓐ now-playing 배너 `phase-cleanup`(amber) + "자원 teardown 정리
    스윕 중" ⓑ 타임라인 헤더 '정리 중' 태그 ⓒ 재생헤드 amber + '정리 중 N분' ⓓ
    마지막 lifecycle-end~지금 사이 **줄무늬 '정리 중' 밴드**(`.pva-cleanband`).
  - **부수 수리**: 차트 horizon 이 모든 lifecycle 종료 시 `nowRel` 을 안 넣어
    재생헤드·밴드가 차트 밖으로 나가던 것 → `running` 이면 `hor=max(hor,nowRel)`.
  - 검증: 헤드리스 하네스(cleanup 판정·헤더/재생헤드/밴드·밴드 위치·반례 3종) +
    `test_cleanup_phase_indicators_frontend_contract` · test_console2 40/40.
- **⑥ 두 타임라인 축 라벨 명시** (오너 2026-07-14: "양쪽이 다르네?"). 구성 ▸ 📊
  예상 타임라인(행=워커 레인)과 실행 ▸ 예측 vs 실제(행=시나리오)가 **같은
  schedule-sim**(동일 makespan·워커·슬롯)인데 행 축이 달라 다르게 보이던 혼동.
  **데이터 불일치 아님** — 파라미터·makespan 동일 확인. 각 뷰 헤더에 축 라벨 명시:
  예상 타임라인엔 "행=워커 레인 · 실행 화면은 같은 sim 을 시나리오축으로", 예측 vs
  실제엔 "행=시나리오 · 워커축은 구성 ▸ 예상 타임라인". (라벨만, 로직 무변경.)

## Overview 출처 표기 다이어트 (오너 검수 2026-07-14, 2차 개정)

오너 1차 검수 "this server·published 헷갈림, 링크 이상" → 세션이 한국어화로
대응했으나, **2차 검수에서 진짜 질문이 드러남**: "한국어화는 필요없어, 다시
영어로 해도 돼 — **이런 내용의 표현이 필요한가부터가 질문**". 즉 언어가 아니라
**기본 출처를 타일마다 반복 표기하는 것 자체가 노이즈**였다.

**새 원칙 — 예외만 배지**: 페이지 기본 출처(발행 스냅샷)는 헤더 Published 배지
(판정/발행 시각 + dd sha) + ctxbar("모든 수치는 이 스냅샷 기준")가 **1곳**에서
선언한다. 배지는 기본과 **다른** 출처에만 붙는다 — 정보가 되는 곳:
  - 잔존 자원 타일 **This server** (라이브 이 서버 실측 — 이 페이지 유일한 예외)
  - 파이프라인 Testing 카드 **This server** (라이브 값)
  - 런 목록의 행별 **CI/This server** (행마다 출처가 실제로 갈림)
  - 런 상세 **This run** (특정 실행 1건 고정)

적용 (`home.html`·`_badges.html`):
- 배지 라벨 **영어 원복** (한국어화 철회 — 오너 지시), 툴팁 한국어 유지.
- 5개 KPI 타일·판정 배너·파이프라인 Reporting 카드의 **Published 배지 제거**.
- **D2 칩("발행 이후 이 서버 런 N건") 제거** — 오너가 명시적으로 혼란 지목;
  같은 정보는 아래 최근 RUN 표(source 열)가 담당. (모순 재발 시 툴팁으로 복귀 검토.)
- 타일 라벨 원복(write-op 커버리지(cov_op) 등 — 한국어화 철회).
- **유지한 1차 수리분**: Triage 실제 링크 2곳(배너·신규 fail 타일), 잔존 타일
  문구("단일 표면"→상세·새로 수집, "0 아님"→미측정—0이라는 뜻 아님, 기지→삭제불가).
- 검증: 렌더 8항목 전수(헤더 Published 1곳·타일 무배지·D2 무·예외 배지 유지·
  링크 유지) + tests_offline 32/32 (">Published" 라벨 카운트 == 1 계약 추가 —
  class 카운트는 ci 배지가 badge-published 스타일을 재사용해 부적합).

## 회귀 판정 배너 재설계 + 관측 0 마스킹 수리 (오너 공동 설계 2026-07-15)

오너와 블록 단위 공동 설계: "메인 = 성공/실패 · 몇 건 · 언제 + 실패 상세 링크".
결정 3건 — **1-a** 내역 미리보기는 서비스별 카운트 · **2** 통과도 배너 유지 ·
**3** 48h+ 에 [지금 실행] 버튼 (콘솔로 — pre-flight 게이트, 자동 실행 아님).

- **홈 배너 4상태** (`home.html` + `app.py fail_svcs` 집계):
  - 실패: "회귀 테스트 실패 — 신규 실패 N건 · MM-DD HH:MM 런 (상대) · suite ·
    알려진 실패 M건 추적 중" + **내역: 서비스별 카운트**(발행 fail_new.json →
    results_data, 폴백 시 '상한 6건 기준' 명시) + [→ Triage에서 상세·분류].
  - 통과: 같은 형식 초록 배너. 오래됨(48h+): "마지막 회귀 테스트가 N 전 — 당시
    판정 …" + [▶ 지금 실행 →]. 판정 기준(known_issues 베이스라인, 직전 런 대비
    아님)은 헤드라인 툴팁.
  - (구 D2 칩 잔재 `local_since_publish` 계산 제거 — 칩 삭제로 사장.)
- **결함 ① 관측 0 판정 마스킹** (오너 스크린샷으로 발견): 0-call 재발행이
  `fail_new==0` 을 타고 발행 index 에 "HEALTHY — 배포 안전"을 자동 선언 (홈은
  history 의 has_results 가드로 25건 유지 → 두 표면 모순). 수리(`build.py
  render`): `has_results` 없으면 **NO NEW OBSERVATIONS** 필 + "판정 불가 —
  직전 실측 판정 유지: …(ts 런 기준)" 배너 + 신규 회귀 카드 '—' (0 위장 금지).
- **결함 ② fail_new.json 발행 누락**: build 는 쓰는데 **두 발행 경로(api-test.yml
  ·publish_dashboard.sh) 모두 copy 목록에 없어** 정공법 파일이 발행된 적 없음
  (results_data 가 계속 배너-파싱 폴백으로 살던 이유). copy 추가 + build 는 관측
  0 이면 skip-write (cp 부재 = 직전 발행본 보존 — 0-call 이 상세 목록을 지우던
  것도 방지).
- 검증: `test_regression_verdict_banner` (실패/통과/오래됨 3상태 렌더 + 내역
  집계 + 가드 소스 계약) + 관측 0 **실빌드**로 index "판정 불가"/skip-write 확인
  · tests_offline 33/33.

## 홈 커버리지 = 미니 사다리 (오너 공동 설계 2026-07-15, 블록 ②)

블록 단위 공동 설계 계속 — 오너 확정: "① 배너 OK · ② 미니 사다리 + 잔존 OK ·
그 아래(파이프라인·최근 RUN)는 재논의" (이번 커밋은 ② 만, 아래 블록 불변).

- **커버리지 타일 4장 + 신규 fail 타일 → 미니 사다리 1장** (`home.html` +
  base.html `.lad.mini`/`.tiles.cov`): 대시보드·Reporting 과 같은 그림(C3 green ·
  C2 amber · C1 blue, 정의 툴팁 동일 — 한 번 배우면 세 화면이 같이 읽힘).
  - 판정 행(history)에 이미 있는 재료 사용: cov_c3 · tested/total(C2) ·
    reachable_pct(C1) · **gap_write·gap_getid** → "남은 실질 gap N — write X ·
    id-bound GET Y · waiver W 제외" + [사다리·서비스별 상세 → 대시보드].
  - 발견 기록: 구 타일의 "write-op 커버리지 (cov_op)" 라벨은 **오표기**였다 —
    cov_op = 전체 1,372 대비 검증율(write 전용 아님; write 전용은 cov_write 별도).
  - 신규 fail 타일 제거 — 판정 배너와 순수 중복. 잔존 자원 타일 유지(라이브
    비용·안전 신호는 이 화면에서 유일).
- **배너 보강 (오너 실문 "오늘 아침에도 돌렸는데 왜 21시간 전?")**: 시각 라벨을
  "MM-DD HH:MM **공식 런**"으로(툴팁: 판정은 CI 발행 런 기준 — 콘솔 로컬 런
  미반영) + 판정 이후 로컬 런이 있으면 "**이후 로컬 런 N건 미반영**" 주석
  (`app.py local_after` — 구 D2 칩의 정보를 칩/링크 없이 배너 문장으로).
- 검증: 렌더 9항목 전수 + `test_home_mini_ladder` · tests_offline 34/34.

## 홈 파이프라인 스트립 제거 (오너 확정 2026-07-15, 블록 ③) — 홈 재설계 완결

블록 분해의 결론: 세 카드 전부 **중복이거나 관객 부재** — Modeling 168/275 는
에이전트 저작 지표(Modeling 화면 소관) · Testing "진행 중 N" 은 최근 RUN 표와
중복 · Reporting % 는 미니 사다리와 중복 · 점프는 상단 네비와 중복. 오너 확정:
"파이프라인 제거, **최근 런은 유지**".

- `home.html` 스트립 + `base.html` `.pipe/.pstage` CSS + `app.py` 홈 전용 전달
  (scenario/catalog/model_stats — 헬퍼는 타 라우트가 계속 사용) 제거.
- `test_ia_catalog_absorbed_into_modeling` 의 구 계약("3단계 현황" 존재)을
  스트립 **부재** 계약으로 개정.
- **최종 홈 = ① 회귀 판정 배너 · ② 미니 사다리 + 잔존 자원 · ③ 최근 RUN** —
  "오늘 처리할 것 / 목표 진척 / 방금 돌린 것" 세 질문에 1:1 대응.

## 최근 RUN 표 개편 (오너 '전부 반영' 2026-07-15, 블록 ④ — 마지막)

표가 "어떻게 됐나"까지 답하게 — 5개 제안 전부 승인:
1. **status 결과 요약**: `failed · lifecycle 3/118 실패` — 로컬 종료 런만,
   `c2._local_run_summary`(런 상세와 같은 소스) 재사용 + 캐시(15s 폴링이 이벤트
   파일을 재독하지 않게). CI 런은 현행 status 만.
2. **시간 1열화(KST)**: UTC ISO 2열(requested/finished) → `시작 07-14 14:25 ·
   1시간 18분` — 배너 판정 시각과 같은 시간 언어 ("왜 21시간 전" 혼란 공범 제거).
3. **profile 열 제거** — 로컬 런은 항상 '-' (정보 0).
4. **연속 실패 경고**: 최신 종료 런부터 3건+ 연속 failed 면 표 위 한 줄
   `⚠ 최근 N건 연속 실패 — 마지막 성공 …` (진행/대기 중 행은 스트릭에서 건너뜀).
5. **하단 대시보드 링크 제거** — 상단 네비+사다리 카드와 3중복. 전체 히스토리만 유지.
- 구현: `app._runs_view`(홈·/partials/runs·testing 공통 컨텍스트) +
  `_runs_table.html` 재작성. 검증: `test_runs_table_view`(라벨 KST/소요·요약·
  스트릭·건너뜀·통과 문구·템플릿 계약) + 렌더 6항목 · tests_offline 34 passed.

## 대시보드 정비 (오너 지시 2026-07-14)

- **서비스별 Platform 링크 제거** ("대시보드에 platform 링크들이 모든 서비스마다
  있는데 없애줘"). `dashboard/build.py` 에서 서비스마다 붙던 `🧩 Platform →`
  딥링크 2종을 제거: ⓐ 인덱스 서비스 카드의 `catLink()`(→`svc-cat-link`) ⓑ 서비스
  상세 페이지 breadcrumb 의 `catlink`. 관련 CSS(`.svc-cat-link`·`.crumb .catlink`)도
  삭제. **전역 헤더의 단일 `🧩 Platform 실행 콘솔 →` 링크는 유지**(서비스별 아님).
  로컬 빌드 검증: index/service 페이지에서 링크 0건 · 전역 1건. **발행 반영은 다음
  대시보드 publish(api-test.yml CI) 부터** — 로컬 빌드는 결과 저장소가 없어 수치가
  비므로 dashboard-data 에 직접 푸시하지 않는다(진짜 커버리지 덮어씀 방지).

## 모델링 화면 개선 ①~⑤ (오너 승인 2026-07-11 "모두 반영해")

- **① 전역 의존 그래프 탭 제거 + 미니그래프 인스펙터** — P2C-25 결정 이행
  (v2 D6와 동일 결론): 275노드 전체 지도 탭 삭제, 서비스 행 **🕸 의존 ▸**
  클릭 시 그 서비스의 의존 폐쇄집합만 드로어로 (donor: v2 svc_graph.js —
  `POST /api/graph` + `resource_graph.js` scene() 원본 재사용, 서비스당 1회
  lazy 로드). 노드 클릭 = 인스펙터(id·service·**노드 편집 →**).
  `/planning/resources/map.json` 라우트는 보존(console2 run 뷰 공유).
- **② 출처·단위 정직화** — KPI 아래 "저장소(main) 기준" 註 + "VALIDATED는
  모델 노드 단위 — 엔드포인트 단위 검증은 Reporting·발행 대시보드" (§2.7).
- **③ `?q=` 딥링크** — 전역 검색/외부에서 필터 프리필 (카탈로그와 동일 관례).
  /search 서비스 결과의 Modeling 링크가 q를 나른다.
- **④ 서비스 행 ▶ 실행** — `/testing?service=<cat>/<svc>` prefill (자동 발사
  아님). 수리 1건: `.epbtn` 스타일 공유 탓에 기존 엔드포인트 토글 핸들러가
  🕸/▶에도 바인딩(▶ 내비게이션 차단·빈 드로어) — 핸들러를 `.epbtn[hx-get]`로
  한정 (실측으로 발견).
- **⑤ 카테고리 검증 진척 미니바** — VALIDATED/노드 비율, 카테고리 간 비교.
- 검증: `test_modeling_improvements_batch` (24/24) + 실주행(미니그래프 로드·
  인스펙터·▶ prefill 내비게이션·엔드포인트 드로어 회귀 무).

## 접목 1 상세 — 무엇을 어디에 얹었나

- **배지 매크로** `controlplane/templates/_badges.html` (donor: v2 `_badges.html`):
  `badge(source, ts, ident, stale)` — `published`(파랑)/`local`(초록)/`run`(보라)
  /+`ci`(파랑 재사용, 병합 타임라인 행별 출처용 — 계약 §2.1 "CI/이 서버").
  stale=True → 노랑. CSS는 base.html 디자인 토큰으로 재구현.
- **홈** (`home.html` + `app.py home()`): 판정 배너에 Published 배지(기존 '노후
  스냅샷' chip 흡수) + **D2 보조 칩** "발행 이후 이 서버 런 N건 →"(완료된
  `local-` 런만, 판정 불변). 타일 5장 배지 + 노후 시 수치 회색화(`is-stale`).
  파이프라인 스트립: Modeling="저장소 기준" note(§2.7 — 새 배지 종류 발명 금지),
  Testing=local, Reporting=published.
- **리포팅** (`reporting.html`): summary 배너/사다리/타일·triage에 Published
  배지 + 노후 회색화. empty-state 표준 문구(§3 — "관측 없음 ≠ 0").
- **런 타임라인** (`_runs_table.html`): source 컬럼 신설 — `gh_run_id`
  `local-` 접두=This server, 숫자=CI. empty-state: "이 서버에 run 기록 없음 —
  공식 수치는 발행본 기준" (v1 최대 약점 "fail N vs run 없음" 모순 해소).
- **런 상세** (`run_detail.html`): This run 배지(S3 — 과거형 고정).
- **발행 시각 라벨** `common.snap_ts_short()` — history ts(=판정 런 시각, v2
  published.py 실측 교훈: 발행 저장소 갱신 시각과 구분)를 KST 짧은 라벨로.
- 테스트: `tests_offline.py::test_source_badges_and_empty_states` (24/24 통과).

## 접목 2 상세 — PLAN vs ACTUAL 스트립 (console2 실행 화면)

- **위치**: `console2/index.html` `#planactual` — now-playing 바로 위, run이
  in-flight(running/queued)일 때만 표시 (donor: v2 `run_exec.js` B층 +
  `run_detail.html` plan-strip, 오너 승인 목업 §2.9).
- **PLAN**: `POST /api/plan {lifecycle_ids: rec.lifecycle_ids}` 서버 재계산
  (run별 1회 캐시) — 생성 ~n · 삭제 ~n · peak VPC. **시간 예측은 2026-07-11
  정합 개정(아래)에 따라 schedule-sim makespan 단일 소스** — 초기 접목의
  병렬-6 근사는 제거(결정 지점 4 개정).
- **ACTUAL**: 이벤트 실측(resource-tracked/-deleted 집계) + 경과 + VPC 슬롯
  미터(`/api/capacity` — 기존/이 런 peak/다른 런/여유 구분). queued면
  **WHY QUEUED**(여유 < 필요 peak 수치).
- **편차는 보수적으로**: "ETA 초과" 칩만. 지연 의심(실측 평균 ×3) 판정은
  접목 4로 미룸 — 엔진 요청 #5(세마포어 대기 이벤트) 전에는 VPC 대기가
  지연으로 오탐된다 (§2.9 명시). 테스트가 `avg * 3` 부재를 고정.
- 검증: 오프라인 계약 테스트(`test_plan_actual_strip_frontend_contract`, 33/33)
  + simulate 런 실주행(Playwright headless — PLAN 고정·ACTUAL 폴링 갱신·종료 시
  숨김 확인).

## 접목 3 상세 — 종료 후 다음 행동 카드 (console2)

- **위치**: `#donecard` (PLAN/ACTUAL 스트립 아래) — run 종료 전이 시
  `onRunEnded()`가 렌더. 토스트는 짧은 확인용으로 유지, 카드가 본체(사라지지
  않음, ✕ 닫기 / 새 런 in-flight 시 자동 숨김).
- **구성**: 헤더 "run 종료 — n/m passed" + **계획 대비 회고 1줄**(접목 2의
  runPlan 재사용: 계획 생성~/ETA → 실제 생성·삭제·경과) + 3줄 —
  ① fail: 실패 lifecycle 나열 + [→ 실패만 보기](레일 fail 필터)
  ② +검증: `GET /api/runs/{rid}/fold-evidence`(신설, donor: v2
    run_detail_data._fold_status 이식) — "공식 미반영 검증 증거 약 N건" +
    [공식 반영 절차] 모달(미리보기 표 + derive_verified→promote_validated→
    검토 커밋 3단계 **안내만** — 콘솔은 fold를 실행하지 않음, Hard Rule 7)
  ③ 잔존: 재스캔(+5m/+15m) 감시 안내 + [🔍 지금 확인] = 기존 `scanOwned()`
    재사용(두 번째 스캔 경로 발명 금지).
- fold-evidence는 시간창(±30s) 근사 — 화면 표기는 반드시 "약 N건"
  (available=False(계산 불가) ≠ count 0, empty-state 규율 준수).
- 검증: 계약 테스트 2건 추가(카드 + fold API, test_console2 35/35 ·
  controlplane 22/22) + simulate 실주행(Playwright — 종료 전이 시 카드 렌더,
  스트립→카드 교대, 관측 파일 없는 환경의 '계산 불가' 정직 표기 확인).

## Reporting 개선 A (오너 승인 2026-07-11) — 트리아지 신규 fail 상세

- **fail_new.json 신설 발행** (`dashboard/build.py` — 엔진 요청 #1 정공법):
  new/known 전체 목록(배너 [:6] 상한 없음) + updated·run_type. 발행 검증 완료
  (오프라인 빌드 실행 — 스키마 {new, known, updated, run_type}).
- **`controlplane/results_data.py`** (donor: v2 results_data.py §2.5):
  fail_new.json 우선 + 미발행 동안 index.html 배너 파싱 폴백(상한 6건을
  화면에 명시). 보강: "당시 500 → 현재 201 (복구 관측)" 분리 표기 ·
  발행 서비스 상세 딥링크(`/dashboard/services/<slug>.html`) + Modeling ?q= ·
  합성/카탈로그 이중 기록 힌트(병합 않고 배지만) · known 목록(저장소 baseline).
- **트리아지 탭**: "신규 fail 상세" + "이미 알던 실패 — 추적 중" 2섹션 추가.
  empty-state 규율 준수 (접근 불가 ≠ 0건).
- 검증: `test_triage_new_fail_detail_and_known_list` (25/25).
- **미결(오너 질문 2026-07-11)**: 색칠지도(/reporting/coverage) 존폐 —
  세션 의견은 제거(전체-지도 뷰의 3번째 반복 탈락 + 로컬 관측 원천의 오독,
  기능은 발행 대시보드·Modeling 진척 바가 대체). 오너 결정 대기.

## 성능 수리 (오너 실측 제보 2026-07-11) — 대형 런 렌더 다이어트

- **증상**: 1,500+ 호출 런의 API 탭에서 "soft 건수를 누르면 화면이 멈춤".
  재현(합성 1,600호출 + 라이브 2,200호출): 폴 틱당 54–274ms 롱태스크 +
  soft 타일은 원래 클릭 무동작 요소(필터 기대와 불일치).
- **수리 3종** (수리 후 라이브 재측정 롱태스크 0건):
  1. `groupedRun()` 메모이즈 — runEvents 배열 참조가 캐시 키. 한 틱의 여러
     소비자(drawReport·runProgress·rail·PLAN/ACTUAL·간트)가 전체 이벤트를
     각자 재스캔하던 것 제거. `lifecycleStates()`도 캐시 사용.
  2. **kpi 타일 = 결과 필터** (api 호출/ok/soft/fail 클릭 토글) — 오너가
     기대한 동작. 타일 필터는 dup-hide 무시(soft 266 타일인데 전부 dup이라
     0행이 나오는 모순 실측 → 원본에서 거름).
  3. **행 상한 500** + 표 첫 행에 "최근 500건만 표시 — 이전 N건 생략" +
     [전체 표시] opt-in (묵살 금지). 스코프/런 전환 시 필터·상한 리셋.
- 검증: `test_large_run_render_diet_frontend_contract` (36/36) + Playwright
  실측 (필터 20ms · 전체 표시 1,334행 32ms · 라이브 롱태스크 0).

## 스케줄 = 이 서버 LIVE 런 (D5 미결 해소 — 오너 승인 2026-07-11 "진행해")

- **결정**: v2 D5가 미결로 남긴 "스케줄 방식"을 확정 — GHA workflow_dispatch
  (오너: "실제로 사용 안 함") 대신 **controlplane 자체 스케줄러가 콘솔 엔진
  로컬 LIVE 런을 발사**. 신설 `tools.console2_server.launch_suite_run()`:
  suite 해석(request 게이트 + scope, scope 없으면 전체 enabled·role=verify),
  admission 큐·동시 LIVE 가드·run 기록/미러 전부 기존 로컬 런 경로 재사용.
- **안전 규칙**: heavy(과금) suite 는 등록(400)과 발화 양쪽에서 거부 —
  무인 실행은 pre-flight opt-in 이 아니다 (Hard Rule 1). heavy lifecycle 은
  선택 해석에서도 항상 제외. LIVE 진행 중이면 그 발화는 건너뜀(다음 주기).
  발화 실패는 failed 런 행으로 기록(사유 포함) — 조용히 사라지지 않음.
- **한계 (UI 문구로 명시)**: 서버 프로세스가 켜져 있어야 동작 · profile 은
  로컬 실행에서 무시(서버 .env 기준) · 발행 파이프라인(CI)은 별개 —
  스케줄 런 결과의 공식 반영은 기존 fold/CI 경로 그대로.
- 사용법: `/testing` 하단 스케줄 섹션 — 예: cron `0 20 * * *`(UTC = KST 05:00)
  + suite `full` = 매일 새벽 전체(비과금) 회귀.
- 검증: `test_launch_suite_run_contract`(37/37) +
  `test_scheduler_fires_local_suite_run_and_heavy_schedule_blocked`(27/27).

## 결정 지점 (오너가 뒤집을 수 있게 기록)

1. **노후 기준 48h 유지** — v2 계약은 24h, v1은 기존 P2C-12 결정(공용
   `common.STALE_AFTER_H=48`)이 있어 하나의 규칙(48h)로 통일 유지. 24h로
   낮추려면 `common.STALE_AFTER_H` 한 곳만 바꾸면 됨.
2. **타일 배지 밀도** — 홈 타일은 카드마다 배지(계약 §0-4), 리포팅 타일 4장은
   섹션 헤더 배지 1개 + 회색화만(같은 출처 반복 노이즈 절충). 오너 검수 시 조정.
3. 배지 라벨은 donor 그대로 영어(Published/This server/This run/CI), 툴팁
   한국어 — **오너 재확정 (2026-07-14 "한국어화는 필요없어")**. 단 같은 검수에서
   **표기 원칙이 개정**됨: 기본 출처는 헤더 1곳 선언, 배지는 예외에만 (아래
   'Overview 출처 표기 다이어트' 참조 — 한때 한국어 전환했다가 같은 날 원복).
4. ~~PLAN ETA 가정 = 병렬 6~~ **개정 (2026-07-11): 예측 단일 소스 =
   `/api/schedule-sim` makespan** — 기존 세션이 main에 넣은 콘솔 간트
   (cf8792b3, '예측 vs 실제 타임라인' 패널)와 같은 예측을 스트립·종료 카드가
   **pvaSim 캐시 공유**로 그대로 쓴다 (같은 화면에 가정이 다른 예측 둘 금지,
   run 당 POST 1회). 편차 칩 용어도 패널 amber와 동일한 "예측 초과"로 정합.
   스트립 [📊 타임라인] 딥링크 = 요약(스트립) → 상세(간트 패널) 역할 분담.
   부수 수리: 이벤트 도착 전 빈 lifecycle_ids로 예측 요청 시 서버가 전체
   플랫폼(124종) makespan을 돌려줘 이 run 예측처럼 오독되던 것 — ids 확보
   전에는 요청하지 않게 (기존 패널 코드의 잠재 결함이었음).

## 경계 메모

- 이 접목은 `controlplane/*`(v1) 수정 — V2-KICKOFF의 구 경계 규약(v2 세션은
  v1 불가침)은 선회로 소멸, 접목 주체는 **이 세션**(오너 확정, V2-WRAP §5 P3).
- 엔진(`regression/**`, `core/**`)·발행 파이프라인(`dashboard/**`)은 불변.
