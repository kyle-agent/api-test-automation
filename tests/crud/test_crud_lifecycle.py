"""Data-driven CRUD lifecycle regression tests.

Each lifecycle in lifecycles.json runs its steps in order against the live
gateway: create -> read -> [update] -> delete. Values produced by one step
(e.g. a new resource id) are captured and substituted into later steps.

Safety:
  * the whole suite is skipped unless SCP_ALLOW_MUTATIONS=true;
  * any step marked "destructive" additionally needs SCP_ALLOW_DESTRUCTIVE=true
    (otherwise the resource is left for manual cleanup and the test xfails with
    a clear message);
  * lifecycles are opt-in per entry via "enabled": true.

This keeps a default regression run from ever creating or deleting real cloud
resources, while giving a single place to declare full CRUD coverage per
service. Add a new service by adding an entry to lifecycles.json — no new code.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from framework.client import MutationBlocked

LIFECYCLES = json.loads((Path(__file__).parent / "lifecycles.json").read_text())["lifecycles"]
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

# Catalog + smoke-tsv recording, used by "probe_reads" steps to exercise the
# path-parameter GETs the read-only smoke must skip (no resource id), reusing a
# resource a lifecycle just created. Results land in the same tsv the dashboard
# reads, so they count toward read coverage.
_CATALOG_PATH = Path(__file__).parents[2] / "framework" / "api_catalog.json"
_CATALOG = json.loads(_CATALOG_PATH.read_text()) if _CATALOG_PATH.exists() else []
_SMOKE_TSV = "reports/smoke_status.tsv"


def _categorize(status: int, text: str) -> str:
    """Same ok/soft/fail split as the smoke suite (only 5xx / HMAC-401 are hard
    fails; everything else is the API answering correctly given this account)."""
    t = (text or "").lower()
    if 200 <= status < 300:
        return "ok"
    if status == 401:
        return "soft" if ("rejected by gateway" in t or "catalog has not target" in t) else "fail"
    if status >= 500:
        return "fail"
    return "soft"  # 400/403/404/409/422 — needs params/permission/provisioning


def _record_smoke(status, category, key, method, path):
    import os
    try:
        os.makedirs("reports", exist_ok=True)
        with open(_SMOKE_TSV, "a") as fh:
            fh.write(f"{status}\t{category}\t{key}\t{method}\t{path}\n")
    except OSError:
        pass


def _probe_reads(client, mapping, service):
    """Call every catalog GET in `service` whose path params are all supplied by
    `mapping` (catalog-param-name -> already-filled value). Read-only and record
    only — a probe never fails the lifecycle (a 404/403 sub-resource is fine),
    but a 5xx/auth fail is recorded so the dashboard flags it."""
    keys = set(mapping)
    called = 0
    for e in _CATALOG:
        if e.get("service") != service or e.get("method") != "GET":
            continue
        params = set(_PLACEHOLDER.findall(e["http_path"]))
        if not params or not params <= keys:
            continue
        path = e["http_path"]
        for p in params:
            path = path.replace("{%s}" % p, str(mapping[p]))
        try:
            resp = client.get(path, service=service)
        except Exception as exc:  # network/host issue — record nothing, continue
            print(f"  probe ERROR {path}: {exc}")
            continue
        _record_smoke(resp.status, _categorize(resp.status, getattr(resp, "raw_text", "")),
                      e["key"], "GET", e["http_path"])
        called += 1
    print(f"  probe-reads[{service}]: {called} path-param GET(s) exercised")


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
    """Capture a value from a response. `expr` is either a JSONPath string
    ("$.a.b", "$.items[0].id") or a filter object that selects the first list
    element matching field prefixes:
        {"list": "$.server_types", "where_prefix": {"id": "s"},
         "where_not_prefix": {"id": "g"}, "get": "id"}
    `where_prefix` keeps only items whose field startswith the value; the value
    of `where_not_prefix` may be a string or list of prefixes to EXCLUDE (e.g.
    exclude GPU 'g*' server types)."""
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
    """Recursively substitute {placeholders} inside a request body (dict/list/str)."""
    if isinstance(obj, str):
        return _fill(obj, ctx)
    if isinstance(obj, dict):
        return {k: _fill_obj(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_obj(v, ctx) for v in obj]
    return obj


def _run_step(client, step, path, body, service, ctx):
    """Execute a step; if it declares "poll", repeat until a condition holds
    (for async provisioning/teardown) or the timeout elapses. The condition is
    either a body field reaching a value ("field"/"until") or the response
    status reaching one of "until_status" (e.g. [404] = resource gone)."""
    params = step.get("params")
    resp = client.request(step["method"], path, json=body, service=service, params=params)
    # Optional: retry while the status is in retry_on_status (e.g. flaky 500 on
    # an async delete). Distinct from poll, which waits for a target state.
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


_active = [lc for lc in LIFECYCLES if lc.get("enabled")]


@pytest.mark.crud
@pytest.mark.parametrize("lifecycle", _active or [pytest.param(None, marks=pytest.mark.skip(
    reason="no enabled lifecycles in lifecycles.json"))],
    ids=[lc["id"] for lc in _active] or ["none"])
def test_crud_lifecycle(lifecycle, client, cfg):
    if not cfg.allow_mutations:
        pytest.skip("set SCP_ALLOW_MUTATIONS=true to run CRUD lifecycle tests")
    if lifecycle.get("heavy") and not cfg.run_heavy:
        pytest.skip("heavy lifecycle (real VM / K8s) — set SCP_RUN_HEAVY=true (manual dispatch) to run")

    # lifecycle "service" is "<category>/<service>"; the host uses the service
    # part. Individual steps may override it (chains span several services).
    service = lifecycle.get("service", "").split("/")[-1] or None

    # Seed context: {unique} = short lowercase alnum token (safe for name
    # patterns / length limits), {region} for bodies that need it, and
    # {today}/{today_plus_5y} (YYYYMMDD) for APIs that reject past start dates
    # (e.g. certificate manager not_before_dt).
    _now = time.gmtime()
    ctx: dict[str, str] = {
        "unique": format(int(time.time()), "x"),
        "region": cfg.region,
        "today": time.strftime("%Y%m%d", _now),
        "today_plus_5y": f"{_now.tm_year + 5}{time.strftime('%m%d', _now)}",
    }
    # Teardown stack of (label, method, path, service, json) for resources created so
    # far, used to best-effort clean up if the lifecycle fails partway — so a
    # failed run never leaks a billable resource (e.g. an orphaned VM).
    cleanups: list[tuple] = []
    failed_groups: set = set()
    group_fail_reason: dict = {}

    def _run_cleanup(entry):
        label, method, path, svc, cu_json, _grp = entry
        try:
            if cfg.allow_destructive:
                client.request(method, path, json=cu_json, service=svc)
                print(f"  cleanup: {method} {path}")
        except Exception as exc:  # best-effort; report and continue
            print(f"  cleanup FAILED for {label} ({path}): {exc}")

    def _teardown():
        for entry in reversed(cleanups):
            _run_cleanup(entry)

    def _teardown_group(grp):
        # An optional step failed: clean up just this group's resources and drop
        # them from the stack so the final teardown doesn't double-delete.
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
            if step.get("wait"):  # let async provisioning settle before this step
                time.sleep(float(step["wait"]))

            # Read-breadth probe: exercise path-param GETs with this live resource.
            if step.get("probe_reads"):
                mapping = {k: _fill(v, ctx) for k, v in step["probe_reads"].items()}
                # Drop vars that never got captured (a soft-capture miss leaves
                # the placeholder unfilled) so we don't probe with a literal "{var}".
                mapping = {k: v for k, v in mapping.items() if "{" not in v}
                _probe_reads(client, mapping, step_service)
                continue

            path = _fill(step["path"], ctx)
            body = _fill_obj(step.get("json"), ctx)

            if step.get("destructive") and not cfg.allow_destructive:
                pytest.xfail(
                    f"destructive step '{step['name']}' skipped (set "
                    f"SCP_ALLOW_DESTRUCTIVE=true). Manual cleanup needed: {path}")

            try:
                resp = _run_step(client, step, path, body, step_service, ctx)
            except MutationBlocked as exc:
                pytest.skip(str(exc))

            expected = step.get("expect_status", [200])
            # Account quota limits (e.g. ExceedMaxVpcCountError when the account is
            # already at its 5-VPC cap with non-regr VPCs) are an environmental
            # condition, not a regression — tear down anything created so far and
            # skip, so the dashboard doesn't flag a false NEW regression.
            if resp.status not in expected and (
                    "exceed-max-count" in resp.raw_text or "ExceedMax" in resp.raw_text):
                _teardown()
                pytest.skip(
                    f"[{lifecycle['id']}] environmental quota limit at step "
                    f"'{step['name']}': {resp.raw_text[:200]}")
            # An "optional" step belongs to a "group" (e.g. one dbaas engine in a
            # multi-engine lifecycle). If it fails (bad status or a capture miss),
            # don't sink the whole lifecycle: clean up that group's resources, mark
            # the group failed (its remaining steps are skipped via the check at the
            # top of the loop), and continue so the other groups still run and
            # record coverage. A wrong body in one engine thus costs only that
            # engine, not the entire ~40-min billable run.
            status_ok = resp.status in expected
            if status_ok:
                for var, expr in step.get("capture", {}).items():
                    if _capture(resp.body, expr) is None:
                        status_ok = False
                        break
            if not status_ok and step.get("optional"):
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

            # Soft captures feed only read-breadth probes; a best-guess JSONPath
            # that finds nothing must not sink the whole lifecycle, so capture
            # best-effort and skip (rather than assert) on a miss — dependent
            # probes for that var are then simply not exercised.
            for var, expr in step.get("capture_soft", {}).items():
                val = _capture(resp.body, expr)
                if val is None:
                    print(f"  soft-capture '{var}' via {expr!r} found nothing "
                          f"from '{step['name']}' — dependent probe(s) skipped")
                    continue
                ctx[var] = str(val)

            # Register teardown for a freshly-created resource (runs only on a
            # later failure; the happy path deletes via its own steps).
            cu = step.get("cleanup")
            if cu:
                cleanups.append((step["name"], cu["method"], _fill(cu["path"], ctx),
                                 cu.get("service") or step_service,
                                 _fill_obj(cu.get("json"), ctx), grp))
    except Exception:
        print(f"\n[{lifecycle['id']}] failed — attempting teardown of created resources:")
        _teardown()
        raise

    # Surface optional-group failures even though the test passed: pytest swallows
    # captured stdout for passing tests, so emit a warning (shown in the run's
    # warnings summary + the CRUD PR comment) naming each skipped group and the
    # exact status/body that skipped it — that's how we debug the per-engine
    # create bodies without re-reading raw job logs.
    if failed_groups:
        import warnings
        for g in sorted(failed_groups):
            warnings.warn(f"[{lifecycle['id']}] group '{g}' skipped: "
                          f"{group_fail_reason.get(g, '?')}")
