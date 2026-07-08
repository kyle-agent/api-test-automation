---
status: superseded (legacy poc console/console_server.py — 현행은 controlplane 척추 + console2)
for: all
superseded_by: ../../console2/README.md
---

# Platform Console — 로컬 실행 플랫폼 핸드오프

> **⚠️ SUPERSEDED.** 이 문서가 다루는 로컬 콘솔(`tools/console_server.py` +
> `poc/scenario-viz/console.html`, port 9000)은 **console2 + controlplane 척추**로
> 대체되었다 (`console2/README.md`, `controlplane/README.md`; 확정 IA:
> `docs/working/plans/PLATFORM-IA-DIRECTION.md`). 역사 기록으로만 보존.

> 다음 세션이 이어서 작업하기 위한 현재 상태 스냅샷. 분기: `claude/start-here-review-5z8jt2`
> (이번 작업은 매 커밋 `main`으로 FF 했음 — 둘이 동일). 최신 커밋: `152e9b3c`.

## 무엇을 만들었나 (한 줄)

`platform/console.html`(서비스/조합 선택 그래프 UI)에서 **고른 서비스를 실제로 실행**하고,
**진행/로그를 보고**, **강제 클린업 + 정리 확인**까지 하는 **로컬 백엔드 서버**(`tools/console_server.py`).
= 사용자가 `git pull` 후 로컬에서 띄워 쓰는 형태 (option 3).

## 실행 방법

```bash
git pull
pip install -r requirements.txt        # stdlib 서버지만 실제 실행엔 pytest 등 필요
cp .env.example .env                    # SCP_REGION + ACCESS/SECRET/PROJECT 채우기
python tools/console_server.py          # → http://127.0.0.1:9000/   (PORT= 로 변경)
```

Plan 탭에서 서비스/조합 선택 → **실행 ▶** → Run(시뮬+실제) → **Report** 탭에서 실제 실행 기록·로그·클린업.

## 아키텍처 / 핵심 파일

- **`tools/console_server.py`** (stdlib `http.server`, zero-dep) — 콘솔 서빙 + 실행 엔진.
  - 기본 포트 **9000** (`PORT` env override). WEB 루트 = `poc/scenario-viz/`.
  - 엔드포인트:
    - `GET /` → console.html, `GET /<static>` → 번들(data/ assets/ kdocs/) (경로탈출 차단)
    - `GET /api/runs` (목록, 최신순) · `GET /api/runs/<id>` (full=로그 tail 200줄)
    - `POST /api/run` `{crud_ids[], parallel, heavy}` → lifecycle 실행
    - `POST /api/cleanup` → **강제 클린업** (reconciler, `SCP_SWEEP_IGNORE_TTL=true`)
    - `POST /api/verify` → **정리 확인** (`cleanup.verify_clean`, read-only)
  - run record `kind` ∈ `lifecycle|cleanup|verify`. `_summarize(kind, log)` 가 종류별 헤드라인:
    pytest 요약 / `🧹 N resource(s) deleted` / `✅ clean — owned survivors: 0` | `⚠️ N owned survivors`.
  - **lifecycle 실행 흐름** (`_run_worker`): heavy면 `_provision_shared`(shared VPC 1개 생성,
    `SCP_SHARED_*` env 주입) → `pytest tests/crud -m crud -n N` (env `SCP_CRUD_IDS`,
    mutations/destructive/heavy 게이트 ON) → `_teardown_shared`(그 VPC만 id로 정확 삭제) →
    reconciler sweep(catch-all).
  - run 로그 파일: `reports/console-runs/<id>.log` (gitignored). run record는 인메모리
    (서버 재시작 시 `/api/runs` 비워짐, 로그 파일은 남음).

