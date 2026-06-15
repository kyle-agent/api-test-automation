"""IB-041 — unit tests for tools.derive_verified.

Verifies the masked-defect-safe evidence rule: ONLY endpoints that observed a
real 2xx (200–299) land in verified_endpoints.json — regardless of whether the
step was soft/optional — and that the merge is accumulative + idempotent on the
key set (the contract the promoter consumes).
"""
import json
from pathlib import Path

from tools import derive_verified as dv


def _write_obs(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


# A 2xx ok, a 2xx soft, a 4xx soft, a 5xx fail.
SYNTHETIC = [
    {"endpoint_key": "ep.ok.2xx", "method": "GET", "path": "/v1/vpcs",
     "status": 200, "category": "ok", "source": "smoke", "run": "run1"},
    {"endpoint_key": "ep.soft.2xx", "method": "POST", "path": "/v1/vpcs/{id}/subnets",
     "status": 201, "category": "soft", "source": "crud_probe", "run": "run1"},
    {"endpoint_key": "ep.soft.4xx", "method": "POST", "path": "/v1/keypairs",
     "status": 404, "category": "soft", "source": "crud_probe", "run": "run1"},
    {"endpoint_key": "ep.fail.5xx", "method": "DELETE", "path": "/v1/secrets/{id}",
     "status": 500, "category": "fail", "source": "crud_probe", "run": "run1"},
]


def test_only_2xx_land(tmp_path):
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "verified_endpoints.json"
    _write_obs(obs, SYNTHETIC)

    merged = dv.run(obs, out)

    # exactly the two 2xx endpoints (ok + soft), non-2xx excluded
    assert set(merged) == {"ep.ok.2xx", "ep.soft.2xx"}
    assert "ep.soft.4xx" not in merged   # 404 soft = reached, NOT verified
    assert "ep.fail.5xx" not in merged   # 5xx fail never verified

    # schema check
    rec = merged["ep.soft.2xx"]
    assert rec["method"] == "POST"
    assert rec["path"] == "/v1/vpcs/{id}/subnets"
    assert rec["norm_path"] == "v1/vpcs/*/subnets"
    assert rec["first_run"] == "run1"
    assert rec["last_run"] == "run1"
    assert rec["count"] == 1

    # 2xx-soft is INCLUDED even though it was a soft/optional step — the whole
    # point of IB-041 (a real 2xx is evidence regardless of step tolerance).
    assert "ep.soft.2xx" in merged

    # the file on disk matches the returned dict
    assert json.loads(out.read_text()) == merged


def test_merge_accumulates_keyset_idempotent(tmp_path):
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "verified_endpoints.json"
    _write_obs(obs, SYNTHETIC)

    first = dv.run(obs, out)
    # re-run on the SAME observations: key set is unchanged (idempotent contract)
    second = dv.run(obs, out)
    assert set(second) == set(first)
    # first_run is preserved across re-runs
    assert second["ep.ok.2xx"]["first_run"] == "run1"


def test_merge_accumulates_new_run(tmp_path):
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "verified_endpoints.json"

    # run1 verifies one endpoint
    _write_obs(obs, [SYNTHETIC[0]])
    dv.run(obs, out)

    # run2 verifies a DIFFERENT endpoint + re-verifies the first
    _write_obs(obs, [
        {"endpoint_key": "ep.ok.2xx", "method": "GET", "path": "/v1/vpcs",
         "status": 200, "category": "ok", "run": "run2"},
        {"endpoint_key": "ep.new.2xx", "method": "POST", "path": "/v1/buckets",
         "status": 200, "category": "ok", "run": "run2"},
    ])
    merged = dv.run(obs, out)

    # accumulative union — run1's endpoint survives
    assert set(merged) == {"ep.ok.2xx", "ep.new.2xx"}
    # first_run preserved, last_run advanced, count accumulated
    rec = merged["ep.ok.2xx"]
    assert rec["first_run"] == "run1"
    assert rec["last_run"] == "run2"
    assert rec["count"] == 2


def test_empty_observations_writes_empty(tmp_path):
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "verified_endpoints.json"
    obs.write_text("")
    merged = dv.run(obs, out)
    assert merged == {}
    assert json.loads(out.read_text()) == {}


def test_missing_observations_file(tmp_path):
    out = tmp_path / "verified_endpoints.json"
    merged = dv.run(tmp_path / "nope.jsonl", out)
    assert merged == {}


def test_malformed_lines_skipped(tmp_path):
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "verified_endpoints.json"
    obs.write_text(
        json.dumps(SYNTHETIC[0]) + "\n"
        + "not json at all\n"
        + json.dumps(SYNTHETIC[1]) + "\n"
    )
    merged = dv.run(obs, out)
    assert set(merged) == {"ep.ok.2xx", "ep.soft.2xx"}
