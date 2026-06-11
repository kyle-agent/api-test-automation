"""Run A vs B comparison (M2, PLATFORM-PLAN §2.6 비교 뷰).

Joins the two runs' snapshot observations on endpoint_key + method and buckets
the transitions: new fails (A ok→B fail), fixed (A fail→B ok), still-failing,
and other category changes (ok↔soft 등). A run is folded to ONE category per
endpoint first — an endpoint may be observed several times in a run (smoke +
read-chain + crud), and the WORST category is the run's verdict for it.
"""
from __future__ import annotations

#: severity order for the per-endpoint fold (worst observation wins)
_RANK = {"fail": 3, "soft": 2, "ok": 1}


def _fold(observations: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for o in observations:
        ek = o.get("endpoint_key") or o.get("path") or ""
        if not ek:
            continue
        key = f"{(o.get('method') or '').upper()} {ek}"
        cat = o.get("category") or ""
        cur = out.get(key)
        if cur is None or _RANK.get(cat, 0) > _RANK.get(cur["category"], 0):
            out[key] = {"category": cat, "status": o.get("status", ""),
                        "source": o.get("source", "")}
    return out


def diff(a_obs: list[dict], b_obs: list[dict]) -> dict:
    """Bucketed A→B transitions. Endpoints absent on one side show '—' and
    only count as new-fail (절대 fixed로 세지 않음 — 미실행은 증거가 아님)."""
    A, B = _fold(a_obs), _fold(b_obs)
    new_fails, fixed, still, changed = [], [], [], []
    for key in sorted(set(A) | set(B)):
        ca = A.get(key, {}).get("category", "")
        cb = B.get(key, {}).get("category", "")
        row = {"key": key, "a": ca or "—", "b": cb or "—",
               "a_status": A.get(key, {}).get("status", ""),
               "b_status": B.get(key, {}).get("status", "")}
        if cb == "fail" and ca == "fail":
            still.append(row)
        elif cb == "fail":
            new_fails.append(row)
        elif ca == "fail" and cb:        # fail → ok/soft (B에서 실측된 회복만)
            fixed.append(row)
        elif ca and cb and ca != cb:     # ok↔soft 등 분류 변화
            changed.append(row)
    return {"new_fails": new_fails, "fixed": fixed, "still": still,
            "changed": changed, "a_total": len(A), "b_total": len(B)}
