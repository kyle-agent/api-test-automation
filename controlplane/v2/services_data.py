"""서비스 축(②) 데이터 계층 — L1 데이터 계약 §2.2.

``dashboard.build``의 순수 함수(``load_catalog`` · ``per_service`` — 내부에서
``endpoint_verdicts``를 그대로 사용)를 서버에서 직접 import해 서비스별 집계를
재현한다. HTML 파싱 금지·로직 복제 금지: 커버리지 계산은 전부 그 함수들에
위임하고, 이 모듈이 하는 일은 (a) 계약이 정한 입력 파일들을 그 함수들이
기대하는 모양으로 맞추고 (b) 결과를 화면이 바로 쓸 dict로 얇게 펴는 것뿐이다.

입력은 전부 S1(발행본, ``controlplane.dashdata`` — root 파일만) + 저장소
(repo HEAD) 파일이다. 로컬 observations(``reports/results/*.jsonl``)는 쓰지
않는다 — 이 화면은 "발행 기준 재현"이 목적이다 (계약 §2.2).

실패 시(발행 접근 불가 등) 예외를 삼키고 ``None``을 반환한다 — 화면은
empty-state로 성립해야 한다 (계약 §3).
"""
from __future__ import annotations

import html
import json
import time
from collections import Counter
from pathlib import Path

from controlplane import dashdata
from controlplane.v2 import terms
from dashboard.build import load_catalog, per_service, slug  # noqa: F401  (slug re-exported)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = REPO_ROOT / "data" / "api_catalog.json"
KNOWN_ISSUES_PATH = REPO_ROOT / "data" / "baselines" / "known_issues.json"
# coverage_waivers.json / untestable_services.json (계약 §2.2의 "마스킹" 입력):
#   - untestable_services.json은 dashboard.build.per_service가 모듈 임포트 시점에
#     이미 저장소에서 읽어 각 서비스 dict에 overlay한다("untestable" 필드) — 여기서
#     따로 읽을 필요가 없다 (재구현 금지).
#   - coverage_waivers.json은 per_service()가 애초에 받지 않는 인자다: 발행된
#     서비스 상세 페이지(render_service_page) 자신도 서비스 단위 커버리지 %에
#     waiver를 반영하지 않는다(반영은 오직 사이트 전체 집계 compute()에서만) —
#     그 실제 동작을 그대로 따른다. 판단 근거는 이 파일 하단 주석 참조.

_CACHE_TTL = 60.0  # dashdata의 git-fetch TTL과 같은 주기로 맞춘다
_cache: dict = {"sha": None, "at": 0.0, "data": None}


def _status_category(status: int) -> str:
    """2xx -> ok(검증) · 5xx/401 -> fail(실패) · 그 외 -> soft(도달).

    regression/smoke.py의 classify()를 상태코드 단위로 근사한다 — 발행본
    endpoint_status.json에는 응답 바디/텍스트가 없어 401의 soft/hard 세부
    분기(그 함수는 텍스트로 구분한다)까지는 재현할 수 없다. 근사이지 계산
    로직의 재구현이 아니다: endpoint_verdicts()가 쓰는 3-way ok/fail/soft
    구분을 위한 입력 어댑터일 뿐, 커버리지 판정 자체는 여전히 그 함수가 한다."""
    if 200 <= status < 300:
        return "ok"
    if status >= 500 or status == 401:
        return "fail"
    return "soft"


