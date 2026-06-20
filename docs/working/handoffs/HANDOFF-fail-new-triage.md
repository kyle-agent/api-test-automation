# HANDOFF — fail_new triage (full heavy run 2026-06-10)

- Date: 2026-06-10 · Branch: `claude/fail-new-classification-optimization-kgn8gf`
- Status: **active**
- Source: run [27258520218](https://github.com/kyle-agent/api-test-automation/actions/runs/27258520218)
  (sha `e3ba190`, the "full heavy coverage run") — `history.jsonl` recorded
  **fail_new = 52**. The 52 TSV rows collapse to **27 unique failures** (write
  steps are double-recorded: once as `lifecycle:step`, once under the catalog
  key; the two `check-duplication` GETs are single rows).
- Evidence: `regression-reports` artifact of that run (`smoke_status.tsv`,
  `results/observations.jsonl`); `data/baselines/known_issues.json` had 1 match
  (billingplan `listplannedcomputeservertypes` 500 → fail_known).

Classification asked by the handoff: **BODY-FIX** (request/harness 보정으로
풀릴 가능성) vs **DOMAIN-HUNT** (도메인 지식/실자원 필요) — plus a third bucket
that emerged from the evidence, **KNOWN-RED candidates** (재현되는 백엔드
동작; 1회 재검 후 `known_issues.json` 등재 후보).

## A · 401 with a valid HMAC — 6 unique

Same signature works for sibling calls in the same lifecycle (they 400, not
401), so these are endpoint-specific.

| Endpoint | Status | Class | Hypothesis / next action |
|---|---|---|---|
| `database/cachestore/cachestoreremovebackuphistories` PUT `/v1/clusters/{id}/backup-histories` | 401 | DOMAIN-HUNT → known-red 후보 | backup-subresource auth quirk, 3 engines affected. Retry once during a live-cluster window; if it still 401s, register all four in known_issues as one family. |
| `database/postgresql/postgresqlremovebackuphistories` PUT 〃 | 401 | 〃 | 〃 |
| `database/mysql/mysqlunsetbackup` DELETE `/v1/clusters/{id}/backups` | 401 | 〃 (quirk already noted in commit `30006a8`) | 〃 |
| `database/postgresql/postgresqlunsetbackup` DELETE 〃 | 401 | 〃 | 〃 |
| `container-scr-registry:check-registry-name-dup` GET `/v1/container-registries/check-duplication/name` | 401 | **BODY-FIX (harness)** | Both 401 GETs are exactly the ones needing a **query string** (`?name=…`). Suspect the HMAC `encodeURI(url)` signing vs the sent URL diverges when query params are present. Test: sign with and without the query; compare against a working param GET. Fix lives in `core/auth.py`/`core/http_client.py`, not the scenario. |
| `devopsservice-write-coverage:check-name-duplication` GET `/v1/devops-services/check-duplication` | 401 | **BODY-FIX (harness)** | 〃 |

## B · 500 on DBaaS guarded sub-ops (no live cluster at call time) — 8 unique

`epas/mariadb`: `set-archive` PUT, `register-log-export-config` POST,
`upgrade-kernel` PUT (×3 each) · `mysql`: `upgrade-kernel` ·
`sqlserver`: `register-log-export-config`.

- Class: **DOMAIN-HUNT (재시도 조건)** — these ran with a placeholder/stale
  `cluster_id` and the backend 500s instead of 4xx-ing (cachestore's identical
  situation returns 400 soft — note the asymmetry). Two actions:
  1. Re-run these sub-ops **while the heavy-shared-dbaas clusters are alive**
     (schedule the guarded sub-op lifecycles into the same run window) — body
     shapes are still docs-derived and unproven.
  2. The 500-on-garbage-input behavior itself is a conformance robustness
     finding candidate (axis 2), not a regression of ours.

## C · 500 on bulk / body-shape writes — 5 unique

| Endpoint | Class | Next action |
|---|---|---|
| `management/iam/deletepolicies` DELETE `/v1/policies/bulk` | **BODY-FIX** | likely `{ids: []}` empty bulk → backend 500. Send one synthetic id (expect 4xx, records C2 cleanly) or capture a deletable policy id. |
| `management/iam/deletesamlproviders` DELETE `/v1/saml-providers/bulk` | **BODY-FIX** | same empty-bulk suspicion + the `createsamlprovider` body is known-corrupt (needs real SAML metadata, ledger note). |
| `management/resourcemanager/updatetags` PUT `/v1/tags/bulk` | **BODY-FIX** | re-derive the bulk tag envelope from api_docs; avoid empty arrays. |
| `management/resourcemanager/deletetags` DELETE `/v1/tags` | **BODY-FIX** | 〃 |
| `management/resourcemanager/setresourcegroup` PUT `/v1/resource-groups/{id}` | **BODY-FIX** | update-body shape (the create works — diff create vs set fields). |

## D · 500 on creates/setters — 8 unique

| Endpoint | Class | Next action |
|---|---|---|
| `management/iam/createrole` POST `/v1/roles` | **BODY-FIX** | trust/assume-policy document shape — re-read the API doc page (`iam-role-full` lifecycle). |
| `management/iam/accesskeycreate` POST `/v1/access-keys` | DOMAIN-HUNT | may require a real target `user_id`; check whether self-issued keys are allowed for the API principal. |
| `application-service/apigateway/setresourcepolicy` PUT | **BODY-FIX** | policy document shape — userguide has a dedicated "리소스 기반 정책 가이드" page; mine it. |
| `application-service/apigateway/createprivatelinkendpoint` POST | DOMAIN-HUNT → known-red 후보 | synthetic `privatelink_service_id` was EXPECTED to 4xx (ledger note) but the backend 500s — needs a real privatelink service to verify; the 500-on-bad-id is a robustness finding. |
| `storage/backup/createbackup` POST `/v1/backups` | DOMAIN-HUNT | backup needs a real target server + agent/policy prerequisites (userguide: Backup Agent). Blocked on a server resource in-run. |
| `storage/parallel-filestorage/createvolume` POST `/v1/volumes` | **BODY-FIX** 우선 | re-derive body; if it persists it likely needs PFS infra entitlement (then DOMAIN). |
| `networking/dns/createpublicdomainname` POST `/v1/public-domain-names` | DOMAIN-HUNT / **waiver 후보** | registers a real public domain (external registrar + billing). Owner decision: waiver like archivestorage/org-writes. |
| `container/scr/updatepublicendpointenabled` PUT `/v1/container-registries/{id}/enable-public-endpoint` | DOMAIN-HUNT | state-dependent toggle on a real registry that otherwise works; check required body (`{enabled: bool}`?) and registry state preconditions. |

## Suggested order of attack

1. **Harness 401 (A.5–6)** — one signing fix may clear both `check-duplication`
   GETs; cheap to test in any read-only run (they're GETs).
2. **Bulk bodies (C)** — five fixes in two services' fragments, no resources
   needed; verifiable in the next mutation run.
3. **DBaaS sub-ops window (B + A.1–4)** — needs scheduling work so guarded
   sub-ops run while heavy clusters are alive; piggyback the 401-family retry.
4. **D** — per-endpoint; two are owner decisions (waiver for public-domain,
   privatelink known-red).

## Bookkeeping

- The cachestore engine-version hypothesis from the previous session is
  **resolved** — see `knowledge/formal/services/database__cachestore.yaml`
  (create got a live 202 in this same run; the gap is cluster-window timing).
- BM blockstorage is **not** in fail_new (its calls are `soft`), but the same
  run's evidence + the userguide retry pinned its blocker: create REQUIRES
  1–8 attached Bare Metal Servers —
  `knowledge/formal/services/storage__baremetal-blockstorage.yaml`.

## Owner decisions (2026-06-10, applied to coverage_waivers.json)

- **BM 계열 C2-only**: storage/baremetal-blockstorage(39) + compute/baremetal(14)
  + compute/multinodegpucluster(13) — real BM server is billing-prohibitive.
- **라이선스 클러스터 C2-only**: database/sqlserver(33) +
  data-analytics/vertica(19) + data-analytics/searchengine(22).
  (eventstreams는 waiver 아님 — topology value_error는 도메인 헌트로 계속.)
- **backup은 agentless만**: agent 계열 8개만 waiver
  (create/delete/show backupagent, checkconnectionstate, listbackupagenttargets,
  showinstallfilepath, restoreagentbackup, listagentbackuprestoretargetservers);
  agentless 백업 본체(createbackup 등 23개)는 C3 타깃 유지.
- **cloudmonitoring C2-only**(14).
- 합계 +162 waivers (80 → 242). C3 분모 1292 → 1130.

## SCR 이미지 push 조사 (userguide cliusage, 2026-06-10)

표준 docker/OCI 인증으로 push 가능 — 단 **두 개의 선행 조건**이 있다:

1. **인증 = IAM 인증키 그대로**: `docker login <registry_endpoint>` with
   Username=AccessKey / Password=SecretKey (CI의 SCP_ACCESS_KEY/SECRET_KEY 재사용
   가능). 필요 권한: LoginContainerRegistry + PushRepositoryImages. **콘솔 작업
   필요**: IAM > 인증키 관리 > 보안 설정에서 해당 키의 인증 방식을 "인증키
   인증"으로 설정해야 함 (API로 가능한지 미확인 — iam accesskey 계열은 현재
   500/404).
2. **엔드포인트 도달성**: 문서가 주는 형식은 프라이빗 엔드포인트
   `[name]-[id].scr.private.[region].[offering].samsungsdscloud.com` — GitHub
   러너(공인망)에서는 **public endpoint를 켜야** 접근 가능할 것. 그 토글이
   바로 fail_new의 `updatepublicendpointenabled` PUT **500** — 이 500을 먼저
   풀어야 함 (빈 바디로 보냈는지 확인; {enabled:true} 류 바디 재시도).

실행 계획(다음 mutation 런): enable-public-endpoint 바디 보정 → 2xx 확인 →
CI에 skopeo 한 스텝 추가 (`skopeo copy docker://busybox:latest
docker://<endpoint>/<repo>/regr:c3 --dest-creds $AK:$SK`) → image/tags 계열
19개 엔드포인트가 read-chain으로 풀림. 콘솔 선행(인증키 인증 설정)은 오너 액션.
