"""AXIS 1 — does the API work?

The regression package answers the operational question: when we call the API,
does it respond correctly, and how fast? It holds three engines, all importable
as plain functions (not pytest-bound) so a scheduler / thin pytest entrypoint /
CI orchestrator can drive them directly:

  * :mod:`regression.smoke`        — catalog smoke: bare read-only GETs, the
    ok/soft/fail categorize, response-time recording.
  * :mod:`regression.read_chains`  — list->show chaining (1-param and 2-param)
    that derives ids from sibling lists to exercise path-param GETs with zero
    resource creation.
  * :mod:`regression.scenarios`    — ordered CRUD lifecycles that create and
    delete real resources, integrated with the kernel's registry (ownership +
    teardown) and budgets (quota-aware scheduling).

Every call records a :class:`core.results.Observation` to the unified results
store, while still dual-writing the legacy ``reports/smoke_status.tsv`` so the
existing dashboard keeps working during the restructure.
"""
from __future__ import annotations

__all__ = ["smoke", "read_chains"]
