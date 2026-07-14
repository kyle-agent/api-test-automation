#!/usr/bin/env python3
"""변경분 검증 리포트 — 런 결과를 '변경된 API vs 기존 API' 두 버킷으로 분리.

``data/spec_diff_latest.json``(``spec.diff --mark``가 영속화한 변경 마커)과
결과 스토어(observations)를 조인해, 스펙 변경 직후 런에서:

  1. **변경/추가된 엔드포인트가 실제로 관측(touch)됐는지** — 안 됐으면 그
     변경분은 이번 런이 검증하지 못한 것 (커버리지 갭으로 보고).
  2. **관측됐다면 정상(2xx/ok)인지** — 변경분의 동작 검증.
  3. **실패가 어느 버킷에 몰리는지** — 변경 버킷에 몰리면 변경 자체의 문제,
     기존 버킷에 새 실패가 나타나면 호환성 회귀(변경의 부수효과) 신호.

조인 방식: observations의 (method, path)를 카탈로그 ``http_path`` 템플릿
(``{param}`` 자리표시자)에 정규식 매칭. crud_probe의 endpoint_key는
``lifecycle:step`` 형태라 key 조인이 불가능하므로 경로 매칭이 단일 규약.

사용:
    python -m spec.change_report                 # 최근 24h 관측 대상
    python -m spec.change_report --last-hours 6  # 직전 런만 (기본 24)
    python -m spec.change_report --out reports/change_report.json

의존: stdlib + core.results (read-only). 마커 파일이 없으면 안내 후 종료 0
(변경이 없었다는 뜻 — 리포트할 것 없음).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKS = ROOT / "data" / "spec_diff_latest.json"
CATALOG = ROOT / "data" / "api_catalog.json"


def _pattern(http_path: str) -> re.Pattern:
    """카탈로그 경로 템플릿 -> 관측 경로 매칭 정규식 ({param} = 한 세그먼트)."""
    esc = re.escape(http_path)
    esc = re.sub(r"\\\{[^/}]+\\\}", r"[^/]+", esc)
    return re.compile(f"^{esc}$")


def _norm_path(p: str) -> str:
    return (p or "").split("?", 1)[0].rstrip("/") or "/"


def build_report(marks: dict, catalog: list[dict], observations: list[dict],
                 *, since_ts: float = 0.0) -> dict:
    """마커 + 카탈로그 + 관측 -> 버킷 리포트 (순수 함수, 오프라인 테스트 대상)."""
    by_key = {e["key"]: e for e in catalog if isinstance(e, dict) and e.get("key")}
    marked = marks.get("marks") or {}

    # 변경/추가 키의 (method, path-pattern) 매처
    matchers = []   # (key, mark, method, pattern)
    unresolved = []  # 카탈로그에 method/path가 없어 매칭 불가한 마크 키
    for key, mark in marked.items():
        e = by_key.get(key)
        if not (e and e.get("method") and e.get("http_path")):
            unresolved.append(key)
            continue
        matchers.append((key, mark, e["method"].upper(),
                         _pattern(_norm_path(e["http_path"]))))

    obs = [o for o in observations
           if isinstance(o, dict) and float(o.get("ts") or 0) >= since_ts]

    touched: dict[str, dict] = {}   # key -> {mark, n, ok, soft, fail, statuses}
    changed_fail_obs, other_fail_obs = [], []
    for o in obs:
        m = str(o.get("method") or "").upper()
        p = _norm_path(str(o.get("path") or ""))
        cat = str(o.get("category") or "")
        hit_key = None
        for key, mark, mm, pat in matchers:
            if m == mm and pat.match(p):
                hit_key = key
                rec = touched.setdefault(key, {
                    "mark": mark, "n": 0, "ok": 0, "soft": 0, "fail": 0,
                    "statuses": []})
                rec["n"] += 1
                rec[cat if cat in ("ok", "soft", "fail") else "soft"] += 1
                st = o.get("status")
                if st is not None and st not in rec["statuses"]:
                    rec["statuses"].append(st)
                break
        if cat == "fail":
            (changed_fail_obs if hit_key else other_fail_obs).append(
                {"method": m, "path": p, "status": o.get("status"),
                 "endpoint_key": o.get("endpoint_key", ""),
                 **({"changed_key": hit_key} if hit_key else {})})

    untouched = [{"key": k, "mark": v} for k, v in sorted(marked.items())
                 if k not in touched and k not in unresolved]
    return {
        "marks_generated_at": marks.get("generated_at"),
        "since_ts": since_ts,
        "observations_considered": len(obs),
        "summary": {
            "marked_total": len(marked),
            "touched": len(touched),
            "touched_ok": sum(1 for r in touched.values()
                              if r["fail"] == 0 and r["ok"] > 0),
            "touched_failing": sum(1 for r in touched.values() if r["fail"] > 0),
            "untouched": len(untouched),
            "unresolved": len(unresolved),
            "failures_in_changed_bucket": len(changed_fail_obs),
            "failures_in_existing_bucket": len(other_fail_obs),
        },
        "touched": {k: touched[k] for k in sorted(touched)},
        "untouched": untouched,          # 이번 런이 검증 못 한 변경분
        "unresolved": sorted(unresolved),
        "failures_changed_bucket": changed_fail_obs,
        "failures_existing_bucket": other_fail_obs[:50],   # 상한 (로그 폭주 방지)
    }


def print_report(rep: dict) -> None:
    s = rep["summary"]
    print(f"=== 변경분 검증 리포트 (marks @ {rep.get('marks_generated_at')}) ===")
    print(f"변경/추가 마크 {s['marked_total']}개 중: "
          f"관측 {s['touched']} (정상 {s['touched_ok']} · 실패 {s['touched_failing']}) · "
          f"미관측 {s['untouched']} · 매칭불가 {s['unresolved']}")
    print(f"실패 분포: 변경 버킷 {s['failures_in_changed_bucket']}건 · "
          f"기존 버킷 {s['failures_in_existing_bucket']}건")
    if s["failures_in_changed_bucket"]:
        print("\n[변경 버킷 실패 — 변경 자체의 문제 후보]")
        for f in rep["failures_changed_bucket"]:
            print(f"  {f['method']} {f['path']} -> {f['status']} "
                  f"({f.get('changed_key')})")
    if s["touched_failing"] == 0 and s["failures_in_existing_bucket"]:
        print("\n[주의] 실패가 전부 기존 버킷 — 변경의 부수효과(호환성 회귀) 검토 필요")
    if rep["untouched"]:
        print(f"\n[미검증 변경분 {len(rep['untouched'])}개 — 다음 런/시나리오 추가 대상]")
        for u in rep["untouched"][:20]:
            print(f"  {u['mark']:7} {u['key']}")
        if len(rep["untouched"]) > 20:
            print(f"  … 외 {len(rep['untouched']) - 20}개")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--marks", default=str(MARKS))
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--last-hours", type=float, default=24.0,
                    help="이 시간 내의 관측만 대상 (기본 24h)")
    ap.add_argument("--out", default=None, help="리포트 JSON 저장 경로")
    args = ap.parse_args(argv)

    mp = Path(args.marks)
    if not mp.exists():
        print(f"마커 파일 없음({mp}) — 스펙 변경 diff가 아직 없다는 뜻. "
              f"`python -m spec.diff old new --mark` 후 다시 실행.")
        return 0
    marks = json.loads(mp.read_text(encoding="utf-8"))
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    from core import results as _results
    observations = _results.load_observations()
    since = time.time() - args.last_hours * 3600.0
    rep = build_report(marks, catalog, observations, since_ts=since)
    print_report(rep)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
