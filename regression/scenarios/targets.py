"""Target selector layer (M6b/M6c) — selector specs -> node-id lists.

A *thin* interpreter that sits ON TOP of ``composer.plan()``/``compose()``
(docs/M6-DESIGN.md §B.1).  ``compose()``/``plan()`` only accept a flat list of
node ids; humans and agents want to say "all of service X" or "every CRUD
node".  ``expand_targets(spec)`` resolves a selector string into a sorted,
de-duplicated list of node ids and hands it straight to the composer — it does
NOT reimplement dependency closure, dedup, ordering or branch selection; those
all belong to ``plan()``/``compose()`` (already implemented).

Grammar (``spec`` is a string; a comma- and/or whitespace-separated list of
clauses, all unioned together):

    service:<category>/<service>   every node whose `service` field == value
    group:<code>                   every node in group <code>
                                   (node `group:` field, else first two code
                                    segments — see knowledge/formal/resources/
                                    _groups.yaml for valid codes)
    theme:read-only                nodes with NO `delete` (lookup/read nodes)
    theme:crud                     nodes WITH a `delete`
    theme:heavy                    nodes with `heavy: true`
    theme:vary                     nodes with any create.options.*.vary == true
    all                            every node id in the model
    <node-id>                      itself (bare node id)

Unknown service / group / theme -> ``ValueError`` listing the valid values.

CLI: ``python -m regression.scenarios.targets <spec>`` prints the resolved node
id list (one per line) for operator sanity / dispatch wiring.
"""
from __future__ import annotations

from pathlib import Path

from regression.scenarios import composer

__all__ = ["expand_targets", "compose_selection"]

_VALID_THEMES = ("read-only", "crud", "heavy", "vary")


# ---------------------------------------------------------------------------
# node-shape predicates (the only "classification" this module owns)

def _service_of(task: dict) -> str | None:
    return task.get("service")


def _group_of(task: dict) -> str | None:
    """A node's group code: explicit `group:` field, else the first two
    segments of its `code` (``nw-vpc-subnet`` -> ``nw-vpc``)."""
    g = task.get("group")
    if g:
        return g
    code = task.get("code") or ""
    parts = code.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else None


def _has_delete(task: dict) -> bool:
    return bool((task.get("delete") or {}).get("endpoint"))


def _is_heavy(task: dict) -> bool:
    return bool(task.get("heavy"))


def _is_vary(task: dict) -> bool:
    opts = ((task.get("create") or {}).get("options")) or {}
    return any(isinstance(s, dict) and s.get("vary") for s in opts.values())


def _valid_group_codes() -> set[str]:
    """Valid group codes from knowledge/formal/resources/_groups.yaml (falls
    back to the codes derivable from the model if the file is unreadable)."""
    import yaml

    path = Path(composer.DEFAULT_MODEL_DIR) / "_groups.yaml"
    try:
        data = yaml.safe_load(path.read_text()) or {}
        codes = set((data.get("groups") or {}).keys())
        if codes:
            return codes
    except Exception:  # pragma: no cover - defensive
        pass
    return set()


# ---------------------------------------------------------------------------
# expand_targets

def _expand_clause(clause: str, model: dict) -> list[str]:
    if clause == "all":
        return list(model)

    kind, sep, value = clause.partition(":")
    if not sep:
        # bare node id
        if clause in model:
            return [clause]
        raise ValueError(
            f"unknown selector / node id '{clause}'. Expected a node id or a "
            f"'service:'/'group:'/'theme:' selector or 'all'.")

    if kind == "service":
        hits = [n for n, t in model.items() if _service_of(t) == value]
        if not hits:
            valid = sorted({_service_of(t) for t in model.values() if _service_of(t)})
            raise ValueError(
                f"unknown service '{value}'. Valid services: {valid}")
        return hits

    if kind == "group":
        valid = _valid_group_codes() or {
            _group_of(t) for t in model.values() if _group_of(t)}
        if value not in valid:
            raise ValueError(
                f"unknown group '{value}'. Valid group codes: {sorted(valid)}")
        hits = [n for n, t in model.items() if _group_of(t) == value]
        if not hits:
            raise ValueError(
                f"group '{value}' is a valid code but matches no model nodes")
        return hits

    if kind == "theme":
        if value == "read-only":
            return [n for n, t in model.items() if not _has_delete(t)]
        if value == "crud":
            return [n for n, t in model.items() if _has_delete(t)]
        if value == "heavy":
            return [n for n, t in model.items() if _is_heavy(t)]
        if value == "vary":
            return [n for n, t in model.items() if _is_vary(t)]
        raise ValueError(
            f"unknown theme '{value}'. Valid themes: {list(_VALID_THEMES)}")

    raise ValueError(
        f"unknown selector kind '{kind}:' in clause '{clause}'. Expected "
        f"'service:', 'group:', 'theme:', 'all', or a bare node id.")


def expand_targets(spec: str, model: dict | None = None) -> list[str]:
    """Resolve a selector *spec* into a sorted, de-duplicated list of node ids.

    *spec* is a comma- and/or whitespace-separated list of clauses; the result
    is the union of every clause.  See the module docstring for the grammar.
    Unknown service/group/theme/node raises ``ValueError`` listing the valid
    values.  Closure/dedup/ordering is NOT done here — feed the result to
    ``composer.plan()``/``compose()``.
    """
    if model is None:
        model = composer.load_model()
    if not isinstance(spec, str):
        raise ValueError(f"spec must be a string, got {type(spec).__name__}")

    clauses = [c for c in spec.replace(",", " ").split() if c]
    if not clauses:
        raise ValueError("empty target spec")

    selected: set[str] = set()
    for clause in clauses:
        selected.update(_expand_clause(clause, model))
    return sorted(selected)


# ---------------------------------------------------------------------------
# convenience: selector -> composed lifecycle (heavy lifting stays in compose)

def compose_selection(spec: str, **kw):
    """Resolve *spec* with ``expand_targets`` then hand the node ids to
    ``composer.compose`` (which does the closure/dedup/ordering).  Minimal by
    design — ``expand_targets`` is where the work is.

    NOTE: per-selector convenience helpers ``compose_service``/
    ``compose_group``/``compose_theme`` are ticket T3 (not built here).
    """
    targets = expand_targets(spec)
    return composer.compose(targets, **kw)


# ---------------------------------------------------------------------------
# CLI — print the resolved node id list (operator sanity / dispatch wiring)

def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m regression.scenarios.targets",
        description="Resolve a target selector spec to a node-id list.")
    ap.add_argument("spec", help="selector spec, e.g. 'service:networking/vpc' "
                                  "or 'group:nw-vpc, theme:crud'")
    args = ap.parse_args(argv)
    try:
        print("\n".join(expand_targets(args.spec)))
    except ValueError as exc:
        print(f"error: {exc}", flush=True)
        return 2
    except BrokenPipeError:  # pragma: no cover - downstream `head` etc.
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