def _synth_tsv_rows(status_map: dict) -> list[tuple]:
    """S1 endpoint_status.json({key: [status, elapsed_ms, sha]})을
    endpoint_verdicts()/per_service()가 기대하는 6-tuple로 변환한다
    (``dashboard.build.obs_to_tsv_row``와 같은 어댑터 역할).

    이게 없으면 verdict는 prior_verified 오버레이로만 "verified"가 채워지고
    "도달"/"실패" 상태는 절대 계산되지 않는다 — 그 두 상태는 오직 tsv_rows를
    거쳐 endpoint_verdicts()가 매기기 때문. 발행본의 마지막 관측 상태를
    "이번 실행이 관측한 값"으로 넣어 그 판정 경로를 재사용한다."""
    rows = []
    for key, val in (status_map or {}).items():
        if not isinstance(val, (list, tuple)) or not val:
            continue
        status = val[0]
        if not isinstance(status, int):
            continue
        elapsed = val[1] if len(val) > 1 else None
        rows.append((status, _status_category(status), key, "", "", elapsed))
    return rows


def _load_dd_json(name: str) -> dict:
    """dashdata의 root 파일 하나 -> dict. 실패/부재 시 {} (empty-state 원칙)."""
    got = dashdata.file(name)
    if not got:
        return {}
    try:
        return json.loads(got[0].decode(errors="replace"))
    except ValueError:
        return {}


def _merge_known_issues(conf_by_endpoint: dict) -> dict:
    """저장소 data/baselines/known_issues.json의 PRODUCT 버그를 by_endpoint에
    병합 — dashboard.build.build()의 동일 병합(그 함수 내부 인라인 로직이라
    별도 import가 불가능해, 여기서 그 로직만 그대로 옮겨온다: 이 저장소가
    소유한 버그는 어느 머신에서 열어도 결함 열에 보이게 하려는 목적)."""
    if not KNOWN_ISSUES_PATH.exists():
        return conf_by_endpoint
    try:
        known_data = json.loads(KNOWN_ISSUES_PATH.read_text())
    except ValueError:
        return conf_by_endpoint
    for it in known_data.get("issues", []):
        ik = it.get("key") or ""
        typ = (it.get("type") or "").strip()
        if not ik or "product" not in typ.lower():
            continue
        rec = conf_by_endpoint.setdefault(ik, {"status": "green", "items": []})
        rec["items"].append({
            "type": f"{typ} (baselined)", "src": "known_issues",
            "issue": it.get("since", ""),
            "detail": ((f"live {it.get('status')} · " if it.get("status") else "")
                       + (it.get("note") or "")),
        })
        rec["status"] = "red"
    return conf_by_endpoint


_COV_LABEL = {
    "verified": terms.TERMS["cov_verified"]["label"],
    "reached": terms.TERMS["cov_reached"]["label"],
    "failed": terms.TERMS["cov_failed"]["label"],
    "none": terms.TERMS["cov_none"]["label"],
}
_COV_ICON = {"verified": "✓", "reached": "◑", "failed": "⛔", "none": "·"}


def _resource_group(path: str) -> str:
    """경로 세그먼트 기반 그룹명 — 발행 페이지 JS의 ``r.p.split('/')[2]||'기타'``
    (``dashboard/build.py`` SVC_TEMPLATE 내 render() 함수, 이식 원본:
    ``origin/dashboard-data:services/networking__loadbalancer.html``)를
    서버 사이드로 그대로 옮긴 것 — 세그먼트가 없으면 "other"(D7: 값은 영어)."""
    parts = (path or "").split("/")
    return parts[2] if len(parts) > 2 and parts[2] else "other"


