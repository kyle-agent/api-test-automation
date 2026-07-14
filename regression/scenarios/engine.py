"""CRUD lifecycle ENGINE (AXIS 1) — ordered create/read/[update]/delete runner.

Ported from ``tests/crud/test_crud_lifecycle.py``, freed of pytest. Each
lifecycle in :file:`scenarios.json` runs its steps in order against the live
gateway; values produced by a step (e.g. a new id) are captured and substituted
into later steps.

Faithfully ported behaviour:
  * optional **groups** — an optional step failure cleans up just that group's
    resources, marks the group failed, and continues (so other groups still run
    and record coverage),
  * **environmental skip** for account quota caps (ExceedMax / max-count-exceed)
    AND gateway/WAF 417 blocks ("Request Rejected" / "Support ID") — these are
    not regressions, so we tear down and skip rather than fail,
  * **capture** (JSONPath + filter-object selectors) and soft-capture,
  * **poll** (field/until or until_status) and retry_on_status,
  * **ordered teardown** of created resources on failure (reverse order).

Kernel integration (new):
  * create-step bodies' ``tags`` get :func:`core.registry.owner_tags`
    (axis="regression") merged in, so every resource is owner/run/ttl-stamped
    for the reconciler;
  * every successfully created resource (a step with a ``cleanup``) is tracked
    in a :class:`core.registry.ResourceRegistry` for ordered teardown and a
    crash-safe per-run manifest;
  * before a VPC-creating step the engine consults :class:`core.budgets.Budget`
    (reserve a slot; skip the lifecycle environmentally if the cap is hit) and
    releases the slot when the VPC is torn down.

Recording: every HTTP call (lifecycle steps + probe-reads) records a
:class:`core.results.Observation` (source ``crud_probe``) AND dual-writes the
legacy ``reports/smoke_status.tsv`` so the dashboard keeps working.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from core import budgets as _budgets
from core import registry, results
try:                       # ops-log resource events (best-effort; never breaks a run)
    from core import oplog as _oplog
except Exception:          # pragma: no cover
    _oplog = None
try:                       # console2 local live-event sink (gated by SCP_CONSOLE_EVENTS)
    from core import console_events as _cev
except Exception:          # pragma: no cover
    _cev = None
try:                       # platform command channel (best-effort; never breaks a run)
    from core import commands as _commands
except Exception:          # pragma: no cover
    _commands = None
from core.registry import ResourceRecord, ResourceRegistry
from core.results import Observation
from core.catalog import load_catalog
from core.http_client import MutationBlocked

_HERE = Path(__file__).parent
SCENARIOS_PATH = _HERE / "scenarios.json"
DEPENDENCIES_PATH = _HERE / "dependencies.json"

# Base scenarios.json + every per-service fragment under lifecycles/ (see
# regression.scenarios.loader). One merged list so the engine, dashboard, and
# gap analyzer all agree on the lifecycle set.
from regression.scenarios.loader import load_lifecycles  # noqa: E402

LIFECYCLES = load_lifecycles()
DEPENDENCIES = json.loads(DEPENDENCIES_PATH.read_text())
_PLACEHOLDER = re.compile(r"\{(env:[A-Za-z_][A-Za-z0-9_]*|[a-zA-Z0-9_]+)\}")

_SMOKE_TSV = "reports/smoke_status.tsv"

# Catalog GETs used by "probe_reads" steps to exercise path-parameter GETs that
# the read-only smoke must skip, reusing a resource a lifecycle just created.
_CATALOG = load_catalog()


def _norm_path(p: str) -> str:
    """Collapse templated id segments to '*' (mirrors dashboard.norm_path) so a
    lifecycle step's templated path can be matched back to its catalog endpoint."""
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def _as_status_list(v):
    """Coerce a status-set config value (expect_status / retry_on_status /
    until_status / poll.until) into a list before any ``status in <v>`` test.

    Defensive: a scalar like ``until_status: 404`` (a malformed lifecycle/
    composed-fragment, e.g. gen-wave5-scf-triggers' wait-function-gone, run
    27540589368) would otherwise raise ``TypeError: argument of type 'int' is
    not iterable`` on ``resp.status in until_status`` and crash the whole
    lifecycle. ``None`` -> ``[]``; a scalar -> one-element list; a list passes
    through unchanged."""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]


# (METHOD, normalized-path, service) -> catalog endpoint key. Lets a CRUD WRITE
# step be recorded under its REAL catalog key (not just "lifecycle:step") so its
# HTTP status + response time show up in the dashboard's per-endpoint column,
# exactly like read-only GETs. Service is part of the key because path roots
# collide across services (e.g. /v1/volumes, /v1/clusters).
_CAT_KEY_BY_MNS: dict[tuple, str] = {}
for _e in _CATALOG:
    _CAT_KEY_BY_MNS.setdefault(
        ((_e.method or "").upper(), _norm_path(_e.http_path), _e.service), _e.key)


def _canon_service(service: str | None) -> str:
    """Normalize a region-routing service ALIAS to its catalog service name.
    ``filestorage-dr`` (SCP_SERVICE_HOSTS -> kr-east1 host) is the SAME catalog
    service as ``filestorage`` — the alias only re-homes the request to the DR
    region. Without this, a DR-side 2xx (the ONLY working path for
    set/deletevolumereplication, which 400 'Invalid.volume.purpose' from the
    source side) records under an unknown service and is DROPPED from per-endpoint
    coverage, pinning those endpoints at the source-side 400 forever. No real SCP
    service name ends in ``-dr``, so this is unambiguous."""
    svc = service or ""
    return svc[:-3] if svc.endswith("-dr") else svc


def _catalog_key_for(method: str, templated_path: str, service: str | None):
    """Resolve a step's (method, templated path, service) to a catalog key, or None."""
    m, p = (method or "").upper(), _norm_path(templated_path)
    return (_CAT_KEY_BY_MNS.get((m, p, service or ""))
            or _CAT_KEY_BY_MNS.get((m, p, _canon_service(service))))

# Quota kinds whose budget must be reserved before a step's create, keyed by the
# path it creates. Derived from dependencies.json (path -> kind) so the kernel
# budget is consulted as DATA, not hardcoded.
_VPC_CREATE_PATH = "/v1/vpcs"

# Shared-resource adoption (knowledge/vpc-scheduling-strategy.md). A step marked
# {"adopt": "<kind>"} reuses a session-shared resource instead of creating its
# own, so the heavy lifecycles don't each consume a slot against the 5-VPC cap.
# Maps adopt kind -> the ctx var holding the shared resource id (seeded from the
# shared_ctx the pytest fixture builds via provision_shared_vpc). When the shared
# id is absent (no fixture / mutations off) an adopt step is a NO-OP and the
# lifecycle falls back to its own create/delete (so this can never regress CRUD).
_ADOPT_SHARED = {"vpc": "shared_vpc_id", "subnet": "shared_subnet_id",
                 "subnet#db": "shared_db_subnet_id",
                 # 네트워킹 공유 VPC 2개 (오너 설계 2026-07-13): peering의 두
                 # VPC를 A/B로 상주 프로비저닝하고 vip-nat(A)·fw/DC(B)가 그
                 # 안에서 IGW·DC 등을 테스트 — IGW(VPC당 1)·DC(VPC당 1) 배타가
                 # A/B 분산으로 해소되고 런당 VPC 생성이 7→5회로 준다.
                 # CIDR은 peering rule 하드코딩과 일치(A=10.130/20, B=10.141/20)
                 # — 10.124 공유 VPC adopt 때의 adopt-cidr 불일치 클래스 회피.
                 "vpc#a": "shared_net_vpc_a_id", "vpc#b": "shared_net_vpc_b_id",
                 "tgw": "shared_tgw_id",
                 # 공유 IGW (adopt:igw 대상, 오너 2026-07-14). IGW는 VPC당 1개
                 # 배타 — 메인 공유 VPC를 여러 adopt:vpc lifecycle이 나눠 쓰므로
                 # 각자 create-igw를 하면 2번째부터 400 already-associated (gen-
                 # heavy-lb-members 실패). 공유 VPC에 IGW 1개를 상주시키고 lb-
                 # members·vs-netops·pilot-net-basics·vpn-gateway가 adopt → skip.
                 # IGW create/PUT/delete 커버리지는 net-VPC A/B의 IGW 소유자(vip-
                 # nat=A·fw=B)가 유지한다. TGW adopt와 동일 패턴.
                 "igw": "shared_igw_id"}
# 이 프로세스가 이미 ACTIVE를 확인한 adopted id 캐시 — 게이트는 id당 1회만 폴.
_ADOPT_ACTIVE_SEEN: set = set()
_SHARED_VPC_CIDR = "10.124.0.0/20"
# Shared subnet carved from the first /24 of the shared VPC's /20. ADOPT-class
# lifecycles re-home their fixed host IPs into this range (10.124.0.x) so that
# parallel adopters do not collide on the SAME host IP in the ONE shared subnet
# (see knowledge/vpc-scheduling-strategy.md fixed-IP map).
_SHARED_SUBNET_CIDR = "10.124.0.0/24"
# DB-lane shared subnet — 10.124.1-6.0/24 are reserved by the adopters'
# self-create FALLBACK subnets (knowledge/domain-constraints.md), so the DB
# lane takes the next free /24 of the shared /20.
# Claimed child-/24 slots of the shared /20 beyond DB (7): 8=vs-port
# (scenarios.json compute-virtualserver-full), 9=networking-vpc-subnet,
# 10=gen-vpc-endpoint endpoint-subnet (light-batch2, 2026-07-08 재호밍).
# New fixed host IPs inside the SHARED subnet(.0/24) go in dependencies.json
# fixed_ip_map (.5/.6 pls, .7/.8 apigw-pls, .20 vpce, .30/.31 lb).
_SHARED_DB_SUBNET_CIDR = "10.124.7.0/24"
# 네트워킹 공유 VPC A/B — vpc-peering의 rule CIDR 하드코딩과 일치해야 한다
# (knowledge/validated-facts.md 2026-07-11: requester rule cidr은 requester VPC
# CIDR의 진부분집합만 202). A에는 vip-nat 서브넷 10.130.9.0/24가 들어간다.
_NET_VPC_A_CIDR = "10.130.0.0/20"
_NET_VPC_B_CIDR = "10.141.0.0/20"
_SUBNET_CREATE_PATH = "/v1/subnets"
# Env keys for cross-process (xdist) adoption of an already-live shared VPC/subnet
# provisioned once by regression.scenarios.shared_infra --provision.
_ENV_SHARED_VPC = "SCP_SHARED_VPC_ID"
_ENV_SHARED_SUBNET = "SCP_SHARED_SUBNET_ID"
# DB-lane subnet: DB cluster provisioning is the slowest thing in the parallel
# pass, so the DB lifecycles get their OWN shared subnet (lane isolation) while
# VM/SKE/networking adopters stay on the main shared subnet (fixed IPs intact).
_ENV_SHARED_DB_SUBNET = "SCP_SHARED_DB_SUBNET_ID"
# 네트워킹 공유 VPC A/B (vpc#a/vpc#b adopt 대상). *_NAME은 gen-wave5-fw의
# 방화벽 조회(vpc_name= 쿼리)용 — id만으로는 IGW 방화벽을 못 찾는다.
_ENV_SHARED_NET_VPC_A = "SCP_SHARED_NET_VPC_A_ID"
_ENV_SHARED_NET_VPC_B = "SCP_SHARED_NET_VPC_B_ID"
_ENV_SHARED_NET_VPC_A_NAME = "SCP_SHARED_NET_VPC_A_NAME"
_ENV_SHARED_NET_VPC_B_NAME = "SCP_SHARED_NET_VPC_B_NAME"
# 공유 TGW (adopt:tgw 대상, 오너 2026-07-13). TGW 계정 캡 3인데 self-create가 3개
# (children·gen-private-nat·heavy-shared-networking) → 헤드룸 0, 잔재 1개면 exceed.
# children만 TGW를 소유(CRUD 주인공)하고, 나머지 둘은 전제조건 용도라 공유 TGW를
# adopt → 동시 TGW 3→2(1 shared + 1 self). 공유 VPC와 동일 패턴.
_ENV_SHARED_TGW = "SCP_SHARED_TGW_ID"
_TGW_CREATE_PATH = "/v1/transit-gateways"
# 공유 IGW (adopt:igw 대상, 오너 2026-07-14). 메인 공유 VPC에 IGW 1개를 상주시켜
# adopt:vpc lifecycle들이 채택 → VPC당 1 배타 충돌(400 already-associated) 해소.
_ENV_SHARED_IGW = "SCP_SHARED_IGW_ID"
_IGW_CREATE_PATH = "/v1/internet-gateways"


# --------------------------------------------------------------------------- #
# categorize + recording (dual-write)
# --------------------------------------------------------------------------- #
def categorize(status: int, text: str) -> str:
    """Same ok/soft/fail split as the smoke suite (only 5xx / HMAC-401 are hard
    fails; everything else is the API answering correctly given this account)."""
    t = (text or "").lower()
    if 200 <= status < 300:
        return results.OK
    if status == 401:
        return results.SOFT if ("rejected by gateway" in t
                                or "catalog has not target" in t) else results.FAIL
    if status >= 500:
        return results.FAIL
    return results.SOFT  # 400/403/404/409/422 — needs params/permission/provisioning


def _record_smoke(status, category, key, method, path, elapsed_ms=None, note=""):
    """Dual-write: unified Observation store AND legacy smoke TSV. `note`
    carries the response body for fail-category calls so every red row in the
    artifacts is self-diagnosing (no log spelunking)."""
    results.record(Observation(
        endpoint_key=key, method=method, path=path, status=status,
        category=category, elapsed_ms=elapsed_ms, source="crud_probe",
        note=(note or "")[:400]))
    import os
    ems = "" if elapsed_ms is None else f"{elapsed_ms:.0f}"
    # Under pytest-xdist each worker writes its OWN smoke shard (smoke_status-gw0.tsv)
    # so parallel workers don't interleave lines on one file; the workflow's
    # "Merge per-worker results" step concatenates the shards back into
    # reports/smoke_status.tsv (loaders glob canonical + shards).
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    tsv = _SMOKE_TSV if not worker else _SMOKE_TSV.replace(".tsv", f"-{worker}.tsv")
    try:
        os.makedirs("reports", exist_ok=True)
        with open(tsv, "a") as fh:
            fh.write(f"{status}\t{category}\t{key}\t{method}\t{path}\t{ems}\n")
    except OSError:
        pass


