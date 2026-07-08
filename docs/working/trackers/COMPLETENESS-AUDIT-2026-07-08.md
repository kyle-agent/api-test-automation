---
status: active (2026-07-08 오프라인 정적 감사 — owner 편입 worklist)
for: owner
method: deterministic offline (catalog 분모 × 시나리오 합집합; tools/retirement.py 분해와 동일)
observed-at: HEAD d306edfa (dedup 05943319 + VS 25-fold d306edfa 반영 후, working tree clean)
counterpart-of: DEDUP-AUDIT-2026-07-08.md (은퇴의 반대 방향 검증 = 완전성)
---

# Completeness Audit — 서비스별 catalog 전량이 시나리오 합집합에서 테스트되는가 (2026-07-08)

**Owner 요청**: "catalog에 있는 해당 서비스의 모든 api가 시나리오의 합집합에서
테스트되어야 해." 오늘 dedup 배치(27개 lifecycle 은퇴, `05943319`)의 counterpart로,
**전 59개 서비스**에 대해 catalog 분모가 시나리오 합집합으로 덮이는지 전수 검증하고
빠진 endpoint를 전부 열거했다. 레시피 무수정 — 본 문서가 편입 작업 목록이다.

## 재현 방법 (deterministic)

1. **분모**: `data/api_catalog.json`(1,372 entries)을 `e['service']`(short name)로
   그룹 — 서비스별 분모 = key 집합.
2. **step→key 매핑** (`tools/retirement.py`의 `_norm`/`_catalog_index`/`ops_of`와
   동일): `norm(path)` = query 제거 후 `{...}` 세그먼트 → `*`;
   인덱스 `(METHOD, norm, service)` → key; step의 service =
   `step.get('service') or lifecycle service short name`. 인덱스 충돌 0 확인.
3. **lifecycle 로드**: `regression.scenarios.loader.load_lifecycles()`
   (base + fragments, role은 HEAVY-PREMISE CONTRACT §1 파생). 서비스별 파티션
   (우선순위 순, 상위에 잡히면 하위에서 제외):
   - **verify** = enabled ∧ role=verify lifecycle들의 key 합집합 (2xx-target 레인)
   - **probe-only** = enabled ∧ role=probe 합집합 − verify (CI-sweep 도달성 레인)
   - **reachability-waived** = `data/baselines/coverage_waivers.json`의
     class=`reachability` (full key 매칭) − 위 둘
   - **excluded-by-waiver** = 비-reachability class(billing-prohibitive /
     blast-radius / entitlement / unsatisfiable-flow) − 위 셋
     — **의도적 2xx-target 제외이므로 gap이 아님**
   - **GAP** = 분모 − 전부
4. **난이도 분류** (method + path 모양): **(a)** 전역 read = GET·무파라미터 ·
   **(b)** id-bound read = GET·path 파라미터(부모 id 필요) · **(c)**
   write-with-context = 기존 리소스 경로 위 POST/PUT/PATCH/DELETE · **(d)**
   standalone write = 무파라미터 최상위 POST.

**한계 2건(확인 완료)**: ① `probe_reads` step은 런타임에 해당 서비스의
id-해석-가능한 path-param GET 전부를 auto-seed로 호출한다(engine `_probe_reads`) —
정적 매핑엔 안 보이므로 아래 `†` 표시 gap은 이미 런타임에 실행되고 있을 수 있다.
단 **비보장**(id 해석·cap·query 조건부)이므로 명시 strict-200 step 편입이 정답.
② 카탈로그에 매핑 안 되는 step 튜플 13건(리터럴 id·off-catalog 경로, 예:
support 고정 inquiry-id GET, searchengine/vertica 0000-UUID showrequest,
`filestorage-dr` service 태그) — 전건 대조 결과 **아래 62개 gap 중 false-gap을
만드는 건 없음**(해당 key들은 전부 다른 step으로 커버됨).

## Executive summary

| 구분 | n | % |
|---|---:|---:|
| catalog 분모 (59 services) | **1,372** | 100% |
| verify 커버 (2xx-target) | 953 | 69.5% |
| probe-only 커버 (CI-sweep 레인) | 340 | 24.8% |
| **시나리오 합집합 (verify+probe)** | **1,293** | **94.2%** |
| reachability-waived (미호출) | 14 | 1.0% |
| excluded-by-waiver (의도적 제외) | 3 | 0.2% |
| **GAP (어느 시나리오에도 없음)** | **62** | **4.5%** |

