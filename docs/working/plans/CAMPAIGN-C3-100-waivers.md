---
status: PROPOSED (오너 결정 대기) — 2026-07-07, OFFLINE 세션 (라이브 호출 없음)
for: 오너 — CAMPAIGN-C3-100 waiver 일괄 심사용 단일 문서
sources: CAMPAIGN-C3-100.md §"Waiver 제안 (G 117)" + 진행 로그 · CAMPAIGN-C3-100-A0-gaps.json ·
  CAMPAIGN-C3-100-repair-log.md (§HB1 · §HB3b-2 · §HB4b · §HB4b-2 · §HB5 · §HB4d) ·
  data/baselines/coverage_waivers.json (기존 261건 컨벤션) · data/baselines/known_issues.json ·
  dashboard/build.py (C3 공식) · docs/COVERAGE-CRITERIA.md
branch: claude/upbeat-ritchie-ieus5u
---

# CAMPAIGN-C3-100 — Waiver 제안 통합 (오너 결정용)

> **현황 주 (2026-07-20):** 이 문서의 수치는 **07-07 기준**(waiver 261건)이다.
> 이후 일괄 심사와 별개로 개별 승인이 진행돼 현재 **315건**: backup-401 PF-48
> 10키(오너 승인 07-18) · CDN 8키 unsatisfiable-flow(07-13) · data-flow/ops
> 34키 entitlement(07-14) · VS lock/unlock 2키(07-08). 클래스 5(PF-500 cascade
> 33건)는 **반려 권고 확정**(07-08 재대조). 클래스 1~4 일괄 심사는 여전히 대기 —
> 승인 시 §3 절차 전에 **수치 재계산 필수**.

> **한 장 요약**: A0가 분류한 gated 117건 + 캠페인 실측(HB1~HB5)으로 확정된 신규
> 후보 57건 = **총 174건**을 클래스별로 통합했다. 전부 승인 시 C3는 A0 기준선
> 72.78%(920/1264)에서 **84.2~85.7%**(터치 진행도에 따라)로 오르고, 잔여
> 달성가능 갭(175건)을 전부 2xx로 채우면 **분모 조작 없이 100%**에 도달한다.
> 산식과 파일 변경 절차는 §3. 수치는 전부 아래 소스에서 이 세션이 재계산했다
> (기억값 없음).

기존 컨벤션 (재확인, `data/baselines/coverage_waivers.json` `_comment` +
`dashboard/build.py` L360-397 + `docs/COVERAGE-CRITERIA.md`):

- **저장소**: `data/baselines/coverage_waivers.json` — 엔트리 스키마
  `{key, class, reason, provenance, added}`. 현재 261건 = **excluded 108**
  (blast-radius 24 + entitlement 14 + unsatisfiable-flow 6 + billing-prohibitive 64)
  + **reachability 153**. (A0 레저의 108/153과 일치 — 이 세션이 파일에서 재집계.)
- **excluded 클래스** (blast-radius / entitlement / unsatisfiable-flow /
  billing-prohibitive): C3 **분자·분모 모두에서 제외**. 단 **C2(호출됨)는 유지
  의무** — 4xx 응답이 게이트가 작동한다는 증거. waived 엔드포인트가 2xx를 받으면
  대시보드가 "waiver 철회 후보"로 자동 표시.
- **reachability 클래스**: 분모에 **남고**, 터치(어떤 상태코드든 호출됨)되면
  분자에 `reach_covered`로 가산 — verified-2xx와 정직하게 구분되는 별도 버킷.
- **사람이 모든 추가를 승인한다** (combo-scenario와 동일한 리뷰 규율).
- `dashboard/build.py`는 `class == "reachability"`만 reachability로 취급하고
  **나머지 class 값은 전부 excluded로 처리** — 신규 class 문자열을 만들면
  코드 수정 없이는 excluded 의미가 된다 (§3 절차에 반영).

---

## 1. 요약 표 — 클래스별 건수와 C3 영향

