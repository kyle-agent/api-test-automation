"""Offline guard for conformance RUNTIME observation recording.

`conformance.runtime` probes call real endpoints (incl. POST/PUT) and, besides
emitting design `Finding`s, now ALSO record a `core.results.Observation` per
endpoint they actually exercise (``source="runtime_probe"``) so the dashboard's
per-endpoint "최근 status (HTTP code + response time)" column is populated by
conformance-exercised endpoints too.

This test runs fully offline with a fake client (no creds, no network). The
results store reads ``APITEST_RESULTS_DIR`` at *import time*, so the probe is
driven in a child process that sets the env BEFORE importing ``core.results`` /
``conformance.runtime`` — guaranteeing the temp dir is honoured regardless of
what the parent pytest process already imported.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# A self-contained probe driver executed in a clean interpreter. It points the
# results store at argv[1] (set via env BEFORE importing core.results), builds a
# minimal docs dict + fake client, runs probe_validation (a POST probe), and the
# observations.jsonl it writes is then asserted by the parent.
_DRIVER = textwrap.dedent(
    """
    import os, sys
    os.environ["APITEST_RESULTS_DIR"] = sys.argv[1]
    sys.path.insert(0, sys.argv[2])

    import conformance.runtime as rt  # imports core.results with env already set

    EP_KEY = "compute/virtualserver/createserver"
    DOCS = {
        "models": {
            "compute/virtualserver/CreateBody": {
                "fields": [{"name": "name", "required": True}],
            }
        },
        "endpoints": {
            EP_KEY: {
                "category": "compute", "service": "virtualserver",
                "name": "createserver", "method": "POST", "path": "/v1/servers",
                "parameters": [
                    {"in": "body", "schema_ref": "CreateBody"},
                ],
            }
        },
    }

    class FakeResp:
        def __init__(self):
            self.status = 400
            self.elapsed_ms = 42.5
            self.body = {"message": "name is required"}
            self.raw_text = '{"message": "name is required"}'

    class FakeClient:
        def request(self, method, path, **kw):
            assert method == "POST" and path == "/v1/servers"
            return FakeResp()

    rt.probe_validation(FakeClient(), DOCS, limit=0, category="", sleep=0)
    print("driver ok")
    """
)


def test_post_probe_records_observation(tmp_path):
    results_dir = tmp_path / "results"
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(results_dir), str(ROOT)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"driver failed:\n{proc.stdout}\n{proc.stderr}"

    obs_file = results_dir / "observations.jsonl"
    assert obs_file.exists(), f"no observations written; driver said:\n{proc.stdout}"

    records = [json.loads(ln) for ln in obs_file.read_text().splitlines() if ln.strip()]
    runtime_obs = [r for r in records if r.get("source") == "runtime_probe"]
    assert len(runtime_obs) == 1, f"expected one runtime_probe observation, got {records}"

    o = runtime_obs[0]
    assert o["endpoint_key"] == "compute/virtualserver/createserver"
    assert o["method"] == "POST"
    assert o["path"] == "/v1/servers"
    assert o["status"] == 400
    assert o["elapsed_ms"] == 42.5
    assert o["source"] == "runtime_probe"
    # 400 (validation) is a "soft" category per the shared smoke classifier.
    assert o["category"] == "soft"
