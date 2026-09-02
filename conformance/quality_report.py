"""AXIS 2 — 서비스 개발팀 전달용 **SCP API 품질점검 결과** 리포트 생성기.

``data/conformance.json``(정적 분석 + 런타임 프로브 폴드) 을 개발팀 어휘로 번역해
MD · HTML · CSV 3종을 ``reports/quality/<label>/`` 에 만든다. 오너 2026-08-20~09-02
핸드오프 리포트(docs/working/API-QUALITY-DEVTEAM-REPORT-2026-08-20.md) 를 만든
스크립트를 기능화한 것 — 목적은 **검증계에 다시 돌려 "해결되었는지" 를 같은
형식으로 비교** 하는 것.

    python -m conformance.quality_report                      # 현재 data/·reports/ 로 생성
    python -m conformance.quality_report --env-label 검증계    # 헤더 환경 표기 + 출력 디렉터리명
    python -m conformance.quality_report --refresh-spec --probes --static
        # (1) spec 재수집 → (2) 런타임 프로브 8종 → (3) 정적 폴드 → (4) 리포트

독자 규칙 (오너 지시, 바꾸지 말 것):
* 대상은 **우리 테스트 시스템을 모르는 서비스 개발팀** — run-id · 시나리오명 ·
  oplog 같은 내부 용어는 :func:`sanitize` 가 근거 문장에서 제거한다.
* 조사자 어투 금지 — "결함/확정/소행/오탐/재실시" 는 쓰지 않는다 (:data:`BANNED_WORDS`;
  오프라인 테스트가 검사). 건조하게 "확인된 사항 + 조치 방안" 만.
* **본문/부록** 분리 — 환경·정책에 따라 해석이 갈리는 항목(부재-id 403/401,
  WAF HTML 차단, CORS, Accept-Language)은 부록(§7) 으로.
* 항목 구분 ``cls`` = 규격(응답 동작) / 문서 / 기능.

읽기 전용: 네트워크 I/O 없음(``--probes``/``--refresh-spec`` 는 하위 프로세스로
기존 CLI 를 부를 뿐). 쓰는 파일은 ``--out`` 디렉터리 안의 3종뿐.
"""
from __future__ import annotations

import collections
import csv
import datetime as _dt
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "reports" / "quality"
CASES_FILE = ROOT / "data" / "quality_report_cases.json"

# 조사자 어투 — 리포트 본문에 나오면 안 되는 단어 (tests/offline/test_quality_report.py)
BANNED_WORDS = ("결함", "확정", "소행", "오탐", "재실시")

# ── 유형별 한국어 정의: (확인된 사항, 조치 방안) ─────────────────────────────
RULE_KR = {
 'undiscoverable-params': ('필수 파라미터의 값 형식·제약·출처가 API Reference에 없음', '필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시'),
 'opaque-validation': ('잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음', '에러 응답에 필드명과 위반 내용을 포함'),
 'no-success-schema': ('성공(2xx) 응답 스키마가 문서에 없음', '2xx 응답 바디 스키마 문서화'),
 'notfound-inconsistent': ('형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 {nf_404}개 API는 404를 반환하므로 통일 필요', '부재 리소스는 404로 통일 (또는 은닉 정책이라면 플랫폼 전체 일관 + 문서화)'),
 'notfound-200': ('존재하지 않는 리소스 조회가 200 반환', '부재 리소스는 404'),
 'notfound-200-list': ('존재하지 않는 부모의 하위 목록 조회가 200(빈 목록) 반환', '부모 부재 시 404'),
 'pagination': ('목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — size/page를 문서화한 API만 대상), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음', 'size개만 반환 + count/page/size 메타 제공. 의도적 전량 반환 API라면 size/page 파라미터를 문서에서 제거'),
 'param-naming': ('경로 파라미터 명명이 표준과 다름', '리소스명을 포함한 파라미터명 사용(예: {alert_id})'),
 'method-verb': ('엔드포인트 이름의 동사와 HTTP 메서드 불일치', '동사-메서드 정합(조회=GET, 생성=POST 등)'),
 'deprecated': ('DEPRECATED 표기만 있고 대체 API 안내 없음', '대체 엔드포인트와 제거 일정 명시'),
 '5xx-on-bad-input': ('잘못된 입력에 500 반환', '입력 오류는 400 + 원인 명시'),
 'runtime.500-on-client-state': ('클라이언트가 유발한 상태·입력에 500 반환', '4xx + 원인/해소 방법 안내 (500은 서버 내부 오류 신호로만)'),
 'networking.subnet-read-plane-version-drift': ('생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생', '조회 계열 enum을 생성 계열과 동일 버전으로 정합'),
 'compute.image-sharing-202-empty-body': ('공유 시작 202 응답 바디가 비어 있어 진행 추적 수단(공유 ID)이 없음', '202 응답에 추적 가능한 식별자 반환'),
 'compute.image-sharing-orphan-volume-no-cleanup': ('공유 과정에서 생성된 임시 볼륨이 공유 중단 시 삭제 불가능(400 반복) 상태로 잔존', '공유 레코드 소멸 시 파생 임시 볼륨 정리 경로 제공'),
 'compute.image-sharing-delete-during-transfer-unguarded': ('공유 전송 중인 원본 이미지 삭제가 차단 없이 성공(204) → 파생 임시 볼륨 영구 고아화', '전송 중 원본 삭제를 409로 차단하거나 파생 자원 연쇄 정리'),
 'docs.async-settle-undocumented': ('생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접)', '일시-상태 거절은 409로 통일(플랫폼 자체 선례 준용). 보조로 "202=접수, ACTIVE 후 변경 가능" 문서화'),
 'docs.image-share-cancellation-undocumented': ('공유의 수락/거절/취소가 별도 엔드포인트(updateimagemember)에 있음이 해당 문서에 없음', '공유 문서에 상대 엔드포인트 상호 참조'),
 'docs.version-semantics-undocumented': ('버전에 따라 응답 시맨틱이 다른데(1.1=202+빈 바디) 문서는 1.0 동작만 기술', '버전별 응답 차이 문서화'),
 'errors.rate-limit-non-json': ('유량 제한 시 JSON 에러 규격이 아닌 HTML 차단 페이지(417) 반환', '엣지 레벨에서도 표준 JSON 에러 엔벨로프 유지'),
 'runtime.empty-collection-404': ('빈 컬렉션 조회가 404 반환', '빈 컬렉션은 200 + 빈 배열'),
 'status.wrong_code_403': ('입력 검증 오류에 403 반환 (권한 문제로 오인 유발)', '입력 오류는 400'),
 'schema-missing-field': ('문서상 필수 응답 필드가 실제 응답에 없음', '문서-실응답 정합'),
 'schema-undocumented-field': ('실제 응답에 문서에 없는 필드 존재', '응답 스키마 문서 갱신'),
 'errors.detail-python-repr': ('errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열)과 달리 필드 단위 파싱 불가. 우선 조치 필요', 'detail을 JSON 배열로 반환'),
 'loadbalancer.accept-then-hang': ('전제조건(subnet 전이 완료) 미충족 상태의 생성 요청을 202로 수락한 뒤 영구 Creating 상태로 유지 — 거부·수렴·실패 전이 중 어느 것도 없어 클라이언트는 타임아웃 외 판단 불가, 잔존 리소스는 LB 삭제로도 정리되지 않음', '전제조건 미충족 시 409로 거절하거나, 수렴/실패 상태로 전이'),
 'versioning.doc-version-not-supported': ('문서에 명시된 API 버전을 서버가 406으로 거절', '문서-서버 버전 정합'),
}

