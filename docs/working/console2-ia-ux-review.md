# console2 — IA + UX Review (design backlog)

> Independent IA/UX critique of console2 (review-only; produced by a design-reviewer
> agent from the screen descriptions + `console2/` source). Filed as a backlog.
> Status of each item vs the in-flight builds is tagged: **[in build-1]** = the current
> console2 polish build already addresses it; **[in build-2]** = the reconciler build;
> **[backlog]** = not yet scheduled.

## TL;DR

The **two-screen spine is right** (구성 → 실행&리포트), and **reusing the composition DAG as
the live-progress canvas is the single best idea in this product** — protect it and lean in.
Three structural problems will bite as we scale to 275 resources / 222 lifecycles:

1. **The 4 report tabs are siblings, but they're a drill-down hierarchy** (흐름 ▸ 자원 ▸ API ▸ 로그).
   Flat tabs force the user to re-establish "which lifecycle" four times. **Biggest IA fix.**
2. **The DAG is the hero object but has no scale strategy.** A 222-node longest-path SVG is a
   horizontal mile. Legibility — not features — breaks first.
3. **Selection + "what did I just select" are split across tree + modal + a readout string**,
   with no way to see/edit the *resolved closure* as an inspectable, trimmable set.

## 1. IA

- **Keep the two screens; fuse select+plan (already done) — correct.** The plan (DAG + order
  table) is *feedback on the selection*, not a separate wizard step.
- **Reframe the 4 report tabs as a master→detail drill-down:** 흐름 (live DAG) is the persistent
  master; clicking a node opens **자원 · API · 로그 scoped to that lifecycle** (+ a "전체" aggregate).
  Cheap first step: **click a DAG node → cross-filter the other tabs to that lifecycle.** [backlog]
- **Compose-DAG vs Flow-DAG (same graph) — keep, but make the MODE explicit:** in 실행, node color
  should switch to **run-state as primary** and demote planning semantics (provenance/dedup) to a
  toggle — otherwise amber means three things at once. [partly in build-1: wave-live]
- **Homes:** Axis+mode belong in the launch bar (✓), make 실행's copy read-only "this run used: …".
  **Suites** need a home: a `Suite ▾` picker at top of 구성 (load/save named scope) [backlog].
  **Queue** is currently aspirational (only `▶ 지금 실행` exists) — if built, it's a *state* in a
  Runs list, not a separate panel.
- **Per-run cleanup vs account hygiene — split them** [in build-1 + build-2]: per-run teardown
  status lives in 자원 (owner-tag scoped); account-wide leftover sweep is a separate "잔존 자원/정리"
  utility, labeled "owner-tag only" — not shown as if it's this run's mess.
- **Promote Runs to a first-class build-list rail** (queued/running/done · axis · pass/soft/fail)
  so the iterate/compare loop works; preserve scope across 실행→구성 round-trips. [backlog]

## 2. UX

- **Selection:** good density + tri-state + 의존전용 dimming + per-service modal. Fix: the resolved
  **closure is invisible as a set** → add a "닫힘 보기" drawer (flat, grouped, trimmable, with
  "pulled by …" provenance); show **"3/8 리소스"** on partial rows; extend **search to resource names**;
  guard `전체 선택` at live/heavy scale. [backlog]
- **Color channels are overloaded** — amber = shared-dedup / docs-provenance / soft-fail; green =
  dep-done / validated. **Budget channels by visual property**: role=fill, provenance=border-style,
  run-state=fill (in 실행), result=badge-shape; one legend per mode; a glyph for every state
  (never hue alone). [in build-1 partial: type/labels]
- **DAG legibility at scale = make-or-break:** longest-path "draw everything" fails at 222 nodes.
  **Collapse-to-service by default + focus-on-click (fade non-path) + minimap/zoom-to-fit**, and
  **promote the order table & wave list to equal toggle views** (그림 | 표). Keep transitive
  reduction on. *If only one DAG change: collapse-to-service + focus-on-click.* [backlog]
