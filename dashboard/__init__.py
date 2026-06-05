"""dashboard — support concern B: visualize both regression and conformance axes.

Reads the unified results store (core.results) first; falls back to the legacy
flat-file inputs (reports/smoke_status.tsv, reports/param_status.tsv,
framework/conformance.json, reports/junit-crud.xml) so nothing regresses while
the migration is in flight.

Public surface:

    from dashboard.build import build

    build()                       # all defaults
    build(out="path/index.html")  # custom output
"""
from dashboard.build import build

__all__ = ["build"]