def _endpoint_rows(service: dict, conf_by_endpoint: dict, status_map: dict,
                    evidence: dict) -> list[dict]:
    """per_service()가 반환한 s['rows'] 튜플(method, path, name, covered,
    status, elapsed_ms, verdict, src)을 화면용 dict로 편다 — 집계 로직 없음,
    표시용 매핑뿐. 최근 status/응답시간은 per_service의 내부 called/merged
    오버레이(src="this run" 가정)를 쓰지 않고 status_map을 직접 조회한다:
    v2는 "이번 런"을 실행하지 않으므로 그 구분이 성립하지 않기 때문 —
    보이는 값은 전부 발행 누적 관측이다.

    ``ev`` (검증 근거 run id)는 ``origin/claude/continuation-uk2rwc``의
    ``dashboard/build.py`` per_service() durable-evidence override(owner
    2026-07-10, "stale-cell fix")를 재현한다: covered==True인데 최근 관측
    status가 없거나 2xx가 아니면(발행 캐시가 stale), 누적 근거 저장소
    (``verified_endpoints_evidence.json`` — dashdata root)에서 그 키의
    last_run/first_run을 찾아 화면이 "이 run에서 검증됨"을 보여줄 수 있게 한다.
    실패(파일 없음/키 없음) 시 조용히 생략 — 계약 §3 empty-state 원칙."""
    out = []
    for method, path, title, covered, status, elapsed_ms, verdict, *_src in service["rows"]:
        cov = ("verified" if covered
               else "failed" if verdict == "failed"
               else "reached" if verdict == "reached" else "none")
        key = f'{service["category"]}/{service["service"]}/{title}'
        rec = conf_by_endpoint.get(key, {})
        pub = status_map.get(key)
        pub_status = pub[0] if isinstance(pub, (list, tuple)) and pub else None
        pub_elapsed = pub[1] if isinstance(pub, (list, tuple)) and len(pub) > 1 else None
        pub_sha = pub[2] if isinstance(pub, (list, tuple)) and len(pub) > 2 else None
        elapsed_s = round(pub_elapsed / 1000, 1) if pub_elapsed is not None else None
        ev_run = None
        if covered and (pub_status is None or not (200 <= pub_status < 300)):
            dur = evidence.get(key) or {}
            ev_run = dur.get("last_run") or dur.get("first_run") or None
        out.append({
            "method": method, "path": path, "api": title,
            "cov": cov, "cov_label": _COV_LABEL[cov], "cov_icon": _COV_ICON[cov],
            "status": pub_status, "elapsed_s": elapsed_s, "sha": pub_sha,
            "ev": ev_run or None,
            "slow": elapsed_s is not None and elapsed_s >= 3,
            "group": _resource_group(path),
            "defect_status": rec.get("status", "green"),
            "defects": rec.get("items", []),
        })
    return out


def _action_banner(svc: dict, rows: list[dict]) -> str:
    """자동 액션 배너 — ``dashboard/build.py`` ``render_service_page()``의
    생성 로직(약한 축 판정 · 최빈 결함 · 신규 5xx/auth, L675 부근, untestable
    분기 L705-711 / 일반 분기 L738-755)을 그대로 이식한다(문장·상수 동일,
    파이썬 재작성만). 반환값은 이미 html.escape로 안전화된 HTML 문자열."""
    if svc.get("untestable"):
        n_reachable = sum(1 for r in rows if r["status"] is not None)
        return (f"<b>기능 테스트 제외 서비스</b> — {html.escape(svc['untestable'])} "
                f"(owner 2026-06-13). smoke가 각 API의 <b>접근성만</b> 확인한다: "
                f"{n_reachable}/{svc['total']}개 엔드포인트가 응답(4xx 포함 = 도달). "
                "커버리지 분모에서는 waiver로 제외되어 있다.")

    gtot, gcov = svc.get("gtot") or 0, svc.get("gcov") or 0
    wtot, wcov = svc.get("wtot") or 0, svc.get("wcov") or 0
    gpct = gcov / gtot * 100 if gtot else 0
    wpct = wcov / wtot * 100 if wtot else 0
    n_failed = sum(1 for r in rows if r["cov"] == "failed")
    def_count: Counter = Counter()
    for r in rows:
        for it in r["defects"]:
            def_count[it.get("type", "")] += 1

    bits = []
    if n_failed:
        bits.append(f"<b>신규 5xx/auth 실패 {n_failed}건 — 조치 필요.</b>")
    weak_write = wpct <= gpct
    axis = (f"쓰기 커버리지 {wpct:.0f}%가 약점" if weak_write
            else f"읽기 커버리지 {gpct:.0f}%가 약점")
    bits.append(
        f"<b>다음 작업 후보:</b> {axis}. 미검증 쓰기 대부분은 부모 리소스 "
        "ID가 없어 404(probe 한계 = 정상) — CRUD 시나리오를 추가하면 도달 가능."
        if weak_write else
        f"<b>다음 작업 후보:</b> {axis} — read-chain/probe 보강 대상.")
    if not n_failed:
        bits.append("회귀 위험은 없음(신규 5xx/auth 0).")
    top = def_count.most_common(2)
    if top:
        bits.append("문서/설계 결함 중 가장 흔한 건 "
                    + "와 ".join(f"<b>{html.escape(t)}({n})</b>" for t, n in top)
                    + ".")
    return " ".join(bits)


