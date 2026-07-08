"""Duration / makespan estimator for the pre-flight payload (HEAVY-PREMISE §3).

Implements the WP2 half of ``docs/working/plans/HEAVY-PREMISE-CONTRACT.md`` §3:
pure functions only — the lead wires :func:`estimate` into ``/api/preflight``.

Source of truth for measured wall time
--------------------------------------
``reports/console2-runs/*.events.jsonl`` — the engine's per-run event stream
(``core.console_events``). Observed event shape (verified 2026-07-08 against the
real corpus):

    {"ts": <epoch seconds float>, "kind": "run-meta|wave-start|lifecycle-start|
     lifecycle-end|step-start|step-end|resource-tracked|resource-deleted|run-end",
     "lifecycle": "<id>", ...}

* ``run-meta`` (with ``"mode": "simulate"``) appears ONLY in simulate runs; live
  runs observed so far emit no run-meta at all. Simulate runs compress hours of
  wall time into seconds, so any run containing a run-meta event whose mode is
  ``"simulate"`` is EXCLUDED from the fold.
* ``step-end`` carries ``elapsed_ms`` — that is the HTTP **call latency**, NOT
  wall time (a wait/poll step records ~1.2s while really occupying ~40 min of
  wall; contract §3, confirmed 2026-07-08). It is deliberately never read here.

Folding choice (documented per contract)
----------------------------------------
Per lifecycle within a run::

    wall = ts(last lifecycle-end of that lifecycle) - ts(its first event)

Steps inside one lifecycle run sequentially, so this first→last event-timestamp
gap equals the sum of consecutive step-end timestamp gaps (the mechanism named
by the contract) while also absorbing engine overhead between steps and being
robust to truncated/malformed lines. A lifecycle contributes a sample only when
its ``lifecycle-end`` event exists (otherwise the run crashed mid-lifecycle and
the gap would be a truncated underestimate).

Sampling policy: samples from runs whose ``lifecycle-end`` status is
``"passed"`` are preferred; when a lifecycle has *no* passed sample at all, its
failed-run walls are used instead (a 3961s failed cluster provision is far more
honest than a 2400s class default). Early-abort failures never dilute passed
data because of the passed-first preference.

Class defaults (contract §3, for unmeasured lifecycles)
-------------------------------------------------------
* ``read``           ≈  30 s — every step is a GET (read/config-only).
* ``small-create``   ≈ 120 s — mutating but small (1–2 resources) or probe-like.
* ``cluster-grade``  ≈ 2400 s — has a strict-2xx mutating step AND (heavy flag
  or a "server"/"cluster" keyword in id/service): real server / cluster builds.

Defaults carry no measured spread, so their p90 is p50 × 1.5 — mirroring the
contract's example est ratio (2200→3300) and erring toward warning the user.

Makespan approximation (documented per contract)
------------------------------------------------
Selected lifecycles are mutually independent in the engine (each composes its
own chain; only the shared VPC is common), so they fan out to N parallel
workers. The estimator uses the simple, honest bound::

    makespan ≈ max(longest single lifecycle, total_sum / parallel)

which is exact for equal-length jobs and a lower-bound-tight approximation of
list scheduling otherwise. ``parallel`` defaults to :data:`PARALLEL_DEFAULT` = 4
— deliberately below the observed engine fan-out (console2 runs pytest
``-n min(6, len(ids))``; ``dag_runner.run_plan`` defaults to 8 workers) so the
estimate errs high, the safe direction for a pre-flight confirm. The lead can
pass the admission's current value via ``model["parallel"]``.

Caching: :func:`refresh_cache` persists the folded stats to
``reports/duration_stats_cache.json`` (reports/ is gitignored) for inspection;
:func:`estimate` itself always folds from the events dir (memoized in-process
on the directory's file signature), never trusting a stale file. Nothing is
ever written under ``data/``.

CLI::

    python -m tools.duration_stats                     # measured stats table
    python -m tools.duration_stats --refresh-cache     # also write the cache
    python -m tools.duration_stats ID [ID ...]         # estimate for a selection
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_DIR = ROOT / "reports" / "console2-runs"
CACHE_PATH = ROOT / "reports" / "duration_stats_cache.json"

#: Conservative lifecycle-level parallelism for the makespan bound (see module
#: docstring — engine observed at 6/8; 4 errs high). Override: model["parallel"].
PARALLEL_DEFAULT = 4

#: Contract §3 class defaults (seconds) for unmeasured lifecycles.
CLASS_DEFAULT_S = {"read": 30.0, "small-create": 120.0, "cluster-grade": 2400.0}

#: Defaults have no measured spread; p90 = p50 × this factor (docstring rationale).
DEFAULT_P90_FACTOR = 1.5

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# In-process memo: {resolved dir: (file signature, stats dict)}.
_FOLD_MEMO: dict[str, tuple[tuple, dict]] = {}


# --------------------------------------------------------------------------- #
# folding: events.jsonl -> per-lifecycle wall-time samples -> stats
# --------------------------------------------------------------------------- #
def _iter_run_events(path: Path):
    """Yield parsed event dicts from one events.jsonl (skip malformed lines)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("ts"), (int, float)):
            yield ev


