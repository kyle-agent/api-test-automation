"""Read-only GET coverage booster via list->show chaining — THIN entrypoint.

The catalog smoke suite can only call GETs WITHOUT path params; every
``show{X}/{id}`` is skipped because it needs a real resource id. Many
single-path-param GETs, though, take an id freely derivable from a sibling
*list*. The derivation + call + record logic lives in the
:mod:`regression.read_chains` engine; this module is only the pytest glue:
one parametrized case per derivable chain (1-param and 2-param families), the
``smoke`` marker, and a session-scoped list cache shared across cases.

Read-only and record-only (like the CRUD ``probe_reads`` step): a derived read
is recorded to core.results + the legacy smoke TSV but never turns the gate red.
"""
from __future__ import annotations

import pytest

from regression import read_chains

pytestmark = pytest.mark.smoke

# None = all services. Set to a list to scope (e.g. ["virtualserver"]).
_CHAIN_SERVICES = None

_CHAINS = read_chains.single_param_chains(_CHAIN_SERVICES)
_CHAINS_2P = read_chains.two_param_chains(_CHAIN_SERVICES)


@pytest.fixture(scope="session")
def _list_cache():
    """Cache parent-list responses for the session: many show endpoints share one
    collection, so we list each (service, path) at most once."""
    return {}


@pytest.mark.parametrize(
    "endpoint,param,list_path",
    _CHAINS,
    ids=[e.key for e, _, _ in _CHAINS] or ["none"],
)
def test_read_chain(endpoint, param, list_path, client, _list_cache):
    """Derive the path-param id from the sibling list and exercise the show GET."""
    res = read_chains.run_chain(endpoint, param, list_path, client, _list_cache)
    if res.get("skipped"):
        pytest.skip(res.get("reason", "skipped"))
    # Record-only: a 4xx/5xx on a derived read is surfaced on the dashboard, not
    # asserted here, so bonus coverage can never by itself turn the gate red.


@pytest.mark.parametrize(
    "endpoint,p1,list1,p2,sublist_tmpl",
    _CHAINS_2P,
    ids=[e.key for e, *_ in _CHAINS_2P] or ["none"],
)
def test_read_chain_2p(endpoint, p1, list1, p2, sublist_tmpl, client, _list_cache):
    """Two-level derive: param1 from the parent list, then param2 from the
    sub-list (with param1 filled), then exercise the two-param show GET."""
    res = read_chains.run_chain_2p(endpoint, p1, list1, p2, sublist_tmpl, client, _list_cache)
    if res.get("skipped"):
        pytest.skip(res.get("reason", "skipped"))
    # Record-only, exactly like test_read_chain above.
