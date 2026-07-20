#!/usr/bin/env python3
"""Generate ``docs/INDEX.md`` from each doc's front-matter + H1 title.

The **docs themselves are the single source of truth**: a doc's lifespan lives in
its YAML front-matter (``status`` / ``for``) and its one-line summary is its H1.
This tool reads them and renders the index grouped by tier (design specs at root ·
working plans/handoffs/trackers · decisions). Edit a doc's front-matter or H1 and
re-run — never hand-edit ``INDEX.md``.

    python -m tools.gen_index            # rewrite docs/INDEX.md
    python -m tools.gen_index --check    # exit 1 if INDEX.md is stale (CI gate)
"""
from __future__ import annotations

import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "INDEX.md"

ICON = {"active": "🟢", "draft": "🟡", "blocked": "⛔",
        "superseded": "⚪", "accepted": "🟢"}


def parse(p: pathlib.Path) -> tuple[str, str, str]:
    """Return (status, for, summary) for a doc."""
    text = p.read_text(encoding="utf-8")
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
            body = text[end + 4:]
    status = fm.get("status")
    aud = fm.get("for", "all")
    if status is None:                       # e.g. an ADR — read **Status:**
        m = re.search(r"\*\*Status:\*\*\s*(\w+)", body)
        status = m.group(1).lower() if m else "active"
    # summary = first H1, minus a leading "<stem>.md — " / "<stem> — " prefix
    summary = p.stem
    for line in body.splitlines():
        if line.startswith("# "):
            h1 = line[2:].strip()
            summary = re.sub(rf"^{re.escape(p.stem)}(\.md)?\s*[—:–-]\s*", "", h1) or h1
            break
    return status, aud, summary


def rel(p: pathlib.Path) -> str:
    return p.relative_to(DOCS).as_posix()


def rows_for(subdir: str, recursive: bool = False) -> list[tuple]:
    base = DOCS / subdir if subdir else DOCS
    out = []
    for p in sorted(base.rglob("*.md") if recursive else base.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        st, aud, summ = parse(p)
        out.append((p, st, aud, summ))
    # active first, then by name (already sorted) — superseded sink to the bottom
    out.sort(key=lambda r: (r[1] == "superseded", r[1] == "blocked"))
    return out


def table(rows: list[tuple]) -> str:
    lines = ["| Doc | For | Summary | Status |", "|-----|-----|---------|--------|"]
    for p, st, aud, summ in rows:
        lines.append(f"| [`{rel(p)}`]({rel(p)}) | {aud} | {summ} | {ICON.get(st, '')} {st} |")
    return "\n".join(lines)


def render() -> str:
    tiers = [
        ("Design & specs — `docs/` root (stable)", rows_for("")),
        ("Working — current state", rows_for("working")),
        ("Working — plans", rows_for("working/plans")),
        ("Working — handoffs", rows_for("working/handoffs")),
        ("Working — trackers", rows_for("working/trackers")),
        ("Decisions (ADR)", rows_for("decisions")),
        ("Archive — frozen history (`docs/archive/`, 정본 아님)", rows_for("archive", recursive=True)),
    ]
    total = sum(len(r) for _, r in tiers)
    active = sum(1 for _, r in tiers for row in r if row[1] == "active")
    out = [
        "# docs/ — index",
        "",
        "> **Generated** by `python -m tools.gen_index` from each doc's front-matter "
        "(`status` / `for`) + H1 title. Do not hand-edit — edit the doc and regenerate.",
        f"> {total} docs · {active} active · status ∈ "
        "{🟢 active · 🟡 draft · ⛔ blocked · ⚪ superseded}.",
        "",
    ]
    for title, rows in tiers:
        if not rows:
            continue
        out += [f"## {title}", table(rows), ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str]) -> int:
    new = render()
    if "--check" in argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != new:
            print("docs/INDEX.md is stale — run `python -m tools.gen_index`", file=sys.stderr)
            return 1
        print("docs/INDEX.md is up to date")
        return 0
    OUT.write_text(new, encoding="utf-8")
    print(f"wrote {rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
