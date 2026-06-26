# console2 — STATIC DEMO SNAPSHOT

This is a backend-free snapshot of the REAL console2 app, for GitHub Pages.
The front-end (`assets/console2.js`) is the UNCHANGED production app; it is
served baked data via a `window.fetch` monkeypatch (`assets/mock-api.js`)
that answers `/api/*` from `data/static-data.js` (`window.__C2_STATIC__`).

Built by `console2/build_static.py` (no creds, no cloud, no running server).
Simulate AND live both surface the same pre-baked SIMULATE run — there is no
real execution in the snapshot.

Baked: model=59 services / 275 resources; graphs=59 single-service (+2 multi) ; sample run=7 lifecycle(s) / 96 API steps.
