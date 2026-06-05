"""spec — support concern A: extract the API spec from docs and track changes.

Sub-modules
-----------
extract_catalog  Extract endpoints from the docs site into data/api_catalog.json.
extract_bodies   Extract example request bodies into data/api_bodies.json.
summary          Print a human-readable coverage summary of the catalog.
diff             Diff two catalog JSON snapshots; surface added/removed/changed endpoints.

OpenAPI-snapshot + export/lint tooling (ported from the conformance session)
----------------------------------------------------------------------------
scrape_docs      Scrape full per-page docs (params/responses/examples/models)
                 into data/api_docs.json via suffix HTTP Range requests.
build_openapi    Assemble data/api_docs.json into one OpenAPI 3.0 spec per
                 service under data/openapi/ (+ index.json).
merge_shards     Merge sharded scrape output (data/shards/*.json) into a single
                 data/api_docs.json.
export_csv       Emit full-analysis CSVs (reports/csv/) for dev hand-off:
                 required-field constraints, error-response coverage, runtime
                 validation-probe verdicts.
lint_spectral.sh Spectral lint over the generated OpenAPI (data/openapi/*.json)
                 using spec/spectral.yaml; writes reports/spectral.json.

Data files live under data/ (data/api_catalog.json, data/api_bodies.json,
data/api_docs.json, data/openapi/, data/shards/); the conformance session's
original framework/ paths were relocated to data/ to match the new layout.
"""
from __future__ import annotations
