# IA Console — STATIC DEMO (4-stage)

Backend-free snapshot of the linear **Catalog → Modeling → Testing → Reporting** console, for GitHub Pages. Built by `python -m controlplane.build_ia_demo` — no creds, no cloud, no running server.

## How it was built
Each page's REAL production HTML is fetched in-process via Starlette `TestClient`, then post-processed: the absolute `fetch()` / asset / nav URLs are rewritten to **relative static files written next to the HTML** (`modeling.map.json`, `reporting.map.json`, `resource_graph.js`). On Pages a relative `*.json` fetch just works off the file tree — no `window.fetch` monkeypatch.

- **Catalog** (`index.html` = `catalog.html`): server-rendered, no fetch.
- **Modeling** (`modeling.html`): fetches `modeling.map.json`, draws the DAG via `resource_graph.js`. (Node-click opens the edit form, which has no offline page — graph focus still works.)
- **Testing** (`testing/`): the console2 static bundle (`console2/build_static.py`), which keeps its own chrome.
- **Reporting** (`reporting.html`): fetches `reporting.map.json`, same shared renderer.

## Publish
Copy this directory to the Pages branch: `dashboard-data:/ia-demo/`.

## Files
- `catalog.html`
- `htmx.min.js`
- `index.html`
- `modeling.html`
- `modeling.map.json`
- `reporting.html`
- `reporting.map.json`
- `resource_graph.js`
- `testing/README.md`
- `testing/assets/console2.css`
- `testing/assets/console2.js`
- `testing/assets/mock-api.js`
- `testing/assets/resource_graph.js`
- `testing/assets/viz.js`
- `testing/data/static-data.js`
- `testing/index.html`
- `testing/runtime.html`
