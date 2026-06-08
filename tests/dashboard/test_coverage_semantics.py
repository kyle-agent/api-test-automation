"""Dashboard coverage-semantics regression tests (offline, no live client).

Locks the contract that write (POST/PUT/PATCH/DELETE) coverage on the dashboard
is grounded in *actual* observations — exactly like GET — so an exercised write
always shows its HTTP status + response time, a write that is only *declared* in
a CRUD scenario (but not run this run) is a distinct '◷' marker, and a path that
collides across services never gets a false ✓ on a service whose endpoint was
never called.

Regression guard for the bug where POST/PUT results did not surface on the
dashboard because write coverage was computed from the static scenario
declaration (service-agnostic (method, path) match) instead of from the unified
results store.
"""
from __future__ import annotations

from dashboard.build import per_service, compute, norm_path


def _e(key, category, service, method, path):
    return {"key": key, "category": category, "service": service,
            "method": method, "http_path": path, "_norm": norm_path(path)}


def _row(status, key, method, path, ems):
    # (status, category, endpoint_key, method, path, elapsed_ms) — the 6-tuple
    # per_service/compute consume (see obs_to_tsv_row).
    return (status, "ok", key, method, path, ems)


CAT = [
    _e("svcA/foo/listfoos", "svcA", "foo", "GET", "/v1/foos"),
    _e("svcA/foo/createfoo", "svcA", "foo", "POST", "/v1/foos"),
    _e("svcA/foo/setfoo", "svcA", "foo", "PUT", "/v1/foos/{foo_id}"),
    _e("svcA/foo/deletefoo", "svcA", "foo", "DELETE", "/v1/foos/{foo_id}"),
    # collision: same (method, path) as svcA's create, different service, never called
    _e("svcB/bar/createbar", "svcB", "bar", "POST", "/v1/foos"),
]


def _svc(services, name):
    return next(s for s in services if s["service"] == name)


def _state(service, method, http_path):
    return next(r[3] for r in service["rows"]
                if r[0] == method and r[1] == http_path)


def test_exercised_write_is_covered_with_status():
    """A POST/PUT we actually observed shows ✓ (exec) + its status + time."""
    rows = [
        _row(200, "svcA/foo/listfoos", "GET", "/v1/foos", 12.0),
        _row(201, "svcA/foo/createfoo", "POST", "/v1/foos", 55.0),
        _row(200, "svcA/foo/setfoo", "PUT", "/v1/foos/{foo_id}", 33.0),
    ]
    # write_hit declares all of foo's writes (incl. the un-run delete)
    write_hit = {("POST", norm_path("/v1/foos")),
                 ("PUT", norm_path("/v1/foos/{foo_id}")),
                 ("DELETE", norm_path("/v1/foos/{foo_id}"))}
    svcA = _svc(per_service(CAT, rows, write_hit), "foo")

    assert _state(svcA, "POST", "/v1/foos") == "exec"               # POST exercised
    assert _state(svcA, "PUT", "/v1/foos/{foo_id}") == "exec"    # PUT exercised
    # exercised writes count toward wcov; delete declared-not-run does not
    assert svcA["wcov"] == 2
    assert svcA["wdecl"] == 1
    # status + elapsed are carried on the exercised POST row
    post_row = next(r for r in svcA["rows"]
                    if r[0] == "POST" and r[1] == "/v1/foos")
    assert post_row[4] == 201 and post_row[5] == 55.0


def test_declared_but_unrun_write_is_distinct_not_covered():
    """A write only declared in a scenario (no observation) is '◷', not ✓."""
    rows = [_row(200, "svcA/foo/listfoos", "GET", "/v1/foos", 1.0)]
    write_hit = {("DELETE", norm_path("/v1/foos/{foo_id}"))}
    svcA = _svc(per_service(CAT, rows, write_hit), "foo")

    assert _state(svcA, "DELETE", "/v1/foos/{foo_id}") == "declared"   # the DELETE
    assert svcA["wcov"] == 0 and svcA["wdecl"] == 1
    # a declared-not-run write carries no status (honest blank)
    del_row = next(r for r in svcA["rows"] if r[0] == "DELETE")
    assert del_row[4] is None


def test_path_collision_does_not_grant_false_check():
    """svcB/bar shares (POST, /v1/foos) with svcA but was never called → not exec
    and (since its own service has no declaration) not even declared."""
    rows = [_row(201, "svcA/foo/createfoo", "POST", "/v1/foos", 5.0)]
    write_hit = {("POST", norm_path("/v1/foos"))}  # declared via svcA's scenario
    services = per_service(CAT, rows, write_hit)
    svcB = _svc(services, "bar")

    # svcB's createbar was never observed: it must NOT be 'exec'.
    assert _state(svcB, "POST", "/v1/foos") != "exec"
    assert svcB["wcov"] == 0


def test_compute_write_coverage_is_exercised_not_declared():
    """compute().cov_write counts writes actually observed, and reports the
    declared surface separately."""
    rows = [
        _row(200, "svcA/foo/listfoos", "GET", "/v1/foos", 1.0),
        _row(201, "svcA/foo/createfoo", "POST", "/v1/foos", 1.0),
    ]
    # one lifecycle declaring two writes (create + delete)
    lifecycles = [{
        "id": "foo-life", "enabled": True, "service": "svcA/foo",
        "steps": [
            {"name": "c", "method": "POST", "path": "/v1/foos"},
            {"name": "d", "method": "DELETE", "path": "/v1/foos/{foo_id}"},
        ],
    }]
    d = compute(CAT, rows, {}, lifecycles, {"issues": []})

    # 4 non-GET endpoints in CAT; exactly 1 exercised (the POST create)
    assert d["write_hit"] == 1                 # exercised writes
    assert d["write_declared"] >= 1            # declared/planned surface
    assert 0 < d["cov_write"] < 100
    # GET denominator is GET-only-exercised, not all observations
    assert d["tested_get"] == 1
