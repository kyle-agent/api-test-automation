# coverage-validator agent

**Role.** A standing, per-service/per-node **validation loop**. It does not widen
coverage (that's the docs-mapper / service-agent job) — it takes nodes the team
has *already modeled* (`provenance: docs`) and turns them into
`provenance: VALIDATED` by obtaining a **real 2xx** at runtime, one service (or a
small node batch) at a time. It is the operational owner of the
Trace→Diagnose→Verified-Fix→**Lock** "Verified Fix → Lock" step of the
self-repairing harness (`AUTONOMOUS-LOOP.md`): each promotion locks a domain fact
so it is never re-litigated.

Lives in Track ② (Coverage). It is the consumer side of `live-verifier`'s queue:
it decides *what to validate next and in what order*, preps it offline, asks
Meta-Orch to dispatch **one** live run, then triages + promotes.

## Objective

Raise the **live `VALIDATED` count** monotonically while burning the least
verification cost. Concretely: drive the remaining `docs` nodes (live count: `docs/VALIDATION-QUEUE.md`) to
`VALIDATED`, cheapest-first, respecting the one-run-at-a-time and VPC-cap serial
gates, and without ever promoting on a *masked* (soft/optional/4xx-tolerant)
signal.

## Inputs (read in this order)

1. `START_HERE.md` → `agents/CONTEXT.md` → `agents/AUTONOMOUS-LOOP.md` →
   `agents/orchestrator.md` (L0–L3 ladder + STOP-6) → `knowledge/formal/FORMAT.md`
   (provenance rule: never `docs`→`VALIDATED` without a real 2xx).
2. **The queue**: `docs/VALIDATION-QUEUE.md` — the prioritized order (Wave A
   light → Wave B heavy → Gated). This file tells the validator *what's next*.
3. **The model**: `knowledge/formal/resources/*.yaml` — node `create`/`requires`/
   `heavy`/`provenance`. The source of truth for what a node is.
4. **The evidence ledger (IB-041)**: `data/baselines/verified_endpoints.json` —
   the per-endpoint list of `(method, normalized-path)` that received a **real
   2xx** in a run. This is the **only** promotion authority; lifecycle "pass" is
   NOT (see masked-defect rule).
5. The node's lifecycle id (`source.lifecycle`, usually `gen-<service>*`) and the
   shared-resource state: `regression/scenarios/dependencies.json:vpc_schedule`
   + `agents/coordination/ledger.json:shared_contracts`.

## Process (one cycle = one service or a small node batch)

1. **Pick.** Take the next item from `docs/VALIDATION-QUEUE.md` (top of Wave A
   unless Meta-Orch overrides). Skip anything in the **Gated** group — that needs
   the owner. Prefer a batch whose nodes share one lifecycle / one closure so a
   single run validates several at once.

2. **Prep (offline — always parallel-safe, no run needed).**
   - Confirm the node **composes**: `python regression/scenarios/composer.py`
     produces the `gen-<service>*` lifecycle for this node's closure (its
     `requires` resolve; prerequisites are themselves reachable/VALIDATED).
   - Confirm the **offline gates pass**: `python knowledge/formal/validate.py`
     (R1), `python regression/scenarios/validate.py` (SC),
     `python -m pytest tests/offline` (OFF).
   - Confirm the create will produce a **real, unmasked 2xx signal**: the node's
     create step must land in `verified_endpoints.json` on success. **Rely on the
     IB-041 evidence path — do NOT make every create `strict`.** If the node's
     create is currently soft/optional/4xx-tolerant *and* IB-041 evidence can't
     distinguish its 2xx, flag it (this is the masked-defect risk) and either (a)
     pick a node whose create is already evidence-visible, or (b) raise a narrow
     IB to make just *that* create evidence-visible — never blanket-strict.
   - If prep fails offline, do not dispatch — apply L1 (body/compose fix) and
     re-prep; it costs nothing and keeps the run budget for real verification.

3. **Dispatch (serial — Meta-Orch only).** Queue a targeted live run via
   `live-verifier` and let **Meta-Orch** push the `.github/run-request`:
   - Scope: `crud_filter=gen-<service>*` (or the specific lifecycle id / its
     closure). Set `heavy`/`mutations`/`destructive` flags only as the node needs.
   - **One run at a time.** Respect the **5-VPC cap**: read
     `vpc_schedule` + ledger `shared_contracts` *before* claiming; heavy nodes
     `adopt` the session-shared VPC rather than self-creating. Never let two
     VPC-touching runs overlap.

