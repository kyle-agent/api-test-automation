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

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from core import budgets as _budgets
from core import registry, results
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
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

_SMOKE_TSV = "reports/smoke_status.tsv"

# Catalog GETs used by "probe_reads" steps to exercise path-parameter GETs that
# the read-only smoke must skip, reusing a resource a lifecycle just created.
_CATALOG = load_catalog()


def _norm_path(p: str) -> str:
    """Collapse templated id segments to '*' (mirrors dashboard.norm_path) so a
    lifecycle step's templated path can be matched back to its catalog endpoint."""
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


# (METHOD, normalized-path, service) -> catalog endpoint key. Lets a CRUD WRITE
# step be recorded under its REAL catalog key (not just "lifecycle:step") so its
# HTTP status + response time show up in the dashboard's per-endpoint column,
# exactly like read-only GETs. Service is part of the key because path roots
# collide across services (e.g. /v1/volumes, /v1/clusters).
_CAT_KEY_BY_MNS: dict[tuple, str] = {}
for _e in _CATALOG:
    _CAT_KEY_BY_MNS.setdefault(
        ((_e.method or "").upper(), _norm_path(_e.http_path), _e.service), _e.key)


def _catalog_key_for(method: str, templated_path: str, service: str | None):
    """Resolve a step's (method, templated path, service) to a catalog key, or None."""
    return _CAT_KEY_BY_MNS.get(
        ((method or "").upper(), _norm_path(templated_path), service or ""))

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
                 "subnet#db": "shared_db_subnet_id"}
_SHARED_VPC_CIDR = "10.124.0.0/20"
# Shared subnet carved from the first /24 of the shared VPC's /20. ADOPT-class
# lifecycles re-home their fixed host IPs into this range (10.124.0.x) so that
# parallel adopters do not collide on the SAME host IP in the ONE shared subnet
# (see knowledge/vpc-scheduling-strategy.md fixed-IP map).
_SHARED_SUBNET_CIDR = "10.124.0.0/24"
# DB-lane shared subnet — 10.124.1-6.0/24 are reserved by the adopters'
# self-create FALLBACK subnets (knowledge/domain-constraints.md), so the DB
# lane takes the next free /24 of the shared /20.
_SHARED_DB_SUBNET_CIDR = "10.124.7.0/24"
_SUBNET_CREATE_PATH = "/v1/subnets"
# Env keys for cross-process (xdist) adoption of an already-live shared VPC/subnet
# provisioned once by regression.scenarios.shared_infra --provision.
_ENV_SHARED_VPC = "SCP_SHARED_VPC_ID"
_ENV_SHARED_SUBNET = "SCP_SHARED_SUBNET_ID"
# DB-lane subnet: DB cluster provisioning is the slowest thing in the parallel
# pass, so the DB lifecycles get their OWN shared subnet (lane isolation) while
# VM/SKE/networking adopters stay on the main shared subnet (fixed IPs intact).
_ENV_SHARED_DB_SUBNET = "SCP_SHARED_DB_SUBNET_ID"


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


def _record_smoke(status, category, key, method, path, elapsed_ms=None):
    """Dual-write: unified Observation store AND legacy smoke TSV."""
    results.record(Observation(
        endpoint_key=key, method=method, path=path, status=status,
        category=category, elapsed_ms=elapsed_ms, source="crud_probe"))
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


def _probe_reads(client, mapping, service):
    """Call every catalog GET in `service` whose path params are all supplied by
    `mapping` (catalog-param-name -> already-filled value). Read-only and record
    only — a probe never fails the lifecycle."""
    keys = set(mapping)
    called = 0
    for e in _CATALOG:
        if e.service != service or (e.method or "").upper() != "GET":
            continue
        if not e.http_path:
            continue
        params = set(_PLACEHOLDER.findall(e.http_path))
        if not params or not params <= keys:
            continue
        path = e.http_path
        for p in params:
            path = path.replace("{%s}" % p, str(mapping[p]))
        try:
            resp = client.get(path, service=service)
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


def _fill(template: str, ctx: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), template)


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
        return _fill(obj, ctx)
    if isinstance(obj, dict):
        return {k: _fill_obj(v, ctx) for k, v in obj.items()}
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


