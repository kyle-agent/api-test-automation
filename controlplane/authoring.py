"""Authoring pipeline — UI 편집의 검증 → 원자적 쓰기 → 로컬 git 커밋 (M3).

docs/PLATFORM-PLAN.md §3.1의 개발 기간 반영 모델: 모든 UI 저작(스위트·환경
프로파일·시나리오·knowledge)은

  1. 임시 파일에 기록 → 원자적 교체(os.replace)로 working copy에 임시 적용
  2. 해당 파일군의 validator 실행 (suites/profiles는 in-process,
     scenarios/knowledge-formal은 기존 CLI validator를 subprocess로)
  3. 실패 시 원본을 byte-identical하게 복원 + 오류를 UI에 반환
  4. 통과 시 적용 유지 + **자동 로컬 git 커밋** (이력·1-click 되돌리기)
  5. 원격 push는 PLATFORM_GIT_PUSH=true일 때만 (기본 off — 운영자가 수동 push)

M4 컷오버는 마지막 반영 단계(5)만 바꾸면 된다 — 검증→쓰기(1~4)는 두 모드가
이 모듈을 그대로 공유한다 (§3.1 "검증 로직은 그대로, 마지막 반영 단계만 교체").

git/subprocess 실패는 절대 raise하지 않는다 — 파일 반영은 유지하고 경고로
보고하는 best-effort 스타일 (controlplane 전반과 동일).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# UI가 편집할 수 있는 범위 — 선언적 데이터 파일만 (엔진 코드 .py는 제외)
EDITABLE_PREFIXES = ("suites/", "environments/", "knowledge/",
                     "regression/scenarios/")
EDITABLE_SUFFIXES = (".yaml", ".yml", ".json", ".md")

# fallback when the plain commit fails (identity unset / signing unavailable)
_GIT_FALLBACK = ("-c", "user.name=Platform UI", "-c", "user.email=platform@local",
                 "-c", "commit.gpgsign=false")


def push_enabled() -> bool:
    return os.environ.get("PLATFORM_GIT_PUSH", "").strip().lower() in (
        "1", "true", "yes", "on")


# --- path scope -----------------------------------------------------------------

def editable_path(rel: str, root: Path | None = None) -> Path | None:
    """Resolve rel inside the repo; None unless it is an editable data file."""
    root = (root or ROOT).resolve()
    try:
        path = (root / (rel or "").strip().lstrip("/")).resolve()
        rp = path.relative_to(root).as_posix()
    except (ValueError, OSError):
        return None
    if not any(rp.startswith(p) for p in EDITABLE_PREFIXES):
        return None
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        return None
    return path


def lifecycle_file(lifecycle_id: str) -> str | None:
    """Which repo file holds this lifecycle (scenarios.json or a fragment) —
    the loader's with_sources merge is the single source of truth."""
    try:
        from regression.scenarios.loader import load_lifecycles
        _, source = load_lifecycles(with_sources=True)
    except Exception:
        return None
    name = source.get(lifecycle_id)
    if not name:
        return None
    if name == "scenarios.json":
        return "regression/scenarios/scenarios.json"
    return f"regression/scenarios/lifecycles/{name}"


# --- parse + validators -----------------------------------------------------------

def parse_errors(rel: str, content: str) -> list[str]:
    if rel.endswith((".yaml", ".yml")):
        import yaml
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            return [f"{rel}: YAML 파싱 실패 — {exc}"]
    elif rel.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as exc:
            return [f"{rel}: JSON 파싱 실패 — {exc}"]
    return []


def _run_cli(args: list[str]) -> list[str]:
    """Run an existing CLI validator from the repo root; non-zero exit -> the
    ERROR lines (or output tail) as the error list."""
    try:
        proc = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)})
    except Exception as exc:
        return [f"validator 실행 실패 ({' '.join(args[1:3])}): {exc}"]
    if proc.returncode == 0:
        return []
    lines = (proc.stdout + "\n" + proc.stderr).splitlines()
    errs = [ln.strip() for ln in lines if "ERROR" in ln]
    return errs or [ln for ln in lines if ln.strip()][-15:] or [
        f"validator exit {proc.returncode}"]


def _suite_errors() -> list[str]:
    from core import suites as core_suites
    return core_suites.validate_all()


def _profile_errors() -> list[str]:
    from core import profiles as core_profiles
    return core_profiles.validate_all()


def _scenario_errors() -> list[str]:
    return _run_cli([sys.executable, "-m", "regression.scenarios.validate"])


