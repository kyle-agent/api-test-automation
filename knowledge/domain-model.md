# SCP domain model

The shape of the platform the suite tests. Source of truth for counts:
`data/api_catalog.json` (re-derive with `python -m spec.summary`).

## Scale

- **1,372 endpoints**, all resolved (0 unresolved).
- 15 categories per the docs; **13** present in the catalog today.
- Methods: GET 527 · POST 383 · PUT 244 · DELETE 209 · PATCH 9.

## Categories (catalog, with endpoint counts)

| Category | Endpoints | Notable services |
|----------|-----------|------------------|
| compute | 181 | virtualserver (VM, keypair, volume, snapshot, image, server-type) |
| networking | 205 | vpc, subnet, port, security-group, public-ip, internet-gateway, private-dns |
| database | 255 | mysql, mariadb, … (clusters) |
| management | 244 | resourcemanager, iam, organization, quota, … |
| storage | 129 | filestorage (volumes), object/block storage |
| data-analytics | 119 | — |
| application-service | 67 | queueservice |
| container | 63 | ske (K8s clusters/nodepools), scr (registry/repository) |
| security | 54 | certificatemanager |
| ai-ml | 21 | — |
| financial-management | 21 | billing-related |
| platform | 7 | — |
| devops-tools | 6 | — |

(Counts drift as the spec evolves — trust `spec.summary`, not this table.)

## Per-service hosts (critical)

SCP Open API is **per service**, not one gateway. Path roots collide across
services (`/v1/clusters` is used by ske, mariadb, mysql, …), so **every call must
target its own service host**.

- **Regional:** `https://<service>.<region>.<env>.samsungsdscloud.com`
  e.g. `vpc.kr-west1.e.samsungsdscloud.com` + path `/v1/vpcs`.
- **Global (account-scoped, no region segment):**
  `https://<service>.<env>.samsungsdscloud.com` e.g. `product.e...`.

Global services (DNS-verified; override via `SCP_GLOBAL_SERVICES`):
`billingplan, budget, cloudcontrol, costexplorer, iam, organization, pricing,
product, quota, resourcemanager, support`.

Resolution order (`core/config.py::resolve_base_url`):
1. `SCP_SERVICE_HOSTS` override map (JSON `{service: host-or-url}`)
2. global template (if service is global) — no region
3. regional template + `SCP_REGION` + `SCP_ENV`
4. `SCP_BASE_URL` single-host fallback

Set `SCP_REGION` (+ `SCP_ENV`, default `e`). Some services' API subdomain differs
from the catalog name — pin those via `SCP_SERVICE_HOSTS`.

## Authentication (HMAC)

SCP signs Open API calls with **Access Key + HMAC-SHA256**:

- Signing string (per the "Common / API 호출하기" guide):
  `method + encodeURI(url) + timestamp + accessKey + clientType` → HMAC-SHA256 →
  Base64.
- Sent in `Scp-*` headers; `clientType` value is `"Openapi"`.
  Defaults: `Scp-Accesskey`, `Scp-Signature`, `Scp-Timestamp`, `Scp-ClientType`,
  `Accept-Language: en-US`.
- All header names + the signing string are **overridable via env**
  (`SCP_HMAC_*`, `SCP_AUTH_SCHEME=hmac|bearer|none`, `SCP_SIGN_FULL_URL`) and the
  one signing method in `core/auth.py`. On 401/403, adjust there and confirm
  against a real 200.

## Resource lifecycle shape (general)

Most resources follow: **create (async)** → **poll a state field to ready** →
read/list → (update) → **delete (async)** → **poll until 404**. The state field
name and ready values differ per service — see `validated-facts.md`. Create
responses often nest the id (`$.vpc.id`, `$.security_group.id`) or use a flat id
(`$.id`, `$.volume_id`, `$.servers[0].id`) — also per service.
