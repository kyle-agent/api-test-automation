# LIVE-READINESS-GATES — disabled-lifecycle inventory (IB-023)

> **Owner / reader.** This doc is read by the **run-dispatch session** (Meta-Orch,
> the only window that pushes `.github/run-request`) **before each heavy/live
> window**, to decide which `enabled:false` lifecycles are ready to flip + dispatch
> vs. which are still blocked. It does NOT flip any flag — flipping `enabled:true`
> and dispatching stays with run-dispatch under the one-run-at-a-time owner rule.
> Built offline (Tier-L) by Track ① platform-improver from a grep of
> `regression/scenarios/lifecycles/*.json` cross-referenced with
> `docs/IMPROVEMENT-BACKLOG.md` + `knowledge/validated-facts.md`. Keep in sync when
> a lifecycle is enabled, when an `_disabled_reason` changes, or when a blocking IB
> closes.

## fix_status legend
- **DONE-MODELED** — blocking cause fixed in model/lifecycle; only live re-validation remains (e.g. IB-012/013).
- **BLOCKED-ENGINE** — needs an engine capability (IB-009 nested capture, IB-010 multipart, IB-014 poll-until-capture).
- **BLOCKED-OWNER** — needs a credential / license / console step / product-bug fix → maps to STOP criteria 1/2/3/4.
- **STALE** — `_disabled_reason` predates a fix that may already address it; flag for Meta-Orch re-check before the window.
- **TIMING-GATED** — model correct; needs a dedicated window (resource must age past a backup/state boundary). Live-runnable but only in its own window.

## Summary count (total = 26 disabled lifecycles)
| fix_status | count | ready_for_live |
|---|---|---|
| DONE-MODELED | 2 | Y |
| TIMING-GATED | 5 | Y (dedicated window) |
| BLOCKED-ENGINE | 3 | N |
| BLOCKED-OWNER | 13 | N |
| STALE (re-check) | 3 | ? (Meta-Orch judgment) |

## Inventory

