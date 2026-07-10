"""CRUD collection: exact-id allowlist + worker-aware longest-first ordering (xdist)
+ per-run duration learning.

Collection-time behaviours, keyed off the parametrized ``lifecycle`` case:

1. ``SCP_CRUD_IDS`` (comma-separated EXACT lifecycle ids) — deselect every lifecycle
   not in the set. The platform console emits a precise id list (node -> source.lifecycle)
   so "select services in the UI -> run exactly those" works without a ``-k`` expression
   (hyphenated ids like ``database-mysql-cluster`` parse as subtraction under ``-k``).

2. Worker-aware longest-first ordering (A1, run-85b2/377e 스케줄 분석 2026-07-10) —
   순수 duration 내림차순은 xdist load 스케줄러의 초기 연속-청크 배정 때문에
   최상위 무거운 2개가 같은 워커에서 직렬화됐다 (실측: mysql 종료 0.2s 뒤
   postgresql 시작 → 그 postgresql이 run-377e makespan 결정, 94.2분). 이제
   [heavy_i, light_i] 페어 인터리브로 긴 작업 n개(워커 수)가 전부 t≈0에 서로
   다른 워커에서 출발한다. 미측정 lifecycle은 0.0(영구 꼬리)이 아니라
   duration_stats 클래스 기본값(cluster-grade≈2400s)을 쓴다 (A3).

3. Duration learning (A2) — 종전엔 dag 경로만 durations.json을 갱신하고 이
   pytest/xdist 경로(콘솔·CI 실경로)는 학습하지 않아 파일이 2주 정체 +
   신설 lifecycle 7종이 영원히 미측정이었다. 이제 controller가 lifecycle
   테스트의 call-duration을 모아 세션 종료 시 rolling-average 스토어에 fold
   (live 런 지표가 있을 때만 — offline/mock 실행이 스토어를 오염시키지 않게).

Only ``lifecycle``-parametrized items are touched; any other collected tests keep their
position and are never deselected.
"""
import json
import os
from pathlib import Path

_DUR_PATH = Path(__file__).resolve().parents[2] / "data" / "optimizer" / "durations.json"


def _durations() -> dict:
    try:
        raw = json.loads(_DUR_PATH.read_text())
        return {k: float(v.get("avg_s") or 0.0) for k, v in raw.items()}
    except Exception:  # noqa: BLE001 — ordering is best-effort; never break collection
        return {}


def _class_default_s(lc: dict) -> float:
    """미측정 lifecycle의 duration 기본값 — 0.0이 아니라 duration_stats의
    클래스 추정(cluster-grade≈2400s 등)을 재사용 (A3)."""
    try:
        from tools.duration_stats import CLASS_DEFAULT_S, classify_lifecycle
        return float(CLASS_DEFAULT_S[classify_lifecycle(lc)])
    except Exception:  # noqa: BLE001
        return 0.0


def _interleave_for_workers(ordered: list, n: int) -> list:
    """desc 정렬 → [heavy_i, light_i] 페어 인터리브 (LPT 페어링).

    xdist load 스케줄러의 초기 배정은 수집 순서의 연속 청크(워커당 2개)라
    순수 내림차순이면 인접한 최상위 항목들이 같은 워커에서 직렬화된다.
    최상위 n개(워커 수)를 각각 가장 가벼운 항목과 짝지으면 초기 페어가
    (긴+짧은)으로 균형 잡혀 긴 작업 전부가 t≈0에 병렬 출발한다."""
    if n < 2 or len(ordered) <= n:
        return list(ordered)
    head, tail = list(ordered[:n]), list(ordered[n:])
    k = min(n, len(tail))
    fillers = tail[len(tail) - k:][::-1]      # 가장 가벼운 k개, 가벼운 순
    remainder = tail[:len(tail) - k]
    out = []
    for i, h in enumerate(head):
        out.append(h)
        if i < len(fillers):
            out.append(fillers[i])
    return out + remainder


def _worker_count(config) -> int:
    try:
        return int(config.getoption("numprocesses") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _lifecycle_id(item) -> str | None:
    spec = getattr(item, "callspec", None)
    lc = spec.params.get("lifecycle") if spec else None
    return lc.get("id") if isinstance(lc, dict) else None


def pytest_collection_modifyitems(config, items) -> None:
    # 1) exact-id allowlist — deselect lifecycle cases not in SCP_CRUD_IDS (non-lifecycle
    #    tests are always kept). Precise selection from the console, no -k parsing.
    only = {x.strip() for x in os.getenv("SCP_CRUD_IDS", "").split(",") if x.strip()}
    if only:
        keep, drop = [], []
        for it in items:
            lid = _lifecycle_id(it)
            (drop if (lid is not None and lid not in only) else keep).append(it)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep

    # 2) worker-aware longest-first ordering of the surviving lifecycle items
    dur = _durations()
    slots = [i for i, it in enumerate(items) if _lifecycle_id(it)]
    if len(slots) < 2:
        return

    def _key(it) -> float:
        lid = _lifecycle_id(it)
        v = dur.get(lid, 0.0)
        if v <= 0.0:
            lc = getattr(it, "callspec", None)
            lc = lc.params.get("lifecycle") if lc else None
            v = _class_default_s(lc if isinstance(lc, dict) else {})
        return v

    ordered = sorted((items[i] for i in slots), key=_key, reverse=True)
    ordered = _interleave_for_workers(ordered, _worker_count(config))
    for slot, it in zip(slots, ordered):
        items[slot] = it


# --- 3) duration learning (A2) — controller-side fold into durations.json ---
_MEASURED: dict[str, float] = {}


def pytest_runtest_logreport(report) -> None:
    if getattr(report, "when", "") != "call":
        return
    nodeid = getattr(report, "nodeid", "") or ""
    if "test_crud_lifecycle[" not in nodeid:
        return
    lid = nodeid.split("[", 1)[1].rstrip("]")
    d = float(getattr(report, "duration", 0.0) or 0.0)
    if lid and d > 0:
        _MEASURED[lid] = max(_MEASURED.get(lid, 0.0), d)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    # controller에서만 (워커는 PYTEST_XDIST_WORKER 보유), 그리고 LIVE 런 지표가
    # 있을 때만 fold — offline/mock 실행이 학습 스토어를 오염시키지 않게.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    if not _MEASURED:
        return
    if not (os.getenv("APITEST_RUN_ID") or os.getenv("SCP_CONSOLE_EVENTS")):
        return
    try:
        from regression.scenarios.schedule_optimizer import update_durations
        update_durations(dict(_MEASURED))
        print(f"[durations] {len(_MEASURED)}개 lifecycle 실측을 "
              f"durations.json에 fold (rolling avg)")
    except Exception as exc:  # noqa: BLE001 — learning is best-effort
        print(f"[durations] fold 실패(무시): {exc}")
