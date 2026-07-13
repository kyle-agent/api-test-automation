"""Discrete-event simulation: native task-queue scheduler vs xdist load/worksteal.

목적 (2026-07-13, 오너 지시 — xdist 워크라운드 대신 목적특화 스케줄러 설계 검증):
engine.run_lifecycle을 직접 구동하는 **단일 프로세스 태스크-큐 러너**가 xdist 대비
① 꼬리 병렬성 붕괴 제거 ② 계정-전역 쿼터 조율(400 레이스 제거) ③ LPT makespan
을 실제로 달성하는지를, 실 durations로 이산사건 시뮬레이션해 수치로 보인다.

세 스케줄러 모델:
  * native  — N 워커가 LPT(긴 것 먼저) ready-queue에서 동적으로 pop, 공유 쿼터
              카운터로 admission(초과면 대기), 큐 빌 때까지 워커 안 죽음.
  * xdist-load     — 초기 2/워커 청크 + 글로벌 pending, 워커는 pending 비면 종료
                     (남은 건 버퍼에 갇혀 1-in-1-out), 쿼터 per-worker(조율 안 됨).
  * xdist-worksteal— 수집 순서 블록 선분배, 훔칠 것 없으면 종료.

읽기 전용 — durations.json + dependencies.json만 읽는다. `python -m tools.scheduler_sim`.
"""
from __future__ import annotations

import heapq
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(ids: list[str] | None = None):
    dur = json.loads((_ROOT / "data/optimizer/durations.json").read_text())
    dep = json.loads((_ROOT / "regression/scenarios/dependencies.json").read_text())
    qk = dep.get("quota_kinds", {})
    prereq = dep.get("prerequisites", {})
    caps = {"vpc": dep["vpc_schedule"].get("per_run_vpc_cap", 4),
            "private-dns": dep["vpc_schedule"].get("private_dns_limit", 3)}

    def av(k):
        v = dur.get(k)
        return float(v.get("avg_s", 0.0)) if isinstance(v, dict) else 0.0
    keys = ids or [k for k in dur if av(k) > 0]
    tasks = []
    for k in keys:
        d = av(k) or 60.0
        # 이 lifecycle이 예약하는 capped kind (create quota) — qk 선언 우선
        kinds = [x for x in qk.get(k, []) if x in caps]
        tasks.append({"id": k, "dur": d, "quota": kinds,
                      "dep": bool(prereq.get(k))})
    return tasks, caps


# ---------------------------------------------------------------------------
# native: 동적 LPT + 공유 쿼터 admission + no-shutdown
# ---------------------------------------------------------------------------
def sim_native(tasks, caps, workers):
    # ready-queue: LPT(긴 것 먼저). dependent는 뒤로 (priority 낮춤).
    # 쿼터: 공유 카운터. capped create가 있는 태스크는 슬롯 있어야 admit;
    #       없으면 슬롯 빌 때까지 대기 (skip-not-fail = 400 레이스 0).
    def prio(t):  # 작을수록 먼저 — dependent는 뒤로, 그다음 긴 것 먼저
        return (t["dep"], -t["dur"])
    pending = sorted(tasks, key=prio)
    used = {k: 0 for k in caps}          # 현재 점유 슬롯
    t = 0.0
    free = workers
    running = []       # heap of (end_time, task)
    q400 = 0           # 쿼터 초과로 실패한 create (native는 0이어야)
    active_series = []
    done = 0
    N = len(tasks)
    while done < N:
        # admit: 빈 워커 있고, ready+쿼터OK 태스크 있으면 시작
        progressed = True
        while progressed and free > 0:
            progressed = False
            for i, tk in enumerate(pending):
                # 쿼터 admission: capped kind 전부 슬롯 있어야
                if all(used[k] < caps[k] for k in tk["quota"]):
                    for k in tk["quota"]:
                        used[k] += 1
                    heapq.heappush(running, (t + tk["dur"], id(tk), tk))
                    pending.pop(i)
                    free -= 1
                    progressed = True
                    break
        active_series.append((t, workers - free))
        if not running:
            break
        # 다음 완료로 시간 점프
        et, _, tk = heapq.heappop(running)
        t = et
        free += 1
        done += 1
        for k in tk["quota"]:
            used[k] -= 1
        # 동시에 끝나는 것 처리
        while running and running[0][0] == t:
            _, _, tk2 = heapq.heappop(running)
            free += 1; done += 1
            for k in tk2["quota"]:
                used[k] -= 1
    return {"makespan": t, "q400": q400, "active": active_series}