- **100% 서비스 28개 / gap 보유 31개.** gap 62 난이도: **(a) 전역 read 41 ·
  (b) id-bound read 13 · (c) write-with-context 8 · (d) standalone write 0** —
  3분의 2가 "부모 불필요 bare list/check GET" 즉 저비용 편입이다. 신규 체인이
  필요한 gap은 **0건** — 62개 전부 기존 lifecycle에 step 편입으로 해결 가능.
- **Dedup 교차검증 통과**: pre-dedup(`cb121d5e`) gap 71 → 현재 62. 오늘 dedup이
  **새로 만든 gap 0** (zero-loss 주장 독립 확인), VS 25-fold가 9개를 닫음.
- **compute/virtualserver**: 병행 세션의 owner 승인 25-endpoint 편입이 **이미
  landed**(`d306edfa`) — 본 감사로 **113/113 verify, gap 0** 재확인(교차검증 일치).
- 패턴: gap의 대부분이 **"id-capture 체인은 있는데 bare 컬렉션 LIST만 빠진"**
  모양(예: vpc의 transit-gateway 17개 중 16개 커버, `listtransitgateways`만 gap)과
  **DB 계열 공통 subops의 서비스별 누락**(mysql archive/start-stop,
  mariadb/cachestore/epas의 backup-histories/parameters/requests GET).

**Top-10 gap 서비스**: mysql 6/45 · vpc 5/95 · mariadb 4/46 · cachestore 3/32 ·
cloudmonitoring 3/18 · dns 3/22 · epas 3/47 · iam 3/62 · scf 3/36 · (2-gap 동률 7개:
baremetal, eventstreams, gslb, multinodegpucluster, organization, servicewatch,
sqlserver) · 나머지 15개 서비스는 gap 1.

표기: `†` = probe_reads auto-seed로 런타임 실행 가능성(비보장) ·
`[U]` = untestable_services.json 등재(라이선스/자원 부재 — 도달성만 목표) ·
host 제안 = **그 서비스의 enabled verify lifecycle 중 표면이 가장 가깝거나 부모를
만드는 것**. verify lifecycle이 없는 서비스(probe 레인 전용 5개: baremetal, gslb,
organization, budget, cdn)는 probe lifecycle을 제안하되, **strict-200 step 추가 시
role 파생(§1)이 verify로 뒤집힐 수 있으니 재확인** 필요.

---

## Gap ≥ 2 서비스 (16개, gap 내림차순)

### database/mysql — gap 6/45 (v33 · p6 · r0 · x0)

전부 **(c)** — 클러스터 위 쓰기. host들이 이미 클러스터를 소유하고, postgresql
쌍둥이 step이 존재(body 복사 가능, NEAR-XSVC 확인). start/stop은 다른 subops와의
간섭을 피해 배치 위치 주의(stop→start 순).

| gap key | METHOD path | class | host 제안 |
|---|---|---|---|
| mysqlsetarchiveconfig | PUT /v1/clusters/{id}/archive | c | `database-mysql-cluster` (PG 쌍둥이 step 복사) |
| mysqlsyncarchiveconfig | POST /v1/clusters/{id}/archive/sync | c | `database-mysql-cluster` (archive set 직후) |
| mysqlstartcluster | POST /v1/clusters/{id}/start | c | `database-mysql-cluster` (stop→start) |
| mysqlstopcluster | POST /v1/clusters/{id}/stop | c | `database-mysql-cluster` |
| mysqlsetblockstoragesize | POST /v1/block-storage-groups/{id}/resize | c | `database-mysql-cluster` (PG 쌍둥이) |
| mysqlsetservertype | POST /v1/instance-groups/{id}/resize | c | `mysql-cluster-subops-full` (PG 쌍둥이) |

archive set/sync는 과거 `gen-heavy-mysql-restore/-upgrade`(disabled — dedup 이전부터)만
호출했었음. probe-only 6: mysqlcreateotherregionreplica, mysqlcreatereplica,
mysqlcreaterestore, mysqlpromotereplicacluster, mysqlresetreplica, mysqlsyncreplicastate.

### networking/vpc — gap 5/95 (v86 · p4 · r0 · x0)

전부 **(a)** bare 컬렉션 LIST — id-capture 체인들이 show/set/delete는 다 부르는데
목록만 안 부른다(transit-gateway family 17개 중 이것 하나만 gap). 전역 read라
부모 불필요 — 빈 목록 200으로도 충분, light 편입 가능.