def _pct(n: int, d: int) -> str:
    return f"{(n / d * 100):.0f}%" if d else "—"


def _build() -> dict | None:
    """전체 재계산(캐시 미스 시에만 호출) — 실패하면 None."""
    try:
        if not CATALOG_PATH.exists():
            return None
        cat = load_catalog(str(CATALOG_PATH))
        if not cat:
            return None

        verified_doc = _load_dd_json("verified_endpoints.json")
        status_doc = _load_dd_json("endpoint_status.json")
        conf_doc = _load_dd_json("conformance.json")

        prior_verified = set(verified_doc.get("verified") or [])
        status_map = status_doc.get("status") or {}
        conf_by_endpoint = _merge_known_issues(dict(conf_doc.get("by_endpoint") or {}))

        tsv_rows = _synth_tsv_rows(status_map)
        services, _merged = per_service(
            cat, tsv_rows, prior_verified=prior_verified,
            prior_status=status_map, sha="")

        out_services = []
        for s in services:
            defect_red = defect_yellow = 0
            for method, path, title, *_rest in s["rows"]:
                key = f'{s["category"]}/{s["service"]}/{title}'
                st = conf_by_endpoint.get(key, {}).get("status")
                if st == "red":
                    defect_red += 1
                elif st == "yellow":
                    defect_yellow += 1
            out_services.append({
                **s,
                "pct_label": _pct(s["covered"], s["total"]),
                "get_pct_label": _pct(s["gcov"], s["gtot"]),
                "write_pct_label": _pct(s["wcov"], s["wtot"]),
                "defect_red": defect_red, "defect_yellow": defect_yellow,
                "endpoint_rows": _endpoint_rows(s, conf_by_endpoint, status_map),
            })
        # 발행본과 동일 정렬: 커버리지 오름차순(백로그 우선) — per_service()가
        # 이미 (category, pct, service)로 정렬해 두지만, 목록 화면 계약(§2.2)은
        # "정렬 기본 = 커버리지 오름차순" 단독 기준이라 카테고리 우선순위를 뺀
        # 순수 pct 오름차순으로 다시 정렬한다.
        out_services.sort(key=lambda s: (s["covered"] / (s["total"] or 1), s["service"]))

        updated = status_doc.get("updated") or verified_doc.get("updated")
        return {"services": out_services, "updated": updated}
    except Exception:
        return None


def get_services_data() -> dict | None:
    """모듈 레벨 캐시 — 발행 식별자(dashboard-data HEAD sha) 기준. 요청마다
    재계산하지 않는다 (계약 요구사항)."""
    from controlplane.v2 import published  # 지연 import: 순환 임포트 회피
    sha = published._dd_sha()
    now = time.time()
    if (_cache["data"] is not None and _cache["sha"] == sha
            and (now - _cache["at"]) < _CACHE_TTL):
        return _cache["data"]
    data = _build()
    _cache.update(sha=sha, at=now, data=data)
    return data


def get_service(svc_slug: str) -> dict | None:
    data = get_services_data()
    if not data:
        return None
    for s in data["services"]:
        if s["slug"] == svc_slug:
            return s
    return None
