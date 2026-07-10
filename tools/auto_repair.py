"""Run-end 자동수리 v1 — 결정적 실패 서명의 규칙 기반 레시피 패치.

Owner 2026-07-10: "니가 알아서 응답 내용 보고 판단해서 고치는 구조" 1단계.
이 세션들이 하루 동안 손으로 반복한 수리 중 **결정적**(판단 여지가 없는)
클래스만 규칙화한다 — 나머지(형식 해독·모델 대조가 필요한 것)는 여전히
에이전트/사람 몫.

규칙 (전부 오늘까지의 실측 클래스):
  R1 settle-409  — step이 409 + 응답에 상태-전이성 서명(NotUpdatableState/
                   ExistInprogress/not-active/EDITING)인데 retry_on_status에
                   409가 없다 → 409 재시도 사다리(8x15s) 추가.
                   (근거: batch-2 ExistInprogress pacing · LB member settle)
  R2 401-retry   — retry_on_status에 401이 있다 → 제거 (401은 재시도 비수렴;
                   knowledge/domain-constraints.md 2026-07-10 규칙).
  R3 timeout-경계 — state-폴이 timeout의 95%+를 소진하고 until 미충족으로
                   끝났다 → timeout 25% 상향(60s 반올림). 관측된 timeout과
                   현재 값이 같을 때만 (중복 상향 방지 — 멱등).

적용 경계: regression/scenarios/lifecycles/*.json 만 자동 패치.
scenarios.json 소속 lifecycle은 REPORT-ONLY (수작업 정본 — 대형 파일 재직렬화
리스크; 에이전트 트리아지로 승격). 패치 후 validate가 0 error가 아니면 전량
롤백. 안전 게이트(SCP_ALLOW_*)는 절대 건드리지 않는다.

사용:
  python -m tools.auto_repair --events <events.jsonl> [--apply] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LC_DIR = _ROOT / "regression" / "scenarios" / "lifecycles"

_SETTLE_RE = re.compile(
    r"NotUpdatableState|ExistInprogress|not[- ]active|Not Active|EDITING",
    re.I)


def _load_events(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def classify(events: list[dict], lifecycles: dict[str, dict],
             sources: dict[str, str]) -> list[dict]:
    """결정적 수리 후보를 찾는다. 반환: [{rule, lifecycle, step, file, detail, patch}]"""
    findings: list[dict] = []
    by_step: dict[tuple, dict] = {}
    for lid, lc in lifecycles.items():
        for s in lc.get("steps", []):
            by_step[(lid, s.get("name"))] = s

    def _src_file(lid: str) -> str | None:
        f = sources.get(lid, "")
        return f if f and f != "scenarios.json" else None

    # R1: settle-409
    seen_r1 = set()
    for e in events:
        if e.get("kind") != "step-end" or e.get("status") != 409:
            continue
        if not _SETTLE_RE.search(str(e.get("resp_snippet") or "")):
            continue
        key = (e.get("lifecycle"), e.get("step"))
        if key in seen_r1:
            continue
        step = by_step.get(key)
        if step is None:
            continue
        ros = step.get("retry_on_status") or []
        if 409 in ros:
            continue  # 이미 사다리 있음 — 멱등
        seen_r1.add(key)
        findings.append({
            "rule": "R1-settle-409", "lifecycle": key[0], "step": key[1],
            "file": _src_file(key[0]),
            "detail": f"409 settle 서명 ({str(e.get('resp_snippet'))[:80]}…) — "
                      f"retry_on_status에 409 없음",
            "patch": {"retry_on_status": sorted(set(ros) | {409}),
                      "retries": step.get("retries", 8) or 8,
                      "retry_interval": step.get("retry_interval", 15) or 15}})

    # R2: 401 in retry ladders (정적 — events 불요, 회귀 방지)
    for (lid, name), step in by_step.items():
        ros = step.get("retry_on_status") or []
        if 401 in ros:
            findings.append({
                "rule": "R2-401-retry", "lifecycle": lid, "step": name,
                "file": _src_file(lid),
                "detail": "retry_on_status에 401 (재시도 비수렴 — 규칙 위반)",
                "patch": {"retry_on_status": [s for s in ros if s != 401]}})

    # R3: timeout 경계 소진 (until 미충족 burn)
    last_poll: dict[tuple, dict] = {}
    for e in events:
        if e.get("kind") == "poll-progress":
            last_poll[(e.get("lifecycle"), e.get("step"))] = e
    for key, e in last_poll.items():
        to = float(e.get("timeout_s") or 0)
        el = float(e.get("elapsed_s") or 0)
        if not to or el < 0.95 * to:
            continue
        step = by_step.get(key)
        if step is None or not step.get("poll"):
            continue
        cur = float(step["poll"].get("timeout", 300))
        if abs(cur - to) > 1:      # 이미 상향됨(관측과 다름) — 중복 방지
            continue
        # until에 이미 명시적으로 그 상태를 기다리는 gone-폴(until_status)은 제외
        if step["poll"].get("until_status"):
            continue
        new_to = int((cur * 1.25 + 59) // 60 * 60)
        findings.append({
            "rule": "R3-timeout-경계", "lifecycle": key[0], "step": key[1],
            "file": _src_file(key[0]),
            "detail": f"폴이 timeout {to:.0f}s의 {el/to*100:.0f}%를 소진 "
                      f"(until 미충족) — 경계 플레이키 위험",
            "patch": {"poll.timeout": new_to}})
    return findings


def apply_findings(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """lifecycles/*.json 소속 finding만 패치. 반환: (applied, skipped)."""
    applied, skipped = [], []
    touched: dict[Path, dict] = {}
    backups: dict[Path, str] = {}
    for f in findings:
        if not f.get("file"):
            skipped.append({**f, "why": "scenarios.json 소속 — report-only"})
            continue
        fp = _LC_DIR / f["file"]
        if fp not in touched:
            backups[fp] = fp.read_text()
            touched[fp] = json.loads(backups[fp])
        doc = touched[fp]
        lc = next((l for l in doc.get("lifecycles", [])
                   if l.get("id") == f["lifecycle"]), None)
        step = next((s for s in (lc or {}).get("steps", [])
                     if s.get("name") == f["step"]), None)
        if step is None:
            skipped.append({**f, "why": "step 미발견"})
            continue
        for k, v in f["patch"].items():
            if k == "poll.timeout":
                step.setdefault("poll", {})["timeout"] = v
            else:
                step[k] = v
        step["_note"] = (str(step.get("_note", "")) +
                         f" || auto-repair {f['rule']} (run-end 자동수리): "
                         f"{f['detail']}").strip(" |")
        applied.append(f)
    for fp, doc in touched.items():
        fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    # 게이트: validate 0 error 아니면 전량 롤백
    if touched:
        r = subprocess.run([sys.executable, "-m", "regression.scenarios.validate"],
                           cwd=str(_ROOT), capture_output=True, text=True)
        if " 0 error(s)" not in (r.stdout + r.stderr):
            for fp, orig in backups.items():
                fp.write_text(orig)
            return [], [{**f, "why": "validate 실패 — 전량 롤백"} for f in findings]
    return applied, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)
    from regression.scenarios.loader import load_lifecycles
    lcs, sources = load_lifecycles(with_sources=True)
    by_id = {l["id"]: l for l in lcs}
    findings = classify(_load_events(a.events), by_id, sources)
    applied, skipped = ([], findings)
    if a.apply and findings:
        applied, skipped = apply_findings(findings)
    out = {"findings": len(findings), "applied": applied, "report_only": skipped}
    if a.as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"auto-repair: 후보 {len(findings)} · 적용 {len(applied)} · "
              f"보고만 {len(skipped)}")
        for f in findings:
            mark = "✔" if f in applied else "·"
            print(f"  {mark} [{f['rule']}] {f['lifecycle']}::{f['step']} — {f['detail'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
