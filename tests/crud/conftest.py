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
# 실측 학습은 git-추적본이 아니라 **로컬 오버레이**에 쓴다 (2026-07-11 실측 결함:
# 커밋된 durations.json에 fold하면 오너 콘솔의 작업트리가 더러워져 다음 git pull과
# 충돌 — 학습이 조용히 유실된다). 읽기는 커밋본+오버레이 병합, 오버레이 우선.
_DUR_LOCAL = _DUR_PATH.with_name("durations.local.json")


def _durations() -> dict:
    out: dict = {}
    for p in (_DUR_PATH, _DUR_LOCAL):
        try:
            raw = json.loads(p.read_text())
            out.update({k: float(v.get("avg_s") or 0.0) for k, v in raw.items()})
        except Exception:  # noqa: BLE001 — ordering is best-effort; never break collection
            continue
    return out


def _priority_first() -> list:
    """dependencies.json vpc_schedule.priority_first — 수집 정렬에서 맨 앞에
    핀할 lifecycle id 목록. 수집 단계라 무거운 import 없이 파일을 직접 읽는다
    (schedule_optimizer.load_priority_first와 같은 원천)."""
    p = Path(__file__).resolve().parents[2] / "regression" / "scenarios" / "dependencies.json"
    try:
        return list(json.loads(p.read_text()).get("vpc_schedule", {})
                    .get("priority_first", []))
    except Exception:  # noqa: BLE001 — ordering is best-effort; never break collection
        return []


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
    """워커 수 — run-892a 딥다이브(2026-07-11)로 잡은 무효화 버그: `-n` 실행에서
    수집·정렬은 xdist WORKER 프로세스가 하는데, xdist remote.setup_config가
    워커의 `numprocesses` 옵션을 None으로 비워 이 함수가 항상 0을 반환했다 →
    인터리브가 배포 이후 실전에서 한 번도 작동하지 않았고, 순수 내림차순 +
    load의 워커당 연속 2개 선배정으로 '몬스터 뒤 몬스터'가 매 런 재현
    (74.6분 런의 롱폴 6건 전부가 이 페어링의 피해자). 워커에서도 항상 채워지는
    PYTEST_XDIST_WORKER_COUNT env를 1차 소스로 쓴다 (컨트롤러/단일 프로세스는
    옵션 폴백)."""
    try:
        env_n = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "") or 0)
        if env_n:
            return env_n
    except ValueError:
        pass
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
    pinned = frozenset(_priority_first())

    def _key(it) -> tuple:
        lid = _lifecycle_id(it)
        lc = getattr(it, "callspec", None)
        lc = lc.params.get("lifecycle") if lc else None
        lc = lc if isinstance(lc, dict) else {}
        v = dur.get(lid, 0.0)
        if v <= 0.0:
            v = _class_default_s(lc)
        # 0차 키 = priority_first 핀 (오너 2026-07-13): 짧은 VPC-슬롯 소비자
        # (vpc-subnet-vip-nat 등)가 LPT에서 뒤로 밀리면 슬롯이 장기 점유된
        # 시점에 도착해 대기+런 꼬리가 된다. t=0에는 슬롯이 비어 있으므로
        # 핀 대상은 duration과 무관하게 맨 앞에 세운다.
        # 2차 키 = 스텝 수: 클래스 추정 동률(cluster-grade 2400s ×20+)로 LPT가
        # 퇴화하던 문제의 타이브레이크 (2026-07-11 run-923a 재구성 — ske/vs-full이
        # 동률 무리에 섞여 뒤로 밀렸음). 실측이 쌓이면 1차 키가 지배한다.
        return (lid in pinned, v, len(lc.get("steps") or []))

    ordered = sorted((items[i] for i in slots), key=_key, reverse=True)
    ordered = _interleave_for_workers(ordered, _worker_count(config))
    for slot, it in zip(slots, ordered):
        items[slot] = it


# --- 3) duration learning (A2) — controller-side fold into durations.json ---
_MEASURED: dict[str, float] = {}


def pytest_runtest_logreport(report) -> None:
    if getattr(report, "when", "") != "call":
        return
    # PASS만 학습 (run-892a 딥다이브): fast-fail(생성 500이 10초 만에 종료)이
    # 실측으로 fold되면 LPT 랭크가 오염된다 — database-mysql-cluster avg 10.8s,
    # mariadb-subops 45.8s(실제 2308s)로 학습돼 몬스터가 경량으로 분류됐었다.
    if not getattr(report, "passed", False):
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
        # 로컬 오버레이에 fold — 커밋본을 더럽히지 않는다 (git pull 충돌 방지).
        # 시딩 카피는 하지 않는다: 옛 커밋본을 복사해두면 커밋본이 재구축돼도
        # 낡은 오버레이가 계속 이겨버린다 (2026-07-11 실측 — 게이트 테스트가
        # 만든 시드 오버레이가 재구축 값을 가림). 오버레이는 이 머신의 실측만.
        update_durations(dict(_MEASURED), path=_DUR_LOCAL)
        print(f"[durations] {len(_MEASURED)}개 lifecycle 실측을 "
              f"durations.local.json에 fold (rolling avg, git-비추적)")
    except Exception as exc:  # noqa: BLE001 — learning is best-effort
        print(f"[durations] fold 실패(무시): {exc}")
