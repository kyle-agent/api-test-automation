"""docs→VALIDATED promotion off endpoint-level 2xx evidence (IB-041 consumer).

The resource model (``knowledge/formal/resources/*.yaml``) marks each node
``provenance: docs`` until its CREATE endpoint has been proven by a **real 2xx**
at runtime. The evidence store is ``data/baselines/verified_endpoints.json``
(built by ``tools.derive_verified`` — only 200–299 observations land there, so
promotion is masked-defect-safe). The promotion rule (derive_verified docstring):

    promote node N → VALIDATED only if N's CREATE endpoint_key is a key in
    verified_endpoints.json. (A GET-create lookup node counts if that GET has
    2xx evidence — the create is the load-bearing step either way.)

Join mechanics (CRITICAL — learned 2026-07-03):

* a verified entry's key is ``category/service/op`` and carries
  ``{method, norm_path}`` where norm_path is query-stripped, leading-slash-
  stripped, id segments collapsed to ``*`` (e.g. ``v1/clusters/*/stop``).
* a node's create endpoint is ``"METHOD /path"`` with ``{ref.field}``
  placeholders — normalised with the SAME ``derive_verified.norm_path``.
* the join MUST be **service-scoped**: the node's ``service`` tail must equal
  the verified key's middle segment. ``/v1/clusters`` collides across
  mysql/pg/epas/cachestore/sqlserver/vertica/searchengine/eventstreams/ske —
  an unscoped match would wrongly promote sqlserver/vertica off cachestore
  evidence.

Default mode is a **dry-run report** (node → evidence key, no writes).
``--apply`` rewrites each promoted node's ``provenance: docs`` line to
``provenance: VALIDATED  # evidence: <verified key> (run <last_run>)`` —
a targeted single-line edit (NOT a full YAML redump: the model files carry
hand-written comments that must survive). Any pre-existing trailing comment on
the provenance line is preserved ahead of the evidence note. After editing,
each file is re-parsed and diffed against the expected in-memory promotion to
guarantee nothing else changed (edit is reverted on mismatch).

CLI::

    python -m tools.promote_validated                 # dry-run report
    python -m tools.promote_validated --apply         # rewrite the YAML files
    python -m tools.promote_validated --node cdn --node gslb --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from tools.derive_verified import norm_path  # noqa: E402  (single normaliser)

DEFAULT_MODEL_DIR = _ROOT / "knowledge" / "formal" / "resources"
DEFAULT_VERIFIED = _ROOT / "data" / "baselines" / "verified_endpoints.json"

# node ids sit at indent 2 under `resources:`; node fields at indent 4.
_NODE_LINE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*(#.*)?$")
_PROV_DOCS = re.compile(r"^(    provenance:\s*docs)\s*(#.*)?$")


def load_model(model_dir: Path) -> tuple[dict, dict]:
    """Merge resources/*.yaml (skip _*.yaml) -> ({node_id: task}, {node_id: file}).
    Mirrors controlplane.resource_model.load_model but stays standalone so the
    tool (and its offline test) work on any directory."""
    import yaml

    model: dict[str, dict] = {}
    sources: dict[str, Path] = {}
    for path in sorted(Path(model_dir).glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            continue
        for nid, node in (doc.get("resources") or {}).items():
            if isinstance(node, dict):
                model[str(nid)] = node
                sources[str(nid)] = path
    return model, sources


def verified_index(verified: dict) -> dict:
    """verified_endpoints.json -> {(service_tail, METHOD, norm_path): [keys]}.
    service_tail is the MIDDLE segment of the ``category/service/op`` key —
    the service scope the join requires."""
    idx: dict[tuple, list[str]] = {}
    for key, rec in (verified or {}).items():
        parts = key.split("/")
        if len(parts) != 3:
            continue
        svc = parts[1]
        method = str(rec.get("method") or "").upper()
        np = str(rec.get("norm_path") or "")
        idx.setdefault((svc, method, np), []).append(key)
    for keys in idx.values():
        keys.sort()
    return idx


def promotable(model: dict, verified: dict,
               only: set[str] | None = None) -> list[dict]:
    """Service-scoped promotable docs nodes.

    Returns [{node, service, endpoint, evidence_key, last_run, all_keys}] —
    every ``provenance: docs`` node whose create endpoint (method + norm_path)
    has a 2xx-verified entry FOR ITS OWN SERVICE. Nodes without a create
    endpoint (no_api / incomplete) are never promotable here."""
    idx = verified_index(verified)
    out: list[dict] = []
    for nid in sorted(model):
        if only is not None and nid not in only:
            continue
        node = model[nid]
        if node.get("provenance") != "docs":
            continue
        endpoint = (node.get("create") or {}).get("endpoint")
        if not endpoint or " " not in str(endpoint):
            continue
        method, _, path = str(endpoint).partition(" ")
        svc_tail = str(node.get("service") or "").split("/")[-1]
        keys = idx.get((svc_tail, method.strip().upper(),
                        norm_path(path.strip())), [])
        if not keys:
            continue
        ev = keys[0]
        out.append({
            "node": nid,
            "service": str(node.get("service") or ""),
            "endpoint": str(endpoint),
            "evidence_key": ev,
            "last_run": str((verified.get(ev) or {}).get("last_run") or ""),
            "all_keys": keys,
        })
    return out


def _node_block(lines: list[str], nid: str) -> tuple[int, int] | None:
    """(start, end) line-index range of node ``nid``'s block: from its
    ``  <nid>:`` line to the next indent-2 key (or indent-0 key / EOF)."""
    start = None
    for i, ln in enumerate(lines):
        m = _NODE_LINE.match(ln)
        if m and m.group(1) == nid:
            start = i
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if _NODE_LINE.match(ln) or (ln and not ln[0].isspace()
                                    and not ln.lstrip().startswith("#")):
            return start, j
    return start, len(lines)


def apply_promotion(path: Path, nid: str, evidence_key: str,
                    last_run: str) -> str | None:
    """Flip ``provenance: docs`` → VALIDATED for ONE node in ONE file via a
    targeted line edit (comments/formatting preserved). Returns an error
    string on failure (file untouched), None on success.

    Safety: after the edit the file is re-parsed and compared against the
    original parse with only this node's provenance flipped — any other
    difference reverts the edit."""
    import yaml

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    span = _node_block(lines, nid)
    if span is None:
        return f"{path.name}: node '{nid}' not found"
    start, end = span
    hit = None
    for i in range(start + 1, end):
        m = _PROV_DOCS.match(lines[i].rstrip("\n"))
        if m:
            if hit is not None:
                return f"{path.name}: node '{nid}' has >1 provenance line"
            hit = (i, m)
    if hit is None:
        return f"{path.name}: node '{nid}' has no 'provenance: docs' line"
    i, m = hit
    prior_comment = (m.group(2) or "").strip()
    note = f"evidence: {evidence_key} (run {last_run})" if last_run \
        else f"evidence: {evidence_key}"
    comment = f"{prior_comment} · {note}" if prior_comment else f"# {note}"
    if not comment.startswith("#"):
        comment = "# " + comment.lstrip("# ")
    eol = "\n" if lines[i].endswith("\n") else ""
    lines[i] = f"    provenance: VALIDATED  {comment}{eol}"
    edited = "".join(lines)

    # paranoia diff: ONLY this node's provenance may differ
    before = yaml.safe_load(original)
    after = yaml.safe_load(edited)
    expect = copy.deepcopy(before)
    expect["resources"][nid]["provenance"] = "VALIDATED"
    if after != expect:
        return f"{path.name}: edit for '{nid}' changed more than provenance — reverted"
    path.write_text(edited, encoding="utf-8")
    return None


def run(model_dir: Path, verified_path: Path, apply: bool,
        only: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """Compute promotable nodes; with apply=True rewrite the YAML files.
    Returns (promotions, errors)."""
    model, sources = load_model(model_dir)
    verified = json.loads(Path(verified_path).read_text(encoding="utf-8"))
    rows = promotable(model, verified, only=only)
    errors: list[str] = []

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"promote_validated [{mode}] — model {model_dir} "
          f"({len(model)} nodes) × verified {verified_path} "
          f"({len(verified)} keys)")
    for r in rows:
        extra = "" if len(r["all_keys"]) == 1 \
            else f"  [+{len(r['all_keys']) - 1} more keys match]"
        run_note = f" (run {r['last_run']})" if r["last_run"] else " (local run)"
        print(f"  {r['node']:32s} {r['service']:28s} -> {r['evidence_key']}"
              f"{run_note}{extra}")
        if apply:
            e = apply_promotion(sources[r["node"]], r["node"],
                                r["evidence_key"], r["last_run"])
            if e:
                errors.append(e)
                print(f"    ERROR {e}")
    print(f"{len(rows)} promotable docs node(s)"
          + (f" · {len(errors)} error(s)" if errors else "")
          + ("" if apply else " (dry-run — use --apply to rewrite)"))
    return rows, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Promote resource-model docs nodes to VALIDATED off "
                    "service-scoped 2xx evidence in verified_endpoints.json "
                    "(dry-run by default).")
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR),
                    help=f"resources/*.yaml directory. Default: {DEFAULT_MODEL_DIR}")
    ap.add_argument("--verified", default=str(DEFAULT_VERIFIED),
                    help=f"verified_endpoints.json. Default: {DEFAULT_VERIFIED}")
    ap.add_argument("--node", action="append", default=None,
                    help="restrict to this node id (repeatable)")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite provenance lines in the YAML files "
                         "(default: dry-run report)")
    args = ap.parse_args(argv)
    _, errors = run(Path(args.model_dir), Path(args.verified),
                    apply=args.apply,
                    only=set(args.node) if args.node else None)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
