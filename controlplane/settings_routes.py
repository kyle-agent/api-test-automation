"""설정(⚙) — 환경/계정 정보를 화면에서 입력해 로컬 .env 를 갱신한다.

오너 지시 (2026-07-15): "누구에게 테스트하라고 전달해 줄 건데, 환경도 계정도
달라질 수 있으니 화면에서 환경정보와 계정 정보를 입력할 수 있게 하자."

설계 (Hard Rule 2 — no secrets in git/logs — 를 화면으로 확장):

* 대상 파일: 리포 루트의 ``.env`` (git-ignored). ``PLATFORM_ENV_FILE`` 로
  경로를 바꿀 수 있다 — 오프라인 테스트는 임시 디렉토리의 가짜 .env 를 쓴다.
* 저장 = **머지**: 기존 파일의 다른 키·주석·순서는 그대로 보존하고, 입력된
  키의 값 줄만 바꾼다(없으면 말미에 추가). 원자적 쓰기(tmp+rename) + 권한 0600.
* 시크릿(SCP_ACCESS_KEY/SECRET_KEY)은 표시 시 끝 4자리만(마스킹), 빈 값/마스크
  문자만 제출하면 **기존 값 유지**. 값은 서버 로그·리다이렉트 URL·오류 메시지
  어디에도 싣지 않는다(키 이름만).
* 반영 시점 (core.config 관찰): .env 는 core.config import 시 1회
  ``setdefault`` 로드되고 Settings 는 frozen 싱글턴이다. 라이브 런은
  ``{**os.environ, ...}`` 를 물려받는 subprocess 라, 저장 시 이 프로세스의
  os.environ 도 함께 갱신하면 **다음 런부터** 새 값이 적용된다. 이 서버
  프로세스 안의 화면(잔존 자원 스캔·단일 삭제·ctxbar env 표시)은 구 싱글턴을
  계속 보므로 재기동 후 반영 — 화면 안내 문구가 이를 그대로 설명한다.
* 검증(선택): 입력 키로 read-only GET 1개(vpc 목록)를 쳐서 자격 유효성만
  표시한다. 실패해도 저장은 가능(오프라인 작성 시나리오).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlplane import common

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter()

# core/config.py 의 기본값과 동일하게 유지 (검증 핑의 폴백용) — 값이 어긋나면
# 검증이 실제 런과 다른 호스트를 치게 되므로 config 쪽 변경 시 함께 갱신.
_DEF_HOST_TEMPLATE = "https://{service}.{region}.{env}.samsungsdscloud.com"
_DEF_GLOBAL_HOST_TEMPLATE = "https://{service}.{env}.samsungsdscloud.com"

# 화면이 관리하는 키 (core/config.py 가 실제 읽는 env 변수의 부분집합 —
# 환경(호스트를 가르는 축) + 계정(자격/스킴). 그 밖의 키는 .env 직접 편집).
FIELDS: list[dict] = [
    # --- 환경 정보 -----------------------------------------------------------
    dict(key="SCP_REGION", group="env", label="Region", secret=False,
         placeholder="kr-west1",
         help="리전 코드 — regional 서비스 호스트의 {region} 세그먼트"),
    dict(key="SCP_ENV", group="env", label="Env 코드", secret=False,
         placeholder="e",
         help="환경 코드 — 호스트의 {env} 세그먼트 (기본 e)"),
    dict(key="SCP_DR_REGION", group="env", label="DR Region", secret=False,
         placeholder="kr-east1",
         help="재해복구 상대 리전 — <service>-dr 호스트가 여기로 해석 (기본 kr-east1)"),
    dict(key="SCP_HOST_TEMPLATE", group="env", label="Regional 호스트 템플릿",
         secret=False, placeholder=_DEF_HOST_TEMPLATE,
         help="기본 패턴과 다를 때만 입력"),
    dict(key="SCP_GLOBAL_HOST_TEMPLATE", group="env",
         label="Global 호스트 템플릿", secret=False,
         placeholder=_DEF_GLOBAL_HOST_TEMPLATE,
         help="region 없는 global 서비스(product·iam·sts …)의 호스트 패턴"),
    dict(key="SCP_SERVICE_HOSTS", group="env",
         label="서비스별 호스트 override (JSON)", secret=False,
         placeholder='{"virtualserver": "vs.kr-west1.e.samsungsdscloud.com"}',
         help="API 서브도메인이 카탈로그 서비스명과 다른 경우만 — JSON 객체"),
    dict(key="SCP_BASE_URL", group="env", label="단일 호스트 폴백", secret=False,
         placeholder="https://…",
         help="region/템플릿을 안 쓸 때의 명시적 단일 엔드포인트 (드묾)"),
    # --- 계정 정보 -----------------------------------------------------------
    dict(key="SCP_ACCESS_KEY", group="account", label="Access Key", secret=True,
         placeholder="", help="SCP Open API 자격 — HMAC 서명의 accessKey"),
    dict(key="SCP_SECRET_KEY", group="account", label="Secret Key", secret=True,
         placeholder="", help="HMAC-SHA256 서명 키 — 저장 후에도 끝 4자리만 표시"),
    dict(key="SCP_PROJECT_ID", group="account", label="Project ID", secret=False,
         placeholder="", help="선택 — 특정 서비스가 요구할 때만 (서명에는 불포함)"),
    dict(key="SCP_AUTH_SCHEME", group="account", label="Auth 스킴", secret=False,
         placeholder="hmac", help="hmac(기본) | bearer | none"),
]
MANAGED_KEYS = tuple(f["key"] for f in FIELDS)
SECRET_KEYS = frozenset(f["key"] for f in FIELDS if f["secret"])
_AUTH_SCHEMES = ("hmac", "bearer", "none")
# 값에 공백을 허용하는 키 (JSON). 나머지는 코드/URL 성격이라 공백 = 오타.
_SPACE_OK = frozenset({"SCP_SERVICE_HOSTS"})

_KEY_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_MASK_ONLY = re.compile(r"^[•*]+[^•*]{0,4}$")  # 마스크 표시값이 되돌아온 경우


def env_path() -> Path:
    """대상 .env 경로 — PLATFORM_ENV_FILE 이 있으면 그 파일(테스트/특수 배치),
    없으면 리포 루트의 .env (core.config._load_dotenv 가 읽는 그 파일)."""
    override = os.environ.get("PLATFORM_ENV_FILE", "").strip()
    return Path(override) if override else ROOT / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """core.config._load_dotenv 와 같은 규칙으로 파싱 (첫 등장이 이긴다)."""
    vals: dict[str, str] = {}
    if not path.is_file():
        return vals
    try:
        text = path.read_text()
    except OSError:
        return vals
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        vals.setdefault(key, val)
    return vals


def mask(value: str) -> str:
    """시크릿 표시용 — 끝 4자리만. 짧은 값은 전부 가린다."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def env_ignored(path: Path) -> bool:
    """path 가 git 에 커밋될 수 있는 위치라면 .gitignore 가 막고 있는지 확인.
    리포 밖 경로는 이 리포가 커밋할 수 없으므로 통과(True)."""
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        return True
    rel_posix = rel.as_posix()
    try:
        lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text().splitlines()]
    except OSError:
        return False
    return rel_posix in lines


