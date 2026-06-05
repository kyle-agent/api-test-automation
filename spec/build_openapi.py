#!/usr/bin/env python3
"""Assemble the scraped docs (data/api_docs.json) into OpenAPI 3.0.

Path roots collide across services (`GET /v1/images` exists in virtualserver,
baremetal and ske; `/v1/volumes` in four storage/compute services), and SCP
serves each service from its own host. A single OpenAPI `paths` map cannot hold
two operations with the same method+path, so we emit **one spec per service**
under data/openapi/<category>__<service>.json, plus an index.

Schema text from the docs ("any of [string, null]", "array[...]",
"enum (a, b)", or a model name link) is mapped best-effort to JSON Schema; the
original string is preserved in `x-raw` so nothing is lost.

Usage: python -m spec.build_openapi

Ported from tools/build_openapi.py (conformance session); logic is unchanged.
Data input/output relocated framework/ -> data/ to match the new layout.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "api_docs.json"
OUTDIR = ROOT / "data" / "openapi"


def map_schema(raw: str, ref: str | None, ref_owner: tuple[str, str] | None) -> dict:
    """Best-effort JSON-Schema for a doc 'Schema' cell. `ref` is a model slug."""
    raw = (raw or "").strip()
    if ref:
        return {"$ref": f"#/components/schemas/{ref}"}
    if not raw or raw.lower() == "none":
        return {}
    s = {"x-raw": raw}
    nullable = "null" in raw
    m = re.search(r"enum\s*\(([^)]*)\)", raw)
    if m:
        s["type"] = "string"
        s["enum"] = [v.strip() for v in m.group(1).split(",") if v.strip()]
    elif raw.startswith("array") or "array[" in raw:
        s["type"] = "array"
        s["items"] = {}
    elif "integer" in raw or "int" in raw:
        s["type"] = "integer"
    elif "number" in raw or "float" in raw or "double" in raw:
        s["type"] = "number"
    elif "boolean" in raw or "bool" in raw:
        s["type"] = "boolean"
    elif "object" in raw:
        s["type"] = "object"
    else:
        s["type"] = "string"
    if nullable:
        s["nullable"] = True
    return s


def build():
    docs = json.loads(DOCS.read_text())
    by_service_ep = defaultdict(list)
    by_service_md = defaultdict(list)
    for k, e in docs["endpoints"].items():
        by_service_ep[(e["category"], e["service"])].append((k, e))
    for k, m in docs["models"].items():
        by_service_md[(m["category"], m["service"])].append((k, m))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    index = []
    for (cat, svc), eps in sorted(by_service_ep.items()):
        spec = {
            "openapi": "3.0.3",
            "info": {
                "title": f"SCP {cat} / {svc}",
                "version": "1.0",
                "x-source": "https://docs.e.samsungsdscloud.com/apireference/",
            },
            "servers": [{"url": f"https://{svc}.{{region}}.e.samsungsdscloud.com",
                         "variables": {"region": {"default": "kr-west1"}}}],
            "paths": {},
            "components": {"schemas": {}},
        }
        # schemas
        for mkey, m in sorted(by_service_md.get((cat, svc), [])):
            props, required = {}, []
            for f in m.get("fields", []):
                sch = map_schema(f.get("schema", ""), f.get("schema_ref"), (cat, svc))
                desc = f["description"] if f.get("description") else None
                if desc:
                    if "$ref" in sch:            # avoid invalid $ref-siblings
                        sch = {"allOf": [sch], "description": desc}
                    else:
                        sch["description"] = desc
                props[f["name"]] = sch
                if f.get("required"):
                    required.append(f["name"])
            sch = {"type": "object", "properties": props}
            if required:
                sch["required"] = required
            spec["components"]["schemas"][m["name"]] = sch
        # paths
        for ekey, e in sorted(eps):
            path = e.get("path")
            method = (e.get("method") or "").lower()
            if not path or not method:
                continue
            params, body = [], None
            for p in e.get("parameters", []):
                if p["in"] == "body":
                    ref = p.get("schema_ref")
                    body = {
                        "required": p.get("required", False),
                        "content": {"application/json": {
                            "schema": map_schema(p.get("schema", ""), ref, (cat, svc))}},
                    }
                    continue
                if p["in"] not in ("path", "query", "header"):
                    continue
                params.append({
                    "name": p["name"],
                    "in": p["in"],
                    "required": bool(p.get("required")) or p["in"] == "path",
                    "description": p.get("description", ""),
                    "schema": map_schema(p.get("schema", ""), p.get("schema_ref"), (cat, svc)),
                })
            responses = {}
            for r in e.get("responses", []):
                responses[str(r["code"])] = {
                    "description": r.get("description", ""),
                    **({"content": {"application/json": {
                        "schema": {"$ref": f"#/components/schemas/{r['schema_ref']}"}}}}
                       if r.get("schema_ref") else {}),
                }
            op = {
                "operationId": e["name"],
                "summary": e.get("description", "")[:120],
                "description": e.get("description", ""),
                "tags": [svc],
                "parameters": params,
                "responses": responses or {"200": {"description": "OK"}},
            }
            if e.get("deprecated"):
                op["deprecated"] = True
            if body:
                op["requestBody"] = body
            spec["paths"].setdefault(path, {})[method] = op

        fname = f"{cat}__{svc}.json"
        (OUTDIR / fname).write_text(json.dumps(spec, indent=2, ensure_ascii=False))
        index.append({"category": cat, "service": svc, "file": fname,
                      "endpoints": len(spec["paths"]),
                      "schemas": len(spec["components"]["schemas"])})

    (OUTDIR / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    tot_e = sum(i["endpoints"] for i in index)
    tot_s = sum(i["schemas"] for i in index)
    print(f"wrote {len(index)} service specs ({tot_e} paths, {tot_s} schemas) -> {OUTDIR}")


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
