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
    """One VPC shared by the heavy CRUD lifecycles that {"adopt": "vpc"} it, so
    they don't each consume a slot against the 5-VPC cap (see
    knowledge/vpc-scheduling-strategy.md). Only provisioned for heavy mutating
    runs; otherwise yields {} and lifecycles self-create as before. Torn down
    once at session end (the tag-scoped sweep is the backstop)."""
    from regression.scenarios import engine
    if not (getattr(cfg, "allow_mutations", False) and getattr(cfg, "run_heavy", False)):
        yield {}
        return
    shared_ctx, teardown = engine.provision_shared_vpc(client, cfg)
    try:
        yield shared_ctx
    finally:
        teardown()