def validate_updates(updates: dict[str, str]) -> str | None:
    """저장 전 검증 — 실패 사유(키 이름만, 값은 싣지 않는다) 또는 None."""
    for key, val in updates.items():
        if key not in MANAGED_KEYS:
            return f"관리 대상이 아닌 키: {key}"
        if "\n" in val or "\r" in val:
            return f"{key}: 줄바꿈 문자는 허용되지 않습니다"
        if key not in _SPACE_OK and re.search(r"\s", val):
            return f"{key}: 공백 문자는 허용되지 않습니다"
    if updates.get("SCP_SERVICE_HOSTS"):
        try:
            parsed = json.loads(updates["SCP_SERVICE_HOSTS"])
        except json.JSONDecodeError:
            return "SCP_SERVICE_HOSTS: 유효한 JSON 이 아닙니다"
        if not isinstance(parsed, dict):
            return "SCP_SERVICE_HOSTS: JSON 객체({서비스: 호스트})여야 합니다"
    if updates.get("SCP_AUTH_SCHEME") and \
            updates["SCP_AUTH_SCHEME"] not in _AUTH_SCHEMES:
        return "SCP_AUTH_SCHEME: hmac | bearer | none 중 하나여야 합니다"
    return None


def merge_env_file(path: Path, updates: dict[str, str]) -> None:
    """기존 .env 에 updates 를 머지해 원자적으로 쓴다 (권한 0600).

    다른 키·주석·순서는 그대로. 관리 키의 비주석 ``KEY=`` 줄은 전부 새 값으로
    치환(중복 줄도 같은 값 — 첫 등장이 이기는 로더 규칙과 무모순), 파일에 없던
    키는 말미의 관리 섹션에 추가."""
    lines = path.read_text().splitlines() if path.is_file() else []
    pending = dict(updates)
    out: list[str] = []
    for raw in lines:
        m = _KEY_LINE.match(raw)
        key = m.group(1) if (m and not raw.lstrip().startswith("#")) else None
        if key and key in updates:
            out.append(f"{key}={updates[key]}")
            pending.pop(key, None)
        else:
            out.append(raw)
    if pending:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- 설정 화면(/settings)에서 추가 ---")
        out.extend(f"{k}={v}" for k, v in pending.items())
    text = "\n".join(out) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    os.chmod(path, 0o600)  # 기존 파일이 다른 권한이었어도 0600 으로 조인다