def _knowledge_formal_errors() -> list[str]:
    return _run_cli([sys.executable, str(ROOT / "knowledge" / "formal" / "validate.py")])


# prefix -> validator; module-level so tests can stub entries.
VALIDATORS = [
    ("suites/", _suite_errors),
    ("environments/", _profile_errors),
    ("regression/scenarios/", _scenario_errors),
    ("knowledge/formal/", _knowledge_formal_errors),
]


def _validators_for(rel: str):
    return [fn for prefix, fn in VALIDATORS if rel.startswith(prefix)]


# --- quota simulation (§2.3 "할당량 시뮬레이션 — 사전 경고") -----------------------

def vpc_peak(deps: dict, lifecycles: list[dict]) -> dict:
    """Peak concurrent VPC usage implied by vpc_schedule, per the shared-infra
    math in knowledge/vpc-scheduling-strategy.md: the adopt class shares ONE
    session VPC (=1) and the vpc-crud class runs serially, so the worst case is
    that shared VPC overlapping the single serial lifecycle that self-creates
    the most VPCs at once (e.g. vpc-peering holds its peer VPC + the shared one).
    """
    sched = (deps or {}).get("vpc_schedule") or {}
    budget_paths = (deps or {}).get("budget_paths") or {"/v1/vpcs": "vpc"}
    vpc_paths = {str(p).split("?")[0].rstrip("/")
                 for p, kind in budget_paths.items() if kind == "vpc"}
    by_id = {l.get("id"): l for l in lifecycles or []}
    adopt = list(sched.get("adopt_lifecycles") or [])
    crud = list(sched.get("vpc_crud_lifecycles") or [])

    def self_creates(lid: str) -> int:
        lc = by_id.get(lid)
        if lc is None:
            return 1  # unknown lifecycle — assume the usual single VPC
        n = 0
        for s in lc.get("steps") or []:
            if not isinstance(s, dict):
                continue
            path = str(s.get("path") or "").split("?")[0].rstrip("/")
            if (str(s.get("method") or "").upper() == "POST"
                    and path in vpc_paths and not s.get("adopt")):
                n += 1
        return n

    worst_id, worst = "", 0
    for lid in crud:
        n = self_creates(lid)
        if n > worst:
            worst_id, worst = lid, n
    shared = 1 if adopt else 0
    from core.budgets import Budget
    return {"shared": shared, "worst": worst, "worst_id": worst_id,
            "peak": shared + worst,
            "limit": Budget().limits.get("vpc"),  # DEFAULT_LIMITS + SCP_BUDGET_LIMITS
            "cap": sched.get("per_run_vpc_cap"),
            "unknown": sorted(x for x in adopt + crud if x not in by_id),
            "overlap": sorted(set(adopt) & set(crud))}


def vpc_quota_warnings(deps: dict, lifecycles: list[dict]) -> list[str]:
    """WARN (never block) when the schedule implies more concurrent VPCs than
    the account limit allows."""
    if not (deps or {}).get("vpc_schedule"):
        return []
    sim = vpc_peak(deps, lifecycles)
    out = []
    if sim["unknown"]:
        out.append("vpc_schedule이 존재하지 않는 lifecycle을 참조합니다: "
                   + ", ".join(sim["unknown"]))
    if sim["overlap"]:
        out.append("adopt와 vpc-crud 양쪽에 분류된 lifecycle: "
                   + ", ".join(sim["overlap"]) + " — 한쪽에만 두세요")
    if sim["limit"] is not None and sim["peak"] > sim["limit"]:
        out.append(f"할당량 시뮬레이션: peak 동시 VPC {sim['peak']}개 "
                   f"(공유 adopt VPC {sim['shared']} + 직렬 vpc-crud 최악 "
                   f"'{sim['worst_id']}' {sim['worst']}개) > 계정 한도 "
                   f"{sim['limit']} — 스케줄/lane을 조정하세요 "
                   f"(knowledge/vpc-scheduling-strategy.md)")
    elif isinstance(sim["cap"], int) and sim["peak"] > sim["cap"]:
        out.append(f"할당량 시뮬레이션: peak 동시 VPC {sim['peak']}개가 "
                   f"per_run_vpc_cap={sim['cap']}을 넘습니다 (계정 한도 "
                   f"{sim['limit']} 이내지만 leftover headroom이 사라짐)")
    return out


