"""oplog 자격 분리 (2026-07-15 테스트 계정 교체, 오너 (b) 결정).

미러 버킷(apitest-oplog-permanent)은 구 계정 키(SCP_OPLOG_*)로 히스토리
연속성을 보존하고, logsink 버킷(apitest-logsink)은 시나리오가 테스트 계정
안에서 참조하는 픽스처라 항상 테스트 키(SCP_*)로 ensure돼야 한다."""
from __future__ import annotations

from core import oplog


def _env(monkeypatch):
    monkeypatch.setenv("SCP_ACCESS_KEY", "new-test-key")
    monkeypatch.setenv("SCP_SECRET_KEY", "new-test-secret")
    monkeypatch.setenv("SCP_OPLOG_ACCESS_KEY", "old-oplog-key")
    monkeypatch.setenv("SCP_OPLOG_SECRET_KEY", "old-oplog-secret")


def test_default_cfg_prefers_oplog_override(monkeypatch):
    _env(monkeypatch)
    cfg = oplog._cfg()
    assert cfg["access"] == "old-oplog-key", "미러 버킷 = 구 계정 키(연속성)"


def test_test_keys_cfg_ignores_oplog_override(monkeypatch):
    _env(monkeypatch)
    cfg = oplog._cfg(keys="test")
    assert cfg["access"] == "new-test-key", \
        "logsink = 테스트 키 (구 계정 오배치 방지)"


def test_fallback_when_no_override(monkeypatch):
    monkeypatch.setenv("SCP_ACCESS_KEY", "only-key")
    monkeypatch.setenv("SCP_SECRET_KEY", "only-secret")
    monkeypatch.delenv("SCP_OPLOG_ACCESS_KEY", raising=False)
    monkeypatch.delenv("SCP_OPLOG_SECRET_KEY", raising=False)
    assert oplog._cfg()["access"] == "only-key", "단일 계정 구성 폴백 무회귀"
