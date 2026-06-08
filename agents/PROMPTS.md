# Prompt conventions (PROMPTS.md)

Reusable prompt scaffolding for spawning subagents (`Task` tool) and for keeping
each agent's behavior consistent across sessions. Each agent file carries its own
**system prompt** in its "Role/Objective/Guardrails" sections; the blocks here
are the *shared* preamble and the delegation pattern.

## Shared preamble (prepend to every subagent prompt)

> You are a specialist agent in the SCP API Test Automation project. Before
> acting, read `START_HERE.md`, `agents/CONTEXT.md`, `agents/HARNESS.md`, and your
> own role file `agents/<your-agent>.md`. Consult `knowledge/` before inventing
> any API call order, request body, or dependency — most is already captured.
> Honor the safety gates: `GET` runs; `POST/PUT/PATCH` need
> `SCP_ALLOW_MUTATIONS=true`; `DELETE` needs `SCP_ALLOW_DESTRUCTIVE=true`; heavy
> billable lifecycles need `SCP_RUN_HEAVY=true`. Never weaken these. Persist any
> new fact you learn to `knowledge/` and/or the scenario `_note`, and report
> back concisely (what you did, what you learned, what's next).

## Delegation pattern (orchestrator → subagent)

When the orchestrator delegates, the prompt should contain, in order:

1. **Preamble** (above).
2. **Objective** — one concrete, bounded outcome (e.g. "Add a CRUD scenario for
   networking `nat-gateway` and validate it dry/static; do not run destructive
   steps").
3. **Inputs** — exact files/sections to read first (catalog query, the service's
   `_note`s, related `knowledge/` entries).
4. **Constraints** — safety gates in play, quota kinds touched, cost ceiling
   (light vs heavy), whether live calls are permitted this session.
5. **Definition of done** — the artifact + how it's verified (e.g. "scenario
   added, `python -m spec.summary` unaffected, static validation clean, committed").
6. **Report format** — "Return: changed files, new facts for `knowledge/`,
   coverage delta, open questions."

## Standing objectives (what agents optimize for)

- **AXIS 1:** monotonically increase endpoint coverage toward **100%**, without
  ever turning quota/skip conditions into false failures. Then deepen with more
  parameter combinations.
- **AXIS 2:** surface real design/AI-usability defects; keep the baseline tight
  so only NEW defects alarm; never report a muted known issue as new.
- **Always:** leave the tree working, the results store consistent, and
  `knowledge/` richer than you found it.

## Anti-patterns (do not)

- Don't hardcode hosts, credentials, ids, or call orders in code — they're
  config/data (`core/config.py`, `knowledge/`, `scenarios.json`).
- Don't broaden a safety gate or skip teardown to make something pass.
- Don't re-discover a fact already in `knowledge/validated-facts.md`.
- Don't open a PR or push to a non-assigned branch unless explicitly asked.

## Starting a new session (copy-paste kickoffs)

Paste one of these into a fresh Claude Code session to resume on top of the
accumulated agents + knowledge. The minimal one is enough to bootstrap; the rest
target a specific goal. (한국어로 그대로 붙여넣어도 됩니다.)

**Minimal (universal):**
```
이 repo는 SCP API 테스트 자동화 멀티에이전트 프로젝트야.
먼저 START_HERE.md → agents/CONTEXT.md → agents/README.md 를 읽고,
누적된 agents/ 와 knowledge/ 를 기반으로 이어서 진행해.
도메인 지식은 새로 추측하지 말고 knowledge/ 를 먼저 확인하고,
새로 알게 된 사실은 knowledge/ 와 시나리오 _note 에 반영해서 커밋해.
안전 게이트(GET 외 mutation은 명시적 opt-in)는 절대 완화하지 마.
오늘 뭘 진행하면 좋을지 CONTEXT.md의 'what to advance next' 기준으로 제안부터 해줘.
```

**Advance coverage (AXIS 1):**
```
START_HERE.md 부터 읽고 regression-agent 역할로 진행해.
knowledge/scenario-catalog.md 의 coverage gap 을 보고,
아직 커버 안 된 엔드포인트를 골라 가장 작은 CRUD 시나리오를 추가해줘.
python -m spec.summary 로 커버리지 델타를 확인하고,
새 시나리오는 scenarios.json 에 선언형으로만 추가(파이썬 코드 X),
검증한 사실은 knowledge/validated-facts.md 에 기록해서 커밋해.
```

**Conformance + AI-usability (AXIS 2):**
```
START_HERE.md 읽고 conformance-agent + ai-evaluator-agent 역할로 진행해.
python -m conformance.static / runtime 을 돌려 새 finding 을 정리하고,
"제3자 AI가 이 API를 쓸 수 있나" 관점(ai-evaluator-agent.md 기준)으로
반복되는 마찰점을 conformance/rules/ 규칙으로 일반화해줘.
```

**Curate domain knowledge:**
```
START_HERE.md 읽고 domain-knowledge-agent 역할로,
<서비스명> 서비스의 호출 순서/의존성/검증된 사실을
knowledge/ 와 scenarios.json 양쪽에 사람이 읽을 수 있게 정리해줘.
```

**Working-branch reminder to add to any of the above:**
```
claude/<작업명> 브랜치에서 작업하고 끝나면 push 해. main 직접 push 금지. PR은 내가 요청할 때만.
```