# 항목 구분: 규격(응답 동작, 기본) / 문서(문서·스키마) / 기능(동작 관련 — 별도 검토 권장)
CLS = {
 'compute.image-sharing-orphan-volume-no-cleanup': '기능',
 'compute.image-sharing-delete-during-transfer-unguarded': '기능',
 'undiscoverable-params': '문서', 'no-success-schema': '문서', 'deprecated': '문서',
 'docs.image-share-cancellation-undocumented': '문서',
 'docs.version-semantics-undocumented': '문서', 'schema-undocumented-field': '문서',
}

# 플랫폼 공통(systemic) 항목 한국어 정의
SYS_KR = {
 'error-schema-undocumented': ('4xx/5xx 에러 응답 스키마가 문서에 없고, 실제 에러 엔벨로프가 3종 혼재 — 표준 errors[](서비스) · Spring 기본(공통 게이트웨이의 인증 실패 경로) · HTML(엣지 WAF). §5.5 실응답 원문 참조', '표준 errors[] 스키마 문서화 + 게이트웨이/엣지 포함 엔벨로프 단일화'),
 'model-fields-no-description': ('모델 필드 설명이 공란', '필드 설명 작성'),
 'accept-language-ignored': ('Accept-Language 헤더 무시 — 에러 메시지 영어 고정', '요청 언어 반영'),
 'path-collisions': ('서로 다른 서비스가 동일 method+path 재사용 (네임스페이스 없음)', '경로 네임스페이스 분리'),
 'unauth-404': ('공통 API 게이트웨이(Spring Cloud Gateway)가 인증 실패를 게이트웨이 기본 포맷으로 반환 — 404 + Spring 기본 엔벨로프({timestamp,path,status,error,requestId}). 전 서비스가 동일 응답이고 requestId 형식을 공유하고 있어 공통 게이트웨이 구간에서 생성되는 응답으로 확인됨 — 게이트웨이에서 일괄 개선 가능', '게이트웨이 에러 핸들러에서 401 + 표준 errors[] 엔벨로프로 변환'),
 'no-cors': ('OPTIONS 요청 403, Allow/CORS 헤더 없음', 'OPTIONS/CORS 표준 응답'),
}

APPENDIX_RULES = {'errors.rate-limit-non-json'}
SYS_APPX_TYPES = {'no-cors', 'accept-language-ignored'}
STD_ENVELOPE_EP = 'ai-ml/aimlops-platform/releaseaimlopsplatformv1'   # §5.5 ② 표준 엔벨로프 예시
SPRING_EXAMPLE_SVC = 'application-service/queueservice'                 # §5.5 ① Spring 엔벨로프 예시

CSV_FIELDS = ['category', 'service', 'endpoint', 'method', 'path', 'severity', 'status',
              'cls', 'tier', 'rule', 'problem', 'expected', 'evidence', 'src']


# ── 내부 용어 제거 ──────────────────────────────────────────────────────────
def sanitize(t: str) -> str:
    """근거 문장에서 run-id · 시나리오명 · 로그 파일명 등 우리 시스템 용어를 지운다."""
    t = t or ''
    t = re.sub(r'\((?:run|heavy)[^)]*\)', '(실측)', t)
    t = re.sub(r'\brun [0-9a-f]{4}\b(?: \(([0-9-]+)\))?',
               lambda m: '실측' + (f' {m.group(1)}' if m.group(1) else ''), t)
    t = re.sub(r'\b(?:artifact/)?events\.jsonl\b', '호출 기록', t)
    t = re.sub(r'\boplog\b', '호출 기록', t, flags=re.I)
    t = re.sub(r'\b[a-z0-9]+-cluster-subops-[a-z0-9-]+\b', '실호출', t)
    t = re.sub(r'\bregr(?!ession\b)(?:[a-z0-9]+)?\b', '<자원명>', t)
    t = re.sub(r'\bTimeline \(실측[^)]*\)', '실측 타임라인', t)
    t = t.replace("Under an opening burst (~80 lifecycles dispatched within the first 60s of a run),",
                  "단시간 다수 요청(약 60초 내 80건) 상황에서")
    t = re.sub(r'\(borrowed [^)]*\)', '', t)
    t = re.sub(r'\blifecycles?\b', '요청 흐름', t)
    t = re.sub(r"\ban AI/agent\b", "자동화 클라이언트", t)
    t = re.sub(r'\bthis run\b', '이번 실측', t)
    t = re.sub(r'  +', ' ', t)
    return t.strip()


# ── 데이터셋 ────────────────────────────────────────────────────────────────
def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _catalog_meta(root: Path) -> tuple[dict, collections.Counter]:
    cat = _load(root / "data" / "api_catalog.json", [])
    items = cat if isinstance(cat, list) else cat.get('endpoints', cat)
    if isinstance(items, dict):
        items = list(items.values())
    meta = {}
    for e in items:
        k = e.get('key')
        if k:
            meta[k] = (e.get('method', ''), e.get('http_path') or e.get('path', ''), e.get('version', ''))
    return meta, collections.Counter(e.get('method') for e in items)


def notfound_status(root: Path) -> dict[str, int]:
    """엔드포인트 → 유효-형식 부재-id 조회 응답 코드 (reports/runtime_notfound.json).
    403/401 은 권한·청약 상태로 해석될 수 있어 부록 판정에 쓴다."""
    nf = _load(root / "reports" / "runtime_notfound.json", {}) or {}
    out = {}
    for r in nf.get("results", []):
        st = r.get("status_nonexistent_id")
        if st is not None:
            out[r["endpoint"]] = st
    return out


