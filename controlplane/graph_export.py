"""Static Catalog export (R-platform P1, read-only path).

The owner's split: *viewing* defined tasks + reports needs no server — only
*defining/editing* tasks needs the FastAPI control plane. This module renders a
self-contained, read-only **Catalog** (category->service drilldown + focused
dependency graph) from the resource-task model, for GitHub Pages / the
`/platform/` static bundle. The composer stays the single source of truth: the
per-node focus graphs are precomputed with :func:`composer.focus_view`.

    python -m controlplane.graph_export <outdir>     # writes catalog.html + data

No fastapi/network needed (composer is pure). Edits link back to the live
control plane at /planning/resources/<id>.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from regression.scenarios import composer

HERE = Path(__file__).resolve().parent


def _requires_summary(task: dict) -> dict:
    and_deps, groups, creds = composer._norm_requires(task or {})
    return {
        "and": [{"ref": d["ref"], "count": d["count"]} for d in and_deps],
        "one_of": [{"bind": g.get("bind"), "branches": [b["ref"] for b in g["branches"]]}
                   for g in groups],
        "creds": creds,
    }


def build_catalog(model: dict | None = None) -> dict:
    if model is None:
        model = composer.load_model()
    groups_meta = composer.__dict__.get("load_groups")  # not present; load file
    groups = {}
    gpath = Path(composer.DEFAULT_MODEL_DIR) / "_groups.yaml"
    if gpath.exists():
        import yaml
        groups = (yaml.safe_load(gpath.read_text()) or {}).get("groups", {}) or {}

    # reverse index for dependents (one scan)
    dep_index: dict[str, list] = {nid: [] for nid in model}
    for nid, task in model.items():
        and_deps, gps, _ = composer._norm_requires(task or {})
        refs = {d["ref"] for d in and_deps}
        for g in gps:
            refs.update(b["ref"] for b in g["branches"])
        for r in refs:
            if r in dep_index:
                dep_index[r].append(nid)

    nodes, focus = {}, {}
    for nid, task in sorted(model.items()):
        task = task or {}
        service = task.get("service", "")
        cat = service.split("/")[0] if "/" in service else ""
        code = task.get("code") or ""
        group = task.get("group") or ("-".join(code.split("-")[:2]) if code else cat)
        create = task.get("create") or {}
        opts = []
        for oname, ospec in (create.get("options") or {}).items():
            if isinstance(ospec, dict):
                opts.append({"name": oname, "type": ospec.get("type", "?"),
                             "required": bool(ospec.get("required", False)),
                             "values": ospec.get("values")})
            else:
                opts.append({"name": oname, "type": "?", "required": False})
        nodes[nid] = {
            "id": nid, "code": code, "service": service, "category": cat,
            "group": group,
            "group_label": (groups.get(group) or {}).get("label", group),
            "provenance": task.get("provenance", "?"),
            "heavy": bool(task.get("heavy", False)),
            "quota": task.get("quota"),
            "endpoint": create.get("endpoint", ""),
            "requires": _requires_summary(task),
            "options": opts,
            "dependents": sorted(dep_index.get(nid, [])),
            "verify_n": len(task.get("verify") or []),
            "ready_timeout": (task.get("ready") or {}).get("timeout"),
        }
        try:
            focus[nid] = composer.focus_view(nid, model=model)
        except Exception as exc:  # never let one bad node fail the export
            focus[nid] = {"error": str(exc), "nodes": [], "edges": [], "focus": nid}

    val = sum(1 for n in nodes.values() if n["provenance"] == "VALIDATED")
    return {
        "generated_from": "knowledge/formal/resources/*.yaml (via composer)",
        "node_count": len(nodes), "validated": val,
        "groups": {g: {"label": (groups.get(g) or {}).get("label", g),
                       "category": (groups.get(g) or {}).get("category", "")}
                   for g in sorted({n["group"] for n in nodes.values()})},
        "nodes": nodes, "focus": focus,
    }


def _split_endpoint(ep: str):
    m, _, p = (ep or "").partition(" ")
    return m.strip().upper(), p.split("?")[0].strip()


def build_report(model: dict | None = None, observations=None) -> dict:
    """Map a run's per-call timings onto resource-task nodes (P4 / req 5).

    *observations* is the parsed ``observations.jsonl`` (list of dicts with
    ``method, path, status, elapsed_ms``). Each node's ``create.endpoint``
    (method+path) is matched to the calls, giving per-node measured time +
    pass/fail. Returns ``{nodes:{id:{status,elapsed_ms,calls,http}}, observed}``.
    """
    if model is None:
        model = composer.load_model()
    idx: dict = {}
    for o in (observations or []):
        key = ((o.get("method") or "").upper(), (o.get("path") or "").split("?")[0])
        rec = idx.setdefault(key, {"elapsed": [], "status": []})
        if o.get("elapsed_ms") is not None:
            rec["elapsed"].append(float(o["elapsed_ms"]))
        if o.get("status") is not None:
            rec["status"].append(int(o["status"]))
    nodes = {}
    for nid, task in (model or {}).items():
        ep = ((task or {}).get("create") or {}).get("endpoint")
        rec = idx.get(_split_endpoint(ep))
        if rec and rec["status"]:
            worst = max(rec["status"])
            el = rec["elapsed"]
            nodes[nid] = {
                "status": "pass" if worst < 400 else "fail",
                "http": worst, "calls": len(rec["status"]),
                "elapsed_ms": round(sum(el) / len(el), 1) if el else None,
            }
        else:
            nodes[nid] = {"status": "untested", "http": None, "calls": 0,
                          "elapsed_ms": None}
    observed = sum(1 for n in nodes.values() if n["calls"])
    return {"nodes": nodes, "observed": observed,
            "passed": sum(1 for n in nodes.values() if n["status"] == "pass"),
            "failed": sum(1 for n in nodes.values() if n["status"] == "fail")}


def _load_observations(path: str | Path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def export(outdir: str | Path, observations_path: str | Path | None = None) -> Path:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    data = build_catalog()
    (out / "catalog.js").write_text(
        "// AUTO-GENERATED by controlplane/graph_export.py\n"
        "window.CATALOG = " + json.dumps(data, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    shutil.copyfile(HERE / "static" / "graph.js", out / "graph.js")
    (out / "catalog.html").write_text(_CATALOG_HTML, encoding="utf-8")
    (out / "plan.html").write_text(_PLAN_HTML, encoding="utf-8")
    (out / "report.html").write_text(_REPORT_HTML, encoding="utf-8")
    (out / "run.html").write_text(_RUN_HTML, encoding="utf-8")
    # optional per-run timing/result overlay (P4) — only when observations exist
    report = None
    if observations_path:
        report = build_report(observations=_load_observations(observations_path))
    (out / "report.js").write_text(
        "// AUTO-GENERATED by controlplane/graph_export.py\n"
        "window.REPORT = " + json.dumps(report or {"nodes": {}, "observed": 0}) + ";\n",
        encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    extra = f" · report observed={report['observed']}" if report else ""
    print(f"wrote {out}/catalog.html + plan.html + report.html — "
          f"{data['node_count']} nodes, {data['validated']} VALIDATED, "
          f"{len(data['groups'])} groups{extra}")
    return out / "catalog.html"


_CATALOG_HTML = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>자원 카탈로그 (읽기 전용)</title>
<style>
:root{--bg:#0f1720;--panel:#16212e;--panel2:#1c2a3a;--line:#27384b;--ink:#e7eef6;
  --muted:#90a4ba;--accent:#5aa9ff;--val:#3fb27f;--docs:#e0922f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,"Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}.wrap{max-width:1280px;margin:0 auto;padding:20px}
h1{font-size:18px}.muted{color:var(--muted)}code{font-family:ui-monospace,Consolas,monospace}
.cols{display:grid;grid-template-columns:280px 1fr 300px;gap:16px;align-items:start}
@media(max-width:1050px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.panel h2{font-size:14px;margin:0 0 10px}.panel h3{font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.5px;margin:14px 0 6px}
select,input{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:8px;padding:6px 9px;font-size:13px;margin-bottom:8px}
.chk{display:flex;align-items:center;gap:7px;padding:3px 4px;border-radius:6px;cursor:pointer}
.chk:hover{background:var(--panel2)}.chk .dot{width:8px;height:8px;border-radius:50%}
.scroll{max-height:520px;overflow:auto}.svgbox{background:#0f1720;border:1px solid var(--line);
  border-radius:10px;overflow:auto}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;
  color:var(--muted);margin:6px 0}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px}
.chip{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:14px;
  padding:2px 8px;font-size:11.5px;margin:2px}.kv{display:flex;justify-content:space-between;
  padding:3px 0;border-bottom:1px dashed var(--line)}.tbl{width:100%;border-collapse:collapse;font-size:12px}
.tbl td,.tbl th{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line)}
.note{background:var(--panel2);border-left:3px solid var(--accent);border-radius:6px;padding:9px 12px;
  color:var(--muted);font-size:12.5px;margin-top:10px}.foot{margin-top:30px;color:#6b7e93;font-size:12px}
</style></head><body><div class="wrap">
<h1>자원 카탈로그 <span class="muted" style="font-size:13px">— 읽기 전용 (정적).
  <a href="plan.html">Plan</a> · <a href="run.html">Run</a> · <a href="report.html">Report</a> · 정의/수정은 control plane.</span></h1>
<p class="muted" id="sub"></p>
<div class="cols">
  <div class="panel">
    <h2>서비스 선택</h2>
    <label>카테고리<select id="cat"></select></label>
    <label>서비스<select id="svc"></select></label>
    <h3>자원 노드</h3><div class="scroll" id="list"></div>
  </div>
  <div class="panel">
    <h2 id="gtitle"></h2>
    <div class="legend"><span><i style="background:var(--val)"></i>VALIDATED</span>
      <span><i style="background:var(--docs)"></i>docs</span>
      <span><i style="background:#5aa9ff"></i>초점 ★</span>
      <span><i style="background:#b48cff"></i>피의존 ↓</span></div>
    <div class="svgbox"><svg id="svg"></svg></div>
    <p class="muted" id="ghint" style="font-size:12px"></p>
  </div>
  <div class="panel" id="detail"></div>
</div>
<div class="foot">생성: <span id="gen"></span> · composer.focus_view 미리계산. 편집은
  <code>/planning/resources/&lt;id&gt;</code>(FastAPI).</div>
</div>
<script src="catalog.js"></script><script src="graph.js"></script>
<script>
var C=window.CATALOG,N=C.nodes,sel=null;
document.getElementById("sub").textContent=C.node_count+" 노드 · "+C.validated+" VALIDATED · "+Object.keys(C.groups).length+" 그룹";
document.getElementById("gen").textContent=C.generated_from;
var cats=[...new Set(Object.values(N).map(n=>n.category))].sort();
var svcOf={};Object.values(N).forEach(n=>{(svcOf[n.category]=svcOf[n.category]||new Set()).add(n.service);});
function fill(s,arr,v){s.innerHTML=arr.map(x=>'<option '+(x===v?'selected':'')+'>'+x+'</option>').join("");}
function pickFirst(){sel="vpc" in N?"vpc":Object.keys(N)[0];}
pickFirst();
function refresh(){
  var n=N[sel];fill(document.getElementById("cat"),cats,n.category);
  fill(document.getElementById("svc"),[...svcOf[n.category]].sort(),n.service);
  var ids=Object.keys(N).filter(id=>N[id].service===n.service).sort();
  document.getElementById("list").innerHTML=ids.map(id=>'<label class="chk"><input type="radio" name="nd" '+(id===sel?"checked":"")+' data-id="'+id+'"><span class="dot" style="background:'+(N[id].provenance==="VALIDATED"?"#3fb27f":"#e0922f")+'"></span><b>'+id+'</b> <span class="muted" style="font-size:11px">'+(N[id].requires.and.length+N[id].requires.one_of.length)+"↑ "+N[id].dependents.length+"↓</span></label>").join("");
  document.querySelectorAll('#list input').forEach(r=>r.onchange=function(){sel=r.dataset.id;refresh();});
  // graph
  var g=C.focus[sel];
  document.getElementById("gtitle").innerHTML="<code>"+sel+"</code> 의존 관계";
  document.getElementById("ghint").textContent="왼쪽=의존(선행), 오른쪽=피의존(후행). 노드 클릭=초점 이동.";
  if(g&&!g.error)ResourceGraph.render(document.getElementById("svg"),g,{onClick:function(id){if(N[id]){sel=id;refresh();}}});
  else document.getElementById("svg").innerHTML='<text x="12" y="24" fill="#ff8585">'+(g&&g.error||"no graph")+'</text>';
  // detail
  var req=n.requires,reqHtml=req.and.map(d=>'<span class="chip">'+d.ref+(d.count>1?" ×"+d.count:"")+'</span>').join("")+req.one_of.map(o=>'<span class="chip">🔀 '+o.branches.join(" | ")+'</span>').join("")||'<span class="muted">없음</span>';
  var depHtml=n.dependents.length?n.dependents.map(d=>'<span class="chip">'+d+'</span>').join(""):'<span class="muted">없음</span>';
  var optHtml=n.options.length?'<table class="tbl">'+n.options.map(o=>'<tr><td><code>'+o.name+'</code></td><td class="muted">'+o.type+(o.required?" *":"")+'</td></tr>').join("")+'</table>':'<p class="muted">옵션 없음</p>';
  document.getElementById("detail").innerHTML='<h2>'+sel+'</h2>'+
    '<div class="kv"><span>service</span><b>'+n.service+'</b></div>'+
    '<div class="kv"><span>provenance</span><b style="color:'+(n.provenance==="VALIDATED"?"#3fb27f":"#e0922f")+'">'+n.provenance+'</b></div>'+
    '<div class="kv"><span>endpoint</span><b style="font-size:11px">'+(n.endpoint||"—")+'</b></div>'+
    (n.quota?'<div class="kv"><span>quota</span><b>⛔ '+n.quota+'</b></div>':'')+
    '<h3>requires</h3><div>'+reqHtml+'</div>'+
    '<h3>피의존</h3><div>'+depHtml+'</div>'+
    '<h3>options</h3>'+optHtml+
    '<div class="note">이 화면은 읽기 전용입니다. 정의/수정은 control plane <code>/planning/resources/'+sel+'</code> 에서.</div>';
}
document.getElementById("cat").onchange=function(e){var c=e.target.value;var fs=[...svcOf[c]].sort()[0];sel=Object.keys(N).find(id=>N[id].service===fs);refresh();};
document.getElementById("svc").onchange=function(e){var s=e.target.value;sel=Object.keys(N).find(id=>N[id].service===s);refresh();};
refresh();
</script></body></html>"""


