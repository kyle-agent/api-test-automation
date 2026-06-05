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
import time
from pathlib import Path

from core import budgets as _budgets
from core import registry, results
from core.registry import ResourceRecord, ResourceRegistry
from core.results import Observation
from framework.catalog import load_catalog
from framework.client import MutationBlocked

_HERE = Path(__file__).parent
SCENARIOS_PATH = _HERE / "scenarios.json"
DEPENDENCIES_PATH = _HERE / "dependencies.json"

LIFECYCLES = json.loads(SCENARIOS_PATH.read_text())["lifecycles"]
DEPENDENCIES = json.loads(DEPENDENCIES_PATH.read_text())
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

_SMOKE_TSV = "reports/smoke_status.tsv"

# Catalog GETs used by "probe_reads" steps to exercise path-parameter GETs that
# the read-only smoke must skip, reusing a resource a lifecycle just created.
_CATALOG = load_catalog()

# Quota kinds whose budget must be reserved before a step's create, keyed by the
# path it creates. Derived from dependencies.json (path -> kind) so the kernel
# budget is consulted as DATA, not hardcoded.
_VPC_CREATE_PATH = "/v1/vpcs"


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
    try:
        os.makedirs("reports", exist_ok=True)
        with open(_SMOKE_TSV, "a") as fh:
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
    return [lc for lc in LIFECYCLES if lc.get("enabled")]


# --------------------------------------------------------------------------- #
# the lifecycle runner
# --------------------------------------------------------------------------- #
class LifecycleSkip(Exception):
    """A lifecycle skipped for an environmental reason (quota / 417 / safety)."""


def run_lifecycle(lifecycle: dict, client, cfg, *,
                  budget: _budgets.Budget | None = None,
                  resource_registry: ResourceRegistry | None = None) -> dict:
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
        for step in lifecycle["steps"]:
            grp = step.get("group")
            if grp and grp in failed_groups:
                continue  # an earlier step in this group failed — skip the rest
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
            except MutationBlocked as exc:
                if bkind:  # roll back the reservation we just took
                    budget.release(bkind)
                    reserved[bkind] = max(0, reserved.get(bkind, 0) - 1)
                _teardown()
                raise LifecycleSkip(str(exc))

            # record the step call itself for coverage/timing
            _record_smoke(resp.status, categorize(resp.status, resp.raw_text or ""),
                          f"{lifecycle['id']}:{step['name']}", step["method"],
                          step.get("path", path), getattr(resp, "elapsed_ms", None))

            expected = step.get("expect_status", [200])
            _txt = resp.raw_text or ""
            _is_quota = ("exceed-max-count" in _txt or "ExceedMax" in _txt
                         or "max-count-exceed" in _txt)
            _is_gateway_block = (resp.status == 417 and (
                "Request Rejected" in _txt or "request was blocked" in _txt
                or "Support ID" in _txt))
            if resp.status not in expected and (_is_quota or _is_gateway_block):
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