| # | 클래스 | 출처 | 건수 | C3 처리 | 승인 시 효과 |
|---|---|---|---|---|---|
| 1 | entitlement | A0 (G 117) | **39** | 분모 제외 | 분모 1264→아래로; C2 호출 의무 유지 |
| 2 | unsatisfiable-flow | A0 (G 117) | **26** | 분모 제외 | 〃 (SCR 19는 docker 레인 승인 시 철회 — §4) |
| 3 | billing-prohibitive | A0 (G 117) | **9** | 분모 제외 | 〃 |
| 4 | reachability (PF 등) | A0 (G 117) | **43** | 분모 유지, touched=covered | 터치분 41 즉시 분자 가산 (미터치 2) |
| 5 | **캠페인 신규: PF-500 cascade** | HB1·HB2 실측 | **33** | reachability (touched=covered) | 터치분 29 즉시 가산 (미터치 4) |
| 6 | **캠페인 신규: remove-backup-histories 401** | HB1 실측 + 기지 quirk | **2** | reachability | 터치분 2 즉시 가산 |
| 7 | **캠페인 신규: backup 이중차단** | HB3·HB3b 실측 | **16** | reachability | 터치분 9 즉시 가산 (미터치 7) |
| 8 | **캠페인 신규: entitlement (LB private-NAT)** | HB4 실측 | **1** | 분모 제외 | 분모 −1 |
| 9 | **캠페인 신규: account-config (DC 로깅스토리지)** | HB5 실측 | **5** | **분기** — 프로비저닝(권장) 또는 waiver | waiver 선택 시 reachability 의미로 등재 (§2.9) |
|  | **합계** |  | **174** (A0 117 + 신규 57) | 분모 제외 75 · reachability 94(+5) |  |

- A0 G 117 = 39+26+9+43 (이 세션이 `CAMPAIGN-C3-100-A0-gaps.json`에서 클래스·건수
  재집계 — CAMPAIGN-C3-100.md §"Waiver 제안" 표와 일치 확인).
- 캠페인 신규 57 = 33+2+16+1+5. **A0 G와 중복 없음** (전부 A0에서 H로 분류됐던
  키; mysql/epas의 remove-backup-histories는 #5에 포함되어 #6은 mariadb·cachestore
  2건만 신규).

---

## 2. 클래스별 상세

근거 열의 발생 run: HB1=28699129653 · HB2=28722435523 · HB3=28723287734 ·
HB3b=28766151214 · HB4=28738115294 · HB4b=28827996068 · HB5=28831560635 ·
HB4d=28835929967. "A0"는 `CAMPAIGN-C3-100-A0-gaps.json`의 누적 실측(last_status).

### 2.1 entitlement — 39건 (분모 제외)

| 엔드포인트 키 | 근거 (오류 + 발생) | 철회 조건 |
|---|---|---|
| management/organization/{listaccounts, listorganizationinvitations, listorganizationunits, listparents, listpoliciesfortarget, listservicecontrolpolicies, listtargetsforpolicy, showaccount, showdelegationpolicy, showorganization, showorganizationunit, showservicecontrolpolicy} ×12 | 403 (A0) — member 계정, org-master 아님 | 계정이 org-master 승격 시 |
| ai-ml/cloud-ml/{checkduplicationnameanddomainname, cloudmlclusterestimate, cloudmlimages, clustercheckreleasable, clusterproduct, createcloudml, deletecloudml, getcloudml, updatecloudml} ×9 | 404 라우팅 (A0) — 제품 미구독 | Cloud ML 구독 시 |
| application-service/apigateway/{approveprivatelinkendpoint, connectprivatelinkendpoint, deleteprivatelinkendpoint, requestprivatelinkendpoint, setprivatelinkendpoint} ×5 | 403 (A0) — PrivateLink entitlement | PL 권한 부여 시 |
| security/configinspection/{creatediagnosisobject, diagnosisrequest, getdiagnosisobjectdetail, getdiagnosisresultdetail, terminatediagnosisobject} ×5 | 400/404 (A0) — 피검사 계정 auth_key_id/외부 CSP 자격 필요 | 피검사 계정 자격 확보 시 |
| devops-tools/devopsservice/{checkdeletabledevopsservice, createdevopsservice, deletedevopsservice, showdevopsservice} ×4 | 409 not-found-admin-user-service (A0) — admin-user-service 미활성 | admin-user-service 활성화 시 |
| management/iam/{adduserpolicybinding, removeuserpolicybinding} ×2 | 403 project-membership (A0) | 프로젝트 멤버십 권한 시 |
| data-analytics/data-ops/{getdataopssubversion, getingresscontrollerlistv1} ×2 | 부모 경로 403 Action-not-found (A0) | 해당 액션 권한 부여 시 |

