"""docs/INDEX.md is generated from each doc's front-matter — keep it fresh.

If this fails, a doc's front-matter (`status` / `for`) or H1 changed without
regenerating the index. Fix: `python -m tools.gen_index`.
"""
from tools import gen_index


def test_index_is_up_to_date():
    current = gen_index.OUT.read_text(encoding="utf-8")
    assert current == gen_index.render(), (
        "docs/INDEX.md is stale — run `python -m tools.gen_index` and commit."
    )