_PLAN_HTML = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>합성 Plan 미리보기 (읽기 전용)</title>
<style>
:root{--bg:#0f1720;--panel:#16212e;--panel2:#1c2a3a;--line:#27384b;--ink:#e7eef6;
  --muted:#90a4ba;--accent:#5aa9ff;--val:#3fb27f;--docs:#e0922f;--shared:#ffd166}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,"Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}.wrap{max-width:1280px;margin:0 auto;padding:20px}
h1{font-size:18px}.muted{color:var(--muted)}code{font-family:ui-monospace,Consolas,monospace}
.cols{display:grid;grid-template-columns:300px 1fr 280px;gap:16px;align-items:start}
@media(max-width:1050px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.panel h2{font-size:14px;margin:0 0 10px}.panel h3{font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.5px;margin:14px 0 6px}
input,select{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:8px;padding:6px 9px;font-size:13px;margin-bottom:8px}
.chk{display:flex;align-items:center;gap:7px;padding:3px 4px;border-radius:6px;cursor:pointer}
.chk:hover{background:var(--panel2)}.chk .dot{width:8px;height:8px;border-radius:50%}
.scroll{max-height:520px;overflow:auto}.svgbox{background:#0f1720;border:1px solid var(--line);
  border-radius:10px;overflow:auto}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;
  color:var(--muted);margin:6px 0}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed var(--line)}