| gap key | METHOD path | class | host 제안 |
|---|---|---|---|
| listsubnets | GET /v1/subnets | a | `networking-vpc-subnet` (subnet 생성 체인, light) |
| listports | GET /v1/ports | a | `gen-pilot-net-basics` (port PUT 이미 보유, light) |
| listnatgateways | GET /v1/nat-gateways | a | `gen-pilot-net-basics` (light; 대안 `heavy-shared-networking`=실자원 중 검증) |
| listtransitgateways | GET /v1/transit-gateways | a | `vpc-transit-gateway-children` (TGW family 정본, [H]) |
| listprivatelinkendpoints | GET /v1/privatelink-endpoints | a | `vpc-privatelink-endpoint` ([H]) |

probe-only 4: createprivatenatip, deleteprivatenatip, listprivatenats, setprivatenat.

### database/mariadb — gap 4/46 (v35 · p7 · r0 · x0)

전부 **(b)**† — 기존 클러스터의 하위 read. mysql/PG 쌍둥이 step 복사 가능.
host: 전건 `mariadb-cluster-subops-full` (클러스터 소유).

| gap key | METHOD path | class |
|---|---|---|
| mariadblistbackuphistories † | GET /v1/clusters/{id}/backup-histories | b |
| mariadblistparametervalues † | GET /v1/clusters/{id}/parameters | b |
| mariadbshowarchiveconfig † | GET /v1/clusters/{id}/archive | b |
| mariadbshowrequest † | GET /v1/requests/{request_id} | b (쓰기 202 응답의 request_id 캡처 — mysql 쌍둥이 step 있음) |

probe-only 7: mariadbcreateotherregionreplica, mariadbcreatereplica, mariadbcreaterestore,
mariadbpromotereplicacluster, mariadbresetreplica, mariadbswitchovercluster, mariadbsyncreplicastate.

### database/cachestore — gap 3/32 (v28 · p1 · r0 · x0)

mariadb와 동형 **(b)**†. host: 전건 `cachestore-cluster-subops-full`.
gap: cachestorelistbackuphistories † (GET …/backup-histories) ·
cachestorelistparametervalues † (GET …/parameters) · cachestoreshowrequest †
(GET /v1/requests/{id}). probe-only 1: cachestorecreaterestore.

### database/epas — gap 3/47 (v37 · p7 · r0 · x0)

동형 **(b)**†. host: 전건 `epas-cluster-subops-full`.
gap: epaslistbackuphistories † · epasshowarchiveconfig † (GET …/archive) ·
epasshowrequest †. probe-only 7: epascreateotherregionreplica, epascreatereplica,
epascreaterestore, epaspromotereplicacluster, epasresetreplica, epasswitchovercluster,
epassyncreplicastate.

### management/cloudmonitoring — gap 3/18 (v13 · p2 · r0 · x0)

전부 **(a)**. host: 전건 `cloudmonitoring-readonly-shows` (bare read 정본).
gap: getaccountmembers (GET …/product/v1/accounts/members) · getmetriclist
(GET …/product/v2/metrics) · getproducttypelist (GET …/product/v1/product-types).
probe-only 2: getmetricperfdatalist, modifyeventpolicy.

### networking/dns — gap 3/22 (v13 · p6 · r0 · x0)

전부 **(a)** bare list. 유일 enabled verify가 `networking-dns-hosted-zone-private`
[H] — 전건 여기 편입 (`listhostedzone`은 과거 `networking-dns-hosted-zone`(disabled)이
커버하다 끊긴 이력 있음).
gap: listhostedzone (GET /v1/hosted-zones) · listprivatedns (GET /v1/private-dns) ·
listpublicdomainnames (GET /v1/public-domain-names).
probe-only 6: createpublicdomainname, sethostedzonerecord, setpublicdomainname,
setpublicdomainnamewhoisinfo, showpublicdomainname, transferpublicdomainname.

### management/iam — gap 3/62 (v42 · p16 · r0 · x1)

| gap key | METHOD path | class | host 제안 |
|---|---|---|---|
| listendpoints | GET /v1/endpoints | a | `iam-readonly-shows` (bare list 정본) |
| listsamlprovider | GET /v1/saml-providers | a | `iam-readonly-shows` (빈 목록 200; SAML 쓰기는 probe 레인에 있음) |
| showresourcepolicy † | GET /v1/resource-policies/{srn} | b | `iam-resource-policy` (policy 생성 체인이 srn 보유) |