# --------------------------------------------------------------------------- #
# step execution (poll + retry, ported)
# --------------------------------------------------------------------------- #
def _run_step(client, step, path, body, service, ctx):
    """Execute a step; honour retry_on_status and poll (field/until or
    until_status) for async provisioning/teardown."""
    params = step.get("params")
    try:
        resp = client.request(step["method"], path, json=body, service=service, params=params)
    except Exception as exc:
        # One retry on a transport timeout (field case: iam PUT hit the 20s
        # read timeout once and failed the whole lifecycle). Slow-but-alive
        # gateways are environmental; a single retry absorbs the blip.
        if "timeout" not in type(exc).__name__.lower() and "timed out" not in str(exc).lower():
            raise
        print(f"  step '{step.get('name')}' transport timeout — retrying once ({exc})")
        time.sleep(5)
        resp = client.request(step["method"], path, json=body, service=service, params=params)
    ros = step.get("retry_on_status")
    if ros:
        attempts = int(step.get("retries", 4))
        interval = float(step.get("retry_interval", 15))
        while resp.status in ros and attempts > 0:
            time.sleep(interval)
            resp = client.request(step["method"], path, json=body, service=service, params=params)
            attempts -= 1
    poll = step.get("poll")
    if not poll:
        return resp
    until_status = poll.get("until_status")
    field, until = poll.get("field"), poll.get("until", [])
    timeout, interval = float(poll.get("timeout", 300)), float(poll.get("interval", 10))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until_status is not None:
            if resp.status in until_status:
                return resp
        elif field:
            val = _jsonpath_get(resp.body, field) if resp.body else None
            if val in until:
                return resp
        time.sleep(interval)
        resp = client.request(step["method"], path, json=body, service=service, params=params)
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
    slow_markers = ("database-", "heavy-", "container-ske",
                    "compute-virtualserver-full", "dns")

    def slow_rank(lc: dict) -> int:
        if lc.get("heavy"):
            return 0
        if any(m in lc["id"] for m in slow_markers):
            return 1
        return 2

    return sorted((lc for lc in LIFECYCLES if lc.get("enabled")),
                  key=lambda lc: (slow_rank(lc), lc["id"]))


