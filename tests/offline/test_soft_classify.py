"""HEAVY-PREMISE-CONTRACT §4 — unit tests for regression.soft_classify.

Hermetic: tiny inline catalog / waivers / verified fixtures, no files, no
network. Covers both observation ``endpoint_key`` shapes (``category/service/op``
and ``<lifecycle>:<step>``), the policy > duplicate > gap priority, duplicate
evidence from the same run AND from the verified store (cross-shape), pure
gaps, and that non-soft entries are ignored.
"""
from regression.soft_classify import (
    DUPLICATE,
    GAP,
    POLICY,
    build_run_2xx,
    classify,
    endpoint_token,
    summarize,
)

# ------------------------------------------------------------------ fixtures
# Inline mini-catalog (data/api_catalog.json item shape: key/method/http_path).
CATALOG = [
    {"key": "compute/virtualserver/lockvirtualserver",
     "method": "POST", "http_path": "/v1/servers/{server_id}/lock"},
    {"key": "compute/virtualserver/unlockvirtualserver",
     "method": "POST", "http_path": "/v1/servers/{server_id}/unlock"},
    {"key": "compute/virtualserver/listservervolumes",
     "method": "GET", "http_path": "/v1/servers/{server_id}/volumes"},
    {"key": "compute/virtualserver/rebuildvirtualserver",
     "method": "POST", "http_path": "/v1/servers/{server_id}/rebuild"},
    {"key": "compute/virtualserver/createvirtualserverdump",
     "method": "POST", "http_path": "/v1/servers/{server_id}/dump"},
    {"key": "networking/vpc/createvpc",
     "method": "POST", "http_path": "/v1/vpcs"},
]

# coverage_waivers.json "waivers" list shape. Only class=="reachability" is
# policy; the billing-prohibitive entry must NOT classify as policy.
WAIVERS = [
    {"key": "compute/virtualserver/lockvirtualserver", "class": "reachability",
     "reason": "test: reached is the definition-of-done"},
    {"key": "compute/virtualserver/unlockvirtualserver", "class": "reachability",
     "reason": "test: reached is the definition-of-done"},
    {"key": "networking/vpc/createvpc", "class": "billing-prohibitive",
     "reason": "test: EXCLUDED class, not policy"},
]

# verified_endpoints.json shape — keys deliberately in BOTH shapes:
#   * colon (lifecycle:step) entry proving GET /v1/servers/{id}/volumes
#   * slash (catalog key) entry proving POST /v1/vpcs
VERIFIED = {
    "compute-virtualserver-full:list-server-volumes": {
        "method": "GET", "path": "/v1/servers/{server_id}/volumes",
        "norm_path": "v1/servers/*/volumes", "count": 7,
        "first_run": "r1", "last_run": "r2",
    },
    "networking/vpc/createvpc": {
        "method": "POST", "path": "/v1/vpcs",
        "norm_path": "v1/vpcs", "count": 3,
        "first_run": "r1", "last_run": "r2",
    },
}


def _soft(key, method, path, status=404):
    return {"endpoint_key": key, "method": method, "path": path,
            "status": status, "category": "soft"}


# ------------------------------------------------------------------- classify

def test_policy_colon_shape_resolves_via_catalog():
    # lifecycle:step key -> method+norm_path -> catalog key -> reachability waiver.
    obs = [_soft("vs-actions:lock-server", "POST", "/v1/servers/{server_id}/lock")]
    got = classify(obs, verified={}, waivers=WAIVERS, run_endpoint_2xx=set(),
                   catalog=CATALOG)
    assert got == {0: POLICY}


def test_policy_slash_shape_exact_key():
    # catalog-key-shaped endpoint_key matches the waiver key directly.
    obs = [_soft("compute/virtualserver/unlockvirtualserver",
                 "POST", "/v1/servers/{server_id}/unlock")]
    got = classify(obs, verified={}, waivers=WAIVERS, run_endpoint_2xx=set(),
                   catalog=CATALOG)
    assert got == {0: POLICY}


def test_policy_beats_duplicate():
    # Waived endpoint that ALSO has 2xx evidence in the same run AND in the
    # verified store -> still policy (contract priority policy > duplicate).
    obs = [_soft("vs-actions:lock-server", "POST", "/v1/servers/{server_id}/lock")]
    run_2xx = {"POST v1/servers/*/lock", "vs-actions:lock-server"}
    verified = {"compute/virtualserver/lockvirtualserver": {
        "method": "POST", "path": "/v1/servers/{server_id}/lock",
        "norm_path": "v1/servers/*/lock", "count": 1}}
    got = classify(obs, verified=verified, waivers=WAIVERS,
                   run_endpoint_2xx=run_2xx, catalog=CATALOG)
    assert got == {0: POLICY}


def test_non_reachability_waiver_is_not_policy():
    # billing-prohibitive waiver class must NOT classify as policy; with 2xx
    # evidence in verified it is a duplicate instead.
    obs = [_soft("networking/vpc/createvpc", "POST", "/v1/vpcs", status=409)]
    got = classify(obs, verified=VERIFIED, waivers=WAIVERS,
                   run_endpoint_2xx=set(), catalog=CATALOG)
    assert got == {0: DUPLICATE}


