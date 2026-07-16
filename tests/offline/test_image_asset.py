"""상비 qcow2 이미지 자산 오프라인 검증 (오너 2026-07-15: "테스트용 이미지는
git에 넣어두고, 이것도 최초 1회는 obj 만들고 넣는 걸로").

- git 상비 원본(assets/regr-minimal.qcow2)의 qcow2 v3 구조 무결성
- oplog.ensure_image_asset(): 없으면 업로드(public-read 폴백), tenant-path
  URL(account_id는 env 우선 → bucket ACL Owner 유도), 프로세스 캐시
- shared_infra._needs_image_asset(): {env:SCP_QCOW2_ASSET_URL} 토큰 탐지
- 라이프사이클에 구 계정 하드코딩 URL이 남아있지 않음
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core import oplog
from regression.scenarios import shared_infra

ROOT = Path(__file__).resolve().parent.parent.parent
ASSET = ROOT / "assets" / "regr-minimal.qcow2"


def test_asset_is_valid_minimal_qcow2_v3():
    data = ASSET.read_bytes()
    assert len(data) == 262144, "수제 자산은 4×64KiB = 262,144B"
    magic, version = struct.unpack(">4sI", data[:8])
    assert magic == b"QFI\xfb" and version == 3
    cluster_bits, = struct.unpack(">I", data[20:24])
    assert cluster_bits == 16, "64KiB 클러스터"
    l1_off, = struct.unpack(">Q", data[40:48])
    rct_off, = struct.unpack(">Q", data[48:56])
    assert rct_off == 65536 and l1_off == 3 * 65536
    # refcount block: 할당된 4개 클러스터 refcount=1
    rcb_off, = struct.unpack(">Q", data[65536:65544])
    assert rcb_off == 2 * 65536
    counts = struct.unpack(">4H", data[rcb_off:rcb_off + 8])
    assert counts == (1, 1, 1, 1)


class _FakeS3:
    def __init__(self, have_object=False, owner="acct123"):
        self.have_object = have_object
        self.owner = owner
        self.puts = []

    def head_bucket(self, Bucket):
        return {}

    def head_object(self, Bucket, Key):
        if not self.have_object:
            raise RuntimeError("404")

    def put_object(self, **kw):
        self.puts.append(kw)
        self.have_object = True

    def get_bucket_acl(self, Bucket):
        return {"Owner": {"ID": self.owner}}


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(oplog, "_IMAGE_ASSET_URL", [None])
    monkeypatch.delenv("SCP_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("SCP_QCOW2_ASSET_URL", raising=False)
    yield


def _wire(monkeypatch, fake):
    cfg = {"bucket": "apitest-oplog-permanent",
           "endpoint": "https://object-store.kr-west1.e.samsungsdscloud.com"}
    seen_keys = []

    def _fake_client(keys="oplog"):
        seen_keys.append(keys)
        return fake, cfg

    monkeypatch.setattr(oplog, "_client", _fake_client)
    return seen_keys


def test_ensure_image_asset_uploads_when_missing_and_builds_url(monkeypatch):
    fake = _FakeS3(have_object=False, owner="newacct$newacct")
    seen_keys = _wire(monkeypatch, fake)
    url = oplog.ensure_image_asset()
    # 2026-07-16 라이브 확정: createimage는 호출자 자신의 계정 버킷 URL만
    # 통과 — SCP_OPLOG_* 오버라이드(타 계정)를 절대 따라가지 않도록
    # keys="test" 고정 (run a690 400 InvalidObjectStorageUrl의 근본 원인).
    assert seen_keys == ["test"]
    assert fake.puts and fake.puts[0]["Key"] == oplog.IMAGE_ASSET_KEY
    assert fake.puts[0].get("ACL") == "public-read"
    assert url == ("https://object-store.kr-west1.e.samsungsdscloud.com/"
                   "newacct:apitest-oplog-permanent/assets/regr-minimal.qcow2")
    # 프로세스 캐시: 두 번째 호출은 S3 재호출 없음
    fake.puts.clear()
    assert oplog.ensure_image_asset() == url and not fake.puts


def test_ensure_image_asset_noop_when_object_exists(monkeypatch):
    fake = _FakeS3(have_object=True)
    _wire(monkeypatch, fake)
    url = oplog.ensure_image_asset()
    assert not fake.puts, "이미 있으면 업로드 없음 (멱등)"
    assert url and ":apitest-oplog-permanent/" in url


def test_ensure_image_asset_env_account_id_wins(monkeypatch):
    fake = _FakeS3(have_object=True, owner="acl-owner")
    _wire(monkeypatch, fake)
    monkeypatch.setenv("SCP_ACCOUNT_ID", "env-acct")
    url = oplog.ensure_image_asset()
    assert "/env-acct:" in url


def test_ensure_image_asset_disabled_oplog_returns_none(monkeypatch):
    monkeypatch.setattr(oplog, "_client", lambda keys="oplog": (None, None))
    assert oplog.ensure_image_asset() is None


def test_needs_image_asset_detection(monkeypatch):
    lc_with = {"id": "hv", "steps": [
        {"method": "POST", "path": "/v1/images",
         "json": {"url": "{env:SCP_QCOW2_ASSET_URL}"}}]}
    lc_without = {"id": "plain", "steps": [{"method": "GET", "path": "/x"}]}
    monkeypatch.delenv("SCP_CRUD_IDS", raising=False)
    monkeypatch.setattr(shared_infra.engine, "active_lifecycles",
                        lambda: [lc_with, lc_without])
    assert shared_infra._needs_image_asset() is True
    monkeypatch.setattr(shared_infra.engine, "active_lifecycles",
                        lambda: [lc_without])
    assert shared_infra._needs_image_asset() is False


def test_no_hardcoded_account_asset_urls_left():
    """구 계정 id가 박힌 자산 URL이 시나리오에 남아 있으면 신규 계정에서
    404 — 전부 {env:SCP_QCOW2_ASSET_URL} 토큰이어야 한다."""
    bad = []
    for f in (ROOT / "regression" / "scenarios" / "lifecycles").glob("*.json"):
        t = f.read_text()
        if "apitest-oplog-permanent/assets/" in t and "{env:" not in t:
            bad.append(f.name)
        # 하드코딩된 32-hex 계정 tenant-path가 남아있는지
        import re
        if re.search(r"/[0-9a-f]{32}:apitest-oplog-permanent/", t):
            bad.append(f.name)
    assert not bad, f"하드코딩 자산 URL 잔존: {bad}"
