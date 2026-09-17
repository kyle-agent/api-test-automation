"""Pre-flight availability-zone guard — 실행 전 `{zone}` 토큰 실측 검증.

계기 (run 20260917-085140-89de, 2026-09-17): 오너 `.env`에 8/1 s2 오퍼링
캠페인 레시피의 `SCP_ZONE=kr-west1-a` 핀이 남아 있었고, 계정 존 구성이 바뀌어
`-a`가 더 이상 유효하지 않게 되자 zone을 바디에 싣는 lifecycle 20여 개가 첫
create에서 3~30초 만에 전멸했다(볼륨·서버·publicip·ASG·복제). 엔진 기본값은
이미 kr-west1→`-b`라 코드 결함이 아니라 **env 핀 stale** 클래스 — 런을
시작하기 전에 잡아야 하는 문제다.

원리 — 읽기 전용 프로브 하나로 판정한다:
  ``GET filestorage /v1/replications/zones?type_name=HDD&source_zone=<zone>
  &replication_type=replication`` 은 source 존이 계정에 유효하면 복제 대상
  존 목록(예: kr-west1-b → ['kr-east1-a'])을, 유효하지 않은 존이면 **빈
  목록**을 돌려준다(2026-07-15 · 2026-09-17 두 차례 실측; 카탈로그에 존을
  열거하는 다른 read-only 엔드포인트는 없다 — hosted-zone/landing-zone은 무관).
  빈 목록만으로는 "복제 상품이 없는 계정"과 구분이 안 되므로, **형제 존
  (`{zone_alt}`, -a↔-b)이 비어 있지 않을 때만** '핀이 틀렸다'로 확정한다.

판정(verdict):
  * ``ok``      — `{zone}` 프로브가 비어 있지 않음.
  * ``invalid`` — `{zone}`은 비고 `{zone_alt}`는 차 있음 → 존 핀이 틀린 것.
                  기본 모드(enforce)에서는 실행을 **차단**한다.
  * ``unknown`` — 둘 다 비었거나 프로브 자체가 실패(4xx/5xx/전송 오류/자격
                  없음) → 경고만, 차단하지 않는다(가드가 런을 죽이면 안 됨).

모드 (``SCP_ZONE_CHECK``): ``true``(기본, invalid면 차단) · ``warn``(경고만)
· ``false``(프로브 자체를 생략). 존 핀 자체는 여전히 ``SCP_ZONE`` /
``SCP_ZONE_ALT``(engine._default_zone / _alt_zone)가 지배한다.

훅 지점: ① `shared_infra.provision()` 진입부(콘솔2·CI 공통, mutations ON일
때만) ② 콘솔2 `_preflight` (confirm 모달에 verdict 표시, invalid면 실행 차단)
③ 콘솔2 REAL 런 시작 직전(pre-flight 이후 env가 바뀐 경우의 최종 방어선).

CLI: ``python -m regression.scenarios.zone_guard`` → JSON 1줄, exit 0(ok/
unknown/warn) · 2(invalid & enforce).
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

PROBE_SERVICE = "filestorage"
PROBE_PATH = "/v1/replications/zones"
_ZONE_RE = re.compile(r"[a-z]{2}-[a-z]+\d+-[a-z]")


class ZoneGuardError(RuntimeError):
    """Raised (enforce mode) when the pinned zone is provably wrong."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(result.get("detail", "zone guard: invalid zone"))


def mode() -> str:
    """``enforce`` (기본) · ``warn`` · ``off`` — from SCP_ZONE_CHECK."""
    raw = os.environ.get("SCP_ZONE_CHECK", "").strip().lower()
    if raw in ("false", "0", "no", "off"):
        return "off"
    if raw == "warn":
        return "warn"
    return "enforce"


def resolve(region: str | None = None) -> dict:
    """Resolve the zone pair the engine will inject, and where it came from."""
    from regression.scenarios import engine
    region = (region or os.environ.get("SCP_REGION", "kr-west1")).strip() or "kr-west1"
    zone = engine._default_zone(region)
    alt = engine._alt_zone(zone)
    source = "env SCP_ZONE" if os.environ.get("SCP_ZONE", "").strip() else f"default({region})"
    return {"region": region, "zone": zone, "zone_alt": alt, "source": source}