- **Live feedback:** the **log flicker** is a re-render bug → append-only log + patch DAG node
  attrs in place (don't re-emit SVG) [in build-1: Q1]. **웨이브 진행** should be the *primary*
  live affordance (more legible than 222 nodes) and must populate on live runs [in build-1].
  Add a **global progress ring + run badge in the context bar**, always visible. [backlog]
- **Empty/loading/error:** ctxbar stuck at "로딩 중…" + blocking `alert()` → inline dismissible
  error banner + model-load-failed retry + skeletons; first-run "예시 스코프 불러오기" to teach by
  demo; make the live-run confirm a real pre-flight (enumerate gates + estimated heavy/billable
  node count). [backlog]
- **API-detail / coverage (the north-star feature)** [in build-1: B6]: expand a row to
  **Schema (declared) | Request (sent) | Response (got)** and **diff declared-vs-sent params** =
  the coverage gap, made visual. Roll **coverage % up to nodes → DAG coverage heatmap**. Inline
  accordion (keep adjacency), not a modal; make rows linkable / "copy as finding".

## 3. Prioritized backlog

Effort: S ≈ <½d · M ≈ 1–2d · L ≈ multi-day/IA shift.

### Quick wins
| # | Prio | Problem → Change | Effort | Status |
|---|------|------------------|--------|--------|
| Q1 | P0 | Log/DAG flicker under 0.7s polling → append-only log + patch nodes in place | S–M | **in build-1** |
| Q2 | P0 | Per-run report shows account-wide leftovers → split per-run teardown (자원) vs account sweep utility | M | **in build-1/2** |
| Q3 | P0 | Amber=3 meanings, green=2 → channel-budget colors; one legend/mode; glyph per state | M | backlog |
| Q4 | P1 | Node click doesn't connect to tabs → click node cross-filters 자원/API/로그 to that lifecycle | S–M | backlog |
| Q5 | P1 | Scope rebuilt after each run → preserve selection across 실행→구성 | S | backlog |
| Q6 | P1 | No global run status on detail tabs → progress ring + run badge in context bar | S | backlog |
| Q7 | P1 | Blocking alert(); model-load stuck "로딩 중…" → inline error banner + retry + skeletons | S–M | backlog |
| Q8 | P2 | Partial services force modal recall → show "3/8 리소스" on the row | S | backlog |
| Q9 | P2 | Search misses resources → search resource names + parent path | S | backlog |
| Q10 | P2 | Axis re-offered in 실행 implies mid-run change → make it read-only "this run used: …" | S | backlog |

### Bigger IA shifts
| # | Prio | Problem → Change | Effort | Status |
|---|------|------------------|--------|--------|
| B1 | P0 | 4 flat tabs force manual re-correlation → report = master(흐름) + detail(자원·API·로그 scoped) | L | backlog |
| B2 | P0 | DAG unusable at 222 nodes → collapse-to-service + focus-on-click + minimap/zoom; order/wave as equal views | L | backlog |
| B3 | P1 | Closure invisible as a set → "닫힘 보기" trimmable drawer with provenance; = "save as suite" | M–L | backlog |
| B4 | P1 | Suites homeless in UI → Suite ▾ picker (load/save named scope) | M | backlog |
| B5 | P1 | Run history subordinate; no compare; queue undefined → Runs build-list rail (queue = a state) | M–L | backlog |
| B6 | P0 | API-detail/coverage is the north star → Schema vs Request vs Response + param diff; coverage heatmap on DAG | L | **in build-1 (started)** |
| B7 | P2 | Plan↔run cut loses order/quota readout → carry compact order/peak-VPC/dedup strip into 실행 | S–M | backlog |

**Suggested sequence:** Q1–Q3 → Q4–Q7 → B1 then B2 → B6 → B3/B4/B5.

## 4. North-star (the core loop: pick → run → see which API fires in DAG order → analyze coverage)

- **One object, three lives.** The composition DAG is the *same* artifact across plan / live-run /
  coverage — colored by *intent* while picking, by *progress* while running, by *coverage* while
  analyzing. Make this the explicit organizing principle.
- **The selection is honest and traceable.** Picking a service shows the full resolved closure as an
  inspectable, trimmable list with "why is this here" provenance — no surprise billable nodes.
- **You can always see, in DAG/wave order, which API is firing right now and what it sent** — method ·
  path · status · timing live; one click deeper = request body vs declared schema.
- **Drilling is zooming, not tab-hopping.** run → node → resource(s) → API calls → raw log keeps
  "which lifecycle" pinned throughout.
- **Coverage is the payoff, on the hero object.** On finish the DAG becomes a **coverage heatmap**:
  nodes whose endpoints' declared params were under-exercised light up, each expandable to the exact
  schema-vs-sent diff — turning "did it pass?" into "what's *not yet tested?*".
