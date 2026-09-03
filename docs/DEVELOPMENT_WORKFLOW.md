# Development Workflow

**Owner:** Solo-developer, coding-agent-first analysis, implementation,
validation, and handoff.

Stage 0 changes rapidly and intentionally accepts breaking contracts. Local
agent work should optimize for short feedback loops; broad regression,
browser, package, and platform coverage belongs to CI.

## Classify the change first

| Class | Typical scope | Normal local validation |
| --- | --- | --- |
| Documentation | Markdown, indexes, comments | Documentation test when links or contracts change |
| Localized code | One component or narrow call chain | Closest unit/integration node IDs and Ruff on touched paths |
| Cross-cutting behavior | Domain plus application/API/projection changes | Focused tests for each changed boundary |
| UI behavior | Route, template, enhancement, browser workflow | Focused HTTP tests and one focused Playwright workflow |
| Content contract | Character, element, or scenario schema | Adjacent migration, golden fixtures, focused migration/catalog tests |
| Model protocol | Controller, compiler, prompt, or provider contract | `model_contract` tests and optional live smoke |
| Release/diagnostic | Stabilization or unexplained broad failure | Explicit full local gates |

A task may combine classes, but each class must correspond to an actual changed
surface. Do not add checks only because they appeared in an older plan.

## Analyze a bounded call chain

1. Inspect `git status` and the current diff. Preserve unrelated work.
2. Read the owning current document from [the documentation map](README.md).
3. Trace the authoritative code path and its direct callers/projections.
4. Read the closest existing tests and helpers before adding new structures.
5. Stop discovery once authority, callers, public/persisted projections, and
   focused checks are known.

Search active code and documentation by default. Historical design bodies are
kept in Git history, not in the working tree, so old schemas and route names do
not pollute normal searches.

Escalate to a repository-wide architecture investigation only when a change
crosses an invariant, versioned source contract, persistence boundary, provider
boundary, or several independently owned subsystems.

## Keep plans compact

An ordinary implementation plan contains:

- objective and non-goals;
- decisions not already established by current contracts;
- authoritative files/components expected to change;
- three to seven dependency-ordered tasks;
- selected local checks;
- compatibility/removal notes when relevant.

Target no more than about 150 lines. If more detail is required, split the work
or state why it is one indivisible contract change. Do not copy full schemas,
architecture documents, exhaustive test catalogs, or validation transcripts
into session plans.

Use session todos for execution state. Code, tests, and current documentation
are the durable result; the plan is not a second implementation report.

## Implement current contracts directly

- Build the smallest vertical slice that proves the contract, then add required
  projections, persistence, UI, and examples.
- Remove superseded runtime paths during intentional breaking changes. Do not
  add compatibility branches without a current requirement.
- SQLite is fresh-only unless a separate database-migration design is approved.
- Character, element, and scenario changes are the exception: use adjacent,
  deterministic migrators, immutable legacy fixtures, chained tests, and
  migration of tracked content as described in
  [Content migration](CONTENT_MIGRATION.md).
- Update the owning current document after behavior stabilizes. Other documents
  should link to that owner instead of restating the contract.
- Never weaken deterministic ordering, domain authority, privacy, strict input
  validation, or explicit failure behavior to shorten implementation.

## Run focused local checks

Examples from the repository root:

```powershell
# One unit or integration boundary
python -m pytest tests\unit\domain\test_navigation.py
python -m pytest tests\integration\simulation\test_engagement_runtime.py -k timeout

# Documentation links and canonical contract values
python -m pytest tests\integration\catalogs\test_documentation.py

# Content contract and migration work
python -m pytest tests\unit\application\test_content_migrations.py tests\integration\catalogs

# Model request/compiler/provider contracts without a live model
python -m pytest -m model_contract

# Focused browser behavior
$env:STAGE0_RUN_PLAYWRIGHT = "1"
python -m pytest tests\e2e\web\test_operator_runtime.py -k engagement

# Lint only touched Python paths
python -m ruff check src\stage0_sim\application tests\unit\application
```

Select commands that can falsify the changed behavior. Documentation-only work
does not require unrelated build or runtime checks.

Do not run full local pytest, full Playwright, package build, installed-wheel,
or multi-version/platform matrices by default. Use the full sequence only for
an explicit release or difficult diagnostic task:

```powershell
python -m pytest
python -m ruff check .
python -m mypy
python -m build
```

CI owns broad regression and keeps core Python, browser, static/type, package,
Windows, and compatibility failures independently visible.

## Model-development loop

Normal development uses scripted, fake, recording, and replay clients. A live
model service is not required.

For a new or materially changed controller/compiler protocol:

1. Add deterministic `model_contract` coverage.
2. Run `python -m pytest -m model_contract`.
3. If a configured local or rented endpoint is available, run the bounded live
   smoke described in [LLM operations](LLM_OPERATIONS.md).
4. Record a representative response only when replay evidence is useful; never
   commit credentials or sensitive model content.

The live smoke verifies provider/tool-call conformance. It does not make model
output authoritative and does not replace deterministic domain tests.

## Handoff

Before completing a task:

- confirm the requested behavior and removals are persistent;
- run the focused checks selected for the change classes;
- state plainly when broad validation is delegated to CI;
- remove temporary diagnostics and screenshots;
- leave unrelated working-tree changes untouched.