### 2.2 unsatisfiable-flow — 26건 (분모 제외)

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| container/scr/{checktagsvulnerability, deleteimage, deletetags, deletetagses, downloadmanifest, listtagses, runimagelifecyclepolicypreview, showimage, showimagelifecyclepolicypreview, showtags, showtagspackages, showtagssecrets, showtagsvulnerabilities, updateimagedescription, updateimagelifecyclepolicy, updateimagelockpolicy, updateimagepullpolicy, updateimagescanpolicy, updatetagslockpolicy} ×19 | 404/400 (A0) — image/tags는 docker-push로만 생성 가능 (REST로 불충족) | **SCR docker-push 레인 승인 시 즉시 철회 → 커버 가능 전환** (§4.2) |
| security/certificatemanager/{createcertificate, validatecertificate} ×2 | 400 (A0) — 실 CA 서명 cert 자료 필요 | 실 CA cert 확보 시 |
| storage/filestorage/{deletevolumereplication, setvolumereplication} ×2 | 400 제품 제약 (A0) — DR-리전 측 전용 op | DR-리전 계정/환경 확보 시 |
| management/quota/showquotarequest ×1 | 미터치 (A0) — request 생성이 UI 전용 | quota request API 생기면 |
| security/kms/updatemanagedkeydescription ×1 | 404 (A0) — system-managed key 필요, create API 없음 | managed key 노출 시 |
| security/secretvault/gettemporarykey ×1 | 400 (A0) — vault 발급 Sv* 헤더 파생 불가 | vault 자격 체계 확보 시 |

### 2.3 billing-prohibitive — 9건 (분모 제외)

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| networking/dns/{createpublicdomainname, setpublicdomainname, setpublicdomainnamewhoisinfo, showpublicdomainname, transferpublicdomainname} ×5 | 500/미터치 (A0) — 유료 public 도메인 등록 선행 | 오너가 도메인 등록 비용 승인 시 |
| financial-management/billingplan/{createplannedcomputes, showcancellationfee, showplannedcompute, updateplannedcompute} ×4 | 400/미터치 (A0) — 유료 약정(commitment) 생성 필요 | 오너가 약정 비용 승인 시 |

### 2.4 reachability (A0 제안분) — 43건 (분모 유지 · touched=covered)

