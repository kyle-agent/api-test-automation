"""AXIS 2 — conformance: is the API well designed & implemented?

This package finds design/implementation defects via two complementary lenses and
reports them against a baseline so only NEW defects alarm:

  * :mod:`conformance.static`   — static analysis of the spec (design findings).
  * :mod:`conformance.runtime`  — read-only / empty-body runtime probes
    (behavior findings); strictly non-destructive.
  * :mod:`conformance.baseline` — diff current conformance vs a stored baseline.
  * :mod:`conformance.rules`    — a pluggable rule framework so the "lens" can be
    extended by adding rule modules (see :class:`conformance.rules.Rule`).

All defects are emitted as :class:`core.results.Finding` to the unified results
store (``source="static"|"runtime"``). For backwards compatibility the legacy
artifacts (``data/conformance.json``, ``reports/runtime_*.json``,
``reports/conformance_new.json``) are still written (dual-write) so the current
dashboard and baseline keep working.

Importing this package (and its submodules) is side-effect free: no network I/O
and no file writes happen at import time — those occur only inside the functions
and ``main()`` entrypoints.
"""
from __future__ import annotations

from conformance import rules  # noqa: F401  (registers built-in rules on import)

__all__ = ["rules"]
