"""Catalog enrichment (design (A)+(B), 2026-06-18) — per-endpoint param metadata.

`python -m spec.enrich_catalog` derives, for every catalog endpoint, structured
**path-param** and **query-param** metadata and writes the sidecar
``data/api_catalog_params.json`` (the base ``api_catalog.json`` schema is left
untouched — purely additive). This is the data foundation that lets the platform
match a read (GET) to the resource that PRODUCES its ids by identity rather than
by capture-var string name, retiring ``engine._PARAM_ALIASES`` and making the
create→조회(show) chain catalog-native.

Shape written per endpoint key::

    {
      "<endpoint_key>": {
        "path_params": [
          {"name": "usage_plan_id", "resource_type": "apigateway/usage-plan",
           "role": "self", "produced_by": ".../createusageplan",
           "capture": "$.usage_plan.id"}],
        "query_params": [{"name": "stage_name", "required": true}]
      }, ...
    }

Derivation (all mechanical — no per-service hand-mapping):
  * path-param NAMES + query-params come from ``api_docs.json`` parameters
    (``in: path|query`` + ``required``); the catalog key == api_docs key (1:1).
  * ``resource_type`` — the path segment immediately before the ``{param}``,
    singularised, prefixed with the service (``.../usage-plans/{usage_plan_id}``
    → ``apigateway/usage-plan``).
  * ``role`` — the LAST path-param of a path is the resource's own id (``self``);
    earlier ones are ``ancestor``.
  * ``produced_by`` — REST convention: the producer of ``.../things/{X_id}`` is
    the POST to the collection ``.../things``. Looked up among the service's POST
    endpoints by exact (normalised) path. ``null`` when no such POST exists
    (composite/name-addressed-without-create paths — honestly flagged).
  * ``capture`` — refined from the resource model's ``capture`` jsonpath for that
    create endpoint when available, else ``$.id``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.catalog import load_catalog

_DOCS = Path("data/api_docs.json")
_OUT = Path("data/api_catalog_params.json")
_PARAM_RE = re.compile(r"\{([^}]+)\}")


def _norm(path: str) -> str:
    """Collapse templated segments to '*' so two templated paths compare by shape."""
    p = (path or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def _singularize(seg: str) -> str:
    """Best-effort English singular for a collection path segment (label only)."""
    if seg.endswith("ies"):
        return seg[:-3] + "y"          # registries -> registry, repositories -> repository
    if seg.endswith("ses"):
        return seg[:-2]                # tagses -> tagse (SCP's odd plural; label only)
    if seg.endswith("s") and not seg.endswith("ss"):
        return seg[:-1]                # usage-plans -> usage-plan, apis -> api
    return seg


def _collection_prefix(path: str, param: str) -> str | None:
    """The path up to and INCLUDING the segment that owns ``{param}`` minus the
    param itself — i.e. the collection a POST would create into.
    ``/v1/apis/{api_id}/usage-plans/{usage_plan_id}`` , ``usage_plan_id`` ->
    ``/v1/apis/{api_id}/usage-plans``."""
    segs = path.strip("/").split("/")
    tok = "{%s}" % param
    if tok not in segs:
        return None
    i = segs.index(tok)
    return "/" + "/".join(segs[:i])


def _owning_segment(path: str, param: str) -> str | None:
    segs = path.strip("/").split("/")
    tok = "{%s}" % param
    if tok not in segs or segs.index(tok) == 0:
        return None
    return segs[segs.index(tok) - 1]


def _model_capture_index() -> dict:
    """{normalised create path -> first capture jsonpath} from the resource model."""
    out: dict[str, str] = {}
    try:
        from regression.scenarios import composer
        model = composer.load_model()
    except Exception:
        return out
    for task in model.values():
        if not isinstance(task, dict):
            continue
        create = (task.get("create") or {}).get("endpoint")
        cap = task.get("capture") or {}
        if create and cap:
            method, _, p = create.partition(" ")
            out.setdefault(_norm(p), next(iter(cap.values())))
    return out


def build() -> dict:
    docs = json.loads(_DOCS.read_text())["endpoints"]
    catalog = load_catalog()
    model_caps = _model_capture_index()

    # index POST endpoints by (service, normalised path) for producer lookup
    post_by: dict[tuple, str] = {}
    for e in catalog:
        if (e.method or "").upper() == "POST" and e.http_path:
            post_by.setdefault((e.service, _norm(e.http_path)), e.key)

    out: dict[str, dict] = {}
    for e in catalog:
        path = e.http_path or ""
        params = (docs.get(e.key) or {}).get("parameters") or []
        path_doc = {p["name"]: p for p in params
                    if isinstance(p, dict) and p.get("in") == "path"}
        query = [{"name": p["name"], "required": bool(p.get("required"))}
                 for p in params if isinstance(p, dict) and p.get("in") == "query"]

        path_param_names = _PARAM_RE.findall(path)
        last = path_param_names[-1] if path_param_names else None
        pps = []
        for name in path_param_names:
            seg = _owning_segment(path, name)
            rtype = f"{e.service}/{_singularize(seg)}" if seg else None
            prod = None
            cap = None
            prefix = _collection_prefix(path, name)
            if prefix is not None:
                prod = post_by.get((e.service, _norm(prefix)))
                if prod:
                    # capture: model refinement on the producer's path, else $.id
                    cap = model_caps.get(_norm(prefix + "_collection")) \
                        or model_caps.get(_norm(prefix)) or "$.id"
            pps.append({
                "name": name,
                "resource_type": rtype,
                "role": "self" if name == last else "ancestor",
                "produced_by": prod,
                "capture": cap,
            })
        if pps or query:
            out[e.key] = {"path_params": pps, "query_params": query}
    return out


def main(argv=None) -> int:
    data = build()
    _OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    # ---- stats ----------------------------------------------------------
    idbound = [v for v in data.values() if v["path_params"]]
    selfs = [pp for v in idbound for pp in v["path_params"] if pp["role"] == "self"]
    self_prod = [pp for pp in selfs if pp["produced_by"]]
    qp = [v for v in data.values() if v["query_params"]]
    req_qp = [v for v in qp if any(q["required"] for q in v["query_params"])]
    print(f"enriched {len(data)} endpoints -> {_OUT}")
    print(f"  id-bound (>=1 path-param): {len(idbound)}")
    print(f"  self-params with produced_by: {len(self_prod)}/{len(selfs)} "
          f"({100*len(self_prod)//max(len(selfs),1)}%)")
    print(f"  endpoints with query-params: {len(qp)} "
          f"(of which {len(req_qp)} have >=1 REQUIRED query param)")
    # apigw spot-check
    for k in ("application-service/apigateway/showusageplan",
              "application-service/apigateway/listreports",
              "container/scr/showcontainerregistry"):
        if k in data:
            print(f"  [{k.split('/')[-1]}] {json.dumps(data[k], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
