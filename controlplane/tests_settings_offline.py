"""Offline tests for the 설정(⚙) screen (controlplane/settings_routes.py).

No network, no real credentials, and — 중요 — **실제 리포 .env 는 절대 읽지
않는다**: PLATFORM_ENV_FILE 을 임시 디렉토리의 가짜 .env 로 고정한 뒤 app 을
import 한다. Rerunnable any time from the repo root:

    PYTHONPATH=. python3 controlplane/tests_settings_offline.py
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import traceback
from pathlib import Path

# fresh throwaway DB + fake .env, BEFORE the app import (tests_offline.py 패턴)
_TMP = Path(tempfile.mkdtemp(prefix="platform-settings-test-"))
os.environ["PLATFORM_DB"] = str(_TMP / "platform.db")
FAKE_ENV = _TMP / ".env"
os.environ["PLATFORM_ENV_FILE"] = str(FAKE_ENV)

from fastapi.testclient import TestClient  # noqa: E402

from controlplane import settings_routes  # noqa: E402
from controlplane.app import app  # noqa: E402

client = TestClient(app)

ROOT = settings_routes.ROOT


def _seed(text: str) -> None:
    FAKE_ENV.write_text(text)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# --- 1. 폼 렌더 -------------------------------------------------------------------

def test_settings_page_renders():
    _seed("SCP_REGION=kr-west1\n")
    r = client.get("/settings")
    assert r.status_code == 200, r.status_code
    html = r.text
    for needle in ("환경 정보", "계정 정보", "SCP_REGION", "SCP_ACCESS_KEY",
                   "SCP_SECRET_KEY", "저장", "자격 검증"):
        assert needle in html, f"missing {needle!r}"
    # 반영 시점 안내 문구 (다음 런 + 서버 재기동)
    assert "다음 실행" in html and "재기동" in html
    # 현재 값 프리필 (비밀 아닌 키)
    assert 'value="kr-west1"' in html
    # 네비게이션에 설정 링크
    assert 'href="/settings"' in html


# --- 2. 저장 = 머지 (다른 키·주석 보존 / 권한 0600 / os.environ 반영) ----------------

def test_save_merges_preserves_and_chmods():
    _seed("# my comment stays\n"
          "OTHER_KEY=keep-me\n"
          "SCP_REGION=kr-old\n"
          "SCP_TIMEOUT=60\n")
    os.chmod(FAKE_ENV, 0o644)  # 저장이 0600 으로 조여야 한다
    r = client.post("/settings/save", data={
        "SCP_REGION": "kr-new1", "SCP_ENV": "e",
        "SCP_ACCESS_KEY": "AKTESTVALUE1234", "SCP_SECRET_KEY": "SKTESTVALUE5678",
    }, follow_redirects=False)
    assert r.status_code == 303, (r.status_code, r.text)
    loc = r.headers["location"]
    # 시크릿 값은 리다이렉트 URL 에 절대 실리지 않는다 (키 이름만)
    assert "SKTESTVALUE5678" not in loc and "AKTESTVALUE1234" not in loc
    assert "SCP_ACCESS_KEY" in loc
    text = FAKE_ENV.read_text()
    assert "# my comment stays" in text
    assert "OTHER_KEY=keep-me" in text
    assert "SCP_TIMEOUT=60" in text
    assert "SCP_REGION=kr-new1" in text and "kr-old" not in text
    assert text.count("SCP_REGION=") == 1
    # 파일에 없던 키는 관리 섹션으로 append
    assert "SCP_ACCESS_KEY=AKTESTVALUE1234" in text
    assert "SCP_SECRET_KEY=SKTESTVALUE5678" in text
    assert _mode(FAKE_ENV) == 0o600, oct(_mode(FAKE_ENV))
    # 다음 런(subprocess) 반영 경로: 이 프로세스 os.environ 갱신
    assert os.environ["SCP_REGION"] == "kr-new1"
    assert os.environ["SCP_SECRET_KEY"] == "SKTESTVALUE5678"


def test_save_creates_file_when_missing():
    if FAKE_ENV.exists():
        FAKE_ENV.unlink()
    r = client.post("/settings/save", data={"SCP_REGION": "kr-west1"})
    assert r.status_code == 200  # 303 followed
    assert FAKE_ENV.exists()
    assert "SCP_REGION=kr-west1" in FAKE_ENV.read_text()
    assert _mode(FAKE_ENV) == 0o600


def test_save_without_changes_is_noop():
    _seed("SCP_REGION=kr-west1\n")
    before = FAKE_ENV.read_text()
    r = client.post("/settings/save", data={"SCP_REGION": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "변경 없음" in r.headers["location"] or "%EB%B3%80%EA%B2%BD" in \
        r.headers["location"]  # urlencoded 한글
    assert FAKE_ENV.read_text() == before


# --- 3. 마스킹 (끝 4자리만, 전체 값은 HTML 에 없다) ---------------------------------

def test_secret_masking_last4_only():
    _seed("SCP_ACCESS_KEY=AKTESTVALUE1234\nSCP_SECRET_KEY=SKTESTVALUE5678\n")
    html = client.get("/settings").text
    assert "AKTESTVALUE1234" not in html
    assert "SKTESTVALUE5678" not in html
    assert "••••1234" in html and "••••5678" in html
    # 짧은 시크릿은 전부 가린다
    assert settings_routes.mask("abc") == "••••"
    assert settings_routes.mask("") == ""


def test_blank_or_mask_submit_preserves_secret():
    _seed("SCP_SECRET_KEY=SKTESTVALUE5678\n")
    # 빈 값 → 유지 (변경 없음), 마스크 표시값이 그대로 제출돼도 유지
    client.post("/settings/save", data={"SCP_SECRET_KEY": "",
                                        "SCP_REGION": "kr-west1"})
    assert "SCP_SECRET_KEY=SKTESTVALUE5678" in FAKE_ENV.read_text()
    client.post("/settings/save", data={"SCP_SECRET_KEY": "••••5678",
                                        "SCP_REGION": "kr-west2"})
    assert "SCP_SECRET_KEY=SKTESTVALUE5678" in FAKE_ENV.read_text()


# --- 4. 저장 전 검증 (주입/형식 오류는 400, 파일 불변) -------------------------------

def test_validation_rejects_bad_values():
    _seed("SCP_REGION=kr-west1\n")
    before = FAKE_ENV.read_text()
    # 줄바꿈 주입 (.env 라인 주입 방지)
    r = client.post("/settings/save",
                    data={"SCP_REGION": "kr-west1\nSCP_ALLOW_MUTATIONS=true"})
    assert r.status_code == 400, r.status_code
    # 코드/URL 성격 키의 공백
    assert client.post("/settings/save",
                       data={"SCP_ENV": "e 2"}).status_code == 400
    # SCP_SERVICE_HOSTS 는 JSON 객체만
    assert client.post("/settings/save",
                       data={"SCP_SERVICE_HOSTS": "{not json"}).status_code == 400
    assert client.post("/settings/save",
                       data={"SCP_SERVICE_HOSTS": '["list"]'}).status_code == 400
    # auth scheme 은 enum
    assert client.post("/settings/save",
                       data={"SCP_AUTH_SCHEME": "magic"}).status_code == 400
    assert FAKE_ENV.read_text() == before, "reject 시 파일은 불변이어야 한다"


# --- 5. gitignore 가드 -------------------------------------------------------------

def test_gitignore_guard():
    # 리포 루트 .env 는 .gitignore 에 있다 (Hard Rule 2) → 통과
    assert settings_routes.env_ignored(ROOT / ".env") is True
    # 리포 안의 임의 경로는 보호 안 됨 → False
    assert settings_routes.env_ignored(ROOT / "controlplane" / "x.env") is False
    # 리포 밖(임시 디렉토리)은 이 리포가 커밋할 수 없다 → 통과
    assert settings_routes.env_ignored(FAKE_ENV) is True
    # 보호 안 되는 리포 내 경로를 대상으로 하면 화면에 경고 배너
    old = os.environ["PLATFORM_ENV_FILE"]
    os.environ["PLATFORM_ENV_FILE"] = str(ROOT / "controlplane" / "x.env")
    try:
        html = client.get("/settings").text
        assert ".gitignore 로 보호되지 않습니다" in html
    finally:
        os.environ["PLATFORM_ENV_FILE"] = old
    assert ".gitignore 로 보호되지 않습니다" not in client.get("/settings").text


# --- 6. 자격 검증 (read-only) — 오프라인에서도 저장을 막지 않고 우아하게 실패 ---------

def test_verify_missing_creds_message():
    _seed("SCP_REGION=kr-west1\n")  # 자격 없음
    r = client.post("/settings/verify", data={"SCP_REGION": "kr-west1"})
    assert r.status_code == 200
    assert "자격 미입력" in r.text


def test_verify_offline_unreachable_is_graceful():
    _seed("SCP_REGION=kr-west1\n")
    # 127.0.0.1:9 (즉시 connection refused) 로 vpc 호스트를 강제 — 네트워크 불요
    r = client.post("/settings/verify", data={
        "SCP_REGION": "kr-west1",
        "SCP_SERVICE_HOSTS": '{"vpc": "http://127.0.0.1:9"}',
        "SCP_ACCESS_KEY": "AKX99999", "SCP_SECRET_KEY": "SKX99999"})
    assert r.status_code == 200
    assert "검증 실패" in r.text and "도달 실패" in r.text
    assert "저장은 가능" in r.text
    # 검증 결과에 시크릿 값이 새지 않는다
    assert "SKX99999" not in r.text
    # 검증은 저장이 아니다 — 파일 불변
    assert "SKX99999" not in FAKE_ENV.read_text()


def test_verify_invalid_input_reports_error():
    r = client.post("/settings/verify", data={"SCP_SERVICE_HOSTS": "{bad"})
    assert r.status_code == 200
    assert "검증 실패" in r.text and "SCP_SERVICE_HOSTS" in r.text


TESTS = [
    test_settings_page_renders,
    test_save_merges_preserves_and_chmods,
    test_save_creates_file_when_missing,
    test_save_without_changes_is_noop,
    test_secret_masking_last4_only,
    test_blank_or_mask_submit_preserves_secret,
    test_validation_rejects_bad_values,
    test_gitignore_guard,
    test_verify_missing_creds_message,
    test_verify_offline_unreachable_is_graceful,
    test_verify_invalid_input_reports_error,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed (env: {FAKE_ENV})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
