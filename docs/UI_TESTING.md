# UI testing for coding agents

The Stage 0 operator UI is a server-rendered application. Treat the browser as
an external client of authoritative Python state, not as a second application
runtime.

## Non-negotiable architecture

- Keep simulation behavior in `domain` and `application`.
- Keep UI orchestration in `stage0_sim.api.ui`.
- Render operator state as HTML and SVG templates under `src/stage0_sim/web`.
- Bind controls directly to named links or HTML form actions.
- Do not add client-side stores, duplicated lifecycle state, telemetry models, or
  simulation rules.
- Prefer native HTML: forms, links, `<details>`, `<dialog>` alternatives,
  downloads, validation attributes, and server redirects.
- Keep `enhancements.js` progressive: it may submit native controls through
  `fetch`, replace named fragments from authoritative server-rendered HTML,
  preserve browser interaction state, use the clipboard API, and provide
  pointer map controls.
- Never calculate simulation outcomes, advance clocks, interpret events, or
  maintain a second telemetry model in JavaScript.
- A JavaScript-disabled browser must retain scenario library editing and
  staging, lifecycle controls, view controls, character operations, and timed
  live refresh.

## Partial-refresh contract

Every enhanced form or link must remain valid native HTML. The enhancement
layer may improve transport but must not become the only implementation.

- Give replaceable regions stable, unique IDs.
- Mark controls with explicit target regions; update the smallest complete set
  needed for the operation.
- Set `aria-busy` and prevent conflicting input while a request is in flight.
- Preserve keyboard focus, window and panel scroll positions, open
  disclosures, current filters, selected event, and map viewport when they
  remain applicable.
- Keep successful and failed operations in the stable live notice region.
- Pause live polling while the user is editing an input or directly
  manipulating the map.
- Use the current URL for filtered live renders so event and focus state do not
  silently reset.
- Treat a missing fragment, non-HTML response, or failed zoom synchronization
  as an explicit browser error; do not silently show stale success-shaped UI.

The map viewport is keyboard focusable and retains ordinary scrollbars. Pointer
drag pans it, wheel input zooms around the pointer, and the named Zoom
in/Zoom out/Fit buttons remain the accessible and no-JavaScript alternatives.
Zoom normally selects city, city-zone, building, or room detail automatically.
The advanced scale override is diagnostic UI, not a second source of spatial
state.

Character inspection and camera following are independent. The operator may
inspect nobody and freely pan the map. Selecting a marker updates the inspector
without locking the camera; following occurs only while the labeled follow
control is enabled. Visible map labels are zoom-tiered and collision-filtered,
while suppressed names remain available through SVG titles and accessible
operator text.

## Install the browser test environment

From the repository root on Linux:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install --with-deps chromium
```

The browser binary is intentionally a separate Playwright installation. A
normal `python -m pytest` skips the browser module so backend contributors are
not forced to download Chromium. UI changes are not complete until the explicit
browser command has passed:

```bash
STAGE0_RUN_PLAYWRIGHT=1 \
  .venv/bin/python -m pytest tests/test_ui_playwright.py
```

CI sets `STAGE0_RUN_PLAYWRIGHT=1`, installs Chromium, and runs the same tests.

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
$env:STAGE0_RUN_PLAYWRIGHT = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_ui_playwright.py
```

## Required autonomous loop

For every behavior or layout change:

1. Read the relevant route, template, and existing Playwright workflow.
2. State the user-visible behavior in terms of accessible controls and visible
   outcomes.
3. Add or update the Playwright test before considering the change complete.
4. Run the smallest affected browser test with `-k`.
5. Use the failure's ARIA snapshot to distinguish an accessibility problem, an
   incorrect test assumption, and an application bug.
6. Fix the root cause and rerun the test.
7. Run the complete Playwright module.
8. Run the normal Python test, lint, type, and wheel checks.

Do not stop after reading generated HTML, grepping a script, or seeing that a
route returns HTTP 200. Those checks cannot prove focus order, accessible
names, form submission, redirects, browser validation, or visible state.