| id | service | heavy | fix_status | ready_for_live | blocking_IB | next_window | _disabled_reason (short) |
|---|---|---|---|---|---|---|---|
| gen-wave5-vpce | networking/vpc | Y | DONE-MODELED | Y | IB-013 | next heavy | VPC_ENDPOINT-type subnet now wired; modeled, no live 2xx yet |
| gen-wave5-privnat | networking/vpc | Y | DONE-MODELED | Y | IB-012 | next heavy | tgw-vpc-connection node makes TGW Connectable; modeled, no live 2xx yet |
| gen-heavy-pg-restore | database/postgresql | Y | TIMING-GATED | Y | IB-006 | post-backup window (after 02:xx KST / pre-aged cluster) | backups are SCHEDULED; no backup exists right after create → 400 |
| gen-heavy-epas-restore | database/epas | Y | TIMING-GATED | Y | IB-006 | post-backup window | same backup-timing gate (PG mirror) |
| gen-heavy-mysql-restore | database/mysql | Y | TIMING-GATED | Y | IB-006 | post-backup window | same backup-timing gate (PG mirror) |
| gen-heavy-mariadb-restore | database/mariadb | Y | TIMING-GATED | Y | IB-006 | post-backup window | same backup-timing gate (PG mirror) |
| gen-heavy-backup-restore | storage/backup | Y | TIMING-GATED | Y | IB-006 | post-backup-job window | restore-target only appears after FULL image backup SUCCEEDs; creates new billable server |
| gen-heavy-backup | storage/backup | Y | BLOCKED-ENGINE | N | IB-014 | after IB-014 lands | backup-target lookup returns empty list right after server ACTIVE; needs poll-until-capture |
| gen-wave5-iam-saml | management/iam | N | BLOCKED-ENGINE | N | IB-010 | after IB-010 (or owner waive) | SAML provider needs multipart/form-data; engine is JSON-only (400 Field required) |
| gen-wave5-swatch-alert | management/servicewatch | N | BLOCKED-ENGINE | N | IB-009 | after IB-009 lands | alert needs nested dimension capture; lookup can't grab dimension key/value |
| gen-heavy-epas-upgrade | database/epas | Y | BLOCKED-OWNER | N | IB-006 | owner confirms old engine ver + epas create proven | GATE-0 epas create unproven; old-minor availability owner-unconfirmed |
| gen-heavy-mysql-upgrade | database/mysql | Y | BLOCKED-OWNER | N | IB-006 | owner console-checks live engine-versions | needs a mysql major older than latest; 8.0 prefix is a docs guess |
| gen-heavy-pg-upgrade | database/postgresql | Y | BLOCKED-OWNER | N | IB-006 | owner reads is_kernel_patchable from live 16.x | kernel-upgrade precondition is_kernel_patchable=true unproven |
| gen-wave5-iam-role | management/iam | N | BLOCKED-OWNER | N | (PF-20) | after product fix | POST /v1/roles 500s ContactAdminForAssistance even with bound policy — product 5xx (STOP-3) |
| idc-instance | management/iam-identity-center | Y | BLOCKED-OWNER | N | IB-008? | owner SSO decision | SSO instance is account-structural / irreversible (blast radius, STOP-1/4) |
| idc-user | management/iam-identity-center | Y | BLOCKED-OWNER | N | IB-008? | owner SSO decision | SSO user; needs real instance_id (placeholder ssoins-12345), sensitive |
| idc-group | management/iam-identity-center | Y | BLOCKED-OWNER | N | IB-008? | owner SSO decision | SSO group; needs real instance_id, sensitive on shared account |
| idc-permission-set | management/iam-identity-center | Y | BLOCKED-OWNER | N | IB-008? | owner SSO decision | SSO permission-set; structural access, placeholder ids |
| idc-account-assignment | management/iam-identity-center | Y | BLOCKED-OWNER | N | IB-008? | owner SSO decision | most sensitive SSO write (grants principal access); never create real on shared acct |
| archivestorage-bucket | storage/archivestorage | Y | BLOCKED-OWNER | N | — | owner entitlement | archivestorage permanently owner-excluded (entitlement; no live 2xx); DOCS-ONLY bodies |
| archivestorage-archiving-policy | storage/archivestorage | Y | BLOCKED-OWNER | N | — | owner entitlement | needs existing OBS source bucket; archivestorage owner-excluded |
| parallel-filestorage-capacity-restore | storage/parallel-filestorage | Y | BLOCKED-OWNER | N | — | never (owner-excluded) | PFS writes out of scope (owner 2026-06-12); reads-only via smoke |
| gen-wave-devops | devops-tools/devopsservice | N | STALE | ? | IB-019 | re-check then light window | 400 with 3 unnamed fields — but IB-019 already corrected the devopsservice body in the standalone lifecycle; cross-apply + re-test |
| gen-wave-mgmisc | management/cloudmonitoring | N | STALE | ? | — | re-check then heavy window | cm-event-policy wrong shape; needs EventPolicyInfo research — verify against any newer cm model work |
| gen-wave2-cmep | management/cloudmonitoring | N | STALE | ? | — | heavy window (server exists) | 404 — no monitoring-registered VM on account; retry in a heavy window that creates a server; possibly superseded by gen-wave-mgmisc |

## Notes for Meta-Orch (fix_status I could not fully determine)
- **gen-wave-devops** — `_disabled_reason` (run 27394211896, "3 UNNAMED required fields") predates **IB-019** which corrected the devopsservice create body to `DevOpsServiceCreateRequest{tenant_name, tenant_code, members}` in the **standalone** `devops-tools__devopsservice.json`. The wave1 copy still sends the old `{name, description}` body. Classified **STALE** — Meta-Orch should decide whether to cross-apply the IB-019 body to `gen-wave-devops` (or retire the duplicate) before any window. Marked ready_for_live **?**.
- **gen-wave-mgmisc** vs **gen-wave2-cmep** — both target cm-event-policy on management/cloudmonitoring with overlapping but differently-described reasons (wrong request shape vs 404 no-VM). Likely the same target tested in two waves; one may be the superseded copy. Needs Meta-Orch judgment on which to keep/enable and whether the EventPolicyInfo research is done. Both marked **STALE / ?**.
- **idc-\*** (5 lifecycles) — tagged BLOCKED-OWNER under STOP-1/4 (account-structural SSO, blast radius). They are intentionally coverage-only (every mutating step `optional` + broad `expect_status` so a 403/400 still records coverage). If run-dispatch judges the coverage-only design safe on the shared account, these could instead be treated as live-runnable-as-is (denial = recorded coverage). Left BLOCKED-OWNER pending owner sign-off; no blocking IB row exists (loosely related to IB-008 C-class live-proof backlog).
- The 5 **TIMING-GATED** restore chains are model-green and live-runnable, but only in a window where the cluster/backup has aged past its first scheduled backup (or a pre-aged cluster is supplied). They are NOT blocked — just window-specific. Tracked under IB-006 (owner-approved heavy/destructive staged enablement).