원칙 (CAMPAIGN-C3-100.md §Waiver 주): **PF(제품버그) 수리 시 waiver 철회 + 실 2xx
재도전.**

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| database/postgresql/{postgresqlcreateotherregionreplica, postgresqlcreaterestore, postgresqlexportlog, postgresqlpatchminorversion, postgresqlregisterlogexportconfig, postgresqlremovebackuphistories, postgresqlsetblockstoragesize, postgresqlsetlogexportconfig, postgresqlsetservertype, postgresqlswitchovercluster, postgresqlsyncparametervalues, postgresqlunregisterlogexportconfig, postgresqlunsetbackup} ×13 | createcluster 500 PF(`known_issues.json` 기등재)가 전체 체인 차단; sub-op는 400/401/404 (A0) | createcluster PF 수리 시 전 체인 재도전 |
| data-analytics/quick-query/{createquickquery, validatequickqueryresources} ×2 + cascade {deletequickquery, getquickquery, updatequickquerydescription, updatequickquerydomain, updatequickquerydscdomain, updatequickqueryengine, updatequickqueryhostalias} ×7 = 9 | create/validate 500 PF(`known_issues.json`), cascade 차단 (A0). **getquickquery 미터치** — C2 터치 필요 | create/validate PF 수리 시 |
| management/iam/{accesskeycreate, createrole} ×2 (500 PF, createrole은 `known_issues.json` 기등재) + cascade {accesskeydelete, accesskeydeletebulk, accesskeyset, deleterole} ×4 = 6 | 500 PF + cascade 400/404 (A0). **deleterole 미터치** — C2 터치 필요 | 500 PF 수리 시 |
| database/epas/{epascreateotherregionreplica, epasregisterlogexportconfig, epassetarchiveconfig, epasupgradekernel} ×4 | product-bug 500, `known_issues.json` 기등재 (A0) | 해당 500 수리 시 |
| platform/sts/{assumerole, assumerolewithsaml, objectstoreauthorization} ×3 | 404 (A0) — IAM createrole 500 PF + SAML provider 부재로 체인 차단 | IAM PF 수리 + SAML 확보 시 |
| compute/scf/{approveprivatelinkendpoint, connectprivatelinkendpoint} ×2 | 403/404 privatelink-endpoint-not-found PF (A0, `known_issues.json` 기등재) | PL PF 수리 시 |
| database/mysql/mysqlcreateotherregionreplica ×1 | 500 product-bug (A0) | 500 수리 시 |
| database/mariadb/mariadbregisterlogexportconfig ×1 | 500 product-bug (A0, `known_issues.json` 기등재) | 500 수리 시 |
| application-service/apigateway/createprivatelinkendpoint ×1 | 500 PF-23 (A0, `known_issues.json` 기등재) | PF-23 수리 시 |
| financial-management/billingplan/listplannedcomputeinstances ×1 | 500 PF (A0, `known_issues.json` 기등재) | 500 수리 시 |
| security/secretsmanager/createsecretsmanagerkmskey ×1 | 404 — kms-key 엔드포인트 미라우팅 (A0) | 라우팅 수리 시 |
| container/scr/createregistry ×1 | 403 — registry quota 1EA를 샘플 registry가 점유 (A0) | quota 증설 또는 샘플 registry 정리 시 |

### 2.5 캠페인 신규: PF-500 cascade — 33건 (reachability 제안)

HB1에서 mysql `create-cluster` 500 `ContactAdminForAssistance`, HB2에서 epas
`create` 500 동일 서명 확정 (postgresql 기지 create-500과 동류 — 진행 로그
"PF(waiver 후보: epas·mysql create-500 계열 확대)" + repair-log §HB1 항목 7).
create가 막혀 실클러스터 sub-op 체인 전체가 도달 불가.

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| database/mysql/{mysqladdblockstorages, mysqlcreaterestore, mysqlexportlog, mysqlpatchminorversion, mysqlregisterlogexportconfig, mysqlremovebackuphistories, mysqlsetblockstoragesize, mysqlsetservertype, mysqlstartcluster, mysqlstopcluster, mysqlswitchovercluster, mysqlunregisterlogexportconfig, mysqlunsetbackup, mysqlsetlogexportconfig} ×14 | mysql create-cluster 500 `ContactAdminForAssistance` (HB1) → 체인 스킵; A0 잔존 서명 400/401/404. **미터치 4**: setblockstoragesize·setservertype·startcluster·stopcluster | create-cluster PF 수리 시 HB1b 재디스패치 (repair-log §HB1 수리분 1–5 이미 반영됨) |
| database/epas/{epasaddblockstorages, epascreaterestore, epasdeletearchivelog, epasexportlog, epaspatchminorversion, epasremovebackuphistories, epasrestartcluster, epassetauditlog, epassetblockstoragesize, epassetlogexportconfig, epassetservertype, epasshowrequest, epasstartcluster, epasstopcluster, epasswitchovercluster, epassyncarchiveconfig, epassyncclusterstate, epasunregisterlogexportconfig, epasunsetbackup} ×19 | epas create 500 `ContactAdminForAssistance` (HB2) → 체인 literal-404 강등; A0 서명 400/404. 전부 터치됨 | create PF 수리 시 HB2b 재디스패치 |

주: mysql/epas `createcluster` 자체는 과거 2xx로 이미 verified(갭 아님)라 waiver
대상이 아니고, **회귀 버그로서 `known_issues.json` 등재 검토** 대상 (§3-4).

### 2.6 캠페인 신규: remove-backup-histories 401 — 2건 (reachability 제안)