excluded-by-waiver 1: deletepolicies (blast-radius). probe-only 16: accesskeycreate,
accesskeydelete, accesskeydeletebulk, accesskeysendtemporaryotp, accesskeyset,
adduserpolicybinding, createiamuser, createsamlprovider, deleteiamuser,
deletesamlproviders, listuserpolicybindings, removeuserpolicybinding, setsamlprovider,
showsamlprovider, updateiamuser, updateiamuserpassword.

### compute/scf — gap 3/36 (v33 · p0 · r0 · x0)

전부 **(a)** 전역 GET. host: 전건 `gen-wave2-scf` (check-duplication은 create 직전
step으로 두면 자연스러움).
gap: checkfunctionnameduplication (GET /v1/cloud-functions/check-duplication) ·
listruntimes (GET /v1/cloud-functions/runtimes) · listsamplecodes
(GET /v1/cloud-functions/sample-codes).

### compute/baremetal [U] — gap 2/16 (v0 · p14 · r0 · x0)

**(a)** 2건인데 **enabled verify lifecycle이 없음**(probe 레인 전용 서비스).
`baremetal-server-coverage`(probe)에 strict-200 read로 추가(role 파생 재확인) 또는
2-step read-coverage lifecycle 신설.
gap: listbaremetalimages (GET /v1/images — ske/virtualserver의 동경로와 **다른 key**,
service=baremetal로 호출해야 함) · listbaremetalproducts (GET /v1/bm_products).
probe-only 14: assignbaremetalprivatenatip, assignbaremetalpublicnatip,
attachlocalsubnetbaremetal, createbaremetals, deletebaremetalprivatenatip,
deletebaremetalpublicnatip, deletebaremetals, detachlocalsubnetbaremetal,
listbaremetals, lockbaremetalserver, showbaremetal, startbaremetals, stopbaremetals,
unlockbaremetalserver.

### data-analytics/eventstreams — gap 2/24 (v22 · p0 · r0 · x0)

**(c)** 2건 — 클러스터 stop→start. host: `eventstreams-cluster-subops-full`
(클러스터 소유; PG 쌍둥이 step 존재).
gap: eventstreamsstartcluster (POST /v1/clusters/{id}/start) ·
eventstreamsstopcluster (POST /v1/clusters/{id}/stop).

### networking/gslb — gap 2/10 (v0 · p8 · r0 · x0)

**(a)** 2건, verify lifecycle 없음 — `networking-gslb-service`(probe)에 strict read
추가(role 재확인) 또는 read lifecycle 신설.
gap: listgslbs (GET /v1/gslbs) · listgslbsregionalroutingcontrol
(GET /v1/gslbs/routing-control).
probe-only 8: creategslb, deletegslb, listgslbresources, setgslb, setgslbhealthcheck,
setgslbregionalroutingcontrol, setgslbresources, showgslb.

### compute/multinodegpucluster [U] — gap 2/16 (v1 · p12 · r0 · x1)

**(a)** 2건. host: `gen-gpu-node-image` (유일 enabled verify).
gap: listclusterfabrics (GET /v1/cluster-fabrics) · listgpunodeproducts
(GET /v1/gpu-nodes/products). excluded-by-waiver 1: listnodepools (billing-prohibitive).
probe-only 12: assigngpunodepublicnatip, creategpunodes, deletegpunodes, listgpunodes,
lockgpunode, modifyclusterfabricmembers, releasegpunodepublicnatip, showclusterfabric,
showgpunode, startgpunodes, stopgpunodes, unlockgpunode.

### management/organization — gap 2/37 (v0 · p35 · r0 · x0)

**(a)** 2건, verify lifecycle 없음(org 6개 전부 probe-guarded).
host: `org-policy-bindings-and-delegation-guarded` (assignments 도메인 소유; strict
read 추가 시 role 재확인).
gap: listpoliciesfortarget (GET /v1/assignments/policies) · listtargetsforpolicy
(GET /v1/assignments/targets).
probe-only 35: acceptinvitation, attachpolicybindings, cancelinvitations, createaccount,
createdelegationpolicy, createinvitation, createorganization, createorganizationunit,
createservicecontrolpolicy, declineinvitation, deleteaccount, deletedelegationpolicy,
deleteorganization, deleteorganizationunits, deleteservicecontrolpolicies,
leaveorganization, listaccountinvitations, listaccounts, listorganizationinvitations,
listorganizations, listorganizationunits, listparents, listservicecontrolpolicies,
moveaccount, removeaccounts, removepolicybindings, setdelegationpolicy, setorganization,
setorganizationunit, setservicecontrolpolicy, showaccount, showdelegationpolicy,
showorganization, showorganizationunit, showservicecontrolpolicy.

