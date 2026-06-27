"""자원 task 모델 헬퍼 (R2b) — 로더 + 폼→yaml 라이터.

docs/RESOURCE-MODEL-PLAN.md §1 스키마 / §5 계약 기준:

  C1  모델 파일: knowledge/formal/resources/<category>__<service>.yaml,
      그룹 정의는 같은 디렉토리의 _groups.yaml. 로더는 디렉토리 전체 merge.
  C5  provenance ∈ {VALIDATED, docs}.

로더는 디렉토리 부재를 빈 모델로 처리한다 (R1이 아직 머지 전이어도 페이지가
뜬다). 라이터는 폼이 만든 §1 구조를 해당 노드의 파일에 합쳐 넣고, 저장은
authoring.propose_edit(검증 → 원자적 쓰기 → 로컬 git 커밋) 파이프라인을
그대로 탄다 — 단, 현 시점 knowledge/formal validator는 resources 레이어를
모를 수 있으므로(R1 병렬) 이 모듈이 §1/C1 레이아웃 검사를 로컬로 먼저
수행하고, 전용 validator 부재 시 "전체 검증은 R1 머지 후" 경고를 덧붙인다.

테스트 주입: 환경변수 PLATFORM_RESOURCES_ROOT가 repo 루트를 대체한다
(그 아래 knowledge/formal/resources/ + drafts/를 쓴다).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

from controlplane import authoring

RESOURCES_REL = "knowledge/formal/resources"

NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+/\S+$")
DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

OPTION_TYPES = ("cidr", "enum", "ref", "string")
PROVENANCE = ("VALIDATED", "docs")

# 폼이 관리하는 §1 키 — 저장 시 이 키들만 교체하고 나머지(source, notes 등
# 폼 밖의 지식)는 기존 정의에서 그대로 보존한다.
MANAGED_KEYS = ("code", "service", "group", "requires", "create",
                "capture", "ready", "delete", "quota", "provenance", "verify")

# verify 스텝에서 폼이 직접 편집하는 키 (나머지 json/임의 키는 hidden으로 보존).
VERIFY_FORM_KEYS = ("name", "endpoint", "expect_status", "note")


# --- 위치 ---------------------------------------------------------------------------

def resources_root() -> Path:
    """repo 루트 — PLATFORM_RESOURCES_ROOT로 테스트가 통째로 바꿔치기한다."""
    override = os.environ.get("PLATFORM_RESOURCES_ROOT", "").strip()
    return Path(override).resolve() if override else authoring.ROOT


def resources_dir() -> Path:
    return resources_root() / RESOURCES_REL


def drafts_dir() -> Path:
    return resources_root() / "drafts"


def node_filename(service: str) -> str:
    """service 필드 -> C1 파일명 (networking/vpc -> networking__vpc.yaml)."""
    if not SERVICE_RE.match(service or ""):
        raise ValueError(
            f"service {service!r}: 'category/service' 형식(소문자·숫자·하이픈)이어야 합니다")
    return service.replace("/", "__") + ".yaml"


# --- 로더 (C1: 디렉토리 전체 merge, 부재 -> 빈 모델) -----------------------------------

def load_model(dir: Path | None = None, with_sources: bool = False):
    """{node_id: task} — *.yaml 전부 merge (_*.yaml 제외). 중복 id는 나중
    파일이 이긴다(파일명 정렬 순). with_sources=True면 (model, {id: 파일명})."""
    d = Path(dir) if dir else resources_dir()
    model: dict[str, dict] = {}
    sources: dict[str, str] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (yaml.YAMLError, OSError):
                continue  # 깨진 파일은 건너뛴다 — 편집기는 best-effort 표시
            if not isinstance(doc, dict):
                continue
            for nid, node in (doc.get("resources") or {}).items():
                if isinstance(node, dict):
                    model[str(nid)] = node
                    sources[str(nid)] = path.name
    return (model, sources) if with_sources else model


def load_groups(dir: Path | None = None) -> dict:
    """_groups.yaml -> {"nw-vpc": {label, category}} (부재/깨짐 -> {})."""
    d = Path(dir) if dir else resources_dir()
    try:
        doc = yaml.safe_load((d / "_groups.yaml").read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    groups = doc.get("groups") if isinstance(doc, dict) else None
    return {str(k): (v or {}) for k, v in groups.items()} if isinstance(groups, dict) else {}


def group_of(node_id: str, node: dict) -> str:
    """그룹 키 — 노드의 group 필드 우선, 없으면 code 접두(nw-vpc-subnet -> nw-vpc)."""
    g = str(node.get("group") or "").strip()
    if g:
        return g
    code = str(node.get("code") or "").strip()
    parts = code.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:2])
    return code or "(미분류)"


# --- 폼 표현 <-> §1 구조 ---------------------------------------------------------------

def requires_rows(node: dict) -> list[dict]:
    """§1 requires -> 폼 행 [{type: ref|count|one_of, target, count}]."""
    rows = []
    for r in node.get("requires") or []:
        if isinstance(r, str):
            rows.append({"type": "ref", "target": r, "count": ""})
        elif isinstance(r, dict) and "one_of" in r:
            items = []
            for alt in r.get("one_of") or []:
                if isinstance(alt, dict):
                    use = alt.get("use")
                    items.append(f"{alt.get('ref', '')}:{use}" if use
                                 else str(alt.get("ref", "")))
                else:
                    items.append(str(alt))
            rows.append({"type": "one_of", "target": ", ".join(items), "count": ""})
        elif isinstance(r, dict) and "ref" in r:
            rows.append({"type": "count", "target": str(r["ref"]),
                         "count": str(r.get("count", 2))})
    return rows


def options_rows(node: dict) -> list[dict]:
    """create.options -> 폼 행 (이름·타입·required·vary·기본값·enum·ref 대상…)."""
    rows = []
    opts = ((node.get("create") or {}).get("options")) or {}
    for name, o in opts.items():
        o = o if isinstance(o, dict) else {}
        rows.append({
            "name": str(name),
            "type": str(o.get("type") or "string"),
            "required": bool(o.get("required")),
            "vary": bool(o.get("vary")),
            "default": "" if o.get("default") is None else str(o.get("default")),
            # ("values"는 Jinja에서 dict.values 메서드와 충돌 — 다른 키로)
            "enum_values": ", ".join(str(v) for v in (o.get("values") or [])),
            "target": str(o.get("target") or ""),
            "pick": str(o.get("pick") or ""),
            "of": str(o.get("of") or ""),
            "note": str(o.get("note") or ""),
        })
    return rows


def body_text(node: dict) -> str:
    body = (node.get("create") or {}).get("body")
    return json.dumps(body, indent=2, ensure_ascii=False) if body else ""


def capture_text(node: dict) -> str:
    cap = node.get("capture") or {}
    return yaml.safe_dump(cap, sort_keys=False, allow_unicode=True,
                          default_flow_style=False).strip() if cap else ""


def _expect_text(expect) -> str:
    """expect_status (int | list[int]) -> 폼 표시용 콤마 문자열."""
    if isinstance(expect, (list, tuple)):
        return ", ".join(str(v) for v in expect)
    return "" if expect is None else str(expect)


def verify_rows(node: dict) -> list[dict]:
    """§1 verify -> 폼 행 [{name, endpoint, expect, note, extra}].

    폼은 name/endpoint/expect_status/note 만 직접 편집한다. 그 외 키(json 본문
    등 PUT/POST 검증 스텝의 페이로드)는 'extra'에 JSON으로 담아 hidden으로 실어
    저장 시 그대로 복원한다 — round-trip에서 본문이 유실되지 않게."""
    rows = []
    for v in node.get("verify") or []:
        if not isinstance(v, dict):
            continue
        extra = {k: val for k, val in v.items() if k not in VERIFY_FORM_KEYS}
        rows.append({
            "name": str(v.get("name") or ""),
            "endpoint": str(v.get("endpoint") or ""),
            "expect": _expect_text(v.get("expect_status")),
            "note": str(v.get("note") or ""),
            "extra": json.dumps(extra, ensure_ascii=False) if extra else "",
        })
    return rows


def _vals(form, key: str) -> list[str]:
    return [str(v) for v in form.getlist(key)]


def _yes(v: str) -> bool:
    return str(v).strip().lower() in ("yes", "true", "on", "1")


def parse_form(form) -> tuple[dict, list[str]]:
    """폼(FormData) -> §1 노드 정의 + 오류 목록 (서버 측 단일 파서).

    필드 계약 (templates/resource_form.html과 1:1):
      code · service · group · quota · provenance(hidden)
      req_type[] req_target[] req_count[]  — one_of target은 콤마 구분,
                                             항목 'id' 또는 'id:use'
      create_endpoint · create_body(JSON)
      opt_name[] opt_type[] opt_required[] opt_vary[] opt_default[]
      opt_enum[] opt_target[] opt_pick[] opt_of[] opt_note[]
      verify_name[] verify_endpoint[] verify_expect[] verify_note[]
                                              verify_extra[](hidden JSON, 보존용)
      capture(YAML 매핑) · ready_field/ready_until/ready_timeout
      delete_endpoint · delete_destructive
    """
    errors: list[str] = []
    node: dict = {}

    def get(key: str) -> str:
        v = form.get(key)
        return str(v).strip() if v is not None else ""

    code = get("code")
    if code:
        node["code"] = code
    service = get("service")
    if not SERVICE_RE.match(service):
        errors.append(f"service {service!r}: 'category/service' 형식이어야 합니다 "
                      "(예: networking/vpc)")
    node["service"] = service
    group = get("group")
    if group:
        node["group"] = group

    # 전제조건 행 — type 선택(ref/one_of/count) + 대상
    requires: list = []
    types, targets = _vals(form, "req_type"), _vals(form, "req_target")
    counts = _vals(form, "req_count")
    for i, raw in enumerate(targets):
        target = raw.strip()
        if not target:
            continue  # 빈 행(추가용)은 무시
        rtype = (types[i].strip() if i < len(types) else "") or "ref"
        if rtype == "one_of":
            alts: list = []
            for item in re.split(r"[,\s]+", target):
                if not item:
                    continue
                if ":" in item:
                    rid, use = item.split(":", 1)
                    alts.append({"ref": rid.strip(), "use": use.strip()})
                else:
                    alts.append(item)
            if len(alts) < 2:
                errors.append(f"requires one_of {target!r}: 대안이 2개 이상 필요합니다")
            requires.append({"one_of": alts})
        elif rtype == "count":
            n_raw = counts[i].strip() if i < len(counts) else ""
            try:
                n = int(n_raw or "2")
            except ValueError:
                n, _ = 2, errors.append(f"requires {target!r}: count {n_raw!r}는 정수여야 합니다")
            if n < 1:
                errors.append(f"requires {target!r}: count는 1 이상이어야 합니다")
            requires.append({"ref": target, "count": n})
        else:
            requires.append(target)
    node["requires"] = requires

    # create — endpoint + 검증된 body 템플릿(JSON) + 옵션
    create: dict = {}
    endpoint = get("create_endpoint")
    if endpoint:
        if not ENDPOINT_RE.match(endpoint):
            errors.append(f"create.endpoint {endpoint!r}: 'METHOD /path' 형식이어야 합니다")
        create["endpoint"] = endpoint
    raw_body = str(form.get("create_body") or "").strip()
    if raw_body:
        try:
            create["body"] = json.loads(raw_body)
        except ValueError as exc:
            errors.append(f"body 템플릿 JSON 파싱 실패: {exc}")

    options: dict = {}
    names = _vals(form, "opt_name")
    o_type, o_req, o_vary = (_vals(form, "opt_type"), _vals(form, "opt_required"),
                             _vals(form, "opt_vary"))
    o_def, o_enum, o_tgt = (_vals(form, "opt_default"), _vals(form, "opt_enum"),
                            _vals(form, "opt_target"))
    o_pick, o_of, o_note = (_vals(form, "opt_pick"), _vals(form, "opt_of"),
                            _vals(form, "opt_note"))

    def col(vals: list[str], i: int) -> str:
        return vals[i].strip() if i < len(vals) else ""

    for i, raw in enumerate(names):
        name = raw.strip()
        if not name:
            continue
        otype = col(o_type, i) or "string"
        if otype not in OPTION_TYPES:
            errors.append(f"옵션 {name!r}: 타입은 {'|'.join(OPTION_TYPES)} 중 하나여야 합니다")
        o: dict = {"type": otype, "required": _yes(col(o_req, i))}
        if _yes(col(o_vary, i)):
            o["vary"] = True
        if col(o_def, i):
            o["default"] = col(o_def, i)
        if otype == "enum":
            values = [v for v in re.split(r"[,\s]+", col(o_enum, i)) if v]
            if not values:
                errors.append(f"옵션 {name!r}: enum 타입은 값 목록이 필요합니다")
            else:
                o["values"] = values
        if otype == "ref":
            if col(o_tgt, i):
                o["target"] = col(o_tgt, i)
            else:
                errors.append(f"옵션 {name!r}: ref 타입은 대상 노드(target)가 필요합니다")
        for key, vals in (("pick", o_pick), ("of", o_of), ("note", o_note)):
            if col(vals, i):
                o[key] = col(vals, i)
        options[name] = o
    if options:
        create["options"] = options
    if create:
        node["create"] = create

    # verify — GET-커버리지 스텝 행 (endpoint + expect_status, name/note 선택).
    # extra(hidden JSON)는 폼이 직접 편집하지 않는 json 본문 등을 보존한다.
    verify: list = []
    v_name, v_ep = _vals(form, "verify_name"), _vals(form, "verify_endpoint")
    v_expect, v_note = _vals(form, "verify_expect"), _vals(form, "verify_note")
    v_extra = _vals(form, "verify_extra")

    def vcol(vals: list[str], i: int) -> str:
        return vals[i].strip() if i < len(vals) else ""

    for i, raw in enumerate(v_ep):
        endpoint = raw.strip()
        if not endpoint:
            continue  # 빈 행(추가용)은 무시
        if not ENDPOINT_RE.match(endpoint):
            errors.append(f"verify.endpoint {endpoint!r}: 'METHOD /path' 형식이어야 합니다")
        step: dict = {}
        # 보존된 extra(json 본문 등)를 먼저 깔고 폼 필드로 덮어쓴다.
        raw_extra = vcol(v_extra, i)
        if raw_extra:
            try:
                ex = json.loads(raw_extra)
                if isinstance(ex, dict):
                    step.update(ex)
            except ValueError:
                pass  # 손상된 hidden 값은 무시 (폼 필드가 진실)
        if vcol(v_name, i):
            step["name"] = vcol(v_name, i)
        step["endpoint"] = endpoint
        expect_raw = vcol(v_expect, i)
        codes = [c for c in re.split(r"[,\s]+", expect_raw) if c]
        nums: list[int] = []
        for c in codes:
            try:
                nums.append(int(c))
            except ValueError:
                errors.append(f"verify {endpoint!r}: expect_status {c!r}는 정수여야 합니다")
        if nums:
            step["expect_status"] = nums[0] if len(nums) == 1 else nums
        if vcol(v_note, i):
            step["note"] = vcol(v_note, i)
        verify.append(step)
    if verify:
        node["verify"] = verify

    # capture / ready / delete / quota / provenance
    raw_cap = str(form.get("capture") or "").strip()
    if raw_cap:
        try:
            cap = yaml.safe_load(raw_cap)
        except yaml.YAMLError as exc:
            cap, _ = None, errors.append(f"capture YAML 파싱 실패: {exc}")
        if cap is not None:
            if isinstance(cap, dict):
                node["capture"] = cap
            else:
                errors.append("capture는 '변수: $.jsonpath' 매핑이어야 합니다")

    ready_field = get("ready_field")
    if ready_field:
        ready: dict = {"field": ready_field}
        if get("ready_until"):
            ready["until"] = get("ready_until")
        if get("ready_timeout"):
            try:
                ready["timeout"] = int(get("ready_timeout"))
            except ValueError:
                errors.append(f"ready.timeout {get('ready_timeout')!r}는 정수여야 합니다")
        node["ready"] = ready

    del_endpoint = get("delete_endpoint")
    if del_endpoint:
        if not ENDPOINT_RE.match(del_endpoint):
            errors.append(f"delete.endpoint {del_endpoint!r}: 'METHOD /path' 형식이어야 합니다")
        node["delete"] = {"endpoint": del_endpoint,
                          "destructive": _yes(get("delete_destructive") or "yes")}

    if get("quota"):
        node["quota"] = get("quota")
    provenance = get("provenance") or "docs"
    if provenance not in PROVENANCE:
        errors.append(f"provenance는 {'/'.join(PROVENANCE)} 중 하나여야 합니다 (C5)")
    node["provenance"] = provenance
    return node, errors


# --- 로컬 §1/C1 검사 (resources 전용 validator는 R1 산출 — 머지 전 degrade) -------------

def layout_errors(node_id: str, node: dict,
                  model: dict | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) — yaml 파싱 + §1 모양 검사. 그래프 차원의 미지 참조는
    경고만 (대상 노드가 다른 파일/나중 저장으로 올 수 있다)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not NODE_ID_RE.match(node_id or ""):
        errors.append(f"노드 id {node_id!r}: 소문자/숫자/하이픈만 허용됩니다")
    if not SERVICE_RE.match(str(node.get("service") or "")):
        errors.append("service는 'category/service' 형식의 필수 필드입니다")
    if node.get("provenance") not in PROVENANCE:
        errors.append(f"provenance는 {'/'.join(PROVENANCE)} 중 하나여야 합니다 (C5)")

    known = set(model or {})
    known.add(node_id)

    def check_target(t: str, where: str) -> None:
        if not NODE_ID_RE.match(t or ""):
            errors.append(f"{where}: 대상 노드 id {t!r}가 유효하지 않습니다")
        elif model is not None and t not in known:
            warnings.append(f"{where}: {t!r}는 아직 모델에 없는 노드입니다 "
                            "(다른 파일/나중 정의로 채워질 수 있음)")

    for r in node.get("requires") or []:
        if isinstance(r, str):
            check_target(r, "requires")
        elif isinstance(r, dict) and "one_of" in r:
            for alt in r.get("one_of") or []:
                check_target(alt.get("ref", "") if isinstance(alt, dict) else str(alt),
                             "requires.one_of")
        elif isinstance(r, dict) and "ref" in r:
            check_target(str(r["ref"]), "requires")
            if not isinstance(r.get("count"), int) or r["count"] < 1:
                errors.append(f"requires {r.get('ref')!r}: count는 1 이상의 정수여야 합니다")
        else:
            errors.append(f"requires 항목 모양이 §1 스키마와 다릅니다: {r!r}")

    for name, o in (((node.get("create") or {}).get("options")) or {}).items():
        if not isinstance(o, dict) or o.get("type") not in OPTION_TYPES:
            errors.append(f"옵션 {name!r}: 타입은 {'|'.join(OPTION_TYPES)} 중 하나여야 합니다")
            continue
        if o["type"] == "ref":
            check_target(str(o.get("target") or ""), f"options.{name}")
        if o["type"] == "enum" and not o.get("values"):
            errors.append(f"옵션 {name!r}: enum 타입은 values 목록이 필요합니다")
    return errors, warnings


def validator_knows_resources() -> bool:
    """knowledge/formal/validate.py가 resources 레이어를 검사하는가 —
    아직이면(R1 머지 전) 저장 결과에 '전체 검증 보류' 경고를 덧붙인다."""
    try:
        src = (authoring.ROOT / "knowledge" / "formal" / "validate.py"
               ).read_text(encoding="utf-8")
    except OSError:
        return False
    return ("formal/resources" in src or "resources/*.yaml" in src
            or 'HERE / "resources"' in src)


# --- 라이터 — 폼 정의 1개를 파일에 합쳐 authoring 파이프라인으로 저장 --------------------

def save_node(node_id: str, node: dict, *, validate_only: bool = False) -> dict:
    """§1 노드 정의를 소속 파일에 round-trip — 반환은 authoring.propose_edit
    형태({ok, errors, warnings, rel, commit, pushed}) + file 키.

    파일 선택: 이미 모델에 있는 노드는 그 파일, 신규 노드는 service 필드에서
    유도한 <category>__<service>.yaml (파일이 없으면 새로 만든다). 저장 경로는
    knowledge/formal/resources/ 밖으로 절대 나가지 않는다.
    """
    root = resources_root()
    model, sources = load_model(with_sources=True)
    errors, warnings = layout_errors(node_id, node, model)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                "rel": "", "commit": "", "pushed": False, "file": ""}

    try:
        fname = sources.get(node_id) or node_filename(node["service"])
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": warnings,
                "rel": "", "commit": "", "pushed": False, "file": ""}
    rel = f"{RESOURCES_REL}/{fname}"

    # 경로 게이트 — 항상 resources 디렉토리 안 (파일명에 구분자 불가)
    target = (root / rel).resolve()
    try:
        target.relative_to(resources_dir().resolve())
    except ValueError:
        return {"ok": False, "errors": [f"{rel!r}: knowledge/formal/resources/ "
                                        "밖으로는 저장할 수 없습니다"],
                "warnings": warnings, "rel": "", "commit": "", "pushed": False,
                "file": ""}

    doc: dict = {"version": 1, "resources": {}}
    if target.exists():
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                doc = loaded
        except yaml.YAMLError as exc:
            return {"ok": False, "errors": [f"{rel}: 기존 파일 YAML 파싱 실패 — {exc}"],
                    "warnings": warnings, "rel": "", "commit": "", "pushed": False,
                    "file": rel}
    doc.setdefault("version", 1)
    doc.setdefault("resources", {})

    # 폼 밖의 키(verify, notes, …)는 기존 정의에서 보존
    existing = doc["resources"].get(node_id) or {}
    preserved = {k: v for k, v in existing.items() if k not in MANAGED_KEYS}
    doc["resources"][node_id] = {**preserved, **node}

    content = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                             default_flow_style=False, width=100)
    result = authoring.propose_edit(rel, content, validate_only=validate_only,
                                    root=root)
    result.setdefault("warnings", [])
    result["warnings"] = warnings + result["warnings"]
    if not validator_knows_resources():
        result["warnings"].append(
            "resources 스키마 전용 validator 미탑재 — 로컬 §1/C1 레이아웃 검사만 "
            "수행했습니다 (전체 검증은 R1 validator 머지 후 자동 적용)")
    result["file"] = rel
    return result


# --- 노드 ↔ lifecycle 연계 (enabled/heavy 토글) -----------------------------------------
#
# 노드는 source.lifecycle 로 lifecycle id 를 가리킨다. lifecycle 본체는 두 곳에
# 흩어져 있다(loader.load_lifecycles 가 병합): 공유 scenarios.json(병렬 캠페인이
# 공유 — 절대 쓰지 않는다) 과 lifecycles/<category>__<service>.json 프래그먼트
# (서비스당 1파일 — 이쪽만 안전하게 쓴다). 그래서 lifecycle_info 는 enabled/heavy
# 와 함께 writable(=프래그먼트 거주 여부) 를 돌려주고, set_lifecycle_flags 는
# 프래그먼트가 아니면 거부한다.

def _lifecycle_id_of(node: dict) -> str:
    return str(((node.get("source") or {}).get("lifecycle")) or "").strip()


def lifecycle_info(node: dict) -> dict:
    """노드의 연계 lifecycle 상태 — 폼의 lifecycle 섹션이 쓰는 단일 소스.

    반환: {linked, id, found, enabled, heavy, source, writable, reason}.
      · linked   — 노드에 source.lifecycle 이 있는가
      · found    — 그 id 가 실제 lifecycle 로 해석되는가
      · writable — 프래그먼트(lifecycles/*.json)에 있어 토글을 저장할 수 있는가
                   (공유 scenarios.json 거주분은 읽기 전용)
      · reason   — writable=False 일 때 사람이 읽을 사유
    로더가 없거나 깨져도(병렬 머지 전) 페이지가 살아있게 best-effort 로 degrade."""
    info = {"linked": False, "id": "", "found": False, "enabled": None,
            "heavy": None, "source": "", "writable": False, "reason": ""}
    lid = _lifecycle_id_of(node)
    if not lid:
        info["reason"] = "이 노드에는 연계된 lifecycle(source.lifecycle)이 없습니다"
        return info
    info["linked"] = True
    info["id"] = lid
    try:
        from regression.scenarios import loader as _loader
        lcs, src = _loader.load_lifecycles(with_sources=True)
    except Exception as exc:  # 로더 부재/중복 id 등 -> 읽기 전용 degrade
        info["reason"] = f"lifecycle 로더를 읽지 못했습니다: {exc}"
        return info
    entry = next((x for x in lcs if x.get("id") == lid), None)
    if entry is None:
        info["reason"] = f"lifecycle {lid!r}를 찾을 수 없습니다 (병렬 머지 전일 수 있음)"
        return info
    info["found"] = True
    info["enabled"] = bool(entry.get("enabled"))
    info["heavy"] = bool(entry.get("heavy"))
    info["source"] = src.get(lid, "")
    if info["source"] == "scenarios.json":
        info["reason"] = ("공유 scenarios.json 에 정의된 lifecycle 입니다 — 병렬 "
                          "캠페인 충돌 방지를 위해 여기서는 읽기 전용입니다")
    elif info["source"]:
        info["writable"] = True
    else:
        info["reason"] = "lifecycle 출처 파일을 알 수 없습니다"
    return info


def set_lifecycle_flags(node: dict, *, enabled: bool, heavy: bool) -> dict:
    """노드의 연계 lifecycle 프래그먼트에 enabled/heavy 를 안전하게 기록한다.

    프래그먼트(lifecycles/*.json) 거주분만 쓴다 — scenarios.json/미해결은 거부.
    파일의 다른 lifecycle 엔트리와 키 순서는 그대로 두고 해당 엔트리의 두 플래그만
    바꾼 뒤 통째로 다시 직렬화한다. 반환 {ok, changed, errors, file, id}."""
    out = {"ok": False, "changed": False, "errors": [], "file": "", "id": ""}
    info = lifecycle_info(node)
    out["id"] = info["id"]
    out["file"] = info["source"]
    if not info["found"]:
        out["errors"].append(info["reason"] or "연계 lifecycle 을 찾을 수 없습니다")
        return out
    if not info["writable"]:
        out["errors"].append(info["reason"] or
                             "이 lifecycle 은 여기서 저장할 수 없습니다")
        return out

    from regression.scenarios import loader as _loader
    path = _loader.FRAGMENTS_DIR / info["source"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["errors"].append(f"{info['source']}: 읽기/파싱 실패 — {exc}")
        return out
    if not isinstance(doc, dict) or not isinstance(doc.get("lifecycles"), list):
        out["errors"].append(f"{info['source']}: 예상한 {{'lifecycles': [...]}} 구조가 "
                             "아니라 토글을 적용하지 않았습니다")
        return out

    hit = False
    for lc in doc["lifecycles"]:
        if isinstance(lc, dict) and lc.get("id") == info["id"]:
            if lc.get("enabled") != enabled or lc.get("heavy") != heavy:
                out["changed"] = True
            lc["enabled"] = enabled
            lc["heavy"] = heavy
            hit = True
            break
    if not hit:
        out["errors"].append(f"{info['source']}에서 lifecycle {info['id']!r} "
                             "엔트리를 찾지 못했습니다")
        return out
    try:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        out["errors"].append(f"{info['source']}: 쓰기 실패 — {exc}")
        return out
    out["ok"] = True
    return out


# --- 합성 draft (C4: drafts/lifecycle-gen-*.json) ---------------------------------------

def save_lifecycle_draft(lifecycle: dict) -> tuple[str, list[str]]:
    """합성기 산출 lifecycle JSON -> drafts/lifecycle-<id>.json. 자동 enable
    금지(C4) — enabled를 강제로 False로 둔다. (이름, 오류들) 반환."""
    if not isinstance(lifecycle, dict):
        return "", ["합성 결과가 lifecycle JSON(dict)이 아닙니다"]
    lid = str(lifecycle.get("id") or "")
    if not DRAFT_ID_RE.match(lid):
        return "", [f"lifecycle id {lid!r}가 유효하지 않아 draft로 저장할 수 없습니다"]
    lifecycle = {**lifecycle, "enabled": False}
    d = drafts_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        name = f"lifecycle-{lid}.json"
        (d / name).write_text(
            json.dumps(lifecycle, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError as exc:
        return "", [f"draft 쓰기 실패: {exc}"]
    return name, []
