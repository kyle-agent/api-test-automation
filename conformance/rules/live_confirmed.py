"""LIVE-CONFIRMED runtime defect classes (AXIS 2 lens) — ported by hand from
``knowledge/validated-facts.md`` (2026-07-15..18 live-probing sessions, owner
task "오너 승인 작업 ②").

These are **not** derivable from the pure spec-scope :class:`conformance.rules.Rule`
protocol (they need a live response body/status the static analyzer never sees)
and were confirmed through ad-hoc runtime probing during this week's coverage
runs rather than the standard ``conformance.runtime --probe all`` sweep (whose
fixed probes — schema/status/notfound/pagination/... — don't happen to shape a
request the way these defects need). Recording them here is how a live
observation becomes a durable, re-emittable :class:`core.results.Finding`
instead of evaporating with the ephemeral ``reports/results/*.jsonl`` shard that
produced it.

Each entry is evidence-bearing (req-id / run-id / status code / repro) in
``detail`` per the AXIS 2 result contract. This module is pure data + a builder
function — **no network I/O**, deterministic, idempotent by
``(endpoint_key, rule_id)`` — so re-running :mod:`conformance.static` always
reproduces the same set (:func:`conformance.static.build` folds :func:`entries`
into ``data/conformance.json`` / the unified findings store the same way it
folds the ``reports/runtime_*.json`` probe outputs).

Rule-id convention followed here (see ``core.results.Finding.rule_id`` doc +
precedent in ``core/http_client.py`` / ``cleanup/reconciler.py``):
  * Reuse an EXISTING rule_id verbatim when the defect is the same *shape* as one
    the engines already emit (e.g. ``versioning.doc-version-not-supported``,
    already implemented in ``core/http_client.py``'s 406 fallback).
  * Otherwise a new ``<domain>.<kebab-or-snake-defect>`` id, dotted-namespace
    style (matches ``versioning.doc-version-not-supported`` /
    ``resourcemanager.stale-index-entry``), distinct from the flat
    ``kebab-case`` ids the built-in probes in :mod:`conformance.runtime` use
    (``5xx-on-bad-input``, ``notfound-200`` etc.) so the two provenances stay
    visually distinguishable in a findings dump.

NOTE — before adding an entry here, check it isn't already reflected: several
candidate classes turned out to already be live in ``data/conformance.json`` via
the standard runtime probe (e.g. DBaaS not-found=400 on ``*showrequest`` is
already ``notfound-inconsistent`` from a committed ``reports/runtime_notfound.json``
run) — re-adding those would duplicate, not surface anything new.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.results import Finding

RED, YELLOW = "red", "yellow"


@dataclass(frozen=True)
class LiveFinding:
    endpoint_key: str
    rule_id: str
    severity: str
    detail: str


# ---------------------------------------------------------------------------
# Class 1 — subnet read-plane stuck on the v1.2 type enum (run c72e, 2026-07-16)
# ---------------------------------------------------------------------------
_SUBNET_ENUM_DRIFT = (
    "PRIVATE subnet enum was added in createsubnet v1.3 "
    "(PUBLIC/PRIVATE/LOCAL/VPC_ENDPOINT) and create/delete accept it (202), but "
    "the READ plane (show/list) still validates against the old v1.2 enum "
    "(GENERAL/LOCAL/VPC_ENDPOINT) -> a PRIVATE-typed subnet is a live, billable "
    "resource with no GET path (create/delete plane version != read plane "
    "version). Live-probed req-65a36c09..., run c72e (2026-07-16); scenario "
    "workaround is a 30s blind settle instead of show-poll."
)

CLASS_SUBNET_ENUM_DRIFT = [
    LiveFinding(
        "networking/vpc/showsubnet", "networking.subnet-read-plane-version-drift",
        RED,
        "GET showsubnet on a PRIVATE-typed subnet -> 404 \"Not found with ID "
        "With Invalid Type\" even though the subnet exists and DELETE on the "
        "same id succeeds (202). " + _SUBNET_ENUM_DRIFT,
    ),
    LiveFinding(
        "networking/vpc/listsubnets", "networking.subnet-read-plane-version-drift",
        RED,
        "GET listsubnets?type=PRIVATE -> 400 \"Input should be 'GENERAL', "
        "'LOCAL' or 'VPC_ENDPOINT'\" (PRIVATE rejected as a filter value) even "
        "though createsubnet documents and accepts PRIVATE. " + _SUBNET_ENUM_DRIFT,
    ),
]


# ---------------------------------------------------------------------------
# Class 2 — 500-on-client-state family: a client-input/client-state problem
# answered with 500 ContactAdminForAssistance instead of 4xx.
# ---------------------------------------------------------------------------
_500_RULE = "runtime.500-on-client-state"

CLASS_500_ON_CLIENT_STATE = [
    LiveFinding(
        "container/ske/createnodepool", _500_RULE, RED,
        "POST /v1/nodepools -> 500 ContactAdminForAssistance (16.7s) when "
        "`zone` is omitted on a single-AZ account (nodepoolcreaterequestv1dot5 "
        "added an optional `zone`; unmatched default-zone placement 500s "
        "server-side instead of 400 asking for zone). Cluster itself reaches "
        "RUNNING fine; failure isolated to nodepool create. req-87752221..., "
        "single-service live rerun 2026-07-16.",
    ),
    LiveFinding(
        "database/postgresql/postgresqlsetparametervalues", _500_RULE, RED,
        "PUT parameters no-op echo (re-submitting the current applied_value "
        "for a template-string parameter, e.g. "
        "\"{1/8 of server total memory}\") -> 500 ContactAdminForAssistance "
        "instead of 200/400. req-ef12a36a..., run a690 campaign A "
        "(2026-07-16). Workaround: scenario only echoes literal-valued "
        "params (e.g. max_connections).",
    ),
    LiveFinding(
        "application-service/apigateway/approveprivatelinkendpoint", _500_RULE,
        RED,
        "PUT approve on a PrivateLink Endpoint whose request/cancel already "
        "AUTO-approved it (state already APPROVED, so REQUESTED/REJECTED are "
        "never reached) -> 500 ContactAdminForAssistance instead of 400 "
        "invalid-state. req-e619b286..., run 6ebd (2026-07-16).",
    ),
    LiveFinding(
        "data-analytics/quick-query/validatequickqueryresources", _500_RULE, RED,
        "POST /v1/quick-query/validate-resources -> 500 ContactAdminForAssistance "
        "when the account has no Quick Query instance (service itself is "
        "reachable — image-versions 200 same run) instead of 400/404. "
        "Reconfirmed run 6ebd (2026-07-16); already masked as a regression-axis "
        "known_issue (data/baselines/known_issues.json) but not previously "
        "reflected as an AXIS-2 design finding.",
    ),
    LiveFinding(
        "database/postgresql/postgresqlregisterlogexportconfig", _500_RULE, RED,
        "POST log-export-config with access_key=\"\" -> 500 "
        "ContactAdminForAssistance instead of 400 (an empty required "
        "credential should fail input validation, not the backend). Same "
        "class reproduced across the mariadb/mysql/epas siblings (see sibling "
        "findings on this rule_id). Heavy n6 DBaaS runs; already a "
        "regression-axis known_issue but not previously an AXIS-2 finding. "
        "Reconfirmed run 6ebd (2026-07-16).",
    ),
    LiveFinding(
        "database/mariadb/mariadbregisterlogexportconfig", _500_RULE, RED,
        "Same access_key=\"\" -> 500 ContactAdminForAssistance class as "
        "database/postgresql/postgresqlregisterlogexportconfig (heavy n6 "
        "DBaaS run, mariadb-cluster-subops-guarded register-log-export-config).",
    ),
    LiveFinding(
        "database/mysql/mysqlregisterlogexportconfig", _500_RULE, RED,
        "Same access_key=\"\" -> 500 ContactAdminForAssistance class as "
        "database/postgresql/postgresqlregisterlogexportconfig (heavy n6 "
        "DBaaS run, mysql-cluster-subops-guarded register-log-export-config).",
    ),
    LiveFinding(
        "database/epas/epasregisterlogexportconfig", _500_RULE, RED,
        "Same access_key=\"\" -> 500 ContactAdminForAssistance class as "
        "database/postgresql/postgresqlregisterlogexportconfig (heavy n6 "
        "DBaaS run, epas-cluster-subops-guarded register-log-export-config).",
    ),
    LiveFinding(
        "container/scr/showregistry", _500_RULE, RED,
        "GET on a registry mid-CREATING (borrowed list[0] by a concurrently "
        "running lifecycle) -> 500 ContactAdminForAssistance instead of "
        "409/425 (a racing client-visible state, not a true not-found). "
        "req-90138294..., live run 2026-07-16. Workaround: 500 retry ladder "
        "15s x 10 until ACTIVE.",
    ),
]


# ---------------------------------------------------------------------------
# Class 3 — createsharingimage irreversible chain (run a690 oplog timeline,
# artifact/events.jsonl, 2026-07-16; corrected 2026-07-16 re: accept/reject API).
# ---------------------------------------------------------------------------
_SHARE_TIMELINE = (
    "Timeline (run a690, artifact/events.jsonl): 18:03:36Z createimage 202 -> "
    "18:03:37Z createsharingimage 202 (req_body {\"account_id\": ...}) -> "
    "18:04:35Z platform spawns a hex-named untagged 104GB temp volume in the "
    "recipient account -> 18:05:53Z deleteimage(original) 204 ACCEPTED while "
    "the share is still in flight -> the share record vanishes from BOTH "
    "accounts' API-visible state (pending-images count 0 either side) but the "
    "temp volume's VolumeForSharingImageDelete flag persists, permanently "
    "rejecting DELETE (\"try again later\") with no API-visible owner left to "
    "reconcile against."
)

CLASS_IMAGE_SHARING = [
    LiveFinding(
        "compute/virtualserver/createsharingimage",
        "compute.image-sharing-202-empty-body", YELLOW,
        "POST /v1/images/{id}/share -> 202 with an EMPTY body {} — no "
        "tracking handle (share/task id) is returned, so a caller can't "
        "correlate the async op with its outcome except by polling the "
        "target account's pending-images or watching temp-volume side "
        "effects. " + _SHARE_TIMELINE,
    ),
    LiveFinding(
        "compute/virtualserver/deleteimage",
        "compute.image-sharing-delete-during-transfer-unguarded", RED,
        "DELETE on the source image of an in-flight createsharingimage "
        "share succeeds (204) with no guard rejecting it — deleting the "
        "source ~2m16s into a still-pending share orphans the derived temp "
        "volume permanently (see "
        "compute.image-sharing-orphan-volume-no-cleanup). " + _SHARE_TIMELINE,
    ),
    LiveFinding(
        "compute/virtualserver/createsharingimage",
        "compute.image-sharing-orphan-volume-no-cleanup", RED,
        "The hex-named 104GB temp volume createsharingimage spawns in the "
        "recipient account has no API-reachable cleanup path once its share "
        "record vanishes (source deleted mid-transfer, or recipient never "
        "acts): DELETE permanently 400s (VolumeForSharingImageDelete, \"try "
        "again later\") with no owning share left in either account's API "
        "state to reconcile against — an unrecoverable billable orphan via "
        "the API plane, confirmed via cross-account API diff 2026-07-16 (old "
        "vs new account both 0 pending-images / 0 private images, volume "
        "still stuck). " + _SHARE_TIMELINE,
    ),
    LiveFinding(
        "compute/virtualserver/createsharingimage",
        "docs.image-share-cancellation-undocumented", YELLOW,
        "createsharingimage's own doc page never mentions that the "
        "accept/reject/cancel counterpart lives on a DIFFERENT endpoint "
        "family — PUT /v1/images/{image_id}/members/{member_id} "
        "(updateimagemember, body {\"status\": pending|accepted|rejected}) — "
        "not a sibling of the share endpoint itself. An AI/agent reading only "
        "the share endpoint's docs has no discoverable path to cancel or "
        "unwind a share. (Corrected 2026-07-16 after an earlier read "
        "mistakenly concluded no accept/reject/cancel API existed at all — "
        "it does, just undocumented as a counterpart of share.)",
    ),
]


# ---------------------------------------------------------------------------
# Class 4 — assorted conformance defects confirmed run c72e / 6ebd / a690+e68b /
# fe88 / 7a26 (2026-07-16).
# ---------------------------------------------------------------------------
CLASS_MISC = [
    LiveFinding(
        "networking/loadbalancer/showloadbalancerpublicnatip",
        "runtime.empty-collection-404", YELLOW,
        "GET .../{loadbalancer_id}/static-nats on a load balancer with zero "
        "NAT IPs attached -> 404 instead of 200 [] (empty collection "
        "represented as not-found rather than an empty successful list). "
        "run c72e (2026-07-16).",
    ),
    LiveFinding(
        "compute/virtualserver/createserverinterfaceprivatenat",
        "status.wrong_code_403", YELLOW,
        "A validation error on private-static-nat create -> 403 instead of "
        "400 (a client-input problem misclassified as an authorization "
        "failure — the two are not interchangeable for a caller that "
        "branches on status family). run c72e (2026-07-16).",
    ),
    LiveFinding(
        "networking/firewall/createfirewallrule",
        "docs.version-semantics-undocumented", YELLOW,
        "Endpoint-pinned version 'firewall 1.1' -> 202 + an EMPTY body {} "
        "(no rule id capturable from the response); the doc page only "
        "describes the 1.0/201-with-full-body semantics. A caller pinned (or "
        "defaulted) to 1.1 who doesn't know to poll listfirewallrules instead "
        "gets no id and downstream rule-id-dependent steps (e.g. an IGW "
        "attach) fail. Live A/B reconfirmation 2026-07-16 (fe88 postmortem); "
        "resolved for us by returning to per-endpoint version pinning.",
    ),
    LiveFinding(
        "container/scr/createregistry",
        "errors.rate-limit-non-json", YELLOW,
        "Under an opening burst (~80 lifecycles dispatched within the first "
        "60s of a run), the SCP edge WAF answers with 417 + an HTML "
        "'Request Rejected' block page (Support ID "
        "3232170405160507975, F5-style) instead of the platform's JSON error "
        "envelope — breaks the 'errors are always JSON' contract an "
        "AI/programmatic consumer relies on to parse failures. run 7a26 "
        "(2026-07-16); root-caused to our own dispatch burst (worker-ramp "
        "fix landed), not a targeted WAF bug — but the non-JSON error shape "
        "itself under any rate-limiting trigger is a real AI-usability gap.",
    ),
]


# ---------------------------------------------------------------------------
# Class 5 — async CREATING/EDITING -> mutate 400, settle requirement
# undocumented (run c72e / 6ebd / a690+e68b, 2026-07-16).
# ---------------------------------------------------------------------------
_SETTLE_RULE = "docs.async-settle-undocumented"
_SETTLE_DETAIL_TMPL = (
    "{op} while the resource is still {state} (async 202 from the preceding "
    "create/set) -> 400 invalid-state; the doc page never states that a "
    "caller must poll to ACTIVE before mutating — an AI/agent following only "
    "the endpoint doc hits an undocumented race. {evidence}"
)

CLASS_SETTLE_UNDOCUMENTED = [
    LiveFinding(
        "networking/vpc/setvpcendpoint", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="PUT (set)", state="CREATING",
            evidence="run c72e (2026-07-16); settle-class extended to "
                     "vpc-endpoint this run."),
    ),
    LiveFinding(
        "networking/vpc/deletevpcendpoint", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="DELETE", state="CREATING",
            evidence="run c72e (2026-07-16); settle-class extended to "
                     "vpc-endpoint this run."),
    ),
    LiveFinding(
        "networking/vpc/setprivatelinkservice", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="PUT (set)", state="CREATING",
            evidence="run c72e (2026-07-16); settle-class extended to "
                     "privatelink-service this run."),
    ),
    LiveFinding(
        "networking/vpc/deleteprivatelinkservice", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="DELETE", state="CREATING",
            evidence="run c72e (2026-07-16); settle-class extended to "
                     "privatelink-service this run."),
    ),
    LiveFinding(
        "networking/vpn/setvpngateway", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="PUT (set)", state="EDITING",
            evidence="run 6ebd/e68b cross-validated (2026-07-16): set-gateway "
                     "202 -> EDITING; a delete/modify 2s later 400s, then "
                     "cascades (gateway 409 has-related -> publicip 400 "
                     "ATTACHED) — EDITING needs the same settle-before-mutate "
                     "gate as CREATING, not just create's transition."),
    ),
    LiveFinding(
        "networking/vpn/setvpntunnel", _SETTLE_RULE, YELLOW,
        _SETTLE_DETAIL_TMPL.format(
            op="PUT (set)", state="EDITING",
            evidence="run 6ebd/e68b cross-validated (2026-07-16): set-tunnel "
                     "202 -> EDITING; delete 2s later 400s (see setvpngateway "
                     "sibling finding for the full cascade)."),
    ),
]


# ---------------------------------------------------------------------------
# Class 6 — 406 NoSuchVersion / docs-vs-served version mismatch reconfirmation.
# Reuses the EXISTING rule_id the engine already emits live
# (core/http_client.py) for the same defect shape — these three instances were
# confirmed in run fe88 (2026-07-16) but the CI artifacts that would have
# auto-emitted them via core.results.record_finding were not retained
# (reports/results/*.jsonl is gitignored/ephemeral), so the observation would
# otherwise be lost.
# ---------------------------------------------------------------------------
CLASS_VERSION_MISMATCH = [
    LiveFinding(
        "compute/scf/showcloudfunctionmetrics",
        "versioning.doc-version-not-supported", YELLOW,
        "docs-derived pin 'scf metrics 1.3' -> 406 NoSuchVersion against the "
        "product pin (1.4); served via the no-pin fallback (latest current). "
        "Confirmed run fe88 (2026-07-16).",
    ),
    LiveFinding(
        "storage/filestorage/listvolumereplicationregion",
        "versioning.doc-version-not-supported", YELLOW,
        "docs-derived pin 'filestorage /v1/replications/regions 1.1' -> 406 "
        "NoSuchVersion against the product pin; served via the no-pin "
        "fallback. Confirmed run fe88 (2026-07-16).",
    ),
    LiveFinding(
        "compute/virtualserver/listserverips",
        "versioning.doc-version-not-supported", YELLOW,
        "docs-derived pin 'virtualserver /v1/servers/{id}/ips 1.3' -> 406 "
        "NoSuchVersion against the product pin; served via the no-pin "
        "fallback. Confirmed run fe88 (2026-07-16).",
    ),
]


ALL: list[LiveFinding] = (
    CLASS_SUBNET_ENUM_DRIFT
    + CLASS_500_ON_CLIENT_STATE
    + CLASS_IMAGE_SHARING
    + CLASS_MISC
    + CLASS_SETTLE_UNDOCUMENTED
    + CLASS_VERSION_MISMATCH
)


# ---------------------------------------------------------------------------
# Class 10 — errors[].detail carries a SERIALIZED PYTHON LIST (repr leak) —
# probe run 39bb (2026-08-20), 빈-바디 400 실측. detail 필드 타입이 전 플랫폼
# 표준으론 배열(131건 실측)인데 billingplan 은 파이썬 리스트의 repr 문자열을
# 그대로 직렬화해 보낸다 — 클라이언트 파싱 불가 계약 위반, 오너 지정
# "즉시 고쳐야 할 대상". budget/iam 의 문자열-detail 은 자연어 문장이라
# 타입 혼재(엔벨로프 계열 systemic)로만 남기고 여기엔 안 넣는다.
# ---------------------------------------------------------------------------
_DETAIL_REPR = [
    LiveFinding(
        "financial-management/billingplan/createplannedcomputes",
        "errors.detail-python-repr", RED,
        "errors[].detail is a stringified Python list — observed "
        "\"['Field required', 'Field required', 'Field required', 'Field "
        "required']\" (empty-body 400, global_request_id req-b6280fd2-7848-…, "
        "2026-08-20). Platform-standard detail is a JSON array (131 endpoints "
        "measured same day); a repr string cannot be parsed field-wise. "
        "Immediate-fix per owner triage."),
    LiveFinding(
        "financial-management/billingplan/showcancellationfee",
        "errors.detail-python-repr", RED,
        "errors[].detail is a stringified Python list — observed "
        "\"['Field required']\" (empty-body 400, global_request_id "
        "req-210f6bdc-3f2a-4932-9585-5e8c8167773e, 2026-08-20). Same repr-leak "
        "shape as createplannedcomputes. Immediate-fix per owner triage."),
]
# ---------------------------------------------------------------------------
# Class 11 — PF-52: lb-health-check create in the subnet-propagation window
# (tracker PRODUCT-FINDINGS PF-52; owner-observed 2026-07-30 + run 5e3f
# req-8571a7db). Two manifestations of the same cross-service propagation gap.
# ---------------------------------------------------------------------------
_PF52 = [
    LiveFinding(
        "networking/loadbalancer/createlbhealthcheck",
        "loadbalancer.accept-then-hang", RED,
        "Health-check create issued while the target subnet is re-transitioning "
        "(a preceding LB create/PUT or subnet-VIP op) is ACCEPTED (202) but then "
        "sits in Creating forever — no rejection, no convergence, no failure "
        "transition. The caller can only give up by timeout, and the zombie "
        "health-check remains (it does not cascade-delete with the LB). "
        "Observed 2026-07-30 (kr-west1). Expected: reject the call (409) when "
        "the precondition is not met, or converge/fail-transition."),
    LiveFinding(
        "networking/loadbalancer/createlbhealthcheck",
        "runtime.500-on-client-state", RED,
        "Same precondition gap, second shape: subnet ACTIVE on the VPC plane "
        "but LB-service partition metadata not yet propagated -> 500 "
        "scp-loadbalancer.common.search-partition-error (req-8571a7db, "
        "2026-07-31). A client-timing condition surfaced as a server error "
        "with no retry hint. Expected: 4xx with a retryable indication."),
]
ALL = ALL + _DETAIL_REPR + _PF52


def entries() -> list[dict]:
    """Plain-dict view of :data:`ALL`, shaped for ``conformance.static.build``'s
    ``add(key, sev, typ, src, detail, issue)`` helper."""
    return [
        {"endpoint_key": f.endpoint_key, "severity": f.severity,
         "rule_id": f.rule_id, "detail": f.detail, "issue": ""}
        for f in ALL
    ]


def findings() -> list[Finding]:
    """:class:`core.results.Finding` view (source="runtime") for callers that
    want to record directly rather than through ``conformance.static.build``."""
    return [
        Finding(endpoint_key=f.endpoint_key, rule_id=f.rule_id,
                severity=f.severity, detail=f.detail, source="runtime")
        for f in ALL
    ]
