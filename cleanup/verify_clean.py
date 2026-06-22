"""Read-only cleanup verification: reuse the reconciler's EXACT collection list +
dependency order, but stub out _delete/_wait_gone/sleep so nothing is deleted —
just report, per (service, collection), how many OWNED (regr*/owner-tag) resources
still survive. A non-zero survivor means cleanup didn't complete for that service
(often because a dependency is still blocking it).

Exposes ``scan_owned(client=None) -> list[dict]`` so other tools (e.g. the console2
server's pre-flight "남은 자원" panel) can consume the structured inventory without
shelling out and re-parsing stdout. The ``__main__`` CLI prints the same data.
"""
import os
os.environ.setdefault("SCP_SWEEP_IGNORE_TTL", "true")  # count unexpired too (full inventory)
# Fast-fail listing: this is a read-only inventory, so don't sink 60s x retries on
# a slow/unreachable service host — a short deadline keeps the full sweep tractable.
os.environ.setdefault("SCP_TIMEOUT", "8")
os.environ.setdefault("SCP_MAX_RETRIES", "1")
import time as _t
_t.sleep = lambda *a, **k: None                        # no internal waits/backoff
import cleanup.reconciler as r  # noqa: E402 — must follow the env/sleep setup above
import core  # noqa: E402 — must follow the env/sleep setup above


def scan_owned(client=None) -> list[dict]:
    """Run the reconciler sweep in a deleteless DRY mode and return the structured
    inventory of OWNED resources it WOULD delete: ``[{"service": ..., "path": ...},
    ...]`` (one entry per delete the sweep would issue). Read-only — _delete /
    _wait_gone are stubbed so nothing is touched, only LIST calls are made.

    ``client`` defaults to a fresh ``core.ApiClient(core.settings)``. Patching the
    module-level ``r._delete`` / ``r._wait_gone`` is local to this call and restored
    afterwards, so importing this module has no lingering effect on the reconciler.
    """
    attempts: list[tuple[str, str]] = []  # (service, path) — every delete the sweep WOULD issue

    def fake_delete(client, service, path, json=None):
        attempts.append((service, path))
        return 200          # pretend success so the pass proceeds

    def fake_wait(*a, **k):
        return True

    orig_delete, orig_wait = r._delete, r._wait_gone
    r._delete, r._wait_gone = fake_delete, fake_wait
    try:
        r.run_sweep(client or core.ApiClient(core.settings))
    finally:
        r._delete, r._wait_gone = orig_delete, orig_wait
    return [{"service": svc, "path": path} for svc, path in attempts]


def _main() -> None:
    from collections import Counter
    owned = scan_owned()
    by_svc = Counter(o["service"] for o in owned)
    print("=== per-service: # owned resources the sweep WOULD still delete (survivors) ===")
    if not owned:
        print("  NONE — every swept collection is empty of owned resources ✅")
    for svc, n in by_svc.most_common():
        paths = Counter(o["path"] for o in owned if o["service"] == svc)
        print(f"  {svc:18} {n:3}  ({dict(paths)})")
    print(f"\nTOTAL owned survivors across all collections: {len(owned)}")


if __name__ == "__main__":
    _main()
