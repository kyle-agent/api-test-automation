"""IB-042 recompose — propagate model-level create/verify tolerances into the
already-composed ``generated__*.json`` lifecycles.

Background: most ``generated__*.json`` files were produced by one-shot compose
drivers that are not committed (only ``drafts/compose_wave5.py`` survives), and
~half the lifecycles carry hand-tuned ids/notes/enabled flags. A full
from-scratch recompose would have to reverse-engineer targets/choices/options
for every lifecycle and would risk dropping ids / clobbering unrelated content
(violates the determinism + no-drop constraint).

Instead this driver does exactly what a faithful recompose would do for the
affected steps: for every model node that carries a create- or verify-level
tolerance (``optional`` / ``expect_status`` / ``note``), it finds the matching
composed step BY THE STEP NAME THE COMPOSER ITSELF EMITS (``create-<node>`` /
``verify-<node>-<vname>``) and re-applies the model's tolerance fields. It never
touches a step's method/path/json/body — only ``expect_status``, ``optional``,
and the human ``_note`` (the IB-042 passthrough fields). It is idempotent:
running it twice is a no-op.

Run from repo root:  python drafts/recompose_ib042.py
"""
from __future__ import annotations

import glob
import json

from regression.scenarios import composer

GEN_GLOB = "regression/scenarios/lifecycles/generated__*.json"


def _build_tolerance_index(model: dict):
    """Return ({create-step-name: fields}, {verify-step-name: fields}).

    The keys are the exact step names the composer emits, so matching needs no
    fragile name parsing. ``fields`` carries only the IB-042 passthrough keys.
    """
    create_idx: dict[str, dict] = {}
    verify_idx: dict[str, dict] = {}
    for nid, task in model.items():
        task = task or {}
        create = task.get("create") or {}
        if create.get("optional") or create.get("expect_status") or create.get("note"):
            f: dict = {}
            f["expect_status"] = (list(create["expect_status"])
                                  if create.get("expect_status")
                                  else [200, 201, 202])
            f["optional"] = bool(create.get("optional"))
            f["_note"] = create.get("note")
            create_idx[f"create-{nid}"] = f
        for vi, v in enumerate(task.get("verify") or []):
            vname = v.get("name", vi + 1)
            f = {}
            f["expect_status"] = list(v["expect_status"]) if v.get("expect_status") else [200]
            f["optional"] = bool(v.get("optional"))
            f["_note"] = v.get("note")
            verify_idx[f"verify-{nid}-{vname}"] = f
    return create_idx, verify_idx


def _apply(step: dict, fields: dict, *, is_create: bool) -> bool:
    """Propagate model tolerances onto *step* in place. Returns True if changed.

    PASSTHROUGH IS ADDITIVE / WIDENING ONLY — it must never undo a deliberate
    stopgap guard that is broader than the model (e.g. gen-wave4-rmtags carries
    a credential-scope ``[200,...,403,404]`` + optional guard the model does not
    model; narrowing it to the model value would turn a tolerated 403 into a
    live failure). So:
      * expect_status -> UNION of the step's codes and the model's (never drop a
        code the step already tolerates; add the ones the model introduces such
        as PF-21's 500 or mariadb's 400);
      * optional -> set True if the model marks it (never clear a guard's
        optional);
      * _note -> set only when the step has none (don't clobber a hand-written
        PF rationale already on the step).
    """
    changed = False
    cur = step.get("expect_status")
    if not isinstance(cur, list):
        cur = []
    union = list(cur)
    for code in fields["expect_status"]:
        if code not in union:
            union.append(code)
    union.sort()
    if union != cur:
        step["expect_status"] = union
        changed = True
    if fields["optional"] and not step.get("optional"):
        step["optional"] = True
        changed = True
    if fields.get("_note") and not step.get("_note"):
        step["_note"] = fields["_note"]
        changed = True
    return changed


def main() -> int:
    model = composer.load_model()
    create_idx, verify_idx = _build_tolerance_index(model)

    total_changes = 0
    for path in sorted(glob.glob(GEN_GLOB)):
        with open(path) as fh:
            data = json.load(fh)
        file_changes = 0
        for lc in data.get("lifecycles", []):
            for step in lc.get("steps", []):
                name = step.get("name", "")
                if name in create_idx:
                    if _apply(step, create_idx[name], is_create=True):
                        file_changes += 1
                        print(f"  {path.split('/')[-1]} :: {lc['id']} :: {name}"
                              f" -> expect={step.get('expect_status')}"
                              f" optional={step.get('optional')}")
                elif name in verify_idx:
                    if _apply(step, verify_idx[name], is_create=False):
                        file_changes += 1
                        print(f"  {path.split('/')[-1]} :: {lc['id']} :: {name}"
                              f" -> expect={step.get('expect_status')}"
                              f" optional={step.get('optional')}")
        if file_changes:
            # preserve the file's existing indentation (all generated__*.json
            # are indent=2) so the diff is the tolerance fields only, not a
            # whole-file whitespace reflow.
            with open(path, "w") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            print(f"wrote {path} ({file_changes} step(s) updated)")
            total_changes += file_changes
    print(f"\nTOTAL steps reconciled to model tolerances: {total_changes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
