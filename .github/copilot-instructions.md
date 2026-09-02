# Copilot instructions for Stage 0

## Commands

Use Python 3.12 or newer and install the editable package:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the standard gates from the repository root:

```powershell
python -m pytest
python -m ruff check .
python -m mypy
python -m build
```

Run a focused test with a pytest node ID, for example:

```powershell
python -m pytest tests\unit\domain\test_navigation.py
```

Run the application or deterministic CLI demo:

```powershell
python -m uvicorn stage0_sim.api.app:app --reload
stage0-sim run demo --ticks 10
```

Use `.\update.ps1` on Windows or `bash ./update.sh` on Linux to refresh an
existing environment. Source updates require explicit `-Pull` or `--pull`.

UI changes require Playwright:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:STAGE0_RUN_PLAYWRIGHT = "1"
.\.venv\Scripts\python.exe -m pytest tests\e2e\web
```

## Current contracts

- Release: `0.2.0`
- Scenario source: version 6
- Character source: version 2, template `human-v1`
- Reusable element source: version 3
- Dataset: `stage0.dataset.v4`
- SQLite: schema 10, fresh-only
- Telemetry: `stage0.telemetry.v3`
- Cognition: typed character-controller tools behind one global barrier
- Navigation: `navigate_to` and `NAVIGATE`
- Action lifecycle: `action.queued`, `action.started`,
  `action.completed`, `action.failed`, `action.cancelled`
- Canonical persisted-data routes: `/simulation/data/*`
- Canonical per-run data/export routes:
  `/simulation/runs/{run_id}/data/*` and `/exports/*`

Use **character** for a simulated person and **character controller** for the
software selecting decisions.

## Non-negotiable invariants

- Dependency direction is `domain <- application <- adapters/API/UI`.
- Domain systems alone decide physical outcomes; providers return proposals.
- Physical ECS components plus `SpatialIndex` are live truth. `CityWorld` and
  element hierarchy are immutable construction/display metadata.
- Semantic mass/dimensions are distinct from rendered collision footprints.
  Equipment uses live slotted `ATTACHED_TO` relations and deterministic typed
  effects; per-sense structural blockers and footprint sweeps remain domain
  authority.
- Local physical execution is fixed at 9 microcells per legacy cell with a
  5×5-microcell character body; physical anchors are microcells and coarse
  compatibility positions may be legacy cells.
- Never call a model or embedding provider from an ordered domain system.
- System 1 is deterministic, non-bypassable, and clears incompatible work.
- System order, stable entity iteration, tie-breaking, and commit order are
  simulation semantics.
- Tool calls become immutable intents and are revalidated before deterministic
  commit. A commit is not proof of success.
- Physical interaction verbs and
  `interaction.requested|started|completed|failed|cancelled` are closed
  contracts. Door links use live open/locked state and domain traversal.
- Custody/hands/live relations, descriptive ownership, and abstract
  possessions are independent representations.
- Telemetry and research traces are never character perception or controller
  context.
- Perception exposes observer-specific execution evidence, not private plans,
  destinations, reasons, vitals, drives, prompts, profiles, or memories.
- Strict scenario/action/tool schemas reject unknown values. Character dossier
  extensions use preserved JSON fields or ordered `custom_sections`; they
  remain descriptive only.
- Provider-specific HTTP/SDK objects stay inside `adapters\llm`.
- Real providers are opt-in; scripted and replay clients remain first-class.
- Failures are explicit; do not create success-shaped fallbacks.
- Browser forms/links and Python state remain authoritative. JavaScript is only
  progressive transport and browser interaction, with a no-JavaScript fallback.
- Operator SVGs project footprints, bodies, posture/held/door/path/anchor
  state; keep markup content-bounded and expose state without relying on color.
- Scenario staging must not start or advance a run.
- Datasets are research records, not checkpoints. Private exports require
  explicit handling.
- Use `pathlib` and preserve Windows and Linux behavior.

## Authoritative documentation

- [Documentation map](../docs/README.md)
- [Architecture](../docs/ARCHITECTURE.md)
- [Runtime semantics](../docs/RUNTIME.md)
- [Configuration](../docs/CONFIGURATION.md)
- [Scenario and element authoring](../docs/SCENARIO_EDITOR_GUIDE.md)
- [Content migration](../docs/CONTENT_MIGRATION.md)
- [Character authoring](../docs/CHARACTER_PROFILE_GUIDE.md)
- [Actions and events](../docs/ACTIONS_AND_EVENTS.md)
- [Research data](../docs/DATA_COLLECTION.md)
- [API and UI workflows](../docs/API_AND_UI.md)
- [UI architecture and testing](../docs/UI_TESTING.md)