def build_dataset(root: Path = ROOT) -> dict:
    """conformance.json + 카탈로그 → rows/통계. 리포트 렌더러의 유일한 입력."""
    conf = _load(root / "data" / "conformance.json")
    if not conf:
        raise SystemExit(f"data/conformance.json 없음 — 먼저 `python -m conformance.static` (root={root})")
    meta, m_tot = _catalog_meta(root)
    nf_status = notfound_status(root)

    def tier_of(endpoint_key: str, rule: str) -> str:
        if rule in APPENDIX_RULES:
            return '부록'
        if rule == 'notfound-inconsistent' and nf_status.get(endpoint_key) in (403, 401):
            return '부록'
        return '본문'

    # 404 를 정상 반환하는 API 수 — notfound-inconsistent 문구의 "통일 필요" 근거
    nf_404 = sum(1 for st in nf_status.values() if st == 404)
    nf_401 = sorted(k for k, st in nf_status.items() if st == 401)

    be = conf['by_endpoint']
    rows = []
    for k, v in sorted(be.items()):
        if v['status'] == 'green':
            continue
        parts = k.split('/')
        c, s = parts[0], parts[1] if len(parts) > 1 else ''
        m, pth, _ver = meta.get(k, ('', '', ''))
        for f in v['items']:
            kr = RULE_KR.get(f['type'], (f['type'], ''))
            rows.append({'category': c, 'service': s, 'endpoint': parts[-1],
                         'method': m, 'path': pth, 'severity': f['sev'], 'status': v['status'],
                         'rule': f['type'], 'problem': kr[0].replace('{nf_404}', str(nf_404)),
                         'expected': kr[1],
                         'evidence': sanitize(f.get('detail', '')), 'src': f.get('src', ''),
                         'cls': CLS.get(f['type'], '규격'),
                         'tier': tier_of(k, f['type'])})

    svc_stat: dict = collections.defaultdict(collections.Counter)
    for k, v in be.items():
        parts = k.split('/')
        key = f"{parts[0]}/{parts[1] if len(parts) > 1 else ''}"
        svc_stat[key][v['status']] += 1
        svc_stat[key]['n'] += 1
        svc_stat[key]['f'] += len(v['items'])
    cat_stat: dict = collections.defaultdict(collections.Counter)
    for key, a in svc_stat.items():
        for kk in ('red', 'yellow', 'green', 'n', 'f'):
            cat_stat[key.split('/')[0]][kk] += a[kk]

    return {'rows': rows,
            'svc_stat': {k: dict(a) for k, a in svc_stat.items()},
            'cat_stat': {c: dict(a) for c, a in cat_stat.items()},
            'summary': conf['summary'], 'systemic': conf.get('systemic', []),
            'method_total': dict(m_tot), 'nf_404_count': nf_404, 'nf_401_endpoints': nf_401,
            'generated_at': conf.get('generated_at') or conf.get('updated_at') or ''}


def _envelope_facts(root: Path) -> dict:
    """§5.5 에러 응답 형식 현황 — runtime_errors/runtime_status 원문에서 도출."""
    err = (_load(root / "reports" / "runtime_errors.json", {}) or {}).get('results', [])
    st = (_load(root / "reports" / "runtime_status.json", {}) or {}).get('results', [])
    spring = sorted(r['service'] for r in err if 'timestamp' in (r.get('envelope_keys') or ''))
    spring_ex = next((r.get('excerpt', '') for r in err if r.get('service') == SPRING_EXAMPLE_SVC), None)
    if spring_ex is None and spring:
        spring_ex = next((r.get('excerpt', '') for r in err if r['service'] == spring[0]), '')
    det_arr = sum(1 for r in st if '"detail":[' in r.get('excerpt', ''))
    det_str = [(r['endpoint'], r['excerpt'][:200]) for r in st if '"detail":"' in r.get('excerpt', '')]
    std_ex = next((r['excerpt'][:280] for r in st if r['endpoint'] == STD_ENVELOPE_EP), None)
    if std_ex is None:
        std_ex = next((r['excerpt'][:280] for r in st if '"detail":[' in r.get('excerpt', '')), '')
    repr_ex = [(k, x) for k, x in det_str if re.search(r'"detail":"\[', x)]
    return {'spring': spring, 'spring_ex': spring_ex or '', 'det_arr': det_arr,
            'det_str': det_str, 'std_ex': std_ex, 'repr_ex': repr_ex,
            'services_total': len({r['service'] for r in err})}


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    """§5 요청/응답 원문 예시 — 원문이지만 우리 시나리오 자원명(regr…)은 지운다."""
    d = _load(path, {}) or {}
    out = []
    for c in d.get('cases', []):
        c = dict(c)
        for k in ('req', 'resp', 'desc'):
            if c.get(k):
                c[k] = sanitize(c[k])
        out.append(c)
    return out


# ── 렌더링 공통 ───────────────────────────────────────────────────────────────
class _Ctx:
    def __init__(self, data: dict, root: Path, env_label: str, date: str, csv_name: str):
        self.rows = data['rows']
        self.svc_stat, self.cat_stat = data['svc_stat'], data['cat_stat']
        self.summary, self.systemic = data['summary'], data['systemic']
        self.m_tot = data['method_total']
        self.nf_404, self.nf_401 = data['nf_404_count'], data['nf_401_endpoints']
        self.env_label, self.date, self.csv_name = env_label, date, csv_name
        # 분석 대상 수는 conformance.json 의 total (카탈로그에는 method 없는 항목이 섞일 수 있다)
        self.total_api = self.summary.get('total') or sum(self.m_tot.values())
        self.main = [r for r in self.rows if r.get('tier') != '부록']
        self.appx = [r for r in self.rows if r.get('tier') == '부록']
        self.sys_main = [x for x in self.systemic if x['type'] not in SYS_APPX_TYPES]
        self.sys_appx = [x for x in self.systemic if x['type'] in SYS_APPX_TYPES]
        self.rule_count = collections.Counter(r['rule'] for r in self.main)
        self.seen_rule = {}
        for r in self.rows:
            self.seen_rule.setdefault(r['rule'], (r['problem'], r['expected']))
        self.red_rows = [r for r in self.rows if r['status'] == 'red']
        self.red_eps = sorted({(r['category'], r['service'], r['endpoint'], r['method'], r['path'])
                               for r in self.red_rows})
        self.bycat: dict = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in self.main:
            self.bycat[r['category']][r['service']].append(r)
        self.env = _envelope_facts(root)
        self.cases = load_cases()
        self.region = os.environ.get('SCP_REGION', '')

    def pct(self, key: str) -> str:
        tot = self.summary.get('total') or 1
        return f"{self.summary.get(key, 0) / tot * 100:.1f}%"

    def scope_line(self) -> str:
        env = f"{self.env_label} · " if self.env_label else ''
        region = f", {self.region}" if self.region else ''
        return (f"공식 API Reference(docs.e.samsungsdscloud.com/apireference) 전 엔드포인트 "
                f"{self.total_api:,}개 정적 분석 + 실제 API 호출 검증({env}{self.date} 실측 기준{region})")

    @staticmethod
    def unit(t: str) -> str:
        if t in ('error-schema-undocumented', 'accept-language-ignored'):
            return 'EP'
        return '필드' if 'model' in t else ('경로' if 'path' in t else '서비스')

    def sys_count(self, t: str) -> str:
        for s in self.systemic:
            if s['type'] == t:
                return f"{s.get('count', '')} {self.unit(t)}"
        return '-'