## Locator rules

Use the same interface a keyboard or assistive-technology user receives.
Preferred locator order:

1. `get_by_role(..., name=...)`
2. `get_by_label(...)`
3. `get_by_text(..., exact=True)` for visible domain output
4. semantic element relationships such as a `dt` and its adjacent `dd`
5. CSS only for structural assertions that have no accessible representation

Good:

```python
page.get_by_role("button", name="Pause").click()
expect(page.locator(".notice[role=status]")).to_contain_text(
    "Simulation paused"
)

page.get_by_role("spinbutton", name="Satiety").fill("8")
page.get_by_role("button", name="Apply supplied values").click()
expect(page.get_by_text("homeostasis.mutated", exact=True)).to_be_visible()
```

Avoid:

```python
page.locator("#pause-button").click()
page.wait_for_timeout(1000)
assert "paused" in page.content()
```

IDs are appropriate for document-integrity checks and copy targets, not as the
default interaction contract.

## Waiting and determinism

Playwright assertions auto-wait. Use `expect(...)` instead of sleeps.

- Pause a realtime run before asserting an exact tick.
- Read the current tick, single-step, and assert exactly `before + 1`.
- Assert lifecycle notices after POST/redirect/GET completes.
- For enhanced controls, assert the navigation performance-entry count does not
  increase unless navigation is the intended behavior.
- Exercise live ticks while the map is scrolled and while an input is focused;
  assert scroll, focus, and unfinished input remain intact.
- Cover wheel zoom and drag panning, then trigger another server-rendered update
  to prove the synchronized zoom survives.
- For city changes, cover characters located in buildings, at transport nodes,
  and partway along transport edges.
- Prove the map remains usable with no inspected character and that optional
  follow can be enabled and disabled without changing simulation state.
- At dense city zoom levels, compare visible SVG label bounding boxes and reject
  overlaps rather than relying on source inspection.
- Keep one Chromium workflow with JavaScript disabled to verify the native
  server-rendered fallback.
- Use the bundled deterministic demo unless a test specifically needs a city or
  tool-controller scenario.
- Never depend on wall-clock timing to prove simulation behavior.
- Keep browser contexts isolated. The test server uses temporary character,
  scenario, element, and dataset directories so CRUD tests cannot modify the
  checkout.

Element-library changes must cover create, edit, duplicate, dependency-blocked
delete, and hash-conflict behavior through labeled controls. Reference-scenario
coverage must prove that repeated building instances resolve from one element,
that an override affects only its target instance, and that missing or changed
dependencies block staging. Repeat the core element create/save path with
JavaScript disabled.

## ARIA contract

Every page must retain:

- one visible `main` landmark;
- the named `Application` navigation landmark;
- a unique level-one heading;
- named regions for major simulation panels;
- labels for every editable control;
- accessible names for icon-only controls;
- `role="status"` for successful operations and `role="alert"` for failures;
- unique element IDs;
- keyboard-operable links, buttons, forms, and disclosure widgets.

World maps are SVG images with an accessible name and a `<title>`. Important
state must also appear as text in the inspector or event history; color and
geometry are never the only representation.

Environment UI changes must keep the server-rendered environment summary,
environment event filter, and map resource titles synchronized with the same
runtime snapshot. Wet and closed map styling is supplemental: condition,
wetness band, closure state, and reason must remain available as text. Browser
coverage should step a deterministic weather scenario and verify that a
partial refresh preserves the map viewport and current operator focus.

When a role locator is ambiguous, improve the accessible naming if two controls
perform different jobs. If two controls genuinely share a role, scope the
locator to their named region rather than falling back immediately to a DOM ID.

## Browser errors

The shared `page` fixture records uncaught page errors and console errors and
fails during teardown if either occurs. Do not suppress that assertion or add a
generic ignore list. Fix the browser error or explicitly assert a narrowly
expected message in a dedicated test.

## Research dataset explorer workflows