.btn{background:var(--accent);color:#06121f;border:none;border-radius:8px;padding:8px 12px;
  font-weight:600;cursor:pointer;font-size:13px;display:inline-block}.chip{display:inline-block;
  background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:2px 8px;font-size:11.5px;margin:2px}
.note{background:var(--panel2);border-left:3px solid var(--accent);border-radius:6px;padding:9px 12px;
  color:var(--muted);font-size:12.5px;margin-top:10px}.foot{margin-top:30px;color:#6b7e93;font-size:12px}
</style></head><body><div class="wrap">
<h1>합성 Plan 미리보기 <span class="muted" style="font-size:13px">— 읽기 전용 (정적).
  <a href="catalog.html">← 카탈로그</a> · <a href="run.html">Run</a> · <a href="report.html">Report</a> · 실제 합성/실행은 control plane.</span></h1>
<p class="muted">여러 자원을 고르면 의존 폐포의 합집합과 <b>공통 선행자원(dedup)</b>을 미리 봅니다.
  실제 compose+draft+실행은 <code>/planning/resources/compose</code>(FastAPI)에서.</p>
<div class="cols">
  <div class="panel">
    <h2>타깃 선택</h2><input type="search" id="q" placeholder="검색…">
    <div class="scroll" id="list"></div>
  </div>
  <div class="panel">
    <h2 id="gtitle">합성 폐포</h2>
    <div class="legend"><span><i style="background:#11314f"></i>대상 ★</span>
      <span><i style="background:#1c2a3a"></i>선행</span>
      <span><i style="background:#ffd166"></i>공유(dedup)</span>
      <span style="color:#3fb27f">VALIDATED</span><span style="color:#e0922f">docs</span>
      <span>세로 띠=level</span></div>
    <div class="svgbox"><svg id="svg"></svg></div>
  </div>
  <div class="panel">
    <h2>plan 요약</h2><div id="sum"></div>
    <a class="btn" id="live" href="#" style="margin-top:10px">control plane에서 실행 →</a>
  </div>
</div>
<div class="foot">폐포·dedup·level은 클라이언트가 모델 의존(requires)으로 계산(미리보기). 실제 합성은
  서버 composer가 동일 규칙으로 수행.</div>
</div>
<script src="catalog.js"></script><script src="graph.js"></script>
<script>
var C=window.CATALOG,N=C.nodes,T=new Set();
["ske-cluster","mysql-cluster","private-nat"].forEach(id=>{if(N[id])T.add(id);});
if(!T.size)Object.keys(N).slice(0,2).forEach(id=>T.add(id));
function deps(id){var n=N[id];if(!n)return[];var out=[];
  n.requires.and.forEach(d=>{if(N[d.ref])out.push(d.ref);});
  n.requires.one_of.forEach(o=>{var b=(o.branches||[]).filter(x=>N[x])[0];if(b)out.push(b);});
  return out;}
function closure(ids){var seen=new Set(),st=ids.slice();while(st.length){var x=st.pop();
  if(!N[x]||seen.has(x))continue;seen.add(x);deps(x).forEach(r=>st.push(r));}return seen;}
function levels(set){var dep={};function d(n,stk){if(n in dep)return dep[n];if(stk.has(n))return 0;
  stk.add(n);var ds=deps(n).filter(r=>set.has(r)).map(r=>d(r,stk));stk.delete(n);
  return dep[n]=ds.length?1+Math.max.apply(0,ds):0;}set.forEach(n=>d(n,new Set()));return dep;}
function build(){
  var ids=[...T];var per=ids.map(t=>closure([t]));
  var union=new Set();per.forEach(s=>s.forEach(x=>union.add(x)));
  var dep=levels(union);
  var share={};union.forEach(id=>share[id]=per.filter(s=>s.has(id)).length);
  var shared=new Set([...union].filter(id=>share[id]>1));
  var nodes=[...union].map(id=>({id:id,service:N[id].service,provenance:N[id].provenance,
    quota:N[id].quota,heavy:N[id].heavy,options:N[id].options.map(o=>o.name),
    level:dep[id],is_target:T.has(id),shared:shared.has(id)}));
  var edges=[];union.forEach(id=>deps(id).forEach(r=>{if(union.has(r))edges.push({from:r,to:id});}));
  var quota={};union.forEach(id=>{if(N[id].quota)quota[N[id].quota]=(quota[N[id].quota]||0)+1;});
  var naive=per.reduce((a,s)=>a+s.size,0);
  return {graph:{nodes:nodes,edges:edges,levels:[...new Set(Object.values(dep))].sort()},
    union:union,shared:shared,quota:quota,naive:naive};
}
function list(){var q=(document.getElementById("q").value||"").toLowerCase();
  var ids=Object.keys(N).sort((a,b)=>N[a].category<N[b].category?-1:1)
    .filter(id=>!q||(id+N[id].service).toLowerCase().includes(q));
  document.getElementById("list").innerHTML=ids.map(id=>'<label class="chk"><input type="checkbox" '+(T.has(id)?"checked":"")+' data-id="'+id+'"><span class="dot" style="background:'+(N[id].provenance==="VALIDATED"?"#3fb27f":"#e0922f")+'"></span><b>'+id+'</b></label>').join("");
  document.querySelectorAll('#list input').forEach(cb=>cb.onchange=function(){cb.checked?T.add(cb.dataset.id):T.delete(cb.dataset.id);draw();});}
function draw(){
  if(!T.size){document.getElementById("svg").innerHTML="";document.getElementById("sum").innerHTML='<p class="muted">타깃을 선택하세요.</p>';return;}
  var b=build();
  ResourceGraph.render(document.getElementById("svg"),b.graph,{onClick:function(id){T.has(id)?T.delete(id):T.add(id);if(T.size){list();draw();}}});
  document.getElementById("gtitle").innerHTML='합성 폐포 <span class="muted" style="font-weight:400;font-size:12px">· '+b.union.size+' 노드</span>';
  var saved=b.naive-b.union.size;
  document.getElementById("sum").innerHTML=
    '<div class="kv"><span>대상</span><b>'+T.size+'</b></div>'+
    '<div class="kv"><span>폐포 노드</span><b>'+b.union.size+'</b></div>'+
    '<div class="kv"><span>dedup 절감</span><b>'+b.naive+'→'+b.union.size+' (−'+saved+')</b></div>'+
    '<div class="kv"><span>peak quota</span><b>'+(Object.keys(b.quota).map(k=>k+"×"+b.quota[k]).join(", ")||"—")+'</b></div>'+
    (b.shared.size?'<h3>공유 인프라(1회)</h3><div>'+[...b.shared].map(s=>'<span class="chip">'+s+'</span>').join("")+'</div>':"");
  document.getElementById("live").href="/planning/resources/compose?"+[...T].map(t=>"targets="+encodeURIComponent(t)).join("&");
}
list();draw();
document.getElementById("q").oninput=list;
</script></body></html>"""


_REPORT_HTML = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>실행 결과 + 수행시간 (읽기 전용)</title>
<style>
:root{--bg:#0f1720;--panel:#16212e;--panel2:#1c2a3a;--line:#27384b;--ink:#e7eef6;
  --muted:#90a4ba;--accent:#5aa9ff;--val:#3fb27f;--docs:#e0922f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,"Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}.wrap{max-width:1280px;margin:0 auto;padding:20px}
h1{font-size:18px}.muted{color:var(--muted)}code{font-family:ui-monospace,Consolas,monospace}
.cols{display:grid;grid-template-columns:300px 1fr 320px;gap:16px;align-items:start}
@media(max-width:1050px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.panel h2{font-size:14px;margin:0 0 10px}.panel h3{font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.5px;margin:14px 0 6px}
input{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:8px;padding:6px 9px;font-size:13px;margin-bottom:8px}
.chk{display:flex;align-items:center;gap:7px;padding:3px 4px;border-radius:6px;cursor:pointer}
.chk:hover{background:var(--panel2)}.chk .dot{width:8px;height:8px;border-radius:50%}
.scroll{max-height:520px;overflow:auto}.svgbox{background:#0f1720;border:1px solid var(--line);
  border-radius:10px;overflow:auto}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;
  color:var(--muted);margin:6px 0}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed var(--line)}
.tbl{width:100%;border-collapse:collapse;font-size:12px}.tbl td,.tbl th{text-align:left;padding:4px 6px;
  border-bottom:1px solid var(--line)}.tbl th{color:var(--muted);font-size:11px}
.warn{background:#33291a;border:1px solid #6e5a2a;border-radius:8px;padding:10px 12px;color:#ffd9a0;font-size:13px}
.foot{margin-top:30px;color:#6b7e93;font-size:12px}
</style></head><body><div class="wrap">
<h1>실행 결과 + 수행시간 <span class="muted" style="font-size:13px">— 읽기 전용 (정적).
  <a href="catalog.html">← 카탈로그</a> · <a href="plan.html">Plan</a> · <a href="run.html">Run</a></span></h1>
<p class="muted">최근 런의 <code>observations.jsonl</code>(호출별 <code>elapsed_ms</code>)을
  노드의 create endpoint로 매칭해 <b>단계별 수행시간</b>과 pass/fail을 그래프에 색칠합니다.</p>
<div id="banner"></div>
<div class="cols">
  <div class="panel"><h2>대상 선택</h2><input type="search" id="q" placeholder="검색…">
    <div class="scroll" id="list"></div></div>
  <div class="panel">
    <h2 id="gtitle">결과 그래프</h2>
    <div class="legend"><span><i style="background:#3fb27f"></i>pass</span>
      <span><i style="background:#ff6b6b"></i>fail</span>
      <span><i style="background:#3a4654"></i>미측정</span><span>세로 띠=level</span></div>
    <div class="svgbox"><svg id="svg"></svg></div>
  </div>
  <div class="panel"><h2>수행시간</h2><div id="sum"></div>
    <h3>단계별</h3><div class="scroll"><table class="tbl" id="tbl"></table></div></div>
</div>
<div class="foot">측정 시간은 create 호출의 평균 <code>elapsed_ms</code>. level 병렬 wall-clock =
  Σ(단계별 최장). 데이터원: dashboard-data <code>observations.jsonl</code>(런별).</div>
</div>
<script src="catalog.js"></script><script src="report.js"></script><script src="graph.js"></script>
<script>
var C=window.CATALOG,N=C.nodes,R=(window.REPORT||{nodes:{},observed:0}),RN=R.nodes||{},T=new Set();
if(!R.observed){document.getElementById("banner").innerHTML='<div class="warn">아직 측정된 런 데이터가 없습니다 (observed=0). 라이브 CRUD 런 후 자동 채워집니다 — 지금은 구조만 미리보기.</div>';}
["ske-cluster","mysql-cluster","private-nat"].forEach(id=>{if(N[id])T.add(id);});
if(!T.size)Object.keys(N).slice(0,2).forEach(id=>T.add(id));
function deps(id){var n=N[id];if(!n)return[];var o=[];n.requires.and.forEach(d=>{if(N[d.ref])o.push(d.ref);});
  n.requires.one_of.forEach(g=>{var b=(g.branches||[]).filter(x=>N[x])[0];if(b)o.push(b);});return o;}
function closure(ids){var s=new Set(),st=ids.slice();while(st.length){var x=st.pop();if(!N[x]||s.has(x))continue;s.add(x);deps(x).forEach(r=>st.push(r));}return s;}
function levels(set){var d={};function f(n,k){if(n in d)return d[n];if(k.has(n))return 0;k.add(n);
  var ds=deps(n).filter(r=>set.has(r)).map(r=>f(r,k));k.delete(n);return d[n]=ds.length?1+Math.max.apply(0,ds):0;}set.forEach(n=>f(n,new Set()));return d;}
var COL={pass:{fill:"#14322a",stroke:"#3fb27f",badge:"✓"},fail:{fill:"#3a1717",stroke:"#ff6b6b",badge:"✕"},
  untested:{fill:"#222e3c",stroke:"#3a4654",badge:""}};
function list(){var q=(document.getElementById("q").value||"").toLowerCase();
  var ids=Object.keys(N).sort((a,b)=>N[a].category<N[b].category?-1:1).filter(id=>!q||(id+N[id].service).toLowerCase().includes(q));
  document.getElementById("list").innerHTML=ids.map(id=>'<label class="chk"><input type="checkbox" '+(T.has(id)?"checked":"")+' data-id="'+id+'"><span class="dot" style="background:'+(N[id].provenance==="VALIDATED"?"#3fb27f":"#e0922f")+'"></span><b>'+id+'</b></label>').join("");
  document.querySelectorAll('#list input').forEach(cb=>cb.onchange=function(){cb.checked?T.add(cb.dataset.id):T.delete(cb.dataset.id);draw();});}
function draw(){
  if(!T.size){document.getElementById("svg").innerHTML="";return;}
  var set=closure([...T]),dep=levels(set);
  var nodes=[...set].map(id=>({id:id,service:N[id].service,provenance:N[id].provenance,quota:N[id].quota,
    heavy:N[id].heavy,level:dep[id],is_target:T.has(id)}));
  var edges=[];set.forEach(id=>deps(id).forEach(r=>{if(set.has(r))edges.push({from:r,to:id});}));
  ResourceGraph.render(document.getElementById("svg"),{nodes:nodes,edges:edges,levels:[...new Set(Object.values(dep))].sort()},
    {overlay:function(id){var st=(RN[id]||{}).status||"untested";return COL[st];},
     onClick:function(id){T.has(id)?T.delete(id):T.add(id);if(T.size){list();draw();}}});
  // timing: per-level max of measured elapsed
  var byL={};[...set].forEach(id=>{(byL[dep[id]]=byL[dep[id]]||[]).push(id);});
  var seq=0,wall=0;Object.keys(byL).sort((a,b)=>a-b).forEach(l=>{var mx=0;byL[l].forEach(id=>{var e=(RN[id]||{}).elapsed_ms||0;seq+=e;mx=Math.max(mx,e);});wall+=mx;});
  var obs=[...set].filter(id=>(RN[id]||{}).calls).length;
  document.getElementById("gtitle").innerHTML='결과 그래프 <span class="muted" style="font-weight:400;font-size:12px">· '+set.size+' 노드 · 측정 '+obs+'</span>';
  document.getElementById("sum").innerHTML=
    '<div class="kv"><span>폐포 노드</span><b>'+set.size+'</b></div>'+
    '<div class="kv"><span>측정됨</span><b>'+obs+'</b></div>'+
    '<div class="kv"><span>순차 합계</span><b>'+Math.round(seq)+' ms</b></div>'+
    '<div class="kv"><span>레벨 병렬 wall</span><b style="color:#3fb27f">'+Math.round(wall)+' ms</b></div>'+
    '<div class="kv"><span>가속</span><b>'+(wall?(seq/wall).toFixed(2):"—")+'×</b></div>';
  var rows=[...set].sort((a,b)=>dep[a]-dep[b]).map(function(id){var r=RN[id]||{};
    return '<tr><td><code>'+id+'</code></td><td class="muted">L'+dep[id]+'</td>'+
      '<td>'+(r.status||"untested")+'</td><td>'+(r.http||"")+'</td>'+
      '<td>'+(r.elapsed_ms!=null?Math.round(r.elapsed_ms)+" ms":"—")+'</td></tr>';}).join("");
  document.getElementById("tbl").innerHTML='<tr><th>node</th><th>L</th><th>status</th><th>http</th><th>elapsed</th></tr>'+rows;
}
list();draw();document.getElementById("q").oninput=list;
</script></body></html>"""


_RUN_HTML = r"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>실행 구조 — 레벨 병렬 (읽기 전용)</title>
<style>
:root{--bg:#0f1720;--panel:#16212e;--panel2:#1c2a3a;--line:#27384b;--ink:#e7eef6;
  --muted:#90a4ba;--accent:#5aa9ff;--val:#3fb27f;--docs:#e0922f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,"Noto Sans KR",sans-serif}
a{color:var(--accent);text-decoration:none}.wrap{max-width:1280px;margin:0 auto;padding:20px}
h1{font-size:18px}.muted{color:var(--muted)}code{font-family:ui-monospace,Consolas,monospace}
.cols{display:grid;grid-template-columns:300px 1fr 300px;gap:16px;align-items:start}
@media(max-width:1050px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.panel h2{font-size:14px;margin:0 0 10px}.panel h3{font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.5px;margin:14px 0 6px}
input{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:8px;padding:6px 9px;font-size:13px;margin-bottom:8px}
.chk{display:flex;align-items:center;gap:7px;padding:3px 4px;border-radius:6px;cursor:pointer}
.chk:hover{background:var(--panel2)}.chk .dot{width:8px;height:8px;border-radius:50%}
.scroll{max-height:520px;overflow:auto}.svgbox{background:#0f1720;border:1px solid var(--line);
  border-radius:10px;overflow:auto}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;
  color:var(--muted);margin:6px 0}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed var(--line)}
.note{background:var(--panel2);border-left:3px solid var(--accent);border-radius:6px;padding:9px 12px;
  color:var(--muted);font-size:12.5px;margin-top:10px}.foot{margin-top:30px;color:#6b7e93;font-size:12px}
.lvl{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;background:var(--panel2)}
.lvl b{color:#5aa9ff}.pill{display:inline-block;background:#16212e;border:1px solid var(--line);
  border-radius:12px;padding:1px 7px;font-size:11px;margin:2px}
</style></head><body><div class="wrap">
<h1>실행 구조 — 레벨 병렬 <span class="muted" style="font-size:13px">— 읽기 전용 (정적).
  <a href="catalog.html">← 카탈로그</a> · <a href="plan.html">Plan</a> · <a href="report.html">Report</a></span></h1>
<p class="muted">합성 plan을 <b>실행 단계(level)</b>로 나눠 보여줍니다 — 같은 단계의 독립 노드는
  <b>병렬 실행 가능</b>, 다음 단계는 이전 단계가 모두 끝나야 시작(배리어). 실시간 상태(생성중/완료/실패)는
  control plane <code>/runs/{id}/graph</code>(oplog)에서 이 그래프에 색칠됩니다.</p>
<div class="cols">
  <div class="panel"><h2>대상 선택</h2><input type="search" id="q" placeholder="검색…">
    <div class="scroll" id="list"></div></div>
  <div class="panel"><h2 id="gtitle">실행 그래프 (level = 세로 띠)</h2>
    <div class="legend"><span><i style="background:#11314f"></i>대상 ★</span>
      <span style="color:#3fb27f">VALIDATED</span><span style="color:#e0922f">docs</span>
      <span>🜂 heavy · 세로 띠=병렬 단계</span></div>
    <div class="svgbox"><svg id="svg"></svg></div></div>
  <div class="panel"><h2>단계별 (병렬)</h2><div id="levels"></div>
    <div id="sum"></div>
    <div class="note">추정 시간은 모델 <code>ready.timeout</code> 기반(worst-case). 실측은
      Report(observations)·실시간은 control plane.</div></div>
</div>
<div class="foot">레벨 = 위상 깊이(longest-path). 같은 깊이 = 서로 의존 없음 = 병렬 후보.
  실제 레벨 병렬 실행(엔진)은 별도 트랙(P3b).</div>
</div>
<script src="catalog.js"></script><script src="graph.js"></script>
<script>
var C=window.CATALOG,N=C.nodes,T=new Set();
["ske-cluster","mysql-cluster","private-nat"].forEach(id=>{if(N[id])T.add(id);});
if(!T.size)Object.keys(N).slice(0,2).forEach(id=>T.add(id));
function deps(id){var n=N[id];if(!n)return[];var o=[];n.requires.and.forEach(d=>{if(N[d.ref])o.push(d.ref);});
  n.requires.one_of.forEach(g=>{var b=(g.branches||[]).filter(x=>N[x])[0];if(b)o.push(b);});return o;}
function closure(ids){var s=new Set(),st=ids.slice();while(st.length){var x=st.pop();if(!N[x]||s.has(x))continue;s.add(x);deps(x).forEach(r=>st.push(r));}return s;}
function levels(set){var d={};function f(n,k){if(n in d)return d[n];if(k.has(n))return 0;k.add(n);
  var ds=deps(n).filter(r=>set.has(r)).map(r=>f(r,k));k.delete(n);return d[n]=ds.length?1+Math.max.apply(0,ds):0;}set.forEach(n=>f(n,new Set()));return d;}
function dur(id){var n=N[id];var c=n.heavy?20:5;var r=n.ready_timeout?Math.round(n.ready_timeout*0.5):(n.heavy?45:0);return c+r+(n.verify_n||0)*3;}
function list(){var q=(document.getElementById("q").value||"").toLowerCase();
  var ids=Object.keys(N).sort((a,b)=>N[a].category<N[b].category?-1:1).filter(id=>!q||(id+N[id].service).toLowerCase().includes(q));
  document.getElementById("list").innerHTML=ids.map(id=>'<label class="chk"><input type="checkbox" '+(T.has(id)?"checked":"")+' data-id="'+id+'"><span class="dot" style="background:'+(N[id].provenance==="VALIDATED"?"#3fb27f":"#e0922f")+'"></span><b>'+id+'</b></label>').join("");
  document.querySelectorAll('#list input').forEach(cb=>cb.onchange=function(){cb.checked?T.add(cb.dataset.id):T.delete(cb.dataset.id);draw();});}
function draw(){
  if(!T.size){document.getElementById("svg").innerHTML="";return;}
  var set=closure([...T]),dep=levels(set);
  var nodes=[...set].map(id=>({id:id,service:N[id].service,provenance:N[id].provenance,quota:N[id].quota,heavy:N[id].heavy,level:dep[id],is_target:T.has(id)}));
  var edges=[];set.forEach(id=>deps(id).forEach(r=>{if(set.has(r))edges.push({from:r,to:id});}));
  ResourceGraph.render(document.getElementById("svg"),{nodes:nodes,edges:edges,levels:[...new Set(Object.values(dep))].sort()},
    {onClick:function(id){T.has(id)?T.delete(id):T.add(id);if(T.size){list();draw();}}});
  var byL={};[...set].forEach(id=>{(byL[dep[id]]=byL[dep[id]]||[]).push(id);});
  var seq=0,wall=0;var levs=Object.keys(byL).map(Number).sort((a,b)=>a-b);
  document.getElementById("levels").innerHTML=levs.map(function(l){
    var mx=0;byL[l].forEach(id=>{var d=dur(id);seq+=d;mx=Math.max(mx,d);});wall+=mx;
    return '<div class="lvl"><b>L'+l+'</b> <span class="muted">('+byL[l].length+' 동시 · ~'+mx+'s)</span><br>'+byL[l].sort().map(id=>'<span class="pill">'+(N[id].heavy?"🜂":"")+id+'</span>').join("")+'</div>';}).join("");
  document.getElementById("gtitle").innerHTML='실행 그래프 <span class="muted" style="font-weight:400;font-size:12px">· '+set.size+' 노드 · '+levs.length+' 단계</span>';
  document.getElementById("sum").innerHTML='<div class="kv"><span>순차 합계(추정)</span><b>'+seq+'s</b></div>'+
    '<div class="kv"><span>레벨 병렬 wall</span><b style="color:#3fb27f">'+wall+'s</b></div>'+
    '<div class="kv"><span>가속</span><b>'+(wall?(seq/wall).toFixed(2):"—")+'×</b></div>';
}
list();draw();document.getElementById("q").oninput=list;
</script></body></html>"""


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "reports/catalog-static"
    obs = sys.argv[2] if len(sys.argv) > 2 else None
    export(out, obs)
