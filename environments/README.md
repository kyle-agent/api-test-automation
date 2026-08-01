# environments/ — environment profiles (regression targets)

One YAML per test target (검증계/운영계 × region). A run is always
**suite × profile** — the suite says *what* to run (`suites/`), the profile
says *against which environment* (docs/PLATFORM-PLAN.md §2.1).

```
python -m core.profiles list                    # what targets exist
python -m core.profiles validate                # offline check (CI: validate.yml)
python -m core.profiles export stage-kr-west1   # KEY=VALUE lines for $GITHUB_ENV
eval "$(python -m core.profiles export stage-kr-west1 --shell)"   # local shell
```

How a profile reaches a run:

* **workflow_dispatch** — the `profile` input.
* **file trigger** — a `profile=<id>` line in `.github/run-request`.
* **locally** — `eval` the `--shell` export before running pytest.

Each job in `api-test.yml` applies the profile right after dependency install
(`core.profiles export >> $GITHUB_ENV`), overriding the repo-vars defaults for
every later step. With no profile given, behaviour is exactly as before.

Key fields (see `core/profiles.py` for the full schema):

* `env:` — engine variables set verbatim (`SCP_REGION`, `SCP_ENV`,
  `SCP_SERVICE_HOSTS`, …). Only engine-known keys pass validation.
* `credentials:` — **references only**: `TARGET: SOURCE_ENV_VAR_NAME`. The
  exporter resolves the source from the calling environment; profiles never
  contain secret values and are safe to commit.
* `forbid:` — hard per-environment safety gate (`mutations`, `destructive`,
  `heavy`). Exported as `SCP_PROFILE_FORBID`; `core/config.py` refuses the
  matching `SCP_ALLOW_*` flags even when the trigger set them — this is what
  makes a production profile read-only by construction.
* `quota_overrides:` — per-account resource caps, exported as
  `SCP_BUDGET_LIMITS` and merged over `core/budgets.py` defaults.

## 다른 오퍼링/계정으로 전환할 때 체크리스트 (2026-07-29 실전 검증)

`SCP_ACCESS_KEY/SECRET`·`SCP_REGION/ENV`를 새 대상으로 바꾸면 **테스트 자체는
자기충족**이다 — 단 아래 3가지만 알면 된다:

1. **oplog 미러만 수동 조치** (유일한 교차-계정 고정 자원). 영구 버킷
   `apitest-oplog-permanent`는 기존 검증계 소유라, 새 자격으로는
   `IncorrectUserXAuthTokenException`으로 미러가 실패한다(런은 계속됨 —
   best-effort). 콘솔 띄우는 셸에 3종 세트를 추가:
   ```bash
   export SCP_OPLOG_ACCESS_KEY=<기존 검증계 AK>   # 둘 다 있어야 발동
   export SCP_OPLOG_SECRET_KEY=<기존 검증계 SK>
   export SCP_OPLOG_S3_ENDPOINT=https://object-store.kr-west1.e.samsungsdscloud.com
   ```
   (endpoint 기본값은 현재 `SCP_REGION/ENV`에서 합성되므로 키만 바꾸면
   새 오퍼링 호스트에 기존 키로 인증하게 된다 — 반드시 같이 고정.
   이 endpoint 핀은 **미러 전용**이다: 2026-07-29 수리로 keys="test" 픽스처
   (logsink·image-asset)는 핀을 따라가지 않는다 — 수리 전 코드로 돌리면
   logsink ensure가 구-계정 호스트로 새어 나가 network-logging 계열이
   400 storage-invalid-bucket이 난다. run 11f2 실측.)
2. **시나리오용 버킷은 전부 자동** — `shared_infra`가 런 시작 시 현재 테스트
   계정 키로 `apitest-logsink`(DB log-export·DC firewall 로깅·loggingaudit
   trail·network-logging)와 qcow2 이미지 자산(버킷+객체, git `assets/` 원본)을
   멱등 ensure한다. `SCP_OPLOG_*` 오버라이드는 이 둘에 **의도적으로 미적용**
   ("새 계정 자기충족"). placeholder 버킷(`regrcoveragebucket` 등)은 관용
   스텝이라 무시. 실패 시 stderr `[shared_infra] ... ensure 실패` 확인.
3. **대상의 API 버전이 다르면** `SCP_API_VERSION_PIN=false`로 핀을 꺼서
   "그 서버의 CURRENT"를 테스트하라 (구버전 오퍼링에 신버전 핀 = 406 폭풍;
   업그레이드 전/후 비교 런은 양쪽 다 PIN=false로 동일 조건 유지).
   상세: `docs/API-VERSIONING.md`.
4. **가용영역/서버타입이 다르면 env 토큰으로 핀**:
   - `SCP_ZONE=<존>` — 존 규칙이 다른 리전 (기본: kr-west1→`-b`, 그 외→`-a`
     — 실측 기반 자동). 2026-08-01부터 시나리오의 `{region}-b` 준-리터럴도
     전부 `{zone}` 토큰이라 이 env가 모든 존 값을 지배한다.
   - 서버타입 세대가 다른 오퍼링 (2026-07-29 west1 실측: s1 풀 고갈로 전
     DB 클러스터 FAILED, "신규 VM은 s2로"; 노드명은 패밀리별 —
     VM `s2v{cpu}m{mem}` · DB `db2v…` · eventstreams `ess2v…`):
     - VM: `SCP_VS_SERVER_TYPE_PREFIX=s2` 하나로 충분 — 캡처 스텝은 min_by가
       s2 최소(s2v1m2)를 집고(2026-08-01: min_by 없던 VS 캡처가 첫 매치
       s2v10m120을 집던 결함 수리), 타입을 바디에 박던 create 6곳은
       `{vs_server_type}` 토큰이 접두에서 바닥을 유도(s2→`s2v1m2`).
       풀네임 접두(`s2v1m2`) = 정확 핀; 유도가 틀리는 오퍼링은
       `SCP_VS_SERVER_TYPE=<풀네임>`으로 명시 오버라이드.
     - DBaaS **권장**: `SCP_DB_SERVER_TYPE="*"` — type 필터(기본 Standard-1)
       해제; min_by가 각 패밀리의 최소 사이즈(db2v2m4·ess2v2m4)를 자동 선택
     - 응답에 구세대가 섞여 또 자원부족이 나면 이름 핀:
       `SCP_DB_SERVER_TYPE_NAME_PREFIX="mysql=db2,eventstreams=ess2,*=db2"`
       (평문 "db2" = 전 스텝 동일; 맵 = lifecycle service별; 이름 핀 시
       type 필터 자동 해제)
