"""CRUD lifecycle scenarios (AXIS 1).

The lifecycle data lives in :file:`scenarios.json` (verbatim copy of the legacy
``tests/crud/lifecycles.json``) and prerequisite / quota metadata in
:file:`dependencies.json`. The runner is :mod:`regression.scenarios.engine`.
"""
from __future__ import annotations

__all__ = ["engine"]