# (A)/(B) catalog enrichment sidecar — identity-based read->producer matching
# (design A, stage 2, 2026-06-18). data/api_catalog_params.json maps each catalog
# endpoint to structured path/query params, including `produced_by` (the create
# that BIRTHS the id) + its `capture` jsonpath. This lets _probe_reads resolve a
# GET's path-param by IDENTITY (which create produced the id, recorded per
# lifecycle in `produced`) instead of by capture-var STRING name — retiring the
# hand-maintained _PARAM_ALIASES map and making create->조회(show) self-maintaining.
_PARAMS_PATH = Path(__file__).resolve().parents[2] / "data" / "api_catalog_params.json"
_PARAMS_SIDECAR: dict = {}
_PRODUCER_OF: dict = {}   # create_endpoint_key -> (resource_type, capture_jsonpath)
try:
    _PARAMS_SIDECAR = json.loads(_PARAMS_PATH.read_text())
    for _ek, _meta in _PARAMS_SIDECAR.items():
        for _pp in _meta.get("path_params", []):
            _pk = _pp.get("produced_by")
            if _pk:
                _PRODUCER_OF.setdefault(_pk, (_pp.get("resource_type"), _pp.get("capture")))
except Exception as _exc:   # missing/corrupt sidecar -> identity disabled, alias fallback stands
    _PARAMS_SIDECAR, _PRODUCER_OF = {}, {}


# Residual name-addressed fallback. The identity resolver above now covers every
# `produced_by` case — LIVE-PROVEN 2026-06-18: the per-service parallel coverage
# runs fired the auto-probe across apigateway/scf/iam/kms/scr/resourcemanager/
# secrets/etc. (884 crud_probe 2xx), resolving id-bound GET path-params by the
# create that produced them. The 8 former string-alias entries (registry_id,
# repository_id, dbaas_engine_version_id, certificate_id, resource_group_id,
# security_group_id, security_group_rule_id, service_account_id) are RETIRED —
# identity supersedes them (offline-proven in tests/offline/test_probe_identity.py).
# Only `srn` remains: it is name-addressed (an arbitrary target resource's SRN)
# with NO producer in the catalog, so the resource-group `rg_srn` capture is the
# practical seed. Tried LAST, after exact-name and identity.
_PARAM_ALIASES = {
    "srn": ("rg_srn",),
}


# Auto-probe runtime guards (2026-06-18): the full-ctx auto-seed fires many more
# id-bound GETs per lifecycle than the old hand-seed, so bound the per-step count
# and use a short, non-retrying deadline (read-only best-effort) so a slow read
# can't blow up the run. Tunable via env for live tuning.
import os as _os
_PROBE_TIMEOUT_S = float(_os.environ.get("SCP_PROBE_TIMEOUT_S", "8"))
_PROBE_MAX_PER_STEP = int(_os.environ.get("SCP_PROBE_MAX_PER_STEP", "60"))


def _resolve_param(param, mapping, endpoint=None, produced=None, produced_rtype=None):
    """Value for a catalog path-param, in priority order:
      1. exact capture-var name match in the seed (`mapping`);
      2. IDENTITY — the enrichment sidecar says this `endpoint`'s `param` is
         `produced_by` a create whose freshly-created id we recorded in
         `produced` (or, failing that, by `resource_type` in `produced_rtype`);
      3. LEGACY `_PARAM_ALIASES` fallback (residual name-addressed params).
    Returns None when none apply."""
    if param in mapping:
        return mapping[param]
    if endpoint is not None and produced is not None:
        meta = _PARAMS_SIDECAR.get(getattr(endpoint, "key", None), {})
        for pp in meta.get("path_params", []):
            if pp.get("name") != param:
                continue
            pk = pp.get("produced_by")
            if pk and pk in produced:
                return produced[pk]
            rt = pp.get("resource_type")
            if rt and produced_rtype and rt in produced_rtype:
                return produced_rtype[rt]
            break
    for alias in _PARAM_ALIASES.get(param, ()):
        if alias in mapping:
            return mapping[alias]
    return None


# Known-safe constant values for REQUIRED query params on id-bound GET probes.
# Some id-bound reads 400 unless a required query param is supplied (the param is
# NOT in the URL path, so it can't be resolved from path-param identity). A value
# the API treats as "give me everything" is read-only and creates nothing.
# LIVE-PROVEN 2026-06-18 (queueservice getqueueattributes): attributes=All is
# required and case-sensitive ("ALL"/"all" 400). See knowledge/validated-facts.md.
_QUERY_DEFAULTS = {
    "attributes": "All",
}


def _resolve_query_param(name, mapping):
    """Value for a REQUIRED query param on an id-bound GET probe:
      1. a known-safe constant (`_QUERY_DEFAULTS`, e.g. attributes=All);
      2. the same name present in the seed/ctx `mapping` (e.g. a captured
         resource name a filter-by-name read requires).
    Returns None when neither applies (the probe then skips that GET)."""
    if name in _QUERY_DEFAULTS:
        return _QUERY_DEFAULTS[name]
    if name in mapping:
        return mapping[name]
    return None


def _probe_reads(client, mapping, service, produced=None, produced_rtype=None):
    """Call every catalog GET in `service` whose path params are all resolvable —
    by exact capture-var name in `mapping`, by IDENTITY (the create that produced
    the id, recorded in `produced`/`produced_rtype`), or by a legacy alias. Also
    supplies any REQUIRED query params the enrichment sidecar declares (skipping
    the GET when one can't be resolved). Read-only, record only — never fails the
    lifecycle."""
    called = 0
    for e in _CATALOG:
        if e.service != service or (e.method or "").upper() != "GET":
            continue
        if not e.http_path:
            continue
        params = set(_PLACEHOLDER.findall(e.http_path))
        if not params:
            continue
        vals = {p: _resolve_param(p, mapping, e, produced, produced_rtype) for p in params}
        if any(v is None for v in vals.values()):
            continue
        # Required query params (sidecar-declared) that live OUTSIDE the path —
        # resolve each or skip this GET (a bare call would just 400).
        req_q = [q["name"] for q in _PARAMS_SIDECAR.get(e.key, {}).get("query_params", [])
                 if q.get("required")]
        qparams = {n: _resolve_query_param(n, mapping) for n in req_q}
        if any(v is None for v in qparams.values()):
            continue
        if called >= _PROBE_MAX_PER_STEP:   # runtime guard (auto-seed can match many)
            print(f"  probe-reads[{service}]: cap {_PROBE_MAX_PER_STEP} reached, "
                  f"skipping remaining")
            break
        path = e.http_path
        for p, v in vals.items():
            path = path.replace("{%s}" % p, str(v))
        try:
            # best-effort: short deadline, no retry — a slow/unreachable read must
            # not cost cfg.timeout x retries (the auto-seed fires many more GETs
            # than the old hand-seed, so an unbounded per-GET cost compounds).
            resp = client.get(path, service=service, params=qparams or None,
                              timeout=_PROBE_TIMEOUT_S, retry=False)
        except Exception as exc:  # network/host issue — record nothing, continue
            print(f"  probe ERROR {path}: {exc}")
            continue
        _record_smoke(resp.status, categorize(resp.status, getattr(resp, "raw_text", "")),
                      e.key, "GET", e.http_path, getattr(resp, "elapsed_ms", None))
        called += 1
    print(f"  probe-reads[{service}]: {called} path-param GET(s) exercised")


# --------------------------------------------------------------------------- #
# capture / fill helpers (ported verbatim)
# --------------------------------------------------------------------------- #
def _jsonpath_get(obj, expr: str):
    """Tiny `$.a.b` / `$.a[0].b` resolver — enough for capturing ids."""
    cur = obj
    for token in expr.lstrip("$").lstrip(".").split("."):
        m = re.match(r"([a-zA-Z0-9_]+)(?:\[(\d+)\])?", token)
        if not m:
            return None
        key, idx = m.group(1), m.group(2)
        cur = cur.get(key) if isinstance(cur, dict) else None
        if idx is not None and isinstance(cur, list):
            cur = cur[int(idx)] if int(idx) < len(cur) else None
        if cur is None:
            return None
    return cur


def _capture(body, expr):
    """Capture a value from a response. `expr` is a JSONPath string or a filter
    object selecting the first list element matching field prefixes:
        {"list": "$.server_types", "where_prefix": {"id": "s"},
         "where_not_prefix": {"id": "g"}, "get": "id"}"""
    if body is None:
        return None
    if isinstance(expr, str):
        return _jsonpath_get(body, expr)
    items = _jsonpath_get(body, expr["list"]) or []
    where = expr.get("where_prefix", {})
    wnot = expr.get("where_not_prefix", {})
    for item in items:
        if not isinstance(item, dict):
            continue
        if not all(str(item.get(k, "")).startswith(v) for k, v in where.items()):
            continue
        excluded = False
        for k, pfx in wnot.items():
            prefixes = [pfx] if isinstance(pfx, str) else pfx
            if any(str(item.get(k, "")).startswith(p) for p in prefixes):
                excluded = True
                break
        if not excluded:
            return item.get(expr["get"])
    return None


def _apply_b64_fields(step: dict, body):
    """필드 단위 base64 인코딩 (step key ``json_b64_fields``) — IAM createiamuser가
    평문 password를 400 "Password must be encoded base64"로 거부하는 문서 미기재
    요구 (2026-07-10 실측, PF-36). 토큰({ualpha}) 치환 **후** 인코딩해야 하므로
    레시피 리터럴이 아니라 엔진의 몫이다."""
    for f in step.get("json_b64_fields") or []:
        if isinstance(body, dict) and isinstance(body.get(f), str):
            body[f] = base64.b64encode(body[f].encode()).decode()
    return body


def _fill(template: str, ctx: dict) -> str:
    def _sub(m):
        key = m.group(1)
        if key.startswith("env:"):
            # Secret/config injection: the value comes from the runner
            # environment (e.g. a GitHub secret), NEVER from git — so real
            # credentials (SCR registry keys for cloud-ml) stay out of the repo.
            # Absent -> "" (the lifecycle's requires_env gate skips it upstream).
            return os.environ.get(key[4:], "")
        return str(ctx.get(key, m.group(0)))
    return _PLACEHOLDER.sub(_sub, template)


_PEM_BLOCK = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)
_CERT_MATERIAL: dict | None = None  # per-process cache; {} = generation attempted+failed


def _self_signed_pem() -> dict | None:
    """Generate a throwaway self-signed RSA cert + key (PEM) via the ``openssl``
    CLI, cached per process. Returns ``{cert_body, private_key, cert_chain}`` for
    {placeholder} substitution, or ``None`` when openssl is unavailable / fails.

    Nothing is written to disk and nothing is committed — a fresh keypair is
    minted each run purely to exercise the certificatemanager import + validate
    endpoints (which need a body/key pair that actually matches). The cert is
    self-signed with a 10-year validity, so there is no expiry flakiness."""
    global _CERT_MATERIAL
    if _CERT_MATERIAL is not None:
        return _CERT_MATERIAL or None
    _CERT_MATERIAL = {}  # mark attempted so we don't re-shell on every lifecycle
    if shutil.which("openssl") is None:
        return None
    try:
        out = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", "/dev/stdout", "-out", "/dev/stdout", "-days", "3650",
             "-subj", "/CN=regr-test.example.com", "-batch"],
            capture_output=True, text=True, timeout=30, check=True).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  cert material: openssl generation failed ({exc}); "
              f"certificatemanager import/validate lifecycle will skip")
        return None
    blocks = _PEM_BLOCK.findall(out)
    cert = next((b for b in blocks if "CERTIFICATE" in b), None)
    key = next((b for b in blocks if "PRIVATE KEY" in b), None)
    if not cert or not key:
        return None
    # `openssl req -newkey` emits the key as PKCS#8 (-----BEGIN PRIVATE KEY-----),
    # but certificatemanager's check-validation/import only accept the traditional
    # PKCS#1 encoding (-----BEGIN RSA PRIVATE KEY-----) and otherwise reject it as
    # "not a PEM format". OpenSSL 3.x also defaults `openssl rsa` to PKCS#8, so we
    # ask for `-traditional`; on OpenSSL 1.x (no such flag) plain `openssl rsa`
    # already yields PKCS#1, so fall back to it.
    if "BEGIN RSA PRIVATE KEY" not in key:
        for args in (["openssl", "rsa", "-traditional"], ["openssl", "rsa"]):
            try:
                conv = subprocess.run(args, input=key, capture_output=True,
                                      text=True, timeout=30, check=True).stdout
            except (subprocess.SubprocessError, OSError):
                continue
            if "BEGIN RSA PRIVATE KEY" in conv:
                key = conv.strip()
                break
        else:
            print("  cert material: PKCS#1 conversion failed; "
                  "certificatemanager import/validate lifecycle will skip")
            return None
    # cert_chain is optional for a self-signed leaf; send empty to avoid the
    # gateway rejecting a self-referential chain.
    _CERT_MATERIAL = {"cert_body": cert + "\n", "private_key": key + "\n",
                      "cert_chain": ""}
    return _CERT_MATERIAL