기지(旣知) 백엔드 인증 버그 계열 — "유효 HMAC인데 이 엔드포인트만 401
`Dbaas.Unauthorized.AuthNFailed`, 형제 sub-op는 전부 통과" (knowledge/formal
2026-06-10 문서화, HB1이 mariadb에서 재확인; repair-log §HB1 항목 6 — body/서명
수정 대상 아님 판정). postgresql분은 §2.4에, mysql/epas분은 §2.5에 이미 포함.

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| database/mariadb/mariadbremovebackuphistories | 401 AuthNFailed — 정상 클러스터 풀체인 완주 중 재현 (HB1) | 백엔드 AuthN 버그 수리 시 |
| database/cachestore/cachestoreremovebackuphistories | 동일 quirk family (`knowledge/formal/services/database__cachestore.yaml`; `known_issues.json` 기등재; HB2 풀체인에서 미해소) | 〃 |

### 2.7 캠페인 신규: backup 이중차단 — 16건 (reachability 제안)

repair-log §HB3b-2 항목 1 + 진행 로그 HB4b("backup 패밀리는 이중 차단 확정 —
waiver 레저로 이동"): `getbackuptargetlist`가 **FILESYSTEM**(Agent형)으로는
빈 목록이 **정상**(agent 계열 8 ops는 오너 waiver 2026-06-10 — 이 계정은 agent를
설치하지 않음)이고, agentless 경로인 **VM_IMAGE**는 기지 500 PF
(`known_issues.json` 2026-06-20)로 차단 — **현재 계정 상태로는 어떤 파라미터
조합도 목표 응답을 줄 수 없음**이 HB3(run 28723287734)·HB3b(run 28766151214)
2회 실측으로 확정.

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| storage/backup/{createbackup, deletebackup, deletebackuprestoretarget, manualbackup, restorebackup, setbackupschedules, setfilesystempath, updatereplicationuse, updateretentionperiod} ×9 (터치됨: 500/404) + {listbackuphistories, listbackuprestorehistories, listbackuprestoresubnets, listbackuprestoretarget, listbackupschedules, listfilesystempath, showbackup} ×7 (**미터치** — C2 터치 필요) | backup-target 확보 불가로 전 체인 차단 (HB3/HB3b) | VM_IMAGE 500 PF 수리 **또는** agent waiver 해제(단, agent 설치는 API 완결 불가로 기록됨 — `knowledge/formal/resources/storage__backup.yaml` L35) 시 gen-heavy-backup 재도전 (count-poll 수리는 self-healing으로 이미 반영) |

주: `storage/backup/checkfilesystemduplication`(L 분류, 쿼리 파라미터 수리 대상)은
waiver 대상 아님 — LB1 잔여 작업.

### 2.8 캠페인 신규: entitlement — 1건 (분모 제외 제안)

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| networking/loadbalancer/createloadbalancerprivatenatip | 403 `PrivateNatIpForbidden` — "You do not have permission to access the private NAT IP resource" (HB4; repair-log §HB4b 항목 4 — body 무관 계정 권한 벽 판정) | private NAT IP 권한 부여 시 |

### 2.9 캠페인 신규: account-config — 5건 (분기 결정 요청)

repair-log §HB5 항목 4: `create-direct-connect` 400 `not-exist-log-storage`
("FIREWALL Logging storage does not exists in this account") — **계정 레벨
선행요건**이며 lifecycle 수리로 해소 불가. 단, 대응 API가 실존
(`management/network-logging` create/list/delete + object-storage 버킷)하므로
**waiver보다 프로비저닝이 우선 권장**.

| 엔드포인트 키 | 근거 | 철회 조건 |
|---|---|---|
| networking/direct-connect/{createroutingrule, deletedirectconnect, deleteroutingrule, setdirectconnect} ×4 (터치: 404) + listroutingrules ×1 (**미터치**) | create-direct-connect 400 계정요건 → 체인 404 강등 (HB5) | — |

**오너 선택지**:
- **(권장) 옵션 A — 공유 인프라 프로비저닝**: 오케스트레이터가 세션당 1회
  FIREWALL network-logging-storage(+버킷)를 만들어 유지 (공유 VPC 패턴과 동일;
  §HB5 권고). → waiver 불필요, 5건은 H로 남아 실 2xx 재도전.
- **옵션 B — waiver**: `class: "reachability"` + reason 접두 `account-config:`로
  등재 (touched=covered 의미 유지; §3-2 주의 참조). 철회 조건: 옵션 A 실행 시.

---

## 3. 승인 절차 — 무엇을 어떻게 바꾸고, C3가 몇 %가 되는가

### 3-1. 오너 액션

이 문서의 §2 클래스(또는 행) 단위로 승인/반려를 지정한다. 부분 승인 가능 —
클래스별 산식이 독립이므로 §3-3 표에서 조합 계산 가능.

### 3-2. 승인 시 파일 변경 (기존 컨벤션 그대로)

1. **`data/baselines/coverage_waivers.json`** — 승인된 키마다 엔트리 추가:
   ```json
   {"key": "<catalog key>", "class": "<entitlement|unsatisfiable-flow|billing-prohibitive|reachability>",
    "reason": "<§2 근거 1줄 + 철회 조건>", "provenance": "campaign-C3-100 <A0|HBn run-id>",
    "added": "<승인일>"}
   ```
   **주의**: `dashboard/build.py`(L383-384)는 `class=="reachability"`만
   touched=covered로 취급하고 그 외 문자열은 전부 분모 제외로 처리한다.
   따라서 **account-config를 waive할 경우 class는 "reachability"로 쓰고 reason에
   `account-config:` 접두를 남긴다** (새 class 문자열 발명 금지 — 코드 수정 없이
   의미가 왜곡됨). PF-500/backup/RBH-401 신규분도 전부 `reachability`.
2. **`knowledge/validated-facts.md`** — 승인 사실 + 철회 조건을 같은 커밋에 기록
   (Hard Rule 7).
3. **대시보드 재빌드**: `python -m dashboard.build` — waived인데 2xx인 키는
   자동으로 "철회 후보" 표시되므로 과승인은 자기교정된다.
4. **부수 등재 검토**: mysql·epas `createcluster` 500 `ContactAdminForAssistance`는
   verified 기록이 있는 **회귀**라 waiver가 아닌 `data/baselines/known_issues.json`
   등재 대상 (postgresql createcluster 선례 있음 — 파일에서 확인).
5. **C2 터치 의무**: waiver여도 "호출됨" 증거가 필요하다. A0 기준 미터치 22건 —
   G 10건(quota showquotarequest · devopsservice 2 · billingplan showplannedcompute
   · dns public-domain 4 · quick-query getquickquery · iam deleterole) + 신규 12건
   (mysql 4 · backup 7 · DC listroutingrules) — 을 read-only/안전 터치 배치로 1회
   호출 (reachability분은 이 터치가 곧 분자 가산). 최신 fold 후 미터치 여부 재확인.

### 3-3. C3 산식 (A0 공식 · 분모 1264 기준 — `dashboard/build.py` L395-397)

```
C3 = |(verified-2xx − excluded_waivers) ∪ reach_covered| / (1372 − |excluded_waivers|)

A0 기준선: excluded 108 → 분모 1264 · 분자 920 (verified-2xx 776 union + reach_covered 149) = 72.78%
```

승인 조합별 (분자는 **A0 기준선 고정** — A0 이후 실수확분은 별도 가산, 아래 주):

| 시나리오 | excluded 추가 | reachability 추가 | 분모 | 분자 | **C3** |
|---|---|---|---|---|---|
| (0) 현재 (승인 전) | — | — | 1264 | 920 | **72.78%** |
| (1) A0 117건만 승인 | +74 (39+26+9) | +43 (터치 41) | 1264−74=**1190** | 920+41=961 | **80.76%** |
| (1') + 미터치 2건 터치 후 | 〃 | 〃 | 1190 | 963 | **80.92%** |
| (2) (1) + 캠페인 신규 (DC 제외: 옵션 A) | +75 (74+1) | +94 (43+33+2+16; 터치 81*) | 1264−75=**1189** | 920+81=1001 → 전 터치 1014 | **84.19→85.28%** |
| (2') (1) + 캠페인 신규 + DC waiver (옵션 B) | +75 | +99 (+DC 5; 터치 85) | 1189 | 920+85=1005 → 전 터치 1019 | **84.52→85.70%** |
| (3) 캠페인 종주: (2) + 잔여 달성가능 175건 전부 2xx | +75 | +94 전 터치 | 1189 | 920+175+94=**1189** | **100.00%** |

\* 터치 81 = A0 41 + mysql 10 + epas 19 + backup 9 + mariadb/cachestore 2;
미터치 13 = A0 2(getquickquery·deleterole) + mysql 4 + backup 7 → 터치 완료 시
분자 1001+13=1014 (85.28%). (2')는 +DC 터치 4/미터치 1. 검산: 344 갭 = G 117 +
신규 reach 51 + 신규 excluded 1 + 달성가능 175 (L14 + W16 + H145 = 197−33−2−16−1).

**주 (분자 드리프트)**: A0 이후 수리 루프가 verified store를 2252→2344키
(+92, HB3b +13 · HB4b +24 · HB4c +12 · HB4d/HB5b +43)로 늘렸다 — store 키는
lifecycle:step 단위라 엔드포인트 단위 분자 증가분은 fold 후
`python -m dashboard.build`가 정본으로 확정한다. 위 표의 %는 **하한(floor)**이며
waiver의 분모/분자 효과 자체는 분자 드리프트와 독립적으로 유효하다.

---

## 4. 미결 / 논쟁 항목 (이번 승인 대상 아님)

1. **vpc-peering CREATING 체류 — 제품버그 재분류 후보 (아직 waiver 아님)**:
   same-account peering이 create 202 후 **~540초가 지나도록 단 한 번도 ACTIVE를
   관측하지 못함** (HB4d run 28835929967 타임스탬프 대조; repair-log §HB4d 항목 5).
   waits는 통과하는데 set/delete가 여전히 400 `(CREATING)` — timeout 900s 상향
   반영됨. **다음 heavy 디스패치에서 900s 내 ACTIVE 미도달이 확인되면
   product-bug로 재분류 + `known_issues.json` 등재** → 그때 peering 체인 4키
   (networking/vpc/{approvalvpcpeering, createvpcpeeringrule, deletevpcpeeringrule,
   setvpcpeering})가 reachability waiver 후보로 올라온다. 부수: `approvalvpcpeering`은
   same-account에서 **영구 400** ("Approval is not required for Same Account VPC
   peering", HB5 §1 확정) — cross-account 환경이 없는 한 unsatisfiable-flow
   후보이기도 함. 오너가 원하면 이번 심사에 포함 가능하나 peering 판정과 묶어
   다음 라운드 권장.
2. **SCR docker-push 레인**: 러너 docker + registry 자격을 승인하면 §2.2의
   unsatisfiable-flow **19건이 waiver 없이 커버 가능(G→H) 전환** — 분모 조작
   없이 실검증을 늘리는 경로라 waiver보다 우선 검토 가치. (그 경우 분모
   1264−55=1209, 19건 2xx 시 시나리오(1) 대비 C3 81.06%.) `createregistry`
   quota 1EA(§2.4)는 별개.
3. **vpc private-nat 7건 (W 분류)**: networking/vpc/{createprivatenat,
   createprivatenatip, deleteprivatenat, deleteprivatenatip, listprivatenatips,
   setprivatenat, showprivatenat} — 실 DC `service_resource_id` 배선 필요
   (§HB5 항목 5: DC 수리와 **독립**, 합성 placeholder가 원인). §2.9에서 옵션 A
   (프로비저닝)를 택하면 DC 실 id 확보 후 배선 작업(별도 세션)으로 커버 가능;
   옵션 B(DC waiver)를 택하면 이 7건도 후속 waiver 후보로 재상정 필요.
4. **eventstreams create body "ZK-quorum" 가설** (repair-log §HB1 작업 2):
   미검증 가설로 저작된 상태 — 실패해도 waiver가 아니라 body 조사 계속
   (H 분류 유지).

> **주(2026-07-08 rebase 후)**: 본 문서의 분자/하한 수치는 A0 공식 + 07-07 시점
> store(2344) 기준이다. 이후 main의 Model B 라인(+134 검증, GAP 편입 5배치,
> 시나리오 합집합 99.8%)이 병합되어 현재 store는 2366+ — 클래스 분류와 승인
> 절차(§3)는 그대로 유효하나, 승인 시 정확한 C3는 `python -m dashboard.build`
> 재계산이 정본이다.
