"""schedule_optimizer — the self-learning, cap-constrained critical-path scheduler.

Ties three inputs into one algorithm that decides the test execution order:

  1. **dependency graph G** — nodes + requires edges (from catalog_planner).
  2. **measured durations d(n)** — each run logs how long every node took; this
     module keeps a rolling AVERAGE per node in data/optimizer/durations.json, so
     the more we run, the better the estimates.
  3. **the VPC cap** — a renewable resource constraint (≤5 concurrent VPCs): a
     self-creating node holds one slot for its duration.

From these it derives:

  * **critical path** — the longest duration-weighted chain through G; its length
    is the wall-time FLOOR (nothing can finish sooner).
  * **priority (tail-length)** — for each node, the longest remaining duration on
    any path from it; scheduling longest-tail-first (critical-path / LPT order)
    minimises makespan.
  * **a cap-feasible schedule + estimated makespan** — greedy list scheduling that
    dispatches the highest-priority ready node whenever a VPC slot is free.

It is SELF-UPDATING: add a new service (node) and the next plan re-derives the
critical path, priorities and makespan automatically — an unseen node just uses a
default duration until its first measurement lands.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

_DUR_PATH = Path(__file__).resolve().parents[2] / "data" / "optimizer" / "durations.json"
_DEPS_PATH = Path(__file__).resolve().parent / "dependencies.json"
_DEFAULT_S = 30.0   # assumed duration of a node we have never measured
# 핀 노드의 priority 가산치 — 어떤 실측 duration(최장 ~46분≈2760s)보다 커서
# 핀 노드가 항상 먼저 디스패치되되, 핀들 사이에서는 여전히 긴 것부터 나간다.
PIN_BOOST_S = 1_000_000.0


def load_priority_first(path: Path | None = None) -> list[str]:
    """dependencies.json vpc_schedule.priority_first — 무조건 최우선 투입할
    lifecycle id 목록 (오너 지시 2026-07-13: 짧은 VPC-슬롯 소비자가 LPT에서
    뒤로 밀려 슬롯 대기 + 런 꼬리가 되는 것을 방지; t=0에는 슬롯이 비어 있다).
    실행(conftest)·예측(simulate_schedule)·dag(priorities) 세 경로가 모두 이
    함수로 같은 목록을 읽는다. 파일이 없거나 키가 없으면 빈 목록."""
    p = path or _DEPS_PATH
    try:
        return list(json.loads(p.read_text()).get("vpc_schedule", {})
                    .get("priority_first", []))
    except Exception:  # noqa: BLE001 — ordering is best-effort; never break a run
        return []


# --------------------------------------------------------------------------- #
# 1. duration store (rolling average per node, learned across runs)
# --------------------------------------------------------------------------- #
def load_durations(path: Path | None = None) -> dict:
    p = path or _DUR_PATH
    if p.exists():
        return json.loads(p.read_text())
    return {}


def update_durations(measured: dict, path: Path | None = None) -> dict:
    """Fold a run's measured ``{node: seconds}`` into the rolling-average store."""
    p = path or _DUR_PATH
    store = load_durations(p)
    for node, sec in measured.items():
        if sec is None:
            continue
        e = store.get(node, {"avg_s": 0.0, "n": 0, "last_s": 0.0})
        n = e["n"] + 1
        e["avg_s"] = (e["avg_s"] * e["n"] + float(sec)) / n
        e["n"] = n
        e["last_s"] = float(sec)
        store[node] = e
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(sorted(store.items())), indent=2))
    return store


def duration_of(node: str, durations: dict, default: float = _DEFAULT_S) -> float:
    e = durations.get(node)
    return float(e["avg_s"]) if e and e.get("avg_s") else default


def measured_from_result(run_result) -> dict:
    """Extract ``{lifecycle_id: wall_seconds}`` from a dag_runner RunResult — the
    accurate per-lifecycle wall-time the executor measured. Fold this into the
    store after each live run so the schedule learns real durations."""
    out = {}
    for o in getattr(run_result, "outcomes", []):
        if getattr(o, "duration_s", 0):
            out[o.lifecycle_id] = o.duration_s
    return out