def quota_warnings_for(rel: str, root: Path | None = None) -> list[str]:
    """Best-effort quota simulation on save of any scenario/dependencies file
    — reads the temp-applied state, so a pending edit IS what gets simulated."""
    if not rel.startswith("regression/scenarios/"):
        return []
    root = (root or ROOT).resolve()
    try:
        deps = json.loads(
            (root / "regression" / "scenarios" / "dependencies.json").read_text())
    except Exception:
        return []
    try:
        from regression.scenarios.loader import load_lifecycles
        lifecycles = load_lifecycles()
    except Exception:
        lifecycles = []
    try:
        return vpc_quota_warnings(deps, lifecycles)
    except Exception:
        return []


# --- atomic write + restore -------------------------------------------------------

def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _restore(path: Path, original: bytes | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, original)


# --- git (never raises) -----------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=60)


def _git_commit(root: Path, rel: str) -> tuple[str, str]:
    """git add + commit the one file; (sha, warning) — never raises."""
    msg = f"authoring: {rel} via platform UI"
    try:
        add = _git(root, "add", "--", rel)
        if add.returncode != 0:
            return "", f"git add 실패 (파일은 반영됨): {add.stderr.strip()[:200]}"
        commit = _git(root, "commit", "-m", msg, "--", rel)
        if commit.returncode != 0:  # e.g. user.name/email unset on the host
            commit = _git(root, *_GIT_FALLBACK, "commit", "-m", msg, "--", rel)
        if commit.returncode != 0:
            out = (commit.stderr or commit.stdout).strip()[:200]
            return "", f"git commit 실패 (파일은 반영됨): {out}"
        sha = _git(root, "rev-parse", "--short=12", "HEAD").stdout.strip()
        return sha, ""
    except Exception as exc:
        return "", f"git 사용 불가 (파일은 반영됨): {exc}"


def _git_push(root: Path) -> tuple[bool, str]:
    try:
        push = _git(root, "push")
        if push.returncode != 0:
            return False, ("git push 실패 (로컬 커밋은 유지 — 수동 push 필요): "
                           + (push.stderr or push.stdout).strip()[:200])
        return True, ""
    except Exception as exc:
        return False, f"git push 실패 (로컬 커밋은 유지): {exc}"


# --- the pipeline ------------------------------------------------------------------

def propose_edit(rel_path: str, new_content: str, *,
                 validate_only: bool = False, root: Path | None = None) -> dict:
    """Validate-then-apply one file edit (§3.1 steps 1–4 + dev-period commit).

    Returns {ok, errors, warnings, rel, commit, pushed}. ``validate_only``
    runs the full temp-apply + validator pass but always restores the original
    (the editor's "검증만" button). ``root`` is test plumbing — the validators
    themselves always check the real repo working copy.
    """
    root = (root or ROOT).resolve()
    result = {"ok": False, "errors": [], "warnings": [],
              "rel": "", "commit": "", "pushed": False}
    path = editable_path(rel_path, root)
    if path is None:
        result["errors"].append(
            f"{rel_path!r}: 편집 가능 범위 밖 — {', '.join(EDITABLE_PREFIXES)} "
            f"아래의 {'/'.join(s.lstrip('.') for s in EDITABLE_SUFFIXES)} 파일만 편집할 수 있습니다")
        return result
    rel = path.relative_to(root).as_posix()
    result["rel"] = rel

    errs = parse_errors(rel, new_content)
    if errs:
        result["errors"] = errs
        return result

    original = path.read_bytes() if path.exists() else None
    if original is not None and original.decode("utf-8", "replace") == new_content:
        result["warnings"].append("내용 변화 없음 — 저장/커밋을 생략합니다")
        result["ok"] = True
        return result
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, new_content.encode("utf-8"))
    except OSError as exc:
        result["errors"].append(f"{rel}: 쓰기 실패 — {exc}")
        return result

    # validators run against the temp-applied working copy; roll back on failure
    try:
        for fn in _validators_for(rel):
            try:
                errs += list(fn() or [])
            except Exception as exc:
                errs.append(f"{rel}: validator 실행 실패 — {exc}")
        if not errs:
            result["warnings"] += quota_warnings_for(rel, root)
    finally:
        if errs or validate_only:
            _restore(path, original)
    if errs:
        result["errors"] = errs
        return result

    result["ok"] = True
    if validate_only:
        return result

    sha, warn = _git_commit(root, rel)
    result["commit"] = sha
    if warn:
        result["warnings"].append(warn)
    if sha and push_enabled():
        pushed, warn = _git_push(root)
        result["pushed"] = pushed
        if warn:
            result["warnings"].append(warn)
    return result
