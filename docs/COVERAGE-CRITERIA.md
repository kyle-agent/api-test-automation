# Coverage criteria — what does "100%" mean?

> Status: **ADOPTED** (승인 2026-06-09). 대시보드가 이 기준대로 구현되어 있음:
> C1/C2/C3 카드 + waiver 메커니즘(`data/baselines/coverage_waivers.json`) +
> C1·C3 동시 추세. waiver 추가는 사람이 승인한다.

## The problem

The dashboard currently shows five numbers whose names don't immediately
convey meaning (도달가능 ceiling / 측정·검증 / 측정·도달 / 읽기 검증 / 쓰기
검증). The underlying **semantics are already right** — what's missing is a
single, memorable ladder and an explicit definition of the 100% goal.

## What the industry does (BP survey)

- **[swagger-coverage](https://github.com/viclovsky/swagger-coverage)** (and
  [variants](https://github.com/Nikita-Filonov/swagger-coverage-tool)) count an
  operation as covered **if it was called at all** — a 4xx call still counts.
  Coverage dimensions: operation, status-code, parameter, branches/conditions.
- **[Restats / TCL](https://arxiv.org/pdf/2108.08209)** (Test Coverage Level)
  defines an **incremental ladder**: TCL1 = 100% path, TCL2 = 100% operation,
  TCL4 = +parameter & status-class, TCL5 = +status codes, TCL6 = +body
  properties, TCL7 = +operation flows. Each level includes the ones below.
- Takeaway: BP tools measure **"was it exercised"**, not **"did it work"**.
  Our regression axis needs both — so we keep the BP "called" dimension *and*
  add a stricter **verified** rung on top. Our criterion is deliberately
  stricter than swagger-coverage.

## Proposed ladder (C0–C4)

Each rung includes the ones below. **One endpoint = one row of the catalog**
(1,372 total — the denominator never shrinks).

| Rung | Name | Criterion | Today's name |
|------|------|-----------|--------------|
| C0 | **정의 Defined** | in the catalog | total |
| C1 | **도달가능 Plannable** | an ENABLED committed scenario can hit it (static, no live call) | 도달가능 ceiling |
| C2 | **호출됨 Called** | a real run invoked it and got *any* HTTP response — incl. 4xx. **"이 API는 호출은 된다"** (routing/auth/path proven) | 측정·도달 |
| C3 | **검증됨 Verified** | semantic success on a real run: **GET → 2xx(200)** · **POST/PUT/PATCH/DELETE → 2xx** (the operation actually did its job) | 측정·검증 = covered |
| C4 | **심화 Deepened** | beyond one 2xx: status-code coverage, parameter combinations, response-schema validation (≈ TCL4–6) | (미구현 — 측정 축의 ✗ schema/◑ status) |

Read/write split stays, but as a **breakdown of C3**, not separate concepts:
C3-read (GET verified) and C3-write (write verified).

## The 100% goal

> **100% = C3 (검증됨) for every catalog endpoint that is not waived,**
> **and C2 (호출됨) for every waived endpoint.**

- **C3 is the headline number** (the dashboard's current `cov_op` already
  computes exactly this). C1/C2 are progress indicators, not the goal:
  C1 says "the scenario exists", C2 says "the call happens", only C3 says
  "the API works".
- A write that only ever gets 4xx is C2, never C3 — a 404'd DELETE deleted
  nothing. (이미 대시보드가 이렇게 계산하고 있음 — 이 정의를 공식화하는 것.)
- 5xx / hard auth failures are **failures**, not coverage — they show up in
  the regression alarm path (new vs known), never in any C-rung.

### Waivers (the honest way to reach 100%)

Some endpoints can **never** 2xx on a shared test account — by design, not by
test gap. Pretending otherwise blocks 100% forever; silently shrinking the
denominator hides risk. BP answer: an explicit, reviewed **waiver list**.

- `data/baselines/coverage_waivers.json` — one entry per waived endpoint:
  `{key, reason, class, provenance, added}`.
- Waiver classes (initial): `blast-radius` (e.g. management/organization
  writes — irreversible account-wide damage), `entitlement` (needs a
  contract/license the test account lacks), `unsatisfiable-flow` (e.g.
  certificatemanager import), `billing-prohibitive` (explicitly excluded by
  cost policy, if any).
- Rules: a waived endpoint **must still be C2** (called; the 4xx itself is the
  evidence the gate works) · waivers carry provenance like all domain
  knowledge · **humans approve waiver additions** (same review discipline as
  combo-scenarios) · the dashboard shows `검증 100% = C3 + waived(C2)` with the
  waived count visible, never hidden.

### Formula

```
goal      = C3_count + waived_C2_count == catalog_total
headline  = C3 / (total - waived)              # 검증 커버리지
secondary = C2 / total                          # 호출 커버리지
planning  = C1 / total                          # 시나리오 도달가능 (저작 진척)
```

## Implementation (done)

1. ✅ Coverage panel relabeled to the ladder: `C3 검증됨` headline (+ 건강도
   카드), `C2 호출됨`, `C1 도달가능`, C3-read / C3-write breakdown, legend
   "C2=호출은 된다(4xx 포함) · C3=실제로 동작했다(2xx)".
2. ✅ Waiver mechanism: `data/baselines/coverage_waivers.json` (initially
   empty; 사람이 승인해 추가) — dashboard surfaces waived/called counts and
   flags waived endpoints that DO 2xx as removal candidates;
   `knowledge/formal/validate.py` checks keys exist in the catalog, class
   enum, reason present.
3. ✅ Trend chart overlays C1 (authoring progress) and C3 (goal progress) on
   one scale; history rows record `cov_c3` + `waived`.
4. ✅ C4 axes stay visible as the post-100% roadmap (ROADMAP Phase 2 "widen
   parameter combinations").

## Sources

- swagger-coverage: <https://github.com/viclovsky/swagger-coverage>
- swagger-coverage-tool: <https://github.com/Nikita-Filonov/swagger-coverage-tool>
- Restats — TCL ladder: <https://arxiv.org/pdf/2108.08209>
- Black-box REST test-gen comparison: <https://arxiv.org/pdf/2108.08196>