def _md_escape(t: str) -> str:
    return (t or '').replace('|', '\\|')


# ── Markdown ─────────────────────────────────────────────────────────────────
def render_markdown(x: _Ctx) -> str:
    L: list[str] = []
    A = L.append
    S, E = x.summary, x.env
    A(f'# SCP API 품질점검 결과 — {x.date}\n')
    A(f'**검증 방법**: {x.scope_line()}. '
      '실측 항목은 응답의 `global_request_id`를 함께 기재하므로 서버 로그에서 해당 호출을 직접 추적할 수 있습니다. '
      f'별첨 CSV(`{x.csv_name}`)에 전체 항목이 로데이터와 함께 있습니다.\n')
    A('## 1. 요약\n')
    A('| 판정 | 엔드포인트 수 | 비율 |\n|---|---:|---:|')
    A(f"| 이상 없음 | {S['green']} | {x.pct('green')} |")
    A(f"| 개선 필요 (YELLOW) | {S['yellow']} | {x.pct('yellow')} |")
    A(f"| 우선 개선 (RED) | {S['red']} | {x.pct('red')} |")
    A(f"\n개선 항목 총 {len(x.rows)}건 = 본문 {len(x.main)}건 + 부록(검토 필요) {len(x.appx)}건 — 부록은 환경·정책에 따라 해석이 달라질 수 있는 항목입니다. 플랫폼 공통 항목은 §2({len(x.sys_main)}종)·§7({len(x.sys_appx)}종).\n")
    A('> 확인 방법: 존재하지 않는 id는 플랫폼의 두 id 형식(32-hex·대시 UUID)을 모두 사용해 확인했으며, size/page를 문서화하지 않은 API의 전량 반환은 항목에서 제외했습니다.\n')

    A('### 메서드별 분포\n')
    A('| 메서드 | 전체 API | 항목 | RED | 항목/100API |\n|---|---:|---:|---:|---:|')
    mf = collections.Counter(r['method'] for r in x.rows)
    mr = collections.Counter(r['method'] for r in x.rows if r['severity'] == 'red')
    for m in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
        n = x.m_tot.get(m, 0); f = mf.get(m, 0)
        A(f"| {m} | {n} | {f} | {mr.get(m, 0)} | {(f / n * 100) if n else 0:.1f} |")
    A('')
    A('### 항목 구분\n')
    cf = collections.Counter(r['cls'] for r in x.rows)
    A(f"규격 관련 {cf.get('규격', 0)}건 · 문서 관련 {cf.get('문서', 0)}건 · 기능 동작 관련 {cf.get('기능', 0)}건 (기능 항목은 별도 검토 권장). 각 행과 CSV의 cls 컬럼에 구분 표기.\n")
    A('### 상품군별 통계\n')
    A('| 상품군 | API 수 | RED | YELLOW | 항목 수 |\n|---|---:|---:|---:|---:|')
    for c, a in sorted(x.cat_stat.items(), key=lambda kv: (-kv[1].get('red', 0), -kv[1].get('yellow', 0))):
        A(f"| {c} | {a.get('n', 0)} | {a.get('red', 0)} | {a.get('yellow', 0)} | {a.get('f', 0)} |")
    A('\n### 상품별 통계 (항목 보유 상품 전체)\n')
    A('| 상품 | API 수 | RED | YELLOW | 항목 수 |\n|---|---:|---:|---:|---:|')
    for k, a in sorted(x.svc_stat.items(), key=lambda kv: (-kv[1].get('red', 0), -kv[1].get('f', 0))):
        if a.get('f', 0):
            A(f"| {k} | {a.get('n', 0)} | {a.get('red', 0)} | {a.get('yellow', 0)} | {a.get('f', 0)} |")

    A('\n## 2. 플랫폼 공통 항목 (전 서비스 영향)\n')
    A('| 항목 | 범위 | 확인된 사항 | 조치 방안 |\n|---|---|---|---|')
    for s in x.sys_main:
        kr = SYS_KR.get(s['type'], (s.get('detail', ''), ''))
        A(f"| `{s['type']}` | {s.get('count', '')} {x.unit(s['type'])} | {_md_escape(kr[0])} | {_md_escape(kr[1])} |")

    A('\n## 3. 유형별 요약 (건수 순)\n')
    A('| 유형 | 건수 | 확인된 사항 | 조치 방안 |\n|---|---:|---|---|')
    for rule, n in x.rule_count.most_common():
        p, e = x.seen_rule[rule]
        A(f"| `{rule}` | {n} | {_md_escape(p)} | {_md_escape(e)} |")

    A(f'\n## 4. 우선 조치 대상 (RED) — {len(x.red_eps)}개 API\n')
    for (c, s, ep, m, pth) in x.red_eps:
        A(f"### {c}/{s} — `{m} {pth}` ({ep})")
        for r in x.red_rows:
            if r['endpoint'] == ep and r['service'] == s:
                A(f"- **[{r['cls']}]** {r['problem']}")
                if r['evidence']:
                    A(f"  - 근거: {r['evidence']}")
                if r['expected']:
                    A(f"  - 조치 방안: {r['expected']}")
        A('')

    if x.cases:
        A('## 5. 실측 예시 (요청/응답 원문)\n')
        for v in x.cases:
            A(f"### {v['title']}")
            A(v.get('desc', '') + '\n')
            A('```')
            A(f"{v['method']} {v['path']}")
            if v.get('req'):
                A(f"요청: {v['req'][:500]}")
            A(f"응답: HTTP {v['status']}")
            A((v.get('resp') or '')[:500])
            A('```\n')

    A('## 5.5 에러 응답 형식 현황 (실응답 원문)\n')
    A(f'한 클라이언트가 SCP 에러를 처리하려면 현재 최소 3가지 형태를 알아야 합니다. 전부 {x.date} 실측 원문입니다.\n')
    A(f'### ① 미인증 경로 — 프레임워크(Spring) 기본 엔벨로프, {len(E["spring"])}개 서비스 (측정 {E["services_total"]}개 중)')
    A(f'무서명 요청에 표준 errors[]가 아닌 프레임워크 기본 바디 + 401이 아닌 404. **{len(E["spring"])}개 서비스가 동일 형태이고, requestId 프리픽스가 서비스 간 재사용됨** — 개별 서비스가 아니라 **공통 API 게이트웨이(Spring Cloud Gateway 계열)의 기본 에러 핸들러**가 내는 응답으로 판단됩니다. 수정 주체 = 게이트웨이 1곳: 인증 실패를 401 + 표준 errors[] 엔벨로프로 변환.\n')
    A('```'); A(E['spring_ex'][:220]); A('```')
    A('대상: ' + ', '.join(E['spring']) + '\n')
    A('### ② 정상 인증 4xx — 표준 errors[] 엔벨로프 (이 형태가 정답이나, 문서에 스키마 없음)')
    A('```'); A(E['std_ex']); A('```')
    A('필드: code / detail / global_request_id / links / related_resources / request_id / status / title — **이 스키마를 API Reference에 공식 문서화하는 것이 개선의 출발점.**\n')
    A('### ③ 같은 표준 엔벨로프 안에서도 detail 타입 혼재')
    A(f'- JSON 배열(표준): **{E["det_arr"]}건** — `"detail":["Field required"]`')
    A(f'- 문자열: **{len(E["det_str"])}건** — ' + ', '.join(f'`{k}`' for k, _ in E['det_str']) + '\n')
    if E['repr_ex']:
        A(f'**우선 조치 필요 — {len(E["repr_ex"])}건은 파이썬 리스트의 repr을 문자열로 직렬화해 반환** (서버 내부 표현 누출, 필드 단위 파싱 불가):\n')
        A('```'); A(E['repr_ex'][0][1]); A('```')
        A('→ RED `errors.detail-python-repr` 로 분류: ' + ', '.join(k.split('/')[-1] for k, _ in E['repr_ex']) + '. (그 외 문자열 detail은 자연어 문장이라 타입 혼재 항목으로만 분류)\n')
    else:
        A('파이썬 repr 형태의 detail 문자열은 이번 실측에서 관측되지 않았습니다.\n')
    A('### ④ 엣지(WAF) 유량 차단 — HTML 페이지 (특정 API 아님, 부록성)')
    A('단시간 다수 요청 시 플랫폼 앞단 WAF가 417 + HTML "Request Rejected"(F5 계열) 반환 — 엣지 공통 동작. 차단 시에도 JSON 엔벨로프 유지가 바람직하나 WAF 관행상 논쟁적.\n')

    A('## 6. 전체 항목 목록 (YELLOW 포함, 상품군→상품별)\n')
    for c in sorted(x.bycat):
        A(f"### {c}")
        for s in sorted(x.bycat[c]):
            rs = x.bycat[c][s]
            A(f"\n#### {c}/{s} — {len(rs)}건\n")
            A('| API | 심각도 | 확인된 사항 | 근거 |\n|---|---|---|---|')
            for r in sorted(rs, key=lambda y: (y['severity'] != 'red', y['endpoint'])):
                sev = 'RED' if r['severity'] == 'red' else 'YELLOW'
                A(f"| `{r['method']} {_md_escape(r['path'])}`<br>({r['endpoint']}) | {sev} | {_md_escape(r['problem'])} | {_md_escape(r['evidence'][:260])} |")
        A('')

    nf_appx = [r for r in x.appx if r['rule'] == 'notfound-inconsistent']
    A('\n## 7. 부록 — 검토 필요 항목 (환경·정책에 따라 해석이 달라질 수 있어 분리)\n')
    A('환경·정책에 따라 해석이 달라질 수 있는 항목들입니다. 근거와 함께 전달하니 각 팀에서 의도 여부를 확인해 주십시오.\n')
    A(f'### 7.1 부재 리소스에 403 반환 — {len(nf_appx)}건')
    A(f'존재 여부 은닉(anti-enumeration) 설계라면 정당할 수 있으나, 같은 자격증명으로 {x.nf_404}개 API는 404를 반환하므로 플랫폼 정책으로 보기 어렵습니다. 은닉이 의도라면 문서화+일관화가 필요합니다.\n')
    A('| API | 근거 |\n|---|---|')
    for r in sorted(nf_appx, key=lambda y: (y['category'], y['service'], y['endpoint'])):
        A(f"| `{r['method']} {_md_escape(r['path'])}`<br>({r['category']}/{r['service']}/{r['endpoint']}) | {_md_escape(r['evidence'][:180])} |")
    A('\n### 7.2 유량 차단 시 HTML 응답 (엣지 WAF — 특정 API 아님)')
    A('단시간 다수 요청 시 앞단 WAF가 417 + HTML "Request Rejected" 반환. 차단 시에도 JSON 엔벨로프가 바람직하나 WAF 관행상 논쟁적이며, 관측 트리거도 클라이언트측 버스트였습니다.\n')
    A('### 7.3 플랫폼 공통 — 검토 항목')
    A('| 항목 | 범위 | 내용 | 참고 |\n|---|---|---|---|')
    A(f"| `no-cors` | {x.sys_count('no-cors')} | OPTIONS→403, Allow/CORS 헤더 없음 | CORS는 브라우저 개념 — 서버 간 전용 API에서는 정상일 수 있음 |")
    A(f"| `accept-language-ignored` | {x.sys_count('accept-language-ignored')} | 에러 메시지 영어 고정 | 문서가 다국어 지원을 약속한 바 없음 — 개선 요망 사항에 가까움 |")
    if x.nf_401:
        A(f'\n참고: 부재-id에 401을 반환하는 {len(x.nf_401)}건({x.nf_401[0].split("/")[1]} 등)은 서비스 미청약 계정 상태로 확인되어 목록에서 제외했습니다.\n')
    return '\n'.join(L)


