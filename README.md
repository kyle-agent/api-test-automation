# SCP API Regression Test Automation

Automated regression tests for the Samsung Cloud Platform (SCP) Open APIs
documented at <https://docs.e.samsungsdscloud.com/apireference/>
(**15 categories / 60 services / ~1,372 endpoints**).

The suite is **catalog-driven**: the API Reference is parsed once into a
machine-readable inventory, and tests are generated from that inventory — so
new/changed APIs are picked up by re-running the extractor instead of writing
code per endpoint.

## Layout

```
tools/build_catalog.py        # scrape API Reference -> framework/api_catalog.json
framework/
  config.py                   # env-var driven settings (.env), safety gates
  auth.py                     # pluggable Access Key + HMAC-SHA256 signer
  client.py                   # HTTP client: retries/backoff + mutation safety gate
  catalog.py                  # load/query the API inventory
tests/
  smoke/test_catalog_smoke.py # 1 generated reachability test per endpoint
  crud/lifecycles.json        # declarative create->read->update->delete flows
  crud/test_crud_lifecycle.py # runs the lifecycles (opt-in, gated)
conftest.py  pytest.ini  .env.example
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in SCP_BASE_URL + credentials
python tools/build_catalog.py # build/refresh framework/api_catalog.json
```

`build_catalog.py` uses HTTP Range requests (only the page <head> is needed),
retries the gateway's intermittent 503s with backoff, and is **resumable**.

## Running

```bash
# read-only smoke regression across the whole catalog (no resource changes)
pytest tests/smoke -m smoke

# limit scope
pytest tests/smoke -m smoke --category compute --service virtualserver

# CRUD lifecycles (creates/deletes REAL resources — opt in explicitly)
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud
```

Reports are written to `reports/report.html` and `reports/junit.xml`.

## Safety model

This catalog includes destructive operations (`Create*`, `Delete*`, …). To make
sure a regression run never changes real cloud state by accident:

| Operation | Default | Enable with |
|-----------|---------|-------------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **blocked** | `SCP_ALLOW_MUTATIONS=true` |
| `DELETE` | **blocked** | `SCP_ALLOW_DESTRUCTIVE=true` |

The smoke suite only calls read-only `GET`s without path params. Mutating and
parameterised endpoints are exercised by explicit, ordered CRUD lifecycles.

## Adding CRUD coverage

CRUD lifecycles are **declarative** — add an entry to `tests/crud/lifecycles.json`
(no new Python). A lifecycle lists ordered steps; `capture` pulls ids out of one
response (`$.id`) for use in later `{placeholders}`. Set `"enabled": true` to
activate it. Fill `path`/`json` from each API's detail + model pages in the
Reference.

## Authentication

SCP signs Open API calls with **Access Key + HMAC-SHA256**. The exact
signing-string layout and header names are not on the public API Reference
pages (they live in the JS-rendered User Guide), so `framework/auth.py` keeps
the signing string in one overridable method and the header names configurable
via env vars (`SCP_HMAC_*`). Confirm them against a real `200` response; if the
gateway returns `401/403`, adjust `HmacSigner.signing_string` / the header env
vars. `SCP_AUTH_SCHEME=bearer|none` is also supported.

## CI (GitHub Actions)

`.github/workflows/api-regression.yml` runs the read-only smoke regression on a
daily schedule (18:00 UTC / 03:00 KST) on a GitHub-hosted runner, and can also
be run on demand from the **Actions** tab with inputs for category/service and
the mutation safety gates.

Configure once in **Settings → Secrets and variables → Actions**:

| Type | Name | Notes |
|------|------|-------|
| Secret | `SCP_BASE_URL` | gateway base URL |
| Secret | `SCP_ACCESS_KEY` | |
| Secret | `SCP_SECRET_KEY` | |
| Secret | `SCP_PROJECT_ID` | optional |
| Variable | `SCP_HMAC_*`, `SCP_AUTH_SCHEME` | optional — override auth header names/scheme |

Scheduled runs are **read-only** (mutations stay blocked). Mutating CRUD
lifecycles only run via a manual dispatch with `allow_mutations` (and
`allow_destructive` for deletes) checked. Each run uploads `report.html` +
`junit.xml` as an artifact and writes a pass/fail summary to the run page.

> The gateway must be reachable from GitHub-hosted runners. If it is on a
> private network/VPN, change `runs-on: ubuntu-latest` to a `self-hosted`
> runner with network access.
