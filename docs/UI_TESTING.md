# UI testing for coding agents

The Stage 0 operator UI is a server-rendered application. Treat the browser as
an external client of authoritative Python state, not as a second application
runtime.

## Non-negotiable architecture

- Keep simulation behavior in `domain` and `application`.
- Keep UI orchestration in `stage0_sim.api.ui`.
- Render operator state as HTML and SVG templates under `src\stage0_sim\web`.
- Bind controls directly to named links or HTML form actions.
- Do not add client-side stores, duplicated lifecycle state, polling models, or
  simulation rules.
- Prefer native HTML: forms, links, `<details>`, `<dialog>` alternatives,
  downloads, validation attributes, and server redirects.
- Add JavaScript only when the browser capability has no HTML equivalent.
  `enhancements.js` currently exists only for the clipboard API. Keep such code
  small, progressive, and covered by Playwright.

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
- Use the bundled deterministic demo unless a test specifically needs a city or
  tool-controller scenario.
- Never depend on wall-clock timing to prove simulation behavior.
- Keep browser contexts isolated. The test server uses temporary character and
  dataset directories so CRUD tests cannot modify the checkout.

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

When a role locator is ambiguous, improve the accessible naming if two controls
perform different jobs. If two controls genuinely share a role, scope the
locator to their named region rather than falling back immediately to a DOM ID.

## Browser errors

The shared `page` fixture records uncaught page errors and console errors and
fails during teardown if either occurs. Do not suppress that assertion or add a
generic ignore list. Fix the browser error or explicitly assert a narrowly
expected message in a dedicated test.

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
- character assignment validation;
- start, pause, exact single-step, resume, stop, and restart availability;
- speed and controlled vital mutation;
- accessible world SVG and view controls;
- event filtering, event detail, copy/download affordances, and clearing;
- transcript and dataset download visibility;
- character search, create/import, duplicate, rename/edit, download, and
  confirmed delete;
- unique IDs and primary landmarks;
- no uncaught browser or console errors.

Add a focused regression workflow whenever a bug could recur without violating
one of these broad paths.
