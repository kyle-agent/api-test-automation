"""Offline gate-refusal test for the LIVE DAG-runner adapters (dag_runner_live).

Hermetic: no credentials, no network, no client. It only proves two contracts:

  1. Importing ``dag_runner_live`` is always safe (no gate read at import time),
     matching ``shared_infra``'s import-safe behaviour.
  2. ``build()`` — the live path — REFUSES (raises RuntimeError naming the gate)
     when SCP_ALLOW_MUTATIONS is unset and the plan needs shared roots, per
     CLAUDE.md Hard Rule 1.

The Settings dataclass reads SCP_ALLOW_MUTATIONS at construction; the adapter
re-reads Settings inside build(), so toggling the env var here is sufficient (no
import-order coupling).
"""
from __future__ import annotations

import pytest

from regression.scenarios import dag_planner, dag_runner_live


def _plan_with_shared_roots() -> dag_planner.Plan:
    """A minimal Plan whose shared_roots are non-empty (the case that must be
    gated). We construct it directly rather than via plan() so the test needs no
    dependencies.json shape assumptions."""
    return dag_planner.Plan(
        leaf_set=["adopter-x"],
        shared_roots=["vpc", "subnet"],
        waves=[dag_planner.Wave(kind="provision", lifecycles=["vpc", "subnet"])],
        adopters=["adopter-x"],
    )


def test_import_is_credential_free():
    # Importing must not require creds or read any gate (already imported above).
    assert hasattr(dag_runner_live, "build")
    assert hasattr(dag_runner_live, "SharedInfraProvisioner")


def test_build_refuses_without_mutation_gate(monkeypatch):
    # Ensure the gate is unset.
    monkeypatch.delenv("SCP_ALLOW_MUTATIONS", raising=False)
    plan = _plan_with_shared_roots()
    with pytest.raises(RuntimeError) as exc:
        dag_runner_live.build(plan)
    msg = str(exc.value)
    assert "SCP_ALLOW_MUTATIONS" in msg
    assert "dag_runner_live" in msg


def test_build_refuses_with_gate_explicitly_false(monkeypatch):
    monkeypatch.setenv("SCP_ALLOW_MUTATIONS", "false")
    plan = _plan_with_shared_roots()
    with pytest.raises(RuntimeError, match="SCP_ALLOW_MUTATIONS"):
        dag_runner_live.build(plan)
