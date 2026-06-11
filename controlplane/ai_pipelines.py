"""AI authoring pipelines (PLATFORM-PLAN §4-A1/A2/A3, RESOURCE-MODEL-PLAN R2c).

Four pipelines, all OUTSIDE the test hot path, all producing reviewable
DRAFT FILES under drafts/ (never auto-enabled, never auto-merged):

  A1 spec_impact(...)    spec diff (mechanical, spec/diff.py) + Claude impact
                         analysis -> rerun scope suggestion.
  A2 scenario_draft(...) one service's catalog + bodies + knowledge + two
                         proven lifecycles as few-shot -> lifecycle JSON draft
                         (enabled:false ALWAYS) + mechanical validation.
  A3 extract_facts(...)  a finished run's 2xx observations -> validated-fact
                         candidates + formal-YAML suggestion.
  R2c task_draft(...)    one UNMODELED service's catalog + bodies + knowledge
                         + cross-service requires edges -> resource TASK
                         DEFINITION draft in the RESOURCE-MODEL-PLAN §1 schema
                         (provenance 'docs' ALWAYS, contract C5), saved as
                         drafts/taskdef-<cat>__<svc>-<ts>.yaml (contract C4).
                         NEVER written into knowledge/formal/resources/ — a
                         human moves it there after review.

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


# =================================================================================
# R2c — 자원 task 정의 초안 (RESOURCE-MODEL-PLAN §1 schema, 커버리지 경로 2)
# =================================================================================

RESOURCES_DIR = ROOT / "knowledge" / "formal" / "resources"
CROSS_SERVICE_PATH = ROOT / "knowledge" / "formal" / "cross-service.yaml"

# Structured outputs forbid free-form objects / union types, so the §1 shapes
# travel flattened: requires entries are fixed-field objects (ref/count/one_of/
# use, mechanically folded back into `str | {ref,count} | {one_of}`), options
# are an array of typed rows, and the arbitrary request body / ready spec are
# JSON-encoded strings (A2 precedent).
_TD_REQUIRE = {
    "type": "object",
    "properties": {
        "ref": {"type": "string",
                "description": "prerequisite node id; '' when one_of is used"},
        "count": {"type": "integer",
                  "description": "multiplicity (vpc-peering needs vpc x2); 1 normally"},
        "one_of": {"type": "array", "items": {"type": "string"},
                   "description": "OR-dependency node ids; [] normally"},
        "use": {"type": "string",
                "description": "which captured output of ref is consumed (e.g. 'ip'); '' normally"},
    },
    "required": ["ref", "count", "one_of", "use"],
    "additionalProperties": False,
}
_TD_OPTION = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": ["cidr", "enum", "ref", "string"]},
        "required": {"type": "boolean"},
        "vary": {"type": "boolean",
                 "description": "true = C4 variation target (enum worth combinatorial coverage)"},
        "values": {"type": "array", "items": {"type": "string"},
                   "description": "enum values; [] for other types"},
        "target": {"type": "string",
                   "description": "target node id for type:ref; '' otherwise"},
        "pick": {"type": "string",
                 "description": "value-picking rule, e.g. unique-block / sub-block-of; '' if none"},
        "of": {"type": "string",
               "description": "source for pick, e.g. vpc.cidr; '' if none"},
        "note": {"type": "string", "description": "한국어 비고; '' if none"},
    },
    "required": ["name", "type", "required", "vary", "values", "target",
                 "pick", "of", "note"],
    "additionalProperties": False,
}
TD_SCHEMA = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string",
                           "description": "graph node id (cross-service.yaml key style, "
                                          "e.g. 'queue', 'lb-server-group')"},
                    "code": {"type": "string",
                             "description": "human group code guess like '001-007-a'; '' if unknown"},
                    "requires": {"type": "array", "items": _TD_REQUIRE},
                    "create_endpoint": {"type": "string",
                                        "description": "'METHOD /path' exactly as in the catalog"},
                    "create_body_json": {"type": "string",
                                         "description": "request body template as a JSON object "
                                                        "encoded as a string; '' if none"},
                    "options": {"type": "array", "items": _TD_OPTION},
                    "capture": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"var": {"type": "string"},
                                           "path": {"type": "string"}},
                            "required": ["var", "path"],
                            "additionalProperties": False,
                        },
                        "description": "where the created resource's id lives, "
                                       "e.g. var=queue_id path=$.id",
                    },
                    "ready_json": {"type": "string",
                                   "description": "readiness poll as JSON string, e.g. "
                                                  '{"field":"$.state","until":"ACTIVE","timeout":600}'
                                                  "; '' if none"},
                    "delete_endpoint": {"type": "string",
                                        "description": "'DELETE /path' from the catalog; '' if unknown"},
                    "quota": {"type": "string",
                              "description": "quota kind guess (core/budgets key); '' if none"},
                },
                "required": ["id", "code", "requires", "create_endpoint",
                             "create_body_json", "options", "capture",
                             "ready_json", "delete_endpoint", "quota"],
                "additionalProperties": False,
            },
        },
        "uncertainties": {
            "type": "array", "items": {"type": "string"},
            "description": "한국어: 근거 없이 추정한 모든 것(추정 body 필드, 미검증 capture "
                           "경로, 불확실한 전제조건/quota/상태머신)을 빠짐없이 나열 — 필수",
        },
    },
    "required": ["resources", "uncertainties"],
    "additionalProperties": False,
}


def _load_yaml(path: Path) -> dict:
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cross_service_resources() -> dict:
    """cross-service.yaml resources: {node_id: {service, requires, ...}}."""
    return _load_yaml(CROSS_SERVICE_PATH).get("resources") or {}


def _resources_dir(resources_dir: str | Path | None) -> Path:
    return Path(resources_dir) if resources_dir else RESOURCES_DIR


def known_resource_nodes(resources_dir: str | Path | None = None) -> set[str]:
    """Node ids a draft may legally reference: cross-service.yaml resources +
    every node in knowledge/formal/resources/*.yaml (when R1 has landed)."""
    known = set(_cross_service_resources())
    rdir = _resources_dir(resources_dir)
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.yaml")):
            if p.name.startswith("_"):
                continue
            known |= set(_load_yaml(p).get("resources") or {})
    return known


def modeled_services(resources_dir: str | Path | None = None) -> set[str]:
    """'category/service' values that already have a resource model: a
    <cat>__<svc>.yaml file in resources/ OR any node declaring that service."""
    out: set[str] = set()
    rdir = _resources_dir(resources_dir)
    if not rdir.is_dir():
        return out
    for p in sorted(rdir.glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        if "__" in p.stem:
            out.add(p.stem.replace("__", "/", 1))
        for node in (_load_yaml(p).get("resources") or {}).values():
            svc = (node or {}).get("service") if isinstance(node, dict) else None
            if svc:
                out.add(svc)
    return out


def model_gap_services(resources_dir: str | Path | None = None) -> list[str]:
    """모델 공백 — catalog services with NO resources-model file yet (this
    pipeline's work queue). Pure mechanical."""
    done = modeled_services(resources_dir)
    return [s for s in list_catalog_services() if s not in done]


def _cross_edges(service: str) -> dict:
    """cross-service.yaml requires edges touching ``service``: the service's
    own nodes + every node that requires one of them."""
    resources = _cross_service_resources()
    mine = {nid for nid, n in resources.items()
            if isinstance(n, dict) and n.get("service") == service}
    edges: dict[str, dict] = {}
    for nid, n in resources.items():
        if not isinstance(n, dict):
            continue
        req_ids: set[str] = set()
        for r in n.get("requires") or []:
            if isinstance(r, str):
                req_ids.add(r)
            elif isinstance(r, dict):
                if r.get("ref"):
                    req_ids.add(str(r["ref"]))
                for m in r.get("one_of") or []:
                    req_ids.add(m if isinstance(m, str)
                                else str((m or {}).get("ref", "")))
        if nid in mine or (req_ids & mine):
            edges[nid] = {"service": n.get("service", ""),
                          "requires": n.get("requires") or [],
                          "notes": n.get("notes", "")}
    return edges


def gather_task_context(service: str,
                        resources_dir: str | Path | None = None) -> dict:
    """Mechanical context for one 'category/service' — everything the prompt
    (and the keyless page) shows. Raises ValueError for unknown services."""
    endpoints = _service_endpoints(service)
    if not endpoints:
        raise ValueError(f"카탈로그에 없는 서비스: {service!r}")
    return {
        "service": service,
        "catalog_endpoints": endpoints,
        "request_body_templates": _service_bodies(service),
        "service_knowledge_yaml": _service_knowledge(service),
        "cross_service_edges": _cross_edges(service),
        "known_node_ids": sorted(known_resource_nodes(resources_dir)),
        "validated_facts_excerpt": _facts_excerpt(service),
        "already_modeled": service in modeled_services(resources_dir),
    }


# --- §1-shape conversion -----------------------------------------------------------

def _fold_requires(items: list) -> list:
    """Flattened require rows -> §1 shape: str | {ref,count[,use]} | {one_of}."""
    out = []
    for r in items or []:
        one_of = [m for m in (r.get("one_of") or []) if m]
        if one_of:
            out.append({"one_of": one_of})
            continue
        ref = (r.get("ref") or "").strip()
        if not ref:
            continue
        count = int(r.get("count") or 1)
        use = (r.get("use") or "").strip()
        if count > 1 or use:
            entry: dict = {"ref": ref}
            if count > 1:
                entry["count"] = count
            if use:
                entry["use"] = use
            out.append(entry)
        else:
            out.append(ref)
    return out


def _fold_options(rows: list) -> dict:
    """Flattened option rows -> §1 options map {name: {type, required, ...}}."""
    out: dict[str, dict] = {}
    for o in rows or []:
        name = (o.get("name") or "").strip()
        if not name:
            continue
        spec: dict = {"type": o.get("type") or "string",
                      "required": bool(o.get("required"))}
        if o.get("vary"):
            spec["vary"] = True
        if o.get("values"):
            spec["values"] = list(o["values"])
        if o.get("target"):
            spec["target"] = o["target"]
        if o.get("pick"):
            spec["pick"] = o["pick"]
        if o.get("of"):
            spec["of"] = o["of"]
        if o.get("note"):
            spec["note"] = o["note"]
        out[name] = spec
    return out


def _parse_json_field(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"_unparsed": text}
    except ValueError:
        return {"_unparsed": text}


def to_task_model(ai: dict, service: str) -> dict:
    """Structured output -> §1 resources map. provenance is forced to 'docs'
    (contract C5) — the model is never trusted to claim VALIDATED."""
    model: dict[str, dict] = {}
    for r in ai.get("resources") or []:
        nid = (r.get("id") or "").strip()
        if not nid:
            continue
        create: dict = {"endpoint": (r.get("create_endpoint") or "").strip()}
        body = _parse_json_field(r.get("create_body_json") or "")
        if body is not None:
            create["body"] = body
        options = _fold_options(r.get("options"))
        if options:
            create["options"] = options
        task: dict = {
            "code": r.get("code") or "",
            "service": service,
            "requires": _fold_requires(r.get("requires")),
            "create": create,
        }
        capture = {c["var"]: c["path"] for c in (r.get("capture") or [])
                   if c.get("var") and c.get("path")}
        if capture:
            task["capture"] = capture
        ready = _parse_json_field(r.get("ready_json") or "")
        if ready is not None:
            task["ready"] = ready
        delete = (r.get("delete_endpoint") or "").strip()
        if delete:
            task["delete"] = {"endpoint": delete, "destructive": True}
        if r.get("quota"):
            task["quota"] = r["quota"]
        task["provenance"] = "docs"  # C5: AI 초안은 항상 docs
        model[nid] = task
    return model


# --- mechanical post-validation ------------------------------------------------------

_ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)$")
_JSONPATH_RE = re.compile(r"^\$(\.[A-Za-z0-9_-]+(\[\d+\])?)+$")


def _ref_known(ref: str, known: set[str]) -> bool:
    return ref in known


def validate_task_draft(model: dict, service: str,
                        known: set[str]) -> tuple[list[str], list[str]]:
    """Mechanical post-validation of a §1-shape draft. Returns
    (problems, demotions) and MUTATES ``model``:

    - create/delete endpoints must parse as 'METHOD /path' AND exist in the
      service's catalog -> problem strings otherwise (rejected, human fixes).
    - capture jsonpaths must be syntactically valid -> problem strings.
    - every requires ref / one_of member / option type:ref target must name a
      known node id (cross-service + resources dir + this draft itself);
      unknown refs are REMOVED from the model and returned as demotion
      strings (appended to uncertainties) — never silently kept.
    """
    problems: list[str] = []
    demoted: list[str] = []
    catalog = {((e.get("method") or "").upper(), _norm_path(e.get("path") or ""))
               for e in _service_endpoints(service)}
    known = set(known) | set(model)   # the draft's own nodes are valid refs

    def _check_endpoint(label: str, endpoint: str, *, required: bool):
        if not endpoint:
            if required:
                problems.append(f"{label}: create.endpoint가 비어 있습니다")
            return
        m = _ENDPOINT_RE.match(endpoint)
        if not m:
            problems.append(f"{label}: {endpoint!r} — 'METHOD /path' 형식이 아닙니다")
            return
        if catalog and (m.group(1), _norm_path(m.group(2))) not in catalog:
            problems.append(
                f"{label}: {endpoint} — 카탈로그({service})에 없는 endpoint")

    for nid, task in model.items():
        create = task.get("create") or {}
        _check_endpoint(f"{nid}.create", create.get("endpoint") or "",
                        required=True)
        delete = task.get("delete") or {}
        if delete:
            _check_endpoint(f"{nid}.delete", delete.get("endpoint") or "",
                            required=False)
        for var, path in (task.get("capture") or {}).items():
            if not _JSONPATH_RE.match(str(path)):
                problems.append(f"{nid}.capture.{var}: {path!r} — jsonpath "
                                "문법이 아닙니다 (예: $.vpc.id, $.servers[0].id)")

        kept_requires = []
        for req in task.get("requires") or []:
            if isinstance(req, str):
                if _ref_known(req, known):
                    kept_requires.append(req)
                else:
                    demoted.append(f"{nid}: requires '{req}' — 알 수 없는 노드 "
                                   "id여서 모델에서 제거함 (사람이 노드를 먼저 "
                                   "정의하거나 id를 수정해야 함)")
            elif isinstance(req, dict) and "one_of" in req:
                kept = [m for m in req["one_of"] if _ref_known(m, known)]
                dropped = [m for m in req["one_of"] if m not in kept]
                for m in dropped:
                    demoted.append(f"{nid}: requires one_of 멤버 '{m}' — 알 수 "
                                   "없는 노드 id여서 제거함")
                if kept:
                    kept_requires.append({"one_of": kept})
            elif isinstance(req, dict) and req.get("ref"):
                if _ref_known(req["ref"], known):
                    kept_requires.append(req)
                else:
                    demoted.append(f"{nid}: requires '{req['ref']}' "
                                   f"(count={req.get('count', 1)}) — 알 수 없는 "
                                   "노드 id여서 모델에서 제거함")
        task["requires"] = kept_requires

        options = (task.get("create") or {}).get("options") or {}
        for name in list(options):
            spec = options[name]
            if spec.get("type") == "ref" and \
                    not _ref_known(spec.get("target") or "", known):
                demoted.append(f"{nid}: option '{name}' (ref → "
                               f"{spec.get('target')!r}) — 알 수 없는 대상 "
                               "노드여서 옵션을 제거함")
                del options[name]
    return problems, demoted


# --- yaml draft output (contract C4) ---------------------------------------------------

_TASKDEF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.yaml$")


def task_yaml_text(model: dict, service: str) -> str:
    """The reviewable §1 yaml — what a human moves into
    knowledge/formal/resources/<cat>__<svc>.yaml after review."""
    import yaml
    header = (f"# DRAFT — knowledge/formal/resources/"
              f"{service.replace('/', '__')}.yaml 후보 (AI 초안, 사람 검토 전)\n"
              f"# Schema: docs/RESOURCE-MODEL-PLAN.md §1 · provenance는 항상 "
              f"docs (C5)\n# 검토/수정 후 위 경로로 옮기고 validator를 "
              f"통과시키세요. 자동 반영 금지.\n")
    return header + yaml.safe_dump({"version": 1, "resources": model},
                                   allow_unicode=True, sort_keys=False,
                                   default_flow_style=False)


def save_taskdef_yaml(stem: str, text: str) -> str:
    """Write the yaml draft next to the JSON envelope; returns the file name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "taskdef"
    name = f"{stem}.yaml"
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFTS_DIR / name).write_text(text, encoding="utf-8")
    return name


def taskdef_yaml_path(name: str) -> Path | None:
    """Guarded path of one yaml draft (same rules as draft_path)."""
    if not _TASKDEF_NAME_RE.match(name or ""):
        return None
    try:
        path = (DRAFTS_DIR / name).resolve()
        path.relative_to(DRAFTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


# --- view helpers + the pipeline --------------------------------------------------------

def _requires_display(req) -> str:
    if isinstance(req, str):
        return req
    if isinstance(req, dict) and "one_of" in req:
        return "one of: " + " | ".join(req["one_of"])
    if isinstance(req, dict) and req.get("ref"):
        s = req["ref"]
        if req.get("count", 1) > 1:
            s += f" ×{req['count']}"
        if req.get("use"):
            s += f" (use: {req['use']})"
        return s
    return json.dumps(req, ensure_ascii=False)


def _resource_views(model: dict) -> list[dict]:
    """Template-friendly per-resource rows (the raw yaml stays authoritative)."""
    views = []
    for nid, task in model.items():
        create = task.get("create") or {}
        options = [{"name": k, **v} for k, v in (create.get("options") or {}).items()]
        views.append({
            "id": nid,
            "code": task.get("code") or "",
            "requires": [_requires_display(r) for r in task.get("requires") or []],
            "create_endpoint": create.get("endpoint") or "",
            "options": options,
            "capture": task.get("capture") or {},
            "ready": json.dumps(task["ready"], ensure_ascii=False)
                     if task.get("ready") else "",
            "delete_endpoint": (task.get("delete") or {}).get("endpoint", ""),
            "quota": task.get("quota") or "",
        })
    return views


def task_draft(category_service: str, *, save: bool = True,
               resources_dir: str | Path | None = None) -> dict:
    """R2c: draft resource TASK DEFINITIONS (§1 schema) for one unmodeled
    'category/service'. Mechanical context always; Claude layer when enabled."""
    service = category_service.strip()
    ctx = gather_task_context(service, resources_dir)

    ai, ai_error = None, ""
    model: dict | None = None
    uncertainties: list[str] = []
    problems: list[str] = []
    yaml_text = ""
    if enabled():
        system = (
            "You are the resource-task-model draft author of the SCP API "
            "Regression Test Platform. For ONE Samsung Cloud Platform service "
            "you draft RESOURCE TASK DEFINITIONS — the platform's resource "
            "model (RESOURCE-MODEL-PLAN §1): for each resource the service "
            "exposes, its minimal prerequisites (requires) and its create-time "
            "options, so a deterministic composer can later generate test "
            "scenarios from the dependency graph. Ground every endpoint in the "
            "provided catalog and write it EXACTLY as 'METHOD /path' from the "
            "catalog; prefer the provided request-body templates over "
            "guessing; honour the service knowledge YAML and validated facts "
            "(capture paths, state machines, constraints). requires must "
            "reference node ids — ONLY ids from known_node_ids (cross-service "
            "prerequisites) or ids of resources you define in this draft; "
            "NEVER invent other ids. Express OR-dependencies with one_of, "
            "multiplicity with count, and which captured output is consumed "
            "with use. options describe what a caller may choose at create "
            "time (type cidr|enum|ref|string; required; vary:true marks enum "
            "options worth combinatorial variation; type:ref options point at "
            "a node id in target). Body placeholders: {unique}/{ualpha} are "
            "engine-seeded, {opt.<name>} injects an option value, "
            "{<node>.<var>} injects a prerequisite's captured value. Add "
            "capture (where the created id lives) and ready (state polling) "
            "ONLY when the knowledge or templates support them. This is a "
            "DRAFT from documentation: provenance is recorded as 'docs' and a "
            "human reviews everything. BE HONEST ABOUT LIMITS like the "
            "fact-curation agent: list EVERY ungrounded assumption (guessed "
            "body fields, unverified capture paths, uncertain prerequisites, "
            "quota guesses, state machines you inferred) in uncertainties, in "
            "Korean — an empty uncertainties list on a docs-only draft is "
            "almost certainly dishonest.")
        user = json.dumps({k: v for k, v in ctx.items()
                           if k != "already_modeled"}, ensure_ascii=False)
        ai, ai_error = _ask(system, user, TD_SCHEMA, max_tokens=32000)
        if ai:
            uncertainties = [str(u) for u in ai.get("uncertainties") or []]
            model = to_task_model(ai, service)
            problems, demoted = validate_task_draft(
                model, service, set(ctx["known_node_ids"]))
            uncertainties += demoted
            if model:
                yaml_text = task_yaml_text(model, service)
            else:
                problems.append("AI가 resource 노드를 하나도 만들지 못했습니다")
    else:
        ai_error = ("ANTHROPIC_API_KEY 미설정 — AI 비활성 (초안 생성 불가; "
                    "mechanical 컨텍스트만 표시)")

    result = {
        "kind": "taskdef",
        "created": _now_iso(),
        "service": service,
        "already_modeled": ctx["already_modeled"],
        "context": {
            "endpoints": ctx["catalog_endpoints"],
            "body_template_keys": sorted(ctx["request_body_templates"]),
            "knowledge_present": bool(ctx["service_knowledge_yaml"]),
            "knowledge_excerpt": ctx["service_knowledge_yaml"][:2000],
            "cross_service_edges": ctx["cross_service_edges"],
            "known_node_count": len(ctx["known_node_ids"]),
            "facts_excerpt": ctx["validated_facts_excerpt"],
        },
        "model": model,
        "resource_views": _resource_views(model) if model else [],
        "uncertainties": uncertainties,
        "validation_problems": problems,
        "yaml": yaml_text,
        "ai_error": ai_error,
        "review_hint": ("검토 후 knowledge/formal/resources/"
                        f"{service.replace('/', '__')}.yaml 로 직접 옮기세요 "
                        "(validator 통과 필수). provenance는 docs로 유지 — "
                        "scoped 라이브 run을 통과해야 VALIDATED로 승격됩니다. "
                        "이 초안은 절대 자동으로 모델에 반영되지 않습니다."),
    }
    if save:
        stem = f"taskdef-{service.replace('/', '__')}-{_ts()}"
        if yaml_text:
            result["yaml_name"] = save_taskdef_yaml(stem, yaml_text)
        result["draft_name"] = save_draft(stem, result)
    return result