# ── HTML ──────────────────────────────────────────────────────────────────────
_CSS = '''<style>
:root{--bg:#f6f7f5;--surface:#fff;--ink:#22282c;--ink-2:#5a646b;--line:#dde2df;--accent:#0e7a6e;--accent-soft:#e4f0ee;--red:#c0392f;--red-soft:#f9e9e7;--amber:#b07708;--amber-soft:#f7efdc;--green:#2e7d4f;--green-soft:#e6f1ea;--mono-bg:#eef1ef}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#14181b;--surface:#1c2225;--ink:#e3e7e9;--ink-2:#98a4ab;--line:#2d363b;--accent:#4db6a8;--accent-soft:#1d3330;--red:#e07067;--red-soft:#3a2422;--amber:#d9a23c;--amber-soft:#37301e;--green:#6dbd8f;--green-soft:#1f3328;--mono-bg:#232b2f}}
:root[data-theme="dark"]{--bg:#14181b;--surface:#1c2225;--ink:#e3e7e9;--ink-2:#98a4ab;--line:#2d363b;--accent:#4db6a8;--accent-soft:#1d3330;--red:#e07067;--red-soft:#3a2422;--amber:#d9a23c;--amber-soft:#37301e;--green:#6dbd8f;--green-soft:#1f3328;--mono-bg:#232b2f}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;font:400 15px/1.65 "IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1020px;margin:0 auto;padding:40px 22px 80px;display:flex;flex-direction:column;gap:30px}
h1,h2,h3,h4{line-height:1.3;text-wrap:balance;margin:0}
h1{font-size:26px;font-weight:700}h2{font-size:19px;font-weight:700;padding-top:8px}
h3{font-size:15.5px;font-weight:700}h4{font-size:14px;font-weight:700}
p{margin:0}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.88em;background:var(--mono-bg);padding:1px 5px;border-radius:4px}
.eyebrow{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
.meta{color:var(--ink-2);font-size:13.5px;line-height:1.7}
header.hd{display:flex;flex-direction:column;gap:10px;border-bottom:2px solid var(--accent);padding-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.stat .n{font-family:"IBM Plex Mono",monospace;font-size:26px;font-variant-numeric:tabular-nums}
.stat .l{font-size:12.5px;color:var(--ink-2)}
.stat.red .n{color:var(--red)}.stat.amber .n{color:var(--amber)}.stat.green .n{color:var(--green)}
section{display:flex;flex-direction:column;gap:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 18px;display:flex;flex-direction:column;gap:8px}
.card.sev-red{border-left:4px solid var(--red)}
.pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;border-radius:99px;padding:1px 9px;white-space:nowrap}
.pill.red{background:var(--red-soft);color:var(--red)}.pill.amber{background:var(--amber-soft);color:var(--amber)}.pill.teal{background:var(--accent-soft);color:var(--accent)}
.tw{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-2);text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 13px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;white-space:nowrap}
details{background:var(--surface);border:1px solid var(--line);border-radius:8px}
details>summary{cursor:pointer;padding:11px 16px;font-weight:700;font-size:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details>summary::-webkit-details-marker{display:none}
details[open]>summary{border-bottom:1px solid var(--line)}
details .tw{border:0;border-radius:0}
pre{background:var(--mono-bg);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto;font:12.5px/1.6 "IBM Plex Mono",monospace;margin:0;white-space:pre-wrap;word-break:break-all}
ul{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:5px}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style>'''


