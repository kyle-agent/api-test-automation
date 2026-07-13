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
    # 병합 규칙 = **max** (2026-07-13 run-c373 실측): 종전 '오버레이 우선'은
    # 로컬 오버레이의 하향 오염(옛 fast-fail 학습 등)이 커밋본의 실측 큰 값을
    # 가려 database-postgresql-cluster(커밋본 24분, rank 10)를 경량으로
    # 오분류 → +42분에야 시작해 홀로 23분 런 꼬리(makespan 66.3분)가 됐다.
    # LPT에서 과대추정은 '일찍 시작'일 뿐 무해하고 과소추정은 몬스터 꼬리를
    # 만드므로, 두 스토어에 다 있으면 큰 값을 쓴다.
    out: dict = {}
    for p in (_DUR_PATH, _DUR_LOCAL):
        try:
            raw = json.loads(p.read_text())
            for k, v in raw.items():
                val = float(v.get("avg_s") or 0.0)
                out[k] = max(out.get(k, 0.0), val)
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
    """desc 정렬 → [heavy_i, light_i] 페어 인터리브 (LPT 페어링, --dist=load용).

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


def _roundrobin_blocks_for_workers(ordered: list, n: int) -> list:
    """desc 정렬 → 라운드로빈 버킷 연접 (--dist=worksteal용, 2026-07-13 run-c373).

    worksteal은 수집 순서를 워커별 **연속 블록**으로 통째로 선분배한다 — 순수
    내림차순이나 페어 인터리브는 최상위 몬스터들이 같은 블록(=같은 워커)에
    직렬로 묶인다 (실측 c373: LPT rank 11 pg-cluster가 +42분 지각, 홀로 런
    꼬리 23분; 실제 시작 순서와 LPT의 스피어만 상관 +0.11 = 정렬 무효화).
    rank j를 버킷 j%n에 라운드로빈 배분 후 버킷을 이어붙이면, 연속 블록 b의
    첫 항목 = 전체 rank b — 상위 n개가 전부 서로 다른 워커에서 t≈0 출발하고
    각 블록 안은 desc라 유휴 워커의 스틸이 꼬리(경량)부터 가져간다."""
    if n < 2 or len(ordered) <= n:
        return list(ordered)
    buckets = [[] for _ in range(n)]
    for j, item in enumerate(ordered):
        buckets[j % n].append(item)
    out = []
    for b in buckets:
        out.extend(b)
    return out


def _is_read_only(lc: dict) -> bool:
    """모든 스텝이 GET인 라이프사이클 = 읽기전용 커버리지 (생성 자원 0).
    이런 건 선행자원이 없어 언제든 돌 수 있으므로, heavy 뒤에 strand시키지
    말고 global pending 앞으로 띄워 빈 워커가 초반에 집게 한다 (오너 2026-07-13
    run-19a5: gen-volume-type·*-reads가 heavy 뒤 페어에 묶여 30~40분에야 시작)."""
    steps = lc.get("steps") or []
    if not steps:
        return False
    return all((s.get("method") or "GET").upper() == "GET" for s in steps if s.get("path"))


def _has_prereq(lid: str, _cache={}) -> bool:
    """dependencies.json prerequisites에 선언된 = provider(클러스터/VM 등) 대기.
    이런 건 provider가 도는 뒤쪽(global pending 후미)에 둬 자연 정렬한다."""
    if not _cache:
        p = Path(__file__).resolve().parents[2] / "regression" / "scenarios" / "dependencies.json"
        try:
            _cache["p"] = set(json.loads(p.read_text()).get("prerequisites", {}))
        except Exception:  # noqa: BLE001
            _cache["p"] = set()
    return lid in _cache["p"]


def _order_for_load(triples: list, n: int) -> list:
    """--dist=load용 순서 (2026-07-13 run-19a5·da22, 오너 설계). 입력은 LPT desc
    정렬된 (item, id, lc) triple. load는 dequeue 시점에 dependency를 못 보므로
    (테스트 불투명), 순서를 수집 순서에 인코딩한다:

      pair-first(t=0)     = heavy(provider·병목) 상위 n
      pair-second(strand) = 가장 가벼운 non-read-only n개 (read-only·heavy는 제외)
      global pending      = 나머지를 **LPT-desc(긴 것 먼저)** — 빈 슬롯엔 예상
                            시간 긴 놈 먼저(오너 2026-07-13). no-dep 먼저(즉시 실행
                            가능), dependent 후미(provider 도는 뒤). read-only는
                            strand에서 빠져 pool에 있으므로 창 안(~수분)에 뜬다.

    heavy를 안 밀어 makespan 무영향 + LPT-pool이라 긴 미디엄이 pool에 들어와도
    makespan을 안 늘린다. read-only 지각(19a5 17~40분)은 strand 제거로 해소."""
    items = [t[0] for t in triples]
    if n < 2 or len(triples) <= n:
        return items
    head = triples[:n]                                   # heavy → pair-first (t=0)
    tail = triples[n:]
    strandable = [t for t in tail if not _is_read_only(t[2])]    # strand 후보(read-only 제외)
    k = min(n, len(strandable))
    fillers = strandable[len(strandable) - k:][::-1]      # 가장 가벼운 non-read n개, strand
    filler_lids = {t[1] for t in fillers}
    # global pending = strand 안 된 나머지, LPT-desc(원 순서) 유지 → 긴 것 먼저.
    pool = [t for t in tail if t[1] not in filler_lids]
    pool_nodep = [t for t in pool if not _has_prereq(t[1])]   # no-dep 먼저(desc)
    pool_dep = [t for t in pool if _has_prereq(t[1])]         # dependent 후미(desc)
    out = []
    for i, h in enumerate(head):
        out.append(h[0])
        if i < len(fillers):
            out.append(fillers[i][0])
    out += [t[0] for t in pool_nodep]
    out += [t[0] for t in pool_dep]
    return out


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

    def _lc_of(it) -> dict:
        lc = getattr(it, "callspec", None)
        lc = lc.params.get("lifecycle") if lc else None
        return lc if isinstance(lc, dict) else {}

    ordered_items = sorted((items[i] for i in slots), key=_key, reverse=True)
    triples = [(it, _lifecycle_id(it), _lc_of(it)) for it in ordered_items]
    # 실행 경로(local_run/console2)는 --dist=load --maxschedchunk=1 (2026-07-13
    # run-afa8/19a5 판정). _order_for_load: heavy는 pair-first(t=0), read-only는
    # global pending 앞으로 띄우고, dependent는 후미로 — load가 dequeue 시점에
    # dependency를 못 보므로(테스트 불투명) dependency 순서를 수집 순서에 인코딩
    # 한다. (--dist=worksteal로 되돌리면 _roundrobin_blocks_for_workers로 교체 —
    # dist 모드와 정렬은 한 쌍이다.)
    ordered = _order_for_load(triples, _worker_count(config))
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
