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


def _snapshot_campaign_state() -> tuple:
    """Copy the reconciler's per-campaign module state (convergence caches,
    stuck/issued sets, round counters) so a scan can run on a CLEAN slate and
    put everything back afterwards."""
    return (set(r._CONVERGED), set(r._DELETED_THIS_SWEEP),
            set(r._DELETE_ISSUED), dict(r._STUCK),
            r._PROGRESS_THIS_ROUND[0], r._INPROGRESS_THIS_ROUND[0])


def _restore_campaign_state(snap: tuple) -> None:
    conv, deleted, issued, stuck, prog, inprog = snap
    r._CONVERGED.clear();          r._CONVERGED.update(conv)            # noqa: E702
    r._DELETED_THIS_SWEEP.clear(); r._DELETED_THIS_SWEEP.update(deleted)
    r._DELETE_ISSUED.clear();      r._DELETE_ISSUED.update(issued)      # noqa: E702
    r._STUCK.clear();              r._STUCK.update(stuck)               # noqa: E702
    r._PROGRESS_THIS_ROUND[0] = prog
    r._INPROGRESS_THIS_ROUND[0] = inprog


def scan_owned(client=None, list_errors=None) -> list[dict]:
    """Run the reconciler sweep in a deleteless DRY mode and return the structured
    inventory of OWNED resources it WOULD delete: ``[{"service": ..., "path": ...},
    ...]`` (one entry per delete the sweep would issue). Read-only — _delete /
    _wait_gone are stubbed so nothing is touched, only LIST calls are made.

    ``client`` defaults to a fresh ``core.ApiClient(core.settings)``. Patching the
    module-level ``r._delete`` / ``r._wait_gone`` is local to this call and restored
    afterwards, so importing this module has no lingering effect on the reconciler.

    CAMPAIGN-STATE ISOLATION (root cause of the 2026-07-04 '재스캔 0건 오보'):
    ``run_sweep`` consults the module-level per-campaign caches — above all
    ``r._CONVERGED``, which makes ``_select`` SKIP re-listing any (service, path)
    a *previous* pass found empty. Only ``r.main()`` resets those caches, and
    scan_owned calls ``run_sweep`` directly — so in a long-lived server process a
    pre-run owned scan of a clean account marked EVERY collection converged, and
    the post-run rescans then "scanned" nothing and reported total 0 while real
    leftovers survived (a fresh-process CLI call, with empty caches, saw them
    immediately). Each scan now runs on a snapshot-clean campaign state and
    restores the previous state afterwards.

    ``list_errors`` (optional, list) collects ``{"service","path","error"}`` for
    every collection LIST that failed during the scan, so callers can tell a
    genuine "0 owned" from "the LISTs failed" (never report the latter as 0건).
    """
    attempts: list[tuple[str, str, object]] = []  # (service, path, json body) per WOULD-delete

    def fake_delete(client, service, path, json=None):
        attempts.append((service, path, json))
        return 200          # pretend success so the pass proceeds

    def fake_wait(*a, **k):
        return True

    def counting_list_all(cl, service, path):
        # mirror r._list_all but RECORD failures instead of silently returning []
        try:
            resp = cl.get(path, service=service)
        except Exception as exc:  # noqa: BLE001
            print(f"  list {path} error: {exc}")
            if list_errors is not None:
                list_errors.append({"service": service, "path": path,
                                    "error": str(exc)})
            return []
        if not resp.ok:
            print(f"  list {path} -> {resp.status}")
            if list_errors is not None:
                list_errors.append({"service": service, "path": path,
                                    "error": f"HTTP {resp.status}"})
            return []
        return [it for it in r._items(resp.body) if isinstance(it, dict)]

    with _SCAN_LOCK:
        orig_delete, orig_wait, orig_sleep = r._delete, r._wait_gone, _t.sleep
        orig_list_all = r._list_all
        state = _snapshot_campaign_state()
        r._reset_campaign_state()        # clean slate — never inherit convergence
        r._delete, r._wait_gone = fake_delete, fake_wait
        r._list_all = counting_list_all
        _t.sleep = lambda *a, **k: None  # no internal waits/backoff DURING the scan only
        try:
            r.run_sweep(client or core.ApiClient(core.settings))
        finally:
            r._delete, r._wait_gone, _t.sleep = orig_delete, orig_wait, orig_sleep
            r._list_all = orig_list_all
            _restore_campaign_state(state)   # scan leaves ZERO footprint
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