- **`poc/scenario-viz/console.html`** — 콘솔 UI (탭: catalog/plan/run/report).
  - `const LOCAL=!location.hostname.endsWith("github.io")` — 로컬 서버면 `execRun()`(POST /api/run),
    github.io면 GitHub `/new` URL(chat-heavy 트리거)로 분기. `runBtn()`이 그 분기 담당.
  - **Report 탭** = 실제 실행 기록 패널(`renderLiveRuns`): `/api/runs` 목록 + 행 클릭 시
    `/api/runs/<id>` 전체 로그 펼침, 실행중이면 3초 자동 폴링(`runsTimer`, 탭 전환 시 `stopRunsPoll`).
    헤더에 **🧹 강제 클린업**(confirm-gated) + **🔍 클린업 확인** 버튼.
  - 우하단 floating `#runstat` = execRun 직후 상태 박스 + "Report에서 로그 보기 →" 링크.

- **`poc/scenario-viz/build_data.py`** — 노드별 `lifecycle`(= `source.lifecycle`) 방출.
  콘솔이 선택 노드 → `lifecyclesFor()` → `crud_ids` 매핑에 사용. (재생성: `python poc/scenario-viz/build_data.py`)

- **`tests/crud/conftest.py`** — `SCP_CRUD_IDS` 정확-id allowlist(매칭 외 deselect) + longest-first 정렬.
- **`conftest.py`** (루트) — `shared_vpc` 픽스처. `-n`(xdist 워커)에서 `SCP_SHARED_VPC_ID` 없으면
  `{}` 반환 → 순수 adopter 스킵. 그래서 서버가 provision 해줘야 함(위 _run_worker).
- **`.github/workflows/chat-heavy.yml`** — github.io 경로용 CI 실행기(파일 push 트리거). 서버와 동일한
  provision→pytest(-n xdist)→sweep 구조. `.github/chat-heavy-request.d/**` 의 최신 파일 또는 legacy 파일 파싱.

## 알려진 이슈 / 주의

1. **컨테이너 내 egress-proxy 503**: 이 원격 컨테이너에서 실제 SCP 호출(특히 reconciler sweep)이
   Claude egress 프록시 503 폭풍에 막힘 → sweep/verify가 끝까지 안 감. **환경 한정 — 사용자 로컬
   네트워크에선 정상.** (근거: `knowledge/validated-facts.md` 503=egress-proxy)
2. **adopter 스킵 버그는 수정됨**: SKE/MySQL이 `1 skipped`로 끝나던 건 서버가 shared VPC를 provision
   안 해서였음(`62f9415f`에서 해결). 이제 heavy 실행은 VPC를 만들고 adopter가 adopt함. SKE 실제 실행은 ~40-50분.
3. **미해결 — `application-apigateway-api-resource`**: 사용자 로컬에서 `1 failed in 1.95s`로 즉시 실패.
   shared VPC와 무관(비-VPC lifecycle). 그 run의 `=== pytest ===` 섹션 트레이스백을 봐야 원인 파악 가능.
   **다음 세션 TODO 후보.**

## 안전 규칙 (절대 불변 — CLAUDE.md Hard Rules)

- 안전 게이트(`SCP_ALLOW_MUTATIONS`/`SCP_ALLOW_DESTRUCTIVE`/`SCP_RUN_HEAVY`)는 "테스트 통과시키려고"
  켜지 말 것. 서버는 **사용자가 실행 버튼을 눌렀을 때 그 요청에 한해서만** 게이트를 켬(chat-heavy와 동일 opt-in).
- `.env` 커밋/로그 금지. 이름추측 삭제 금지(`core.registry` 소유 태그 경유). 워크플로 1회 1런.
- 분기 `claude/start-here-review-5z8jt2`에 커밋, 요청 없이는 PR 만들지 말 것.
- 모델 식별자(claude-opus-4-8[1m])는 커밋/PR/코드 어디에도 넣지 말 것.

## 다음에 할 만한 것 (열린 항목)

- [ ] apigateway lifecycle 즉시 실패 원인 분석/수정 (실제 로그 필요).
- [ ] run record 영속화(서버 재시작 후 `reports/console-runs/*.log` 를 `/api/runs`에 복원).
- [ ] verify를 read-only 빠른 동기 응답으로도 노출(현재는 tracked run).
- [ ] (선택) 콘솔에 "전체 실행 히스토리" 별도 뷰 / kind 필터.
