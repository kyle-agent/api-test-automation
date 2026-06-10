"""CLI entrypoint for the shared VPC+subnet used by the parallel-adopt CRUD run.

Provisions ONE shared VPC + ONE shared subnet ONCE (out-of-band of pytest) so
that every pytest-xdist worker can ADOPT the same live infra via env ids, and
the few VPC-creating lifecycles still self-create within the VPC cap.

Subcommands (used by .github/workflows/api-test.yml):

  python -m regression.scenarios.shared_infra --provision
      Create the shared VPC+subnet and print ``SCP_SHARED_VPC_ID=..`` /
      ``SCP_SHARED_SUBNET_ID=..`` to STDOUT (for ``>> $GITHUB_ENV``); all human
      diagnostics go to STDERR so stdout stays machine-parseable. No-op (prints
      nothing) unless SCP_ALLOW_MUTATIONS=true.

  python -m regression.scenarios.shared_infra --teardown
      Delete the shared subnet THEN vpc named by SCP_SHARED_SUBNET_ID /
      SCP_SHARED_VPC_ID. No-op without SCP_ALLOW_DESTRUCTIVE=true.

  python -m regression.scenarios.shared_infra --print-filters
      Print ``ADOPT_K=..`` / ``VPC_CRUD_K=..`` / ``PARALLEL_K=..`` pytest ``-k``
      expressions derived from dependencies.json (adopt vs vpc-crud
      classification) to STDOUT (for ``>> $GITHUB_ENV``). Does NOT need a client.

Import-safe: the API client is only built inside the provision/teardown paths,
so importing this module (and --print-filters) never requires credentials.
"""
from __future__ import annotations

import argparse
import sys

from regression.scenarios import engine


def _eprint(*a, **k):
    """Diagnostics -> stderr so stdout stays GITHUB_ENV-clean."""
    print(*a, file=sys.stderr, **k)


def _build_client():
    """Build the live API client (only here, so import + --print-filters are
    credential-free)."""
    from core.http_client import ApiClient
    from core.config import settings
    settings.require_credentials()
    return settings, ApiClient(settings)


# --------------------------------------------------------------------------- #
# filter derivation (data-driven from dependencies.json)
# --------------------------------------------------------------------------- #
def _crud_classification():
    """Return (adopt_ids, vpc_crud_ids) from dependencies.json, restricted to
    lifecycles that are actually ENABLED so the -k partition matches collection."""
    sched = engine.DEPENDENCIES.get("vpc_schedule", {})
    adopt = list(sched.get("adopt_lifecycles", []))
    vpc_crud = list(sched.get("vpc_crud_lifecycles", []))
    enabled = {lc["id"] for lc in engine.active_lifecycles()}
    adopt = [i for i in adopt if i in enabled]
    vpc_crud = [i for i in vpc_crud if i in enabled]
    return adopt, vpc_crud


def _k_or(ids):
    """Join lifecycle ids into a pytest -k OR expression. pytest matches each
    bare (hyphenated) term as a substring of the test node id; the enabled ids
    have no substring collisions (verified offline), so each term selects exactly
    its own test_crud_lifecycle[<id>] case."""
    return " or ".join(ids)


def print_filters():
    adopt, vpc_crud = _crud_classification()
    vpc_crud_k = _k_or(vpc_crud)
    adopt_k = _k_or(adopt)
    # PARALLEL_K selects everything that is NOT a vpc-crud lifecycle (i.e. the
    # adopt-class PLUS every other enabled CRUD lifecycle that touches no VPC).
    # This guarantees the two -k selections PARTITION all enabled CRUD cases:
    # VPC_CRUD_K and PARALLEL_K are exact complements.
    parallel_k = f"not ({vpc_crud_k})" if vpc_crud_k else ""
    print(f"ADOPT_K={adopt_k}")
    print(f"VPC_CRUD_K={vpc_crud_k}")
    print(f"PARALLEL_K={parallel_k}")
    _eprint(f"[shared_infra] {len(adopt)} adopt-class, {len(vpc_crud)} vpc-crud "
            f"lifecycle(s); PARALLEL_K is the complement of VPC_CRUD_K")
    return 0


