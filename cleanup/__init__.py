"""cleanup — support concern C: guarantee teardown via a registry-driven reconciler.

The reconciler (``cleanup.reconciler``) sweeps the account for resources that
carry the ``owner=apitest`` tag or match a legacy run-stamped name prefix, and
deletes those whose run is finished or whose TTL has expired.

Usage::

    python -m cleanup.reconciler          # dry-run (prints candidates)
    SCP_ALLOW_DESTRUCTIVE=true python -m cleanup.reconciler

See ``cleanup/reconciler.py`` for the full contract.
"""
from __future__ import annotations

from cleanup import reconciler

__all__ = ["reconciler"]
