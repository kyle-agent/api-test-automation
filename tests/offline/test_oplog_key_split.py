"""oplog 키 선택 원칙 (2026-07-15 오너 원칙 전환: 테스트 계정이 기본).

oplog 미러/자동수리 버킷(apitest-oplog-permanent)도 테스트 계정 키(SCP_*)가
기본값이고, SCP_OPLOG_*는 **둘 다** 설정됐을 때만 명시적 오버라이드로 동작
한다(레거시 분리 구성 하위호환; 한쪽만 설정 시 키쌍 갈림 방지를 위해 무시).
logsink 버킷(apitest-logsink)은 오버라이드와 무관하게 항상 테스트 키.
버킷이 없으면 최초 사용 시 1회 자동 ensure(멱등, best-effort — 실패해도
런을 죽이지 않는다). core/oplog.py `_cfg` docstring이 canonical."""
from __future__ import annotations

import pytest

from core import oplog


def _env(monkeypatch, override=True):
    monkeypatch.setenv("SCP_ACCESS_KEY", "test-account-key")
    monkeypatch.setenv("SCP_SECRET_KEY", "test-account-secret")
    if override:
        monkeypatch.setenv("SCP_OPLOG_ACCESS_KEY", "override-key")
        monkeypatch.setenv("SCP_OPLOG_SECRET_KEY", "override-secret")
    else:
        monkeypatch.delenv("SCP_OPLOG_ACCESS_KEY", raising=False)
        monkeypatch.delenv("SCP_OPLOG_SECRET_KEY", raising=False)


# ── 키 선택 우선순위 ─────────────────────────────────────────────────────────

def test_default_is_test_account_keys(monkeypatch):
    """기본 = 테스트 계정 키 (오버라이드 미설정 — 새 원칙의 표준 구성)."""
    _env(monkeypatch, override=False)
    cfg = oplog._cfg()
    assert cfg["access"] == "test-account-key"
    assert cfg["secret"] == "test-account-secret"


def test_oplog_override_when_both_set(monkeypatch):
    """SCP_OPLOG_* 두 키가 모두 설정되면 명시적 오버라이드 (레거시 하위호환)."""
    _env(monkeypatch)
    cfg = oplog._cfg()
    assert cfg["access"] == "override-key"
    assert cfg["secret"] == "override-secret"


def test_half_set_override_is_ignored(monkeypatch):
    """오버라이드 키가 한쪽만 설정되면 무시 — 키쌍이 갈라지면 서명 오류가
    되므로 통째로 테스트 키로 폴백해야 한다."""
    _env(monkeypatch, override=False)
    monkeypatch.setenv("SCP_OPLOG_ACCESS_KEY", "override-key")  # secret 없음
    cfg = oplog._cfg()
    assert cfg["access"] == "test-account-key"
    assert cfg["secret"] == "test-account-secret"


def test_test_keys_cfg_ignores_oplog_override(monkeypatch):
    """keys="test"(logsink 픽스처 경로)는 오버라이드가 있어도 항상 테스트 키
    — 다른 계정에 ensure되는 오배치 방지."""
    _env(monkeypatch)
    cfg = oplog._cfg(keys="test")
    assert cfg["access"] == "test-account-key"
    assert cfg["secret"] == "test-account-secret"


def test_test_keys_cfg_ignores_endpoint_pin(monkeypatch):
    """keys="test"는 SCP_OPLOG_S3_ENDPOINT 핀도 따라가지 않는다 — 키 가드와
    동일한 오배치 방지. 2026-07-29 타 오퍼링 실측(run 11f2): 미러용 구-계정
    endpoint 핀이 logsink ensure까지 끌고 가 "새 계정 키 × 구 계정 호스트"
    인증 실패 → 버킷 미생성 → network-logging create 400 storage-invalid-bucket.
    픽스처 endpoint는 항상 현재 SCP_REGION/ENV 합성 규약."""
    _env(monkeypatch)
    monkeypatch.setenv("SCP_OPLOG_S3_ENDPOINT",
                       "https://object-store.kr-west1.e.samsungsdscloud.com")
    monkeypatch.setenv("SCP_REGION", "kr-east1")
    monkeypatch.setenv("SCP_ENV", "x")
    cfg = oplog._cfg(keys="test")
    assert cfg["endpoint"] == "https://object-store.kr-east1.x.samsungsdscloud.com"
    # 미러("oplog") 모드는 핀을 그대로 따른다 (기존 시맨틱 무회귀)
    cfg2 = oplog._cfg()
    assert cfg2["endpoint"] == "https://object-store.kr-west1.e.samsungsdscloud.com"