def render_html(x: _Ctx) -> str:
    esc = html.escape
    S, E = x.summary, x.env

    def sevpill(sev):
        return f'<span class="pill {"red" if sev == "red" else "amber"}">{"RED" if sev == "red" else "YELLOW"}</span>'

    parts: list[str] = []
    P = parts.append
    P('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">')
    P(f'<title>SCP API 품질점검 결과 — {esc(x.date)}</title>')
    P('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">')
    P(_CSS)
    P('</head><body><main>')
    P('<header class="hd"><span class="eyebrow">API Quality Report · for Service Dev Teams</span>')
    P('<h1>SCP API 품질점검 결과</h1>')
    P(f'<p class="meta">{esc(x.scope_line())} · 실측 건은 <span class="mono">global_request_id</span> 동봉 — 서버 로그에서 해당 호출 추적 가능 · 전체 항목 로데이터는 별첨 CSV({esc(x.csv_name)})</p></header>')

    P('<section><div class="stats">')
    P(f'<div class="stat green"><span class="n">{S["green"]}</span><span class="l">이상 없음 ({x.pct("green")})</span></div>')
    P(f'<div class="stat amber"><span class="n">{S["yellow"]}</span><span class="l">개선 필요 YELLOW ({x.pct("yellow")})</span></div>')
    P(f'<div class="stat red"><span class="n">{S["red"]}</span><span class="l">우선 개선 RED ({x.pct("red")})</span></div>')
    P(f'<div class="stat"><span class="n">{len(x.main)}</span><span class="l">본문 항목 (부록 검토 {len(x.appx)}건 별도 · §7)</span></div>')
    P('</div></section>')

    P('<section><h2>0 · 메서드별 통계</h2>')
    mf = collections.Counter(r['method'] for r in x.rows)
    mr = collections.Counter(r['method'] for r in x.rows if r['severity'] == 'red')
    P('<div class="tw"><table><tr><th>메서드</th><th class="num">전체 API</th><th class="num">항목</th><th class="num">RED</th><th class="num">항목/100API</th></tr>')
    for m in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
        n = x.m_tot.get(m, 0); f = mf.get(m, 0)
        P(f"<tr><td class='mono'>{m}</td><td class='num'>{n}</td><td class='num'>{f}</td><td class='num'>{mr.get(m, 0)}</td><td class='num'>{(f / n * 100) if n else 0:.1f}</td></tr>")
    P('</table></div>')
    cf = collections.Counter(r['cls'] for r in x.rows)
    P(f'<p class="meta">항목 구분 — 규격 관련 {cf.get("규격", 0)} · 문서 관련 {cf.get("문서", 0)} · 기능 동작 관련 {cf.get("기능", 0)} (기능 항목은 별도 검토 권장). 각 행과 CSV cls 컬럼에 표기.</p></section>')
    P('<section><h2>1 · 상품군·상품별 통계</h2><div class="tw"><table>')
    P('<tr><th>상품군</th><th class="num">API</th><th class="num">RED</th><th class="num">YELLOW</th><th class="num">항목</th></tr>')
    for c, a in sorted(x.cat_stat.items(), key=lambda kv: (-kv[1].get('red', 0), -kv[1].get('yellow', 0))):
        P(f'<tr><td>{esc(c)}</td><td class="num">{a.get("n", 0)}</td><td class="num">{a.get("red", 0)}</td><td class="num">{a.get("yellow", 0)}</td><td class="num">{a.get("f", 0)}</td></tr>')
    P('</table></div>')
    P('<details><summary>상품별 통계 펼치기 (항목 보유 상품 전체)</summary><div class="tw"><table>')
    P('<tr><th>상품</th><th class="num">API</th><th class="num">RED</th><th class="num">YELLOW</th><th class="num">항목</th></tr>')
    for k, a in sorted(x.svc_stat.items(), key=lambda kv: (-kv[1].get('red', 0), -kv[1].get('f', 0))):
        if a.get('f', 0):
            P(f'<tr><td class="mono">{esc(k)}</td><td class="num">{a.get("n", 0)}</td><td class="num">{a.get("red", 0)}</td><td class="num">{a.get("yellow", 0)}</td><td class="num">{a.get("f", 0)}</td></tr>')
    P('</table></div></details></section>')

    P('<section><h2>2 · 플랫폼 공통 항목 <span class="pill teal">전 서비스 영향</span></h2><div class="tw"><table>')
    P('<tr><th>항목</th><th class="num">범위</th><th>확인된 사항</th><th>조치 방안</th></tr>')
    for s in x.sys_main:
        kr = SYS_KR.get(s['type'], (s.get('detail', ''), ''))
        P(f'<tr><td class="mono">{esc(s["type"])}</td><td class="num">{s.get("count", "")} {x.unit(s["type"])}</td><td>{esc(kr[0])}</td><td>{esc(kr[1])}</td></tr>')
    P('</table></div></section>')

    P('<section><h2>3 · 유형별 요약 (건수 순)</h2><div class="tw"><table>')
    P('<tr><th>유형</th><th class="num">건수</th><th>확인된 사항</th><th>조치 방안</th></tr>')
    for rule, n in x.rule_count.most_common():
        p_, e_ = x.seen_rule[rule]
        P(f'<tr><td class="mono">{esc(rule)}</td><td class="num">{n}</td><td>{esc(p_)}</td><td>{esc(e_)}</td></tr>')
    P('</table></div></section>')

    P(f'<section><h2>4 · 우선 조치 대상 (RED) — {len(x.red_eps)}개 API</h2>')
    for (c, s, ep, m, pth) in x.red_eps:
        P(f'<div class="card sev-red"><h3><span class="mono">{esc(m)} {esc(pth)}</span> <span class="meta">— {esc(c)}/{esc(s)}/{esc(ep)}</span></h3><ul>')
        for r in x.red_rows:
            if r['endpoint'] == ep and r['service'] == s:
                ev = f'<br><span class="meta">근거: {esc(r["evidence"])}</span>' if r['evidence'] else ''
                exp = f'<br><span class="meta">조치 방안: {esc(r["expected"])}</span>' if r['expected'] else ''
                P(f'<li><b>[{esc(r["cls"])}] {esc(r["problem"])}</b>{ev}{exp}</li>')
        P('</ul></div>')
    P('</section>')

    if x.cases:
        P('<section><h2>5 · 실측 로데이터 (요청/응답 원문)</h2>')
        for v in x.cases:
            body = esc(f"{v['method']} {v['path']}\n")
            if v.get('req'):
                body += esc(f"요청: {v['req'][:500]}\n")
            body += esc(f"응답: HTTP {v['status']}\n{(v.get('resp') or '')[:500]}")
            P(f'<div class="card"><h3>{esc(v["title"])}</h3><p class="meta">{esc(v.get("desc", ""))}</p><pre>{body}</pre></div>')
        P('</section>')

    P('<section><h2>5.5 · 에러 응답 형식 현황 <span class="pill red">우선 조치 포함</span></h2>')
    P(f'<p class="meta">한 클라이언트가 SCP 에러를 처리하려면 현재 최소 3가지 형태를 알아야 합니다 — 전부 {esc(x.date)} 실측 원문.</p>')
    P(f'<div class="card"><h3>① 미인증 경로 — Spring 기본 엔벨로프 · {len(E["spring"])}개 서비스 (측정 {E["services_total"]}개 중)</h3>')
    P(f'<p class="meta">무서명 요청에 표준 errors[]가 아닌 프레임워크 기본 바디 + 401이 아닌 404. <b>{len(E["spring"])}개 서비스가 동일 + requestId 프리픽스가 서비스 간 재사용</b> — 공통 API 게이트웨이(Spring Cloud Gateway)의 기본 에러 핸들러 응답으로 확인됨 — 게이트웨이에서 인증 실패를 401 + 표준 errors[]로 변환하면 일괄 개선.</p>')
    P(f'<pre>{esc(E["spring_ex"][:220])}</pre>')
    P(f'<p class="meta">대상: {esc(", ".join(E["spring"]))}</p></div>')
    P('<div class="card"><h3>② 정상 인증 4xx — 표준 errors[] (이 형태가 정답 — 단, 문서에 스키마 없음)</h3>')
    P(f'<pre>{esc(E["std_ex"])}</pre>')
    P('<p class="meta">code / detail / global_request_id / links / related_resources / request_id / status / title — <b>이 스키마의 공식 문서화가 개선의 출발점.</b></p></div>')
    P(f'<div class="card sev-red"><h3>③ 표준 엔벨로프 안에서도 detail 타입 혼재 — 배열 {E["det_arr"]}건 vs 문자열 {len(E["det_str"])}건</h3>')
    P(f'<p class="meta">문자열 {len(E["det_str"])}건: ' + ', '.join(f'<span class="mono">{esc(k)}</span>' for k, _ in E['det_str']) + '</p>')
    if E['repr_ex']:
        P(f'<p><b>우선 조치 필요 — {len(E["repr_ex"])}건은 파이썬 리스트 repr을 문자열로 직렬화해 반환</b> (내부 표현 누출, 파싱 불가) → RED <span class="mono">errors.detail-python-repr</span> 분류.</p>')
        P(f'<pre>{esc(E["repr_ex"][0][1])}</pre>')
    else:
        P('<p class="meta">파이썬 repr 형태의 detail 문자열은 이번 실측에서 관측되지 않았습니다.</p>')
    P('</div>')
    P('<div class="card"><h3>④ 엣지(WAF) 유량 차단 — HTML (특정 API 아님)</h3>')
    P('<p class="meta">단시간 다수 요청 시 앞단 WAF가 417 + HTML "Request Rejected"(F5 계열) — 엣지 공통 동작. 차단 시에도 JSON 유지가 바람직하나 WAF 관행상 논쟁적(부록성).</p></div></section>')

    P(f'<section><h2>6 · 전체 항목 목록 ({len(x.main)}건 · 상품군→상품)</h2>')
    for c in sorted(x.bycat):
        tot = sum(len(v) for v in x.bycat[c].values())
        P(f'<h3>{esc(c)} <span class="meta">— {tot}건</span></h3>')
        for s in sorted(x.bycat[c]):
            rs = x.bycat[c][s]
            nred = sum(1 for r in rs if r['severity'] == 'red')
            badge = f'<span class="pill red">RED {nred}</span>' if nred else ''
            P(f'<details><summary><span class="mono">{esc(c)}/{esc(s)}</span> {badge} <span class="meta">{len(rs)}건</span></summary><div class="tw"><table>')
            P('<tr><th>API</th><th>심각도</th><th>구분</th><th>확인된 사항</th><th>근거</th></tr>')
            for r in sorted(rs, key=lambda y: (y['severity'] != 'red', y['endpoint'])):
                P(f'<tr><td><span class="mono">{esc(r["method"])} {esc(r["path"])}</span><br><span class="meta">{esc(r["endpoint"])}</span></td>'
                  f'<td>{sevpill(r["severity"])}</td><td>{esc(r["cls"])}</td><td>{esc(r["problem"])}</td><td class="meta">{esc(r["evidence"][:300])}</td></tr>')
            P('</table></div></details>')
    P('</section>')

    nf_appx = [r for r in x.appx if r['rule'] == 'notfound-inconsistent']
    P(f'<section><h2>7 · 부록 — 검토 필요 항목 <span class="pill amber">{len(x.appx)}건 + 공통 {len(x.sys_appx)}종</span></h2>')
    P('<p class="meta">환경·정책에 따라 해석이 달라질 수 있는 항목 — 근거와 함께 각 팀의 의도 확인을 요청.</p>')
    P(f'<details><summary>7.1 부재 리소스에 403 반환 — {len(nf_appx)}건 <span class="meta">(은닉 설계라면 정당 — 단 {x.nf_404}개 API는 404라 비일관)</span></summary><div class="tw"><table>')
    P('<tr><th>API</th><th>근거</th></tr>')
    for r in sorted(nf_appx, key=lambda y: (y['category'], y['service'], y['endpoint'])):
        P(f'<tr><td><span class="mono">{esc(r["method"])} {esc(r["path"])}</span><br><span class="meta">{esc(r["category"])}/{esc(r["service"])}/{esc(r["endpoint"])}</span></td><td class="meta">{esc(r["evidence"][:180])}</td></tr>')
    P('</table></div></details>')
    P('<div class="card"><h3>7.2 유량 차단 시 HTML 응답 (엣지 WAF — 특정 API 아님)</h3><p class="meta">단시간 다수 요청 시 앞단 WAF가 417 + HTML "Request Rejected". 차단 시에도 JSON이 바람직하나 WAF 관행상 논쟁적, 관측 트리거도 클라이언트측 버스트.</p></div>')
    P('<div class="tw"><table><tr><th>공통 항목</th><th class="num">범위</th><th>내용</th><th>논점</th></tr>')
    P(f'<tr><td class="mono">no-cors</td><td class="num">{esc(x.sys_count("no-cors"))}</td><td>OPTIONS→403 · Allow/CORS 헤더 없음</td><td>CORS는 브라우저 개념 — 서버 간 전용 API에서는 정상일 수 있음</td></tr>')
    P(f'<tr><td class="mono">accept-language-ignored</td><td class="num">{esc(x.sys_count("accept-language-ignored"))}</td><td>에러 메시지 영어 고정</td><td>문서가 다국어 지원을 약속한 바 없음 — 개선 요망 수준</td></tr>')
    P('</table></div>')
    if x.nf_401:
        P(f'<p class="meta">참고: 부재-id에 401 반환 {len(x.nf_401)}건({esc(x.nf_401[0].split("/")[1])} 등)은 서비스 미청약 계정 상태로 판단되어 목록 제외.</p>')
    P('</section>')
    P(f'<section><p class="meta">본 리포트의 모든 실측 항목은 응답 원문의 global_request_id로 서버 측 로그와 대조 가능합니다 · 전체 로데이터: {esc(x.csv_name)}</p></section>')
    P('</main></body></html>')
    return '\n'.join(parts)


