"""Account budgets — make resource quotas explicit so scenarios schedule safely.

The 5-VPC cap repeatedly skipped networking scenarios and, combined with leaks,
caused flaky coverage. Modelling limits as data lets the scenario scheduler:

  * reserve a slot before a create and release it after teardown,
  * serialize scenarios that would exceed a limit (instead of failing), and
  * leave head-room shared between axis-1 scenarios and axis-2 active probes.

This is intentionally a small in-process accounting helper; the authoritative
limits live in data and the live usage is reconciled from the account at run
start (a scheduler can call :meth:`sync` with counts from a list call).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Conservative defaults; override from data/baselines or env as they are learned.
DEFAULT_LIMITS = {
    "vpc": 5,            # scp-network.vpc.exceed-max-count (the recurring one)
    "private-dns": 3,    # scp-network.private-dns.max-count-exceed
}


@dataclass
class Budget:
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
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
