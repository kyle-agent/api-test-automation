"""spec — support concern A: extract the API spec from docs and track changes.

Sub-modules
-----------
extract_catalog  Extract endpoints from the docs site into data/api_catalog.json.
extract_bodies   Extract example request bodies into data/api_bodies.json.
summary          Print a human-readable coverage summary of the catalog.
diff             Diff two catalog JSON snapshots; surface added/removed/changed endpoints.

Data files live at their original paths (data/api_catalog.json,
data/api_bodies.json) — physical relocation is a later step.
"""
from __future__ import annotations
