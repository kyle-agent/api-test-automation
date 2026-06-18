"""Offline proof for design (A) stage 2 — identity-based read->producer matching.

The auto-probe resolves an id-bound GET's path-param by IDENTITY (the create that
produced the id, recorded per-lifecycle in `produced`) instead of by capture-var
string name. These tests prove, WITHOUT a live run, that:

  1. every `produced_by` path-param in the enrichment sidecar resolves via the
     identity path with an EMPTY name-seed (so neither exact-name nor the legacy
     alias map can be what resolved it) — i.e. identity alone is sufficient;
  2. the 8 legacy _PARAM_ALIASES targets that have a producer are now covered by
     identity (so the hand alias map is vestigial for them);
  3. resource_type is a valid secondary key;
  4. priority order holds (exact name > identity > alias).
"""
from regression.scenarios import engine


class _Ep:
    """Minimal stand-in for a catalog Endpoint (only .key is read)."""
    def __init__(self, key):
        self.key = key


def test_sidecar_and_producer_index_loaded():
    assert engine._PARAMS_SIDECAR, "enrichment sidecar failed to load"
    assert engine._PRODUCER_OF, "producer reverse-index is empty"


def test_every_produced_by_param_resolves_by_identity():
    """For each endpoint param with a producer, identity must resolve it from an
    empty name-seed (exact-name and alias both impossible -> identity proven)."""
    checked = 0
    for ekey, meta in engine._PARAMS_SIDECAR.items():
        ep = _Ep(ekey)
        for pp in meta.get("path_params", []):
            pk = pp.get("produced_by")
            if not pk:
                continue
            produced = {pk: "IDENT-VALUE"}
            got = engine._resolve_param(pp["name"], {}, ep, produced, {})
            assert got == "IDENT-VALUE", (
                f"{ekey} param {pp['name']!r} did not resolve by identity "
                f"(produced_by={pk}); got {got!r}")
            checked += 1
    assert checked > 500, f"expected to verify many params, only saw {checked}"


def test_legacy_alias_targets_now_covered_by_identity():
    """The 8 _PARAM_ALIASES targets that have a producer resolve via identity
    with NO alias involvement (empty seed)."""
    want = {"registry_id", "repository_id", "dbaas_engine_version_id",
            "certificate_id", "resource_group_id", "security_group_id",
            "security_group_rule_id", "service_account_id"}
    covered = set()
    for ekey, meta in engine._PARAMS_SIDECAR.items():
        ep = _Ep(ekey)
        for pp in meta.get("path_params", []):
            if pp["name"] in want and pp.get("produced_by"):
                produced = {pp["produced_by"]: "X"}
                if engine._resolve_param(pp["name"], {}, ep, produced, {}) == "X":
                    covered.add(pp["name"])
    assert covered == want, f"identity did not cover: {want - covered}"


def test_resource_type_is_secondary_key():
    for ekey, meta in engine._PARAMS_SIDECAR.items():
        for pp in meta.get("path_params", []):
            rt = pp.get("resource_type")
            if pp.get("produced_by") and rt:
                ep = _Ep(ekey)
                # producer index miss, resource_type hit
                got = engine._resolve_param(pp["name"], {}, ep, {}, {rt: "BY-RTYPE"})
                assert got == "BY-RTYPE"
                return
    raise AssertionError("no produced_by+resource_type param found to test")


def test_priority_exact_name_beats_identity():
    for ekey, meta in engine._PARAMS_SIDECAR.items():
        for pp in meta.get("path_params", []):
            pk = pp.get("produced_by")
            if pk:
                ep = _Ep(ekey)
                got = engine._resolve_param(
                    pp["name"], {pp["name"]: "EXACT"}, ep, {pk: "IDENT"}, {})
                assert got == "EXACT", "exact name-seed must win over identity"
                return
    raise AssertionError("no produced_by param found")