def _zones_of(body: Any) -> list[str] | None:
    """Pull the zone list out of the probe body. ``None`` = unparseable."""
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body)
        except Exception:  # noqa: BLE001 — treat as unparseable
            return None
    if not isinstance(body, dict):
        return None
    z = body.get("zones")
    if isinstance(z, list):
        return [str(x) for x in z]
    if isinstance(z, str):          # docs example renders it as "['kr-west1']"
        return _ZONE_RE.findall(z)
    return None


def probe(client, zone: str, *, timeout: float = 15) -> list[str] | None:
    """Read-only probe. Returns the replication-target zone list for ``zone``,
    or ``None`` when the call failed/was unparseable (never raises)."""
    try:
        r = client.get(PROBE_PATH, service=PROBE_SERVICE,
                       params={"type_name": "HDD", "source_zone": zone,
                               "replication_type": "replication"},
                       timeout=timeout, retry=False)
    except Exception:  # noqa: BLE001 — a guard must never take the run down
        return None
    status = getattr(r, "status", getattr(r, "status_code", 0))
    if not (200 <= int(status or 0) < 300):
        return None
    body = getattr(r, "body", None)
    if body is None:
        body = getattr(r, "raw_text", None)
    return _zones_of(body)


def check(client=None, cfg=None, *, probe_fn: Callable | None = None) -> dict:
    """Run the guard and return the verdict dict (never raises).

    ``client``/``cfg`` default to the live settings client; ``probe_fn`` lets
    tests inject a fake ``(client, zone) -> list|None``.
    """
    m = mode()
    res = resolve(getattr(cfg, "region", None) if cfg is not None else None)
    res["mode"] = m
    if m == "off":
        res.update(verdict="skipped", detail="SCP_ZONE_CHECK=false — 존 프로브 생략")
        return res
    if client is None and probe_fn is None:
        try:
            from core.config import settings as _settings
            from core.http_client import ApiClient
            _settings.require_credentials()
            client = ApiClient(_settings)
        except Exception as exc:  # noqa: BLE001 — no creds = cannot judge
            res.update(verdict="unknown", targets=None, alt_targets=None,
                       detail=f"프로브 불가(클라이언트 생성 실패: {type(exc).__name__}) — 검증 생략")
            return res
    _probe = probe_fn or probe
    targets = _probe(client, res["zone"])
    res["targets"] = targets
    if targets:
        res.update(verdict="ok", alt_targets=None,
                   detail=f"{res['zone']} 유효 (복제 대상 존 {targets})")
        return res
    alt_targets = _probe(client, res["zone_alt"])
    res["alt_targets"] = alt_targets
    if targets is not None and alt_targets:
        res.update(
            verdict="invalid",
            detail=(f"존 핀 '{res['zone']}'({res['source']})은 이 계정에서 유효하지 않고 "
                    f"형제 존 '{res['zone_alt']}'는 유효합니다(복제 대상 {alt_targets}). "
                    f"SCP_ZONE 을 '{res['zone_alt']}' 로 바꾸거나 핀을 지우세요 "
                    f"(무시: SCP_ZONE_CHECK=warn)"))
        return res
    why = ("프로브 실패(전송/4xx/5xx)" if targets is None
           else f"{res['zone']}·{res['zone_alt']} 둘 다 복제 대상 없음(복제 미제공 계정?)")
    res.update(verdict="unknown", detail=f"존 검증 불가 — {why}; 실행은 계속합니다")
    return res


def enforce(client=None, cfg=None, *, log: Callable[[str], Any] = print,
            probe_fn: Callable | None = None) -> dict:
    """``check`` + 로그 한 줄; enforce 모드에서 invalid면 ZoneGuardError."""
    res = check(client, cfg, probe_fn=probe_fn)
    tag = {"ok": "OK", "invalid": "BLOCK" if res["mode"] == "enforce" else "WARN",
           "unknown": "WARN", "skipped": "SKIP"}[res["verdict"]]
    log(f"[zone-guard] {tag} zone={res['zone']} ({res['source']}) alt={res['zone_alt']} "
        f"— {res['detail']}")
    if res["verdict"] == "invalid" and res["mode"] == "enforce":
        raise ZoneGuardError(res)
    return res


def main(argv=None) -> int:
    try:
        res = enforce(log=lambda s: print(s, file=sys.stderr))
    except ZoneGuardError as exc:
        print(json.dumps(exc.result, ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
