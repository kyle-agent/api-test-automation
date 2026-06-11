"""AI authoring pipelines (PLATFORM-PLAN §4-A1/A2/A3) — M3.

Three pipelines, all OUTSIDE the test hot path, all producing reviewable
DRAFT FILES under drafts/ (never auto-enabled, never auto-merged):

  A1 spec_impact(...)    spec diff (mechanical, spec/diff.py) + Claude impact
                         analysis -> rerun scope suggestion.
  A2 scenario_draft(...) one service's catalog + bodies + knowledge + two
                         proven lifecycles as few-shot -> lifecycle JSON draft
                         (enabled:false ALWAYS) + mechanical validation.
  A3 extract_facts(...)  a finished run's 2xx observations -> validated-fact
                         candidates + formal-YAML suggestion.

Every pipeline degrades gracefully without ANTHROPIC_API_KEY: A1 still
returns the full mechanical diff (AI section marked disabled); A2/A3 report
the disabled state instead of calling out.

Claude integration follows controlplane/triage.py: anthropic SDK,
claude-opus-4-8, thinking={"type": "adaptive"}, structured output via
output_config json_schema, refusal check, defensive JSON parse. The client is
obtained through the module-level seam ``_client()`` so offline tests can
monkeypatch ``ai_pipelines._client`` with a stub.

Config (env):
  ANTHROPIC_API_KEY    enables the Claude layer (absent -> mechanical-only)
  PLATFORM_AI_MODEL    default claude-opus-4-8
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = ROOT / "drafts"
CATALOG_PATH = ROOT / "data" / "api_catalog.json"
BODIES_PATH = ROOT / "data" / "api_bodies.json"
DEFAULT_MODEL = "claude-opus-4-8"

# placeholders the engine seeds before any step runs (engine.py ctx) — these
# never need a prior capture. Shared-infra adopt keys included (shared VPC).
ENGINE_GLOBALS = {"unique", "ualpha", "region", "today", "today_plus_5y",
                  "vpc_id", "subnet_id", "cert_body", "cert_key", "cert_chain"}


# --- Claude seam (stub me in tests) ---------------------------------------------

def _client():
    """The anthropic client — module-level seam so tests can monkeypatch."""
    import anthropic
    return anthropic.Anthropic()


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _ask(system: str, user: str, schema: dict, *,
         max_tokens: int = 16000) -> tuple[dict | None, str]:
    """One structured-output call, triage.py's defensive pattern.

    Returns (parsed_dict, "") on success, (None, reason) otherwise — callers
    render the mechanical part regardless."""
    if not enabled():
        return None, "ANTHROPIC_API_KEY 미설정 — AI 비활성 (mechanical-only)"
    try:
        response = _client().messages.create(
            model=os.environ.get("PLATFORM_AI_MODEL", DEFAULT_MODEL),
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        return None, f"Claude 호출 실패: {exc}"
    if response.stop_reason == "refusal":
        return None, "Claude가 안전 분류기에 의해 응답을 거부했습니다"
    try:
        text = next(b.text for b in response.content if b.type == "text")
    except StopIteration:
        return None, "응답에 text 블록이 없습니다"
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return None, f"응답 JSON 파싱 실패: {exc}"


# --- draft store ----------------------------------------------------------------

_DRAFT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")


def save_draft(stem: str, payload: dict) -> str:
    """Write a draft file and return its name. ``stem`` is sanitized."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "draft"
    name = f"{stem}.json"
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFTS_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return name