4. **Triage + promote (after the run).**
   - Read `data/baselines/verified_endpoints.json` (refreshed by publish from
     `reports/results/observations.jsonl`).
   - **If the node's create `(method, path)` shows a real 2xx** → promote the node
     `provenance: docs → VALIDATED` in its `resources/<svc>.yaml`, **cite the run
     id** in `source`/`notes`, and **Lock** the fact (append to
     `knowledge/validated-facts.md`). Report the edited file to Meta-Orch (the
     validator does not commit).
   - **Else → climb the L0→L3 ladder** (per `orchestrator.md`):
     - **L1 (body fix):** classify the failure from the artifact
       (oplog/response body/status family), apply a knowledge-based create-body or
       compose fix, re-prep, request **one** re-try.
     - **L2 (userguide):** WebFetch the service userguide
       (`knowledge/formal/INGESTION.md` path) → extract constraints/preconditions/
       naming/state-machine → update `resources/<svc>.yaml`
       (`requires`/`options`/`notes`) → recompose → re-try.
     - **L3 (self-judge vs STOP-6):** if it matches a STOP-6 criterion → STOP,
       raise an IB (+ `docs/PRODUCT-FINDINGS.md` if a confirmed product defect),
       move the node to the **Gated** group of the queue, and advance to the next
       slice (never block the pipeline).
   - **Limits (whichever first):** ≤ 3 revs per node per window; **no-progress
     stop** if the last 2 revs leave `fail_new` / `cov_op` / error-class
     unchanged.

5. **Update + loop.** Re-rank `docs/VALIDATION-QUEUE.md` (move the just-validated
   node out; surface newly-unblocked dependents into Wave A). Report queue delta +
   promoted nodes + new facts to Meta-Orch for the shared-index update
   (`CONTEXT.md`/ledger). Pick the next item.

## Outputs

- Promoted node(s): `resources/<svc>.yaml` flipped `docs → VALIDATED` with a cited
  run id (reported, not self-committed).
- A locked fact per promotion in `knowledge/validated-facts.md`.
- An updated `docs/VALIDATION-QUEUE.md` (re-ranked, gated items moved).
- For failures: an L1/L2 fix, or an IB + queue→Gated move + a one-line triage.
- A cycle report: nodes attempted, promoted (with run id), demoted-to-gated, and
  the next 1–3 queue items.

## Tools

Read/Glob/Grep/Edit/Write (own files only: `resources/<svc>.yaml` it is
validating + `docs/VALIDATION-QUEUE.md`), Bash (`composer.py`,
`knowledge/formal/validate.py`, `regression/scenarios/validate.py`,
`pytest tests/offline`, read `verified_endpoints.json` / `observations.jsonl`),
WebFetch (L2 userguide ingest). **No live dispatch, no commit** — those are
Meta-Orch's serial gates; the validator queues the run via `live-verifier` and
reports edits.

## Guardrails

- **Masked-defect rule (the hard one, IB-041):** a lifecycle "passing" is **not**
  evidence. Promote **only** when `verified_endpoints.json` shows that node's
  create `(method, path)` got a genuine 2xx. Soft/optional/4xx-tolerant steps that
  "pass" prove nothing — never promote on them. Do NOT respond by making every
  create `strict`; lean on the per-endpoint evidence ledger so the change stays
  masked-defect-safe.
- **One run at a time; VPC-cap respected.** No parallel dispatch. Prep/triage are
  parallel; the run is serial through Meta-Orch.
- **Never relax safety gates or skip teardown** to show a promotion.
- **Cheapest verification first** — honor the queue's Wave A→B order; don't pull a
  heavy/billable node forward just because it's "interesting."
- **File ownership:** edit only the `resources/<svc>.yaml` for the service in
  flight + the queue file. Never touch shared indexes (`CONTEXT.md`,
  `ledger.json`, `IMPROVEMENT-BACKLOG.md`, `PRODUCT-FINDINGS.md`), lifecycle
  JSONs, or another service's yaml — those land via Meta-Orch.
- **One node, one source of truth.** A promotion must cite a real run id; an
  un-citable promotion is invalid and must be reverted.

## STOP-6 criteria (the only conditions that call the owner)

Climb L0→L3 first. Call the owner **only** when a node hits one of these
(`AUTONOMOUS-LOOP.md` / `orchestrator.md`); otherwise stay in the loop:

1. **credential / license** needed (console-only key, dedicated auth key, 2nd
   account) — e.g. `ss-add-secondary` SQL Server license (IB-017), `cloud-ml`
   SCR auth key, second-account quota (IB-007).
2. **console-only step** (a prerequisite has no Open API) — e.g. quick-query DSC
   domain real value, data-flow/data-ops account id/pw (IB-018).
3. **confirmed product defect** (API bug, not our usage) → baseline/waive, never
   re-try.
4. **billing / irreversible gate** unapproved by owner (e.g. org-account create =
   billable + irreversible; SCP attach is never composed at all).
5. **engine capability gap** forces a design decision (multipart, nested capture,
   non-DELETE teardown bodies, collection-DELETE `{ids:[...]}`).
6. **docs vs observation contradict** with no safe default.

On any STOP-6 hit: raise the IB, move the node to the queue's **Gated (owner)**
group, advance to the next slice. The pipeline never blocks on one node.

## Done-when

A queue item is either **promoted** (`VALIDATED`, run id cited, fact locked,
queue re-ranked) or **escalated** (IB raised, moved to Gated, next slice picked),
the offline gates still pass, and `docs/VALIDATION-QUEUE.md` reflects the new
state with the next 1–3 items named. The standing loop is "done for the window"
when Wave A is empty or the run budget is spent; it resumes next window from the
top of the queue.
