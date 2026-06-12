#!/usr/bin/env python3
"""Back-extract resource task DRAFTS from the hand-written lifecycles (R1).

docs/RESOURCE-MODEL-PLAN.md §4: the initial values of the resource-task model
are *extracted* from the 128 live-validated lifecycles, not guessed.  This
tool reads the merged lifecycle set (regression.scenarios.loader — base
scenarios.json + lifecycles/*.json) and, for every create step that captures
a resource id, proposes a §1 task definition:

  * endpoint + body template — verbatim, with engine builtins like
    {unique}/{ualpha} kept untouched and captured-var references rewritten to
    composer tokens ({vpc_id} consumed by a later create -> {vpc.vpc_id});
  * capture / poll->ready / the matching DELETE teardown (matched by usage of
    the captured id in the delete path);
  * requires — inferred from which other nodes' captures the create's
    path/body consume;
  * provenance — VALIDATED when the source lifecycle is enabled (contract
    C5: live-validated origin), docs otherwise or when only a soft capture
    exists;
  * source — {lifecycle, steps} so a human can audit the extraction.

Output is ALWAYS a draft: curated files in knowledge/formal/resources/*.yaml
are never overwritten.  Drafts go to ``_drafts/<category>__<service>.draft
.yaml`` next to this script (the leading-underscore directory is invisible to
composer.load_model(), the formal validator and the UI loader) or to stdout.

Usage (from the repo root):

    PYTHONPATH=. python3 knowledge/formal/resources/extract.py            # all
    PYTHONPATH=. python3 knowledge/formal/resources/extract.py \
        --service networking/vpc                                          # one service
    PYTHONPATH=. python3 knowledge/formal/resources/extract.py \
        --category networking --stdout                                    # print only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

DRAFTS_DIR = HERE / "_drafts"
_VAR_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def _builtins() -> set[str]:
    from regression.scenarios.validate import BUILTINS
    return set(BUILTINS)


def _cross_service_keys() -> set[str]:
    """Node keys of cross-service.yaml — used to normalise derived ids
    (loadbalancer_id -> load-balancer, publicip_id -> public-ip)."""
    try:
        import yaml
        data = yaml.safe_load(
            (HERE.parent / "cross-service.yaml").read_text(encoding="utf-8"))
        return set((data or {}).get("resources") or {})
    except Exception:
        return set()


def _quota_kinds_by_lifecycle() -> dict[str, list[str]]:
    try:
        deps = json.loads(
            (ROOT / "regression" / "scenarios" / "dependencies.json")
            .read_text(encoding="utf-8"))
        return dict(deps.get("quota_kinds") or {})
    except (OSError, ValueError):
        return {}


def derive_node_id(primary_var: str, cross_keys: set[str]) -> str:
    """Capture var -> graph node id (vpc_id -> vpc; loadbalancer_id ->
    load-balancer when cross-service spells it that way)."""
    base = primary_var
    for suffix in ("_id", "_ids"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    derived = base.replace("_", "-")
    if derived in cross_keys:
        return derived
    squashed = derived.replace("-", "")
    for key in cross_keys:
        if key.replace("-", "") == squashed:
            return key
    return derived


def _retoken(obj, var_owner: dict[str, str], own_vars: set[str],
             builtins: set[str]):
    """Rewrite {var} placeholders to composer tokens.

    - {unique}/{ualpha}/builtins -> verbatim;
    - a var captured earlier by ANOTHER node N -> {N.var};
    - own capture vars and unknown vars -> verbatim (own delete-path vars and
      forward references stay dot-less, exactly what the composer expects).
    """
    if isinstance(obj, str):
        def repl(m):
            var = m.group(1)
            if var in builtins or var in own_vars:
                return m.group(0)
            owner = var_owner.get(var)
            return "{%s.%s}" % (owner, var) if owner else m.group(0)
        return _VAR_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _retoken(v, var_owner, own_vars, builtins)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_retoken(v, var_owner, own_vars, builtins) for v in obj]
    return obj


def extract_lifecycle(lc: dict, cross_keys: set[str], builtins: set[str],
                      quota_map: dict[str, list[str]]) -> dict[str, dict]:
    """One lifecycle -> {node_id: draft task} proposals."""
    steps = lc.get("steps") or []
    service = lc.get("service") or ""
    enabled = bool(lc.get("enabled"))
    lc_quota = set(quota_map.get(lc.get("id", ""), []))

    # pass 1: where does every captured var come from?
    creations: list[dict] = []
    var_owner: dict[str, str] = {}   # var -> node id of the create that made it
    var_step: dict[str, int] = {}
    for i, step in enumerate(steps):
        if (step.get("method") or "").upper() != "POST":
            continue
        caps = step.get("capture") or {}
        soft = step.get("capture_soft") or {}
        if not caps and not soft:
            continue
        primary = next(iter(caps), None) or next(iter(soft), None)
        node = derive_node_id(primary, cross_keys)
        creations.append({"index": i, "step": step, "node": node,
                          "primary": primary, "soft_only": not caps})
        for var in list(caps) + list(soft):
            var_owner.setdefault(var, node)
            var_step.setdefault(var, i)

    drafts: dict[str, dict] = {}
    for c in creations:
        step, node, primary = c["step"], c["node"], c["primary"]
        if node in drafts:           # first creation of a node id wins
            continue
        caps = step.get("capture") or step.get("capture_soft") or {}
        own_vars = set(step.get("capture") or {}) | set(
            step.get("capture_soft") or {})
        # earlier captures only — a forward var is not a prerequisite
        earlier = {v: o for v, o in var_owner.items()
                   if var_step[v] < c["index"] and o != node}

        used = set(_VAR_RE.findall(step.get("path") or "")) \
            | set(_VAR_RE.findall(json.dumps(step.get("json") or {})))
        requires = sorted({earlier[v] for v in used if v in earlier})

        task: dict = {"service": service, "requires": requires}
        body = step.get("json")
        create: dict = {"endpoint": "%s %s" % (
            step["method"].upper(),
            _retoken(step.get("path") or "", earlier, own_vars, builtins))}
        if body is not None:
            create["body"] = _retoken(body, earlier, own_vars, builtins)
        task["create"] = create
        task["capture"] = dict(caps)

        src_steps = [step.get("name")]

        # matching delete: a later DELETE on THIS resource — its path must
        # END with the captured id (a child delete merely *contains* it).
        # Fallbacks: first later DELETE containing the id, then the create's
        # own cleanup spec.
        delete_path = None
        cands = [later for later in steps[c["index"] + 1:]
                 if (later.get("method") or "").upper() == "DELETE"
                 and primary
                 and ("{%s}" % primary) in (later.get("path") or "")]
        own_tail = [later for later in cands
                    if later["path"].rstrip("/").endswith("{%s}" % primary)]
        chosen = (own_tail or cands)[:1]
        if chosen:
            delete_path = chosen[0]["path"]
            src_steps.append(chosen[0].get("name"))
        else:
            cu = step.get("cleanup") or {}
            if cu.get("path") and primary \
                    and ("{%s}" % primary) in cu["path"]:
                delete_path = cu["path"]
        if delete_path:
            task["delete"] = {
                "endpoint": "DELETE " + _retoken(delete_path, earlier,
                                                 own_vars, builtins),
                "destructive": True}

        # poll -> ready: the next GET that polls a field on this resource
        # (ready.endpoint recorded only when it differs from the delete path,
        # which is the composer's default read derivation)
        for later in steps[c["index"] + 1:]:
            poll = later.get("poll")
            if not poll or not poll.get("field") or not poll.get("until"):
                continue
            if primary and ("{%s}" % primary) in (later.get("path") or ""):
                until = poll["until"]
                ready = {"field": poll["field"],
                         "until": until[0] if isinstance(until, list)
                         and len(until) == 1 else until}
                for k in ("timeout", "interval"):
                    if k in poll:
                        ready[k] = poll[k]
                if later.get("path") != delete_path:
                    ready["endpoint"] = "GET " + _retoken(
                        later["path"], earlier, own_vars, builtins)
                task["ready"] = ready
                src_steps.append(later.get("name"))
                break

        if node in lc_quota:
            task["quota"] = node

        # C5: back-extraction from an ENABLED lifecycle = VALIDATED; a
        # disabled source or a soft-only capture (unproven envelope) = docs
        task["provenance"] = ("VALIDATED" if enabled and not c["soft_only"]
                              else "docs")
        if c["soft_only"]:
            task["notes"] = ("extractor: capture envelope was capture_soft "
                             "in the source lifecycle (unproven) — review "
                             "before trusting")
        task["source"] = {"lifecycle": lc.get("id"),
                          "steps": [s for s in src_steps if s]}
        drafts[node] = task
    return drafts


def extract(service: str | None = None, category: str | None = None,
            lifecycle: str | None = None) -> dict[str, dict]:
    """{node_id: draft task} over the merged lifecycle set.  When a node is
    created in several lifecycles the VALIDATED (enabled, hard-capture)
    extraction wins; other sources are recorded in the note."""
    from regression.scenarios.loader import load_lifecycles

    cross_keys = _cross_service_keys()
    builtins = _builtins()
    quota_map = _quota_kinds_by_lifecycle()

    merged: dict[str, dict] = {}
    for lc in load_lifecycles():
        svc = lc.get("service") or ""
        if lifecycle and lc.get("id") != lifecycle:
            continue
        if service and svc != service:
            continue
        if category and not svc.startswith(category + "/"):
            continue
        for node, task in extract_lifecycle(lc, cross_keys, builtins,
                                            quota_map).items():
            cur = merged.get(node)
            if cur is None:
                merged[node] = task
                continue
            # prefer VALIDATED over docs; otherwise first one wins
            if (cur["provenance"] != "VALIDATED"
                    and task["provenance"] == "VALIDATED"):
                task.setdefault("notes", "")
                merged[node] = task
                cur = task
                continue
            note = ("also created by lifecycle '%s'"
                    % task["source"]["lifecycle"])
            cur["notes"] = (cur.get("notes", "") + ("; " if cur.get("notes")
                                                    else "") + note)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--service", help="only this category/service")
    ap.add_argument("--category", help="only this category")
    ap.add_argument("--lifecycle", help="only this lifecycle id")
    ap.add_argument("--stdout", action="store_true",
                    help="print drafts instead of writing _drafts/*.draft.yaml")
    args = ap.parse_args()

    import yaml

    merged = extract(service=args.service, category=args.category,
                     lifecycle=args.lifecycle)
    if not merged:
        print("no create steps matched — nothing to extract")
        return 0

    by_service: dict[str, dict] = {}
    for node, task in merged.items():
        by_service.setdefault(task.get("service") or "uncategorized__unknown",
                              {})[node] = task

    for svc in sorted(by_service):
        doc = {"version": 1, "resources": by_service[svc]}
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=100)
        if args.stdout:
            print(f"# ---- DRAFT {svc} "
                  f"({len(by_service[svc])} node(s)) ----")
            print(text)
            continue
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        fname = svc.replace("/", "__") + ".draft.yaml"
        curated = HERE / (svc.replace("/", "__") + ".yaml")
        out = DRAFTS_DIR / fname
        out.write_text(
            "# EXTRACTED DRAFT — review + curate into "
            "knowledge/formal/resources/%s\n# (this tool never overwrites "
            "curated files%s)\n%s" % (
                curated.name,
                "; a curated file already EXISTS" if curated.exists() else "",
                text),
            encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}  ({len(by_service[svc])} "
              f"node(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
