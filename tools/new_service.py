"""신규 서비스 온보딩 스캐폴더 (M6a, docs/M6-DESIGN.md §A.3).

owner/agent가 `--service <cat>/<svc>` 하나만 주면, data/api_catalog.json +
data/api_docs.json 에서 그 서비스의 endpoint/DTO 를 읽어 *컴파일 가능하지만
비활성인* resource-task stub YAML 한 파일을 찍는다. owner 는 quota 키, enum 값,
verify 본문, requires 만 손보면 된다.

모델은 tools/retirement.py — 동일 패턴: data/api_catalog.json 을 인덱싱하고
(_norm 으로 경로 정규화), argparse CLI, `python -m tools.new_service` 진입,
stdout/파일로 산출.

휴리스틱 (docs/M6-DESIGN.md §A.3):
  - 서비스의 모든 catalog 엔트리를 service 로 필터.
  - `POST /v1/<thing>` + 매칭 `DELETE /v1/<thing>/{id}` = 생성 가능 노드(creatable).
  - 매칭 DELETE 없는 collection 패밀리(GET 만) = lookup 노드(create: GET, no delete).
  - 노드 id = <thing> 의 단수형, code = "<abbrev>-<svc>-<resource>"
    (abbrev 는 _groups.yaml 의 category 약어).
  - body 스켈레톤은 create 요청 DTO 의 required 필드에서 placeholder 값으로 생성.
  - capture 는 {<resource>_id: "$.id"} 추측 (# TODO verify envelope).
  - 모든 노드 provenance: docs (미증명). owner 가 라이브 2xx 확인 후 VALIDATED 승급.

사용:
  python -m tools.new_service --service application-service/queueservice
  python -m tools.new_service --service compute/scf --stdout
  python -m tools.new_service --service database/cachestore \\
      --out knowledge/formal/resources/database__cachestore.yaml
"""
import argparse
import json
from pathlib import Path

_ROOT = Path(__file__).parent.parent

# Category abbreviations — fixed (resources/_groups.yaml header comment). We
# read _groups.yaml at runtime to recover them, falling back to this map.
_FALLBACK_ABBREV = {
    "networking": "nw", "compute": "cp", "storage": "st", "database": "db",
    "security": "sec", "management": "mg", "container": "ct", "ai-ml": "ai",
    "data-analytics": "da", "application-service": "ap", "devops-tools": "dt",
    "financial-management": "fm", "platform": "pf",
}


def _norm(p):
    """retirement._norm 와 동일 — {id} 세그먼트를 '*' 로, 쿼리 제거."""
    return "/".join("*" if "{" in s else s
                    for s in (p or "").split("?")[0].strip("/").split("/"))


def _abbrev_map():
    """resources/_groups.yaml 의 group code 들에서 category->abbrev 를 복원.

    group key 는 '<abbrev>-<grp>' 이고 각 group 은 category 를 가진다.
    카테고리당 abbrev 는 일관되므로 첫 매핑을 채택. 못 찾으면 fallback."""
    abbrev = dict(_FALLBACK_ABBREV)
    gpath = _ROOT / "knowledge" / "formal" / "resources" / "_groups.yaml"
    try:
        import yaml
        gdata = yaml.safe_load(gpath.read_text(encoding="utf-8")) or {}
        for gid, g in (gdata.get("groups") or {}).items():
            cat = (g or {}).get("category")
            ab = str(gid).split("-")[0]
            if cat and ab:
                abbrev.setdefault(cat, ab)
    except Exception:
        pass
    return abbrev


