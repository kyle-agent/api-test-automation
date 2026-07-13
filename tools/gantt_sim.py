"""Native-scheduler full-run Gantt simulation (읽기 전용).

native_runner.priority_order(=dependent-last, LPT-desc) + 30 워커 동적 pop +
공유 Budget 쿼터 admission을, 실 durations로 이산사건 시뮬레이션해 **태스크별
(워커, start, end)** 스케줄을 뽑는다. scheduler_sim이 makespan/병렬성 요약만
내던 것을, 여기선 간트차트용 per-task 타임라인으로 확장.

쿼터 모델(adopt-정확): 공유 VPC 1개를 미리 provision하고 adopt_lifecycles는 그걸
재사용(= vpc create 슬롯 미소비)하므로, vpc 캡(per_run_vpc_cap)에는 self-create
vpc_crud_lifecycles만, private-dns 캡(3)에는 두 소비자만 admission을 건다. 나머지
186개는 쿼터 무관 → 30 워커 순수 LPT.

출력: JSON(stdout) — {makespan_s, workers, tasks:[{id,worker,start,end,dur,dep,
quota,wait}], util:[(t,active)]}. `python -m tools.gantt_sim`.
"""
from __future__ import annotations

import heapq
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _durations() -> dict:
    out: dict = {}
    for p in (_ROOT / "data/optimizer/durations.json",
              _ROOT / "data/optimizer/durations.local.json"):
        try:
            for k, v in json.loads(p.read_text()).items():
                out[k] = max(out.get(k, 0.0), float(v.get("avg_s") or 0.0))
        except Exception:  # noqa: BLE001
            continue
    return out


def _class_default(lc) -> float:
    try:
        from tools.duration_stats import CLASS_DEFAULT_S, classify_lifecycle
        return float(CLASS_DEFAULT_S[classify_lifecycle(lc)])
    except Exception:  # noqa: BLE001
        return 60.0


def load():
    from regression.scenarios import engine
    dur = _durations()
    dep = json.loads((_ROOT / "regression/scenarios/dependencies.json").read_text())
    prereq = set(dep.get("prerequisites", {}))
    qk = dep.get("quota_kinds", {})
    vs = dep.get("vpc_schedule", {})
    caps = {"vpc": vs.get("per_run_vpc_cap", 4), "private-dns": vs.get("private_dns_limit", 3)}
    adopt = set(vs.get("adopt_lifecycles", []))

    lcs = engine.active_lifecycles()
    tasks = []
    for lc in lcs:
        lid = lc["id"]
        d = dur.get(lid, 0.0) or _class_default(lc)
        # adopt-정확 쿼터: adopter는 공유 VPC 재사용 → vpc 슬롯 미소비.
        # self-create(=non-adopt)만 vpc 캡을 소비. private-dns는 소비자 그대로.
        kinds = []
        for k in qk.get(lid, []):
            if k == "vpc" and lid in adopt:
                continue          # 공유 VPC 재사용 — 새 슬롯 안 씀
            if k in caps:
                kinds.append(k)
        tasks.append({"id": lid, "dur": float(d), "dep": lid in prereq,
                      "quota": kinds, "cat": lid.split("-")[0]})
    return tasks, caps


def simulate(tasks, caps, workers=30):
    """native_runner 정확: priority_order = (dep asc, dur desc). 30 워커가 ready에서
    동적 pop; capped kind는 슬롯 있어야 admit(없으면 대기=admission-wait)."""
    def prio(t):
        return (t["dep"], -t["dur"], t["id"])
    pending = sorted(tasks, key=prio)
    used = {k: 0 for k in caps}
    free_workers = list(range(workers))
    running = []            # heap (end, wid, task, start)
    t = 0.0
    sched = []              # {id,worker,start,end,dur,dep,quota,wait}
    start_wait = {}         # id -> first time it was ready-but-blocked
    util = []
    N = len(tasks)
    done = 0
    while done < N:
        # admit as many as workers+quota allow
        progressed = True
        while progressed and free_workers and pending:
            progressed = False
            for i, tk in enumerate(pending):
                if all(used[k] < caps[k] for k in tk["quota"]):
                    for k in tk["quota"]:
                        used[k] += 1
                    wid = free_workers.pop(0)
                    st = t
                    heapq.heappush(running, (t + tk["dur"], wid, id(tk), tk, st))
                    pending.pop(i)
                    sched.append({"id": tk["id"], "worker": wid, "start": st,
                                  "end": st + tk["dur"], "dur": tk["dur"],
                                  "dep": tk["dep"], "quota": tk["quota"],
                                  "wait": round(st - start_wait.get(tk["id"], st), 1),
                                  "cat": tk["cat"]})
                    progressed = True
                    break
                else:
                    start_wait.setdefault(tk["id"], t)  # blocked on quota
        util.append((round(t, 1), workers - len(free_workers)))
        if not running:
            break
        et, wid, _, tk, _st = heapq.heappop(running)
        t = et
        free_workers.append(wid)
        free_workers.sort()
        done += 1
        for k in tk["quota"]:
            used[k] -= 1
        while running and running[0][0] == t:
            _, wid2, _, tk2, _ = heapq.heappop(running)
            free_workers.append(wid2); free_workers.sort()
            done += 1
            for k in tk2["quota"]:
                used[k] -= 1
    makespan = max((s["end"] for s in sched), default=0.0)
    return {"makespan_s": makespan, "workers": workers, "tasks": sched, "util": util}


def main():
    tasks, caps = load()
    W = 30
    out = simulate(tasks, caps, W)
    out["caps"] = caps
    out["n_tasks"] = len(tasks)
    out["serial_s"] = sum(t["dur"] for t in tasks)
    out["longest"] = sorted(({"id": t["id"], "dur": t["dur"]} for t in tasks),
                            key=lambda x: -x["dur"])[:8]
    waits = [s for s in out["tasks"] if s["wait"] > 1]
    out["quota_waits"] = waits
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