# --------------------------------------------------------------------------- #
# 2. critical path (longest duration-weighted path through the dependency DAG)
# --------------------------------------------------------------------------- #
def _topo_order(nodes: set, requires) -> list:
    indeg = {n: sum(1 for r in requires(n) if r in nodes) for n in nodes}
    children = defaultdict(list)
    for n in nodes:
        for r in requires(n):
            if r in nodes:
                children[r].append(n)
    dq = deque(n for n in nodes if indeg[n] == 0)
    order = []
    while dq:
        n = dq.popleft()
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                dq.append(c)
    if len(order) != len(nodes):
        raise ValueError("cycle in dependency graph")
    return order


def tail_lengths(nodes: set, requires, durations: dict, default: float = _DEFAULT_S) -> dict:
    """tail[n] = longest remaining duration on any path STARTING at n (incl. n).
    This is the critical-path priority: schedule largest tail first."""
    order = _topo_order(nodes, requires)
    children = defaultdict(list)
    for n in nodes:
        for r in requires(n):
            if r in nodes:
                children[r].append(n)
    tail = {}
    for n in reversed(order):
        d = duration_of(n, durations, default)
        tail[n] = d + max((tail[c] for c in children[n]), default=0.0)
    return tail


def critical_path(nodes: set, requires, durations: dict, default: float = _DEFAULT_S):
    """Return (path, total_seconds): the longest duration-weighted chain."""
    tail = tail_lengths(nodes, requires, durations, default)
    if not tail:
        return [], 0.0
    cur = max(tail, key=lambda n: tail[n])
    path = [cur]
    total = tail[cur]
    while True:
        kids = [c for c in nodes if c != cur and cur in requires(c)]  # nodes requiring cur
        nxt = [c for c in kids if c in tail]
        if not nxt:
            break
        cur = max(nxt, key=lambda n: tail[n])
        path.append(cur)
    return path, total


# --------------------------------------------------------------------------- #
# 3. cap-feasible schedule + estimated makespan (greedy critical-path list-sched)
# --------------------------------------------------------------------------- #
@dataclass
class Schedule:
    order: list = field(default_factory=list)          # dispatch order (priority)
    makespan_s: float = 0.0                             # estimated wall-time
    critical_path: list = field(default_factory=list)  # the floor-determining chain
    floor_s: float = 0.0                               # critical-path length
    slot_consumers: list = field(default_factory=list) # nodes that hold a VPC slot

    def to_dict(self) -> dict:
        return {"order": self.order, "makespan_s": round(self.makespan_s, 1),
                "floor_s": round(self.floor_s, 1), "critical_path": self.critical_path,
                "slot_consumers": self.slot_consumers}


def schedule(nodes, requires, durations, *, vpc_cap: int = 5, shared_vpc: int = 1,
             holds_slot=None, default: float = _DEFAULT_S) -> Schedule:
    """Greedy critical-path list-scheduling under the VPC-slot constraint.

    ``holds_slot(n)`` -> True if node n occupies a VPC slot while running (a
    self-creator). At most ``vpc_cap - shared_vpc`` slot-holders run at once;
    everything else is bounded only by readiness. Ready node with the largest
    tail-length is dispatched first. Returns the order + estimated makespan +
    the critical path (wall-time floor)."""
    nodes = set(nodes)
    holds_slot = holds_slot or (lambda n: False)
    tail = tail_lengths(nodes, requires, durations, default)
    cp, floor = critical_path(nodes, requires, durations, default)
    slot_budget = max(1, vpc_cap - shared_vpc)

    indeg = {n: sum(1 for r in requires(n) if r in nodes) for n in nodes}
    children = defaultdict(list)
    for n in nodes:
        for r in requires(n):
            if r in nodes:
                children[r].append(n)

    import heapq
    ready = [(-tail[n], n) for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    running = []           # (finish_time, node)
    t = 0.0
    slots_used = 0
    order = []
    while ready or running:
        # dispatch every ready node we can (respecting slot budget)
        deferred = []
        while ready:
            negtail, n = heapq.heappop(ready)
            if holds_slot(n) and slots_used >= slot_budget:
                deferred.append((negtail, n))
                continue
            if holds_slot(n):
                slots_used += 1
            fin = t + duration_of(n, durations, default)
            heapq.heappush(running, (fin, n))
            order.append(n)
        for item in deferred:
            heapq.heappush(ready, item)
        if not running:
            break
        # advance time to the next finishing node
        fin, n = heapq.heappop(running)
        t = fin
        if holds_slot(n):
            slots_used -= 1
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                heapq.heappush(ready, (-tail[c], c))
    return Schedule(order=order, makespan_s=t, critical_path=cp, floor_s=floor,
                    slot_consumers=sorted(n for n in nodes if holds_slot(n)))