def write_csv(rows: list[dict], path: Path) -> None:
    with open(path, 'w', newline='', encoding='utf-8-sig') as fp:
        w = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r['severity'] != 'red', r['category'], r['service'], r['endpoint'])):
            w.writerow(r)


# ── 파이프라인 ────────────────────────────────────────────────────────────────
def _slug(s: str) -> str:
    s = re.sub(r'[^\w.-]+', '-', s.strip(), flags=re.UNICODE).strip('-')
    return s or 'report'


def generate(root: Path = ROOT, out_dir: Path | None = None, env_label: str = '',
             date: str | None = None) -> dict:
    """리포트 3종 생성 → {'md','html','csv','out_dir','summary','rows'} 경로/수치."""
    date = date or _dt.date.today().isoformat()
    env_label = env_label or os.environ.get('SCP_ENV_LABEL', '')
    label = _slug(env_label) if env_label else ''
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT / (f"{date}-{label}" if label else date)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"SCP-API-품질점검-{date}" + (f"-{label}" if label else '')
    csv_name = f"{stem}-항목목록.csv"
    data = build_dataset(root)
    x = _Ctx(data, root, env_label, date, csv_name)
    md_p, html_p, csv_p = out_dir / f"{stem}.md", out_dir / f"{stem}.html", out_dir / csv_name
    md_p.write_text(render_markdown(x), encoding='utf-8')
    html_p.write_text(render_html(x), encoding='utf-8')
    write_csv(data['rows'], csv_p)
    return {'md': md_p, 'html': html_p, 'csv': csv_p, 'out_dir': out_dir,
            'summary': data['summary'], 'rows': len(data['rows']),
            'main': len(x.main), 'appendix': len(x.appx)}


