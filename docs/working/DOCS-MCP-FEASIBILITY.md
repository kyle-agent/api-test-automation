# SCP Docs MCP Server — 도입 타당성 조사 (2026-07-15)

> 오너 지시 #6: "docs를 스크래핑으로 긁는 대신 오픈된 MCP로 가져올 수 있는지 확인."
> 조사 + 라이브 실증 결과. 코드 변경 없음. 작성: docs-MCP 조사 세션.

## 결론 (TL;DR)

**조건부 — spec 파이프라인 대체는 불가, 보조 지식 조회용으로만 도입 권고.**

MCP 서버는 살아 있고(인증 불필요, streamable HTTP, 핸드셰이크 성공) 도구는
`QueryKnowledgeBase` **1개뿐**이다. 인덱싱 범위가 User Guide / Architecture
Center / Knowledge Center / FAQ로, 우리 스크래퍼가 소비하는 **`/apireference/`
(엔드포인트·모델 문서)는 인덱스에 없다.** `SubnetCreateRequest` 모델 필드를
한/영으로 질의해도 apireference URL이 단 하나도 반환되지 않았다(아래 실증).
따라서 카탈로그/바디/OpenAPI 생성 파이프라인은 스크래핑을 유지해야 한다.

---

## (a) MCP 서버 스펙 요약

