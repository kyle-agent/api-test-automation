"""Account budgets — make resource quotas explicit so scenarios schedule safely.

The 3-VPC cap repeatedly skipped networking scenarios and, combined with leaks,
caused flaky coverage. Modelling limits as data lets the scenario scheduler:

  * reserve a slot before a create and release it after teardown,
  * serialize scenarios that would exceed a limit (instead of failing), and
  * leave head-room shared between axis-1 scenarios and axis-2 active probes.

This is intentionally a small in-process accounting helper; the authoritative
limits live in data and the live usage is reconciled from the account at run
start (a scheduler can call :meth:`sync` with counts from a list call).
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Conservative defaults; override from data/baselines or env as they are learned.
DEFAULT_LIMITS = {
    "vpc": 5,            # scp-network.vpc.exceed-max-count — account cap is 5, VALIDATED by the live error "The number(5) of VPCs ... exceeded" (run 27306490231)
    "private-dns": 3,    # scp-network.private-dns.max-count-exceed
}


def _env_limits() -> dict:
    """SCP_BUDGET_LIMITS (JSON {"kind": int}) — per-environment quota overrides
    exported by core.profiles from a profile's `quota_overrides:`; merged over
    the validated defaults so other accounts/environments need no code change."""
    raw = os.environ.get("SCP_BUDGET_LIMITS", "").strip()
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return {str(k): int(v) for k, v in val.items()}
    except (ValueError, TypeError):
        pass
    return {}


@dataclass
class Budget:
    limits: dict = field(default_factory=lambda: {**DEFAULT_LIMITS, **_env_limits()})
    used: dict = field(default_factory=dict)

    def sync(self, kind: str, live_count: int) -> None:
        """Set the currently-observed usage for a kind (from a real list call)."""
        self.used[kind] = live_count

    def available(self, kind: str) -> int:
        limit = self.limits.get(kind)
        if limit is None:
            return 1_000_000  # untracked kinds are effectively unlimited
        return max(0, limit - self.used.get(kind, 0))

    def can_create(self, kind: str, n: int = 1) -> bool:
        return self.available(kind) >= n

    def reserve(self, kind: str, n: int = 1) -> bool:
        if not self.can_create(kind, n):
            return False
        self.used[kind] = self.used.get(kind, 0) + n
        return True

    def release(self, kind: str, n: int = 1) -> None:
        self.used[kind] = max(0, self.used.get(kind, 0) - n)


# ---------------------------------------------------------------------------
# Cross-process quota semaphore (scheduler v0.5)
# ---------------------------------------------------------------------------
# The :class:`Budget` above is per-process accounting: each pytest-xdist worker
# owns its own instance, so it CANNOT coordinate a shared account cap (e.g. the
# 5-VPC limit) across workers. That is why VPC-self-creating lifecycles run in a
# SEPARATE serial job today — parallel self-creates would race past the cap.
#
# This semaphore is the v0.5 enabling primitive from
# docs/decisions/2026-06-19-dependency-dag-test-scheduler.md: a counting
# semaphore whose state lives in a run-scoped file guarded by an OS advisory
# lock (`fcntl.flock`). Because xdist workers are processes on the SAME machine
# sharing one filesystem, they all see the same file → one shared count. A
# VPC-creating lifecycle then ACQUIRES a slot (blocking until one frees, unlike
# Budget which skips) and RELEASES it after teardown, letting the lane run
# 2-3-wide inside one parallel pool instead of as a serial job.
#
# It is deliberately standalone and OPT-IN: nothing calls it until the engine /
# workflow are cut over, so adding it cannot change the current run. Crashed
# holders are reclaimed via PID-liveness (valid for same-machine workers) so a
# worker that dies mid-lifecycle never deadlocks the pool.

def _sem_run_id() -> str:
    """Same run-id resolution as core.oplog/core.commands so every channel of a
    run agrees on one identifier (xdist workers inherit these env vars)."""
    return (os.getenv("APITEST_RUN_ID")
            or os.getenv("GITHUB_RUN_ID")
            or "local")


def _sem_dir() -> Path:
    """Run-scoped directory for semaphore state. Under reports/ (gitignored);
    overridable via SCP_BUDGET_SEM_DIR for tests / non-default layouts."""
    base = os.getenv("SCP_BUDGET_SEM_DIR", "").strip()
    root = Path(base) if base else Path("reports") / ".locks"
    return root / _sem_run_id()


def _alive(pid: int) -> bool:
    """True if a holder process is still running (same-machine assumption).
    signal 0 probes existence without delivering a signal; EPERM means the pid
    exists but is owned by another user (still alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _flock(lock_path: Path):
    """Exclusive advisory lock over the read-modify-write of one kind's state.
    POSIX (fcntl) — the CI runner + dev containers are Linux. Best-effort no-op
    if fcntl is unavailable (e.g. Windows dev box): the semaphore degrades to
    per-process, which is exactly today's behaviour, so it never hard-breaks."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        os.close(fd)


class CrossProcessSemaphore:
    """File-backed counting semaphore shared across processes of one run.

    Each :meth:`acquire` records a *holder* ``{token, pid, n, ts}`` in a JSON
    state file; the sum of live holders' ``n`` is the current usage. A holder
    whose pid is no longer alive is reclaimed on the next access, so a crashed
    worker cannot wedge the pool. ``limit`` is supplied per-acquire (the caller
    knows ``cap - shared`` at call time), so one semaphore kind can be sized
    differently as shared infrastructure is provisioned.
    """

    def __init__(self, kind: str, *, dir: Path | None = None):
        self.kind = kind
        self._dir = dir or _sem_dir()
        self._state = self._dir / f"sem-{kind}.json"
        self._lock = self._dir / f"sem-{kind}.lock"

    # -- state file helpers (always called under _flock) --------------------
    def _read(self) -> list[dict]:
        try:
            holders = json.loads(self._state.read_text()).get("holders", [])
        except (FileNotFoundError, ValueError):
            return []
        # Reclaim dead holders (crashed worker / finished process).
        return [h for h in holders if _alive(int(h.get("pid", 0)))]

    def _write(self, holders: list[dict]) -> None:
        self._state.write_text(json.dumps({"holders": holders}))

    def used(self) -> int:
        """Live reserved count (prunes dead holders as a side effect)."""
        with _flock(self._lock):
            holders = self._read()
            self._write(holders)
            return sum(int(h.get("n", 1)) for h in holders)

    def try_acquire(self, limit: int, n: int = 1) -> str | None:
        """Reserve ``n`` slots if ``used + n <= limit``; return a release token
        or ``None`` if the cap is full. Non-blocking."""
        with _flock(self._lock):
            holders = self._read()
            used = sum(int(h.get("n", 1)) for h in holders)
            if used + n > limit:
                self._write(holders)   # persist the dead-holder pruning
                return None
            token = uuid.uuid4().hex
            holders.append({"token": token, "pid": os.getpid(),
                            "n": int(n), "ts": time.time()})
            self._write(holders)
            return token

    def acquire(self, limit: int, n: int = 1, *, timeout: float = 1800.0,
                poll: float = 1.0) -> str | None:
        """Block until ``n`` slots are free (or ``timeout`` s elapse). Returns a
        release token, or ``None`` on timeout. Unlike :meth:`Budget.reserve`
        (which skips when exhausted) this WAITS — the throttle that lets the
        VPC-CRUD lane run inside one parallel pool instead of a serial job."""
        deadline = time.monotonic() + timeout
        while True:
            token = self.try_acquire(limit, n)
            if token is not None:
                return token
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll)

    def release(self, token: str) -> None:
        """Free the slots held under ``token``. Idempotent — releasing an
        unknown/already-released token is a no-op."""
        with _flock(self._lock):
            holders = [h for h in self._read() if h.get("token") != token]
            self._write(holders)

    @contextlib.contextmanager
    def slot(self, limit: int, n: int = 1, *, timeout: float = 1800.0,
             poll: float = 1.0):
        """Context manager: acquire on enter, release on exit. Yields the token
        (``None`` if the acquire timed out — caller decides skip vs. fail)."""
        token = self.acquire(limit, n, timeout=timeout, poll=poll)
        try:
            yield token
        finally:
            if token is not None:
                self.release(token)


# ---------------------------------------------------------------------------
# Live usage probe + status CLI
# ---------------------------------------------------------------------------
# A capped create needs the LIVE account usage (not just in-process reservations)
# to know real head-room. A per-service coverage agent calls this BEFORE a
# VPC-consuming create so concurrent agents don't blow the account cap — pair it
# with CrossProcessSemaphore("vpc") to coordinate reservations across processes.

# kind -> (service short-name, read-only LIST path). Only kinds with a known
# list endpoint are live-tracked; others report live=None (unknown, not 0).
_LIVE_LIST = {
    "vpc": ("vpc", "/v1/vpcs"),
}


def live_count(kind: str) -> int | None:
    """Current LIVE account usage for a capped kind via a read-only LIST.
    Returns None when the kind has no known list endpoint or the call fails
    (so callers treat 'unknown' differently from a confirmed 0)."""
    spec = _LIVE_LIST.get(kind)
    if not spec:
        return None
    service, path = spec
    try:
        from core.config import Settings
        from core.http_client import ApiClient
        r = ApiClient(Settings()).get(path, params={"size": 1}, service=service,
                                      timeout=15, retry=False)
        if not getattr(r, "ok", False):
            return None
        b = r.body if isinstance(r.body, dict) else {}
        n = b.get("totalCount")
        if n is None:
            items = b.get("contents") or b.get(path.rsplit("/", 1)[-1]) or []
            n = len(items) if isinstance(items, list) else 0
        return int(n)
    except Exception:  # noqa: BLE001 — best-effort head-room probe, never raise
        return None


def status() -> dict:
    """Per-capped-kind {limit, live, free}. ``free`` is None when the live count
    is unavailable, so a scheduler never over-creates on an unknown."""
    b = Budget()
    out = {}
    for kind, limit in b.limits.items():
        live = live_count(kind)
        out[kind] = {"limit": int(limit), "live": live,
                     "free": (max(0, int(limit) - live) if isinstance(live, int) else None)}
    return out


def _main(argv=None) -> int:
    rows = status()
    print(f"{'kind':<14}{'limit':>6}{'live':>6}{'free':>6}")
    for kind, v in rows.items():
        live = "?" if v["live"] is None else v["live"]
        free = "?" if v["free"] is None else v["free"]
        print(f"{kind:<14}{v['limit']:>6}{str(live):>6}{str(free):>6}")
    print("\njson: " + json.dumps(rows))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
