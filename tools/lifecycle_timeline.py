#!/usr/bin/env python3
"""라이프사이클 타임라인 — 런 events.jsonl을 시간순 워터폴 HTML로 렌더.

오너 2026-07-14: "시나리오가 각 api 호출과 실제 자원생성에 소요되는 시간
포함해서 시간순으로 어떤 일이 있는지 보여줄 수 있을까?" — console2의 타이밍
실측 탭은 op별 '합산'이라 시간축이 없다. 이 도구는 step-start/step-end 이벤트로
각 스텝의 [API 호출 | settle(자원 정착 대기) | 재시도]를 시간축 위에 그린다.

사용:
    python -m tools.lifecycle_timeline reports/console2-runs/<run>.events.jsonl \
        --lifecycle epas-cluster-subops-full --out reports/timeline.html
    (--lifecycle 생략 = 런의 모든 라이프사이클을 섹션별로)

읽는 이벤트: step-start(ts) · step-end(ts, elapsed_ms=마지막 요청의 API 시간,
status, category) · poll-progress(attempt — settle 구간의 폴 횟수 주석).
스텝 벽시계 = end.ts - start.ts; settle = 벽시계 - API. stdlib만 사용.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


def load_events(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def build_timeline(events: list[dict], lifecycle: str | None = None) -> dict:
    """events -> {lifecycle: {"t0", "steps": [{name, method, t_rel, wall_s,
    api_s, settle_s, polls, status, category, gap_s}]}} (순수 함수, 테스트 대상)."""
    open_steps: dict[tuple, dict] = {}
    polls: dict[tuple, int] = {}
    out: dict[str, dict] = {}
    for ev in events:
        lc = ev.get("lifecycle")
        if not lc or (lifecycle and lc != lifecycle):
            continue
        kind = ev.get("kind")
        key = (lc, ev.get("step"))
        if kind == "step-start":
            open_steps[key] = ev
        elif kind == "poll-progress":
            polls[key] = max(polls.get(key, 0), int(ev.get("attempt") or 0))
        elif kind == "step-end":
            st = open_steps.pop(key, None)
            if st is None:
                continue
            rec = out.setdefault(lc, {"t0": st["ts"], "steps": []})
            rec["t0"] = min(rec["t0"], st["ts"])
            wall = max(0.0, float(ev["ts"]) - float(st["ts"]))
            api = min(wall, float(ev.get("elapsed_ms") or 0) / 1000.0)
            prev = rec["steps"][-1] if rec["steps"] else None
            gap = (max(0.0, float(st["ts"]) - prev["_end_ts"]) if prev else 0.0)
            rec["steps"].append({
                "name": ev.get("step", ""), "method": ev.get("method", ""),
                "t_rel": float(st["ts"]) - rec["t0"], "wall_s": wall,
                "api_s": api, "settle_s": max(0.0, wall - api),
                "polls": polls.pop(key, 0),
                "status": ev.get("status"), "category": ev.get("category", ""),
                "gap_s": gap, "_end_ts": float(ev["ts"]),
            })
    # t_rel은 라이프사이클별 t0 기준으로 재계산(이벤트 순서와 무관하게 정합)
    for lc, rec in out.items():
        rec["steps"].sort(key=lambda s: s["t_rel"])
        rec["total_s"] = (rec["steps"][-1]["_end_ts"] - rec["t0"]
                          if rec["steps"] else 0.0)
        api = sum(s["api_s"] for s in rec["steps"])
        settle = sum(s["settle_s"] for s in rec["steps"])
        gaps = sum(s["gap_s"] for s in rec["steps"])
        rec["breakdown"] = {"api_s": api, "settle_s": settle, "gap_s": gaps}
    return out


def _fmt(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}" if s >= 60 else f"{s:.1f}s"


def render_html(tl: dict, title: str = "lifecycle timeline") -> str:
    parts = [f"""<meta charset="utf-8"><title>{html.escape(title)}</title><style>
