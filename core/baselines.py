"""Per-environment baseline file resolution (확정: 파일 분리 — PLATFORM-PLAN §6).

Baselines mute already-tracked defects so only NEW breakage alarms. They are
environment-specific facts (a backend bug tracked on 검증계 may not exist on
운영계), so each environment profile may carry its OWN copy of any baseline
file using a profile-suffixed name next to the default:

  data/baselines/known_issues.json                  default (and single-env legacy)
  data/baselines/known_issues.prod-kr-west1.json    overrides for that profile

Resolution order: explicit ``profile`` argument, else the SCP_PROFILE_ID the
profile exporter sets (core/profiles.py), else the default file. A missing
profile-suffixed file falls back to the default, so single-environment
operation is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve(path: str | Path, profile: str | None = None) -> Path:
    """Return the profile-suffixed sibling of ``path`` when it exists."""
    p = Path(path)
    prof = (profile if profile is not None
            else os.environ.get("SCP_PROFILE_ID", "")).strip()
    if prof:
        candidate = p.with_name(f"{p.stem}.{prof}{p.suffix}")
        if candidate.exists():
            return candidate
    return p