def _fill_obj(obj, ctx: dict):
    """Recursively substitute {placeholders} inside a request body."""
    if isinstance(obj, str):
        out = _fill(obj, ctx)
        # 정수 보존 치환 (svc-opt 2026-07-11): epoch_* 토큰 단독 값만 int로
        # — 숫자형 스키마 필드(listmetricdata start/end)용. 토큰 이름 한정인
        # 이유: 범용 '숫자면 int' 규칙은 {today}("20260711") 같은 문자열
        # 스키마 필드를 int로 바꿔 400을 유발했다 (cert selfsign 실측 회귀).
        if (isinstance(out, str) and out.isdigit()
                and obj.startswith("{epoch_") and obj.endswith("}")
                and obj.count("{") == 1):
            return int(out)
        return out
    if isinstance(obj, dict):
        # keys can be templated too (e.g. resourcemanager bulk-tag bodies are
        # {"{rg_srn}": [...]} maps keyed by SRN)
        return {(_fill(k, ctx) if isinstance(k, str) else k): _fill_obj(v, ctx)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_obj(v, ctx) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# kernel: tag injection + budget kind resolution
# --------------------------------------------------------------------------- #
def _is_create(step: dict) -> bool:
    """A create step: a POST that registers a cleanup (i.e. births a resource)."""
    return (step.get("method", "").upper() == "POST" and bool(step.get("cleanup")))


def _inject_owner_tags(body, axis: str = "regression"):
    """Merge owner/run/ttl tags into a create body's ``tags`` list so the
    resource is attributable + time-bounded for the reconciler. Only acts when
    the body already carries a ``tags`` list (SCP's ``[{key,value}]`` shape);
    bodies without a tags field are left untouched (the API would reject extras)."""
    if isinstance(body, dict) and isinstance(body.get("tags"), list):
        existing_keys = {t.get("key") for t in body["tags"] if isinstance(t, dict)}
        for tag in registry.owner_tags(axis=axis):
            if tag["key"] not in existing_keys:
                body["tags"].append(tag)
    return body


def _budget_kind_for_path(path: str) -> str | None:
    """Map a create path to a budget kind (so quota checks are data-driven)."""
    if path == _VPC_CREATE_PATH:
        return "vpc"
    if path == "/v1/private-dns":
        return "private-dns"
    return None


# --- cross-process VPC throttle (scheduler ADR v0.5) ----------------------- #
# The in-process Budget can't coordinate the 5-VPC account cap across xdist
# workers, which is why VPC-self-creating lifecycles run in a SEPARATE serial
# job today. When SCP_VPC_SEMAPHORE=true, a VPC self-create instead ACQUIRES a
# slot from core.budgets.CrossProcessSemaphore (file-backed, shared by every
# worker of the run) and BLOCKS until one frees — so the VPC-CRUD lane can run
# inside ONE parallel pool, ≤ cap concurrent. OPT-IN: off by default -> None ->
# behaviour is exactly today's per-process budget skip (serial job unchanged).
_VPC_DELETE_RE = re.compile(r"^/v1/vpcs/[^/]+/?$")


def _is_vpc_delete(method: str, path: str) -> bool:
    """A successful own DELETE of a whole VPC (the happy path deletes via its
    own step, not teardown) — the point at which its slot must be freed."""
    return method.upper() == "DELETE" and bool(_VPC_DELETE_RE.match(path or ""))


def _vpc_semaphore_cfg():
    """Return (semaphore, limit, timeout, poll) when the v0.5 VPC throttle is
    enabled (SCP_VPC_SEMAPHORE=true), else None. ``limit`` = the vpc cap minus
    the slots reserved for shared infra (the ADOPT lane's shared VPC), so
    self-creators never collide with the shared one; override the reservation
    via SCP_VPC_SHARED_RESERVED (defaults to 1 when a shared VPC id is present)."""
    if os.environ.get("SCP_VPC_SEMAPHORE", "").strip().lower() not in ("1", "true", "yes"):
        return None
    cap = _budgets.Budget().limits.get("vpc", 5)
    _res = os.environ.get("SCP_VPC_SHARED_RESERVED", "").strip()
    reserved = int(_res) if _res else (1 if os.environ.get(_ENV_SHARED_VPC, "").strip() else 0)
    limit = max(1, cap - reserved)
    timeout = float(os.environ.get("SCP_VPC_SEMAPHORE_TIMEOUT", "1800"))
    poll = float(os.environ.get("SCP_VPC_SEMAPHORE_POLL", "1.0"))
    return _budgets.CrossProcessSemaphore("vpc"), limit, timeout, poll


# --------------------------------------------------------------------------- #
# step execution (poll + retry, ported)
# --------------------------------------------------------------------------- #
def _run_step(client, step, path, body, service, ctx, *, lifecycle_id: str = ""):
    """Execute a step; honour retry_on_status and poll (field/until or
    until_status) for async provisioning/teardown. ``lifecycle_id`` lets the
    platform command channel target this step's poll loop (stop_polling)."""
    # Fill {placeholders} in query params from ctx (e.g. a captured id or the
    # {unique} name suffix) — some id-bound GETs REQUIRE a query param whose value
    # is the resource's own name (e.g. queueservice getqueueattributes needs
    # name=<queue name>), so params must be resolved just like path/body.
    params = _fill_obj(step.get("params"), ctx)
    try:
        resp = client.request(step["method"], path, json=body, service=service, params=params,
                          headers=step.get("headers"))
    except Exception as exc:
        # One retry on a transport timeout (field case: iam PUT hit the 20s
        # read timeout once and failed the whole lifecycle). Slow-but-alive
        # gateways are environmental; a single retry absorbs the blip.
        _exc_name = type(exc).__name__.lower()
        # ConnectionError/ProxyError도 1회 재시도 대상 (svc-opt 2026-07-11:
        # gen-wave-apigw가 일시 ConnectionError 한 방에 라이프사이클째 실패 —
        # timeout과 같은 '느리지만 살아있는 게이트웨이' 환경 클래스).
        if ("timeout" not in _exc_name and "timed out" not in str(exc).lower()
                and "connection" not in _exc_name and "proxy" not in _exc_name):
            raise
        print(f"  step '{step.get('name')}' transport blip — retrying once ({exc})")
        time.sleep(5)
        resp = client.request(step["method"], path, json=body, service=service, params=params,
                          headers=step.get("headers"))
    # 429 레이트리밋 재시도는 http_client.RETRY_STATUS(+Retry-After 존중)가
    # 전 요청 공통으로 처리한다 (run-c373 수리, 2026-07-13 — 병렬 세션이
    # 클라이언트 레벨로 먼저 반영) — 엔진 레벨 중복 백오프는 두지 않는다.
    ros = _as_status_list(step.get("retry_on_status"))
    if ros:
        attempts = int(step.get("retries", 4))
        interval = float(step.get("retry_interval", 15))
        while resp.status in ros and attempts > 0:
            # 플랫폼 중단 명령은 재시도 사다리 **도중에도** 듣는다 (2026-07-11
            # 오너 실측: 40×30s 사다리에 물린 시나리오가 ⏹로 안 끊겼음 —
            # 종전에는 폴 루프만 명령을 확인했다). 비소비 peek만 쓴다:
            # 여기서 should_skip을 소비하면 스텝 경계의 teardown+스킵이 무산됨.
            if _commands is not None and getattr(_commands, "peek_interrupt",
                                                 lambda _l: False)(lifecycle_id):
                print(f"  step '{step.get('name')}': platform command — "
                      f"abandoning retry ladder ({attempts} attempts left)")
                break
            time.sleep(interval)
            resp = client.request(step["method"], path, json=body, service=service, params=params,
                          headers=step.get("headers"))
            attempts -= 1
    poll = step.get("poll")
    if not poll:
        return resp
    # A path/param that still carries an unresolved {token} (soft-capture miss
    # after a tolerated create) can NEVER converge — the server keeps answering
    # 400/404 for the literal token until the poll's full timeout burns (field
    # case: gen-heavy-aimlops run-2 spun 30min x 10 attempts on a literal
    # {release_id}; same judgement as the optional-retry cap above). Return the
    # first response as-is: give_up_status/expect_status semantics still apply.
    def _has_unresolved(v) -> bool:
        if isinstance(v, str):
            return "{" in v
        if isinstance(v, dict):
            return any(_has_unresolved(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return any(_has_unresolved(x) for x in v)
        return False
    if "{" in path or _has_unresolved(params):
        print(f"  step '{step.get('name')}': unresolved placeholder in polled "
              f"path/params — skipping poll (a literal token can never converge)")
        return resp
    until_status = _as_status_list(poll.get("until_status")) or None
    field, until = poll.get("field"), _as_status_list(poll.get("until"))
    # give_up_status: statuses that END the poll immediately (resp returned as-is).
    # For settle-polls (wait-after-<mutation> GETs) a 4xx means the polled resource
    # never existed (create failed / placeholder unresolved) — without this, each
    # such poll burns its FULL timeout against a 404 (observed: epas in run
    # 28602725440 burned 900s x ~15 waits after a rejected create).
    give_up_status = _as_status_list(poll.get("give_up_status")) or None
    timeout, interval = float(poll.get("timeout", 300)), float(poll.get("interval", 10))
    # Optional refire: while polling for a teardown to complete, a resource can
    # wedge in a FAILED-delete state (field report: a console delete of a
    # failed-state DB cluster succeeds immediately). When the polled body's
    # `field` hits one of `when`, re-issue the configured request (usually the
    # DELETE that preceded this poll) up to `max` times and keep polling.
    refire = poll.get("refire")
    refire_left = int(refire.get("max", 3)) if refire else 0
    deadline = time.monotonic() + timeout
    _poll_t0 = time.monotonic()
    _poll_n = 0
    while time.monotonic() < deadline:
        if give_up_status is not None and resp.status in give_up_status:
            return resp
        if until_status is not None:
            if resp.status in until_status:
                return resp
        elif field:
            val = _jsonpath_get(resp.body, field) if resp.body else None
            if val in until:
                return resp
            # TERMINAL-BAD (batch-2 규약의 엔진 내장화 — owner 2026-07-09 실측:
            # VM이 ERROR로 전이했는데 wait 폴이 20분 한도까지 공회전). until이
            # 못 잡은 ERROR/FAILED는 절대 수렴하지 않으니 즉시 폴을 끝내고
            # 응답에 마커를 남긴다 — 호출측이 스텝을 실패로 분류한다.
            # until에 명시된 값(의도적 대기)과, refire가 직접 처리하는 상태
            # (refire.when — failed-delete 재발사, UNKNOWN→sync-state 복구)는
            # 건드리지 않는다. refire가 있어도 refire.when 밖의 ERROR/FAILED는
            # 여전히 fast-exit — 종전엔 refire 설정만으로 terminal-bad 전체가
            # 꺼져 ERROR 클러스터를 타임아웃까지 공회전할 수 있었다.
            # per-poll override: poll.terminal_bad (예: [] = 끔, ["UNKNOWN"] = 확장).
            _bad = poll.get("terminal_bad")
            _bad = {"ERROR", "FAILED"} if _bad is None else set(map(str, _bad))
            _refire_handles = set((refire or {}).get("when") or [])
            if (isinstance(val, str) and val in _bad
                    and val not in _refire_handles):
                print(f"  step '{step.get('name')}': polled state '{val}' is "
                      f"TERMINAL-BAD — ending poll early (never converges)")
                try:
                    resp._terminal_bad = val
                except Exception:
                    pass
                return resp
        if refire and refire_left > 0 and resp.status < 400 and resp.body:
            _state = _jsonpath_get(resp.body, refire["field"])
            if _state in refire.get("when", []):
                refire_left -= 1
                _rpath = _fill(refire["path"], ctx)
                print(f"  step '{step.get('name')}': polled state '{_state}' is a "
                      f"failed-delete state — refiring {refire['method']} {_rpath} "
                      f"({refire_left} refire(s) left)")
                try:
                    rr = client.request(refire["method"], _rpath, service=service)
                    print(f"    refire -> {rr.status}")
                except Exception as exc:  # destructive gate / transport — keep polling
                    print(f"    refire failed: {exc}")
        # Platform command channel: an operator can force a wedged wait to end
        # NOW. Break out exactly as a poll timeout would — the post-loop
        # not-ready gate then applies: if the last response still hasn't met the
        # wait condition, the step fails (abandoning a wait doesn't make the
        # resource ready). Only a poll that had already converged passes.
        if _commands is not None and _commands.should_stop_polling(lifecycle_id):
            print(f"  step '{step.get('name')}': platform command stop_polling "
                  f"— abandoning wait (handled like a poll timeout)")
            break
        # 폴링 생존 신호 (console2): attempt/현재 state/경과를 이벤트로 — 리포트의
        # ⏳ run 행이 "멈춤"이 아니라 "N회차 · CREATING · 7분째"로 읽히게. env-gated
        # no-op라 headless 런 비용 0.
        _poll_n += 1
        if _cev or poll.get("verbose"):
            _val = _jsonpath_get(resp.body, field) if (field and resp.body) else None
            _state = _val if isinstance(_val, str) else resp.status
            if _cev:
                _cev.emit("poll-progress", lifecycle=lifecycle_id,
                          step=step.get("name"), attempt=_poll_n,
                          state=_state,
                          elapsed_s=round(time.monotonic() - _poll_t0, 1),
                          timeout_s=timeout)
            if poll.get("verbose"):
                # 비-pytest 경로(공유 인프라 프로비저닝)의 로그 생존 신호 — pytest
                # 아래 lifecycle step은 stdout이 캡처되므로 poll-progress 이벤트가
                # 그 역할을 하고, 이 print는 스트리밍되는 provision 로그용이다.
                print(f"    ⏳ {step.get('name')}: {_poll_n}회차 state={_state} "
                      f"({round(time.monotonic() - _poll_t0)}s/{timeout:.0f}s)",
                      flush=True)
        time.sleep(interval)
        resp = client.request(step["method"], path, json=body, service=service, params=params,
                          headers=step.get("headers"))
    # Poll loop ended without an in-loop success return — the deadline passed
    # or an operator forced a stop. If this poll carried a real wait condition
    # (until_status or field/until) and the LAST response does NOT satisfy it,
    # the resource never reached the required state. Returning it silently used
    # to let expect_status [200] pass on a still-CREATING body, so downstream
    # steps ran on a not-ready resource (masked-defect class, sibling of the
    # TERMINAL-BAD gate above — owner 2026-07-13: "의존 관계 앞단이 성공하지도
    # 않았는데 뒤 스텝을 진행한다고? 수리해"). Mark it so the caller classifies
    # FAILED. refire polls (failed-delete retry — timeout is their success)
    # and an explicit poll.allow_timeout escape hatch are exempt.
    # 제외 두 가지 (오너 2026-07-14):
    # (a) 타임아웃 시점 마지막 응답이 429/5xx(rate-limit·일시 전송오류)면 상태를 '못
    #     읽은' 것(unknown)이지 not-ready 확정이 아니다 — heavy-net wait-subnet 지속
    #     429가 자원 멀쩡한데 실패로 잡힌 케이스. http_client가 429를 이미 재시도하므로
    #     여기 도달 = 지속 429. 진짜 not-ready(2xx인데 CREATING)는 그대로 확정.
    # (b) gone-poll(until_status에 404 = 자원 소멸 대기)은 teardown 정리라, 캡 안에
    #     안 사라져도(예: mariadb ~90분 drain > 900s 캡) 실패로 볼 게 아니라 sweep/
    #     cleanup 백스톱에 맡긴다. masked-defect(다운스트림이 준비 안 된 자원 위 진행)
    #     는 create-side wait(field/until·non-404 until_status)에만 해당 — 그 351개는
    #     게이트 유지.
    _transient = resp.status == 429 or 500 <= resp.status < 600
    _is_gone_poll = until_status is not None and 404 in until_status
    if (not refire and not poll.get("allow_timeout")
            and not _transient and not _is_gone_poll):
        _met = None
        if until_status is not None:
            _met = resp.status in until_status
        elif field:
            _met = (_jsonpath_get(resp.body, field) if resp.body else None) in until
        if _met is False:
            _last = (resp.status if until_status is not None
                     else (_jsonpath_get(resp.body, field) if resp.body else None))
            _want = f"status {until_status}" if until_status is not None else str(until)
            print(f"  step '{step.get('name')}': poll timed out after ~{timeout:.0f}s "
                  f"without reaching {_want} (last={_last}) — ending as NOT-READY "
                  f"(caller classifies FAILED; resource never converged)")
            try:
                resp._poll_timed_out = _last
            except Exception:
                pass
    return resp


# --------------------------------------------------------------------------- #
# scheduling helpers (data-driven from dependencies.json)
# --------------------------------------------------------------------------- #
def quota_kinds_for(lifecycle_id: str) -> list[str]:
    """Quota kinds a lifecycle consumes, from dependencies.json (empty if none).
    A scheduler uses this to serialize scenarios that share a capped resource."""
    return list(DEPENDENCIES.get("quota_kinds", {}).get(lifecycle_id, []))


def active_lifecycles() -> list[dict]:
    """Enabled lifecycles, SLOWEST-FIRST.

    pytest-xdist hands tests to workers in parametrize order as they free up.
    With the long provisioners (DB clusters, VM, SKE — tens of minutes each)
    scattered through the list, two of them can land on the SAME worker
    back-to-back and run serially (field report: postgresql started only after
    mysql finished). Putting heavy/known-slow lifecycles FIRST means the
    initial worker assignment starts them all concurrently, so wall-clock
    tends to max(slow) instead of sums.
    """
    slow_markers = ("heavy-", "dns")
    # The genuinely-longest provisioners (tens of minutes each): DB clusters,
    # SKE k8s, baremetal, full VM, GPU node. These MUST grab the first xdist
    # workers so they run CONCURRENTLY — otherwise the alphabetical tie-break
    # below buries them behind lighter "heavy" lifecycles
    # (aimlops/archivestorage/backup/billingplan) and they serialize. Field:
    # run 27811864234 (-n 6) had only mysql of the 5 DB engines started 19 min
    # into the CRUD pass — the other 4 were queued behind alphabetically-earlier
    # heavy lifecycles. Promoting them to rank 0 starts all long-poles at once,
    # so the heavy phase is max(longest) not sum.
    slowest_markers = ("database-", "heavy-shared-dbaas", "-cluster-subops",
                       "container-ske", "gen-heavy-ske", "gen-wave4-asg",
                       "baremetal", "compute-virtualserver-full",
                       "mngc-gpu-node")

    def slow_rank(lc: dict) -> int:
        lid = lc["id"]
        if lc.get("heavy") and any(m in lid for m in slowest_markers):
            return 0   # longest provisioners FIRST -> concurrent on the first workers
        if lc.get("heavy"):
            return 1   # other heavy
        if any(m in lid for m in slow_markers):
            return 2
        return 3

    return sorted((lc for lc in LIFECYCLES if lc.get("enabled")),
                  key=lambda lc: (slow_rank(lc), lc["id"]))


# --------------------------------------------------------------------------- #
# the lifecycle runner
# --------------------------------------------------------------------------- #
class LifecycleSkip(Exception):
    """A lifecycle skipped for an environmental reason (quota / 417 / safety)."""


def _ensure_adopted_active(client, kind: str, rid: str, lifecycle_id: str, *,
                           timeout: float = 300.0, interval: float = 5.0) -> None:
    """Adopted 공유 자원(vpc/subnet/subnet#db)의 ACTIVE를 프로세스당 id별 1회
    보장한다. 이미 ACTIVE면 GET 1회, CREATING이면 ACTIVE까지 폴(≤timeout).
    provision의 서브넷 no-wait 반환(SCP_PROVISION_SUBNET_NOWAIT)과 짝.

    어떤 상황에서도 lifecycle을 죽이지 않는 soft 게이트: 상태 필드가 없거나
    (응답 모양 미상 — 게이트 불가), GET이 실패하거나, 타임아웃이어도 통과시켜
    이어지는 create가 실제 상태를 4xx로 표면화한다 (종전과 동일한 실패 모드)."""
    if rid in _ADOPT_ACTIVE_SEEN:
        return
    paths = {"vpc": (_VPC_CREATE_PATH, "$.vpc.state"),
             "vpc#a": (_VPC_CREATE_PATH, "$.vpc.state"),
             "vpc#b": (_VPC_CREATE_PATH, "$.vpc.state"),
             "subnet": (_SUBNET_CREATE_PATH, "$.subnet.state"),
             "subnet#db": (_SUBNET_CREATE_PATH, "$.subnet.state"),
             "tgw": (_TGW_CREATE_PATH, "$.transit_gateway.state"),
             "igw": (_IGW_CREATE_PATH, "$.internet_gateway.state")}
    if kind not in paths:
        _ADOPT_ACTIVE_SEEN.add(rid)
        return
    base, field = paths[kind]
    ok_states = ("ACTIVE", "RUNNING", "CREATED", "AVAILABLE")
    deadline = time.time() + timeout
    try:
        while True:
            resp = client.request("GET", f"{base}/{rid}", service="vpc")
            state = _capture(getattr(resp, "body", None) or {}, field)
            if state is None or str(state).upper() in ok_states:
                break
            if time.time() >= deadline:
                print(f"  [{lifecycle_id}] adopted {kind} {rid} still {state} "
                      f"after {timeout:.0f}s — proceeding (next step surfaces it)")
                break
            print(f"  [{lifecycle_id}] adopted {kind} {rid} state={state} — "
                  f"waiting ACTIVE")
            time.sleep(interval)
    except Exception as exc:  # noqa: BLE001 — soft gate, see docstring
        print(f"  [{lifecycle_id}] adopted {kind} {rid} ACTIVE gate failed "
              f"({exc}); proceeding — next step surfaces the real state")
    _ADOPT_ACTIVE_SEEN.add(rid)


def run_lifecycle(lifecycle: dict, client, cfg, *,
                  budget: _budgets.Budget | None = None,
                  resource_registry: ResourceRegistry | None = None,
                  shared_ctx: dict | None = None) -> dict:
    """Run one lifecycle's steps in order. Returns a result dict
    ``{id, status: 'passed'|'skipped'|'failed', reason?, failed_groups, created}``.

    Mirrors the pytest test's control flow but raises nothing on an
    environmental skip — instead returns ``status='skipped'``. A genuine assert
    failure (wrong status on a required step, capture miss) raises after the
    best-effort teardown, so a thin pytest entrypoint can surface it.
    """
    if not cfg.allow_mutations:
        return {"id": lifecycle["id"], "status": "skipped",
                "reason": "set SCP_ALLOW_MUTATIONS=true to run CRUD lifecycles",
                "failed_groups": [], "created": 0}
    if lifecycle.get("heavy") and not cfg.run_heavy:
        return {"id": lifecycle["id"], "status": "skipped",
                "reason": "heavy lifecycle — set SCP_RUN_HEAVY=true to run",
                "failed_groups": [], "created": 0}
    # Secret-gated lifecycles (e.g. cloud-ml needs the SCR registry credential,
    # injected via {env:...}). Without the secret(s) set on the runner, env-skip
    # instead of firing a doomed request — keeps untargeted heavy runs green.
    missing_env = [v for v in lifecycle.get("requires_env", []) if not os.environ.get(v)]
    if missing_env:
        return {"id": lifecycle["id"], "status": "skipped",
                "reason": f"requires env/secret(s) not set: {', '.join(missing_env)}",
                "failed_groups": [], "created": 0}

    budget = budget if budget is not None else _budgets.Budget()
    reg = resource_registry if resource_registry is not None else ResourceRegistry()

    service = lifecycle.get("service", "").split("/")[-1] or None
    if _oplog:
        _oplog.emit_resource("lifecycle-start", service=service or "",
                             name=lifecycle["id"], lifecycle=lifecycle["id"])
    if _cev:
        _cev.emit("lifecycle-start", lifecycle=lifecycle["id"],
                  service=service or "", heavy=bool(lifecycle.get("heavy")),
                  n_steps=len(lifecycle.get("steps", [])))

    _now = time.gmtime()
    # {unique}/{ualpha} name the resources a lifecycle creates. A bare
    # int(time.time()) only has 1-second resolution, so two lifecycles that
    # share a name prefix and start in the same second (routine under
    # pytest-xdist) generate IDENTICAL names — the second create then 400s
    # ("name already exists", run 27500363845: gen-wave2-rg vs gen-wave4-rmtags).
    # Mix in randomness so names are unique across concurrent lifecycles while
    # staying STABLE within this call (computed once here, reused by every step +
    # its cleanup). CRITICAL: keep the total length 8 chars (4 hex of the
    # low-16-bit timestamp + 4 random hex) — VPC names are capped at 20 chars and
    # "regrvpc{unique}" must fit (run 27514177331 regressed at 21 chars when this
    # was timestamp(8)+random(6)=14).
    _rand_hex = os.urandom(2).hex()                       # 4 hex chars, random
    _ts_hex = format(int(time.time()) & 0xFFFF, "04x")    # 4 hex chars, low-16-bit time
    _u = _ts_hex + _rand_hex                              # 8 hex chars total
    ctx: dict[str, str] = {
        "unique": _u,
        "ualpha": "".join(chr(ord("a") + int(c, 16)) for c in _u),  # 8 alpha chars
        "region": cfg.region,
        "today": time.strftime("%Y%m%d", _now),
        "today_plus_5y": f"{_now.tm_year + 5}{time.strftime('%m%d', _now)}",
        # ISO YYYY-MM-DD dates for endpoints that take a bounded report/metric
        # window. apigateway listreports rejects any range that exceeds 30 days
        # OR starts earlier than 30 days ago (live-confirmed 2026-06-18:
        # "Date range cannot exceed 30 days." / "Dates cannot be earlier than 30
        # days ago."), so a hardcoded calendar-year range always 400s. Use a
        # rolling 29-day window ending today — always in-bounds on both rules.
        "iso_today": time.strftime("%Y-%m-%d", _now),
        "iso_29d_ago": time.strftime("%Y-%m-%d", time.gmtime(time.time() - 29 * 86400)),
        # epoch 초 토큰 (servicewatch metric 창 등) — _fill_obj의 정수 보존
        # 치환과 함께 사용: "{epoch_now}" 단독 값은 int로 들어간다.
        "epoch_now": str(int(time.time())),
        "epoch_1h_ago": str(int(time.time()) - 3600),
        # cloudmonitoring event/v2 계열은 (a) datetime 형식(…T00:00:00.000Z)만
        # 받고 (b) queryEndDt가 미래면 400 InvalidInputValue — 라이브 이분탐색
        # 실증 2026-07-11 (date-only도, 오늘 23:59Z 끝도 400; 1시간 전 끝은 200).
        # 끝을 1시간 전으로 두면 항상 과거라 rot 없음.
        "iso_dt_29d_ago": time.strftime("%Y-%m-%dT00:00:00.000Z",
                                        time.gmtime(time.time() - 29 * 86400)),
        "iso_dt_1h_ago": time.strftime("%Y-%m-%dT%H:00:00.000Z",
                                       time.gmtime(time.time() - 3600)),
    }
    # Seed shared resources (e.g. a session-shared VPC) so {"adopt": ...} steps
    # reuse them instead of creating their own.
    if shared_ctx:
        ctx.update({k: str(v) for k, v in shared_ctx.items() if v})

    # Identity index for create->show (design A, stage 2): a create step records
    # the id it produced, keyed by its catalog endpoint key + resource_type, so an
    # id-bound GET probe resolves by IDENTITY (which create made the id) instead of
    # by capture-var string name — see _resolve_param / _probe_reads.
    produced: dict[str, str] = {}        # create_endpoint_key -> resource id
    produced_rtype: dict[str, str] = {}  # resource_type        -> resource id

    # Teardown stack of (label, method, path, service, json, group, budget_kind).
    cleanups: list[tuple] = []
    failed_groups: set = set()
    group_fail_reason: dict = {}
    reserved: dict = {}  # budget kind -> count reserved by this lifecycle
    created_count = 0
    # Per-lifecycle wall-clock budget for optional-step 4xx retries (the 20s
    # sleeps below). Guarded coverage lifecycles can have dozens of optional
    # steps that 4xx; without a cap the retry sleeps alone add ~1 min/step.
    opt_retry_left = float(os.getenv("SCP_OPT_RETRY_BUDGET_S", "240"))

    # v0.5 cross-process VPC throttle (opt-in). A held slot token per LIVE VPC
    # this lifecycle created, keyed by the VPC id so release is exact even when
    # a lifecycle creates several VPCs (e.g. vpc-peering creates two): the slot
    # is freed for the specific id deleted (own DELETE step or teardown), never
    # by popping an arbitrary token. `_pending_vpc_tok` holds a token acquired
    # just before a create but not yet bound to an id (bound at cleanup
    # registration once the create's delete-path id is known; released if the
    # create fails). No-op throughout unless SCP_VPC_SEMAPHORE=true.
    _vpc_sem = _vpc_semaphore_cfg()          # (sem, limit, timeout, poll) | None
    vpc_tokens_by_id: dict[str, str] = {}    # live VPC id -> held slot token
    _pending_vpc_tok: list[str] = []         # acquired, not yet bound to an id

    def _vpc_id_of(path: str) -> str | None:
        return path.rstrip("/").rsplit("/", 1)[-1] if _is_vpc_delete("DELETE", path) else None

    def _release_pending_vpc_tok():
        """Give back a slot acquired for a create that did not take effect."""
        if _vpc_sem is not None and _pending_vpc_tok:
            _vpc_sem[0].release(_pending_vpc_tok.pop())

    def _release_vpc_for_path(path: str):
        """Free the slot for the VPC addressed by a /v1/vpcs/{id} delete path
        (own DELETE step or teardown). No-op when off or that id isn't held."""
        if _vpc_sem is None:
            return
        vid = _vpc_id_of(path)
        tok = vpc_tokens_by_id.pop(vid, None) if vid else None
        if tok:
            _vpc_sem[0].release(tok)

    def _run_cleanup(entry):
        label, method, path, svc, cu_json, _grp, bkind = entry
        try:
            if cfg.allow_destructive:
                resp = client.request(method, path, json=cu_json, service=svc)
                # Async-state ladder: a teardown DELETE fired right after a
                # mid-chain failure often hits 409/invalid-state while the
                # resource is still EDITING/DELETING (run-2b field case: the
                # LB + TGW leaks — one-shot best-effort leaked by design).
                # Bounded retries, then report loudly instead of silently.
                attempts = int(os.getenv("SCP_CLEANUP_RETRIES", "3"))
                while (attempts > 0 and resp.status >= 400
                       and resp.status != 404
                       and (resp.status == 409
                            or "state" in (resp.raw_text or "").lower())):
                    time.sleep(float(os.getenv("SCP_CLEANUP_RETRY_INTERVAL", "20")))
                    resp = client.request(method, path, json=cu_json, service=svc)
                    attempts -= 1
                # 404 = already gone (an explicit delete step or a racing sweep
                # got there first) — success for teardown purposes.
                ok = resp.status < 400 or resp.status == 404
                print(f"  cleanup: {method} {path} -> {resp.status}"
                      + ("" if ok else f"  !! NOT deleted: {(resp.raw_text or '')[:160]}"))
                if _oplog:
                    # Emit 'deleted' only on an actually-successful delete —
                    # a 4xx here previously recorded 'deleted' and hid the leak.
                    _oplog.emit_resource("deleted" if ok else "delete-failed",
                                         path=path, service=svc or "",
                                         lifecycle=lifecycle["id"],
                                         status="cleanup" if ok else str(resp.status))
                if _cev:
                    # Console live view previously saw NOTHING from teardown
                    # cleanups (only explicit DELETE steps emitted) — the owner
                    # watched "teardown 시도 완료" with leftovers and no clue why.
                    _cev.emit("resource-deleted" if ok else "resource-delete-failed",
                              lifecycle=lifecycle["id"], path=path,
                              service=(svc or ""), status=resp.status)
        except Exception as exc:  # best-effort; report and continue
            print(f"  cleanup FAILED for {label} ({path}): {exc}")
            if _oplog:
                _oplog.emit_resource("delete-failed", path=path, service=svc or "",
                                     lifecycle=lifecycle["id"], status=str(exc)[:40])
            if _cev:
                _cev.emit("resource-delete-failed", lifecycle=lifecycle["id"],
                          path=path, service=(svc or ""), status=str(exc)[:80])
        finally:
            if bkind:  # release the reserved budget slot regardless of outcome
                budget.release(bkind)
                reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                if bkind == "vpc":   # ...and its cross-process slot (v0.5)
                    _release_vpc_for_path(path)

    def _teardown():
        for entry in reversed(cleanups):
            _run_cleanup(entry)

    def _teardown_group(grp):
        keep = []
        for entry in reversed(cleanups):
            if entry[5] == grp:
                _run_cleanup(entry)
            else:
                keep.append(entry)
        cleanups[:] = list(reversed(keep))

    try:
        # Lifecycles that import/validate a real certificate need a matching
        # body/key pair. Mint one (per process) and expose it as placeholders;
        # if the toolchain can't, skip environmentally rather than 4xx-ing.
        if lifecycle.get("needs_cert_material"):
            pem = _self_signed_pem()
            if not pem:
                raise LifecycleSkip(
                    "no certificate material (openssl unavailable) — skipping "
                    "certificatemanager import/validate lifecycle")
            ctx.update(pem)

        for step in lifecycle["steps"]:
            # Platform command channel (M2): a step boundary is the only point
            # where stopping mid-lifecycle is safe — _teardown() reclaims every
            # resource created so far (reverse order, budget slots released),
            # then the existing environmental-skip path records the outcome, so
            # pytest reports a skip rather than a crash. abort is sticky in
            # core.commands, so every remaining lifecycle skips at ITS first
            # step boundary too — after its own (empty) teardown.
            if _commands is not None:
                if _commands.should_abort_run():
                    _teardown()
                    raise LifecycleSkip(
                        f"[{lifecycle['id']}] platform command: run abort — "
                        f"remaining steps skipped after cleanup")
                if _commands.should_skip(lifecycle["id"]):
                    # UI 가시성 (오너 2026-07-11 "중단되었는지도 모르겠다"):
                    # 집행 사실을 이벤트로 남겨 로그 탭에 바로 보이게.
                    if _cev is not None:
                        _cev.emit("command-applied", lifecycle=lifecycle["id"],
                                  action="skip_scenario",
                                  note="⏹ 중단 명령 집행 — 생성 자원 정리 후 스킵")
                    _teardown()
                    raise LifecycleSkip(
                        f"[{lifecycle['id']}] platform command: scenario "
                        f"skipped after cleanup of created resources")

            grp = step.get("group")
            if grp and grp in failed_groups:
                continue  # an earlier step in this group failed — skip the rest

            # Pure ctx-transform action steps (no HTTP request). `b64_encode`
            # base64-encodes a captured value for path segments the API decodes
            # as base64 — resourcemanager {srn}/{key} path params yield a 400
            # "SRN decoding error" unless base64-encoded (knowledge/services.md).
            # The encoded value is published to ctx under step['output'] for
            # later {placeholder} substitution; the step issues no request.
            _action = step.get("action")
            if _action:
                if _action != "b64_encode":
                    raise ValueError(
                        f"[{lifecycle['id']}] unknown step action '{_action}' "
                        f"(step '{step.get('name')}')")
                raw = _fill(step.get("input", ""), ctx)
                if "{" in raw:  # input placeholder unresolved (soft-capture miss)
                    if step.get("optional"):
                        continue
                    raise LifecycleSkip(
                        f"[{lifecycle['id']}] b64_encode input "
                        f"'{step.get('input')}' unresolved — cannot encode "
                        f"(step '{step.get('name')}')")
                ctx[step["output"]] = base64.b64encode(raw.encode()).decode()
                continue

            # Shared-resource adoption: reuse a session-shared resource instead of
            # creating/deleting our own (so heavy lifecycles share one VPC rather
            # than each consuming a slot against the 5-VPC cap). NO-OP when the
            # shared id is absent — the lifecycle then self-creates as before.
            _adopt = step.get("adopt")
            if _adopt:
                _shared_val = ctx.get(_ADOPT_SHARED.get(_adopt, ""))
                # the DB-lane subnet falls back to the main shared subnet when a
                # provisioner predates the two-subnet design (graceful degrade).
                if not _shared_val and _adopt == "subnet#db":
                    _shared_val = ctx.get("shared_subnet_id")
                # IB-049: a {"adopt":"vpc"} create that finds NO shared id while
                # running under pytest-xdist must NOT self-create. With N worker
                # processes each falling back to self-create, a failed shared-VPC
                # provision turns into an N-way `POST /v1/vpcs` race that saturates
                # the 5-VPC account cap (IB-047 confirmed cascade). Degrade instead
                # to an environmental skip — "all adopters skip" is recoverable
                # (the next run's pre-reclaim + provision lets them adopt). Gated
                # STRICTLY on the xdist worker env var so the single-process
                # fallback (test_no_shared_vpc_falls_back_to_self_create) keeps
                # self-creating exactly as before.
                # base kind로 판정 — vpc#a/vpc#b(네트워킹 공유 VPC)도 같은
                # 다중워커 self-create 레이스 클래스다 (게다가 A/B는 CIDR이
                # 고정이라 동시 self-create는 즉시 overlap 400).
                if (not _shared_val and _adopt.split("#", 1)[0] == "vpc"
                        and step.get("method", "").upper() == "POST"
                        and os.environ.get("PYTEST_XDIST_WORKER")):
                    _teardown()
                    raise LifecycleSkip(
                        f"[{lifecycle['id']}] no shared VPC and running under "
                        f"xdist worker {os.environ['PYTEST_XDIST_WORKER']} — "
                        f"skipping adopter instead of self-creating a VPC "
                        f"(IB-049: avoid the multi-worker create-VPC race that "
                        f"saturates the account cap)")
                if _shared_val:
                    _m = step.get("method", "").upper()
                    if _m == "POST":  # adopt: skip the create, seed its capture vars
                        # ACTIVE 1회 보장 게이트 (2026-07-13): provision이 서브넷
                        # ACTIVE 대기를 생략하고 반환할 수 있으므로(런 머리 4.3분
                        # 유휴 제거 — run-543a 실측: 같은 VPC 서브넷 2개는 동시
                        # 생성해도 백엔드가 ACTIVE 전이를 직렬화, 128s/238s)
                        # CREATING 상태의 id가 adopt될 수 있다. 첫 사용자가 여기서
                        # ACTIVE까지 폴하고, 이미 ACTIVE면 GET 1회로 끝난다
                        # (프로세스당 id별 1회 캐시). 실패해도 soft — 이어지는
                        # create가 4xx로 표면화한다 (종전과 동일한 실패 모드).
                        _ensure_adopted_active(client, _adopt, str(_shared_val),
                                               lifecycle["id"])
                        # 캡처 변수를 공유 id로 시딩하되 소스 JSONPath가 자원의
                        # 자기 id(`.id`)를 가리키는 것만 — 공유 id는 자원 id이지
                        # account_id/name 같은 하위필드가 아니다. capture_soft도
                        # 포함(IGW create는 400 관용이라 id를 capture_soft로 잡음:
                        # internet_gateway_id/owned_igw_id=$.internet_gateway.id).
                        # `.id` 필터가 vpc-peering의 account_id(=$.vpc.account_id)와
                        # gen-wave5-fw의 net_b_vpc_name(=$.vpc.name)을 올바로 제외.
                        for _capkey in ("capture", "capture_soft"):
                            for _v, _src in (step.get(_capkey) or {}).items():
                                # 이미 ctx에 있는 캡처 변수는 보존 — shared_ctx가
                                # 시딩한 부가 정보를 공유 ID로 덮어쓰면 안 된다.
                                if _v not in ctx and str(_src).endswith(".id"):
                                    ctx[_v] = _shared_val
                        print(f"  [{lifecycle['id']}] adopting shared {_adopt}="
                              f"{_shared_val} (skip create '{step['name']}')")
                        continue
                    if _m == "DELETE":  # retain shared resource — fixture tears it down
                        print(f"  [{lifecycle['id']}] retaining shared {_adopt} "
                              f"(skip delete '{step['name']}')")
                        continue
                    if _m == "PUT":  # 공유 자원을 mutate하지 않음 — 다른 adopter가
                        # 같은 자원을 보므로 set/update는 skip (2026-07-13 공유 TGW).
                        # 커버리지는 소유 lifecycle(예: children)이 담당.
                        print(f"  [{lifecycle['id']}] skipping '{step['name']}' "
                              f"(shared {_adopt} retained, not mutated)")
                        continue
                    if _m == "GET":  # e.g. wait-<x>-gone — pointless on a retained
                        # shared resource (it never 404s); skip to avoid a long poll.
                        print(f"  [{lifecycle['id']}] skipping '{step['name']}' "
                              f"(shared {_adopt} retained, not deleted)")
                        continue

            step_service = step.get("service") or service
            if step.get("wait"):
                time.sleep(float(step["wait"]))

            if step.get("probe_reads") is not None:
                # AUTO-SEED from the full capture context (platform-level
                # create->show, 2026-06-18): probe-reads fires every catalog GET
                # whose path-params are all in the seed, so seeding it with EVERY
                # captured attribute-id (the ctx) — not just a hand-listed subset —
                # exercises the read of every child resource this lifecycle just
                # created, for all services at once. (Historically each lifecycle
                # hand-seeded one id, so nested child-id GETs were never probed; see
                # docs/working/plans/COVERAGE-GETID-PLAN.md §7.) Explicit probe_reads entries still
                # apply ON TOP — for name-addressed segments that are not captured
                # vars (e.g. stage_name: "stg{unique}"). Only scalar ctx values are
                # usable as path-param substitutes; unfilled placeholders are dropped.
                auto = {k: v for k, v in ctx.items()
                        if isinstance(v, (str, int)) and v != ""}
                explicit = {k: _fill(v, ctx)
                            for k, v in (step.get("probe_reads") or {}).items()}
                mapping = {**auto, **explicit}
                mapping = {k: v for k, v in mapping.items() if "{" not in str(v)}
                _probe_reads(client, mapping, step_service, produced, produced_rtype)
                continue

            path = _fill(step["path"], ctx)
            body = _apply_b64_fields(step, _fill_obj(step.get("json"), ctx))

            # Kernel: stamp create bodies with owner/run/ttl tags.
            if _is_create(step):
                body = _inject_owner_tags(body, axis="regression")

            if step.get("destructive") and not cfg.allow_destructive:
                # Mirror the test's xfail: leave the resource, signal needs-cleanup.
                _teardown()
                raise LifecycleSkip(
                    f"destructive step '{step['name']}' skipped (set "
                    f"SCP_ALLOW_DESTRUCTIVE=true). Manual cleanup needed: {path}")

            # Kernel: consult the budget BEFORE a quota-bound create. If the cap
            # is hit, treat it as an environmental skip (same class as the live
            # ExceedMax response) instead of provoking the API into a 4xx.
            bkind = _budget_kind_for_path(_fill(step.get("path", ""), ctx)) \
                if _is_create(step) else None
            if bkind and not budget.reserve(bkind):
                if step.get("optional"):
                    reason = (f"{step['name']} -> budget '{bkind}' exhausted "
                              f"(available={budget.available(bkind)})")
                    print(f"  optional step '{step['name']}' (group={grp}) hit a "
                          f"budget limit -> skipping group. {reason}")
                    if grp:
                        failed_groups.add(grp)
                        group_fail_reason.setdefault(grp, reason)
                        _teardown_group(grp)
                    continue
                _teardown()
                raise LifecycleSkip(
                    f"[{lifecycle['id']}] budget '{bkind}' exhausted before step "
                    f"'{step['name']}' (available={budget.available(bkind)})")
            if bkind:
                reserved[bkind] = reserved.get(bkind, 0) + 1

            # v0.5 throttle: the in-process reserve above is per-worker; for a
            # VPC self-create ALSO acquire a run-wide cross-process slot, BLOCKING
            # until one frees (≤ cap concurrent VPCs across all xdist workers).
            # On timeout (peers held the cap the whole window) treat it as an
            # environmental skip — same class as a budget-exhausted skip, never a
            # failure (Hard Rule 6). No-op unless SCP_VPC_SEMAPHORE=true.
            if bkind == "vpc" and _vpc_sem is not None:
                _sem, _lim, _to, _poll = _vpc_sem
                _tok = _sem.acquire(_lim, timeout=_to, poll=_poll)
                if _tok is None:
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                    _teardown()
                    raise LifecycleSkip(
                        f"[{lifecycle['id']}] VPC quota semaphore: no slot within "
                        f"{_to:.0f}s (limit={_lim}) — skipping rather than racing "
                        f"the account VPC cap")
                _pending_vpc_tok.append(_tok)   # bound to its id at cleanup reg

            if _cev:
                _cev.emit("step-start", lifecycle=lifecycle["id"],
                          step=step.get("name", ""), method=step["method"],
                          path=step.get("path", path), service=step_service or "",
                          optional=bool(step.get("optional")))
            try:
                resp = _run_step(client, step, path, body, step_service, ctx,
                                 lifecycle_id=lifecycle["id"])

                # Optional setter steps routinely race async provisioning (a
                # DBaaS cluster is busy applying the PREVIOUS setter -> 400
                # invalid-state). When 4xx is NOT an expected status for the
                # step, give it a few spaced retries before classifying — this
                # converts transient called-only (C2) into verified (C3).
                # Capped twice: a path that still carries an unresolved {token}
                # can never turn 2xx (skip retries entirely), and the cumulative
                # retry sleep per lifecycle is bounded by SCP_OPT_RETRY_BUDGET_S
                # so guarded coverage lifecycles don't burn ~1 min per 4xx step.
                _nontransient_4xx = any(tok in (resp.raw_text or "").lower()
                                        for tok in ("max-count-exceed", "quota"))
                if (step.get("optional") and not step.get("retry_on_status")
                        and resp.status in (400, 409, 429)
                        and resp.status not in (_as_status_list(step.get("expect_status")) or [200])
                        # 쿼터/최대개수 초과는 시간이 풀어주지 않는 클래스 —
                        # 사다리(3×20s) 낭비 금지 (svc-opt 2026-07-11 실측:
                        # private-dns max-count-exceed에 87s 소진)
                        and not _nontransient_4xx
                        and "{" not in path):
                    for _attempt in range(3):
                        if opt_retry_left < 20:
                            print(f"  optional-retry budget exhausted "
                                  f"(SCP_OPT_RETRY_BUDGET_S) — recording "
                                  f"'{step['name']}' as-is")
                            break
                        opt_retry_left -= 20
                        time.sleep(20)
                        resp = _run_step(client, step, path, body, step_service,
                                         ctx, lifecycle_id=lifecycle["id"])
                        if resp.status not in (400, 409, 429):
                            break
            except MutationBlocked as exc:
                if bkind:  # roll back the reservation we just took
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                    if bkind == "vpc":   # the create did not take effect
                        _release_pending_vpc_tok()
                _teardown()
                raise LifecycleSkip(str(exc))

            # record the step call itself for coverage/timing
            _cat = categorize(resp.status, resp.raw_text or "")
            _ems = getattr(resp, "elapsed_ms", None)
            if _cev and _cev.enabled():
                # console2 API-tab detail (coverage analysis): enrich step-end with
                # the resolved query params, the request body, and a response
                # snippet so a clicked API row can show what was actually sent vs
                # received. Additive + env-gated — `_cev.enabled()` is the same
                # SCP_CONSOLE_EVENTS check emit() makes, so none of this payload is
                # even built (and nothing is written) unless the console sink is
                # configured: zero cost + zero behaviour change when unset. All
                # values are TRUNCATED so the event stream stays small; these are
                # test-resource bodies (owner-tagged synthetic ids), not secrets.
                _cev_params = _fill_obj(step.get("params"), ctx) or None
                _cev_req = None
                if body is not None:
                    try:
                        _cev_req = json.dumps(body, ensure_ascii=False, default=str)[:400]
                    except Exception:  # noqa: BLE001 — never let detail break a run
                        _cev_req = str(body)[:400]
                _cev_resp = (resp.raw_text or "")[:400] or None
                _cev.emit("step-end", lifecycle=lifecycle["id"],
                          step=step.get("name", ""), method=step["method"],
                          path=step.get("path", path), service=step_service or "",
                          status=resp.status, category=_cat, elapsed_ms=_ems,
                          params=_cev_params, req_body=_cev_req,
                          resp_snippet=_cev_resp)
            _note = ""
            if _cat == results.FAIL or (
                    _cat == results.SOFT and step["method"].upper() != "GET"
                    and resp.status >= 400):
                _note = (resp.raw_text or "")[:400]
            _record_smoke(resp.status, _cat,
                          f"{lifecycle['id']}:{step['name']}", step["method"],
                          step.get("path", path), _ems, note=_note)
            # ALSO record WRITE steps under their real catalog endpoint key so the
            # dashboard surfaces their HTTP status + response time per endpoint, the
            # same way GETs do (reads already arrive under the catalog key via smoke
            # / probe_reads). Records the ACTUAL response — incl. a 4xx from an
            # isolated optional write — which is exactly the signal we want shown.
            # Also credit an id-bound GET step that carries explicit query `params`
            # under its catalog key: such reads (e.g. queueservice getqueueattributes,
            # which REQUIRES attributes+name) are unreachable by the bare probe, so
            # this explicit step is the ONLY place that exercises the endpoint.
            if step["method"].upper() != "GET" or "params" in step:
                _ck = _catalog_key_for(step["method"], step.get("path", ""), step_service)
                if _ck:
                    _record_smoke(resp.status, _cat, _ck, step["method"],
                                  step.get("path", path), _ems, note=_note)
            if (_oplog and step["method"].upper() == "DELETE"
                    and 200 <= resp.status < 300):
                _oplog.emit_resource("deleted", path=path,
                                     service=step_service or "",
                                     lifecycle=lifecycle["id"])
            # console2 local live view: mark the resource deleted on a successful
            # DELETE step so 자원 advances create→test→delete for live runs too.
            # Env-gated via _cev (no-op when SCP_CONSOLE_EVENTS unset).
            if (_cev and step["method"].upper() == "DELETE"
                    and 200 <= resp.status < 300):
                _cev.emit("resource-deleted", lifecycle=lifecycle["id"],
                          resource_type=(step_service or ""),
                          service=(step_service or ""), path=path)

            expected = _as_status_list(step.get("expect_status")) or [200]
            _txt = resp.raw_text or ""
            _tl = _txt.lower()
            # Account quota caps are environmental, not regressions. SCP uses
            # several shapes: networking "max-count-exceed"/"ExceedMax*", and
            # service quotas like "scp-container-registry.quota.value.exceeded"
            # ("Exceeded the service quota limit"). Match them broadly.
            _is_quota = ("exceed-max-count" in _txt or "ExceedMax" in _txt
                         or "max-count-exceed" in _txt
                         or "quota.value.exceeded" in _tl
                         or "exceeded the service quota" in _tl
                         or (".quota." in _tl and "exceed" in _tl))
            _is_gateway_block = (resp.status == 417 and (
                "Request Rejected" in _txt or "request was blocked" in _txt
                or "Support ID" in _txt))
            # A dependency resource we created was concurrently removed (e.g. a
            # cross-run prefix-sweep deleting our subnet): the API reports the
            # parent as not-active/DELETING. That is environmental interference,
            # not a regression — skip rather than fail.
            _is_dep_gone = (resp.status == 400 and (
                "not-active-state" in _tl or "notactivestate" in _tl
                or "(deleting)" in _tl
                # scp-network.subnet.state.invalid-format: "Subnet ... has
                # invalid state(state : DELETING)" — a dependency is mid-delete
                # (e.g. the shared subnet being torn down, or a racing sweep).
                or "state : deleting" in _tl or "state: deleting" in _tl
                or ("state.invalid-format" in _tl and "deleting" in _tl)))
            # Idempotent re-assertion on a SHARED resource: a verify/setup step
            # that re-adds something already present has ACHIEVED its desired
            # state, so it is SUCCESS, not a failure. Today's case: several
            # parallel lifecycles each add the same secondary CIDR to the ONE
            # shared VPC -> the first wins, the rest get
            # scp-network.vpc.cidr-already-in-use. Only honoured when the step
            # captures nothing (a pure verify/setup) so we never mask a create
            # whose id we still need.
            _is_already_present = (resp.status not in expected
                                   and not step.get("capture")
                                   and "cidr-already-in-use" in _tl)
            if resp.status not in expected and (_is_quota or _is_gateway_block or _is_dep_gone):
                if bkind:  # the create did not take effect — give the slot back
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                    if bkind == "vpc":   # the create did not take effect
                        _release_pending_vpc_tok()
                if step.get("optional"):
                    reason = f"{step['name']} -> {resp.status} (env): {resp.raw_text[:300]}"
                    print(f"  optional step '{step['name']}' (group={grp}) hit an "
                          f"environmental limit -> skipping group. {resp.raw_text[:200]}")
                    if grp:
                        failed_groups.add(grp)
                        group_fail_reason.setdefault(grp, reason)
                        _teardown_group(grp)
                    continue
                _teardown()
                raise LifecycleSkip(
                    f"[{lifecycle['id']}] environmental limit at step "
                    f"'{step['name']}': {resp.raw_text[:200]}")

            status_ok = resp.status in expected or _is_already_present
            # 폴이 terminal-bad(ERROR/FAILED)로 끝났으면 2xx라도 성공이 아니다 —
            # 종전엔 200+ERROR가 expect [200]을 조용히 통과해 다음 스텝이 죽은
            # 자원 위에서 계속 진행됐다 (masked-defect 클래스). optional/group
            # 스텝은 아래 기존 분기로 그룹-스킵, 필수 스텝은 명확한 사유로 실패.
            _tbad = getattr(resp, "_terminal_bad", None)
            if _tbad is not None and status_ok:
                print(f"  step '{step['name']}': state '{_tbad}' = terminal-bad "
                      f"— classifying as FAILED despite HTTP {resp.status}")
                status_ok = False
            # 폴이 until/until_status를 못 채우고 타임아웃했으면 2xx라도 성공이
            # 아니다 — 자원이 요구 상태에 도달하지 못했으므로 뒤 스텝이 준비 안 된
            # 자원 위에서 진행되면 안 된다 (terminal-bad의 자매 결함, owner 2026-07-13).
            _ptimeout = getattr(resp, "_poll_timed_out", None)
            if _ptimeout is not None and status_ok:
                print(f"  step '{step['name']}': poll never reached its wait "
                      f"condition (last={_ptimeout}) — classifying as FAILED "
                      f"despite HTTP {resp.status}")
                status_ok = False
            if _is_already_present:
                print(f"  step '{step['name']}' -> {resp.status} "
                      f"already-present (idempotent on shared resource) "
                      f"-> treating as success")
            if status_ok:
                for var, expr in step.get("capture", {}).items():
                    if _capture(resp.body, expr) is None:
                        status_ok = False
                        break
            if not status_ok and step.get("optional"):
                if bkind:  # creation failed — release the reserved slot
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                    if bkind == "vpc":   # the create did not take effect
                        _release_pending_vpc_tok()
                reason = f"{step['name']} -> {resp.status}: {resp.raw_text[:400]}"
                print(f"  optional step '{step['name']}' (group={grp}) failed "
                      f"-> {resp.status}; skipping group. {resp.raw_text[:200]}")
                if grp:
                    failed_groups.add(grp)
                    group_fail_reason.setdefault(grp, reason)
                    _teardown_group(grp)
                continue
            assert ((resp.status in expected or _is_already_present)
                    and _tbad is None and _ptimeout is None), (
                f"[{lifecycle['id']}] step '{step['name']}' "
                f"{step['method']} {path} -> {resp.status}, expected {expected}"
                + (f" · polled state '{_tbad}' is TERMINAL-BAD (resource will "
                   f"never converge)" if _tbad is not None else "")
                + (f" · poll timed out at '{_ptimeout}' without reaching its wait "
                   f"condition (resource not ready)" if _ptimeout is not None else "")
                + f"\n{resp.raw_text[:500]}")

            # v0.5 throttle: the happy path deletes its VPC via its OWN step (not
            # teardown), so free the cross-process slot here too — otherwise a
            # created-then-deleted VPC would leak its slot for the whole run.
            _release_vpc_for_path(path)
            # …그리고 IN-PROCESS budget도 반납. 종전엔 cross-process sem만 풀어서,
            # native 러너의 공유 budget은 self-create VPC를 created→deleted 후에도
            # 예약을 런 내내 붙잡았다(오너 2026-07-14 실측: 자원 다 지웠는데 "예약
            # 5·여유 0" 안 풀림 → 이후 self-create가 슬롯을 못 얻음). VPC 자체 삭제가
            # 성공하면 그 lifecycle이 잡은 vpc 예약 1개를 반납한다. adopter는 delete가
            # adopt-skip돼 여기 안 오므로 reserved["vpc"]=0 → 무영향.
            if _vpc_id_of(path) and reserved.get("vpc", 0) > 0:
                budget.release("vpc")
                reserved["vpc"] = max(0, reserved.get("vpc", 0) - 1)

            for var, expr in step.get("capture", {}).items():
                val = _capture(resp.body, expr)
                assert val is not None, (
                    f"could not capture '{var}' via {expr!r} from {step['name']} "
                    f"response\n{resp.raw_text[:500]}")
                ctx[var] = str(val)

            for var, expr in step.get("capture_soft", {}).items():
                val = _capture(resp.body, expr)
                if val is None:
                    print(f"  soft-capture '{var}' via {expr!r} found nothing "
                          f"from '{step['name']}' — dependent probe(s) skipped")
                    continue
                ctx[var] = str(val)

            # Identity registration (design A, stage 2): if this step is the create
            # that the sidecar names as a `produced_by` for some id-bound GET, record
            # the id it produced — keyed by catalog key AND resource_type — so the
            # auto-probe resolves that GET by identity, not by capture-var name.
            _skey = _catalog_key_for(step.get("method"), step.get("path"), step_service)
            if _skey and _skey in _PRODUCER_OF:
                _rt, _cap = _PRODUCER_OF[_skey]
                _idv = _capture(resp.body, _cap) if _cap else None
                if _idv is None:   # fall back to a value the lifecycle just captured
                    for _v in step.get("capture", {}):
                        if _v in ctx:
                            _idv = ctx[_v]
                            break
                if _idv is not None:
                    produced[_skey] = str(_idv)
                    if _rt:
                        produced_rtype[_rt] = str(_idv)

            # Register teardown + track in the kernel registry for the freshly
            # created resource (deletes only on a later failure; the happy path
            # deletes via its own steps).
            #
            # 2026-07-08 gate: only when the create ACTUALLY succeeded (real 2xx).
            # Tolerant coverage steps (e.g. apigw set-resource-policy allowing
            # 400~500 to ride past PF-19) used to register cleanup + a console
            # resource row even when nothing was created — the sweep's delete
            # then 404s harmlessly, but the 자원 화면 showed phantom rows
            # (owner screenshot: RESOURCE_ID 빈 값). _is_already_present also
            # skips: the resource pre-existed, this step created nothing new.
            cu = step.get("cleanup")
            if cu and not (200 <= resp.status < 300):
                cu = None
                if bkind == "vpc" and _pending_vpc_tok:
                    _release_pending_vpc_tok()   # 미생성 — 슬롯 누수 방지
            if cu:
                created_count += 1
                cu_path = _fill(cu["path"], ctx)
                cu_svc = cu.get("service") or step_service
                cleanups.append((step["name"], cu["method"], cu_path, cu_svc,
                                 _fill_obj(cu.get("json"), ctx), grp, bkind))
                # v0.5: bind the slot acquired before this VPC create to the now
                # known VPC id, so its release (own DELETE or teardown) is exact.
                if bkind == "vpc" and _pending_vpc_tok:
                    _vid = _vpc_id_of(cu_path)
                    if _vid:
                        vpc_tokens_by_id[_vid] = _pending_vpc_tok.pop()
                    else:   # unbindable cleanup shape — free it rather than leak
                        _release_pending_vpc_tok()
                # crash-safe manifest entry for the reconciler. rid = the first
                # capture that is PLAUSIBLY an id — a keypair create captures only
                # private_key, and that PEM blob became the tracked resource_id in
                # the manifest/live-event/oplog (2026-07-08 자원 탭 목격: 키 원문
                # 노출). Multiline or huge values are never ids → skip them; when
                # no id-like capture exists fall back to the create body's name
                # (the actual identity of name-addressed resources like keypair).
                rid = ""
                for v in step.get("capture", {}):
                    val = ctx.get(v, "")
                    if not val or "\n" in val or len(val) > 200:
                        continue
                    rid = val
                    break
                if not rid and isinstance(body, dict):
                    rid = str(body.get("name") or "")
                if not rid:
                    # last fallback (2026-07-08): the cleanup path's tail segment
                    # (e.g. …/stages/dev -> "dev") — an addressable identity for
                    # capture-less children; never an unfilled {token}.
                    _tail = cu_path.rstrip("/").rsplit("/", 1)[-1]
                    if _tail and "{" not in _tail:
                        rid = _tail
                reg.track(ResourceRecord(
                    service=cu_svc or "", delete_path=cu_path, resource_id=rid,
                    kind=bkind or step["name"], parent=grp))
                # console2 local live view: surface the REAL tracked resource id
                # (same shape simulate emits synthetically). Env-gated via _cev —
                # a single os.environ.get + return when SCP_CONSOLE_EVENTS unset,
                # so this is a no-op (zero behaviour change) outside console2.
                if _cev:
                    # name included (same value the oplog 'created' event carries)
                    # so console2's /runtime mine-attribution can fall back to
                    # name-matching loggingaudit spans whose resource_id is absent.
                    _cev.emit("resource-tracked", lifecycle=lifecycle["id"],
                              resource_id=rid, resource_type=(cu_svc or ""),
                              service=(cu_svc or ""), path=cu_path,
                              name=(body or {}).get("name", "")
                              if isinstance(body, dict) else "")
                if _oplog:
                    _parent = ""
                    if isinstance(body, dict):
                        _parent = str(body.get("subnet_id")
                                      or body.get("vpc_id") or "")
                        if "{" in _parent:   # unresolved placeholder
                            _parent = ""
                    _oplog.emit_resource(
                        "created", path=cu_path, service=cu_svc or "",
                        name=(body or {}).get("name", "") if isinstance(body, dict) else "",
                        res_id=rid, lifecycle=lifecycle["id"], parent=_parent)
    except LifecycleSkip as exc:
        # Invariant: a slot acquired for a create that never bound to a VPC id
        # (e.g. raised between acquire and cleanup registration) is freed on any
        # lifecycle exit — no run-wide leak. Idempotent (no-op when empty).
        _release_pending_vpc_tok()
        if _oplog:
            _oplog.emit_resource("lifecycle-end", service=service or "",
                                 name=lifecycle["id"], lifecycle=lifecycle["id"],
                                 status="skipped")
            _oplog.flush_resources()
        if _cev:
            _cev.emit("lifecycle-end", lifecycle=lifecycle["id"],
                      status="skipped", reason=str(exc))
        return {"id": lifecycle["id"], "status": "skipped", "reason": str(exc),
                "failed_groups": sorted(failed_groups), "created": created_count}
    except Exception as exc:
        _release_pending_vpc_tok()   # same invariant on the failure path
        print(f"\n[{lifecycle['id']}] failed — attempting teardown of created resources:")
        _teardown()
        return _finish(lifecycle, "failed", failed_groups, group_fail_reason,
                       created_count, reason=str(exc), raised=exc)

    return _finish(lifecycle, "passed", failed_groups, group_fail_reason, created_count)


def _finish(lifecycle, status, failed_groups, group_fail_reason, created, *,
            reason=None, raised=None):
    if _oplog:
        _oplog.emit_resource("lifecycle-end",
                             service=lifecycle.get("service", "").split("/")[-1],
                             name=lifecycle["id"], lifecycle=lifecycle["id"],
                             status=status)
        _oplog.flush_resources()
    if _cev:
        _cev.emit("lifecycle-end", lifecycle=lifecycle["id"], status=status,
                  failed_groups=sorted(failed_groups), reason=reason)
    if failed_groups:
        import warnings
        for g in sorted(failed_groups):
            warnings.warn(f"[{lifecycle['id']}] group '{g}' skipped: "
                          f"{group_fail_reason.get(g, '?')}")
    if raised is not None:
        # propagate the genuine failure so a pytest entrypoint fails the run
        raise raised
    return {"id": lifecycle["id"], "status": status,
            "reason": reason, "failed_groups": sorted(failed_groups),
            "created": created}


def run_all(client, cfg, *, budget: _budgets.Budget | None = None,
            resource_registry: ResourceRegistry | None = None) -> list[dict]:
    """Run every enabled lifecycle, sharing one Budget + ResourceRegistry so
    quota reservations and the teardown manifest span the whole run."""
    budget = budget if budget is not None else _budgets.Budget()
    reg = resource_registry if resource_registry is not None else ResourceRegistry()
    out = []
    for lc in active_lifecycles():
        out.append(run_lifecycle(lc, client, cfg, budget=budget, resource_registry=reg))
    return out


def provision_shared_vpc(client, cfg, *, resource_registry: ResourceRegistry | None = None,
                         need_db_subnet: bool = True,
                         wait_subnets_active: bool = False,
                         need_net_vpcs=False, need_tgw: bool = False,
                         need_igw: bool = False):
    """Create ONE VPC + ONE subnet (both ACTIVE) for the heavy/ADOPT-class
    lifecycles to ADOPT, so they don't each create their own against the 5-VPC
    cap (knowledge/vpc-scheduling-strategy.md).

    Returns ``(shared_ctx, teardown)`` where ``shared_ctx`` is
    ``{"shared_vpc_id": <id>, "shared_subnet_id": <id>}`` (subnet key omitted if
    the subnet could not be created — adopters then self-create their own subnet
    under the shared VPC), or ``{}`` if nothing could be provisioned (callers
    then fall back to per-lifecycle self-create). ``teardown()`` deletes the
    subnet THEN the VPC at session end. Both are owner/run/ttl-tagged so the
    reconciler sweep reclaims them even if teardown is skipped.

    ENV-AWARE: if ``SCP_SHARED_VPC_ID`` (and optionally ``SCP_SHARED_SUBNET_ID``)
    are set, ADOPT those already-live ids — no create, teardown is a no-op (the
    provisioner process owns teardown). This lets pytest-xdist workers all adopt
    the SAME live infra provisioned once out-of-band (shared_infra --provision).

    No-op ``({}, noop)`` unless mutations are allowed.
    """
    import os
    noop = ({}, lambda: None)

    # 1) ENV adoption — ids already provisioned out-of-band (xdist / CI). Adopt
    #    them; never create/teardown here (the provisioner owns the lifecycle).
    env_vpc = os.environ.get(_ENV_SHARED_VPC, "").strip()
    if env_vpc:
        ctx = {"shared_vpc_id": env_vpc}
        env_sub = os.environ.get(_ENV_SHARED_SUBNET, "").strip()
        if env_sub:
            ctx["shared_subnet_id"] = env_sub
        env_db_sub = os.environ.get(_ENV_SHARED_DB_SUBNET, "").strip()
        if env_db_sub:
            ctx["shared_db_subnet_id"] = env_db_sub
        for env_key, ctx_key in ((_ENV_SHARED_NET_VPC_A, "shared_net_vpc_a_id"),
                                 (_ENV_SHARED_NET_VPC_B, "shared_net_vpc_b_id"),
                                 (_ENV_SHARED_NET_VPC_A_NAME, "net_a_vpc_name"),
                                 (_ENV_SHARED_NET_VPC_B_NAME, "net_b_vpc_name"),
                                 (_ENV_SHARED_TGW, "shared_tgw_id"),
                                 (_ENV_SHARED_IGW, "shared_igw_id")):
            v = os.environ.get(env_key, "").strip()
            if v:
                ctx[ctx_key] = v
        print(f"  adopting pre-provisioned shared VPC={env_vpc}"
              f"{' subnet=' + env_sub if env_sub else ''}"
              f"{' db-subnet=' + env_db_sub if env_db_sub else ''}"
              f"{' net-a=' + ctx['shared_net_vpc_a_id'] if ctx.get('shared_net_vpc_a_id') else ''}"
              f"{' net-b=' + ctx['shared_net_vpc_b_id'] if ctx.get('shared_net_vpc_b_id') else ''}"
              f" (env)")
        return ctx, (lambda: None)

    if not cfg.allow_mutations:
        return noop
    reg = resource_registry if resource_registry is not None else ResourceRegistry()
    uniq = format(int(time.time()), "x")
    body = _inject_owner_tags({
        # 'regrvpc' prefix (not 'regrshared') so the reconciler's VPC sweep AND
        # its LB/NAT-by-vpc_id sweep (name_prefixes=('regr','zznet')) reclaim
        # this shared VPC + its children even if a VPC list response omits tags.
        # IB-051 (Wave E): the SCP /v1/vpcs API enforces name length 3..20; the
        # old 'regrvpcshared'+8-hex name was 21 chars -> POST returned HTTP 400
        # ValidationError ("VPC name should have 3 to 20 digits long ...") on
        # every attempt, so no SCP_SHARED_VPC_ID was ever exported and all
        # {"adopt":"vpc"} lifecycles IB-049-skipped. Shortened the stem to
        # 'regrvpcsh' (9) so 'regrvpcsh'+8-hex == 17 chars stays under the cap
        # while keeping the 'regr' family root the reconciler matches on.
        "name": f"regrvpcsh{uniq}", "description": "API regression shared VPC",
        "cidr": _SHARED_VPC_CIDR, "tags": [],
    }, axis="regression")
    create = {"name": "create-shared-vpc", "method": "POST", "service": "vpc"}
    resp = _run_step(client, create, _VPC_CREATE_PATH, body, "vpc", {})
    if resp.status not in (200, 201, 202) or not resp.body:
        print(f"  shared-VPC provision failed ({resp.status}); heavy lifecycles "
              f"will self-create. {(resp.raw_text or '')[:200]}")
        return noop
    vpc_id = _capture(resp.body, "$.vpc.id")
    if not vpc_id:
        return noop
    vpc_id = str(vpc_id)

    # 네트워킹 공유 VPC A/B (오너 설계 2026-07-13): peering의 두 VPC를 상주
    # 프로비저닝해 vip-nat(A)·fw/DC(B)가 그 안에서 테스트한다. 메인 VPC의
    # ACTIVE 폴 **앞**에서 생성을 발행해 세 VPC의 전이가 겹치게 한다.
    net_ids: dict[str, tuple[str, str]] = {}   # tag -> (vpc_id, name)
    # net_tags: 만들 네트워킹 공유 VPC. True → {'a','b'}(하위호환), iterable → 그 태그만,
    # False/빈 것 → 없음 (오너 2026-07-13 세분화: net-A는 vpc#a 사용자, net-B는 vpc#b/
    # peering 사용자 있을 때만 — peering은 둘 다 가지므로 자동으로 A·B, vip-nat만이면 A뿐).
    _net_tags = ({"a", "b"} if need_net_vpcs is True
                 else set(need_net_vpcs) if need_net_vpcs else set())
    if _net_tags:
        for tag, cidr in (("a", _NET_VPC_A_CIDR), ("b", _NET_VPC_B_CIDR)):
            if tag not in _net_tags:
                continue
            nname = f"regrvpcn{tag}{uniq}"     # 'regrvpcna'+8hex = 17 ≤ 20 (IB-051)
            nbody = _inject_owner_tags({
                "name": nname, "description": f"API regression shared net VPC {tag.upper()}",
                "cidr": cidr, "tags": [],
            }, axis="regression")
            ncreate = {"name": f"create-shared-net-vpc-{tag}", "method": "POST",
                       "service": "vpc"}
            nresp = _run_step(client, ncreate, _VPC_CREATE_PATH, nbody, "vpc", {})
            nid = None
            if nresp.status in (200, 201, 202) and nresp.body:
                nid = _capture(nresp.body, "$.vpc.id")
            if nid:
                nid = str(nid)
                reg.track(ResourceRecord(service="vpc",
                                         delete_path=f"{_VPC_CREATE_PATH}/{nid}",
                                         resource_id=nid, kind="vpc",
                                         parent="shared-net"))
                net_ids[tag] = (nid, nname)
                print(f"  shared net VPC {tag.upper()} created: {nid} ({cidr})")
            else:
                print(f"  shared net VPC {tag.upper()} provision failed "
                      f"({nresp.status}); its adopters fall back / IB-049-skip.")
    # 공유 TGW (adopt:tgw 대상, 오너 2026-07-13). 계정 캡 3 압박 완화 — children만
    # TGW를 self-create(CRUD 주인공)하고, gen-private-nat·heavy-net은 이걸 adopt
    # (전제조건 용도)해 동시 TGW 3→2. account-level이라 VPC 불요. ACTIVE 대기는
    # no-wait: 첫 adopter의 _ensure_adopted_active("tgw")가 게이트(서브넷과 동일).
    tgw_id = None
    if need_tgw:
        tname = f"regrtgwsh{uniq}"       # 'regrtgwsh'+8hex = 17 ≤ 20 (IB-051 name cap)
        tbody = _inject_owner_tags({
            "name": tname, "description": "API regression shared transit gateway",
            "tags": [],
        }, axis="regression")
        tcreate = {"name": "create-shared-tgw", "method": "POST", "service": "vpc"}
        tresp = _run_step(client, tcreate, _TGW_CREATE_PATH, tbody, "vpc", {})
        if tresp.status in (200, 201, 202) and tresp.body:
            tgw_id = _capture(tresp.body, "$.transit_gateway.id")
        if tgw_id:
            tgw_id = str(tgw_id)
            reg.track(ResourceRecord(service="vpc",
                                     delete_path=f"{_TGW_CREATE_PATH}/{tgw_id}",
                                     resource_id=tgw_id, kind="transit-gateway",
                                     parent="shared-tgw"))
            print(f"  shared TGW created: {tgw_id}")
        else:
            print(f"  shared TGW provision failed ({tresp.status}); "
                  f"its adopters fall back to self-create.")
    # poll to ACTIVE so adopters can build under it immediately. interval 5 (not
    # 10) + verbose: this wait sits on the run's CRITICAL start path and its log
    # is streamed live — heartbeat lines + a tighter residual wait (2026-07-08
    # "시작까지 너무 오래걸려").
    wait = {"name": "wait-shared-vpc", "method": "GET", "service": "vpc",
            "poll": {"field": "$.vpc.state",
                     "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                     "timeout": 300, "interval": 5, "verbose": True}}
    _run_step(client, wait, f"{_VPC_CREATE_PATH}/{vpc_id}", None, "vpc", {})
    reg.track(ResourceRecord(service="vpc",
                             delete_path=f"{_VPC_CREATE_PATH}/{vpc_id}",
                             resource_id=vpc_id, kind="vpc", parent="shared"))
    print(f"  shared VPC provisioned: {vpc_id} ({_SHARED_VPC_CIDR})")

    # 공유 IGW (adopt:igw 대상, 오너 2026-07-14). IGW는 VPC당 1개 배타라, 메인
    # 공유 VPC를 나눠 쓰는 adopt:vpc lifecycle들이 각자 create-igw하면 2번째부터
    # 400 already-associated. 여기서 IGW 1개를 상주시키고 그들이 adopt→skip한다.
    # VPC가 ACTIVE여야 attach 가능하므로 위 wait-shared-vpc 직후에 발행. ACTIVE
    # 대기는 no-wait — 첫 adopter의 _ensure_adopted_active("igw")가 게이트한다
    # (서브넷/TGW와 동일). body는 시나리오 create-internet-gateway와 동일 형태.
    igw_id = None
    if need_igw:
        ibody = _inject_owner_tags({
            "description": "API regression shared internet gateway",
            "firewall_enabled": False, "firewall_loggable": False,
            "type": "IGW", "vpc_id": vpc_id, "tags": [],
        }, axis="regression")
        icreate = {"name": "create-shared-igw", "method": "POST", "service": "vpc"}
        iresp = _run_step(client, icreate, _IGW_CREATE_PATH, ibody, "vpc", {})
        if iresp.status in (200, 201, 202) and iresp.body:
            igw_id = _capture(iresp.body, "$.internet_gateway.id")
        if igw_id:
            igw_id = str(igw_id)
            reg.track(ResourceRecord(service="vpc",
                                     delete_path=f"{_IGW_CREATE_PATH}/{igw_id}",
                                     resource_id=igw_id, kind="internet-gateway",
                                     parent=vpc_id))
            print(f"  shared IGW created: {igw_id} (vpc {vpc_id})")
        else:
            print(f"  shared IGW provision failed ({iresp.status}); its adopters "
                  f"fall back to find-or-create.")

    # 2)+3) shared SUBNETs under the shared VPC — the general one (mirrors a
    #    create-subnet step body: name/description/cidr/type=GENERAL/vpc_id/tags,
    #    carved from the first /24 of the VPC's /20) and the DB-lane one (the DB
    #    cluster lifecycles adopt THIS one via adopt: "subnet#db" so their slow
    #    provisioning is isolated from the VM/SKE/networking adopters).
    #    Both creates FIRST, then both ACTIVE waits: they only need the VPC, so
    #    the DB subnet turns ACTIVE while the main-subnet wait runs and its own
    #    wait usually returns on the first GET (start-latency shave 2026-07-08).
    def _subnet_create(step_name: str, name: str, cidr: str, desc: str):
        body = _inject_owner_tags({
            # IB-051: subnet name length 3..20 ('regrsubsh(db)'+8-hex ≤ 19 chars,
            # 'regrsub'-prefixed so the reconciler's subnet sweep reclaims them).
            "name": name, "description": desc,
            "cidr": cidr, "type": "GENERAL", "vpc_id": vpc_id, "tags": [],
        }, axis="regression")
        create = {"name": step_name, "method": "POST", "service": "vpc"}
        resp = _run_step(client, create, _SUBNET_CREATE_PATH, body, "vpc", {})
        sid = None
        if resp.status in (200, 201, 202) and resp.body:
            sid = _capture(resp.body, "$.subnet.id")
        if sid:
            # track는 create 직후 (wait 성공 여부와 무관) — no-wait 반환이든
            # wait 타임아웃이든 스윕이 항상 회수할 수 있어야 한다.
            reg.track(ResourceRecord(service="vpc",
                                     delete_path=f"{_SUBNET_CREATE_PATH}/{sid}",
                                     resource_id=str(sid), kind="subnet",
                                     parent=vpc_id))
        return (str(sid) if sid else None), resp

    def _subnet_wait(step_name: str, sid: str, cidr: str, label: str):
        wait_step = {"name": step_name, "method": "GET", "service": "vpc",
                     "poll": {"field": "$.subnet.state",
                              "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                              "timeout": 300, "interval": 5, "verbose": True}}
        _run_step(client, wait_step, f"{_SUBNET_CREATE_PATH}/{sid}", None, "vpc", {})
        print(f"  {label} provisioned: {sid} ({cidr})")

    subnet_id, sresp = _subnet_create(
        "create-shared-subnet", f"regrsubsh{uniq}", _SHARED_SUBNET_CIDR,
        "API regression shared subnet")
    db_subnet_id = None
    if need_db_subnet:
        db_subnet_id, dresp = _subnet_create(
            "create-shared-db-subnet", f"regrsubshdb{uniq}", _SHARED_DB_SUBNET_CIDR,
            "API regression shared DB subnet")
    else:
        # 선택-인지 스킵 (owner 2026-07-08 "db subnet 만들어지기 전까지 아무것도
        # 안 하고 있네"): subnet#db 를 입양하는 lifecycle 이 이 런의 선택에 없으면
        # DB-lane 서브넷은 순수 직렬 대기(~1-2분)일 뿐이다.
        print("  DB-lane shared subnet SKIPPED — selection has no subnet#db adopter")
    if not subnet_id:
        print(f"  shared-subnet provision failed ({sresp.status}); adopters will "
              f"self-create a subnet under the shared VPC.")
    if not db_subnet_id and need_db_subnet:
        print(f"  shared-DB-subnet provision failed ({dresp.status}); DB adopters "
              f"fall back to the main shared subnet.")
    if wait_subnets_active:
        if subnet_id:
            _subnet_wait("wait-shared-subnet", subnet_id, _SHARED_SUBNET_CIDR,
                         "shared subnet")
        if db_subnet_id:
            _subnet_wait("wait-shared-db-subnet", db_subnet_id,
                         _SHARED_DB_SUBNET_CIDR, "shared DB subnet")
        for tag, (nid, _nname) in net_ids.items():
            nwait = {"name": f"wait-shared-net-vpc-{tag}", "method": "GET",
                     "service": "vpc",
                     "poll": {"field": "$.vpc.state",
                              "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                              "timeout": 300, "interval": 5, "verbose": True}}
            _run_step(client, nwait, f"{_VPC_CREATE_PATH}/{nid}", None, "vpc", {})
            print(f"  shared net VPC {tag.upper()} provisioned: {nid}")
    elif subnet_id or db_subnet_id:
        # no-wait 반환 (2026-07-13, run-543a 실측): 같은 VPC의 서브넷 2개는
        # 동시 생성해도 백엔드가 ACTIVE 전이를 직렬화(128s/238s)해 head 대기
        # 4.3분간 전 워커가 유휴였다. 여기서 CREATING인 채 id만 넘기고,
        # 첫 adopt 시점의 _ensure_adopted_active 게이트가 ACTIVE를 보장한다
        # (그동안 free-class·자체 VPC 생성군이 먼저 돈다).
        print(f"  shared subnet(s) created, NOT waiting ACTIVE "
              f"(adopt-time gate): subnet={subnet_id} db={db_subnet_id}")

    def teardown():
        if not cfg.allow_destructive:
            return
        # subnets THEN vpc (children before parent). 서브넷 DELETE는 202 비동기
        # (DELETING 30s~3min) — 곧바로 VPC DELETE를 날리면 409로 남는다. 이
        # 클래스는 2026-07-12에 shared_infra.teardown만 고쳐졌고, 이 인라인
        # teardown(단독/conftest 경로)은 빠져 있어 solo 런마다 공유 VPC가
        # 잔존했다 (2026-07-13 vip-nat 재검증 런 regrvpcsh6a5467bd 실측).
        # 동일 수리: 서브넷 gone 대기(≤240s) 후 VPC 409 사다리(5×15s).
        issued = []
        for sid, label in ((db_subnet_id, "shared DB subnet"),
                           (subnet_id, "shared subnet")):
            if not sid:
                continue
            try:
                r = client.request("DELETE", f"{_SUBNET_CREATE_PATH}/{sid}",
                                   service="vpc")
                st = getattr(r, "status", None)
                if st in (200, 202, 204, 404):
                    issued.append(sid)
                print(f"  {label} {sid} delete -> {st}")
            except Exception as exc:
                print(f"  {label} {sid} delete failed ({exc}); "
                      f"sweep will reclaim")
        if issued:
            deadline = time.time() + 240
            pending = list(issued)
            while pending and time.time() < deadline:
                still = []
                for sid in pending:
                    try:
                        g = client.get(f"{_SUBNET_CREATE_PATH}/{sid}", service="vpc")
                        if getattr(g, "status", None) != 404:
                            still.append(sid)
                    except Exception:  # noqa: BLE001 — read hiccup, retry next round
                        pass
                pending = still
                if pending:
                    time.sleep(10)

        def _vpc_ladder(vid: str, label: str) -> None:
            for attempt in range(5):
                try:
                    r = client.request("DELETE", f"{_VPC_CREATE_PATH}/{vid}",
                                       service="vpc")
                    st = getattr(r, "status", None)
                except Exception as exc:  # noqa: BLE001 — sweep backstop
                    print(f"  {label} {vid} delete failed ({exc}); "
                          f"sweep will reclaim")
                    return
                if st in (200, 202, 204, 404):
                    print(f"  {label} {vid} delete -> {st}")
                    return
                print(f"  {label} {vid} delete -> {st} "
                      f"(attempt {attempt + 1}/5); retrying in 15s")
                time.sleep(15)
            print(f"  {label} {vid} still not deleted; sweep will reclaim")

        # 공유 TGW 먼저 회수 (account-level, VPC와 독립). adopter의 vpc-connection은
        # 각 lifecycle이 지우므로 teardown 시점엔 connectionless여야 삭제 성공.
        # EDITING/DELETING 비동기 잔재는 사다리가 흡수, 실패는 스윕이 회수.
        if tgw_id:
            # adopter가 vpc-connection을 붙였다 떼면 TGW가 EDITING이 된다 — EDITING
            # TGW는 삭제 거부되므로(오너 2026-07-13 실측: regrtgw..가 Editing 잔존)
            # ACTIVE(또는 사라짐)까지 폴한 뒤 삭제한다 (children의 wait-tgw-active 패턴).
            _tdl = time.time() + 300
            while time.time() < _tdl:
                try:
                    g = client.get(f"{_TGW_CREATE_PATH}/{tgw_id}", service="vpc")
                    if getattr(g, "status", None) == 404:
                        break
                    stt = _capture(getattr(g, "body", None) or {}, "$.transit_gateway.state")
                    if stt is None or str(stt).upper() in ("ACTIVE", "RUNNING",
                                                           "CREATED", "AVAILABLE"):
                        break
                except Exception:  # noqa: BLE001 — read hiccup; proceed to delete
                    break
                print(f"  shared TGW {tgw_id} state={stt} — ACTIVE 대기 후 삭제")
                time.sleep(10)
            for attempt in range(5):
                try:
                    r = client.request("DELETE", f"{_TGW_CREATE_PATH}/{tgw_id}",
                                       service="vpc")
                    st = getattr(r, "status", None)
                except Exception as exc:  # noqa: BLE001 — sweep backstop
                    print(f"  shared TGW {tgw_id} delete failed ({exc}); "
                          f"sweep will reclaim")
                    break
                if st in (200, 202, 204, 404):
                    print(f"  shared TGW {tgw_id} delete -> {st}")
                    break
                print(f"  shared TGW {tgw_id} delete -> {st} "
                      f"(attempt {attempt + 1}/5); retrying in 15s")
                time.sleep(15)
        # 공유 IGW는 메인 공유 VPC의 자식 — VPC보다 먼저 삭제해야 VPC DELETE가
        # 409(IGW attached)로 안 남는다. adopter들은 IGW를 detach/삭제하지 않고
        # adopt만 하므로 teardown 시점엔 detach 가능. 409 사다리로 흡수, 실패는 스윕.
        if igw_id:
            for attempt in range(5):
                try:
                    r = client.request("DELETE", f"{_IGW_CREATE_PATH}/{igw_id}",
                                       service="vpc")
                    st = getattr(r, "status", None)
                except Exception as exc:  # noqa: BLE001 — sweep backstop
                    print(f"  shared IGW {igw_id} delete failed ({exc}); "
                          f"sweep will reclaim")
                    break
                if st in (200, 202, 204, 404):
                    print(f"  shared IGW {igw_id} delete -> {st}")
                    break
                print(f"  shared IGW {igw_id} delete -> {st} "
                      f"(attempt {attempt + 1}/5); retrying in 15s")
                time.sleep(15)
        _vpc_ladder(vpc_id, "shared VPC")
        for tag, (nid, _nname) in net_ids.items():
            # A/B의 자식(vip-nat 서브넷·IGW·DC)은 각 lifecycle이 지운다 —
            # 비동기 소멸 잔재는 같은 사다리가 흡수, 실패는 스윕이 회수.
            _vpc_ladder(nid, f"shared net VPC {tag.upper()}")

    ctx = {"shared_vpc_id": vpc_id}
    if subnet_id:
        ctx["shared_subnet_id"] = subnet_id
    if db_subnet_id:
        ctx["shared_db_subnet_id"] = db_subnet_id
    if "a" in net_ids:
        ctx["shared_net_vpc_a_id"], ctx["net_a_vpc_name"] = net_ids["a"]
    if "b" in net_ids:
        ctx["shared_net_vpc_b_id"], ctx["net_b_vpc_name"] = net_ids["b"]
    if tgw_id:
        ctx["shared_tgw_id"] = tgw_id
    if igw_id:
        ctx["shared_igw_id"] = igw_id
    return ctx, teardown