### management/servicewatch — gap 2/31 (v28 · p1 · r0 · x0)

**(a)** 2건. host: `servicewatch-loggroup-logstream` (log-group 생성 체인).
gap: listloggroups (GET /v1/log-groups) · showagentdownloadlink
(GET /v1/agents/download-link). probe-only 1: seteventrule.

### database/sqlserver [U] — gap 2/38 (v2 · p30 · r4 · x0)

**(a)** 2건 — 라이선스 무관 전역 read(다른 DB 서비스에선 동일 path가 2xx 검증됨).
host: `sqlserver-read-coverage`.
gap: sqlserverlistparametergroups (GET /v1/parameter-groups) ·
sqlserverlistservertypes (GET /v1/server-types).
reachability-waived 4(§C2 debt 참조): sqlserverlistbackuphistories,
sqlserverlistparameters, sqlserverlistparametervalues, sqlservershowrequest.
probe-only 30: sqlserveraddblockstorages, sqlserveraddsecondary, sqlservercreatecluster,
sqlservercreaterestore, sqlserverdeletecluster, sqlserverexportlog, sqlserverlistclusters,
sqlserverlistlogexportconfigs, sqlserverpatchminorversion, sqlserverregisterlogexportconfig,
sqlserverremovebackuphistories, sqlserverrestartcluster, sqlserversetauditlog,
sqlserversetbackup, sqlserversetblockstoragesize, sqlserversetdatabases,
sqlserversetlogexportconfig, sqlserversetmaintenance, sqlserversetparametervalues,
sqlserversetsecuritygrouprules, sqlserversetservertype, sqlservershowcluster,
sqlserverstartcluster, sqlserverstopcluster, sqlserverswitchovercluster,
sqlserversyncclusterstate, sqlserversyncparametervalues, sqlserverunregisterlogexportconfig,
sqlserverunsetbackup, sqlserverunsetmaintenance.

## Gap = 1 서비스 (15개, 통합 표)

| service | gap key | METHOD path | class | host 제안 |
|---|---|---|---|---|
| ai-ml/aimlops-platform | checkduplicationaimlopsplatformnamev1 | GET /v1/aimlops-platform/check-duplication | a | `gen-heavy-aimlops` (create 직전; 전역 GET이라 light 신설도 가능) |
| application-service/apigateway | listprivatelinkendpoints | GET /v1/privatelink-endpoints | a | `gen-wave5-apigw-privatelink` |
| storage/backup | showinstallfilepath | GET /v1/backup-agents/agent-install-file-path | a | `backup-light-reads` |
| financial-management/budget | showaccountbudget | GET /v1/budgets/account/{budget_id} | b | `budget-account-budget` (probe; create가 budget_id 캡처 — verify lc 없음, role 재확인) |
| networking/cdn | listcdnservice | GET /v1/cdns | a | `networking-cdn-service` (probe; verify lc 없음, role 재확인) |
| ai-ml/cloud-ml | checkduplicationnameanddomainname | GET /v1/cloud-ml/check-duplication | a | `gen-cloudml-chain` (create 직전) |
| data-analytics/data-flow | getdataflowimages | GET /v1/data-flows/image-versions | a | `data-flow-read-coverage` |
| data-analytics/data-ops | getdataopsimageversionv1 | GET /v1/data-ops/image-versions | a | `data-ops-read-coverage` |
| storage/filestorage | listvolumereplicationregion | GET /v1/replications/regions | a | `filestorage-replication-schedule` [H] (replication 도메인; light 대안 `filestorage-volume`) |
| security/kms | checkduplicatename | GET /v1/kms/transit/duplicate | a | `security-kms-transit-crypto` (transit create 직전) |
| storage/parallel-filestorage [U] | listaccessrule † | GET /v1/volumes/{volume_id}/access-rules | b | `parallel-filestorage-light-reads` (기존 볼륨 soft-capture) |
| application-service/queueservice | checkqueuenameduplication | GET /v1/queues/check-duplication | a | `application-queueservice-queue` (create 직전) |
| data-analytics/searchengine [U] | searchenginelistservertypes | GET /v1/server-types | a | `searchengine-read-coverage` |
| networking/security-group | listsecuritygroups | GET /v1/security-groups | a | `networking-security-group` (SG 생성 체인 — rule-list 쿼리형만 있고 bare SG list가 없음) |
| data-analytics/vertica [U] | verticalistservertypes | GET /v1/server-types | a | `vertica-read-coverage` |