def draft_path(name: str) -> Path | None:
    """Path of one draft, or None when the name is unsafe / absent.
    Guarded: the name must be a plain filename and resolve inside drafts/."""
    if not _DRAFT_NAME_RE.match(name or ""):
        return None
    try:
        path = (DRAFTS_DIR / name).resolve()
        path.relative_to(DRAFTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


def load_draft(name: str) -> dict | None:
    path = draft_path(name)
    if not path:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def list_drafts() -> list[dict]:
    """Newest-first draft index for the /ai page."""
    if not DRAFTS_DIR.is_dir():
        return []
    out = []
    for p in DRAFTS_DIR.glob("*.json"):
        try:
            head = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            head = {}
        out.append({"name": p.name, "kind": head.get("kind", "?"),
                    "created": head.get("created", ""),
                    "kb": round(p.stat().st_size / 1024, 1)})
    out.sort(key=lambda d: d["created"], reverse=True)
    return out


def _ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# =================================================================================
# A1 — spec-diff 영향 분석
# =================================================================================

A1_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string",
                    "description": "한국어 요약: 이번 spec 변경의 핵심과 권고 조치."},
        "impacted_services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "why": {"type": "string"},
                    "suggested_action": {"type": "string"},
                },
                "required": ["service", "why", "suggested_action"],
                "additionalProperties": False,
            },
        },
        "rerun": {
            "type": "object",
            "properties": {
                "suites": {"type": "array", "items": {"type": "string"}},
                "service_filters": {"type": "array", "items": {"type": "string"}},
                "crud_filters": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["suites", "service_filters", "crud_filters"],
            "additionalProperties": False,
        },
    },
    "required": ["summary", "impacted_services", "rerun"],
    "additionalProperties": False,
}

_DIFF_CAP = 200  # per bucket, in the saved draft / prompt


def catalog_from_git(rev: str) -> str:
    """Materialize data/api_catalog.json at ``rev`` into a temp file.
    Raises ValueError when git cannot produce it."""
    rev = (rev or "").strip()
    if not re.match(r"^[A-Za-z0-9._/~^-]{1,80}$", rev):
        raise ValueError(f"잘못된 git rev: {rev!r}")
    try:
        out = subprocess.run(
            ["git", "show", f"{rev}:data/api_catalog.json"],
            cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git show 실패: {exc}")
    if out.returncode != 0:
        raise ValueError(f"git show {rev}:data/api_catalog.json 실패: "
                         f"{out.stderr.decode(errors='replace')[:200]}")
    fd, path = tempfile.mkstemp(prefix="catalog-", suffix=".json")
    with os.fdopen(fd, "wb") as fh:
        fh.write(out.stdout)
    return path


def _service_of(key: str) -> str:
    """'networking/vpc/createvpc' -> 'networking/vpc'."""
    return "/".join(key.split("/")[:2])


def _enabled_lifecycles_by_service(services: list[str]) -> dict[str, list[str]]:
    try:
        from regression.scenarios.loader import load_lifecycles
        lifecycles = load_lifecycles()
    except Exception:
        return {}
    wanted = set(services)
    out: dict[str, list[str]] = {}
    for lc in lifecycles:
        svc = lc.get("service", "")
        if svc in wanted and lc.get("enabled"):
            out.setdefault(svc, []).append(lc.get("id", ""))
    return out


def spec_impact(*, old_path: str | None = None, rev: str | None = None,
                new_path: str | None = None, save: bool = True) -> dict:
    """A1: mechanical catalog diff + (optional) Claude impact analysis.

    ``old_path`` (a previous catalog file) or ``rev`` (git revision of
    data/api_catalog.json) selects the baseline; ``rev`` wins when both given.
    Raises ValueError when the baseline cannot be loaded."""
    from spec.diff import diff_catalog

    new_path = new_path or str(CATALOG_PATH)
    baseline_label = old_path or ""
    if rev:
        old_path = catalog_from_git(rev)
        baseline_label = f"git:{rev}"
    if not old_path:
        raise ValueError("비교할 이전 카탈로그가 없습니다 — 파일 경로 또는 git rev 필요")
    report = diff_catalog(old_path, new_path)

    # trim full endpoint entries to what review + the prompt need
    diff = {
        "added": [{"key": e["key"], "sig": e["sig"]}
                  for e in report["added"][:_DIFF_CAP]],
        "removed": [{"key": e["key"], "sig": e["sig"]}
                    for e in report["removed"][:_DIFF_CAP]],
        "changed": [{"key": c["key"], "sig_old": c["sig_old"],
                     "sig_new": c["sig_new"], "fields": c["fields"]}
                    for c in report["changed"][:_DIFF_CAP]],
    }
    affected = sorted({_service_of(e["key"])
                       for bucket in ("added", "removed", "changed")
                       for e in diff[bucket]})
    lifecycles = _enabled_lifecycles_by_service(affected)

    ai, ai_error = None, ""
    if report["summary"]["added"] or report["summary"]["removed"] \
            or report["summary"]["changed"]:
        system = (
            "You are the spec-impact analyst of the SCP API Regression Test "
            "Platform. Given a mechanical diff between two versions of Samsung "
            "Cloud Platform's API catalog plus the enabled regression lifecycles "
            "of the affected services, identify which services are impacted and "
            "why, and propose a minimal rerun scope: suite ids (smoke/full or "
            "service-deep-*), service_filters ('category/service' strings) and "
            "crud_filters (lifecycle-id or 'category/service*' glob strings the "
            "engine's crud filter accepts). Removed endpoints that an enabled "
            "lifecycle still calls are the most urgent. Write summary/why/"
            "suggested_action in Korean.")
        user = (f"Catalog diff (baseline={baseline_label or old_path}):\n"
                + json.dumps({"summary": report["summary"], **diff},
                             ensure_ascii=False)
                + "\n\nEnabled lifecycles of affected services:\n"
                + json.dumps(lifecycles, ensure_ascii=False))
        ai, ai_error = _ask(system, user, A1_SCHEMA)
    else:
        ai_error = "변경 없음 — AI 분석 생략"

    result = {
        "kind": "spec-impact",
        "created": _now_iso(),
        "baseline": baseline_label or old_path,
        "summary_counts": report["summary"],
        "diff": diff,
        "affected_services": affected,
        "enabled_lifecycles": lifecycles,
        "ai": ai,
        "ai_error": ai_error,
    }
    if save:
        result["draft_name"] = save_draft(f"spec-impact-{_ts()}", result)
    return result


# =================================================================================
# A2 — 시나리오 초안 생성
# =================================================================================

# Structured outputs forbid free-form objects (additionalProperties must be
# false), so the arbitrary request body / capture map / poll spec travel as
# JSON-encoded strings ("" when absent) and are parsed back into engine shape.
A2_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "service": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "method": {"type": "string",
                               "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string"},
                    "json_body": {"type": "string",
                                  "description": "request body as a JSON object "
                                                 "encoded as a string; '' if none"},
                    "expect_status": {"type": "array",
                                      "items": {"type": "integer"}},
                    "capture": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"var": {"type": "string"},
                                           "path": {"type": "string"}},
                            "required": ["var", "path"],
                            "additionalProperties": False,
                        },
                        "description": "placeholder captures, e.g. "
                                       "var=rg_id path=$.resource_group.id",
                    },
                    "poll_json": {"type": "string",
                                  "description": "poll spec as JSON string, e.g. "
                                                 '{"field":"$.vpc.state","until":["ACTIVE"]}'
                                                 " or '' if none"},
                    "destructive": {"type": "boolean"},
                    "optional": {"type": "boolean"},
                },
                "required": ["name", "method", "path", "json_body",
                             "expect_status", "capture", "poll_json",
                             "destructive", "optional"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"},
                  "description": "한국어: 불확실한 부분(추정한 body 필드, 검증 안 된 "
                                 "capture 경로, 상태머신 가정 등)을 빠짐없이 나열"},
    },
    "required": ["id", "service", "steps", "notes"],
    "additionalProperties": False,
}