body{{font:13px/1.45 -apple-system,'Segoe UI',sans-serif;margin:20px;color:#1d2530}}
h2{{margin:18px 0 4px}} .sum{{color:#6b7480;font-size:12px;margin-bottom:8px}}
.tl{{overflow-x:auto;border:1px solid #e4e7eb;border-radius:8px;padding:10px}}
.row{{display:flex;align-items:center;height:20px;margin:1px 0;white-space:nowrap}}
.nm{{width:340px;flex:none;font-family:ui-monospace,monospace;font-size:11.5px;
  overflow:hidden;text-overflow:ellipsis}}
.bar{{position:relative;height:14px;flex:none}}
.api{{position:absolute;top:0;height:14px;background:#2563c9;border-radius:2px 0 0 2px}}
.settle{{position:absolute;top:0;height:14px;background:#f0b429;opacity:.85}}
.gap{{position:absolute;top:4px;height:6px;background:#c63434;opacity:.55}}
.lbl{{margin-left:6px;font-size:11px;color:#6b7480}}
.f .lbl{{color:#c63434;font-weight:600}}
.legend span{{display:inline-block;margin-right:14px;font-size:11.5px}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;
  vertical-align:-1px}}</style>
<div class="legend"><span><i class="sw" style="background:#2563c9"></i>API 호출</span>
<span><i class="sw" style="background:#f0b429"></i>settle(자원 정착 대기·폴)</span>
<span><i class="sw" style="background:#c63434"></i>선행 스텝과의 idle 갭</span></div>"""]
    for lc, rec in tl.items():
        total = rec.get("total_s") or 1.0
        px_per_s = max(0.15, min(6.0, 1100.0 / max(total, 1.0)))
        b = rec["breakdown"]
        parts.append(
            f"<h2>{html.escape(lc)}</h2><div class='sum'>총 {_fmt(total)} · "
            f"API {_fmt(b['api_s'])} · settle {_fmt(b['settle_s'])} · "
            f"갭 {_fmt(b['gap_s'])} · 스텝 {len(rec['steps'])}</div><div class='tl'>")
        for s in rec["steps"]:
            left = s["t_rel"] * px_per_s
            api_w = max(1.0, s["api_s"] * px_per_s)
            set_w = s["settle_s"] * px_per_s
            gap_w = s["gap_s"] * px_per_s
            fail = " f" if s["category"] == "fail" else ""
            polls = f" · 폴 {s['polls']}회" if s["polls"] else ""
            lbl = (f"{_fmt(s['wall_s'])} (api {_fmt(s['api_s'])}"
                   + (f" + settle {_fmt(s['settle_s'])}" if s["settle_s"] >= 0.5 else "")
                   + f"){polls} · {s['status']}")
            parts.append(
                f"<div class='row{fail}'><span class='nm' title='{html.escape(s['name'])}'>"
                f"{html.escape(s['method'])} {html.escape(s['name'])}</span>"
                f"<span class='bar' style='width:{left + api_w + set_w + 2:.0f}px'>"
                + (f"<i class='gap' style='left:{left - gap_w:.0f}px;width:{gap_w:.0f}px'></i>"
                   if s["gap_s"] >= 2.0 else "")
                + f"<i class='api' style='left:{left:.0f}px;width:{api_w:.0f}px'></i>"
                + (f"<i class='settle' style='left:{left + api_w:.0f}px;width:{set_w:.0f}px'></i>"
                   if set_w >= 1 else "")
                + f"</span><span class='lbl'>{html.escape(lbl)}</span></div>")
        parts.append("</div>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("events", help="런 events.jsonl 경로")
    ap.add_argument("--lifecycle", default=None, help="특정 라이프사이클만")
    ap.add_argument("--out", default="reports/lifecycle_timeline.html")
    args = ap.parse_args(argv)
    tl = build_timeline(load_events(args.events), args.lifecycle)
    if not tl:
        print("step 이벤트가 없습니다 (simulate 런이거나 lifecycle id 오타?)")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(tl, title=Path(args.events).stem),
                   encoding="utf-8")
    for lc, rec in tl.items():
        b = rec["breakdown"]
        print(f"{lc}: 총 {_fmt(rec['total_s'])} = API {_fmt(b['api_s'])} "
              f"+ settle {_fmt(b['settle_s'])} + 갭 {_fmt(b['gap_s'])} "
              f"({len(rec['steps'])} steps)")
    print(f"timeline -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
