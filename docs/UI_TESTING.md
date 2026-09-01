# UI Architecture and Playwright Testing

**Owner:** Server-rendered browser architecture, progressive enhancement,
accessibility, and required end-to-end validation.

## Non-negotiable architecture

- Python application services own simulation and lifecycle behavior.
- UI orchestration stays in `stage0_sim.api.ui` and focused route modules.
- Jinja templates under `src\stage0_sim\web\templates\` render authoritative
  HTML/SVG.
- Every control is an ordinary labelled form or link bound to a Python route.
- `src\stage0_sim\web\static\enhancements.js` may improve transport, preserve
  browser state, use clipboard APIs, and provide pointer map interaction.
- JavaScript must not own lifecycle, telemetry, events, clocks, world rules, or
  simulation outcomes.
- Scenario editing, staging, lifecycle controls, view controls, catalog CRUD,
  and timed refresh must remain usable with JavaScript disabled.

## Partial-refresh contract

Enhancement may submit a native control through `fetch` and replace the smallest
complete set of named server-rendered regions. Replaceable regions need stable
unique IDs.

During refresh:

- mark targets busy and prevent conflicting input;
- preserve focus, selection, window/panel scroll, open disclosures, filters,
  selected event, and map viewport when still applicable;
- keep success/failure in stable status/alert regions;
- pause polling while an input is being edited or the map is manipulated;
- use the current filtered URL so live refresh does not reset state;
- surface missing fragments, non-HTML responses, and synchronization failures
  as explicit browser errors.

Accessible zoom/focus/follow controls remain the fallback for pointer drag and
wheel zoom. Inspection and camera following are independent presentation state.

## Accessibility contract

Every page keeps:

- one visible `main` landmark and named `Application` navigation;
- one unique level-one heading;
- named major regions and labelled controls;
- accessible names for icon-only controls;
- `role="status"` for success and `role="alert"` for failure;
- unique IDs and keyboard-operable controls;
- an accessible SVG name/title plus textual equivalents for important map state.

Color or geometry must never be the only representation. Improve ambiguous
accessible names instead of bypassing them with DOM IDs.

## Install and run

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
$env:STAGE0_RUN_PLAYWRIGHT = "1"
.\.venv\Scripts\python.exe -m pytest tests\e2e\web
```

Linux:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install --with-deps chromium
STAGE0_RUN_PLAYWRIGHT=1 .venv/bin/python -m pytest tests/e2e/web
```

Normal `python -m pytest` skips browser workflows unless
`STAGE0_RUN_PLAYWRIGHT=1` is set.

## Required change loop

For every behavior or layout change:

1. Read the route, template, enhancement, and existing browser workflow.
2. Define the visible behavior through accessible controls and outcomes.
3. Add or update a Playwright test.
4. Run the smallest affected test with `-k`.
5. Use the ARIA snapshot to distinguish test error, accessibility defect, and
   application bug.
6. Fix the root cause, rerun the focused test, then run `tests\e2e\web`.
7. Run the standard pytest, Ruff, mypy, and package-build gates.

Source inspection, JavaScript syntax checks, HTTP 200 responses, and generated
HTML do not prove browser behavior.

## Locator and waiting rules

Preferred locators:

1. `get_by_role(..., name=...)`
2. `get_by_label(...)`
3. exact visible text
4. semantic element relationships
5. CSS only for structure without an accessible representation

Use Playwright `expect(...)` auto-waiting, not sleeps. Pause realtime runs before
asserting exact ticks; read the tick, single-step, and assert `before + 1`.
Assert notices after POST/redirect/GET. Browser contexts and character,
scenario, element, and data directories must be isolated.

The shared fixture fails on uncaught page and console errors. Do not add broad
ignore lists.

## Required workflow coverage

The suite must retain:

- stage/load without starting, upload, assignment validation, and explicit
  start/pause/resume/step/stop;
- scenario and element create/import/edit/duplicate/rename/download/delete,
  stale hashes, dependencies, structured grid/city editing, and save-versus-
  stage isolation;
- character CRUD, import/download, validation, and conflicts;
- world inspection, semantic zoom, pan/follow/focus, accessible SVG, vital
  mutation, event filtering/detail/copy/clear, transcript, and downloads;
- dataset summary/schema, filters, pagination, details, filtered exports,
  private-data rejection/opt-in, and no-JavaScript fallback;
- dataset-catalog selection, pooled versus macro aggregation, privacy warnings,
  exports, guarded atomic deletion, stale tokens, and active-run rejection;
- focus, scroll, disclosure, filter, input, and map-position preservation across
  partial refresh;
- at least one Chromium workflow with JavaScript disabled;
- unique IDs, landmarks, labels, and no browser/console errors.

Research tests must use deliberately synthetic private content and must not copy
real prompts, memories, profiles, or model text into failure output.

## Focused debugging

```powershell
$env:STAGE0_RUN_PLAYWRIGHT = "1"
.\.venv\Scripts\python.exe -m pytest `
  tests\e2e\web\test_operator_runtime.py -k lifecycle -vv
```

Temporary screenshots must be removed before finishing. Prefer ARIA snapshots
for headless failures.
