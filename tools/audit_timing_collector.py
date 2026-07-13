"""audit_timing_collector — 테스트 실행마다 audit log(loggingaudit)에서 자원별
생성/삭제/업데이트 실제 소요시간을 뽑아 누적 저장한다.

audit event_type = "{resource}.{action}.{start|end}" (예: subnet.create.end).
같은 resource_id의 .start/.end 쌍으로 실제 백엔드 소요(duration_s)를 계산해
data/audit_timings.jsonl 에 append 한다. (resource_id, action) 기준으로 dedupe
하므로 같은 자원을 여러 번 수집해도 중복되지 않는다.

사용:
  # 이번 실행 구간을 수집 (라벨 필수)
  python -m tools.audit_timing_collector collect --run-label 2026-07-13-sweep \
      --start 2026-07-13T14:00:00Z --end 2026-07-14T00:00:00Z
  # 최근 N시간(기본 12h) 자동
  python -m tools.audit_timing_collector collect --run-label nightly --lookback-hours 12
  # 누적 데이터 → report용 마크다운 요약 (자격증명 불필요, read-only)
  python -m tools.audit_timing_collector report

저장 스키마(한 줄=한 자원-작업):
  run_label, collected_at, resource_type, resource_id, resource_name,
  action(create|delete|update|modify|...), start, end, duration_s, status
"""
from __future__ import annotations
import argparse, json, os, statistics as st
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "audit_timings.jsonl"


def _toux(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _fetch(start_at: str, end_at: str) -> list[dict]:
    """audit log 전체 페이지 수집 (라이브, 자격증명 필요)."""
    from core.config import settings
    from core.http_client import ApiClient
    c = ApiClient(settings)
    out, page = [], 0
    while True:
        r = c.get('/v1/logs', service='loggingaudit', params={
            'start_at': start_at, 'end_at': end_at,
            'sort': 'created_at:desc', 'size': 100, 'page': page})
        if r.status != 200:
            if page == 0:
                raise SystemExit(f"listlogs -> {r.status}: {str(r.body)[:200]}")
            break
        logs = r.body.get('logs', [])
        if not logs:
            break
        out.extend(logs)
        if len(out) >= r.body.get('count', 0) or page > 40:
            break
        page += 1
    return out


def _pair_durations(events: list[dict]) -> list[dict]:
    """resource_id별 .start/.end 쌍 → duration 레코드."""
    res = defaultdict(lambda: defaultdict(list))
    meta = {}
    for e in events:
        et = e.get('event_type', '')
        if '.' not in et:
            continue
        action = et.split('.', 1)[1]           # 'create.end', 'delete.start', ...
        rid = e.get('resource_id') or e.get('resource_name')
        if not rid:
            continue
        res[rid][action].append((_toux(e['timestamp']), e.get('status')))
        meta[rid] = (e.get('resource_type'), e.get('resource_name'))
    recs = []
    for rid, acts in res.items():
        rtype, rname = meta[rid]
        bases = {a.rsplit('.', 1)[0] for a in acts}   # create/delete/update/...
        for base in bases:
            starts = acts.get(base + '.start', [])
            ends = acts.get(base + '.end', [])
            start = min((t for t, _ in starts), default=None)
            end = min((t for t, _ in ends), default=None)
            status = (ends or starts)[0][1] if (ends or starts) else None
            dur = (end - start) if (start is not None and end is not None and end >= start) else None
            recs.append({
                "resource_type": rtype, "resource_id": rid, "resource_name": rname,
                "action": base,
                "start": (datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if start else None),
                "end": (datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if end else None),
                "duration_s": (round(dur, 1) if dur is not None else None),
                "status": status,
            })
    return recs


def _load_store() -> list[dict]:
    if not STORE.exists():
        return []
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]


def collect(args) -> int:
    if args.start and args.end:
        start_at, end_at = args.start, args.end
    else:
        now = datetime.now(timezone.utc)
        start_at = (now - timedelta(hours=args.lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    events = _fetch(start_at, end_at)
    recs = _pair_durations(events)
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # dedupe vs existing store by (resource_id, action)
    existing = {(r["resource_id"], r["action"]) for r in _load_store()}
    new = [r for r in recs if (r["resource_id"], r["action"]) not in existing]
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        for r in new:
            r["run_label"] = args.run_label
            r["collected_at"] = collected_at
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    withdur = [r for r in new if r["duration_s"] is not None]
    print(f"수집 구간 {start_at} ~ {end_at} | audit {len(events)}건 → 자원-작업 {len(recs)}건 "
          f"(신규 {len(new)}, 그중 duration有 {len(withdur)}) → {STORE} 누적")
    _print_summary(new, title=f"이번 수집 ({args.run_label})")
    return 0


def _print_summary(recs: list[dict], title: str):
    byta = defaultdict(list)
    for r in recs:
        if r["duration_s"] is not None:
            byta[(r["action"], r["resource_type"])].append(r["duration_s"])
    print(f"\n=== {title} — 자원×작업별 실제 소요 ===")
    print(f"{'action':<8}{'resource_type':<18}{'n':>4}{'median':>9}{'max':>9}")
    for (act, rt), arr in sorted(byta.items(), key=lambda kv: (kv[0][0], -st.median(kv[1]))):
        print(f"{act:<8}{str(rt):<18}{len(arr):>4}{st.median(arr):>8.0f}s{max(arr):>8.0f}s")


def report(args) -> int:
    """누적 store → report용 마크다운 (read-only, 자격증명 불필요)."""
    recs = _load_store()
    if not recs:
        print("(no data in store)"); return 0
    runs = sorted({r.get("run_label") for r in recs})
    byta = defaultdict(list)
    for r in recs:
        if r["duration_s"] is not None:
            byta[(r["action"], r["resource_type"])].append(r["duration_s"])
    lines = [f"## 자원별 실제 소요시간 (audit log 누적, {len(recs)}건 / runs: {', '.join(map(str,runs))})", "",
             "| action | resource_type | n | median | p90 | max |",
             "|--------|---------------|---|--------|-----|-----|"]
    for (act, rt), arr in sorted(byta.items(), key=lambda kv: (kv[0][0], -st.median(kv[1]))):
        a = sorted(arr)
        p90 = a[int(len(a) * .9)] if a else 0
        lines.append(f"| {act} | {rt} | {len(a)} | {st.median(a):.0f}s | {p90:.0f}s | {max(a):.0f}s |")
    out = "\n".join(lines)
    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n")
        print(f"\n-> {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="audit log에서 자원별 소요 수집 → 누적")
    c.add_argument("--run-label", required=True, help="이번 실행 라벨 (예: 2026-07-13-sweep)")
    c.add_argument("--start", help="ISO8601 시작 (예: 2026-07-13T14:00:00Z)")
    c.add_argument("--end", help="ISO8601 종료")
    c.add_argument("--lookback-hours", type=int, default=12, help="--start/--end 없을 때 최근 N시간")
    c.set_defaults(func=collect)
    r = sub.add_parser("report", help="누적 store → 마크다운 요약")
    r.add_argument("--out", help="마크다운 저장 경로")
    r.set_defaults(func=report)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
