"""catalog_run — "press execute on the topology": run the tests for the resource
node(s) selected on the catalog graph.

The full chain the scheduler ADR set out:

    pick node(s) on the topology
      → catalog_planner: dependency closure (the shared services pulled in)
      → map each closure resource to the lifecycle that exercises it (source.lifecycle)
      → dag_planner: cap-safe topological waves over those lifecycles
      → dag_runner: execute (provision shared roots → waves → teardown)

So selecting ``ske-cluster`` runs exactly the lifecycles that stand up + test
ske-cluster and everything it transitively needs (vpc/subnet, security-group,
keypair, filestorage-volume, …), in a VPC-cap-safe order — the same dag_runner
that was parity-validated against pytest-xdist (ADR 1.0-d).

Dry-run by default (credential-free); a live run needs ``SCP_DAG_RUNNER=true`` plus
the usual mutation/destructive/heavy safety gates.
"""
from __future__ import annotations

from regression.scenarios import catalog_planner, dag_planner, dag_runner


def leaf_set_for(targets, *, include_closure: bool = True) -> list[str]:
    """The runnable lifecycle leaf set for the selected target resource node(s)."""
    return catalog_planner.lifecycles_for(targets, include_closure=include_closure)


def plan_for(targets, *, include_closure: bool = True, vpc_cap: int | None = None):
    """Resolve target node(s) → leaf set → a dag_planner execution Plan."""
    leaf = leaf_set_for(targets, include_closure=include_closure)
    return leaf, dag_planner.plan(leaf_set=leaf, vpc_cap=vpc_cap)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(
        description="Run the tests for resource node(s) selected on the catalog topology.")
    ap.add_argument("--target", nargs="+", required=True,
                    help="resource node id(s) selected on the topology (e.g. ske-cluster)")
    ap.add_argument("--no-deps", action="store_true",
                    help="run only the target's own lifecycle(s), not the dependency closure")
    ap.add_argument("--vpc-cap", type=int, default=None)
    ap.add_argument("--max-workers", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the resolved plan; do not execute (default unless SCP_DAG_RUNNER=true)")
    args = ap.parse_args(argv)

    graph = catalog_planner.load_graph()
    cat = catalog_planner.plan(targets=args.target, graph=graph)
    leaf, plan = plan_for(args.target, include_closure=not args.no_deps, vpc_cap=args.vpc_cap)

    print(f"selected target(s): {', '.join(sorted(args.target))}")
    print(f"  → closure: {len(cat.closure)} resource(s) in {len(cat.layers)} create-layer(s)")
    print(f"  → runnable lifecycles ({len(leaf)}): {', '.join(leaf) or '(none)'}")
    if not leaf:
        print("nothing runnable for this selection.")
        return 0

    live = os.environ.get("SCP_DAG_RUNNER") == "true" and not args.dry_run
    if not live:
        print("\n-- execution plan (dry-run) --")
        print(dag_runner.format_run(dag_runner.dry_run(plan)))
        return 0

    from regression.scenarios import dag_runner_live
    executor, provisioner = dag_runner_live.build(plan, max_workers=args.max_workers)
    result = dag_runner.run_plan(plan, executor, provisioner=provisioner,
                                 max_workers=args.max_workers)
    print("\n-- run result --")
    print(dag_runner.format_run(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
