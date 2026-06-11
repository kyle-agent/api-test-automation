# environments/ — environment profiles (regression targets)

One YAML per test target (검증계/운영계 × region). A run is always
**suite × profile** — the suite says *what* to run (`suites/`), the profile
says *against which environment* (docs/PLATFORM-PLAN.md §2.1).

```
python -m core.profiles list                    # what targets exist
python -m core.profiles validate                # offline check (CI: validate.yml)
python -m core.profiles export stage-kr-west1   # KEY=VALUE lines for $GITHUB_ENV
eval "$(python -m core.profiles export stage-kr-west1 --shell)"   # local shell
```

How a profile reaches a run:

* **workflow_dispatch** — the `profile` input.
* **file trigger** — a `profile=<id>` line in `.github/run-request`.
* **locally** — `eval` the `--shell` export before running pytest.

Each job in `api-test.yml` applies the profile right after dependency install
(`core.profiles export >> $GITHUB_ENV`), overriding the repo-vars defaults for
every later step. With no profile given, behaviour is exactly as before.

Key fields (see `core/profiles.py` for the full schema):

* `env:` — engine variables set verbatim (`SCP_REGION`, `SCP_ENV`,
  `SCP_SERVICE_HOSTS`, …). Only engine-known keys pass validation.
* `credentials:` — **references only**: `TARGET: SOURCE_ENV_VAR_NAME`. The
  exporter resolves the source from the calling environment; profiles never
  contain secret values and are safe to commit.
* `forbid:` — hard per-environment safety gate (`mutations`, `destructive`,
  `heavy`). Exported as `SCP_PROFILE_FORBID`; `core/config.py` refuses the
  matching `SCP_ALLOW_*` flags even when the trigger set them — this is what
  makes a production profile read-only by construction.
* `quota_overrides:` — per-account resource caps, exported as
  `SCP_BUDGET_LIMITS` and merged over `core/budgets.py` defaults.
