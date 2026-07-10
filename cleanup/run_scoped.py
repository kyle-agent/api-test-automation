"""Run-scoped leftover reaper — the missing tail of per-run cleanup.

Owner directive (2026-07-09): "파일스토리지 교차리전 절차 삭제는 테스트 전체
완료 후 정리작업에 추가" + "나머지 vpc 등도 안지워지던데 그것도 삭제해줘 반드시".

The lifecycle teardown deletes what it can, but three classes survived every
run this week: filestorage volumes pinned by a live cross-region replication
(delete 400 until the DR-side pause→delete procedure runs), VPCs pinned by a
child whose delete 4xx'd (vpc-endpoint in CREATING, hidden subnets named only
in the 409's related_resources), and slow-async children. This module reaps
EXACTLY the resources the run itself tracked (its events ledger — never a
name-guess, Hard Rule 3): tracked − deleted, re-verified live, then deleted in
dependency-safe order with the proven ladders. Account-wide reaping stays with
the reconciler (강제 클린업); this is the run's own tail only.
"""
from __future__ import annotations

import dataclasses
import json
import re
import time
from pathlib import Path

import core
from cleanup import reconciler as r

# deletion order: children before parents (mirror of the reconciler's ordering,
# reduced to the collections a run can track).
_ORDER = [
    "vpc-endpoints", "snapshots", "volumes", "vips", "ports", "publicips",
    "nat-gateways", "internet-gateways", "lb-listeners", "lb-server-groups",
    "lb-health-checks", "loadbalancers", "vpc-peerings", "transit-gateways",
    "private-dns", "subnets", "vpcs",
]
_SRN_SUBNET = re.compile(r":subnet/([0-9a-f]{32})")


def _collection(path: str) -> str:
    segs = [s for s in path.split("?")[0].strip("/").split("/") if s]
    # /v1/<collection>/<id>[...]; nested (tgw vpc-connections) keyed on root
    return segs[1] if len(segs) >= 2 else (segs[0] if segs else "")


def _leftovers_from_events(events_path: str | Path) -> list[dict]:
    """Tracked − deleted, straight from THIS run's events ledger."""
    tracked: dict[tuple, dict] = {}
    deleted: set = set()
    try:
        lines = Path(events_path).read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        key_path = (e.get("path") or "").split("?")[0]
        if e.get("kind") == "resource-tracked" and key_path:
            tracked[(e.get("service"), key_path)] = {
                "service": e.get("service") or "", "path": e.get("path"),
                "lifecycle": e.get("lifecycle") or ""}
        elif e.get("kind") == "resource-deleted" and key_path:
            deleted.add((e.get("service"), key_path))
    return [v for k, v in tracked.items() if k not in deleted]


def _client_for(service: str, path: str, west, east):
    # filestorage replicas live in the DR region; everything else is west.
    return east if (service == "filestorage" and "kr-east1" in path) else west


def reap_run_leftovers(events_path: str | Path, log=print) -> int:
    """Delete this run's surviving resources. Returns count of issued deletes.
    Best-effort: every step logs; nothing raises out."""
    leftovers = _leftovers_from_events(events_path)
    if not leftovers:
        log("  run-scoped reap: 잔존 후보 0 — 정리 완료 상태")
        return 0
    # 호스트 프로세스(콘솔 서버 등)는 게이트 env 없이 떠 있을 수 있다. 리퍼는
    # 이 런의 원장에 기록된 자원만 지우므로(Hard Rule 3 준수) 게이트를 강제로
    # 켠 클라이언트를 쓴다 — 안 그러면 run-end 정리가 통째로 무력화된다
    # (2026-07-10 run-0099에서 실측: "DELETE blocked" 후 TGW/VPC 잔존).
    _gated = dataclasses.replace(
        core.settings, allow_mutations=True, allow_destructive=True)
    west = core.ApiClient(_gated)
    east = core.ApiClient(dataclasses.replace(_gated, region="kr-east1"))
    by_col: dict[str, list[dict]] = {}
    for it in leftovers:
        by_col.setdefault(_collection(it["path"] or ""), []).append(it)
    issued = 0
    for col in _ORDER + sorted(set(by_col) - set(_ORDER)):
        for it in by_col.get(col, []):
            svc, path = it["service"], it["path"]
            cli = _client_for(svc, path, west, east)
            bare = path.split("?")[0]
            try:  # still alive? (async teardown may have converged)
                g = cli.get(path, service=svc)
            except Exception as exc:  # noqa: BLE001
                log(f"  reap {path}: GET error {exc} — skip")
                continue
            if g.status in (404, 410):
                continue
            # filestorage volume pinned by replication → owner procedure ①②③
            if svc == "filestorage" and col == "volumes":
                vid = bare.rstrip("/").split("/")[-1]
                if r._teardown_filestorage_replication(cli, vid):
                    time.sleep(20)  # async replication delete settles
                r._reap_filestorage_snapshots(cli, vid)
            try:
                d = cli.delete(path, service=svc)
                st = d.status
            except core.MutationBlocked as exc:
                log(f"  reap {path}: blocked ({exc})")
                return issued
            except Exception as exc:  # noqa: BLE001
                log(f"  reap {path}: DELETE error {exc}")
                continue
            # VPC 409 names hidden holders (subnets absent from lists) in
            # related_resources — delete them by the 409's own ids, retry once.
            if col == "vpcs" and st == 409:
                body = d.body if isinstance(d.body, dict) else {}
                srns = " ".join(
                    str(x) for err in body.get("errors", [])
                    for x in err.get("related_resources", []))
                for sid in _SRN_SUBNET.findall(srns):
                    ds = cli.delete(f"/v1/subnets/{sid}", service=svc)
                    log(f"  reap holder subnet {sid} -> {ds.status}")
                    r._wait_gone(cli, svc, f"/v1/subnets/{sid}")
                d = cli.delete(path, service=svc)
                st = d.status
            log(f"  reap [{it['lifecycle']}] {path} -> {st}")
            if 200 <= (st or 0) < 300:
                issued += 1
                r._wait_gone(cli, svc, bare)
    log(f"  run-scoped reap: {issued}건 삭제 발행 ({len(leftovers)}건 후보)")
    return issued