출처: [MCP Server 사용 가이드](https://docs.e.samsungsdscloud.com/userguide/developers_tools/mcp_server_enterprise/how_to_guides/docs_mcp_server/) + 라이브 initialize 응답.

| 항목 | 값 |
|---|---|
| 엔드포인트 | `https://scp-docs-mcp.kr-west1.e.samsungsdscloud.com/mcp` |
| 전송 방식 | **Streamable HTTP** (POST JSON-RPC → `text/event-stream` SSE 프레이밍 응답; 세션 헤더 없이 stateless 동작 확인) |
| 인증 | **없음** — 가이드 명시: "별도 SCP 계정이 필요하지 않으며 SCP 웹사이트 이용 약관을 적용 받습니다" |
| 서버 | `scp-knowledge-mcp-server` v3.4.3 (FastMCP 기반, `_meta.fastmcp` 존재), protocolVersion 2025-03-26 |
| 데이터 출처 (가이드 + 서버 instructions 모두 일치) | User Guide(`/userguide/`), Architecture Center(`/arch_cent/`), Knowledge Center(`/knowledge_cent/`), FAQ(서비스포탈). **`/apireference/` 미포함** |

### 제공 tool (tools/list 실측 — 1개)

```
QueryKnowledgeBase
  query    (string, required)  — 자연어 질의
  language ("ko"|"en", default "ko")
  top_k    (integer, default 4) — 반환 문서 수
→ 반환: [{content: <문서 청크 텍스트>, metadata: {source: <원문 URL>, label}}]
```

RAG식 문서 검색 도구다. 문서 직접 조회(URL/ID 지정 fetch), API 명세 조회,
모델 스키마 조회 도구는 **없다**. prompts/resources capability는 광고되지만
실질 도구는 위 하나.

## (b) 접속 실증 결과 (이 환경, 프록시 경유)

모두 `curl --cacert /root/.ccr/ca-bundle.crt`, 인증 헤더 없이 성공.

1. **initialize** → HTTP 200, 정상 JSON-RPC 응답 (serverInfo/capabilities/instructions 수신). 세션 ID 헤더 미발급 — 이후 요청도 stateless로 성공.
2. **tools/list** → HTTP 200, 위 `QueryKnowledgeBase` 1개 스키마 수신.
3. **tools/call 실증 A (ko)** — 질의: "Subnet 생성 API SubnetCreateRequest 요청 모델의 필드 목록(vpcId, subnetCidrBlock 등)과 각 필드 타입"
   → 반환 4건 전부 FAQ/arch_cent (LB 개수 제한, SASE 소개, VPC 개수 제한 등). **모델 필드 정보 0건.**
4. **tools/call 실증 B (en, top_k=8)** — 질의: "API reference: POST subnet create request body schema SubnetCreateRequest fields …"
   → 반환 source: `arch_cent/reference/vpc_dmz_web_service`, FAQ 2건, `knowledge_cent/networking/vpc/vpc_subnet_ip_range`. **apireference URL 0건.**

응답 샘플 (실증 A 중 1건):

```json
{"content": "## Account 당 VPC 몇 개까지 사용할 수 있습니까?\n\n하나의 Account에 VPC 5개까지 구성할 수 있습니다.",
 "metadata": {"source": "https://cloud.samsungsds.com/serviceportal/support/faq.html?search=...", "label": "Account 당 VPC..."}}
```

### 스크래핑 결과와의 비교 (같은 대상: subnet create request 모델)

스크래퍼 산출 `data/api_docs.json` → `models["networking/vpc/subnetcreaterequestv1dot2"]`:
필드별 구조화 테이블 — `cidr (string, required)`, `allocation_pools (array[object])`,
`dns_nameservers (array[string])`, `gateway_ip_address`, … (name/required/schema/description/default).
MCP는 이 정보에 접근 자체가 불가.

## (c) 스크래핑 대비 장단점

| 축 | 스크래핑 (`spec/*.py`) | Docs MCP |
|---|---|---|
| **완전성 (API 명세)** | ✅ 1,372 엔드포인트 + 모델 필드 테이블 + 예제 바디 전량 | ❌ apireference 미인덱스 — 명세/모델 문서 접근 불가 |
| **버전별 모델 (v1dotN)** | ✅ `.../models/subnetcreaterequestv1dot2/` 등 버전 변형을 개별 키로 수집 | ❌ 제공 안 함 (실증 A/B에서 0건) |
| **결정성/재현성** | ✅ 전수 크롤 → 스냅샷/diff 가능 | ❌ top-k 유사도 검색 — 전수 열거 불가, 재현 비결정적 |
| **속도/부하** | △ 페이지당 ~4.3MB SSR → Range 요청 필수, 간헐 503 재시도 | ✅ 청크만 반환, 가볍고 빠름 (503 게이트웨이와 별개 호스트) |
| **파싱 취약성** | △ HTMLParser + 정규식, phantom-row 필터 등 fragile | ✅ 파싱 불필요 (서버가 정제 텍스트 반환) |
| **가이드/FAQ/아키텍처 지식** | ❌ 파이프라인 대상 아님 (apireference만 긁음) | ✅ 유일하게 여기서 강함 — 쿼터/요금/제약 등 자연어 조회 |
| **인증/운영 비용** | 없음 | 없음 (계정 불필요) |

핵심: **두 시스템은 다른 문서 집합을 본다.** 스크래퍼=apireference(명세),
MCP=userguide/arch/knowledge/FAQ(지식). 대체 관계가 아니라 보완 관계.

## (d) 도입 방안 (보조 도구로서)

### Claude Code 세션용 `.mcp.json` 스니펫 (프로젝트 루트)

```json
{
  "mcpServers": {
    "scp-knowledge": {
      "type": "http",
      "url": "https://scp-docs-mcp.kr-west1.e.samsungsdscloud.com/mcp"
    }
  }
}
```

(이 환경은 outbound가 프록시 경유이므로 Claude Code의 HTTP MCP 클라이언트가
`HTTPS_PROXY` + CA 번들을 그대로 쓰면 됨 — curl 실증에서 프록시 통과 확인됨.)

### spec 리프레시 파이프라인 전환 단계 — **현시점 비권고, 조건부 로드맵만 기록**

전제 조건: SCP가 MCP에 apireference 인덱스(또는 spec 조회 tool)를 추가할 때만 착수.
1. 분기 조사: tools/list를 주기 재확인(스펙 조회 tool 추가 여부 감시 — spec-intel 리프레시 시 1회 curl이면 충분).
2. 추가되면: `spec/scrape_docs.py`의 모델/엔드포인트 fetch 함수 뒤에 MCP 클라이언트 백엔드를 두고, 산출 스키마(`data/api_docs.json` 포맷)는 유지 — 다운스트림(`enrich_catalog`, `build_openapi`) 무변경.
3. 전환 검증: 동일 스냅샷을 양쪽으로 생성해 `spec/diff.py`로 비교, 필드 손실 0 확인 후 스위치.

### 즉시 활용 가능한 지점 (파이프라인 밖)

- `coverage-service`/`ai-evaluator`/`conformance` 에이전트가 쿼터·요금·서비스 제약을
  자연어로 조회할 때 (예: "Account당 VPC 5개" — FAQ에서 즉답). 현재는 이런 지식이
  `knowledge/`에 수동 축적되는데, MCP가 1차 조회원이 될 수 있음.
- 단, MCP 응답도 "메모리는 힌트" 규칙 적용 — 라이브 관측과 충돌 시 관측 우선.

## (e) 권고

- **spec 파이프라인 대체: 보류.** 근거: apireference 미인덱스(실증 2회 0건), 버전별
  모델(v1dotN) 미제공, top-k 검색이라 전수성·결정성 없음 → 카탈로그/diff 용도 부적합.
- **보조 지식 도구로 도입: 찬성(저비용).** 근거: 무인증·무설정 접속 성공, FAQ/가이드
  청크가 소스 URL과 함께 반환 — 에이전트의 쿼터/제약 질의 품질을 올릴 수 있음.
  도입 시 위 `.mcp.json` 한 파일 추가로 끝 (별도 결정/승인 후 진행).
- **감시 항목:** 서버 버전(현 3.4.3)·tool 목록 변화. apireference 인덱스가 추가되는
  순간 (d)의 전환 로드맵이 유효해짐.

---
*실증 원본(핸드셰이크/응답 캡처)은 세션 스크래치패드에 있으며 재현 커맨드는 (b)의 curl 패턴 그대로다. 자격증명은 일절 사용/기록하지 않음.*