def list_catalog_services() -> list[str]:
    """Sorted 'category/service' choices for the A2 picker."""
    try:
        cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted({f"{e.get('category')}/{e.get('service')}" for e in cat
                   if e.get("category") and e.get("service")})


def _service_endpoints(service: str) -> list[dict]:
    try:
        cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"key": e.get("key"), "method": e.get("method"),
             "path": e.get("http_path"), "name": e.get("name")}
            for e in cat
            if f"{e.get('category')}/{e.get('service')}" == service]


def _service_bodies(service: str) -> dict:
    try:
        bodies = json.loads(BODIES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in bodies.items() if k.startswith(service + "/")}


def _service_knowledge(service: str) -> str:
    """knowledge/formal/services/<cat>__<svc>.yaml, if it exists."""
    path = ROOT / "knowledge" / "formal" / "services" / \
        (service.replace("/", "__") + ".yaml")
    try:
        return path.read_text(encoding="utf-8")[:8000]
    except OSError:
        return ""


def _facts_excerpt(service: str) -> str:
    """validated-facts.md lines mentioning the service (short name included)."""
    try:
        text = (ROOT / "knowledge" / "validated-facts.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    short = service.split("/")[-1]
    lines = [ln for ln in text.splitlines()
             if short and short.lower() in ln.lower()]
    return "\n".join(lines[:40])


def _few_shot_lifecycles() -> list[dict]:
    """Two proven lifecycles: resourcemanager-resource-group (base) + the
    first enabled fragment lifecycle (one full real-world example each)."""
    shots: list[dict] = []
    try:
        base = json.loads((ROOT / "regression" / "scenarios" /
                           "scenarios.json").read_text(encoding="utf-8"))
        for lc in base.get("lifecycles", []):
            if lc.get("id") == "resourcemanager-resource-group":
                shots.append(lc)
                break
    except (OSError, ValueError):
        pass
    frag_dir = ROOT / "regression" / "scenarios" / "lifecycles"
    if frag_dir.is_dir():
        for frag in sorted(frag_dir.glob("*.json")):
            try:
                data = json.loads(frag.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            ls = data.get("lifecycles", data if isinstance(data, list) else [])
            for lc in ls:
                if lc.get("enabled") and lc.get("steps"):
                    shots.append(lc)
                    break
            if len(shots) >= 2:
                break
    return shots[:2]


def _to_engine_shape(draft: dict) -> dict:
    """Convert the A2 structured output into the engine lifecycle shape.
    enabled is ALWAYS False — a human flips it after review."""
    steps = []
    for s in draft.get("steps", []):
        step: dict = {"name": s.get("name", ""), "method": s.get("method", ""),
                      "path": s.get("path", "")}
        body = s.get("json_body") or ""
        if body.strip():
            try:
                step["json"] = json.loads(body)
            except ValueError:
                step["json"] = {"_unparsed": body}
        if s.get("expect_status"):
            step["expect_status"] = s["expect_status"]
        cap = {c["var"]: c["path"] for c in s.get("capture", [])
               if c.get("var") and c.get("path")}
        if cap:
            step["capture"] = cap
        poll = s.get("poll_json") or ""
        if poll.strip():
            try:
                step["poll"] = json.loads(poll)
            except ValueError:
                step["poll"] = {"_unparsed": poll}
        if s.get("destructive"):
            step["destructive"] = True
        if s.get("optional"):
            step["optional"] = True
        steps.append(step)
    return {"id": draft.get("id", ""), "service": draft.get("service", ""),
            "enabled": False, "steps": steps}


_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _norm_path(path: str) -> tuple[str, ...]:
    """'/v1/resource-groups/{rg_id}' -> ('v1','resource-groups','*') so a
    draft path matches the catalog path regardless of placeholder names."""
    parts = [p for p in (path or "").split("?")[0].split("/") if p]
    return tuple("*" if "{" in p else p for p in parts)


def validate_lifecycle_draft(lifecycle: dict, service: str) -> list[str]:
    """Mechanical validation of an A2 draft. Returns problem strings ([] = ok).

    Checks: every step path exists in the service's catalog; every placeholder
    is an engine global or captured by an EARLIER step; a destructive teardown
    step exists and the last API step is destructive."""
    problems: list[str] = []
    catalog = {( (e.get("method") or "").upper(), _norm_path(e.get("path") or ""))
               for e in _service_endpoints(service)}

    defined = set(ENGINE_GLOBALS)
    steps = lifecycle.get("steps") or []
    if not steps:
        return ["steps가 비어 있습니다"]
    last_api_step = None
    has_destructive = False
    for i, step in enumerate(steps):
        label = f"step[{i}] '{step.get('name', '')}'"
        method = (step.get("method") or "").upper()
        path = step.get("path") or ""
        if method and path:
            last_api_step = step
            if catalog and (method, _norm_path(path)) not in catalog:
                problems.append(
                    f"{label}: {method} {path} — 카탈로그({service})에 없는 경로")
        # placeholders used in path + body must already be defined
        used = set(_PLACEHOLDER_RE.findall(path))
        if step.get("json") is not None:
            used |= set(_PLACEHOLDER_RE.findall(
                json.dumps(step["json"], ensure_ascii=False)))
        undefined = used - defined
        if undefined:
            problems.append(
                f"{label}: 정의 전에 참조된 placeholder {sorted(undefined)} "
                "(이전 step의 capture에 없음)")
        for var in (step.get("capture") or {}):
            defined.add(var)
        for var in (step.get("capture_soft") or {}):
            defined.add(var)
        if step.get("destructive"):
            has_destructive = True
    if not has_destructive:
        problems.append("destructive teardown step이 없습니다 — 생성한 리소스를 "
                        "지우는 마지막 DELETE step이 필요합니다")
    elif last_api_step is not None and not last_api_step.get("destructive"):
        problems.append("마지막 API step이 destructive가 아닙니다 — teardown은 "
                        "맨 끝에 와야 합니다")
    return problems


def scenario_draft(service: str, *, save: bool = True) -> dict:
    """A2: draft a lifecycle JSON for ``service`` ('category/service')."""
    endpoints = _service_endpoints(service)
    if not endpoints:
        raise ValueError(f"카탈로그에 없는 서비스: {service!r}")

    ai, ai_error, lifecycle, notes, problems = None, "", None, [], []
    if enabled():
        system = (
            "You are the scenario-draft author of the SCP API Regression Test "
            "Platform. Draft ONE CRUD lifecycle (create -> read/list -> update "
            "-> delete) for the given Samsung Cloud Platform service, in the "
            "exact JSON shape requested. Ground every path in the provided "
            "catalog; prefer the provided request-body templates over guessing; "
            "honour the service knowledge (capture paths, state machines, "
            "constraints). Use placeholders like {unique} (engine-seeded) and "
            "{my_id} (captured by an earlier step). Capture the created "
            "resource's id in the create step; poll state when the knowledge "
            "names a state machine; the FINAL step must be the destructive "
            "DELETE of what was created. Keep it minimal and safe — no "
            "org-level or irreversible operations. List EVERY uncertainty "
            "(guessed body fields, unverified capture paths) in notes, "
            "in Korean.")
        ctx = {
            "service": service,
            "catalog_endpoints": endpoints,
            "request_body_templates": _service_bodies(service),
            "service_knowledge_yaml": _service_knowledge(service),
            "validated_facts_excerpt": _facts_excerpt(service),
            "few_shot_good_lifecycles": _few_shot_lifecycles(),
        }
        user = json.dumps(ctx, ensure_ascii=False)
        ai, ai_error = _ask(system, user, A2_SCHEMA, max_tokens=32000)
        if ai:
            notes = ai.get("notes", [])
            lifecycle = _to_engine_shape(ai)
            lifecycle["service"] = service  # never trust the echo
            problems = validate_lifecycle_draft(lifecycle, service)
    else:
        ai_error = "ANTHROPIC_API_KEY 미설정 — AI 비활성 (초안 생성 불가)"

    result = {
        "kind": "scenario-draft",
        "created": _now_iso(),
        "service": service,
        "lifecycle": lifecycle,
        "notes": notes,
        "validation_problems": problems,
        "ai_error": ai_error,
        "review_hint": ("검토 후 regression/scenarios/lifecycles/"
                        f"{service.replace('/', '__')}.json 의 lifecycles 배열에 "
                        "추가하고, 검증이 끝나면 enabled를 true로 바꾸세요. "
                        "이 초안은 절대 자동 활성화되지 않습니다."),
    }
    if save:
        slug = service.replace("/", "-")
        result["draft_name"] = save_draft(f"lifecycle-{slug}-{_ts()}", result)
    return result


# =================================================================================
# A3 — validated-fact 추출
# =================================================================================

# What observations actually contain (core/results.py Observation):
#   endpoint_key, method, path, status, category(ok|soft|fail), elapsed_ms,
#   source(smoke|read_chain|crud_probe), note, run, ts.
# They do NOT carry request bodies, response bodies, or capture/id paths —
# so the extractable truth is: WHICH endpoints answered 2xx (proving the
# scenario's request body/ordering works) plus whatever the note says.
# Anything beyond that is at most "probable", and the prompt says so.
A3_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "fact": {"type": "string", "description": "한국어 한 문장"},
                    "evidence": {"type": "string",
                                 "description": "endpoint + status, e.g. "
                                                "'POST /v1/vpcs -> 201'"},
                    "confidence": {"type": "string",
                                   "enum": ["validated", "probable"]},
                },
                "required": ["service", "fact", "evidence", "confidence"],
                "additionalProperties": False,
            },
        },
        "formal_yaml_suggestions": {
            "type": "string",
            "description": "knowledge/formal/services/ 스키마(FORMAT.md)에 맞는 "
                           "YAML 스니펫 — 추가/갱신할 항목만",
        },
    },
    "required": ["facts", "formal_yaml_suggestions"],
    "additionalProperties": False,
}

