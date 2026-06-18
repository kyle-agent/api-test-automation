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


# --------------------------------------------------------------------------- #
# Residual producer rules — the hard tail the REST path-convention cannot reach
# (evidence: multi-agent investigation 2026-06-18 + knowledge/formal/resources/
# *.yaml). Applied ONLY to a self-param the mechanical derivation left null, so it
# can never regress an existing match. Each rule yields (produced_by, capture, kind).
# --------------------------------------------------------------------------- #
# NOTE: keys below use the BARE service name (Endpoint.service, e.g. "mysql"),
# NOT the category-prefixed "database/mysql". Producer VALUES are full catalog
# keys (category/service/name), verified against the live catalog before use.
#
# DBaaS engines: instance-groups / block-storage-groups are born inside the
# cluster DETAIL read (showcluster), NOT a collection POST; request_id is the
# async-operation id from the cluster create's 202 asyncresponse. The producer is
# in the SAME service as the consumer, so its key prefix == the consumer's.
_DBAAS_SERVICES = (
    "epas", "mariadb", "mysql", "postgresql", "sqlserver",
    "searchengine", "vertica", "eventstreams", "cachestore",
)

# Explicit (bare-service, param) -> (produced_by, capture, kind) for cross-service /
# pseudo-resource ops the path convention can't see.
_RESIDUAL_EXPLICIT = {
    # cross-service: the cluster is an external SKE cluster passed in as a body field
    ("aimlops-platform", "cluster_id"): ("container/ske/createcluster", "$.resource_id", "create-xsvc"),
    ("cloud-ml", "cluster_id"):         ("container/ske/createcluster", "$.resource_id", "create-xsvc"),
    ("data-flow", "cluster_id"):        ("container/ske/createcluster", "$.resource_id", "create-xsvc"),
    ("data-ops", "cluster_id"):         ("container/ske/createcluster", "$.resource_id", "create-xsvc"),
    # same-service creates the convention missed (202/no-body -> jsonpath from model, UNPROVEN)
    ("data-flow", "data_flow_id"):      ("data-analytics/data-flow/createdataflow", "$.id", "create"),
    ("data-ops", "data_ops_id"):        ("data-analytics/data-ops/createdataops", "$.id", "create"),
    ("baremetal", "baremetal_id"):      ("compute/baremetal/createbaremetals", "$.resource_id", "create"),
    # pseudo-resource crypto/vault/diagnosis ops keyed off the parent create
    ("kms", "key_id"):                  ("security/kms/createkey", "$.key.id", "create"),
    ("secretvault", "secret_vault_id"): ("security/secretvault/createsecretvault", "$.secret_vault.id", "create"),
    ("configinspection", "diagnosis_id"):("security/configinspection/creatediagnosisobject", "$.diagnosis_id", "create"),
    # cross-service subnet (server ip read keys off the VPC subnet)
    ("virtualserver", "subnet_id"):     ("networking/vpc/createsubnet", "$.subnet.id", "create-xsvc"),
    # apigateway resource tree: create-under-parent POST; same producer for both roles
    ("apigateway", "resource_id"):      ("application-service/apigateway/createresource", "$.id", "create"),
    ("apigateway", "parent_id"):        ("application-service/apigateway/createresource", "$.id", "create"),
    # generic SRN target: owner-supplied; a disposable resource-group SRN is the practical source
    ("iam", "srn"):                     ("management/resourcemanager/createresourcegroup", "$.resource_group.srn", "create-xsvc"),
}

# Genuine waivers: no producer exists (name-addressed / console-only / EOL). Left
# produced_by=null but tagged producer_kind="waiver" so the worklist is honest.
_RESIDUAL_WAIVERS = {
    ("resourcemanager", "key"),
    ("resourcemanager", "resource_identifier"),
    ("cloudmonitoring", "addrbookId"),
    ("scr", "tags_id"),
}


def _residual_for(service: str, param: str, key_prefix: str):
    """(produced_by, capture, kind) for a null self-param, or (None, None, None).
    `service` is the bare Endpoint.service; `key_prefix` is the consumer endpoint's
    category/service prefix (for same-service templated producers)."""
    if service in _DBAAS_SERVICES:
        if param == "instance_group_id":
            return f"{key_prefix}/{service}showcluster", "$.instance_groups[0].id", "detail-read"
        if param == "block_storage_group_id":
            return f"{key_prefix}/{service}showcluster", "$.instance_groups[0].block_storage_groups[0].id", "detail-read"
        if param == "request_id":
            return f"{key_prefix}/{service}createcluster", "$.request_id", "async-op"
    return _RESIDUAL_EXPLICIT.get((service, param), (None, None, None))


def build() -> dict:
    docs = json.loads(_DOCS.read_text())["endpoints"]
    catalog = load_catalog()
    catalog_keys = {e.key for e in catalog}
    model_caps = _model_capture_index()

    # index POST + list-GET endpoints by (service, normalised path) for producer
    # lookup. A resource id is produced by the POST that creates into its
    # collection; for read-only LOOKUP resources (engine-versions, images,
    # server-types) there is no POST — the list GET to the collection is the
    # producer instead. cross_by holds the same keyed only by path (service
    # dropped) so a cross-service producer (e.g. an SKE cluster_id consumed by
    # aimlops/cloud-ml) can still resolve.
    post_by: dict[tuple, str] = {}
    list_by: dict[tuple, str] = {}
    post_xby: dict[str, str] = {}
    list_xby: dict[str, str] = {}
    for e in catalog:
        if not e.http_path:
            continue
        m = (e.method or "").upper()
        np = _norm(e.http_path)
        if m == "POST":
            post_by.setdefault((e.service, np), e.key)
            post_xby.setdefault(np, e.key)
        elif m == "GET" and "{" not in e.http_path:   # collection list GET
            list_by.setdefault((e.service, np), e.key)
            list_xby.setdefault(np, e.key)

    def _producer(service: str, prefix: str):
        """(producer_key, kind) for the collection at *prefix* — create POST >
        same-service list lookup > cross-service create/list, else (None, None)."""
        np = _norm(prefix)
        if (service, np) in post_by:
            return post_by[(service, np)], "create"
        if (service, np) in list_by:
            return list_by[(service, np)], "lookup"
        if np in post_xby:
            return post_xby[np], "create-xsvc"
        if np in list_xby:
            return list_xby[np], "lookup-xsvc"
        return None, None

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
            prod = kind = cap = None
            prefix = _collection_prefix(path, name)
            if prefix is not None:
                prod, kind = _producer(e.service, prefix)
                if prod:
                    # capture: model refinement on the producer's path, else a
                    # convention default by kind (create envelopes vary -> $.id;
                    # list lookups -> first element id).
                    cap = model_caps.get(_norm(prefix)) or (
                        "$.contents[0].id" if kind and kind.startswith("lookup")
                        else "$.id")
            # Residual fallback: only for a self-param the convention left null —
            # the hard tail (cross-service / nested-in-detail / async / pseudo-op).
            if prod is None and name == last:
                rprod, rcap, rkind = _residual_for(e.service, name, e.key.rsplit("/", 1)[0])
                if rprod and rprod in catalog_keys:
                    prod, cap, kind = rprod, rcap, rkind
                elif (e.service, name) in _RESIDUAL_WAIVERS:
                    kind = "waiver"
            pps.append({
                "name": name,
                "resource_type": rtype,
                "role": "self" if name == last else "ancestor",
                "produced_by": prod,
                "producer_kind": kind,
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
