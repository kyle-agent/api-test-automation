"""Resource inventory + single-resource delete (M2, PLATFORM-PLAN §2.5).

Inventory: folds the ingested resource events (db events, kind='resource',
stage=action, detail=full event JSON from core/oplog.emit_resource) into live
state — per res_id the LATEST state-changing action wins: created → live,
deleted / successful platform-delete → gone (delete-failed leaves it live).
This reflects INGESTED events only: runs executed without the
APITEST_PLATFORM_URL mirror never appear here.

Single-resource delete: reuses cleanup/reconciler.py instead of inventing a
new mapping — the low-level ``_delete`` primitive (MutationBlocked-safe,
returns the raw HTTP status) plus the sweep's per-kind DELETE shapes:

  generic            DELETE /v1/<kind>/<res_id>      (vpcs, subnets, servers,
                     volumes, snapshots, ports, publicips, *-gateways,
                     clusters, security-groups, …  — run_sweep's f"{coll}/{id}")
  keypairs           DELETE by NAME, not id           (run_sweep step 2)
  secrets            body {"waiting_time_ndays": 7}   (step 10)
  kms                collection lives at /v1/kms/transit (step 11)
  vpc-peerings       approve (CREATE_APPROVE) first, then DELETE (step 3b-2)
  servicewatch       bulk body {"ids": [id]} on the collection; log-groups
                     need their log-streams deleted first (step 12)

The event 'kind' is core/oplog._kind_of(path) — the raw collection segment —
so the generic path reconstruction is exact for every kind the engine emits.
"""
from __future__ import annotations

import calendar
import json
import time

from controlplane import db

#: actions that flip the live/gone state (everything else — lifecycle-start,
#: lifecycle-end, polling… — only enriches identity fields)
_GONE_ACTIONS = ("deleted",)


