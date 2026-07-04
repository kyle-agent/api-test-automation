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
import threading
os.environ.setdefault("SCP_SWEEP_IGNORE_TTL", "true")  # count unexpired too (full inventory)
# Fast-fail listing: this is a read-only inventory, so don't sink 60s x retries on
# a slow/unreachable service host — a short deadline keeps the full sweep tractable.
os.environ.setdefault("SCP_TIMEOUT", "8")
os.environ.setdefault("SCP_MAX_RETRIES", "1")
import time as _t
import cleanup.reconciler as r  # noqa: E402 — must follow the env setup above
import core  # noqa: E402 — must follow the env setup above

# scan_owned monkeypatches MODULE GLOBALS (r._delete/_wait_gone + time.sleep);
# concurrent scans would corrupt each other's stubs and could leave time.sleep
# permanently no-op'd (post-run auto-rescans made overlap routine — H1 2026-07-04).
_SCAN_LOCK = threading.Lock()


def scan_owned(client=None) -> list[dict]:
    """Run the reconciler sweep in a deleteless DRY mode and return the structured
    inventory of OWNED resources it WOULD delete: ``[{"service": ..., "path": ...},
    ...]`` (one entry per delete the sweep would issue). Read-only — _delete /
    _wait_gone are stubbed so nothing is touched, only LIST calls are made.

    ``client`` defaults to a fresh ``core.ApiClient(core.settings)``. Patching the
    module-level ``r._delete`` / ``r._wait_gone`` is local to this call and restored
    afterwards, so importing this module has no lingering effect on the reconciler.
    """
    attempts: list[tuple[str, str, object]] = []  # (service, path, json body) per WOULD-delete

    def fake_delete(client, service, path, json=None):
        attempts.append((service, path, json))
        return 200          # pretend success so the pass proceeds

    def fake_wait(*a, **k):
        return True

    with _SCAN_LOCK:
        orig_delete, orig_wait, orig_sleep = r._delete, r._wait_gone, _t.sleep
        r._delete, r._wait_gone = fake_delete, fake_wait
        _t.sleep = lambda *a, **k: None  # no internal waits/backoff DURING the scan only
        try:
            r.run_sweep(client or core.ApiClient(core.settings))
        finally:
            r._delete, r._wait_gone, _t.sleep = orig_delete, orig_wait, orig_sleep
    # 'json' rides along when the sweep's delete carried a body (bulk ids /
    # secrets waiting_time) so consumers can expand bulk deletes to per-id rows;
    # existing consumers only read service/path and are unaffected.
    return [{"service": svc, "path": path, **({"json": body} if body else {})}
            for svc, path, body in attempts]


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