# --------------------------------------------------------------------------- #
# the lifecycle runner
# --------------------------------------------------------------------------- #
class LifecycleSkip(Exception):
    """A lifecycle skipped for an environmental reason (quota / 417 / safety)."""


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

    budget = budget if budget is not None else _budgets.Budget()
    reg = resource_registry if resource_registry is not None else ResourceRegistry()

    service = lifecycle.get("service", "").split("/")[-1] or None

    _now = time.gmtime()
    ctx: dict[str, str] = {
        "unique": format(int(time.time()), "x"),
        "ualpha": "".join(chr(ord("a") + int(c, 16)) for c in format(int(time.time()), "x")),
        "region": cfg.region,
        "today": time.strftime("%Y%m%d", _now),
        "today_plus_5y": f"{_now.tm_year + 5}{time.strftime('%m%d', _now)}",
    }
    # Seed shared resources (e.g. a session-shared VPC) so {"adopt": ...} steps
    # reuse them instead of creating their own.
    if shared_ctx:
        ctx.update({k: str(v) for k, v in shared_ctx.items() if v})

    # Teardown stack of (label, method, path, service, json, group, budget_kind).
    cleanups: list[tuple] = []
    failed_groups: set = set()
    group_fail_reason: dict = {}
    reserved: dict = {}  # budget kind -> count reserved by this lifecycle
    created_count = 0

    def _run_cleanup(entry):
        label, method, path, svc, cu_json, _grp, bkind = entry
        try:
            if cfg.allow_destructive:
                client.request(method, path, json=cu_json, service=svc)
                print(f"  cleanup: {method} {path}")
        except Exception as exc:  # best-effort; report and continue
            print(f"  cleanup FAILED for {label} ({path}): {exc}")
        finally:
            if bkind:  # release the reserved budget slot regardless of outcome
                budget.release(bkind)
                reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)

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
            grp = step.get("group")
            if grp and grp in failed_groups:
                continue  # an earlier step in this group failed — skip the rest

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
                if _shared_val:
                    _m = step.get("method", "").upper()
                    if _m == "POST":  # adopt: skip the create, seed its capture vars
                        for _v in step.get("capture", {}):
                            ctx[_v] = _shared_val
                        print(f"  [{lifecycle['id']}] adopting shared {_adopt}="
                              f"{_shared_val} (skip create '{step['name']}')")
                        continue
                    if _m == "DELETE":  # retain shared resource — fixture tears it down
                        print(f"  [{lifecycle['id']}] retaining shared {_adopt} "
                              f"(skip delete '{step['name']}')")
                        continue
                    if _m == "GET":  # e.g. wait-<x>-gone — pointless on a retained
                        # shared resource (it never 404s); skip to avoid a long poll.
                        print(f"  [{lifecycle['id']}] skipping '{step['name']}' "
                              f"(shared {_adopt} retained, not deleted)")
                        continue

            step_service = step.get("service") or service
            if step.get("wait"):
                time.sleep(float(step["wait"]))

            if step.get("probe_reads"):
                mapping = {k: _fill(v, ctx) for k, v in step["probe_reads"].items()}
                mapping = {k: v for k, v in mapping.items() if "{" not in v}
                _probe_reads(client, mapping, step_service)
                continue

            path = _fill(step["path"], ctx)
            body = _fill_obj(step.get("json"), ctx)

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

            try:
                resp = _run_step(client, step, path, body, step_service, ctx)

                # Optional setter steps routinely race async provisioning (a
                # DBaaS cluster is busy applying the PREVIOUS setter -> 400
                # invalid-state). When 4xx is NOT an expected status for the
                # step, give it a few spaced retries before classifying — this
                # converts transient called-only (C2) into verified (C3).
                if (step.get("optional") and not step.get("retry_on_status")
                        and resp.status in (400, 409, 429)
                        and resp.status not in step.get("expect_status", [200])):
                    for _attempt in range(3):
                        time.sleep(20)
                        resp = _run_step(client, step, path, body, step_service, ctx)
                        if resp.status not in (400, 409, 429):
                            break
            except MutationBlocked as exc:
                if bkind:  # roll back the reservation we just took
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                _teardown()
                raise LifecycleSkip(str(exc))

            # record the step call itself for coverage/timing
            _cat = categorize(resp.status, resp.raw_text or "")
            _ems = getattr(resp, "elapsed_ms", None)
            _record_smoke(resp.status, _cat,
                          f"{lifecycle['id']}:{step['name']}", step["method"],
                          step.get("path", path), _ems)
            # ALSO record WRITE steps under their real catalog endpoint key so the
            # dashboard surfaces their HTTP status + response time per endpoint, the
            # same way GETs do (reads already arrive under the catalog key via smoke
            # / probe_reads). Records the ACTUAL response — incl. a 4xx from an
            # isolated optional write — which is exactly the signal we want shown.
            if step["method"].upper() != "GET":
                _ck = _catalog_key_for(step["method"], step.get("path", ""), step_service)
                if _ck:
                    _record_smoke(resp.status, _cat, _ck, step["method"],
                                  step.get("path", path), _ems)

            expected = step.get("expect_status", [200])
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
            if resp.status not in expected and (_is_quota or _is_gateway_block or _is_dep_gone):
                if bkind:  # the create did not take effect — give the slot back
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
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

            status_ok = resp.status in expected
            if status_ok:
                for var, expr in step.get("capture", {}).items():
                    if _capture(resp.body, expr) is None:
                        status_ok = False
                        break
            if not status_ok and step.get("optional"):
                if bkind:  # creation failed — release the reserved slot
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                reason = f"{step['name']} -> {resp.status}: {resp.raw_text[:400]}"
                print(f"  optional step '{step['name']}' (group={grp}) failed "
                      f"-> {resp.status}; skipping group. {resp.raw_text[:200]}")
                if grp:
                    failed_groups.add(grp)
                    group_fail_reason.setdefault(grp, reason)
                    _teardown_group(grp)
                continue
            assert resp.status in expected, (
                f"[{lifecycle['id']}] step '{step['name']}' "
                f"{step['method']} {path} -> {resp.status}, expected {expected}\n"
                f"{resp.raw_text[:500]}")

            for var, expr in step.get("capture", {}).items():
                val = _capture(resp.body, expr)
                assert val is not None, (
                    f"could not capture '{var}' via {expr!r} from {step['name']} response")
                ctx[var] = str(val)

            for var, expr in step.get("capture_soft", {}).items():
                val = _capture(resp.body, expr)
                if val is None:
                    print(f"  soft-capture '{var}' via {expr!r} found nothing "
                          f"from '{step['name']}' — dependent probe(s) skipped")
                    continue
                ctx[var] = str(val)

            # Register teardown + track in the kernel registry for the freshly
            # created resource (deletes only on a later failure; the happy path
            # deletes via its own steps).
            cu = step.get("cleanup")
            if cu:
                created_count += 1
                cu_path = _fill(cu["path"], ctx)
                cu_svc = cu.get("service") or step_service
                cleanups.append((step["name"], cu["method"], cu_path, cu_svc,
                                 _fill_obj(cu.get("json"), ctx), grp, bkind))
                # crash-safe manifest entry for the reconciler
                rid = ""
                for v in step.get("capture", {}):
                    if v in ctx:
                        rid = ctx[v]
                        break
                reg.track(ResourceRecord(
                    service=cu_svc or "", delete_path=cu_path, resource_id=rid,
                    kind=bkind or step["name"], parent=grp))
    except LifecycleSkip as exc:
        return {"id": lifecycle["id"], "status": "skipped", "reason": str(exc),
                "failed_groups": sorted(failed_groups), "created": created_count}
    except Exception as exc:
        print(f"\n[{lifecycle['id']}] failed — attempting teardown of created resources:")
        _teardown()
        return _finish(lifecycle, "failed", failed_groups, group_fail_reason,
                       created_count, reason=str(exc), raised=exc)

    return _finish(lifecycle, "passed", failed_groups, group_fail_reason, created_count)