def _fold_run(path: Path) -> dict[str, tuple[float, str]] | None:
    """One run file -> {lifecycle_id: (wall_seconds, end_status)}.

    Returns None for simulate runs (run-meta mode=="simulate") and for files
    with no usable lifecycle sample. Wall = last lifecycle-end ts − first event
    ts of that lifecycle (see module docstring; never elapsed_ms).
    """
    first: dict[str, float] = {}
    end: dict[str, tuple[float, str]] = {}
    for ev in _iter_run_events(path):
        if ev.get("kind") == "run-meta" and ev.get("mode") == "simulate":
            return None
        lc = ev.get("lifecycle")
        if not lc:
            continue
        ts = float(ev["ts"])
        if lc not in first or ts < first[lc]:
            first[lc] = ts
        if ev.get("kind") == "lifecycle-end":
            prev = end.get(lc)
            if prev is None or ts >= prev[0]:
                end[lc] = (ts, str(ev.get("status") or "unknown"))
    out: dict[str, tuple[float, str]] = {}
    for lc, (end_ts, status) in end.items():
        wall = end_ts - first.get(lc, end_ts)
        if wall >= 0:
            out[lc] = (wall, status)
    return out or None


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile of an ascending list (q in [0, 1])."""
    if not sorted_vals:
        raise ValueError("no samples")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def _dir_signature(events_dir: Path) -> tuple:
    files = sorted(events_dir.glob("*.events.jsonl"))
    sig = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        sig.append((f.name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def fold_events(events_dir: str | Path = EVENTS_DIR) -> dict[str, dict]:
    """Fold every live run's events.jsonl into per-lifecycle wall-time stats.

    Returns ``{lifecycle_id: {"p50_s": float, "p90_s": float, "n_runs": int}}``
    where n_runs is the number of run samples used (passed-status runs when any
    exist, else failed runs — see module docstring). Simulate runs are excluded.
    Memoized in-process on the directory's (name, mtime, size) file signature.
    """
    events_dir = Path(events_dir)
    key = str(events_dir.resolve()) if events_dir.exists() else str(events_dir)
    sig = _dir_signature(events_dir) if events_dir.is_dir() else ()
    memo = _FOLD_MEMO.get(key)
    if memo is not None and memo[0] == sig:
        return memo[1]

    samples: dict[str, dict[str, list[float]]] = {}
    if events_dir.is_dir():
        for path in sorted(events_dir.glob("*.events.jsonl")):
            run = _fold_run(path)
            if not run:
                continue
            for lc, (wall, status) in run.items():
                bucket = "passed" if status == "passed" else "other"
                samples.setdefault(lc, {"passed": [], "other": []})[bucket].append(wall)

    stats: dict[str, dict] = {}
    for lc, buckets in samples.items():
        vals = sorted(buckets["passed"] or buckets["other"])
        stats[lc] = {
            "p50_s": _percentile(vals, 0.5),
            "p90_s": _percentile(vals, 0.9),
            "n_runs": len(vals),
        }
    _FOLD_MEMO[key] = (sig, stats)
    return stats


# --------------------------------------------------------------------------- #
# class inference for unmeasured lifecycles (contract §3 defaults)
# --------------------------------------------------------------------------- #
def classify_lifecycle(lc: dict | None) -> str:
    """Infer the duration class of a lifecycle from its steps/metadata.

    * every step is a GET                              -> "read"
    * strict-2xx mutating step present AND (heavy flag
      or "server"/"cluster" in id/service)             -> "cluster-grade"
    * anything else (small creates, tolerant probes,
      unknown lifecycles)                              -> "small-create"
    """
    if not isinstance(lc, dict):
        return "small-create"
    steps = lc.get("steps") or []
    methods = {str(s.get("method", "")).upper() for s in steps if isinstance(s, dict)}
    if steps and methods <= {"GET"}:
        return "read"
    strict_create = False
    for s in steps:
        if not isinstance(s, dict):
            continue
        if str(s.get("method", "")).upper() not in _MUTATING:
            continue
        codes = [c for c in (s.get("expect_status") or []) if isinstance(c, int)]
        if codes and all(200 <= c < 300 for c in codes):
            strict_create = True
            break
    kw = f"{lc.get('id', '')} {lc.get('service', '')}".lower()
    if strict_create and (lc.get("heavy") or "server" in kw or "cluster" in kw):
        return "cluster-grade"
    return "small-create"


def _lifecycles_by_id(model: dict | None) -> dict[str, dict]:
    """Normalize the model's lifecycles into {id: lifecycle_dict}.

    Accepts the console2 ``_model()`` shape (``{"lifecycles": {id: {...}}}``),
    the loader shape (a list of dicts), or None -> load via the canonical
    ``regression.scenarios.loader`` (best-effort: {} when unavailable).
    """
    lcs = (model or {}).get("lifecycles")
    if lcs is None:
        try:
            from regression.scenarios.loader import load_lifecycles
            lcs = load_lifecycles()
        except Exception:  # noqa: BLE001 — estimator degrades to class defaults
            return {}
    if isinstance(lcs, dict):
        return {str(k): v for k, v in lcs.items() if isinstance(v, dict)}
    return {str(lc.get("id")): lc for lc in lcs
            if isinstance(lc, dict) and lc.get("id")}


# --------------------------------------------------------------------------- #
# public contract API (§3 — signature fixed)
# --------------------------------------------------------------------------- #
def estimate(lifecycle_ids: list[str], model: dict | None = None) -> dict:
    """Contract §3 duration estimate for a selection of lifecycles.

    Returns (keys fixed by the contract)::

        {"p50_s": int, "p90_s": int, "basis": "measured"|"default"|"mixed",
         "per_lifecycle": {id: {"p50_s": int, "p90_s": int, "basis": ...}}}

    Top-level p50_s/p90_s are the estimated **makespan** of the whole selection
    under ``makespan ≈ max(longest lifecycle, total_sum / parallel)`` (see
    module docstring for why this approximation is used and how ``parallel`` is
    chosen). ``basis`` is "measured" when every lifecycle had run history,
    "default" when none did, "mixed" otherwise.

    ``model`` (optional) may carry: ``lifecycles`` (list or {id: dict}) used for
    class inference of unmeasured ids, ``parallel`` (int — the admission's
    current worker count), and ``events_dir`` (path override, used by tests/ops).
    Unknown ids absent from the model fall back to the "small-create" default.
    """
    model = model or {}
    stats = fold_events(model.get("events_dir") or EVENTS_DIR)
    lcs = _lifecycles_by_id(model)

    per: dict[str, dict] = {}
    p50s: list[float] = []
    p90s: list[float] = []
    bases: set[str] = set()
    for lid in lifecycle_ids:
        st = stats.get(lid)
        if st and st.get("n_runs", 0) > 0:
            p50, p90, basis = float(st["p50_s"]), float(st["p90_s"]), "measured"
        else:
            default = CLASS_DEFAULT_S[classify_lifecycle(lcs.get(lid))]
            p50, p90, basis = default, default * DEFAULT_P90_FACTOR, "default"
        per[lid] = {"p50_s": int(round(p50)), "p90_s": int(round(p90)),
                    "basis": basis}
        p50s.append(p50)
        p90s.append(p90)
        bases.add(basis)

    basis = "mixed" if len(bases) > 1 else (bases.pop() if bases else "default")
    try:
        parallel = int(model.get("parallel") or PARALLEL_DEFAULT)
    except (TypeError, ValueError):
        parallel = PARALLEL_DEFAULT
    parallel = max(1, min(parallel, len(lifecycle_ids) or 1))
    mk50 = max(max(p50s, default=0.0), sum(p50s) / parallel) if p50s else 0.0
    mk90 = max(max(p90s, default=0.0), sum(p90s) / parallel) if p90s else 0.0
    return {"p50_s": int(round(mk50)), "p90_s": int(round(mk90)),
            "basis": basis, "per_lifecycle": per}


# --------------------------------------------------------------------------- #
# optional cache under reports/ (gitignored) — inspection/ops only
# --------------------------------------------------------------------------- #
def refresh_cache(events_dir: str | Path = EVENTS_DIR,
                  path: str | Path = CACHE_PATH) -> dict:
    """Fold and persist the stats to ``reports/duration_stats_cache.json``.

    The cache is informational (dashboards, quick inspection): ``estimate``
    never reads it — it folds the live events dir itself, so a stale cache can
    never skew a pre-flight. Refuses any path outside reports/ (contract: WP2
    writes nothing under data/).
    """
    path = Path(path)
    reports = (ROOT / "reports").resolve()
    if reports not in path.resolve().parents and path.resolve() != reports:
        raise ValueError(f"cache must live under reports/ (got {path})")
    stats = fold_events(events_dir)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "events_dir": str(events_dir),
        "n_lifecycles_measured": len(stats),
        "stats": stats,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return payload


def _main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--refresh-cache"]
    if "--refresh-cache" in argv:
        payload = refresh_cache()
        print(f"cache -> {CACHE_PATH}  "
              f"({payload['n_lifecycles_measured']} measured lifecycles)")
    if args:
        est = estimate(args)
        print(json.dumps(est, indent=2, sort_keys=True))
    else:
        stats = fold_events()
        if not stats:
            print("no measured lifecycles (no live events under "
                  f"{EVENTS_DIR})")
            return 0
        width = max(len(k) for k in stats)
        print(f"{'lifecycle':<{width}}  {'p50_s':>8}  {'p90_s':>8}  {'n':>3}")
        for lc in sorted(stats):
            st = stats[lc]
            print(f"{lc:<{width}}  {st['p50_s']:>8.1f}  {st['p90_s']:>8.1f}  "
                  f"{st['n_runs']:>3}")
    return 0


if __name__ == "__main__":
    import sys
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except BrokenPipeError:      # e.g. `python -m tools.duration_stats | head`
        raise SystemExit(0) from None
