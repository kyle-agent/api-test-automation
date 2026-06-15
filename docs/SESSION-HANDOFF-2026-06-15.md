# Session handoff — 2026-06-15 07:05 UTC

State at handoff: **working tree clean**, branch `claude/autonomous-progress-check-rklsix`
== `origin/main` == branch, tip `d48a7f7`. **No workflow run in progress** (slot free).
Dispatch = edit `.github/run-request` (KEY=VALUE, last line wins) and push to **main**
(only `branches:[main]` triggers now — see Fixes). One run at a time; wait for the
whole run incl. its **sweep** job to conclude before the next dispatch.

---

## ⏭️ IMMEDIATE NEXT TASK — finish DBaaS minor-version upgrade lifecycles

Owner wants old→new **minor version** upgrades. Mechanism (confirmed from catalog):
`PUT /v1/clusters/{cluster_id}/patch` body `{"backup_before_upgrade": false,
"software_version": "<NEW_VERSION_STRING>"}` (software_version is a STRING, not an id).
Pattern: create cluster on OLD minor → /patch to NEW minor → poll `service_state`
→ verify → reverse teardown (retry_on_status [409]). Verify the patch path per
service in `data/api_catalog.json` (may be namespaced).

**Confirmed creatable old→new pairs (TODAY 2026-06-15; docs are STALE, owner-corrected):**
| engine | create OLD | patch to NEW | enable? | note |
|---|---|---|---|---|
| mysql | **8.4.5** | **8.4.6** | ✅ ENABLE | owner: 8.4.6 current, 8.4.5 previous (doc's 8.0.x EOS 2026-03-19 passed) |
| mariadb | **10.11.8** | **10.11.13** | ✅ ENABLE | 10.6.x EOS 2026-03-19 passed; 10.11.x creatable. (existing lifecycle used kernel-upgrade+major=10 filter — REPLACE with patch) |
| postgresql | **16.8** | **16.10** | ✅ ENABLE | 14.x EOS 2026-05-20 passed; 16.x creatable |
| epas | **16.8** | **16.10** | ✅ ENABLE | 14.x EOS 2026-05-20 passed; 16.x creatable |

Existing: `regression/scenarios/lifecycles/generated__heavy-dbaas.json` has
gen-heavy-{mariadb,mysql,epas}-upgrade; gen-heavy-pg-upgrade may be in another
generated__heavy*.json. Create step resolves the OLD version's
`dbaas_engine_version_id` via the engine-versions lookup (filter by the old minor
string). Template: gen-heavy-ske-upgrade + DBaaS cluster lifecycles. heavy:true.
After building: validate (`python -m regression.scenarios.validate` 0 errors;
`pytest tests/offline/ -q` all pass), commit, then dispatch ONE engine per heavy
window (each makes its own VPC → 5-VPC cap). Suggest **mysql first** (smallest).
UNPROVEN live risk: does /patch require the cluster idle; software_version
validated against availability; is the captured old-version id exactly the old minor.

---

## 🟦 OTHER DISPATCH-READY (slot free; one at a time)
- **aimlops re-run** (`crud_filter=gen-heavy-aimlops`, heavy/mutations/destructive=true):
  SKE closure is LIVE-GREEN; image_id capture fixed (`$.contents[0].id`, capture_soft,
  commit). Next run reaches `POST /v1/aimlops-platform` (release) — body UNPROVEN
  (cpu/memory/storage_class/volume_size guesses), guarded, will RECORD the real
  4xx so we learn the true release body. **Recommended next live run.**
- **clean light wave** (queue/rg/apigw/financial + fixed wave5) — cheap, promotes
  many nodes to VALIDATED.
- DBaaS upgrades (above) once built.

## ⬜ OWNER ACTIONS PENDING
1. **DBaaS** — versions now confirmed (above); no further owner input needed to build.
2. **cloud-ml — DEFERRED by owner** ("나중에 따로 진행"). When resumed:
   - gen-cloudml-chain is enabled; engine has `{env:...}` injection + `requires_env`
     skip; workflow passes 5 SCR secrets (`SCP_SCR_REGISTRY_HOST`, `SCP_SCR_ACCESS_KEY`,
     `SCP_SCR_ACCESS_SECRET_KEY`, `SCP_SCR_PROJECT_ID`, `SCP_CLOUDML_PRODUCT_GROUP_ID`).
   - cloud-ml CREATE does NOT need a pushed image (uses platform base image_id +
     registry-creds binding; cluster pulls internally).
   - **SCR docker push from the CI runner is unverified**: a fresh registry's
     `.scr.public.` subdomain didn't resolve from the runner within 4 min (run
     27525758064). Owner confirms an ESTABLISHED host resolves publicly
     (sample-nayvugfp → 123.41.33.137). `scr_docker_probe` now supports
     `scr_probe_host=<host>` (run-request) / `SCR_PROBE_HOST` env to probe an
     existing registry (no create) — run `docker_probe=true` + `scr_probe_host=
     sample-nayvugfp.scr.public.kr-west1.e.samsungsdscloud.com` + `mutations=false`
     + a non-matching `crud_filter` to get the docker login/push verdict without
     collateral CRUD.
3. **rmtags** (optional) — grant the test key resourcemanager `/v1/resources`+`/v1/tags`
   IAM action to promote the (currently guarded) SRN tag sub-flow to full CRUD.

## ✅ DONE THIS SESSION (all on main)
- **Conformance full sweep** (run 27523653625): 1372 eps — green 900/yellow 454/red 18,
  **NEW defects 0**. red = opaque-validation 13 (`*createcluster` etc.), 5xx-on-bad-input 2
  (baremetal-blockstorage createvolume/group: empty body→500), notfound-200 2
  (servicewatch show*), schema-missing-field 1 (data-ops). All baselined (product bugs).
- **aimlops Stage B**: SKE cluster closure (vpc→subnet→filestorage→cluster RUNNING→
  nodepool→kubeconfig) **live-validated**; live preconditions recorded in
  knowledge/formal/resources/ai-ml__aimlops-platform.yaml.
- **scr repo-create**: 403 → actually **404** (no permission block; registry_id/account).
- **DBaaS upgrade prep** (mariadb enabled w/ kernel-upgrade — to be replaced by patch).
- Light gen-* audit + 2 fixes (apigw-privatelink desc≤50, fw rule 400/409).
- Dashboard drill-down now greys untestable services ("접근성만").

## 🔧 FIXES / LESSONS (don't regress)
- **`{unique}`/`{ualpha}` MUST stay 8 chars** (engine.py): VPC names cap at 20;
  `regrvpc{unique}` broke at 21 (run 27514177331). Now low-16-bit-ts(4hex)+rand(4hex).
- **Duplicate runs**: pushing run-request to BOTH main and the branch fired the
  workflow on each ref → 2× concurrent heavy runs. Fixed with `branches:[main]` on
  the push trigger. Still push run-request to main to dispatch; branch-sync is a no-op.
- For probe/conformance runs, set `mutations=false` (or a non-matching crud_filter)
  so the ADOPT-class CRUD step doesn't run a heavy pass as collateral.
- Can't cancel runs or set GitHub secrets via MCP (403 "Resource not accessible").
- Live-run completion is NOT auto-notified (push trigger, no webhook): re-check via
  mcp__github__actions_get after dispatch.
