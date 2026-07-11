"""Offline tests for the worker-aware CRUD collection ordering (A1/A3, 2026-07-10)
and the duration-learning gate (A2) — run-85b2/377e 스케줄 분석의 회귀 고정.

근거: 순수 duration 내림차순은 xdist 초기 연속-청크 배정에서 최상위 무거운
2개를 같은 워커에 직렬화시켰다 (mysql 종료 0.2s 뒤 postgresql 시작 — 그
postgresql이 run-377e makespan 결정). 인터리브는 [heavy, light] 페어로 긴
작업들을 서로 다른 워커에 t≈0 배정한다.
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


def test_class_default_replaces_zero_for_unmeasured():
    # cluster-grade lifecycle(무거운 create 포함)은 0.0이 아니라 클래스 기본값
    lc = {"id": "postgresql-cluster-subops-full", "service": "database/postgresql",
          "heavy": True,
          "steps": [{"name": "create-cluster", "method": "POST",
                     "path": "/v1/clusters", "expect_status": [202]}]}
    v = crud_conftest._class_default_s(lc)
    assert v >= 1000.0, f"cluster-grade default expected, got {v}"


def test_learning_gate_requires_live_run_markers(monkeypatch):
    """offline/mock pytest 실행이 durations.json을 오염시키지 않는다 —
    APITEST_RUN_ID/SCP_CONSOLE_EVENTS 없으면 fold가 호출되지 않는다."""
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
    assert folded["x"]["n"] == 2 and folded["x"]["last_s"] == 30.0