_MAX_OBS_LINES = 150


def successful_observations(gh_run_id: str) -> list[dict]:
    """The run's 2xx observations — the only rows that carry positive truth."""
    from controlplane import snapshots
    out = []
    for obs in snapshots.observations(gh_run_id):
        try:
            status = int(obs.get("status") or 0)
        except (TypeError, ValueError):
            continue
        if 200 <= status < 300:
            out.append(obs)
    return out


def extract_facts(gh_run_id: str, *, save: bool = True) -> dict:
    """A3: distill validated-fact candidates from a finished run's 2xx rows."""
    obs = successful_observations(gh_run_id)
    if not obs:
        raise ValueError(f"run {gh_run_id}: 2xx observation이 없습니다 "
                         "(스냅샷 미보관 또는 성공 호출 없음)")

    # compact per-endpoint rollup: service, method+path, statuses, sources, note
    rollup: dict[str, dict] = {}
    for o in obs:
        key = o.get("endpoint_key") or f"{o.get('method')} {o.get('path')}"
        r = rollup.setdefault(key, {
            "endpoint_key": key, "method": o.get("method"),
            "path": o.get("path"), "statuses": set(), "sources": set(),
            "notes": set()})
        r["statuses"].add(o.get("status"))
        if o.get("source"):
            r["sources"].add(o["source"])
        note = str(o.get("note") or "").strip()
        if note:
            r["notes"].add(note[:200])
    lines = []
    for key in sorted(rollup)[:_MAX_OBS_LINES]:
        r = rollup[key]
        lines.append(json.dumps({
            "endpoint_key": r["endpoint_key"], "method": r["method"],
            "path": r["path"], "statuses": sorted(r["statuses"], key=str),
            "sources": sorted(r["sources"]), "notes": sorted(r["notes"])[:3],
        }, ensure_ascii=False))

    ai, ai_error = None, ""
    if enabled():
        system = (
            "You are the knowledge-curation agent of the SCP API Regression "
            "Test Platform. You receive the 2xx (successful) observations of "
            "one finished regression run against Samsung Cloud Platform. "
            "BE HONEST ABOUT LIMITS: observations carry only endpoint_key, "
            "method, path, status, source and a short note — NOT request "
            "bodies, response bodies, or id/capture paths. So a 'validated' "
            "fact is one the data directly proves: this endpoint, called by "
            "the named scenario source, answered 2xx (i.e. the scenario's "
            "request body and step ordering work against the live API). Facts "
            "you infer beyond that (e.g. a state machine implied by repeated "
            "polling reads) must be 'probable'. Do NOT invent capture paths "
            "or body fields. formal_yaml_suggestions must follow the "
            "knowledge/formal FORMAT (version/service/constraints/captures/"
            "states/quirks with provenance VALIDATED or docs) and contain "
            "only what the observations support. Write facts in Korean.")
        user = (f"Run {gh_run_id} — 2xx observations "
                f"({len(obs)} rows, {len(rollup)} endpoints, "
                f"showing {min(len(rollup), _MAX_OBS_LINES)}):\n"
                + "\n".join(lines))
        ai, ai_error = _ask(system, user, A3_SCHEMA)
    else:
        ai_error = "ANTHROPIC_API_KEY 미설정 — AI 비활성 (fact 추출 불가)"

    result = {
        "kind": "facts",
        "created": _now_iso(),
        "run_id": gh_run_id,
        "observation_count": len(obs),
        "endpoint_count": len(rollup),
        "ai": ai,
        "ai_error": ai_error,
        "review_hint": ("facts는 knowledge/validated-facts.md에, YAML 제안은 "
                        "knowledge/formal/services/ 에 사람이 검토 후 반영하세요 "
                        "(python knowledge/formal/validate.py 통과 필수)."),
    }
    if save:
        result["draft_name"] = save_draft(f"facts-{gh_run_id}", result)
    return result