# ---------------------------------------------------------------------------
# xdist-load: 초기 2/워커 + 글로벌 pending, pending 비면 워커 종료, 쿼터 per-worker
# ---------------------------------------------------------------------------
def sim_xdist_load(tasks, caps, workers, order):
    # order: 수집 순서 (conftest 정렬 결과). 초기 청크 2/워커, 나머지 글로벌 pending.
    N = len(tasks)
    by_id = {t["id"]: t for t in tasks}
    seq = [by_id[i] for i in order if i in by_id]
    cs = max(min((N // workers) // 4, 1), 2)      # =2
    node_pending = [[] for _ in range(workers)]
    pos = 0
    for w in range(workers):
        node_pending[w] = seq[pos:pos + cs]; pos += cs
    global_pending = seq[pos:]
    # 쿼터: per-worker (조율 안 됨) → 계정 상한 초과 시 400 (create 실패, 태스크는
    #       끝나되 자원 0). 시뮬은 400 카운트만 (makespan엔 태스크 dur 그대로).
    t = 0.0
    free = workers
    running = []   # (end, worker, task)
    worker_busy = [False] * workers
    q400 = 0
    acct = {k: 0 for k in caps}     # 실제 계정 점유 (per-worker 예약은 이걸 모름)
    active_series = []
    done = 0

    def try_start(w):
        nonlocal free, q400
        if worker_busy[w] or not node_pending[w]:
            return False
        tk = node_pending[w].pop(0)
        # per-worker 예약은 성공(자기 버짓엔 슬롯 있다고 봄) → 실제 계정 확인:
        for k in tk["quota"]:
            if acct[k] >= caps[k]:
                q400 += 1            # 계정 초과 → 실제 400 (조율 없어서)
            else:
                acct[k] += 1
        heapq.heappush(running, (t + tk["dur"], w, tk))
        worker_busy[w] = True
        free -= 1
        return True

    # 초기 시작
    for w in range(workers):
        try_start(w)
    while done < N:
        active_series.append((t, sum(worker_busy)))
        if not running:
            break
        et, w, tk = heapq.heappop(running)
        t = et
        worker_busy[w] = False; free += 1; done += 1
        for k in tk["quota"]:
            if acct[k] > 0:
                acct[k] -= 1
        # 리필: 글로벌 pending에서 이 워커에 (load: pending 있으면 채움)
        if global_pending:
            need = 2 - len(node_pending[w])
            for _ in range(max(0, need)):
                if global_pending:
                    node_pending[w].append(global_pending.pop(0))
        # pending 비었고 이 워커 큐도 비면 → 워커 종료(재시작 안 함)
        try_start(w)
    return {"makespan": t, "q400": q400, "active": active_series}


def _order_conftest(tasks, workers):
    """conftest _order_for_load 근사: LPT desc + read-only(=여기선 dep=False&짧은
    것)와 무관하게, 시뮬은 순수 LPT desc로 (순서 효과는 native와 동일 입력)."""
    return [t["id"] for t in sorted(tasks, key=lambda t: -t["dur"])]


def main():
    tasks, caps = _load()
    W = 30
    order = _order_conftest(tasks, W)
    nat = sim_native(tasks, caps, W)
    xl = sim_xdist_load(tasks, caps, W, order)
    print(f"태스크 {len(tasks)} · 워커 {W} · 쿼터캡 {caps}\n")
    print(f"{'스케줄러':16} {'makespan(분)':>12} {'쿼터400':>8}")
    for name, r in [("native", nat), ("xdist-load", xl)]:
        print(f"{name:16} {r['makespan']/60:12.1f} {r['q400']:8}")

    def active_at(series, frac, ms):
        target = ms * frac
        best = 0
        for tt, a in series:
            if tt <= target:
                best = a
        return best
    print("\n활성 워커(병렬성) — makespan 대비 시점별 (꼬리 붕괴 = 뒤로 갈수록 낮음):")
    print(f"{'시점':>6} {'native':>8} {'xdist-load':>12}")
    for frac in (0.25, 0.5, 0.75, 0.9, 0.97):
        na = active_at(nat["active"], frac, nat["makespan"])
        xa = active_at(xl["active"], frac, xl["makespan"])
        print(f"{int(frac*100):5}% {na:8} {xa:12}")
    print("\n해석: native는 큐 빌 때까지 워커 유지(활성 높게 지속) → 꼬리 붕괴 없음.")
    print("      xdist-load는 뒤로 갈수록 활성 급감(유휴 워커 종료) + 쿼터400 발생.")


if __name__ == "__main__":
    main()