# --------------------------------------------------------------------------- #
# provision / teardown
# --------------------------------------------------------------------------- #
def provision():
    cfg, client = _build_client()
    if not getattr(cfg, "allow_mutations", False):
        _eprint("[shared_infra] SCP_ALLOW_MUTATIONS not set — nothing to provision "
                "(adopters self-create); printing no env ids.")
        return 0
    # engine.provision_shared_vpc emits human diagnostics via plain print() ->
    # STDOUT. This entrypoint's STDOUT is redirected to $GITHUB_ENV by the
    # workflow, where ONLY well-formed `KEY=VALUE` lines are legal — a stray
    # "  shared VPC provisioned: ..." line makes the runner fail the step with
    # "Invalid format". So capture the engine's stdout onto STDERR and let ONLY
    # our explicit SCP_SHARED_*= lines below reach STDOUT.
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        shared_ctx, _teardown = engine.provision_shared_vpc(client, cfg)
    if not shared_ctx:
        _eprint("[shared_infra] could not provision shared VPC; adopters will "
                "self-create.")
        return 0
    vpc_id = shared_ctx.get("shared_vpc_id")
    subnet_id = shared_ctx.get("shared_subnet_id")
    db_subnet_id = shared_ctx.get("shared_db_subnet_id")
    if vpc_id:
        print(f"SCP_SHARED_VPC_ID={vpc_id}")
    if subnet_id:
        print(f"SCP_SHARED_SUBNET_ID={subnet_id}")
    if db_subnet_id:
        print(f"SCP_SHARED_DB_SUBNET_ID={db_subnet_id}")
    _eprint(f"[shared_infra] provisioned vpc={vpc_id} subnet={subnet_id} "
            f"db_subnet={db_subnet_id}")
    return 0


def teardown():
    import os
    cfg, client = _build_client()
    if not getattr(cfg, "allow_destructive", False):
        _eprint("[shared_infra] SCP_ALLOW_DESTRUCTIVE not set — skipping teardown "
                "(tag-scoped reconciler sweep is the backstop).")
        return 0
    vpc_id = os.environ.get(engine._ENV_SHARED_VPC, "").strip()
    subnet_id = os.environ.get(engine._ENV_SHARED_SUBNET, "").strip()
    db_subnet_id = os.environ.get(engine._ENV_SHARED_DB_SUBNET, "").strip()
    if not vpc_id and not subnet_id and not db_subnet_id:
        _eprint("[shared_infra] no SCP_SHARED_VPC_ID / SCP_SHARED_SUBNET_ID set — "
                "nothing to tear down.")
        return 0
    # subnets THEN vpc (children before parent)
    if db_subnet_id:
        try:
            client.request("DELETE", f"{engine._SUBNET_CREATE_PATH}/{db_subnet_id}",
                           service="vpc")
            _eprint(f"[shared_infra] shared DB subnet {db_subnet_id} deleted")
        except Exception as exc:
            _eprint(f"[shared_infra] shared DB subnet {db_subnet_id} delete failed "
                    f"({exc}); sweep will reclaim")
    if subnet_id:
        try:
            client.request("DELETE", f"{engine._SUBNET_CREATE_PATH}/{subnet_id}",
                           service="vpc")
            _eprint(f"[shared_infra] shared subnet {subnet_id} deleted")
        except Exception as exc:
            _eprint(f"[shared_infra] shared subnet {subnet_id} delete failed "
                    f"({exc}); sweep will reclaim")
    if vpc_id:
        try:
            client.request("DELETE", f"{engine._VPC_CREATE_PATH}/{vpc_id}",
                           service="vpc")
            _eprint(f"[shared_infra] shared VPC {vpc_id} deleted")
        except Exception as exc:
            _eprint(f"[shared_infra] shared VPC {vpc_id} delete failed ({exc}); "
                    f"sweep will reclaim")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--provision", action="store_true",
                   help="create shared VPC+subnet; print SCP_SHARED_* to stdout")
    g.add_argument("--teardown", action="store_true",
                   help="delete shared subnet then vpc named by SCP_SHARED_* env")
    g.add_argument("--print-filters", action="store_true",
                   help="print ADOPT_K/VPC_CRUD_K/PARALLEL_K pytest -k expressions")
    args = ap.parse_args(argv)
    if args.print_filters:
        return print_filters()
    if args.provision:
        return provision()
    if args.teardown:
        return teardown()
    return 1


if __name__ == "__main__":
    sys.exit(main())
