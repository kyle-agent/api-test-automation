"""dashboard — support concern B: visualize both regression and conformance axes.

Reads the unified results store (core.results) first; falls back to the legacy
flat-file inputs (reports/smoke_status.tsv, reports/param_status.tsv,
data/conformance.json, reports/junit-crud.xml) so nothing regresses while
the migration is in flight.

Public surface (exposed as ``build_dashboard`` so it does not shadow the
``dashboard.build`` submodule):

    from dashboard import build_dashboard      # the entrypoint function
    from dashboard.build import build          # equivalently, from the module
    # or:  python -m dashboard.build

    build_dashboard()                          # all defaults
    build_dashboard(out="path/index.html")     # custom output
"""
from dashboard.build import build as build_dashboard

__all__ = ["build_dashboard"]