def _singular(word):
    """collection 세그먼트의 단수형(노드 id 용). 'queues'->'queue',
    'cloud-functions'->'cloud-function', 'policies'->'policy'."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _path_id_param(path):
    """경로의 마지막 {...} 파라미터명을 반환 (delete 경로의 id 토큰).
    'DELETE /v1/cloud-functions/{cloud_function_id}' -> 'cloud_function_id'.
    없으면 None."""
    import re
    if not path:
        return None
    found = re.findall(r"\{([A-Za-z0-9_]+)\}", path)
    return found[-1] if found else None


def _catalog_for(service):
    """그 service 의 catalog 엔트리만 (retirement._catalog_index 와 같은 소스)."""
    cat = json.loads((_ROOT / "data" / "api_catalog.json").read_text("utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat \
        else cat
    return [e for e in eps
            if f"{e['category']}/{e['service']}" == service]


def _docs():
    d = json.loads((_ROOT / "data" / "api_docs.json").read_text("utf-8"))
    return d.get("endpoints") or {}, d.get("models") or {}


def _body_ref(ep_doc):
    """endpoint doc 의 in==body 파라미터에서 schema_ref(또는 schema)를 반환."""
    for p in (ep_doc or {}).get("parameters") or []:
        if p.get("in") == "body":
            return p.get("schema_ref") or (p.get("schema") or "").lower() or None
    return None


def _lookup_model(models, category, service, ref):
    """models[<cat>/<svc>/<lower(ref)>] 우선, 없으면 ref 가 이미 완전키인지 확인."""
    if not ref:
        return None
    ref_l = str(ref).lower()
    for key in (f"{category}/{service}/{ref_l}", ref_l):
        if key in models:
            return models[key]
    return None


def _resp_ref(ep_doc):
    """endpoint doc 의 2xx 응답에서 schema_ref(없으면 schema)를 반환."""
    for r in (ep_doc or {}).get("responses") or []:
        if str(r.get("code", "")).startswith("2"):
            return r.get("schema_ref") or (r.get("schema") or "").lower() or None
    return None


def _envelope_hint(post_doc, models, category, service):
    """create 응답 모델을 보고 capture 봉투를 추정 (IB-026/028).

    반환 (suggested_jsonpath_or_None, human_hint_or_None):
      - flat (top-level 'id' 필드)         -> (None, None)  현재 $.id 가 맞음
      - LIST/nested ('contents'/'<plural>' array[...]) -> ("$.<field>[0].id",
        "docs response shows {<field>:[{...}]}")
      - single-wrapped (단일 object 필드)  -> ("$.<field>.id",
        "docs response wraps {<field>:{...}}")
      - 모델 없음                          -> (None, None)  일반 TODO 만
    """
    ref = _resp_ref(post_doc)
    short = service.split("/")[-1]
    model = _lookup_model(models, category, short, ref)
    if not model:
        return None, None
    fields = model.get("fields") or []

    def _id_like(name):
        n = str(name or "")
        return n == "id" or n.lower().endswith("id")

    # A top-level id-like scalar => flat/detail envelope ($.<that> is correct).
    if any(_id_like(f.get("name"))
           and "array" not in str(f.get("schema") or "").lower()
           for f in fields):
        return None, None
    # array field => nested list envelope (contents[], <plural>[], ...).
    # Prefer an array whose element is a defined sub-model (schema_ref) over a
    # bare array[object] (e.g. 'links') — the modelled list is the payload.
    arrays = [f for f in fields
              if str(f.get("name") or "")
              and ("array" in str(f.get("schema") or "").lower()
                   or str(f.get("schema") or "").lower().startswith("["))]
    if arrays:
        modelled = [f for f in arrays if f.get("schema_ref")]
        chosen = (modelled or arrays)[0]
        fname = str(chosen.get("name"))
        return (f"$.{fname}[0].id",
                f"docs response shows {{{fname}:[{{id}}]}}")
    # exactly one object field that references a sub-model => single wrap
    obj_fields = [f for f in fields
                  if f.get("schema_ref")
                  and "array" not in str(f.get("schema") or "").lower()]
    if len(obj_fields) == 1:
        fname = str(obj_fields[0].get("name") or "")
        if fname:
            return (f"$.{fname}.id",
                    f"docs response wraps {{{fname}:{{id}}}}")
    return None, None


def _placeholder(field):
    """required 필드의 placeholder 값. 타입 힌트(schema) 로 추정.

    문자열은 {unique}(엔진 빌트인) 으로 — validator 의 'no dangling placeholder'
    규칙을 통과한다. name 류는 prefix+{unique}."""
    schema = (field.get("schema") or "").lower()
    name = (field.get("name") or "").lower()
    if "bool" in schema:
        return False
    if any(t in schema for t in ("int", "number", "float")):
        return 0
    if "array" in schema or "list" in schema or schema.startswith("["):
        return []
    if "object" in schema or schema.startswith("{"):
        return {}
    if "name" in name:
        return "regr{unique}"
    return "{unique}"


def build_node(family, eps_by_key, models, category, service, abbrev):
    """create POST 패밀리 하나 -> resource-task 노드 dict.

    family = {"thing","post_key","create_path","delete_path"}
    """
    thing = family["thing"]
    node_id = _singular(thing)
    short = service.split("/")[-1]
    code = f"{abbrev}-{short}-{node_id}"

    post_doc = eps_by_key.get(family["post_key"], {})
    node = {
        "code": code,
        "service": service,
        "requires": [],  # owner fills in; TODO comment added at emit time
        "create": {"endpoint": f"POST {family['create_path']}"},
    }

    # ---- body 스켈레톤 (required 필드만) --------------------------------
    body_ref = _body_ref(post_doc)
    model = _lookup_model(models, category, short, body_ref)
    if model:
        body = {}
        for f in model.get("fields") or []:
            if f.get("required"):
                body[f["name"]] = _placeholder(f)
        if body:
            node["create"]["body"] = body

    # ---- capture 추측 -----------------------------------------------------
    # capture 변수명은 teardown(delete) 경로의 {...} 파라미터와 *일치*해야 한다
    # (validator: delete 경로 토큰은 own capture 로 해소). delete 가 있으면 그
    # 파라미터명을, 없으면 <node>_id 를 쓴다.
    cap_var = _path_id_param(family.get("delete_path")) or f"{node_id}_id"
    node["capture"] = {cap_var: "$.id"}
    # envelope-relative hint (IB-026/028): docs response may wrap the id
    suggested, hint = _envelope_hint(post_doc, models, category, service)
    if hint:
        node["_envelope_hint"] = hint
        node["_envelope_suggest"] = suggested

    # ---- delete (있으면) --------------------------------------------------
    if family.get("delete_path"):
        node["delete"] = {
            "endpoint": f"DELETE {family['delete_path']}",
            "destructive": True,
        }
    return node


def _existing_node_ids(self_file):
    """다른 resources/*.yaml 들에 이미 정의된 노드 id 집합. composer.load_model 은
    노드 id 전역 유일을 요구하므로(validator 중복 거부), 충돌하는 id 는
    서비스명으로 네임스페이스한다. self_file 은 제외(덮어쓰기 케이스)."""
    out = set()
    rdir = _ROOT / "knowledge" / "formal" / "resources"
    if not rdir.is_dir():
        return out
    try:
        import yaml
    except Exception:
        return out
    for p in rdir.glob("*.yaml"):
        if p.name.startswith("_") or p.resolve() == Path(self_file).resolve():
            continue
        try:
            data = yaml.safe_load(p.read_text("utf-8")) or {}
            out |= set((data.get("resources") or {}).keys())
        except Exception:
            continue
    return out


def scaffold(service, _out_file=None):
    """서비스 -> {node_id: node-dict} (출력 직전 형태).

    _out_file: 충돌 검사에서 제외할 대상 파일(같은 서비스를 덮어쓰는 경우)."""
    parts = service.split("/")
    if len(parts) != 2 or not all(parts):
        raise SystemExit(
            f"--service must be '<category>/<service>' (got {service!r})")
    category, svc = parts
    entries = _catalog_for(service)
    if not entries:
        raise SystemExit(f"no catalog endpoints for service {service!r}")
    eps_doc, models = _docs()
    abbrev = _abbrev_map().get(category, category[:2])
    default_out = (_ROOT / "knowledge" / "formal" / "resources"
                   / f"{category}__{svc}.yaml")
    taken = _existing_node_ids(_out_file or default_out)

    def _uid(node_id):
        """전역 유일 노드 id — 충돌하면 서비스 short 로 네임스페이스."""
        if node_id not in taken:
            return node_id
        alt = f"{svc}-{node_id}"
        return alt if alt not in taken else f"{svc}-{node_id}-x"

    # endpoint 를 (method, normalized-path) 로 인덱싱
    by_norm = {}
    for e in entries:
        by_norm.setdefault((e["method"].upper(), _norm(e["http_path"])), e)

    nodes = {}
    seen_things = set()

    # 1) creatable: POST /v1/<thing> (collection, {id} 없음)
    for e in sorted(entries, key=lambda x: (x["http_path"], x["method"])):
        if e["method"].upper() != "POST":
            continue
        norm = _norm(e["http_path"])
        segs = norm.split("/")
        # collection POST = 단일 하위 세그먼트(v1/<thing>), {id} 아님
        if len(segs) != 2 or segs[-1] == "*":
            continue
        thing = segs[-1]
        if thing in seen_things:
            continue
        del_e = by_norm.get(("DELETE", norm + "/*"))
        family = {
            "thing": thing,
            "post_key": e["key"],
            "create_path": e["http_path"],
            "delete_path": del_e["http_path"] if del_e else None,
        }
        nid = _uid(_singular(thing))
        nodes[nid] = build_node(
            family, eps_doc, models, category, service, abbrev)
        taken.add(nid)
        seen_things.add(thing)

    # 2) lookup: GET /v1/<thing> collection 인데 POST collection 이 없는 것
    for e in sorted(entries, key=lambda x: (x["http_path"], x["method"])):
        if e["method"].upper() != "GET":
            continue
        norm = _norm(e["http_path"])
        segs = norm.split("/")
        if len(segs) != 2 or segs[-1] == "*":
            continue
        thing = segs[-1]
        if thing in seen_things or ("POST", norm) in by_norm:
            continue
        base = _singular(thing)
        node_id = _uid(base)
        lk = {
            "code": f"{abbrev}-{svc}-{base}",
            "service": service,
            "requires": [],
            "create": {"endpoint": f"GET {e['http_path']}"},
            "capture": {f"{base}_id": "$.id"},
            "capture_soft": True,  # lookup — id 를 다른 노드에 먹일 수 없음
            "_lookup": True,
        }
        # GET list 응답은 거의 항상 봉투(contents[]/<plural>[]) — 힌트 부착
        suggested, hint = _envelope_hint(e["key"] and eps_doc.get(e["key"]),
                                          models, category, service)
        if hint:
            lk["_envelope_hint"] = hint
            lk["_envelope_suggest"] = suggested
        nodes[node_id] = lk
        taken.add(node_id)
        seen_things.add(thing)

    if not nodes:
        raise SystemExit(
            f"no creatable/lookup resource families found for {service!r} "
            "(no top-level 'POST /v1/<thing>' or 'GET /v1/<thing>')")
    return nodes


# --------------------------------------------------------------------------
# YAML emit — 손으로 그려 주석(# TODO)을 보존한다 (yaml.dump 는 주석 불가).
# --------------------------------------------------------------------------
def _yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v)  # 토큰/특수문자 포함 가능 — 항상 따옴표
    if isinstance(v, list):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_yaml_scalar(x)}"
                               for k, x in v.items()) + "}"
    return json.dumps(v)


def emit_yaml(service, nodes):
    out = [
        f"# Resource-task model — {service} (auto-scaffolded stub).",
        "# Generated by tools/new_service.py (docs/M6-DESIGN.md §A.3).",
        "# owner must verify bodies/captures/requires before promoting "
        "provenance to VALIDATED.",
        "version: 1",
        "resources:",
    ]
    for node_id, node in nodes.items():
        is_lookup = node.get("_lookup", False)
        out.append(f"  {node_id}:")
        out.append(f"    code: {_yaml_scalar(node['code'])}")
        out.append(f"    service: {node['service']}")
        out.append("    requires: []  # TODO owner: declare prerequisites "
                   "(plan() pulls the dependency closure)")
        out.append("    create:")
        out.append(f"      endpoint: {_yaml_scalar(node['create']['endpoint'])}")
        body = node["create"].get("body")
        if body:
            out.append("      body:  # TODO owner: verify required fields + "
                       "placeholder values")
            for k, v in body.items():
                out.append(f"        {k}: {_yaml_scalar(v)}")
        elif not is_lookup:
            out.append("      # TODO owner: create body — DTO had no required "
                       "fields (or no body DTO in docs)")
        hint = node.get("_envelope_hint")
        if hint:
            suggest = node.get("_envelope_suggest")
            tail = f" → may need {suggest}" if suggest else ""
            out.append(f"    capture: {_yaml_scalar(node['capture'])}  "
                       f"# TODO verify envelope — {hint}{tail} (IB-026)")
        else:
            out.append(f"    capture: {_yaml_scalar(node['capture'])}  "
                       "# TODO verify envelope (response root may wrap the id)")
        if node.get("capture_soft"):
            out.append("    capture_soft: true  # lookup node — id not feedable "
                       "to dependents")
        if node.get("delete"):
            out.append(f"    delete: {_yaml_scalar(node['delete'])}")
        out.append("    provenance: docs")
        out.append('    notes: "auto-scaffolded by tools/new_service.py — owner '
                   'must verify bodies/captures/requires"')
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description="Scaffold a starter resource-task YAML for a new service.")
    ap.add_argument("--service", required=True,
                    help="<category>/<service> "
                         "(e.g. application-service/queueservice)")
    ap.add_argument("--out",
                    help="output path (default knowledge/formal/resources/"
                         "<cat>__<svc>.yaml)")
    ap.add_argument("--stdout", action="store_true",
                    help="print to stdout instead of writing a file")
    args = ap.parse_args()

    cat, svc = args.service.split("/") if "/" in args.service else ("", "")
    out = Path(args.out) if args.out else (
        _ROOT / "knowledge" / "formal" / "resources" / f"{cat}__{svc}.yaml")

    # the target file is excluded from the global node-id collision check, so
    # overwriting a service's own file does not namespace its nodes.
    nodes = scaffold(args.service, _out_file=out)
    text = emit_yaml(args.service, nodes)

    if args.stdout:
        print(text, end="")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} — {len(nodes)} node(s) for {args.service}")


if __name__ == "__main__":
    main()
