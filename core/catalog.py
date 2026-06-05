"""Loader/queries over the generated API catalog (data/api_catalog.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "api_catalog.json"


@dataclass(frozen=True)
class Endpoint:
    key: str
    category: str
    service: str
    name: str
    version: str
    method: str | None
    http_path: str | None
    title: str | None
    doc_url: str

    @property
    def is_read_only(self) -> bool:
        return (self.method or "").upper() == "GET"

    @property
    def is_mutating(self) -> bool:
        return (self.method or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}

    @property
    def has_path_params(self) -> bool:
        return "{" in (self.http_path or "")


@lru_cache(maxsize=1)
def load_catalog() -> list[Endpoint]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"{CATALOG_PATH} not found. Run: python tools/build_catalog.py")
    raw = json.loads(CATALOG_PATH.read_text())
    out = []
    for e in raw:
        out.append(Endpoint(
            key=e["key"], category=e["category"], service=e["service"], name=e["name"],
            version=e["version"], method=e.get("method"), http_path=e.get("http_path"),
            title=e.get("title"), doc_url=e.get("doc_url", ""),
        ))
    return out


def endpoints(category: str | None = None, service: str | None = None,
              method: str | None = None, resolved_only: bool = True) -> list[Endpoint]:
    items = load_catalog()
    if resolved_only:
        items = [e for e in items if e.http_path]
    if category:
        items = [e for e in items if e.category == category]
    if service:
        items = [e for e in items if e.service == service]
    if method:
        items = [e for e in items if (e.method or "").upper() == method.upper()]
    return items