def _finish(lifecycle, status, failed_groups, group_fail_reason, created, *,
            reason=None, raised=None):
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


def provision_shared_vpc(client, cfg, *, resource_registry: ResourceRegistry | None = None):
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
        print(f"  adopting pre-provisioned shared VPC={env_vpc}"
              f"{' subnet=' + env_sub if env_sub else ''}"
              f"{' db-subnet=' + env_db_sub if env_db_sub else ''} (env)")
        return ctx, (lambda: None)

    if not cfg.allow_mutations:
        return noop
    reg = resource_registry if resource_registry is not None else ResourceRegistry()
    uniq = format(int(time.time()), "x")
    body = _inject_owner_tags({
        # 'regrvpc' prefix (not 'regrshared') so the reconciler's VPC sweep AND
        # its LB/NAT-by-vpc_id sweep (name_prefixes=('regrvpc','zznetvpc')) reclaim
        # this shared VPC + its children even if a VPC list response omits tags.
        "name": f"regrvpcshared{uniq}", "description": "API regression shared VPC",
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
    # poll to ACTIVE so adopters can build under it immediately
    wait = {"name": "wait-shared-vpc", "method": "GET", "service": "vpc",
            "poll": {"field": "$.vpc.state",
                     "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                     "timeout": 300, "interval": 10}}
    _run_step(client, wait, f"{_VPC_CREATE_PATH}/{vpc_id}", None, "vpc", {})
    reg.track(ResourceRecord(service="vpc",
                             delete_path=f"{_VPC_CREATE_PATH}/{vpc_id}",
                             resource_id=vpc_id, kind="vpc", parent="shared"))
    print(f"  shared VPC provisioned: {vpc_id} ({_SHARED_VPC_CIDR})")

    # 2) shared SUBNET under the shared VPC (mirrors a create-subnet step body in
    #    scenarios.json: name/description/cidr/type=GENERAL/vpc_id/tags). Carved
    #    from the first /24 of the VPC's /20.
    subnet_id = None
    sub_body = _inject_owner_tags({
        "name": f"regrsubshared{uniq}", "description": "API regression shared subnet",
        "cidr": _SHARED_SUBNET_CIDR, "type": "GENERAL", "vpc_id": vpc_id, "tags": [],
    }, axis="regression")
    sub_create = {"name": "create-shared-subnet", "method": "POST", "service": "vpc"}
    sresp = _run_step(client, sub_create, _SUBNET_CREATE_PATH, sub_body, "vpc", {})
    if sresp.status in (200, 201, 202) and sresp.body:
        subnet_id = _capture(sresp.body, "$.subnet.id")
        if subnet_id:
            subnet_id = str(subnet_id)
            swait = {"name": "wait-shared-subnet", "method": "GET", "service": "vpc",
                     "poll": {"field": "$.subnet.state",
                              "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                              "timeout": 300, "interval": 10}}
            _run_step(client, swait, f"{_SUBNET_CREATE_PATH}/{subnet_id}",
                      None, "vpc", {})
            reg.track(ResourceRecord(service="vpc",
                                     delete_path=f"{_SUBNET_CREATE_PATH}/{subnet_id}",
                                     resource_id=subnet_id, kind="subnet",
                                     parent=vpc_id))
            print(f"  shared subnet provisioned: {subnet_id} ({_SHARED_SUBNET_CIDR})")
    if not subnet_id:
        print(f"  shared-subnet provision failed ({sresp.status}); adopters will "
              f"self-create a subnet under the shared VPC.")

    # 3) DB-lane shared subnet — the DB cluster lifecycles adopt THIS one
    #    (adopt: "subnet#db") so their slow provisioning is isolated from the
    #    VM/SKE/networking adopters on the main shared subnet.
    db_subnet_id = None
    db_body = _inject_owner_tags({
        "name": f"regrsubshareddb{uniq}", "description": "API regression shared DB subnet",
        "cidr": _SHARED_DB_SUBNET_CIDR, "type": "GENERAL", "vpc_id": vpc_id, "tags": [],
    }, axis="regression")
    db_create = {"name": "create-shared-db-subnet", "method": "POST", "service": "vpc"}
    dresp = _run_step(client, db_create, _SUBNET_CREATE_PATH, db_body, "vpc", {})
    if dresp.status in (200, 201, 202) and dresp.body:
        db_subnet_id = _capture(dresp.body, "$.subnet.id")
        if db_subnet_id:
            db_subnet_id = str(db_subnet_id)
            dwait = {"name": "wait-shared-db-subnet", "method": "GET", "service": "vpc",
                     "poll": {"field": "$.subnet.state",
                              "until": ["ACTIVE", "RUNNING", "CREATED", "AVAILABLE"],
                              "timeout": 300, "interval": 10}}
            _run_step(client, dwait, f"{_SUBNET_CREATE_PATH}/{db_subnet_id}",
                      None, "vpc", {})
            reg.track(ResourceRecord(service="vpc",
                                     delete_path=f"{_SUBNET_CREATE_PATH}/{db_subnet_id}",
                                     resource_id=db_subnet_id, kind="subnet",
                                     parent=vpc_id))
            print(f"  shared DB subnet provisioned: {db_subnet_id} ({_SHARED_DB_SUBNET_CIDR})")
    if not db_subnet_id:
        print(f"  shared-DB-subnet provision failed ({dresp.status}); DB adopters "
              f"fall back to the main shared subnet.")

    def teardown():
        if not cfg.allow_destructive:
            return
        # subnets THEN vpc (children before parent)
        if db_subnet_id:
            try:
                client.request("DELETE", f"{_SUBNET_CREATE_PATH}/{db_subnet_id}",
                               service="vpc")
                print(f"  shared DB subnet {db_subnet_id} deleted")
            except Exception as exc:
                print(f"  shared DB subnet {db_subnet_id} delete failed ({exc}); "
                      f"sweep will reclaim")
        if subnet_id:
            try:
                client.request("DELETE", f"{_SUBNET_CREATE_PATH}/{subnet_id}",
                               service="vpc")
                print(f"  shared subnet {subnet_id} deleted")
            except Exception as exc:
                print(f"  shared subnet {subnet_id} delete failed ({exc}); "
                      f"sweep will reclaim")
        try:
            client.request("DELETE", f"{_VPC_CREATE_PATH}/{vpc_id}", service="vpc")
            print(f"  shared VPC {vpc_id} deleted")
        except Exception as exc:  # best-effort; the tag-scoped sweep is the backstop
            print(f"  shared VPC {vpc_id} delete failed ({exc}); sweep will reclaim")

    ctx = {"shared_vpc_id": vpc_id}
    if subnet_id:
        ctx["shared_subnet_id"] = subnet_id
    if db_subnet_id:
        ctx["shared_db_subnet_id"] = db_subnet_id
    return ctx, teardown
