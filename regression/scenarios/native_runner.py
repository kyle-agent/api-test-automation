"""Native task-queue runner — engine.run_lifecycle을 직접 구동하는 단일 프로세스
스레드풀 스케줄러. xdist 대체 (2026-07-13, 오너 지시: "xdist 워크라운드 말고
목적특화 스케줄러").

**왜 xdist가 아니라 이것인가** (tools/scheduler_sim 실측: native 70분/쿼터400=0 vs
xdist-load 90분/400=4): 우리 워크로드는 I/O-bound(API 호출 + async provisioning
대기)인데 xdist는 CPU-bound 테스트 병렬용이라, ①의존성/쿼터/LPT 무지 ②수집순서
디스패치 ③MIN_PENDING 버퍼 ④풀 비면 유휴 워커 종료(꼬리 붕괴) — 우리가 지금까지
정렬 인코딩·strand 제거로 우회하던 그 한계들이다. engine.run_lifecycle이 이미
pytest와 분리돼 있으므로, 얇은 스레드풀 스케줄러로 직접 구동하면 그 넷을 네이티브로
해결한다:

  * 동적 LPT — 빈 워커가 ready-queue에서 가장 긴 것을 pop (빈 슬롯 = 긴 것 먼저).
  * no-shutdown — 큐 빌 때까지 워커 유지 (남은 일이 워커 버퍼에 갇혀 1-in-1-out
    되던 꼬리 붕괴 제거; 종료는 대기 0일 때만).
  * 공유 Budget — 모든 스레드가 하나의 Budget 공유 → 계정-전역 쿼터 조율
    (private-dns 3·vpc 5 초과 create가 400 나던 레이스를 skip-not-fail로).
  * dependent 뒤로 — **진짜 inter-lifecycle 의존만** 뒤에 디스패치(provider가 도는
    뒤). prereq가 공유-인프라/자체-생성 kind뿐인 soft-의존(예: SKE)은 길이대로 앞단
    (LPT) — 긴 soft-의존을 demote하면 꼬리만 늘어난다(2026-07-13 수정).

opt-in: local_run/console2가 SCP_NATIVE_RUNNER=true일 때 pytest-xdist 서브프로세스
대신 이 러너를 호출. xdist 경로는 그대로 폴백으로 남긴다 (라이브 검증 후 기본화).
정렬 해킹(_order_for_load/interleave/roundrobin)은 xdist 전용 — 러너엔 불필요.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# VPC-생성자가 슬롯을 못 얻었을 때 skip 대신 대기하는 상한/폴 간격 (초). 워커 하나가
# 이 동안 그 라이프사이클을 붙잡고 재시도한다. 슬롯은 다른 self-create가 끝나면 반납됨.
_VPC_WAIT_TIMEOUT = float(os.environ.get("SCP_NATIVE_VPC_WAIT_TIMEOUT", "1800"))
_VPC_WAIT_POLL = float(os.environ.get("SCP_NATIVE_VPC_WAIT_POLL", "5"))


# ---------------------------------------------------------------------------
# 우선순위 = LPT(긴 것 먼저) + dependent 뒤로. xdist의 strand/interleave 불요 —
# 동적 pop이 버퍼-갇힘을 원천 제거하므로 순수 정렬이면 충분.
# ---------------------------------------------------------------------------
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


# 공유-인프라 / 자체-생성 리소스 kind — 라이프사이클 내부에서 만들거나 미리
# 프로비저닝된 공유 VPC/서브넷이 제공한다(= 다른 라이프사이클의 산출물이 아님).
# 이런 kind로만 이뤄진 prereq는 inter-lifecycle 순서 제약이 없으므로 demote하면
# 안 된다 (owner 2026-07-13: 긴 soft-의존 태스크 container-ske-cluster-nodepool
# (34.6m)을 dependent-last로 t=16분에 미뤄 makespan 50.7분 — 실제로는 다른
# 라이프사이클을 안 기다리는데 벌점만 먹음. 진짜 inter-lifecycle 의존만 뒤로 →
# 47.7분(최장 태스크 바닥선)으로 수렴). gantt_sim이 같은 집합을 재사용한다.
_SHARED_INFRA_KINDS = {"vpc", "subnet", "security-group", "keypair", "image",
                       "server-type", "filestorage-volume", "private-dns"}


def _prereq_map() -> dict:
    try:
        d = json.loads((_ROOT / "regression/scenarios/dependencies.json").read_text())
        return dict(d.get("prerequisites", {}))
    except Exception:  # noqa: BLE001
        return {}


def _true_dependents() -> set:
    """다른 라이프사이클의 산출물(공유-인프라가 아닌 prereq kind)에 의존하는
    라이프사이클 id 집합 — 이들만 LPT 뒤로 demote한다. 예: gen-cloudml-chain은
    ske-cluster/container-registry(다른 라이프사이클 산출)를 필요로 하므로 후미;
    container-ske-cluster-nodepool의 prereq(vpc/subnet/keypair 등)는 전부
    공유-인프라/자체-생성이라 demote 대상이 아니다."""
    return {lid for lid, kinds in _prereq_map().items()
            if any(k not in _SHARED_INFRA_KINDS for k in kinds)}


def _is_vpc_creator(lc: dict) -> bool:
    """새 VPC를 self-create하는 라이프사이클 = adopt 없는 create-vpc(POST /vpcs)
    스텝을 가진 것. 이들만 VPC 캡(생성 슬롯)을 점유한다(adopter는 재사용). 희소한
    VPC 슬롯을 라이프사이클 전체 구간 동안 붙잡으므로, 슬롯이 비어 있는 t=0에
    먼저 투입해 일찍 반납시켜야 다른 VPC-생성 시나리오가 슬롯을 못 얻어 대기하는
    창을 최소화한다 (오너 2026-07-13: "vpc 생성하는 주황색을 먼저 배치"). xdist의
    하드코딩 priority_first(vip-nat·vpc-subnet)를 스텝에서 파생 — 드리프트 견고."""
    for st in lc.get("steps", []):
        m = (st.get("method", "") or "").upper()
        p = (st.get("path", "") or "").rstrip("/")
        if m == "POST" and p.endswith("/vpcs") and not st.get("adopt"):
            return True
    return False


def priority_order(lifecycles: list[dict]) -> list[dict]:
    """정렬 우선순위: (1) 진짜 inter-lifecycle 의존은 후미, (2) VPC-생성자는 선두
    (희소 슬롯 조기 점유·반납), (3) 그 안에서 LPT(긴 것 먼저). VPC-생성자를 앞에
    둬도 워커가 넉넉해(30개 ≫ 무거운 것) makespan 바닥선(최장 태스크)은 그대로."""
    dur = _durations()
    dependents = _true_dependents()

    def key(lc):
        d = dur.get(lc["id"], 0.0)
        if d <= 0.0:
            try:
                from tools.duration_stats import CLASS_DEFAULT_S, classify_lifecycle
                d = float(CLASS_DEFAULT_S[classify_lifecycle(lc)])
            except Exception:  # noqa: BLE001
                d = 60.0
        # 진짜 의존 뒤 → VPC-생성자 앞 → 긴 것 먼저 → id 타이브레이크
        return (lc["id"] in dependents, not _is_vpc_creator(lc), -d, lc["id"])

    return sorted(lifecycles, key=key)


# ---------------------------------------------------------------------------
# 러너
# ---------------------------------------------------------------------------
def _wait_shared_subnets_active(client, shared_ctx, log, timeout=600, interval=10):
    """공유 서브넷 ACTIVE까지 대기 후 adopter 디스패치 (2026-07-13 라이브 검증
    2회 재현). native 러너는 provision 직후 즉시 디스패치라, 서브넷이 CREATING인
    채로 라이프사이클이 adopt하면 라이프사이클의 wait-subnet 타임아웃(180s)이
    서브넷 활성(>180s)보다 짧아 VM이 준비 안 된 서브넷에 생성돼 ERROR. 콘솔/xdist
    경로는 provision→pytest 기동 간격이 이를 흡수하지만 native엔 그 간격이 없으므로
    러너가 명시적으로 게이트한다 (adopter는 공유 인프라 준비에 의존)."""
    import time as _t
    sub_ids = [shared_ctx[k] for k in ("shared_subnet_id", "shared_db_subnet_id")
               if shared_ctx.get(k)]
    if not sub_ids:
        return
    deadline = _t.time() + timeout
    pending = set(sub_ids)
    while pending and _t.time() < deadline:
        for sid in list(pending):
            try:
                sn = client.get(f"/v1/subnets/{sid}", service="vpc").body.get("subnet", {})
                if sn.get("state") == "ACTIVE":
                    pending.discard(sid)
            except Exception:  # noqa: BLE001
                pass
        if pending:
            log(f"[native] 공유 서브넷 ACTIVE 대기 {len(pending)}/{len(sub_ids)}…")
            _t.sleep(interval)
    if pending:
        log(f"[native] ⚠ 공유 서브넷 {len(pending)}개 ACTIVE 미달({timeout}s) — 그대로 진행")
    else:
        log("[native] 공유 서브넷 ACTIVE 확인 — 라이프사이클 디스패치 시작")


def run(lifecycle_ids, *, workers: int | None = None, log=print) -> dict:
    """선택된 lifecycle을 스레드풀로 병렬 실행. 게이트(mutations/heavy 등)는
    engine.run_lifecycle이 cfg(env)로 강제. 반환: {results, workers, elapsed_s}."""
    from core.config import settings as cfg
    from core.http_client import ApiClient
    from core import budgets as _budgets
    from regression.scenarios import engine

    cfg.require_credentials()
    client = ApiClient(cfg)
    ids = [x for x in lifecycle_ids if x]
    idset = set(ids)
    lcs = [lc for lc in engine.active_lifecycles() if lc["id"] in idset]
    ordered = priority_order(lcs)
    n = int(workers or os.environ.get("SCP_LOCAL_WORKERS", "30"))
    n = max(1, min(n, len(ordered) or 1))

    # 공유 인프라 — provision_shared_vpc는 env-aware (SCP_SHARED_VPC_ID 있으면 adopt,
    # 없으면 provision + teardown 반환). local_run이 앞서 provision했으면 adopt.
    shared_ctx, shared_teardown = {}, (lambda: None)
    try:
        res = engine.provision_shared_vpc(client, cfg)
        if isinstance(res, tuple):
            shared_ctx, shared_teardown = res
        else:
            shared_ctx = res or {}
    except Exception as e:  # noqa: BLE001
        log(f"[native] shared VPC provision 경고: {e}")

    # 공유 서브넷 ACTIVE 게이트 — adopter를 준비된 인프라에만 디스패치 (라이브
    # 검증: 이게 없으면 서브넷 CREATING 중 adopt → wait-subnet 타임아웃 → VM ERROR).
    if shared_ctx:
        _wait_shared_subnets_active(client, shared_ctx, log)

    budget = _budgets.Budget()          # **공유** (스레드-안전) — 계정-전역 쿼터 조율
    # VPC 세마포어 시드 (2026-07-13, 오너: "세마포어로 하면 b가 되는 거 아냐?").
    # 상주 VPC(공유+net-A+net-B)와 provision은 Budget 밖에서 만들어지므로, 시드가
    # 없으면 세마포어는 캡 5가 통째로 비었다고 보고 self-create를 무제한 admit한다
    # (상주 3 + self-create N → 5 초과 시 400). 계정 실사용을 sync하면 상주 3개(+이전
    # 런 잔재까지 — IB-047 구멍)가 소비된 것으로 잡혀, self-create는 남은 슬롯만 쓴다.
    # adopter 27개는 adopt-skip(engine.py:1215 continue)이라 reserve를 안 해 세마포어를
    # 오염시키지 않음 → 순수하게 '새 VPC를 만드는' self-create만 캡에 걸린다.
    _live_vpc = _budgets.live_count("vpc")
    if _live_vpc is not None:
        budget.sync("vpc", _live_vpc)
        log(f"[native] VPC 세마포어 시드: 계정 실사용 {_live_vpc}/"
            f"{budget.limits.get('vpc')} → self-create 여유 {budget.available('vpc')}")
    else:
        log("[native] VPC 실사용 조회 실패 — 세마포어 미시드(상주 미반영, 종전 동작)")
    reg = engine.ResourceRegistry()     # 공유 매니페스트

    lock = threading.Lock()
    queue = list(ordered)
    results: list[dict] = []
    started = 0
    t0 = time.time()

    def worker(wid: int):
        nonlocal started
        while True:
            with lock:
                if not queue:
                    return                          # 큐 빔 → 그때만 워커 종료
                lc = queue.pop(0)                    # 동적 LPT: 가장 긴 ready
                started += 1
                sidx = started
            log(f"[native] w{wid} start [{sidx}/{len(ordered)}] {lc['id']}")
            # VPC-생성자가 슬롯 부족으로 예산-skip되면 skip이 아니라 **대기 후 재실행**
            # (오너 2026-07-13: "대기했다 실행"). 예산-skip은 create 이전에 나므로
            # (created=0, 토큰 반납됨) 재실행이 멱등 — 슬롯이 나면 성공한다. VPC-생성자를
            # 앞에 배치(priority_order)해 t=0 빈 슬롯을 먼저 잡으니 이 대기는 드물게만
            # 발동(생성자 수 > 여유 슬롯일 때). 무한 대기 방지 timeout.
            _is_vc = _is_vpc_creator(lc)
            _waited = 0.0
            while True:
                try:
                    r = engine.run_lifecycle(lc, client, cfg, budget=budget,
                                             resource_registry=reg, shared_ctx=shared_ctx)
                except Exception as e:  # noqa: BLE001 — 개별 실패가 러너를 죽이면 안 됨
                    r = {"id": lc["id"], "status": "failed", "reason": repr(e),
                         "failed_groups": [], "created": 0}
                    log(f"[native] w{wid} FAIL {lc['id']}: {e}")
                    break
                if (_is_vc and r.get("status") == "skipped"
                        and "budget 'vpc'" in (r.get("reason") or "")
                        and _waited < _VPC_WAIT_TIMEOUT):
                    if _waited == 0.0:
                        log(f"[native] w{wid} {lc['id']} VPC 슬롯 대기 중"
                            f"(여유 {budget.available('vpc')}/{budget.limits.get('vpc')})…")
                    time.sleep(_VPC_WAIT_POLL)
                    _waited += _VPC_WAIT_POLL
                    continue                    # 슬롯 날 때까지 대기 후 재실행
                break
            with lock:
                results.append(r)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        shared_teardown()
    except Exception as e:  # noqa: BLE001
        log(f"[native] shared teardown 경고: {e}")

    elapsed = time.time() - t0
    by_status: dict = {}
    for r in results:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    log(f"[native] 완료 — {len(results)}개 · {by_status} · {elapsed/60:.1f}분 · 워커 {n}")
    return {"results": results, "workers": n, "elapsed_s": elapsed,
            "by_status": by_status}


def main(argv=None) -> int:
    """CLI 진입점 — local_run/console2가 pytest 대신 Popen (SCP_NATIVE_RUNNER=true).
    SCP_CRUD_IDS(정확 id 목록)·게이트·SCP_CONSOLE_EVENTS를 env로 받는다 (pytest
    경로와 동일 계약). 콘솔이 kill 가능하도록 별도 프로세스로 돈다."""
    import sys
    ids = [x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",") if x.strip()]
    if not ids:
        from regression.scenarios import engine
        ids = [lc["id"] for lc in engine.active_lifecycles()]

    def _log(msg):
        print(msg, flush=True)

    out = run(ids, log=_log)
    # rc: 진짜 실패가 있으면 1 (skip은 실패 아님 — pytest 경로와 동일 의미)
    return 1 if out["by_status"].get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
