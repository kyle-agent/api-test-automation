"""Resource registry — tag-based ownership for deterministic cleanup.

The cross-run leak problems came from cleaning up by *guessing* name prefixes.
Instead, every resource a run creates is stamped with an **owner tag**
``(owner=apitest, run=<run_id>, axis=<axis>, ttl=<iso8601>)`` and recorded in a
per-run manifest. Cleanup then deletes by tag:

  * a run tears down **its own** manifest in reverse order (immediate), and
  * the reconciler (see ``cleanup/``) lists account resources and removes any
    carrying our owner tag whose run is finished or whose ttl has passed —
    never touching another run's live resources.

Resources whose API has no tag support fall back to a run-stamped **name**
(``{prefix}{run_id}{rand}``) which ``is_owned`` also recognises.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

OWNER = "apitest"            # owner-tag value identifying resources we created
OWNER_KEY = "owner"
RUN_KEY = "run"
AXIS_KEY = "axis"
TTL_KEY = "ttl"


def run_id() -> str:
    """Stable per-process run id (CI run id if present, else a timestamp)."""
    return os.environ.get("APITEST_RUN_ID") or os.environ.get(
        "GITHUB_RUN_ID") or time.strftime("%Y%m%d%H%M%S")


def name_stamp(prefix: str) -> str:
    """A collision-resistant, owner-encoded resource name: ``{prefix}{run}{rand}``.
    The run id makes a leaked resource attributable; the reconciler matches on it."""
    import random
    import string
    rid = run_id()[-8:]
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{prefix}{rid}{rand}"


def owner_tags(axis: str, ttl_hours: int = 6) -> list[dict]:
    """Tag payload to merge into a create body's ``tags`` so the resource is
    attributable and time-bounded. Shape follows SCP's ``[{key,value}]``."""
    ttl = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    return [
        {"key": OWNER_KEY, "value": OWNER},
        {"key": RUN_KEY, "value": run_id()},
        {"key": AXIS_KEY, "value": axis},
        {"key": TTL_KEY, "value": ttl},
    ]


def _tag_value(item: dict, key: str) -> str | None:
    for t in (item.get("tags") or []):
        if isinstance(t, dict) and t.get("key") == key:
            return t.get("value")
    return None


def is_owned(item: dict, *, name_prefixes: tuple[str, ...] = ()) -> bool:
    """True if a listed cloud resource is ours — by owner tag (preferred) or, as a
    fallback for tag-less resources, by a known run-stamped name prefix."""
    if _tag_value(item, OWNER_KEY) == OWNER:
        return True
    name = str(item.get("name", ""))
    return bool(name_prefixes) and name.startswith(name_prefixes)


def is_expired(item: dict, *, grace_minutes: int = 0) -> bool:
    """True if the owner ttl tag has passed (used by the reconciler to reap
    orphans from crashed runs). Untagged/ttl-less items are not auto-expired."""
    ttl = _tag_value(item, TTL_KEY)
    if not ttl:
        return False
    try:
        deadline = datetime.fromisoformat(ttl) + timedelta(minutes=grace_minutes)
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= deadline


@dataclass
class ResourceRecord:
    service: str
    delete_path: str          # e.g. /v1/vpcs/{id} already filled
    resource_id: str
    kind: str = ""            # e.g. "vpc", "subnet"
    parent: str | None = None  # delete-ordering hint (delete children first)
    created_at: float = field(default_factory=time.time)


class ResourceRegistry:
    """Per-run manifest of created resources for ordered, immediate teardown.

    Append every create via :meth:`track`; :meth:`teardown_order` yields records
    newest-first (children before parents) for the run's own cleanup. The manifest
    is also persisted (JSONL) so a separate reconciler step can finish teardown if
    the run process dies mid-way.
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.records: list[ResourceRecord] = []
        self.path = Path(path or f"reports/registry/{run_id()}.jsonl")

    def track(self, rec: ResourceRecord) -> ResourceRecord:
        self.records.append(rec)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(asdict(rec)) + "\n")
        except OSError:
            pass
        return rec

    def teardown_order(self) -> list[ResourceRecord]:
        """Newest-first: created last == deepest child == deleted first."""
        return list(reversed(self.records))
