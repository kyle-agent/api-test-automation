"""수작업 lifecycle 은퇴 매트릭스 (owner 정책: 시나리오는 task 합성 — 수작업은
동치 검증 후 대체되는 레거시).

기준: LIVE-GREEN 합성 lifecycle들이 수작업 체인의 catalog op 집합을 완전히
덮으면(superset) 그 수작업 체인은 은퇴 대상. 은퇴 절차는 2단계:
  1) enabled:false + _replaced_by 주석 (한 윈도우 동안 커버리지/fail_new 무영향 확인)
  2) 다음 정리 커밋에서 물리 삭제

사용: python -m tools.retirement --green gen-pilot-net-basics,gen-wave2-queue,...
(--green 생략 시 data/baselines/green_lifecycles.json 사용)
"""
import argparse
import json
from pathlib import Path

from regression.scenarios.loader import load_lifecycles

_ROOT = Path(__file__).parent.parent
GREEN_FILE = _ROOT / "data" / "baselines" / "green_lifecycles.json"


def _norm(p):
    return "/".join("*" if "{" in s else s
                    for s in (p or "").split("?")[0].strip("/").split("/"))


def _catalog_index():
    cat = json.load(open(_ROOT / "data" / "api_catalog.json"))
    return {(e["method"].upper(), _norm(e["http_path"]), e["service"]): e["key"]
            for e in cat}


def ops_of(lc, idx):
    out = set()
    svc_default = (lc.get("service") or "").split("/")[-1]
    for s in lc.get("steps", []):
        if "path" not in s:
            continue
        key = idx.get(((s.get("method") or "").upper(), _norm(s["path"]),
                       s.get("service") or svc_default))
        if key:
            out.add(key)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--green", help="comma-separated green composed lifecycle ids")
    args = ap.parse_args()
    if args.green:
        green = set(args.green.split(","))
    else:
        green = set(json.load(open(GREEN_FILE))["green"])
    idx = _catalog_index()
    lcs = load_lifecycles()
    gen_ops = set()
    for lc in lcs:
        if lc["id"] in green:
            gen_ops |= ops_of(lc, idx)
    print(f"green 합성 {len(green)}개가 덮는 op: {len(gen_ops)}")
    hand = [l for l in lcs if not l["id"].startswith(("gen-", "bundle-"))
            and l.get("enabled", True)]
    for lc in sorted(hand, key=lambda x: x["id"]):
        o = ops_of(lc, idx)
        if not o:
            continue
        missing = o - gen_ops
        if not missing:
            print(f"RETIRE  {lc['id']} ({len(o)} ops 전부 합성 그린이 커버)")
        elif len(missing) <= max(1, len(o) // 4):
            print(f"NEAR    {lc['id']} 부족 {len(missing)}: {sorted(missing)}")


if __name__ == "__main__":
    main()
