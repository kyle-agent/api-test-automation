"""Environment profiles — the regression target (검증계/운영계 × region) as data.

``environments/<id>.yaml`` declares one test target: endpoint templates,
credential *references* (never values) and per-environment safety gates. A run
is always "suite × profile" (docs/PLATFORM-PLAN.md §2.1). The exporter turns a
profile into the SCP_* environment variables the engine already reads
(core/config.py), so the engine itself needs no knowledge of profiles.

Credentials are indirection only: the profile names the environment variable
that HOLDS each secret (e.g. ``SCP_ACCESS_KEY: SCP_ACCESS_KEY_PROD``) and the
exporter resolves it from the calling process's environment at export time.
Profiles never contain secret values, so they are safe to commit.

``forbid:`` is a hard per-environment safety gate: the exporter emits
SCP_PROFILE_FORBID and core/config.py refuses the matching SCP_ALLOW_* /
SCP_RUN_HEAVY flags even when they are set — e.g. a production profile makes
mutating runs impossible no matter what the trigger requested.

CLI:
  python -m core.profiles list
  python -m core.profiles validate
  python -m core.profiles export <id>            # KEY=VALUE lines (>> $GITHUB_ENV)
  python -m core.profiles export <id> --shell    # `export KEY=...` lines (eval-able)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "environments"

GATES = ("mutations", "destructive", "heavy")

# Engine-known endpoint/behaviour variables a profile may set verbatim. A
# typo'd key fails validation instead of silently exporting a dead variable.
ALLOWED_ENV = frozenset({
    "SCP_REGION", "SCP_ENV", "SCP_HOST_TEMPLATE", "SCP_GLOBAL_HOST_TEMPLATE",
    "SCP_GLOBAL_SERVICES", "SCP_SERVICE_HOSTS", "SCP_BASE_URL",
    "SCP_AUTH_SCHEME", "SCP_TIMEOUT", "SCP_MAX_RETRIES",
    "SCP_OPLOG_BUCKET", "SCP_OPLOG_S3_ENDPOINT",
})

# Credential targets the engine reads; sources are resolved from the caller's
# environment at export time (never stored in the profile).
CREDENTIAL_TARGETS = frozenset({
    "SCP_ACCESS_KEY", "SCP_SECRET_KEY", "SCP_PROJECT_ID",
    "SCP_OPLOG_ACCESS_KEY", "SCP_OPLOG_SECRET_KEY",
})


def _load_yaml(path: Path) -> dict:
    import yaml
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: top level must be a mapping")
    return data


def profile_path(profile_id: str) -> Path:
    return PROFILE_DIR / f"{profile_id}.yaml"


def list_profiles() -> list[dict]:
    if not PROFILE_DIR.is_dir():
        return []
    return [_load_yaml(p) for p in sorted(PROFILE_DIR.glob("*.yaml"))]


def load_profile(profile_id: str) -> dict:
    path = profile_path(profile_id)
    if not path.exists():
        known = ", ".join(p.stem for p in sorted(PROFILE_DIR.glob("*.yaml"))) or "(none)"
        raise FileNotFoundError(
            f"unknown environment profile {profile_id!r} — known: {known}")
    return _load_yaml(path)


def validate_profile(data: dict, path: Path) -> list[str]:
    errs = []
    pid = data.get("id")
    if not pid or not isinstance(pid, str):
        errs.append(f"{path.name}: missing/invalid 'id'")
    elif pid != path.stem:
        errs.append(f"{path.name}: id {pid!r} must match the filename stem")
    env = data.get("env", {})
    if not isinstance(env, dict):
        errs.append(f"{path.name}: 'env' must be a mapping")
        env = {}
    for key in env:
        if key not in ALLOWED_ENV:
            errs.append(f"{path.name}: env key {key!r} is not an engine variable "
                        f"(allowed: {', '.join(sorted(ALLOWED_ENV))})")
    creds = data.get("credentials", {})
    if not isinstance(creds, dict):
        errs.append(f"{path.name}: 'credentials' must be a mapping")
        creds = {}
    for target, source in creds.items():
        if target not in CREDENTIAL_TARGETS:
            errs.append(f"{path.name}: credential target {target!r} unknown "
                        f"(allowed: {', '.join(sorted(CREDENTIAL_TARGETS))})")
        if not (isinstance(source, str) and source.replace("_", "").isalnum()
                and source == source.upper()):
            errs.append(f"{path.name}: credential source for {target!r} must be an "
                        f"UPPER_SNAKE env var name, got {source!r}")
    forbid = data.get("forbid", [])
    if not isinstance(forbid, list) or any(g not in GATES for g in forbid):
        errs.append(f"{path.name}: 'forbid' must be a list drawn from {GATES}")
    quotas = data.get("quota_overrides", {})
    if not isinstance(quotas, dict) or any(
            not isinstance(v, int) or v < 0 for v in quotas.values()):
        errs.append(f"{path.name}: 'quota_overrides' must map kind -> non-negative int")
    return errs


def validate_all() -> list[str]:
    errs = []
    paths = sorted(PROFILE_DIR.glob("*.yaml")) if PROFILE_DIR.is_dir() else []
    if not paths:
        errs.append(f"no profiles found under {PROFILE_DIR}")
    for p in paths:
        try:
            errs += validate_profile(_load_yaml(p), p)
        except Exception as exc:
            errs.append(f"{p.name}: {exc}")
    return errs


def export_pairs(data: dict, environ=os.environ) -> list[tuple[str, str]]:
    """Profile -> ordered (KEY, VALUE) pairs for the engine's environment."""
    pairs: list[tuple[str, str]] = [("SCP_PROFILE_ID", str(data.get("id", "")))]
    for key, val in (data.get("env") or {}).items():
        # mapping values (e.g. SCP_SERVICE_HOSTS) serialize to the JSON shape
        # core/config.py already parses.
        pairs.append((key, json.dumps(val) if isinstance(val, dict) else str(val)))
    for target, source in (data.get("credentials") or {}).items():
        val = environ.get(source, "")
        if val:
            pairs.append((target, val))
        elif source != target:
            print(f"[profiles] note: credential source {source} is unset — "
                  f"{target} keeps its current value", file=sys.stderr)
    quotas = data.get("quota_overrides") or {}
    if quotas:
        pairs.append(("SCP_BUDGET_LIMITS", json.dumps(quotas)))
    # Always emitted (an empty value clears a stale gate from a prior profile).
    pairs.append(("SCP_PROFILE_FORBID", ",".join(data.get("forbid") or [])))
    return pairs


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="environment profiles (environments/*.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    ex = sub.add_parser("export")
    ex.add_argument("profile_id")
    ex.add_argument("--shell", action="store_true",
                    help="emit eval-able `export KEY=...` lines instead of KEY=VALUE")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for p in list_profiles():
            forbid = ",".join(p.get("forbid") or []) or "-"
            print(f"{p.get('id'):<24} forbid={forbid:<28} {p.get('label', '')}")
        return 0
    if a.cmd == "validate":
        errs = validate_all()
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"profiles: {len(list(PROFILE_DIR.glob('*.yaml')))} checked, "
              f"{len(errs)} error(s)")
        return 1 if errs else 0
    data = load_profile(a.profile_id)
    errs = validate_profile(data, profile_path(a.profile_id))
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if a.shell:
        import shlex
        for k, v in export_pairs(data):
            print(f"export {k}={shlex.quote(v)}")
    else:
        for k, v in export_pairs(data):
            print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
