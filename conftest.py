"""Shared pytest fixtures and CLI options for the SCP regression suite."""
# CRUD lifecycle run: resource-group + vpc/subnet + virtualserver (opt-in).
from __future__ import annotations

import pytest

from core.http_client import ApiClient
from core.config import settings


def pytest_addoption(parser):
    parser.addoption("--category", default=None,
                     help="Limit smoke tests to one API category (e.g. compute).")
    parser.addoption("--service", default=None,
                     help="Limit smoke tests to one service (e.g. baremetal).")


@pytest.fixture(scope="session")
def cfg():
    return settings


@pytest.fixture(scope="session")
def client(cfg):
    cfg.require_credentials()
    return ApiClient(cfg)


@pytest.fixture(scope="session")
def shared_vpc(client, cfg):
    """One VPC + one subnet shared by the ADOPT-class CRUD lifecycles (they carry
    {"adopt":"vpc"}/{"adopt":"subnet"}) so they don't each consume a slot against
    the 5-VPC cap (knowledge/vpc-scheduling-strategy.md).

    xdist-safe — three modes:
      * env ids set (SCP_SHARED_VPC_ID[/SUBNET_ID]) -> adopt the already-live
        infra, NO creation, NO teardown (the provisioner that set the env owns
        teardown). This is the CI path: shared_infra --provision creates once,
        all xdist workers adopt the same ids.
      * else, running UNDER an xdist worker (PYTEST_XDIST_WORKER set) but no env
        ids -> yield {} (never provision per-worker; that would race + multiply
        VPCs). Workers in this state self-create per lifecycle as before.
      * else (single-process, no env ids) -> provision once + tear down at
        session end, exactly as before. Only for heavy mutating runs; otherwise
        yield {} and lifecycles self-create.
    """
    import os
    from regression.scenarios import engine

    # 0) explicit opt-out (SCP_SHARED_VPC_DISABLE=true). The A∥B-split VPC-CRUD
    #    job sets this: its lifecycles never adopt the shared VPC, and a
    #    self-provisioned one would burn a slot of the 3-VPC cap that job A's
    #    shared VPC + a 2-VPC lifecycle (vpc-peering) already fill exactly.
    if os.environ.get("SCP_SHARED_VPC_DISABLE", "").strip().lower() == "true":
        yield {}
        return

    # 1) adopt pre-provisioned live infra by env (provision_shared_vpc is itself
    #    env-aware: it returns the env ids + a no-op teardown).
    if os.environ.get("SCP_SHARED_VPC_ID", "").strip():
        shared_ctx, _ = engine.provision_shared_vpc(client, cfg)
        yield shared_ctx
        return

    # 2) under an xdist worker without env ids: never provision per-worker.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        yield {}
        return

    # 3) single-process: provision once (heavy mutating runs only) + teardown.
    if not (getattr(cfg, "allow_mutations", False) and getattr(cfg, "run_heavy", False)):
        yield {}
        return
    shared_ctx, teardown = engine.provision_shared_vpc(client, cfg)
    try:
        yield shared_ctx
    finally:
        teardown()
