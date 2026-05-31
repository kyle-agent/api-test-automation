"""Shared pytest fixtures and CLI options for the SCP regression suite."""
from __future__ import annotations

import pytest

from framework.client import ApiClient
from framework.config import settings


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