def _collect_updates(form) -> dict[str, str]:
    """폼 → 실제 반영할 {키: 값}. 빈 값 = 변경 없음; 시크릿은 마스크 문자만
    되돌아온 경우도 변경 없음(브라우저가 표시값을 그대로 제출한 케이스)."""
    updates: dict[str, str] = {}
    for key in MANAGED_KEYS:
        val = str(form.get(key) or "").strip()
        if not val:
            continue
        if key in SECRET_KEYS and _MASK_ONLY.match(val):
            continue
        updates[key] = val
    return updates


def _field_ctx(current: dict[str, str]) -> list[dict]:
    rows = []
    for f in FIELDS:
        cur = current.get(f["key"], "")
        rows.append({**f,
                     "value": "" if f["secret"] else cur,
                     "masked": mask(cur) if f["secret"] else "",
                     "is_set": bool(cur)})
    return rows


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = ""):
    path = env_path()
    current = read_env_file(path)
    try:
        rel = str(path.resolve().relative_to(ROOT))
    except ValueError:
        rel = str(path)
    return templates.TemplateResponse(request, "settings.html", {
        **common.base_ctx("settings"),
        "fields": _field_ctx(current),
        "env_file": rel,
        "env_file_exists": path.is_file(),
        "gitignore_ok": env_ignored(path),
        "msg": msg[:300],
    })


@router.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    updates = _collect_updates(form)
    if not updates:
        return RedirectResponse(
            "/settings?" + urlencode({"msg": "변경 없음 — 저장할 값이 없습니다."}),
            status_code=303)
    err = validate_updates(updates)
    if err:
        raise HTTPException(400, err)
    path = env_path()
    try:
        merge_env_file(path, updates)
    except OSError as exc:
        # 값은 싣지 않는다 — 파일 경로/오류 종류만.
        raise HTTPException(500, f".env 쓰기 실패: {exc.__class__.__name__}")
    # 다음 런부터 반영: 라이브 런 subprocess 는 {**os.environ, ...} 를 물려받고
    # 자식의 _load_dotenv 는 setdefault(부모 env 가 이긴다) — 그래서 이 프로세스의
    # os.environ 을 갱신해야 새 .env 값이 다음 런에 실제로 도달한다.
    for key, val in updates.items():
        os.environ[key] = val
    msg = ("저장 완료 (권한 0600) — 갱신: " + ", ".join(sorted(updates))
           + ". 새 값은 다음 실행(라이브 런)부터 적용됩니다.")
    return RedirectResponse("/settings?" + urlencode({"msg": msg}),
                            status_code=303)


