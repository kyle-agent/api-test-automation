"""json_b64_fields — 토큰 치환 후 필드 base64 인코딩 (PF-36, 2026-07-10).

IAM createiamuser가 평문 password를 400 'Password must be encoded base64'로
거부(문서 미기재). 인코딩은 {ualpha} 치환 이후여야 하므로 엔진 단계에서 수행.
"""
from __future__ import annotations

import base64

from regression.scenarios.engine import _apply_b64_fields


def test_encodes_listed_string_fields_only():
    step = {"json_b64_fields": ["password"]}
    body = {"password": "Regr9x!abc", "user_name": "regrusr"}
    out = _apply_b64_fields(step, body)
    assert out["password"] == base64.b64encode(b"Regr9x!abc").decode()
    assert out["user_name"] == "regrusr"


def test_noop_without_key_or_on_non_dict():
    assert _apply_b64_fields({}, {"a": 1}) == {"a": 1}
    assert _apply_b64_fields({"json_b64_fields": ["x"]}, None) is None
    # 필드 부재/비문자열은 그대로
    assert _apply_b64_fields({"json_b64_fields": ["x"]}, {"x": 5}) == {"x": 5}
