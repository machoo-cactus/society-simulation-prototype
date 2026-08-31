# Copilot instructions for Stage 0 Simulation

## Build, run, test, and lint

Use Python 3.12 or newer. Install the package in editable mode so the `src/`
layout, console scripts, and packaged web assets are exercised:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Existing Linux checkouts can use `bash ./update.sh` to fast-forward pull and
refresh the editable development environment. Windows checkouts can use
`.\update.ps1`.

Run the standard checks from the repository root:

```bash
python -m pytest
python -m ruff check .
python -m mypy
python -m pip wheel . --no-deps --wheel-dir dist
```

Run a single test, test class, or file with pytest node IDs:

```bash
python -m pytest tests/test_navigation.py
python -m pytest tests/test_navigation.py::test_name
```

Run the API and bundled browser UI:

```bash
python -m uvicorn stage0_sim.api.app:app --reload
```

Run a deterministic CLI scenario:

```bash
stage0-sim run scenarios/minimal.json --ticks 10
```

Tool-agent scenarios require an explicit model provider. For local testing,
start the separate OpenAI-compatible fake API and point the simulation at it:

```bash
stage0-fake-llm --host 127.0.0.1 --port 8081
export STAGE0_LLM_PROVIDER=openai-compatible
export STAGE0_LLM_BASE_URL=http://127.0.0.1:8081/v1
export STAGE0_LLM_MODEL=stage0-fake
stage0-sim run scenarios/real-llm-tool-agent.json --ticks 30
```

The browser UI is Python-rendered HTML/SVG under `src/stage0_sim/web/`; there is
no Node build step and no client-side application state. Keep authored
JavaScript limited to browser APIs that HTML cannot provide, and cover every
such enhancement with Playwright. Follow `docs/UI_TESTING.md` for required
role-driven browser testing.

## Architecture

Stage 0 is a deterministic, fully materialized simulation. The major dependency
direction is:

```text
domain <- application orchestration <- adapters/API/UI
```

- `domain` owns authoritative ECS state, grid/pathfinding, physiology,
  sparse city transport, System 1 survival arbitration, plans, affordances,
  speech, and ordered deterministic systems.
- `application` owns scenario construction, the runner and post-tick boundary,
  perception projection, controller scheduling, tool validation/commit,
  memory, telemetry projection, and dataset collection.
- `adapters` contain provider and persistence details. Provider-specific HTTP
  or SDK objects must not escape the adapter layer.
- `api` composes the application and exposes scenario, run-control, telemetry,
  event-history, and export endpoints.
- `web` is an operator-facing omniscient console. It must not become the source
  of simulation behavior.

The runner advances a fixed simulation clock, executes systems in explicit
numeric order, emits `simulation.tick`, and only then drains slow provider work.
Never call a model or embedding provider from an ordered domain system.
Wall-clock latency must not alter physical rules.

System 1 is deterministic and has absolute priority. It clears incompatible
plans and movement, cancels or invalidates cognition, selects corrective
stations, and remains active until recovery. Correctness for late model results
depends on decision IDs and state revisions, not successful network
cancellation.

Character controllers propose one typed action through `go_to`, `travel_to`,
`perform`, `say`, or `wait`. Tool calls are validated, converted to immutable
intents, and committed at a deterministic boundary. They never directly mutate
ECS state or declare that an action succeeded.

Perception separates authoritative events from privacy-safe perceptible facts
and observer-specific perceived facts. Telemetry is intentionally omniscient
for operators and must never be reused as controller context. Other characters
may observe execution evidence, not private plans, destinations, reasons,
vitals, drives, prompts, or memories.

Scenarios are strict Pydantic input models and are the portable source for
world configuration. Provider credentials and endpoints belong in
`STAGE0_*` environment settings, never scenario JSON. Reusable character
profiles use the structured `human-v1` template and explicit `custom_sections`;
the UI can assign a scenario profile to each entity slot before creating a run.
Legacy grids use schema version 1; sparse hierarchical cities use schema version
2 with buildings, local maps, outdoor places, transport graphs, vehicles, and
hierarchical locations.

SQLite/JSONL datasets are research records, not resumable checkpoints. The
collector subscribes to domain events and projects specialized records plus
tick state. Adding or changing behavior often requires updating both telemetry
and dataset projections.

## Repository-specific conventions

- Use **character** for the simulated person and **character controller** for
  the software selecting decisions. Do not call the simulated person an LLM
  agent.
- Components contain typed state and local invariants; systems orchestrate
  deterministic behavior. Iterate entities and resolve ties in stable order.
- System order is simulation semantics. Treat an `order` change as a behavior
  change and check same-tick event, perception, and preemption consequences.
- `PlanAction` and tool/action names are closed vocabularies. Add deterministic
  preconditions, execution, events, persistence, and tests before exposing a
  new action as a controller tool.
- Strict schemas reject extra fields. For extensible character experiments, use
  ordered `custom_sections` and fields rather than allowing arbitrary keys.
- Model contracts in `application/agents/contracts.py` are provider-neutral.
  Normalize provider responses, usage, IDs, errors, and tool calls inside
  `adapters/llm`.
- Real providers are opt-in. Keep scripted and replay clients first-class so
  behavior can be tested without network access.
- Failures are explicit events (`*.failed`, `*.rejected`, `*.cancelled`,
  `system1.blocked`); do not create success-shaped fallbacks or silently ignore
  invalid input.
- Domain events carry structured payloads plus causation/correlation IDs.
  Avoid making event text the only representation of meaning. Canonical event
  comparisons exclude run and wall-clock identity.
- Memory must respect perception. A character must not gain an episode from
  another character's private global event merely because it is on the event
  bus.
- Browser scenario loading validates and stages configuration but must not start
  or advance a run. Start, pause, resume, single-step, and stop are separate
  lifecycle operations.
- The browser stores full event envelopes for inspection and uses summarized
  text only for collapsed rows. Include both `speech.*` and legacy
  `dialogue.*` in dialogue-oriented views.
- Browser controls must remain ordinary labeled forms or links bound directly
  to Python routes. Do not recreate lifecycle or telemetry state in JavaScript.
- Exercise changed UI workflows with Playwright using roles, labels, and
  visible outcomes. Source inspection and JavaScript syntax checks are not
  substitutes for browser behavior.
- When adding scenario fields, update the Pydantic definition, domain
  construction, representative scenario JSON, and any operator projection that
  should expose the field.
- Use `pathlib` and platform-neutral Python APIs. Linux is the primary runtime
  and CI target; preserve macOS and Windows compatibility. Static UI assets must
  remain inside the Python package because FastAPI serves them from an installed
  wheel.

Read `docs/CONCEPT_GUIDE.md` before changing simulation behavior, cognition,
memory, perception, telemetry, or persistence. Use
`docs/CHARACTER_PROFILE_GUIDE.md` for profile schema and extension rules, and
`docs/UI_TESTING.md` for browser changes.
