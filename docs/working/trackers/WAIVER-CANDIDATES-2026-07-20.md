---
status: proposal
for: owner
---

# Waiver 후보 일괄 제안 — 2026-07-20 프론티어 130키 전수 트리아지 산출

> **오너 승인 대기 제안서.** `data/baselines/coverage_waivers.json`은 인간 승인
> 필수(파일 규약)라 여기에 **붙여넣기 가능한 엔트리**로 정리했다. 근거는
> `knowledge/validated-facts.md` 2026-07-20 블록 + PF 원장(PF-37/48/49/50/51).
> class 어휘: `blast-radius | entitlement | unsatisfiable-flow | billing-prohibitive`
> (+ 기존 파일에서 쓰는 `reachability`).
>
> **주의**: scr 19키·DB log-export 12키는 waiver보다 **PF/SDS-문의 유지**가 맞을
> 수 있다(해소되면 배선이 이미 완료돼 자동 fold — waiver로 분모에서 빼면 해소를
> 놓친다). 아래 §3에 분리했다. §1·§2만 즉시 waiver 감.

## §1. entitlement / console-only — 계정 구조상 이 계정에서 영구 불능 (35키)

| 키 | class | 근거 |
|---|---|---|
| management/organization/{listaccounts, listorganizationinvitations, listorganizationunits, listparents, listpoliciesfortarget, listservicecontrolpolicies, listtargetsforpolicy, showaccount, showdelegationpolicy, showorganization, showservicecontrolpolicy, showorganizationunit} (12) | entitlement | 멤버 계정, IAM Deny 디코드 "[IAM] not allowed by delegation policy(organization)" (2026-07-20 실측) |
| ai-ml/cloud-ml/* 9키 전부 | entitlement | 라우트 미프로비저닝 — 익명 Spring 404 전 경로 (2026-07-20 실측; gen-cloudml-chain 오너 waiver와 정합) |
| platform/sts/{assumerole, assumerolewithsaml, objectstoreauthorization} (3) | entitlement | assumerole 403 위임정책(자기신뢰 role로도) · SAML은 실 IdP 필요 · objstore는 PF-51(500)이자 세션토큰 전제 |
| security/configinspection/{creatediagnosisobject, diagnosisrequest, getdiagnosisobjectdetail, getdiagnosisresultdetail, terminatediagnosisobject} (5) | entitlement (console-only auth_key) | 진단 auth_key 발급 API가 카탈로그 1,416개 어디에도 없음(2026-07-16 전수 재확인) — 콘솔 등록 필요. 오너가 콘솔에서 auth_key 등록해주면 체인 전체 자동 활성(waiver 불요) — **오너 선택지 A: 콘솔 등록 / B: waiver** |
| security/secretvault/gettemporarykey (1) | unsatisfiable-flow | vault 발급 Sv* 헤더 서명 체계 — SCP 키로 파생 불가(클라이언트 미지원 축) |
| security/kms/updatemanagedkeydescription (1) | unsatisfiable-flow | transit managed key count:0 영구(생성 API 없음, 2026-07-13 확정) |
| management/quota/showquotarequest (1) | unsatisfiable-flow (console-only) | quota request는 콘솔 제출 전용, 생성 API 없음, 계정 내 0건 |
| devops-tools/devopsservice/{createdevopsservice, deletedevopsservice} (2) | entitlement | admin-user 미보유(not-found-admin-user) — 2026-07-20 재현 불가였으나 fragment 라이브 관찰 기록 준거 |
| secretsmanager/createsecretsmanagerkmskey (1) | reachability (spec-removed) | 2026-07 스펙에서 공식 제거(setkmsid로 대체) — 카탈로그 잔존키. waiver보다 **카탈로그 정리**가 정도 |

## §2. billing-prohibitive — 과금 약정/고가 리소스 전제 (21키)

| 키 | class | 근거 |
|---|---|---|
| financial-management/billingplan/{createplannedcomputes, updateplannedcompute, showplannedcompute, showcancellationfee} (4) | billing-prohibitive | planned compute = 약정 구매, DELETE API 없음 |
| data-analytics/quick-query/* 8키 | billing-prohibitive | 실 클러스터(3-node SKE+VPC) 전제 create — 기존 오너 결정 유지 |
| storage/parallel-filestorage/{listsnapshots, listaccessrule} (2) | billing-prohibitive | PFS 볼륨 과금 — listsnapshots는 volume_id 필수 쿼리 확정(2026-07-20), 볼륨 생기면 즉시 2xx 가능 |
| database/{postgresql,mariadb}/…createotherregionreplica + {mysql,epas} 500류 (4) | billing-prohibitive | 타리전 subnet/DR 인프라 전제 (mysql/epas 500은 PF 재분류 여지 — §3 참조) |
| database 5엔진 switchovercluster (5)* | billing-prohibitive (HA 2x) | ha_enabled=true 재프로비저닝 필요 — 기존 ② 오너 큐. *또는 HA 1회 실험 런으로 5키 일괄 회수 가능 |
| networking/loadbalancer/{createloadbalancerprivatenatip, showloadbalancercertificate} (2)** | entitlement/연쇄 | private-NAT 부재 환경 + LB cert는 PF-49(certmanager) 연쇄 — **PF-49 해소 시 showloadbalancercertificate는 자동 회수 가능성, waiver 보류 권장** |

## §3. waiver 비권장 — PF/SDS-문의 유지 (해소 시 자동 fold, 배선 완료 상태)

- **container/scr 19키**: PF-37(레지스트리 토큰서버가 유효키 거부, 오너 로컬
  docker도 실패). 시드 배선 완료 — 이미지 1개 push되는 순간 17키 자동 fold.
- **DB log-export 12키**: register 500 PF(클린 입력 재확정은 이번 access_key
  토큰화 후 다음 heavy 런이 판정) — register가 뚫리면 set/export/unregister
  체인 최대 16키 연쇄 fold.
- **servicewatch/createcustommetrics**: PF-50 — SDS가 라우팅 키를 공개/수리하면
  자동 fold.
- **certificatemanager 2키**: PF-49 — 콘솔 발급 자재 1회 크로스체크(오너)로
  파서 결함 vs 자재 요건 판정 완결.
- **compute/virtualserver 4키**(password PF-17·importimage·updateimagemember·
  private-nat 2키), **vpc/approvalvpcpeering**: 기존 판정 유지(§ 오너 ② 큐).

## 적용 방법 (오너)

승인 키만 `data/baselines/coverage_waivers.json`의 `waivers` 배열에 아래 형식으로
추가 (기존 엔트리 스타일 준수):

```json
{"key": "<category/service/opname>", "class": "<entitlement|billing-prohibitive|unsatisfiable-flow|reachability>",
 "reason": "<위 표의 근거 + 날짜>", "approved_by": "owner", "approved_at": "2026-07-XX"}
```
