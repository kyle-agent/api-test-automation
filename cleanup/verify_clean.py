"""Read-only cleanup verification: reuse the reconciler's EXACT collection list +
dependency order, but stub out _delete/_wait_gone/sleep so nothing is deleted —
just report, per (service, collection), how many OWNED (regr*/owner-tag) resources
still survive. A non-zero survivor means cleanup didn't complete for that service
(often because a dependency is still blocking it)."""
import os, time
os.environ.setdefault("SCP_SWEEP_IGNORE_TTL", "true")  # count unexpired too (full inventory)
# Fast-fail listing: this is a read-only inventory, so don't sink 60s x retries on
# a slow/unreachable service host — a short deadline keeps the full sweep tractable.
os.environ.setdefault("SCP_TIMEOUT", "8")
os.environ.setdefault("SCP_MAX_RETRIES", "1")
import time as _t
_t.sleep = lambda *a, **k: None                        # no internal waits/backoff
import cleanup.reconciler as r
import core

survivors = []          # (service, path, name)
attempts = []           # (service, path) every delete the sweep WOULD issue

def fake_delete(client, service, path, json=None):
    attempts.append((service, path))
    return 200          # pretend success so the pass proceeds
def fake_wait(*a, **k):
    return True
r._delete = fake_delete
r._wait_gone = fake_wait

client = core.ApiClient(core.settings)
r.run_sweep(client)

from collections import Counter
by_svc = Counter(s for s, _ in attempts)
print("=== per-service: # owned resources the sweep WOULD still delete (survivors) ===")
if not attempts:
    print("  NONE — every swept collection is empty of owned resources ✅")
for svc, n in by_svc.most_common():
    paths = Counter(p for s, p in attempts if s == svc)
    print(f"  {svc:18} {n:3}  ({dict(paths)})")
print(f"\nTOTAL owned survivors across all collections: {len(attempts)}")
