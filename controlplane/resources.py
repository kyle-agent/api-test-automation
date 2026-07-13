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
import datetime as _dt
import json
import os
import re
import threading
import time
from pathlib import Path

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


# --- owned-resource scan (실측 정본) ---------------------------------------------
# 잔존 자원의 SINGLE SOURCE OF TRUTH = cleanup.verify_clean.scan_owned (reconciler의
# 소유 태그 스윕을 delete-stub 으로 돌린 read-only 인벤토리 — console2 /api/owned 와
# 같은 엔진). 스캔은 느리므로(전 컬렉션 LIST) 백그라운드 스레드 + 캐시 (_runtime
# 캐시 패턴). 위의 ingest 기반 inventory()는 '이력(플랫폼이 본 것)'으로 강등.

_OWNED = {"rows": None, "ts": 0.0, "scanning": False, "error": None}
_OWNED_LOCK = threading.Lock()
_VER_RE = re.compile(r"^v\d")
_ROOT = Path(__file__).resolve().parent.parent


def stuck_entries() -> list[dict]:
    """data/baselines/known_issues.json 의 ``stuck_resources`` — 문서화된, 현재
    API로 지울 수 없는 잔존 자원 목록 (부재/깨짐 → 빈 목록)."""
    try:
        from core import baselines
        path = baselines.resolve(_ROOT / "data" / "baselines" / "known_issues.json")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [e for e in data.get("stuck_resources", []) if isinstance(e, dict)]
    except Exception:
        return []


def _expand_scan(scan: list[dict]) -> list[dict]:
    """scan_owned 항목(service·path·json) → 행(service·collection·res_id·name·kind).

    * 일반형  DELETE /v1/<coll>/<id>            → collection=<coll>, res_id=<id>
    * kms     DELETE /v1/kms/transit/<id>       → kind=kms (단건 삭제 매핑과 일치)
    * keypairs는 이름으로 삭제 → res_id 칸이 곧 name
    * bulk(body.ids — servicewatch 등)          → id 하나당 한 행
    ``kind`` 는 단건 삭제(_delete_call) 재사용을 위한 키."""
    rows: list[dict] = []
    for o in scan:
        service, path = o.get("service", ""), o.get("path", "")
        body = o.get("json") if isinstance(o.get("json"), dict) else {}
        segs = [s for s in path.split("?")[0].split("/") if s]
        if segs and _VER_RE.match(segs[0]):
            segs = segs[1:]
        ids = body.get("ids")
        if isinstance(ids, list) and ids:
            coll = "/".join(segs)
            for rid in ids:
                rows.append({"service": service, "collection": coll,
                             "res_id": str(rid), "name": "",
                             "kind": segs[-1] if segs else "", "path": path})
            continue
        if len(segs) > 1:
            coll, rid = "/".join(segs[:-1]), segs[-1]
        else:
            coll, rid = "/".join(segs), ""
        kind = "kms" if segs and segs[0] == "kms" else (segs[0] if segs else "")
        name = rid if kind == "keypairs" else ""
        rows.append({"service": service, "collection": coll, "res_id": rid,
                     "name": name, "kind": kind, "path": path})
    return rows


def _owned_scan_worker() -> None:
    rows = err = None
    try:
        # read-only-ness is guaranteed by scan_owned stubbing _delete/_wait_gone;
        # the env default is just a belt-and-braces hint.
        os.environ.setdefault("SCP_ALLOW_DESTRUCTIVE", "false")
        from cleanup.verify_clean import scan_owned
        rows = _expand_scan(scan_owned())
        # 이름 보강: ingest 이력에서 res_id → name (있을 때만)
        names = {r["res_id"]: r["name"] for r in inventory() if r.get("name")}
        for r in rows:
            if not r["name"]:
                r["name"] = names.get(r["res_id"], "")
    except Exception as exc:  # noqa: BLE001 — 스캔 실패는 상태로 노출, 서버는 계속
        err = str(exc)
    with _OWNED_LOCK:
        if err is None:
            _OWNED.update(rows=rows, error=None, ts=time.time(), scanning=False)
        else:  # 실패 시 마지막 성공 결과 유지 (있다면), 에러만 갱신
            _OWNED.update(error=err, ts=time.time(), scanning=False)


def start_owned_scan() -> bool:
    """백그라운드 owned 스캔 시작 (이미 도는 중이면 no-op). True = 새로 시작."""
    with _OWNED_LOCK:
        if _OWNED["scanning"]:
            return False
        _OWNED["scanning"] = True
    threading.Thread(target=_owned_scan_worker, daemon=True).start()
    return True


def _match_stuck(row: dict, stuck: list[dict]) -> dict | None:
    for e in stuck:
        if row.get("res_id") and row["res_id"] == str(e.get("id", "")):
            return e
        nm = str(e.get("name", ""))
        if nm and row.get("name") and (row["name"] == nm or nm in row["name"]):
            return e
    return None


def owned_state() -> dict:
    """현재 스캔 상태 스냅샷 — 화면 렌더용. rows 는 normal/stuck 으로 분리
    (stuck = known_issues.stuck_resources 매칭, 접힌 '기지 항목' 그룹으로)."""
    with _OWNED_LOCK:
        st = {k: _OWNED[k] for k in ("rows", "ts", "scanning", "error")}
    st["age_s"] = int(time.time() - st["ts"]) if st["ts"] else None
    stuck_docs = stuck_entries()
    normal, stuck_rows = [], []
    for r in st["rows"] or []:
        hit = _match_stuck(r, stuck_docs)
        if hit:
            stuck_rows.append({**r, "stuck": hit})
        else:
            normal.append(r)
    st["normal"], st["stuck_rows"], st["stuck_docs"] = normal, stuck_rows, stuck_docs
    st["total"] = len(st["rows"] or []) if st["rows"] is not None else None
    return st


def _age_label(sec) -> str:
    if sec is None:
        return ""
    if sec < 60:
        return f"{sec}초 전"
    if sec < 3600:
        return f"{sec // 60}분 전"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분 전"


def owned_summary() -> dict:
    """홈 KPI 승격용 컴팩트 요약 (D8) — owned_state()를 읽기만 하고 스캔은
    **트리거하지 않는다** (홈 열 때 자동 수집 금지, 마지막 캐시만 노출).

    * ``scanned`` False = 아직 한 번도 스캔 안 함 → 화면은 '미확인'(0으로 위장
      금지, empty-state 규율). ``actionable`` = 기지(stuck)를 뺀 우리가 치워야
      할 잔존 수(=normal), ``stuck`` = 문서화된 기지 항목 수. 잔존 자원은 라이브
      **이 서버** 관측(발행 스냅샷 아님)이라 배지는 local 이어야 한다."""
    st = owned_state()
    scanned = st["total"] is not None
    when = ""
    if st.get("ts"):
        when = _dt.datetime.fromtimestamp(
            st["ts"], _dt.timezone(_dt.timedelta(hours=9))).strftime("%H:%M")
    return {
        "scanned": scanned,
        "scanning": bool(st.get("scanning")),
        "actionable": len(st.get("normal") or []) if scanned else None,
        "stuck": len(st.get("stuck_rows") or []),
        "total": st["total"],
        "age": _age_label(st.get("age_s")),
        "when": when,
        "error": st.get("error"),
    }


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