def _run(cmd: list[str], env: dict, label: str) -> int:
    print(f"\n=== {label}: {' '.join(cmd)} ===", flush=True)
    return subprocess.run(cmd, cwd=str(ROOT), env=env).returncode


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-label", default="",
                    help="환경 표기 (예: 검증계, staging) — 헤더/파일명/출력 디렉터리에 반영. 기본 $SCP_ENV_LABEL")
    ap.add_argument("--out", default="", help=f"출력 디렉터리 (기본 {DEFAULT_OUT.relative_to(ROOT)}/<date>[-label]/)")
    ap.add_argument("--date", default="", help="실측 기준일 표기 (기본 오늘, YYYY-MM-DD)")
    ap.add_argument("--refresh-spec", action="store_true",
                    help="리포트 전에 spec.extract_catalog --fresh 로 API 명세 재수집 (검증계 배포 후 필수)")
    ap.add_argument("--probes", action="store_true",
                    help="리포트 전에 conformance.runtime --probe all 실행 (실제 호출 — 환경변수 필요)")
    ap.add_argument("--probe", default="all", help="--probes 시 프로브 선택 (기본 all)")
    ap.add_argument("--static", action="store_true",
                    help="리포트 전에 conformance.static 재폴드 (--probes 를 켜면 자동)")
    args = ap.parse_args(argv)

    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    if args.refresh_spec:
        rc = _run([sys.executable, "-m", "spec.extract_catalog", "--fresh"], env, "spec refresh")
        if rc:
            print(f"spec refresh failed rc={rc}", file=sys.stderr); return rc
    if args.probes:
        # console2 _conformance_worker 와 같은 게이트: status/l10n 프로브는 빈 바디 POST 로
        # 400 을 측정하므로 MUTATIONS=true 가 필요하고(아니면 checked=0), destructive 는 항상 false.
        penv = {**env, "SCP_PROBE_RUNTIME": "true", "SCP_ALLOW_MUTATIONS": "true",
                "SCP_ALLOW_DESTRUCTIVE": "false"}
        rc = _run([sys.executable, "-m", "conformance.runtime", "--probe", args.probe], penv, "runtime probes")
        if rc:
            print(f"probes finished rc={rc} — 계속 진행(부분 결과 폴드)", file=sys.stderr)
    if args.probes or args.static:
        rc = _run([sys.executable, "-m", "conformance.static"], env, "static fold")
        if rc:
            print(f"static fold failed rc={rc}", file=sys.stderr); return rc

    res = generate(ROOT, Path(args.out) if args.out else None, args.env_label, args.date or None)
    s = res['summary']
    print(f"\nSCP API 품질점검 결과 — green {s['green']} / yellow {s['yellow']} / red {s['red']} "
          f"(총 {s['total']}) · 항목 {res['rows']}건 = 본문 {res['main']} + 부록 {res['appendix']}")
    for k in ('md', 'html', 'csv'):
        print(f"  {k:4s} {res[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
