"""Offline tests for the worker-aware CRUD collection ordering (A1/A3, 2026-07-10)
and the duration-learning gate (A2) — run-85b2/377e/afa8 스케줄 분석의 회귀 고정.

근거: 순수 duration 내림차순은 xdist 초기 연속-청크 배정에서 최상위 무거운
2개를 같은 워커에 직렬화시켰다 (mysql 종료 0.2s 뒤 postgresql 시작 — 그
postgresql이 run-377e makespan 결정). 인터리브는 [heavy, light] 페어로 긴
작업들을 서로 다른 워커에 t≈0 배정한다.

**활성 스케줄러 = --dist=load --maxschedchunk=1 (2026-07-13 run-afa8 판정).**
load는 글로벌 pending 풀을 유지해 work-conserving하다 — 빈 워커가 의존성 없는
대기를 즉시 집으므로 worksteal의 워커 shutdown→라이트 꼬리 지각(afa8: 2분짜리
scr-repo가 46.3분 시작)이 없다. load 초기 청크=워커당 2라 인터리브와 한 쌍
(상위 n 몬스터 offset0). worksteal용 라운드로빈(_roundrobin_blocks_for_workers)은
대안 경로로 보존 — dist 모드와 정렬은 한 쌍이다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parents[1] / "crud" / "conftest.py"
_spec = importlib.util.spec_from_file_location("crud_conftest", _CONFTEST)
crud_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and crud_conftest)


def test_interleave_pairs_heaviest_with_lightest():
    ordered = list("ABCDEFGH")  # A=가장 무거움 … H=가장 가벼움
    out = crud_conftest._interleave_for_workers(ordered, 3)
    # 최상위 3개(A,B,C)가 각각 가장 가벼운 3개(H,G,F)와 페어 → 워커별 초기
    # 2-청크가 (긴+짧은)으로 균형; 나머지는 desc 그대로 이어짐.
    assert out == ["A", "H", "B", "G", "C", "F", "D", "E"]
    # 어떤 워커의 초기 페어에도 최상위 무거운 항목이 2개 연속으로 없다
    for i in range(3):
        pair = out[2 * i:2 * i + 2]
        assert not set(pair) <= {"A", "B", "C"}


def test_interleave_noop_when_few_items_or_serial():
    assert crud_conftest._interleave_for_workers(["A", "B"], 0) == ["A", "B"]
    assert crud_conftest._interleave_for_workers(["A", "B"], 1) == ["A", "B"]
    assert crud_conftest._interleave_for_workers(["A", "B"], 4) == ["A", "B"]


def test_interleave_preserves_membership():
    ordered = [f"lc{i}" for i in range(25)]
    out = crud_conftest._interleave_for_workers(ordered, 18)
    assert sorted(out) == sorted(ordered) and len(out) == 25


def _xdist_load_initial_chunks(order: list, n: int, maxschedchunk: int = 1):
    """xdist load `schedule()`의 **초기 분배** 재현 (버전 3.8 검증).

    load는 pending=range(N)로 놓고, 각 노드에 `node_chunksize=max(min(items//4,
    maxschedchunk), 2)`개의 **연속 청크**를 1회 배정한 뒤 나머지는 **글로벌
    pending 풀**에 남긴다(이후 완료 시마다 동적 리필). worksteal과 달리 pending이
    남아있는 한 워커를 죽이지 않으므로, 초기 청크 배치만 makespan 리스크다.
    반환: (초기청크 리스트, 글로벌 pending 잔여)."""
    N = len(order)
    if N < 2 * n:                                   # round-robin 1개씩
        chunks = [[] for _ in range(n)]
        for i, it in enumerate(order):
            chunks[i % n].append(it)
        return chunks, []
    cs = max(min((N // n) // 4, maxschedchunk), 2)
    chunks, pos = [], 0
    for _ in range(n):
        chunks.append(order[pos:pos + cs]); pos += cs
    return chunks, order[pos:]


def test_interleave_puts_every_monster_at_offset0_under_load():
    """활성 경로 회귀 (run-afa8): load 초기 청크(워커당 2) 하에서 [heavy,light]
    인터리브는 상위 n 몬스터를 **전부 각 워커 초기 청크의 offset 0**(=t=0 시작)에
    놓는다 — 순수 desc는 인접 몬스터를 같은 워커 청크에 직렬화(offset 1)한다."""
    N, n = 119, 24
    ordered = [f"lc{i}" for i in range(N)]          # lc0=가장 무거움
    weights = {f"lc{i}": N - i for i in range(N)}
    il = crud_conftest._interleave_for_workers(ordered, n)
    chunks, pool = _xdist_load_initial_chunks(il, n)
    pos = {it: off for ch in chunks for off, it in enumerate(ch)}
    monsters = sorted(weights, key=lambda k: weights[k], reverse=True)[:n]
    # 상위 n 몬스터가 전부 초기 청크에 있고, 전부 offset 0
    assert all(m in pos for m in monsters), "몬스터가 초기 청크 밖(풀)으로 밀림"
    assert all(pos[m] == 0 for m in monsters), \
        f"몬스터 offset 0 아님: {[pos[m] for m in monsters]}"

    # 대조군: 순수 desc는 직렬화(offset 1 존재)
    dchunks, _ = _xdist_load_initial_chunks(ordered, n)
    dpos = {it: off for ch in dchunks for off, it in enumerate(ch)}
    assert any(dpos.get(m) == 1 for m in monsters), "desc가 직렬화를 재현해야 유효"


def _xdist_worksteal_blocks(collection_order: list, n: int) -> list[list]:
    """xdist ≥3.2 worksteal 초기 분배의 **정확한** 재현 (버전 3.8 검증).

    WorkStealingScheduling.schedule()은 pending=range(N)(수집 순서)로 놓고
    check_schedule()이 유휴 워커에게 `num_send = len(pending) // nodes_remaining`
    만큼 **연속 프리픽스**로 나눠준다 → 워커 i의 블록은 수집 순서의 연속 조각이고
    블록 크기는 균등하지 않다 (N=119,n=24 → [4,5,5,…]). 이 비균등이 라운드로빈
    버킷 경계와 어긋나는 지점이 검증 대상이므로, 균등 가정이 아니라 실제 분배
    알고리즘을 그대로 흉내 낸다."""
    pending = list(collection_order)
    blocks = []
    for i in range(n):
        num = len(pending) // (n - i)
        blocks.append(pending[:num])
        del pending[:num]
    return blocks


def _monster_offsets(order: list, weights: dict, n: int, top: int) -> list[int]:
    """order를 실제 worksteal로 분배했을 때, 상위 top개 몬스터가 자기 워커
    블록 안에서 몇 번째(offset)에 오는지. 0=블록 선두(즉시 시작)."""
    blocks = _xdist_worksteal_blocks(order, n)
    pos = {item: off for blk in blocks for off, item in enumerate(blk)}
    monsters = sorted(weights, key=lambda k: weights[k], reverse=True)[:top]
    return [pos[m] for m in monsters]


def test_roundrobin_spreads_monsters_across_worker_blocks():
    """worksteal 수리의 **실효** 속성 (run-c373): 상위 몬스터들이 서로 다른 워커
    블록으로 흩어져, 각 몬스터가 자기 블록에서 경량 항목 뒤에만 온다 (offset ≤ 1) —
    순수 내림차순은 최상위 몬스터들을 같은 블록에 직렬화(offset이 n까지)한다.

    이전 회귀 테스트는 블록 경계를 0,3,6(균등)으로 가정했으나 실제 worksteal은
    8/3을 0,2,5로 쪼갠다 → 그 단언은 실제 분배와 무관했다. 여기서는 실제 분배
    알고리즘(_xdist_worksteal_blocks)으로 검증한다."""
    N, n = 119, 24
    ordered = [f"lc{i}" for i in range(N)]   # lc0=가장 무거움 … lc118=가장 가벼움
    weights = {f"lc{i}": N - i for i in range(N)}

    rr = crud_conftest._roundrobin_blocks_for_workers(ordered, n)
    desc = list(ordered)

    rr_off = _monster_offsets(rr, weights, n, top=n)
    desc_off = _monster_offsets(desc, weights, n, top=n)

    # 라운드로빈: 어떤 몬스터도 블록에서 다른 몬스터 뒤에 직렬화되지 않는다.
    # offset ≤ 1 이고, offset 1인 몬스터의 앞 항목은 반드시 더 가벼운 항목이다.
    assert max(rr_off) <= 1, f"라운드로빈 몬스터 offset이 1 초과: {rr_off}"
    blocks = _xdist_worksteal_blocks(rr, n)
    pos = {it: (b, o) for b, blk in enumerate(blocks) for o, it in enumerate(blk)}
    for m in sorted(weights, key=lambda k: weights[k], reverse=True)[:n]:
        b, o = pos[m]
        if o == 1:
            predecessor = blocks[b][0]
            assert weights[predecessor] < weights[m], (
                f"{m} 앞 항목 {predecessor}가 더 무겁다 — 몬스터 직렬화")

    # 순수 내림차순은 최상위들을 같은 블록에 직렬화 → offset이 훨씬 크다.
    assert max(desc_off) >= 3, (
        f"내림차순이 직렬화를 재현해야 회귀 대비가 유효: {desc_off}")


def test_roundrobin_blocks_preserve_membership_and_desc_within_bucket():
    """멤버십 보존 + 각 버킷(=라운드로빈 그룹) 내부는 desc — 유휴 워커의 스틸이
    꼬리(경량)부터 가져간다."""
    ordered = list("ABCDEFGH")   # A=가장 무거움 … H=가장 가벼움
    out = crud_conftest._roundrobin_blocks_for_workers(ordered, 3)
    assert out == ["A", "D", "G", "B", "E", "H", "C", "F"]  # 버킷 [A,D,G][B,E,H][C,F] 연접
    assert sorted(out) == sorted(ordered) and len(out) == 8


def _triple(lid, methods):
    return (lid, lid, {"id": lid, "steps": [{"method": m, "path": "/x"} for m in methods]})


def test_order_for_load_floats_readonly_to_global_pending(monkeypatch):
    """활성 경로 (run-19a5 오너 설계): read-only(전부 GET)는 heavy 뒤 strand가
    아니라 global pending 앞으로 → 빈 워커가 초반에 집는다. heavy는 pair-first
    (t=0) 유지, strand는 non-read light로 채운다."""
    monkeypatch.setattr(crud_conftest, "_has_prereq", lambda lid: False)
    # LPT desc: heavy(POST) → non-read light(POST) → read-only(GET, 가장 가벼움)
    triples = ([_triple(f"H{i}", ["POST", "GET"]) for i in range(3)]
               + [_triple(f"L{i}", ["POST"]) for i in range(3)]
               + [_triple(f"R{i}", ["GET"]) for i in range(3)])
    out = crud_conftest._order_for_load(triples, 3)
    assert sorted(out) == sorted(t[0] for t in triples)            # 멤버십 보존
    assert all(out[2 * i].startswith("H") for i in range(3))       # pair-first = heavy (t=0)
    assert all(out[2 * i + 1].startswith("L") for i in range(3))   # strand = non-read (read-only 아님)
    ro_pos = [i for i, x in enumerate(out) if x.startswith("R")]
    assert min(ro_pos) >= 2 * 3, f"read-only가 초반 strand에 묶임: {out}"


def test_order_for_load_dependents_go_last(monkeypatch):
    """dependent(prereq)는 global pending 후미 — provider가 도는 뒤에 디스패치되어
    dequeue 시점엔 provider가 준비돼 있을 확률이 높다 (dependency 순서를 수집
    순서에 인코딩; xdist는 dequeue 시 dependency를 못 본다)."""
    monkeypatch.setattr(crud_conftest, "_has_prereq", lambda lid: lid.startswith("D"))
    triples = ([_triple(f"H{i}", ["POST", "GET"]) for i in range(2)]
               + [_triple(f"L{i}", ["POST"]) for i in range(2)]
               + [_triple("Rno", ["GET"])]      # no-dep read-only
               + [_triple("Dep", ["GET"])])     # 같은 read-only지만 prereq 있음
    out = crud_conftest._order_for_load(triples, 2)
    assert out.index("Dep") > out.index("Rno")      # dependent가 no-dep 뒤


def test_order_for_load_pool_is_lpt_longest_first(monkeypatch):
    """빈 슬롯엔 예상 시간 긴 놈 먼저(오너 2026-07-13): global pending은 LPT-desc라
    non-read 미디엄이 read-only보다 앞. read-only는 strand에서 빠져 pool에 있으므로
    여전히 창 안에 뜬다(40분 지각 아님)."""
    monkeypatch.setattr(crud_conftest, "_has_prereq", lambda lid: False)
    # n=2 heavy; non-read 3개 중 2개는 strand filler, 1개(M0)는 pool에 남음; read 2개
    triples = ([_triple(f"H{i}", ["POST", "GET"]) for i in range(2)]     # heaviest
               + [_triple(f"M{i}", ["POST"]) for i in range(3)]           # non-read 미디엄
               + [_triple(f"R{i}", ["GET"]) for i in range(2)])           # read-only 최경량
    out = crud_conftest._order_for_load(triples, 2)
    pool = out[2 * 2:]                                       # global pending 영역
    m_pos = min(i for i, x in enumerate(pool) if x.startswith("M"))
    r_pos = min(i for i, x in enumerate(pool) if x.startswith("R"))
    assert m_pos < r_pos, f"pool이 LPT-desc 아님(미디엄이 read 뒤): {pool}"
    # read-only는 strand(pair-second)에 없다
    assert not any(out[2 * i + 1].startswith("R") for i in range(2)), \
        f"read-only가 strand에 묶임: {out}"


def test_order_for_load_noop_when_few_items():
    tr = [("A", "A", {}), ("B", "B", {})]
    assert crud_conftest._order_for_load(tr, 4) == ["A", "B"]
    assert crud_conftest._order_for_load(tr, 0) == ["A", "B"]


def test_roundrobin_blocks_noop_when_few_items_or_serial():
    assert crud_conftest._roundrobin_blocks_for_workers(["A", "B"], 0) == ["A", "B"]
    assert crud_conftest._roundrobin_blocks_for_workers(["A", "B"], 1) == ["A", "B"]
    assert crud_conftest._roundrobin_blocks_for_workers(["A", "B"], 4) == ["A", "B"]


def test_priority_first_pins_read_from_dependencies():
    """priority_first(오너 2026-07-13)는 dependencies.json이 원천 — conftest와
    schedule_optimizer가 같은 목록을 읽는다."""
    from regression.scenarios.schedule_optimizer import load_priority_first
    pins = crud_conftest._priority_first()
    assert "vpc-subnet-vip-nat" in pins and "networking-vpc-subnet" in pins
    assert pins == load_priority_first()


def test_simulate_schedule_pins_priority_first_at_t0():
    """예측 Gantt(simulate_schedule)도 핀을 t=0에 배치 — 실행 경로(conftest
    수집 정렬)와 규칙이 갈리면 예측/실측 비교(schedule_verdict)가 무의미해진다.
    회귀 근거: 2026-07-13 런에서 vpc-subnet-vip-nat ~28분, networking-vpc-subnet
    ~40분 시작(LPT 후순위 + 슬롯 대기)으로 런 꼬리가 됐다."""
    from regression.scenarios.local_run import simulate_schedule
    # net-VPC A/B 설계(2026-07-13) 이후: peering·vip-nat은 vpc#a/b adopt라
    # 슬롯 0 — 슬롯 소비자는 networking-vpc-subnet(핀)과 heavy-shared-networking.
    # 선택에 vpc#a/b adopter가 있어 유효 슬롯 = max(1, 2-2) = 1: 핀인 nvs가
    # 그 하나를 선점하고, 비핀 hsn은 nvs 종료 후에야 시작해야 한다.
    ids = ["vpc-peering", "heavy-shared-networking",
           "vpc-subnet-vip-nat", "networking-vpc-subnet"]
    sim = simulate_schedule(ids, workers=4, vpc_slots=2)
    byid = {b["id"]: b for b in sim["bars"]}
    # 사전작업(shared-infra prework) 도입(2026-07-15) 후 첫 웨이브 = prework
    # 종료 시각 — 핀 규칙은 '첫 웨이브 선두'라는 상대 순서다.
    t0 = byid["shared-infra"]["e"] if "shared-infra" in byid else 0.0
    assert byid["vpc-subnet-vip-nat"]["s"] == t0           # 핀 (슬롯 0)
    assert byid["networking-vpc-subnet"]["s"] == t0        # 핀 (슬롯 1 선점)
    assert byid["vpc-peering"]["s"] == t0                   # vpc#a/b adopt — 슬롯 0
    assert byid["heavy-shared-networking"]["s"] >= byid["networking-vpc-subnet"]["e"]


def test_class_default_replaces_zero_for_unmeasured():
    # cluster-grade lifecycle(무거운 create 포함)은 0.0이 아니라 클래스 기본값
    lc = {"id": "postgresql-cluster-subops-full", "service": "database/postgresql",
          "heavy": True,
          "steps": [{"name": "create-cluster", "method": "POST",
                     "path": "/v1/clusters", "expect_status": [202]}]}
    v = crud_conftest._class_default_s(lc)
    assert v >= 1000.0, f"cluster-grade default expected, got {v}"


def test_learning_gate_requires_live_run_markers(monkeypatch, tmp_path):
    """offline/mock pytest 실행이 durations.json을 오염시키지 않는다 —
    APITEST_RUN_ID/SCP_CONSOLE_EVENTS 없으면 fold가 호출되지 않는다."""
    monkeypatch.setattr(crud_conftest, "_DUR_LOCAL", tmp_path / "durations.local.json")
    monkeypatch.delenv("APITEST_RUN_ID", raising=False)
    monkeypatch.delenv("SCP_CONSOLE_EVENTS", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    crud_conftest._MEASURED.clear()
    crud_conftest._MEASURED["x"] = 12.3
    called = []
    import regression.scenarios.schedule_optimizer as so
    monkeypatch.setattr(so, "update_durations", lambda *a, **k: called.append(1))
    crud_conftest.pytest_sessionfinish(None, 0)
    assert not called, "live 마커 없이 fold가 호출되면 안 된다"
    # live 마커가 있으면 fold
    monkeypatch.setenv("APITEST_RUN_ID", "test-run")
    crud_conftest.pytest_sessionfinish(None, 0)
    assert called, "live 마커가 있으면 fold되어야 한다"
    crud_conftest._MEASURED.clear()


def test_durations_reader_merges_local_overlay(tmp_path, monkeypatch):
    """학습은 로컬 오버레이(durations.local.json)에 쓰고, 읽기는 커밋본+오버레이
    병합(오버레이 우선) — 커밋본을 더럽혀 git pull과 충돌하던 결함의 회귀 방지
    (2026-07-11)."""
    import json
    import tests.crud.conftest as cf

    committed = tmp_path / "durations.json"
    local = tmp_path / "durations.local.json"
    committed.write_text(json.dumps({"a": {"avg_s": 100.0}, "b": {"avg_s": 50.0}}))
    local.write_text(json.dumps({"b": {"avg_s": 999.0}, "c": {"avg_s": 5.0}}))
    monkeypatch.setattr(cf, "_DUR_PATH", committed)
    monkeypatch.setattr(cf, "_DUR_LOCAL", local)
    d = cf._durations()
    assert d == {"a": 100.0, "b": 999.0, "c": 5.0}


def test_durations_merge_takes_max_against_downward_pollution(tmp_path, monkeypatch):
    """병합 = max (2026-07-13 run-c373): 오버레이의 하향 오염(옛 fast-fail 학습)이
    커밋본의 실측 큰 값을 가리면 몬스터가 경량 오분류돼 런 꼬리가 된다 —
    pg-cluster(커밋 1450s)가 오염 오버레이로 +42분 지각한 실측의 회귀 고정."""
    import json
    import tests.crud.conftest as cf

    committed = tmp_path / "durations.json"
    local = tmp_path / "durations.local.json"
    committed.write_text(json.dumps({"pg": {"avg_s": 1450.0}}))
    local.write_text(json.dumps({"pg": {"avg_s": 12.0}}))   # 오염된 작은 값
    monkeypatch.setattr(cf, "_DUR_PATH", committed)
    monkeypatch.setattr(cf, "_DUR_LOCAL", local)
    assert cf._durations() == {"pg": 1450.0}


def test_sessionfinish_folds_into_local_overlay_not_committed(tmp_path, monkeypatch):
    import json
    import tests.crud.conftest as cf

    committed = tmp_path / "durations.json"
    local = tmp_path / "durations.local.json"
    committed.write_text(json.dumps({"x": {"avg_s": 10.0, "n": 1, "last_s": 10.0}}))
    monkeypatch.setattr(cf, "_DUR_PATH", committed)
    monkeypatch.setattr(cf, "_DUR_LOCAL", local)
    monkeypatch.setattr(cf, "_MEASURED", {"x": 30.0}, raising=False)
    monkeypatch.setenv("SCP_CONSOLE_EVENTS", "/tmp/x.jsonl")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    before = committed.read_text()
    cf.pytest_sessionfinish(None, 0)
    assert committed.read_text() == before          # 커밋본 불변
    folded = json.loads(local.read_text())
    # 시딩 카피 없음 (2026-07-11): 오버레이는 이 머신의 실측만 담는다 —
    # 옛 커밋본을 시드하면 커밋본 재구축을 낡은 오버레이가 가려버린다.
    assert folded["x"]["n"] == 1 and folded["x"]["last_s"] == 30.0
    assert list(folded) == ["x"]


def test_simulate_schedule_prepends_shared_infra_prework(monkeypatch):
    """오너 2026-07-15: '사전 VPC/subnet 작업(~5분)이 예측에 없어 예측 vs 실제
    비교가 안 됨 — 사전작업도 예측에 넣자.' adopter가 있는 선택은 t=0에
    shared-infra 고스트 행이 서고 모든 시나리오 예측이 그 뒤로 밀린다;
    adopter가 없는(self-create 전용) 선택은 prework 없이 t=0 시작."""
    from regression.scenarios.local_run import simulate_schedule

    # adopter 있는 선택 — vpc-subnet-vip-nat은 vpc#a adopt
    sim = simulate_schedule(["vpc-subnet-vip-nat"], workers=2, vpc_slots=2)
    byid = {b["id"]: b for b in sim["bars"]}
    assert "shared-infra" in byid, sorted(byid)
    pre = byid["shared-infra"]
    assert pre["s"] == 0.0 and pre["e"] > 0 and pre.get("prework")
    assert byid["vpc-subnet-vip-nat"]["s"] >= pre["e"], \
        "시나리오 예측은 사전작업 종료 이후에 시작해야"
    assert sim["makespan_s"] >= pre["e"]

    # adopter 없는 선택 — networking-vpc-subnet은 adopt 마커 0 (self-create)
    sim2 = simulate_schedule(["networking-vpc-subnet"], workers=2, vpc_slots=2)
    ids2 = {b["id"] for b in sim2["bars"]}
    assert "shared-infra" not in ids2
    assert min(b["s"] for b in sim2["bars"]) == 0.0

    # env로 예측 시간 강제 가능
    monkeypatch.setenv("SCP_SIM_PREWORK_S", "600")
    sim3 = simulate_schedule(["vpc-subnet-vip-nat"], workers=2, vpc_slots=2)
    pre3 = next(b for b in sim3["bars"] if b["id"] == "shared-infra")
    assert pre3["e"] == 600.0
