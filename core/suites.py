"""Named test suites — full / smoke / service-deep run shapes as data.

``suites/<id>.yaml`` gives a name to a run shape that was previously an
implicit combination of CLI filters and safety flags (docs/PLATFORM-PLAN.md
§2.3). A suite compiles ("renders") to the ``.github/run-request`` KEY=VALUE
options the api-test.yml orchestrator already parses — the workflow gates and
the engine need no new concepts; the suite is simply where the combination
LIVES, versioned and validated.

Trigger paths:
  * file trigger — a ``suite=<id>`` line in .github/run-request: the spec job
    expands it (explicit KEY=VALUE lines in the file still win). Or render the
    whole block locally:  python -m core.suites render full > .github/run-request
  * workflow_dispatch — the ``suite`` input; same expansion in the spec job.
    Suite values OR into the safety gates; explicit dispatch inputs still apply.

Ad-hoc narrowing ("특정 서비스만 상세 확인") needs no file per service:
  python -m core.suites render full --set service=filestorage \
      --set crud_filter=filestorage

CLI:
  python -m core.suites list
  python -m core.suites validate
  python -m core.suites render <id> [--set key=value ...] [--note "..."]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = ROOT / "suites"

# Exactly the option keys .github/run-request understands (api-test.yml spec
# job), in render order. Booleans render as true/false.
BOOL_KEYS = ("mutations", "destructive", "heavy", "sweep_force", "conformance")
STR_KEYS = ("category", "service", "crud_filter")
REQUEST_KEYS = BOOL_KEYS + STR_KEYS


def _load_yaml(path: Path) -> dict:
    import yaml
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: top level must be a mapping")
    return data


def suite_path(suite_id: str) -> Path:
    return SUITE_DIR / f"{suite_id}.yaml"


def list_suites() -> list[dict]:
    if not SUITE_DIR.is_dir():
        return []
    return [_load_yaml(p) for p in sorted(SUITE_DIR.glob("*.yaml"))]


def load_suite(suite_id: str) -> dict:
    path = suite_path(suite_id)
    if not path.exists():
        known = ", ".join(p.stem for p in sorted(SUITE_DIR.glob("*.yaml"))) or "(none)"
        raise FileNotFoundError(f"unknown suite {suite_id!r} — known: {known}")
    return _load_yaml(path)


def validate_suite(data: dict, path: Path) -> list[str]:
    errs = []
    sid = data.get("id")
    if not sid or not isinstance(sid, str):
        errs.append(f"{path.name}: missing/invalid 'id'")
    elif sid != path.stem:
        errs.append(f"{path.name}: id {sid!r} must match the filename stem")
    req = data.get("request", {})
    if not isinstance(req, dict):
        errs.append(f"{path.name}: 'request' must be a mapping")
        req = {}
    for key, val in req.items():
        if key not in REQUEST_KEYS:
            errs.append(f"{path.name}: request key {key!r} is not a run-request "
                        f"option (allowed: {', '.join(REQUEST_KEYS)})")
        elif key in BOOL_KEYS and not isinstance(val, bool):
            errs.append(f"{path.name}: request.{key} must be a boolean")
        elif key in STR_KEYS and not isinstance(val, str):
            errs.append(f"{path.name}: request.{key} must be a string")
    # destructive without mutations would create-then-strand nothing, but
    # mutations without destructive strands resources past the sweep — flag it.
    if req.get("mutations") and not req.get("destructive"):
        errs.append(f"{path.name}: mutations=true requires destructive=true "
                    f"(otherwise created resources outlive the run)")
    return errs


def validate_all() -> list[str]:
    errs = []
    paths = sorted(SUITE_DIR.glob("*.yaml")) if SUITE_DIR.is_dir() else []
    if not paths:
        errs.append(f"no suites found under {SUITE_DIR}")
    for p in paths:
        try:
            errs += validate_suite(_load_yaml(p), p)
        except Exception as exc:
            errs.append(f"{p.name}: {exc}")
    return errs


def render(data: dict, overrides: dict | None = None, note: str = "") -> str:
    """Suite -> .github/run-request content (KEY=VALUE lines + header)."""
    req = dict(data.get("request") or {})
    for key, val in (overrides or {}).items():
        if key not in REQUEST_KEYS:
            raise ValueError(f"--set key {key!r} is not a run-request option "
                             f"(allowed: {', '.join(REQUEST_KEYS)})")
        req[key] = val
    lines = [f"# suite: {data.get('id')} — {data.get('label', '')}".rstrip(" —"),
             f"# rendered by core.suites at "
             f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"]
    if note:
        lines += [f"# {ln}" for ln in note.splitlines()]
    for key in REQUEST_KEYS:
        if key not in req:
            continue
        val = req[key]
        if key in BOOL_KEYS and isinstance(val, bool):
            val = "true" if val else "false"
        lines.append(f"{key}={val}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="named test suites (suites/*.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    rd = sub.add_parser("render")
    rd.add_argument("suite_id")
    rd.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="key=value", help="override one request option")
    rd.add_argument("--note", default="", help="comment line(s) for the header")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in list_suites():
            req = s.get("request") or {}
            flags = " ".join(k for k in BOOL_KEYS if req.get(k)) or "read-only"
            print(f"{s.get('id'):<16} [{flags}]  {s.get('label', '')}")
        return 0
    if a.cmd == "validate":
        errs = validate_all()
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"suites: {len(list(SUITE_DIR.glob('*.yaml')))} checked, "
              f"{len(errs)} error(s)")
        return 1 if errs else 0
    data = load_suite(a.suite_id)
    errs = validate_suite(data, suite_path(a.suite_id))
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    overrides = {}
    for item in a.sets:
        key, _, val = item.partition("=")
        if key in BOOL_KEYS:
            overrides[key] = val.strip().lower() in ("1", "true", "yes", "on")
        else:
            overrides[key] = val
    sys.stdout.write(render(data, overrides, a.note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
