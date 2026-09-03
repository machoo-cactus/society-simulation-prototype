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

## Physical room SVG contract

The server route and Jinja template, not browser JavaScript, derive the room
SVG from operator snapshots. Room-scale coordinates are microcells with a
legacy-cell pattern as the overview guide. The one-microcell pattern is emitted
only at close zoom. Grid patterns and merged rectangles must keep markup
proportional to zones, blocked regions, objects, characters, paths, and
selected anchors—not to room width × height in microcells.

The SVG and inspector must retain:

- exact object footprint/occupied-cell shapes and 5×5 character bodies;
- cardinal pose, posture/support, held state, and current interaction state;
- open/closed and locked/unlocked door state plus entrance/portal links;
- movement paths, destinations when enabled, and selected approach/occupancy
  anchors;
- accessible SVG title/description and object/character labels;
- text, ARIA, class, shape, or glyph state so color is never the only signal;
- ordinary object selectors, inspect links, zoom controls, and inspector
  content with JavaScript disabled.

Operator access is intentionally authoritative and may include hidden state
under operator conventions. Browser rendering must not reinterpret that state
as character perception.

## Engagement operator contract

The selected-character inspector renders current and recent engagement state
from Python telemetry projection. It must distinguish pending compilation,
compiled work, active execution, group progress, required-atomic status,
grounded evidence, partial completion, failure, and cancellation. The event
filter exposes the `engagement.*` family. Raw intent/reason, compiler
scene/summary/response, rejected proposal details, and private normalized
arguments must not appear in ordinary operator HTML.

Auditory evidence shows domain-resolved recipients/effects, not compiler
claims. Visual/heard state requires text and semantic markup in addition to
color. Staging a scenario with engagement compiler settings remains separate
from starting or stepping it.

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
6. Fix the root cause and rerun the focused browser and closest HTTP tests.
7. Delegate complete browser and repository regression to CI unless this is an
   explicit release or diagnostic run.

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
  mutation, physical footprints/body/posture/held/doors/paths/anchors, bounded
  SVG markup, event filtering/detail/copy/clear, transcript, engagement
  pending/active/recent inspection, partial/cancelled outcomes, and downloads;
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

Changes to engagement projection, event filtering, selected-character
inspection, or scenario forms require focused Playwright coverage with
`STAGE0_RUN_PLAYWRIGHT=1`. Complete browser, no-JavaScript, accessibility, and
partial-refresh regression runs in CI; Python unit or HTTP tests alone do not
prove browser behavior.

## Focused debugging

```powershell
$env:STAGE0_RUN_PLAYWRIGHT = "1"
.\.venv\Scripts\python.exe -m pytest `
  tests\e2e\web\test_operator_runtime.py -k lifecycle -vv
```

Temporary screenshots must be removed before finishing. Prefer ARIA snapshots
for headless failures.