def test_duplicate_via_run_2xx_cross_shape():
    # The 2xx was recorded by the sweep under the CATALOG key; the soft entry
    # comes from the engine under lifecycle:step. build_run_2xx's token bridges.
    run_obs = [{"endpoint_key": "compute/virtualserver/listservervolumes",
                "method": "GET", "path": "/v1/servers/{server_id}/volumes",
                "status": 200, "category": "ok"}]
    run_2xx = build_run_2xx(run_obs)
    obs = [_soft("vs-actions:list-server-volumes",
                 "GET", "/v1/servers/{server_id}/volumes")]
    got = classify(obs, verified={}, waivers=[], run_endpoint_2xx=run_2xx,
                   catalog=CATALOG)
    assert got == {0: DUPLICATE}


def test_duplicate_via_verified_store_both_directions():
    obs = [
        # slash-shape soft vs COLON-shape verified entry (method+norm_path).
        _soft("compute/virtualserver/listservervolumes",
              "GET", "/v1/servers/{server_id}/volumes"),
        # colon-shape soft vs SLASH-shape verified entry (method+norm_path).
        _soft("net-vpc:create-vpc", "POST", "/v1/vpcs", status=409),
    ]
    got = classify(obs, verified=VERIFIED, waivers=[], run_endpoint_2xx=set(),
                   catalog=CATALOG)
    assert got == {0: DUPLICATE, 1: DUPLICATE}


def test_pure_gap():
    # Not waived, no 2xx anywhere -> recipe still owed.
    obs = [_soft("vs-actions:rebuild-server",
                 "POST", "/v1/servers/{server_id}/rebuild")]
    got = classify(obs, verified=VERIFIED, waivers=WAIVERS,
                   run_endpoint_2xx=set(), catalog=CATALOG)
    assert got == {0: GAP}


def test_non_soft_entries_get_no_key():
    obs = [
        {"endpoint_key": "compute/virtualserver/lockvirtualserver",
         "method": "POST", "path": "/v1/servers/{server_id}/lock",
         "status": 200, "category": "ok"},       # ok — skipped even if waived
        {"endpoint_key": "networking/vpc/createvpc", "method": "POST",
         "path": "/v1/vpcs", "status": 500, "category": "fail"},  # fail — skipped
        _soft("vs-actions:dump-server", "POST", "/v1/servers/{server_id}/dump"),
    ]
    got = classify(obs, verified=VERIFIED, waivers=WAIVERS,
                   run_endpoint_2xx=set(), catalog=CATALOG)
    assert got == {2: GAP}  # only the soft observation is classified


def test_indices_are_positional_and_query_string_normalized():
    # Index keys track the observations list; a query string on the observed
    # path must not defeat matching (norm_path strips it).
    obs = [
        {"endpoint_key": "x", "method": "GET", "path": "/v1/x",
         "status": 200, "category": "ok"},
        _soft("vs-actions:list-server-volumes",
              "GET", "/v1/servers/{server_id}/volumes?limit=1"),
    ]
    got = classify(obs, verified=VERIFIED, waivers=[], run_endpoint_2xx=set(),
                   catalog=CATALOG)
    assert got == {1: DUPLICATE}


def test_colon_shape_without_catalog_cannot_be_policy():
    # Documented degradation: no catalog -> lifecycle:step keys cannot resolve
    # to a waiver, but duplicate matching by method+path still works.
    obs = [
        _soft("vs-actions:lock-server", "POST", "/v1/servers/{server_id}/lock"),
        _soft("vs-actions:list-server-volumes",
              "GET", "/v1/servers/{server_id}/volumes"),
    ]
    got = classify(obs, verified=VERIFIED, waivers=WAIVERS,
                   run_endpoint_2xx=set())  # catalog omitted
    assert got == {0: GAP, 1: DUPLICATE}


def test_waivers_whole_file_dict_tolerated():
    obs = [_soft("compute/virtualserver/lockvirtualserver",
                 "POST", "/v1/servers/{server_id}/lock")]
    got = classify(obs, verified={}, waivers={"waivers": WAIVERS},
                   run_endpoint_2xx=set(), catalog=CATALOG)
    assert got == {0: POLICY}


# ---------------------------------------------------------------- helpers

def test_build_run_2xx_only_real_2xx():
    obs = [
        {"endpoint_key": "a/b/c", "method": "GET", "path": "/v1/x",
         "status": 200, "category": "ok"},
        # soft 2xx IS evidence (same rule as derive_verified)
        {"endpoint_key": "lc:step", "method": "POST", "path": "/v1/y/{id}",
         "status": 201, "category": "soft"},
        # non-2xx never enters, whatever the category
        {"endpoint_key": "d/e/f", "method": "GET", "path": "/v1/z",
         "status": 404, "category": "soft"},
        {"endpoint_key": "g/h/i", "method": "GET", "path": "/v1/w",
         "status": 500, "category": "fail"},
    ]
    got = build_run_2xx(obs)
    assert got == {"a/b/c", "GET v1/x", "lc:step", "POST v1/y/*"}


def test_endpoint_token_norm_and_empties():
    assert endpoint_token("post", "/v1/servers/{id}/lock/") == "POST v1/servers/*/lock"
    assert endpoint_token("GET", "/v1/x?limit=1") == "GET v1/x"
    assert endpoint_token("", "/v1/x") == ""
    assert endpoint_token("GET", "") == ""


def test_summarize_counts_and_zero_defaults():
    assert summarize({}) == {"duplicate": 0, "gap": 0, "policy": 0}
    class_map = {0: POLICY, 3: GAP, 5: GAP, 7: DUPLICATE, 9: GAP}
    assert summarize(class_map) == {"duplicate": 1, "gap": 3, "policy": 1}