def _age(created_ts: str) -> str:
    """'2026-06-11T02:00:00Z' -> '3h 12m' (best-effort, '' on bad input)."""
    try:
        t = calendar.timegm(time.strptime(created_ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return ""
    s = max(0, int(time.time()) - t)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def inventory(gh_run_id: str | None = None) -> list[dict]:
    """Fold resource events into per-res_id rows (live rows first)."""
    rows: dict[str, dict] = {}
    for ev in db.list_resource_events(gh_run_id):
        try:
            d = json.loads(ev["detail"] or "{}")
        except ValueError:
            d = {}
        rid = str(d.get("res_id") or "")
        if not rid:
            continue  # lifecycle markers / delete events whose id wasn't recoverable
        action = ev["stage"] or d.get("action", "")
        cur = rows.setdefault(rid, {
            "res_id": rid, "run": "", "service": "", "kind": "", "name": "",
            "lifecycle": "", "live": False, "created_ts": "", "last_action": "",
            "last_ts": "",
        })
        for k in ("service", "kind", "name", "lifecycle"):
            if d.get(k):
                cur[k] = d[k]
        if action == "created":
            cur["live"] = True
            cur["created_ts"] = d.get("ts") or ev["ts"] or ""
            cur["run"] = ev["gh_run_id"] or cur["run"]
        elif action in _GONE_ACTIONS:
            cur["live"] = False
        elif action == "platform-delete":
            # our own single-delete attempts — only a successful one kills it
            if (ev["status"] or "").startswith("ok"):
                cur["live"] = False
        cur["run"] = cur["run"] or (ev["gh_run_id"] or "")
        cur["last_action"] = action
        cur["last_ts"] = ev["ts"] or cur["last_ts"]
    # live first, then newest activity first within each group
    out = sorted(rows.values(), key=lambda r: r["last_ts"], reverse=True)
    out.sort(key=lambda r: not r["live"])
    for r in out:
        r["age"] = _age(r["created_ts"]) if r["live"] else ""
    return out


# --- single-resource delete ----------------------------------------------------

def destructive_enabled() -> bool:
    """The SAME gate the reconciler and ApiClient enforce (SCP_ALLOW_DESTRUCTIVE)."""
    try:
        import core
        return bool(core.settings.allow_destructive)
    except Exception:
        return False


def _delete_call(client, service: str, kind: str, res_id: str, name: str = ""):
    """Issue ONE resource's DELETE the way cleanup.reconciler.run_sweep does.

    Returns the raw HTTP status (or None — blocked/network), exactly like
    reconciler._delete."""
    from cleanup.reconciler import _delete, _items

    if kind == "keypairs":                      # step 2: delete by name
        return _delete(client, service, f"/v1/keypairs/{name or res_id}")
    if kind == "secrets":                       # step 10: required body
        return _delete(client, service, f"/v1/secrets/{res_id}",
                       json={"waiting_time_ndays": 7})
    if kind == "kms":                           # step 11: /v1/kms/transit/<id>
        return _delete(client, service, f"/v1/kms/transit/{res_id}")
    if kind == "vpc-peerings":                  # step 3b-2: approve then delete
        try:
            client.put(f"/v1/vpc-peerings/{res_id}/approval", service=service,
                       json={"type": "CREATE_APPROVE"})
        except Exception:
            pass
        return _delete(client, service, f"/v1/vpc-peerings/{res_id}")
    if service == "servicewatch" and kind in ("alerts", "dashboards",
                                              "event-rules", "log-groups"):
        if kind == "log-groups":                # step 12: streams block the group
            try:
                streams = _items(client.get(
                    f"/v1/log-groups/{res_id}/log-streams",
                    service=service).body)
                s_ids = [s["id"] for s in streams
                         if isinstance(s, dict) and s.get("id")]
                if s_ids:
                    _delete(client, service,
                            f"/v1/log-groups/{res_id}/log-streams",
                            json={"ids": s_ids})
            except Exception:
                pass
        return _delete(client, service, f"/v1/{kind}", json={"ids": [res_id]})
    # the sweep's generic shape: DELETE <collection>/<id>
    return _delete(client, service, f"/v1/{kind}/{res_id}")


def delete_resource(service: str, kind: str, res_id: str,
                    name: str = "") -> tuple[bool, str]:
    """Delete ONE live resource. Returns (ok, 한국어 message).

    Caller must have checked destructive_enabled() — this re-checks anyway and
    refuses without it (no network call is made)."""
    if not destructive_enabled():
        return False, ("SCP_ALLOW_DESTRUCTIVE=true 미설정 — 삭제가 차단되었습니다. "
                       "서버 환경변수로 활성화한 뒤 다시 시도하세요.")
    if not (service and kind and res_id):
        return False, "service/kind/res_id가 없는 행은 삭제할 수 없습니다."
    import core
    try:
        core.settings.require_credentials()
        client = core.ApiClient(core.settings)
    except Exception as exc:
        return False, f"credential 오류 — 삭제 호출 불가: {exc}"
    st = _delete_call(client, service, kind, res_id, name=name)
    if st is None:
        return False, ("삭제 호출 실패 (mutation 차단 또는 네트워크 오류 — "
                       "서버 로그를 확인하세요).")
    if 200 <= st < 300 or st == 404:
        return True, f"삭제 요청 성공 (HTTP {st})" + (" — 이미 없음" if st == 404 else "")
    return False, f"삭제 거부됨 (HTTP {st}) — 자식 리소스가 남아 있을 수 있습니다."


def record_attempt(gh_run_id: str, *, service: str, kind: str, res_id: str,
                   name: str = "", lifecycle: str = "", ok: bool = False,
                   message: str = "") -> None:
    """Persist the platform-initiated delete attempt as a resource event so the
    inventory fold (and the per-run timeline) sees it."""
    now = time.time()
    detail = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "t": int(now * 1000), "action": "platform-delete", "kind": kind,
        "service": service, "name": name, "res_id": res_id,
        "lifecycle": lifecycle, "status": ("ok" if ok else "failed"),
        "parent": "", "outcome": message,
    }
    db.insert_event(gh_run_id or "platform", "resource", detail["ts"],
                    stage="platform-delete", status="ok" if ok else "failed",
                    detail=json.dumps(detail, ensure_ascii=False))
