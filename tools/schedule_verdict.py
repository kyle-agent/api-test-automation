"""스케줄 판정 — 실제 투입 순서 vs simulate_schedule 예측 비교 (인수인계 미결 1순위).

2026-07-11 밤 인수인계: 117종 런에서 예측(고스트)과 달리 DB 계열+VPC 자체생성군이
늦게/못 투입 — 원인이 "정렬 미적용"(pytest 수집 순서가 LPT 정렬을 안 탔다)인지
"레인/슬롯 대기"(정렬은 됐지만 실행 시점에 VPC 슬롯·xdist 선배정이 붙잡았다)인지
events로 확정해야 한다. 이 도구는 그 판정을 자동화한다:

  python -m tools.schedule_verdict                      # 버킷에서 최신 완료 런 자동
  python -m tools.schedule_verdict --run-id 20260711-…  # 특정 런
  python -m tools.schedule_verdict --events path.jsonl  # 로컬 events 파일
  옵션: --workers N (기본: events의 동시 실행 피크로 추정) --vpc-slots M (기본 4)

판정 로직 (실측 근거를 그대로 출력하고 마지막에 힌트를 준다):
  * 실제 lifecycle-start 순서(START RANK)를 events에서 뽑는다.
  * 같은 라이프사이클 집합으로 local_run.simulate_schedule을 돌려 예측 시작
    순서(PREDICTED RANK)를 얻는다 (실 디스패처와 같은 durations 데이터 사용).
  * 판정 A — 정렬 적용 여부: "첫 워커-사이즈 배치"(실제 최초 W개의 시작)가
    예측 상위 W개와 얼마나 겹치나 (겹침율 낮음 → 정렬 미적용 쪽 증거).
  * 판정 B — 레인 대기 여부: 예측 상위권인데 실제 시작이 늦은 항목들을
    분류(DB 계열/VPC 자체생성/기타)해 지연 중앙값을 비교 (특정 분류만 늦음 →
    레인/슬롯 대기 쪽 증거; 전 분류 고르게 뒤섞임 → 정렬 문제 쪽 증거).

읽기 전용: 버킷 GET/LIST만. 오프라인 테스트는 --events 경로로 주입.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# events 로딩 — 완료 런 artifact(jsonl) / oplog res 스트림 / 로컬 파일 공용
# ---------------------------------------------------------------------------

def _parse_events_lines(lines) -> list[dict]:
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _bucket():
    import core.oplog as o
    res = o._client()
    c = res[0] if isinstance(res, tuple) else res
    cfg = o._cfg()
    bucket = cfg[0] if isinstance(cfg, tuple) else cfg["bucket"]
    return c, bucket


def load_events_from_bucket(run_id: str) -> list[dict]:
    """완료 런의 artifact/events.jsonl 우선, 없으면 res/* 스트림을 폴딩
    (진행 중/강제중지 런도 res 스트림만으로 부분 판정 가능)."""
    c, bucket = _bucket()
    key = f"runs/{run_id}/artifact/events.jsonl"
    try:
        body = c.get_object(Bucket=bucket, Key=key)["Body"].read()
        return _parse_events_lines(body.decode("utf-8", "replace").splitlines())
    except Exception:
        pass
    # res/* 폴딩 — 각 오브젝트는 {"events": [...]} 배치
    events: list[dict] = []
    token = None
    while True:
        kw = dict(Bucket=bucket, Prefix=f"runs/{run_id}/res/", MaxKeys=1000)
        if token:
            kw["ContinuationToken"] = token
        r = c.list_objects_v2(**kw)
        for x in r.get("Contents", []):
            try:
                b = c.get_object(Bucket=bucket, Key=x["Key"])["Body"].read()
                d = json.loads(b)
                events.extend(e for e in d.get("events", []) if isinstance(e, dict))
            except Exception:
                continue
        token = r.get("NextContinuationToken")
        if not token:
            break
    return events


def latest_run_id() -> str | None:
    """summary/artifact가 있는 최신 run prefix (완료 런), 없으면 최신 prefix."""
    c, bucket = _bucket()
    r = c.list_objects_v2(Bucket=bucket, Prefix="runs/20", Delimiter="/")
    prefixes = sorted(p["Prefix"].split("/")[1] for p in r.get("CommonPrefixes", []))
    for rid in reversed(prefixes):
        try:
            c.head_object(Bucket=bucket, Key=f"runs/{rid}/artifact/events.jsonl")
            return rid
        except Exception:
            continue
    return prefixes[-1] if prefixes else None


# ---------------------------------------------------------------------------
# 시작/종료 스팬 추출
# ---------------------------------------------------------------------------

def _ts_of(e: dict) -> float | None:
    t = e.get("t")
    if isinstance(t, (int, float)) and t > 1e12:      # epoch ms
        return t / 1000.0
    ts = e.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):                            # ISO
        from datetime import datetime
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def extract_starts(events: list[dict]) -> dict[str, float]:
    """lifecycle_id -> 최초 lifecycle-start epoch (kind= 콘솔계열 / action= oplog계열)."""
    starts: dict[str, float] = {}
    for e in events:
        if (e.get("kind") or e.get("action")) != "lifecycle-start":
            continue
        lid = e.get("lifecycle") or e.get("name")
        ts = _ts_of(e)
        if not (lid and ts):
            continue
        if lid not in starts or ts < starts[lid]:
            starts[lid] = ts
    return starts


def peak_concurrency(events: list[dict]) -> int:
    """lifecycle-start/-end 이벤트로 동시 실행 피크를 추정 (워커수 근사)."""
    pts = []
    for e in events:
        k = e.get("kind") or e.get("action")
        ts = _ts_of(e)
        if not ts:
            continue
        if k == "lifecycle-start":
            pts.append((ts, 1))
        elif k in ("lifecycle-end", "lifecycle-skip", "lifecycle-fail"):
            pts.append((ts, -1))
    cur = peak = 0
    for _, d in sorted(pts):
        cur += d
        peak = max(peak, cur)
    return peak


# ---------------------------------------------------------------------------
# 분류 (판정 B의 그룹)
# ---------------------------------------------------------------------------

_DB_SERVICES = ("mysql", "postgresql", "mariadb", "epas", "cachestore",
                "eventstreams", "searchengine", "sqlserver", "vertica")


def _load_lifecycles() -> dict[str, dict]:
    try:
        from regression.scenarios.loader import load_lifecycles
        lcs, _ = load_lifecycles(with_sources=True)
        return {lc["id"]: lc for lc in lcs}
    except Exception:
        # 폴백: lifecycles 디렉토리 직접 로드
        out = {}
        base = Path("regression/scenarios/lifecycles")
        for f in base.glob("*.json"):
            try:
                doc = json.loads(f.read_text())
            except ValueError:
                continue
            for lc in (doc if isinstance(doc, list) else doc.get("lifecycles", [])):
                if isinstance(lc, dict) and lc.get("id"):
                    out[lc["id"]] = lc
        return out


def classify(lid: str, lc: dict | None) -> str:
    if any(s in lid for s in _DB_SERVICES):
        return "db"
    steps = (lc or {}).get("steps", [])
    for s in steps:
        p = str(s.get("path", ""))
        if s.get("method", "").upper() == "POST" and p.rstrip("/").endswith("/vpcs") \
                and not s.get("adopt"):
            return "vpc-self"
    return "etc"


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------

def verdict(events: list[dict], workers: int | None = None,
            vpc_slots: int = 4, top: int = 25, out=print) -> dict:
    starts = extract_starts(events)
    if not starts:
        out("판정 불가: events에 lifecycle-start가 없습니다.")
        return {"ok": False}
    ids = sorted(starts)
    w = workers or max(peak_concurrency(events), 1)

    from regression.scenarios.local_run import simulate_schedule
    sim = simulate_schedule(ids, workers=w, vpc_slots=vpc_slots)
    pred_start = {b["id"]: b["s"] for b in sim["bars"]}

    t0 = min(starts.values())
    actual_rank = {lid: i for i, (lid, _) in enumerate(
        sorted(starts.items(), key=lambda kv: kv[1]))}
    pred_rank = {lid: i for i, (lid, _) in enumerate(
        sorted(pred_start.items(), key=lambda kv: kv[1]))}

    lcs = _load_lifecycles()
    rows = []
    for lid in ids:
        rows.append({
            "id": lid, "class": classify(lid, lcs.get(lid)),
            "pred_rank": pred_rank.get(lid, -1), "act_rank": actual_rank[lid],
            "pred_s": round(pred_start.get(lid, -1), 1),
            "act_s": round(starts[lid] - t0, 1),
            "lag_s": round((starts[lid] - t0) - pred_start.get(lid, 0), 1),
        })

    # 판정 A — 첫 배치 겹침율: 실제 최초 w개 vs 예측 상위 w개
    first_actual = {r["id"] for r in sorted(rows, key=lambda r: r["act_rank"])[:w]}
    first_pred = {r["id"] for r in sorted(rows, key=lambda r: r["pred_rank"])[:w]}
    overlap = len(first_actual & first_pred) / max(len(first_pred), 1)

    # 판정 B — 분류별 지연 중앙값
    import statistics
    lag_by_class = {}
    for cls in ("db", "vpc-self", "etc"):
        lags = [r["lag_s"] for r in rows if r["class"] == cls]
        if lags:
            lag_by_class[cls] = round(statistics.median(lags), 1)

    out(f"run lifecycles={len(ids)} workers(추정 피크)={w} vpc_slots={vpc_slots} "
        f"sim_makespan={round(sim['makespan_s']/60,1)}분")
    out(f"\n[판정 A] 첫 배치(워커 {w}개) 겹침율: {overlap:.0%} "
        f"(높음=정렬 적용됨, 낮음=정렬 미적용 의심)")
    out(f"[판정 B] 분류별 시작 지연 중앙값(실제-예측): {lag_by_class} "
        f"(db/vpc-self만 크게 양수=레인·슬롯 대기, 전 분류 고름=정렬 문제)")
    out(f"\n예측 상위 {top}개의 예측/실제 순위·시작(분):")
    out(f"{'lifecycle':44} {'cls':8} {'p.rk':>4} {'a.rk':>4} {'p.분':>6} {'a.분':>6} {'지연분':>6}")
    for r in sorted(rows, key=lambda r: r["pred_rank"])[:top]:
        out(f"{r['id'][:44]:44} {r['class']:8} {r['pred_rank']:4d} {r['act_rank']:4d} "
            f"{r['pred_s']/60:6.1f} {r['act_s']/60:6.1f} {r['lag_s']/60:6.1f}")

    hint = ("레인/슬롯 대기 우세" if overlap >= 0.6 and
            max(lag_by_class.get("db", 0), lag_by_class.get("vpc-self", 0))
            > 2 * abs(lag_by_class.get("etc", 0) or 1)
            else "정렬 미적용 우세" if overlap < 0.4
            else "혼합/추가 조사 필요")
    out(f"\n>>> 힌트: {hint} (겹침율 {overlap:.0%}, 지연 {lag_by_class})")
    return {"ok": True, "overlap": overlap, "lag_by_class": lag_by_class,
            "rows": rows, "hint": hint, "workers": w,
            "sim_makespan_s": sim["makespan_s"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id")
    ap.add_argument("--events", help="로컬 events.jsonl 경로 (버킷 대신)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--vpc-slots", type=int, default=4)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)
    if a.events:
        events = _parse_events_lines(Path(a.events).read_text().splitlines())
        rid = a.events
    else:
        rid = a.run_id or latest_run_id()
        if not rid:
            print("런을 찾지 못했습니다.")
            return 1
        print(f"run: {rid}")
        events = load_events_from_bucket(rid)
    r = verdict(events, workers=a.workers, vpc_slots=a.vpc_slots, top=a.top)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