@router.post("/settings/verify", response_class=HTMLResponse)
async def settings_verify(request: Request):
    """read-only 자격 검증 (선택) — 입력값(빈 칸은 저장된 값으로 폴백)으로
    vpc 목록 GET 1개를 쳐 본다. 실패해도 저장과 무관 (fragment 로만 표시)."""
    form = await request.form()
    typed = _collect_updates(form)
    err = validate_updates(typed)
    if err:
        return templates.TemplateResponse(request, "_settings_verify.html", {
            "ok": False, "message": "입력 오류 — " + err})
    cand = {**read_env_file(env_path()), **typed}
    result = _ping(cand)
    return templates.TemplateResponse(request, "_settings_verify.html", result)


def _ping(cand: dict[str, str]) -> dict:
    """후보 값으로 만든 일회용 Settings 로 GET /v1/vpcs (service=vpc) 1회.
    상태만 분류해 돌려준다 — 값·URL 파라미터는 메시지에 싣지 않는다."""
    from core.config import Settings
    from core.http_client import ApiClient
    try:
        service_hosts = (json.loads(cand["SCP_SERVICE_HOSTS"])
                         if cand.get("SCP_SERVICE_HOSTS") else {})
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "message": "SCP_SERVICE_HOSTS JSON 오류 — 검증 불가"}
    try:
        cfg = Settings(
            region=cand.get("SCP_REGION", ""),
            dr_region=cand.get("SCP_DR_REGION") or "kr-east1",
            env_code=cand.get("SCP_ENV") or "e",
            host_template=cand.get("SCP_HOST_TEMPLATE") or _DEF_HOST_TEMPLATE,
            global_host_template=(cand.get("SCP_GLOBAL_HOST_TEMPLATE")
                                  or _DEF_GLOBAL_HOST_TEMPLATE),
            service_hosts=service_hosts,
            base_url=(cand.get("SCP_BASE_URL") or "").rstrip("/"),
            access_key=cand.get("SCP_ACCESS_KEY", ""),
            secret_key=cand.get("SCP_SECRET_KEY", ""),
            project_id=cand.get("SCP_PROJECT_ID", ""),
            auth_scheme=cand.get("SCP_AUTH_SCHEME") or "hmac",
        )
        if not cfg.access_key or not cfg.secret_key:
            return {"ok": False,
                    "message": "자격 미입력 — Access/Secret Key 를 입력(또는 저장)"
                               "한 뒤 검증하세요. 저장은 검증 없이도 가능합니다."}
        resp = ApiClient(cfg).get("/v1/vpcs", service="vpc",
                                  timeout=8, retry=False)
    except RuntimeError:
        # resolve_base_url 실패 — region/base_url 미구성
        return {"ok": False,
                "message": "엔드포인트 미구성 — Region(또는 단일 호스트 폴백)을 "
                           "입력해야 검증할 수 있습니다."}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False,
                "message": f"도달 실패 ({exc.__class__.__name__}) — 오프라인이거나 "
                           "엔드포인트 오류일 수 있습니다. 저장은 가능합니다."}
    if resp.ok:
        return {"ok": True,
                "message": f"자격 유효 — vpc 목록 GET 이 HTTP {resp.status} 로 "
                           "응답했습니다."}
    if resp.status in (401, 403):
        return {"ok": False,
                "message": f"인증 실패 (HTTP {resp.status}) — Access/Secret Key 를 "
                           "확인하세요. 저장은 가능합니다."}
    return {"ok": False,
            "message": f"엔드포인트 도달 (HTTP {resp.status}) — 자격 또는 권한을 "
                       "확인하세요. 저장은 가능합니다."}