The dataset explorer at `/ui/datasets/{run_id}/` is a server-rendered view of
persisted research data, not a live telemetry store. Browser coverage must:

- enter through the named **Explore research dataset** link and verify the run
  summary and capture-completeness facts;
- switch among dataset views and submit entity, tick/time, record/category,
  status/outcome, schema, and lineage filters through the ordinary GET form;
- inspect raw and normalized rows through accessible `<details>` disclosures;
- prove `PRIVATE_RESEARCH` rows are absent by default;
- prove choosing `PRIVATE_RESEARCH` without **Include private research data**
  produces the explicit opt-in guidance rather than exposing content;
- opt in with the labeled checkbox, verify the warning and private rows, and
  verify that filtered NDJSON and analysis-bundle links retain
  `include_private=true`;
- download filtered NDJSON and the analysis ZIP and verify their filenames and
  privacy behavior;
- exercise cursor pagination without losing active filters;
- verify partial replacement of `#dataset-query-region` preserves applicable
  focus, open disclosures, and scroll state;
- repeat the core summary, filtering, detail, and download-link workflow with
  JavaScript disabled to prove the native form/link fallback.

Do not copy prompts, model text, memories, or profile/situation content into a
test failure message unless the fixture is deliberately synthetic. Prefer
asserting visibility labels, schema IDs, and known synthetic markers.

## Data Management workflows

The `/ui/data/` workflow is also server-rendered. Browser coverage must enter
through the named **Data** navigation link and exercise catalog filters,
pagination, cross-page selection, select-all/clear, aggregate compatibility and
pooled-versus-macro output, the default private-derived warning and exclusion
control, and JSON/CSV downloads. It must cover both individual and bulk
deletion previews, active-run rejection, required confirmation controls, stale
tokens, atomic success notices, and removal of deleted session references.

Use role/label locators for catalog checkboxes and destructive controls. Verify
enhanced replacement of the stable catalog, selection, aggregate, notice, and
deletion regions without navigation, then repeat the core catalog-selection-
aggregate path with JavaScript disabled. Aggregate assertions must never expose
raw private prompts, memories, profiles, or model content.

## Debugging failures

Run one workflow:

```bash
STAGE0_RUN_PLAYWRIGHT=1 \
  .venv/bin/python -m pytest tests/test_ui_playwright.py -k lifecycle -vv
```

Run headed:

```bash
STAGE0_RUN_PLAYWRIGHT=1 PWDEBUG=1 \
  .venv/bin/python -m pytest tests/test_ui_playwright.py -k lifecycle -s
```

For difficult visual failures, temporarily capture a full-page screenshot:

```python
page.screenshot(path="playwright-failure.png", full_page=True)
```

Store temporary screenshots outside tracked source or remove them before
finishing. Prefer the ARIA snapshot in the Playwright assertion output for
behavioral failures because it is compact, semantic, and reviewable headlessly.

## Minimum workflow coverage

The browser suite must continue to cover:

- load and stage without starting;
- upload and validate scenario JSON;
- search, import, create, duplicate, rename, edit, download, and delete saved
  scenarios;
- structured grid and city editing, native repeated-record operations, retained
  validation failures, save-versus-stage isolation, and saved-scenario staging;
- character assignment validation;
- start, pause, exact single-step, resume, stop, and restart availability;
- speed and controlled vital mutation;
- accessible world SVG and view controls;
- event filtering, event detail, copy/download affordances, and clearing;
- transcript and dataset download visibility;
- research dataset summary/schema views, accessible filters, stable
  pagination, record detail, and filtered exports;
- private research exclusion by default, rejected implicit disclosure, explicit
  opt-in warning, and private-enabled download propagation;
- native no-JavaScript dataset explorer fallback;
- character search, create/import, duplicate, rename/edit, download, and
  confirmed delete;
- unique IDs and primary landmarks;
- no uncaught browser or console errors.

Add a focused regression workflow whenever a bug could recur without violating
one of these broad paths.