이 그룹의 probe-only 키: aimlops-platform 1(getaimlopsplatformlistv1) ·
backup 10(checkconnectionstate, createbackupagent, deletebackupagent,
deletebackuprestoretarget, listagentbackuprestoretargetservers, listbackupagenttargets,
listfilesystempath, restoreagentbackup, restorebackup, showbackupagent) ·
budget 4(createaccountbudget, deleteaccountbudget, listaccountbudgets, setaccountbudget) ·
cdn 8(createcdnservice, deletecdnservice, detailcdnservice, purgecdnservice,
startcdnservice, stopcdnservice, updatecdnservice, updatedescriptionofcdnservice) ·
cloud-ml 1(updatecloudml) · data-flow 8(createdataflow, createdataflowserviceconsole,
dataflowservicevalidateresourcescreation, dataflowservicevalidateresourcesupdate,
deletedataflow, deletedataflowserviceconsole, updatedataflow, updatedataflowserviceconsole) ·
data-ops 8(createdataops, createdataopsservice, deletedataopsservice, deletedataopsv1,
getdataopsservicevalidateresourcescreation, getdataopsservicevalidateresourcesupdate,
updatedataopsservice, updatedataopsv1) · parallel-filestorage 8(createsnapshot,
createvolume, deletesnapshot, deletevolume, listsnapshots, restoresnapshot,
setvolumecapacity, showvolume) · searchengine 19(searchengineaddblockstorages,
searchengineaddinstances, searchenginecreaterestore, searchenginelistclusters,
searchenginepatchminorversion, searchengineremovebackuphistories,
searchenginerestartcluster, searchenginerestartdashboard, searchenginesetbackup,
searchenginesetblockstoragesize, searchenginesetmaintenance,
searchenginesetsecuritygrouprules, searchenginesetservertype, searchengineshowcluster,
searchenginestartcluster, searchenginestopcluster, searchenginesyncclusterstate,
searchengineunsetbackup, searchengineunsetmaintenance) · vertica 16(verticaaddblockstorages,
verticacreaterestore, verticalistclusters, verticaremovebackuphistories,
verticarestartcluster, verticasetbackup, verticasetblockstoragesize, verticasetmaintenance,
verticasetsecuritygrouprules, verticasetservertype, verticashowcluster, verticastartcluster,
verticastopcluster, verticasyncclusterstate, verticaunsetbackup, verticaunsetmaintenance).
reachability-waived: searchengine 4 · vertica 4 · parallel-filestorage 1 (§C2 debt).

## C2 debt — reachability-waived인데 어느 시나리오에도 없음 (14)

waiver 계약("waived여도 반드시 **호출은** 되어야 함 — 4xx가 증거")을 현재 아무
시나리오도 이행하지 않는 key들. gap은 아니지만 편입 시 함께 넣으면 좋음
(tolerant 4xx step으로):
searchengine 4(searchenginecreatecluster, searchenginedeletecluster,
searchenginelistbackuphistories, searchengineshowrequest) ·
vertica 4(verticacreatecluster, verticadeletecluster, verticalistbackuphistories,
verticashowrequest) · sqlserver 4(sqlserverlistbackuphistories, sqlserverlistparameters,
sqlserverlistparametervalues, sqlservershowrequest) ·
iam-identity-center 1(showgroup) · parallel-filestorage 1(setaccessrule).

## 100% 서비스 (28 — gap 0)

archivestorage, baremetal-blockstorage¹, billingplan, certificatemanager, cloudcontrol,
configinspection, costexplorer, devopsservice, direct-connect, firewall,
iam-identity-center², loadbalancer, loggingaudit, network-logging, postgresql, pricing,
product, quick-query, quota, resourcemanager, scr, secretsmanager, secretvault, ske,
sts, support, **virtualserver**³, vpn

¹ setvolumeqos는 excluded-by-waiver(billing-prohibitive)로 분모 정산.
² showgroup은 reachability-waived(위 C2 debt). ³ 25-endpoint 편입 landed(`d306edfa`)
— 113/113 verify, 본 감사로 교차 확인.

---
*레시피 무수정 — 본 문서는 owner 편입 worklist. 재현: 위 "재현 방법" 절차를
repo 내 코드/데이터로만 계산(네트워크 불요). Dedup 교차검증은 `git archive cb121d5e`
스냅샷에 동일 절차 적용 후 gap 집합 diff.*
