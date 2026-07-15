# API Versioning (microversions) — design & roadmap

> SCP Open APIs are **microversioned**. This doc records the wire convention,
> how the version axis will enter the catalog/coverage model, and the staged
> plan. Current implementation status: **Phase 1 (latest-version pinning) is
> live** in `core/http_client.py` + `data/api_versions.json`.

## 1. The wire convention (verified 2026-07-15 against the official docs)

Source: `https://docs.e.samsungsdscloud.com/apireference/api-common/` and
`.../api-common/version/`.

* Request header: `Scp-Api-Version: {product} {version}` — space-separated,
  e.g. `vpc 1.3`, `virtualserver 1.4`. The **product token equals our code
  service name (= host subdomain)**: validated against every
  `request_example` in `data/api_docs.json` (59/59 services, incl.
  `ske`, `scf`, `scr`, `kms`, `security-group`, `iam-identity-center`).
* **Header omitted → the latest CURRENT version is served.** Req/res schemas
  may change between versions, so an unpinned caller silently drifts.
* Version lifecycle statuses (from the docs):
  | STATUS | Meaning |
  |---|---|
  | CURRENT | recommended latest (only 1 kept per product) |
  | SUPPORTED | bug-fix only, no new features |
  | DEPRECATED | ≤ 90 days of `not_before` guarantee remaining |
  | PLANNED | docs-only preview, **not callable** |
* Runtime version discovery: `GET https://{service}.{region}.{env}.samsungsdscloud.com/`
  (host root) returns `{"versions":[{"id":"v1.0","status":"CURRENT","not_before":"YYYY-MM-DD",...}]}`.
  `not_before` = date until which the version is guaranteed unchanged.
* Per-version model docs exist at
  `.../{category}/{service}/models/{model}v{major}dot{minor}/`
  (e.g. `subnetcreaterequestv1dot2`, `...v1dot3`) and per-version API pages at
  `.../apis/{operation}/{version}/`.

## 2. What is implemented now (Phase 1: pin latest)

* `data/api_versions.json` — machine-readable snapshot of the docs' version
  list: `products` (service → latest CURRENT), `supported` (older callable
  versions, the back-compat candidates), `display_names` (docs page name →
  provenance), `unmapped_display_names` (docs products we don't test —
  Object Storage, Payment, Scalable DB, …). Refresh belongs to the
  spec-intel loop, same cadence as `data/api_docs.json`.
* `core/http_client.py::api_version_header(service)` — every live request
  built by `ApiClient.request()` (smoke / CRUD / sweep all go through it)
  carries `Scp-Api-Version: {service} {version}`.
  * Service not in the map → **no header** (current behavior = latest
    CURRENT; safer than a guessed header the gateway may 400).
  * `<svc>-dr` alias pins the base service's version (same product).
  * Headers are not part of the HMAC signing string — no signing interplay.
* Controls:
  * `SCP_API_VERSION_PIN=false` — global kill-switch (default: on).
  * `SCP_API_VERSION_OVERRIDES="vpc=1.2,firewall=1.0"` — per-service pin;
    wins over the map. This is the minimal hook for back-compat runs.
  * An explicit per-call `headers={"Scp-Api-Version": ...}` wins over both.
* Offline contract: `tests/offline/test_api_version_header.py`.

Why pin at all: an unpinned suite tests "whatever is CURRENT today", so a
platform-side version bump changes what we test without any commit on our
side — failures look like regressions but are actually **spec drift** (see §4).
Pinning makes the tested version an explicit, diffable input.

## 3. The version axis in the catalog/coverage model (design)

Today the catalog key is `(service, operation)`. The version axis extends it
to `(service, operation, version)` **lazily** — we never enumerate the full
matrix, only what we intend to run:

* **Catalog**: each endpoint in `data/api_docs.json` already carries
  `support: [{version, min_support}]` and a versioned `doc_url`
  (`.../apis/{op}/{version}/`). spec-intel additionally scrapes the
  per-version model pages (`{model}v1dotN`) *only for endpoints where the
  CURRENT version changed*, producing a **per-version required-parameter
  matrix**: `data/api_version_params.json` (future):
  `{service: {model: {version: {required: [...], enums: {field: [...]}}}}}`.
* **Coverage**: coverage numbers stay reported against CURRENT (one number,
  no matrix explosion). Version-specific results are recorded as ordinary
  `Observation`s with a `version` dimension in the context, so the dashboard
  can slice them without changing the headline metric.
* **Waivers/known-issues**: `data/baselines/known_issues.json` entries gain an
  optional `version` scope, so a bug fixed in 1.3 but present in 1.2 doesn't
  mute the 1.3 run (and vice versa).

## 4. Back-compat regression strategy (design)

Goal: detect *breaking* version transitions, not test every version forever.

1. **current−1 diff run**: for services where `supported` (in
   `data/api_versions.json`) is non-empty, run the read-only smoke +
   validated lifecycles with `SCP_API_VERSION_OVERRIDES="{svc}={current-1}"`
   and diff verdicts against the CURRENT baseline. A test that passes on
   current−1 but fails on CURRENT = **breaking change** (or our payloads are
   version-stale); passes on CURRENT but fails on current−1 = we already
   depend on new-version behavior — flag before the old version deprecates.
2. **Version-bump detection**: spec-intel compares `data/api_versions.json`
   against the live docs (and optionally the host-root discovery endpoint) on
   its refresh cadence. A changed CURRENT version triggers: re-scrape that
   service's models → regenerate payload defaults → targeted re-test of the
   changed endpoints only.
3. **Deprecation watch**: DEPRECATED entries with our pinned version = a
   ticking clock (≤90 days). Surface in the dashboard before the gateway
   drops the version.

## 5. Field case: vpc 1.2 → 1.3 subnet create (2026-07-15)

The motivating incident, kept as the canonical example of silent drift:

* `SubnetCreateRequest` model exists as `subnetcreaterequestv1dot2` **and**
  `subnetcreaterequestv1dot3` in the docs.
* On the 1.3 bump: subnet `type` enum value `GENERAL` → `PUBLIC`, and
  `category` became **required**.
* Our suite was unpinned → the gateway started serving 1.3 semantics the day
  it went CURRENT, and previously-green subnet creates broke with no change
  on our side. With Phase-1 pinning, the same event would surface as a
  *diffable version-list change* (vpc 1.2 → 1.3 in `api_versions.json`)
  followed by a deliberate payload migration — not a mystery regression.

## 6. Roadmap

| Phase | Scope | Status |
|---|---|---|
| **1. Pin latest** | `Scp-Api-Version` on every live call from `data/api_versions.json`; kill-switch + per-service overrides | **done** (this change) |
| **2. Version-aware spec intel** | spec-intel refreshes `api_versions.json`; version-scoped waivers; per-version required-param matrix for changed services; payload defaults regenerated on bump | next |
| **3. Back-compat runs** | scheduled current−1 override runs on high-value services (vpc, virtualserver, ske, DBaaS) with verdict diffing; deprecation watch | later |
| **4. Version-matrix regression** | selective `(service, op, version)` matrix for endpoints with known cross-version schema deltas | final |

Non-goals: testing PLANNED versions (not callable); enumerating all versions
for all services (matrix explosion — only diff-worthy transitions get runs).
