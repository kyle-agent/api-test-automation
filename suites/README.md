# suites/ — named test suites (run shapes)

One YAML per named run shape (full / smoke / conformance / …). A run is always
**suite × profile** — the suite says *what* to run, the environment profile
(`environments/`) says *against which target* (docs/PLATFORM-PLAN.md §2.3).

```
python -m core.suites list                  # what suites exist
python -m core.suites validate              # offline check (CI: validate.yml)
python -m core.suites render full           # -> .github/run-request content
```

A suite's `request:` block holds exactly the KEY=VALUE options
`.github/run-request` already understands (`mutations`, `destructive`,
`heavy`, `sweep_force`, `conformance`, `category`, `service`, `crud_filter`)
— so the workflow gates and engine are unchanged; the suite is just where the
combination lives, versioned and validated.

How a suite reaches a run:

* **file trigger** — a single `suite=<id>` line in `.github/run-request`; the
  spec job expands it. Explicit KEY=VALUE lines in the file still win, so a
  one-off tweak is `suite=full` + `crud_filter=…` on the next line.
* **workflow_dispatch** — the `suite` input. Suite values OR into the safety
  gates; explicit dispatch inputs still apply on top.
* **render** — `python -m core.suites render full > .github/run-request`
  writes the fully-expanded block (committed = self-documenting run record).

Service-deep runs need no file per service — narrow at render/request time:

```
python -m core.suites render full --set service=filestorage --set crud_filter=filestorage
```
