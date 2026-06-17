#!/usr/bin/env python3
"""Build the SCP API Regression Platform console bundle for publishing at /platform/.

Runs the three generators and assembles a self-contained static bundle:
  - build_data.py       -> data/model.js     (resource-task model)
  - build_overlays.py   -> data/results.js + data/context.js (results + scope)
  - build_knowledge.py  -> data/kindex.js + kdocs/*.html (knowledge viewer)
then copies console.html (as index.html) + knowledge.html + assets/ + data/ + kdocs/
into <outdir> (default reports/platform).

    python3 poc/scenario-viz/build_console.py [outdir]

In CI the generators read the run's real reports/results + git sha, so /platform
shows live data. Locally they fall back to tools.sample_data.
"""
from __future__ import annotations
import os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "reports" / "platform")
OUT = OUT if OUT.is_absolute() else (ROOT / OUT)


def _run(script: str):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    subprocess.run([sys.executable, str(HERE / script)], cwd=str(ROOT), env=env, check=True)


def main():
    # generators (write into poc/scenario-viz/data + kdocs)
    _run("build_data.py")
    _run("build_overlays.py")
    _run("build_knowledge.py")

    OUT.mkdir(parents=True, exist_ok=True)
    # copy the bundle
    shutil.copyfile(HERE / "console.html", OUT / "index.html")     # entry = console
    shutil.copyfile(HERE / "console.html", OUT / "console.html")
    shutil.copyfile(HERE / "knowledge.html", OUT / "knowledge.html")
    for d in ("assets", "data", "kdocs"):
        dst = OUT / d
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(HERE / d, dst)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    n_kdocs = len(list((OUT / "kdocs").glob("*.html"))) if (OUT / "kdocs").exists() else 0
    try:
        rel = OUT.relative_to(ROOT)
    except ValueError:
        rel = OUT
    print(f"console bundle -> {rel} "
          f"(index.html + knowledge.html + assets + data + {n_kdocs} kdocs)")


if __name__ == "__main__":
    main()