# ── oplog 버킷 auto-ensure (멱등, 최초 1회, best-effort) ─────────────────────

class _FakeClient:
    def __init__(self, exists=True):
        self.exists = exists
        self.head_calls = 0

    def head_bucket(self, Bucket):
        self.head_calls += 1
        if not self.exists:
            raise RuntimeError("404 NoSuchBucket")


def test_ensure_once_noop_when_bucket_exists(monkeypatch):
    """버킷이 있으면 head 1회 후 무음 no-op — 두 번째 호출은 head도 안 한다
    (프로세스당 1회 멱등)."""
    monkeypatch.setattr(oplog, "_OPLOG_ENSURED", [False])
    created = []
    monkeypatch.setattr(oplog, "ensure_bucket",
                        lambda: created.append(1) or True)
    c = _FakeClient(exists=True)
    cfg = {"bucket": "apitest-oplog-permanent"}
    oplog._ensure_oplog_once(c, cfg)
    oplog._ensure_oplog_once(c, cfg)
    assert c.head_calls == 1
    assert created == []


def test_ensure_once_creates_when_missing(monkeypatch):
    """버킷이 없으면 최초 1회만 ensure_bucket()(create+CORS+ACL) 경로 —
    재호출은 no-op."""
    monkeypatch.setattr(oplog, "_OPLOG_ENSURED", [False])
    created = []
    monkeypatch.setattr(oplog, "ensure_bucket",
                        lambda: created.append(1) or True)
    c = _FakeClient(exists=False)
    cfg = {"bucket": "apitest-oplog-permanent"}
    oplog._ensure_oplog_once(c, cfg)
    oplog._ensure_oplog_once(c, cfg)
    assert created == [1]


def test_ensure_once_swallows_failure(monkeypatch):
    """ensure 실패는 삼킨다 — 깨진 oplog가 런을 죽이면 안 된다 (logsink 규약)."""
    monkeypatch.setattr(oplog, "_OPLOG_ENSURED", [False])

    def _boom():
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(oplog, "ensure_bucket", _boom)
    oplog._ensure_oplog_once(_FakeClient(exists=False),
                             {"bucket": "apitest-oplog-permanent"})  # no raise


def test_client_wires_ensure_for_oplog_keys_only(monkeypatch):
    """_client(keys="oplog")가 auto-ensure를 배선 (emit/put_text/snapshot 미러
    업로드 등 모든 최초-사용 경로 커버); keys="test"(logsink)는 비적용."""
    pytest.importorskip("boto3")
    _env(monkeypatch, override=False)
    calls = []
    monkeypatch.setattr(oplog, "_ensure_oplog_once",
                        lambda c, cfg: calls.append(cfg["bucket"]))
    c, cfg = oplog._client()
    assert c is not None
    assert calls == [cfg["bucket"]]
    calls.clear()
    c2, _ = oplog._client(keys="test")
    assert c2 is not None
    assert calls == []


# ── logsink 자동 부트스트랩 감지 (기존 시맨틱 무회귀) ────────────────────────

def test_needs_logsink_detection(monkeypatch):
    """선택에 apitest-logsink 참조 스텝(gen-wave4-nlog)이 있으면 True — provision
    이 테스트 키로 자동 ensure(새 계정 자기충족). 없으면 False(불필요한 S3 호출
    없음)."""
    from regression.scenarios import shared_infra
    monkeypatch.setenv("SCP_CRUD_IDS", "gen-wave4-nlog")
    assert shared_infra._needs_logsink() is True
    monkeypatch.setenv("SCP_CRUD_IDS", "iam-role-full")
    assert shared_infra._needs_logsink() is False
