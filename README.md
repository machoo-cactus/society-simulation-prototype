# Stage 0 Simulation Sandbox

A deterministic sandbox for experimenting with spatial agents, continuous
homeostasis, System 1 survival interrupts, System 2 planning, episodic memory,
dialogue, realtime telemetry, and reproducible datasets.

The project runs as one Python application. FastAPI serves both the simulation
API and a dependency-free browser interface; Node.js is not required.

## Quick start

Requirements:

- Python 3.12 or newer
- SQLite support in Python (included in normal CPython builds)

Clone the repository, enter its root directory, and create a virtual environment.

### Linux and macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Start the application from the repository root:

```bash
python -m uvicorn stage0_sim.api.app:app --reload
```

Pass standard Uvicorn options to change the bind address, port, or logging:

```bash
python -m uvicorn stage0_sim.api.app:app \
  --host 0.0.0.0 --port 8080 --log-level info
```

Open <http://127.0.0.1:8000/ui/>. The health endpoint is
<http://127.0.0.1:8000/health>, and OpenAPI documentation is available at
<http://127.0.0.1:8000/docs>.

Reusable characters are stored as individual JSON files under `characters/`.
Manage them at <http://127.0.0.1:8000/ui/characters.html>.

## Update an existing checkout

On Windows, run the repository update script from PowerShell:

```powershell
.\update.ps1
```

It performs a fast-forward-only `git pull`, creates `.venv` with Python 3.12 if
needed, upgrades pip, refreshes the editable `.[dev]` installation, and creates
`.env` from `.env.example` only when `.env` does not already exist. It does not
delete datasets or overwrite local environment settings.

To refresh only the environment without pulling:

```powershell
.\update.ps1 -SkipPull
```

Equivalent manual commands:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Try the included experiments

After installation, `stage0-sim` runs a scenario without the browser:

```bash
stage0-sim run scenarios/minimal.json --ticks 10
stage0-sim run scenarios/navigation.json --ticks 20
stage0-sim run scenarios/homeostasis.json --ticks 60
stage0-sim run scenarios/system1-preemption.json --ticks 20
stage0-sim run scenarios/fake-llm-planning.json --ticks 30
stage0-sim run scenarios/sparse-city-car-demo.json --ticks 700
stage0-sim run scenarios/greyford-office-evening.json --ticks 3000
```

| Scenario | What it demonstrates |
| --- | --- |
| `minimal.json` | Fixed-step clock, deterministic events, and basic entities |
| `navigation.json` | Grid zones, A* pathfinding, occupancy, and movement |
| `homeostasis.json` | Activity-dependent satiety, energy, and stress trajectories |
| `system1-preemption.json` | Plan cancellation, survival navigation, affordance recovery, and resumption |
| `fake-llm-planning.json` | Post-tick planning, memory retrieval, and validated routines without an external model |
| `real-llm-tool-agent.json` | Observer-specific sensing and externally configured typed-tool character control |
| `sparse-city-car-demo.json` | Hierarchical location, sparse city routing, explicit car travel, and city UI |
| `greyford-office-evening.json` | Large provincial-capital city, detailed office neighborhood, explicit character knowledge, dinner, and mixed walk/metro travel home |

By default, canonical events are written to standard output and a SQLite dataset
is created under `data/runs/`. Canonical events omit run IDs and wall-clock
timestamps so identical seeded runs can be compared.

Useful CLI options:

```bash
# Write events and a versioned dataset export.
stage0-sim run scenarios/system1-preemption.json \
  --ticks 20 \
  --output data/runs/events.jsonl \
  --database data/runs/experiment.sqlite3 \
  --export data/runs/experiment.jsonl

# Include complete event envelopes.
stage0-sim run scenarios/navigation.json --ticks 20 --full-events

# Pace simulation time against wall time at 4x speed.
stage0-sim run scenarios/homeostasis.json --ticks 60 --realtime --speed 4

# Use a different character library.
stage0-sim run scenarios/real-llm-tool-agent.json \
  --characters-dir path/to/characters --ticks 30

# Migrate an older scenario containing an inline character catalog.
stage0-sim characters extract scenarios/legacy.json \
  --directory characters --write
```

Use `python -m stage0_sim.cli` instead of `stage0-sim` if the virtual
environment's executable directory is not on `PATH`.

## Use the browser sandbox

The UI is bundled under `src/stage0_sim/web/` and served at `/ui/` by FastAPI.
It is intentionally plain HTML, CSS, and JavaScript:

- no separate frontend server;
- no Node.js build;
- no duplicated simulation logic in the browser.

The browser code uses native ES modules. API access, telemetry protocol parsing,
lifecycle state, and transcript rendering are separated into focused modules;
all `web/*.js` files are shipped as package data.

From the UI you can:

- load and validate a scenario without starting it;
- assign reusable character-library entries to scenario character slots;
- start, pause, single-step, resume, stop, and restart the loaded scenario;
- inspect positions, paths, destinations, activities, plans, and memories;
- watch satiety, energy, and stress change;
- mutate vitals to force survival behavior;
- search and filter planning, survival, perception, cognition, speech, dialogue,
  and failure events;
- expand, copy, and inspect complete long-form event payloads;
- view character names, current vision, hearing pulses, speech bubbles, and a
  delivered-speech transcript;
- follow characters between building and city views with AUTO/MANUAL scale,
  pan, zoom, vehicle progress, and travel events;
- download the run's versioned JSONL dataset.

The Characters page provides durable create, duplicate, rename, edit, and
delete operations for the JSON character library. Character editing is
independent from loading or running a scenario.

The empty top-level `frontend/` scaffold from the original proposed architecture
has been removed. It had no source files and was not used by packaging or at
runtime.

## Create an experiment

Copy a scenario and change one variable at a time:

```bash
cp scenarios/system1-preemption.json scenarios/my-experiment.json
stage0-sim run scenarios/my-experiment.json --ticks 120 \
  --database data/runs/my-experiment.sqlite3 \
  --export data/runs/my-experiment.jsonl
```

Legacy single-grid scenarios use schema version 1:

```json
{
  "schema_version": 1,
  "name": "example",
  "seed": 42,
  "dt": 1.0,
  "speed": 1.0,
  "world": {
    "width": 8,
    "height": 5,
    "blocked": [],
    "zones": [],
    "stations": []
  },
  "entities": []
}
```

Sparse city scenarios use schema version 2. See
`scenarios/sparse-city-car-demo.json` for the current city, local-map,
transport-network, vehicle, and hierarchical-location schema.

Common experiment variables include:

- `seed`, `dt`, and initial simulation speed;
- grid dimensions, blocked cells, zones, and station placement;
- activity-specific homeostasis coefficients;
- critical and recovery thresholds;
- initial positions, vitals, activities, plans, goals, and memories;
- affordance duration, capacity, and deterministic effects;
- memory relevance, recency, and importance weights.
- schema-version-2 city bounds, districts, buildings, entrances, local maps,
  outdoor places, sparse transport edges, vehicles, and scripted `TRAVEL_TO`
  actions.

The full examples in `scenarios/` are the most reliable schema reference.
Invalid fields and values are rejected when a scenario is loaded.

## API workflow

The browser uses the same public API available to other clients:

1. Manage reusable files through `GET/POST /characters` and
   `GET/PUT/DELETE /characters/{character_id}`.
2. `POST /simulation/scenarios` with a scenario document. Character references
   are resolved and frozen at this boundary.
3. `POST /simulation/runs` with the returned `scenario_id`.
4. Inspect `/simulation/runs/{run_id}` or its `/snapshot`.
5. Control the run with `/pause`, `/resume`, `/step`, `/speed`, and `/stop`.
6. Stream ordered telemetry from
   `ws://127.0.0.1:8000/simulation/runs/{run_id}/stream`.
7. Export records from `/simulation/runs/{run_id}/export`.

Additional endpoints provide agent inspection, controlled vital mutation, event
history, and dataset summaries. Model APIs are deliberately not mounted in the
simulation process.

API run objects are process-local. Restarting the server does not restore a live
runner, although completed records and episodic memories remain in SQLite.

The browser uses telemetry schema `stage0.telemetry.v2`. Static world/profile
bootstrap data, latest runtime snapshots, and durable domain-event cursors are
separate. Reconnecting clients backfill missed events through the REST history
endpoint before resuming live updates.

## Reproducibility and provider isolation

- The micro-clock advances by a fixed simulated `dt`.
- Entity, system, pathfinding, and conflict ordering are deterministic.
- The run seed is stored in the dataset manifest.
- Physical systems enqueue cognition work but never call a provider from the
  ordered system pass.
- System 1 preemption cancels conflicting planner/dialogue work.
- Telemetry samples authoritative state without advancing the simulation.
- Legacy planner tests use deterministic in-process fakes.

Tool-agent scenarios require an explicitly configured OpenAI-compatible or
replay provider. Provider work runs outside the ordered physical system pass,
and completed tools are applied at deterministic post-system boundaries. The
default `cognition.execution_mode` is `global_barrier`: all requests created by
one tick run concurrently, simulation time remains frozen until the whole batch
settles, and results then commit in stable order. Set the mode to `background`
only when intentionally reproducing the earlier latency-independent behavior.

Controllers must return exactly one tool call. OpenAI-compatible requests
therefore default to `tool_choice=required`. `skip` means that no useful
decision is needed and defers cognition without creating a plan; `wait` creates
an intentional in-world idle action.

### Start the standalone fake model API

The fake server uses the same `/v1/chat/completions` shape as a real
OpenAI-compatible server. Each request increments a process-local counter; text
responses say `Fake response N`, while tool requests return a valid `wait` call
whose duration counts upward.

```powershell
stage0-fake-llm --host 127.0.0.1 --port 8081
```

The equivalent module command is
`python -m stage0_sim.api.fake_llm --host 127.0.0.1 --port 8081`.

In another PowerShell window:

```powershell
$env:STAGE0_LLM_PROVIDER = "openai-compatible"
$env:STAGE0_LLM_BASE_URL = "http://127.0.0.1:8081/v1"
$env:STAGE0_LLM_MODEL = "stage0-fake"
stage0-sim run scenarios/real-llm-tool-agent.json --ticks 30
```

For llama.cpp, point `STAGE0_LLM_BASE_URL` at either the server root, its `/v1`
root, or the complete `/v1/chat/completions` URL. The adapter retries transient
503 responses with backoff and includes llama.cpp's response detail in failures.

## Configuration

Settings use `STAGE0_` environment variables and may be placed in `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STAGE0_CORS_ORIGINS` | `[]` | Optional origins for separately hosted clients |
| `STAGE0_DATA_DIRECTORY` | `data/runs` | API dataset directory |
| `STAGE0_DATASET_DATABASE` | `stage0.sqlite3` | API SQLite filename |
| `STAGE0_CHARACTER_DIRECTORY` | `characters` | Reusable character JSON directory |
| `STAGE0_LLM_PROVIDER` | unset | Set to `openai-compatible` to enable a real model |
| `STAGE0_LLM_BASE_URL` | unset | OpenAI-compatible API root, including `/v1` |
| `STAGE0_LLM_MODEL` | unset | Provider model identifier |
| `STAGE0_LLM_API_KEY` | unset | Optional provider credential; never persisted |
| `STAGE0_LLM_TIMEOUT_SECONDS` | `30` | Provider HTTP timeout |
| `STAGE0_LLM_RETRY_ATTEMPTS` | `3` | Attempts for transient HTTP/transport failures |
| `STAGE0_LLM_RETRY_DELAY_SECONDS` | `1` | Initial retry backoff in seconds |
| `STAGE0_LLM_TOOL_CHOICE` | `required` | OpenAI-compatible tool-choice mode; `none` is invalid for tool-agent cognition |
| `STAGE0_LLM_MAX_OUTPUT_TOKENS` | `512` | Deployment ceiling per response |
| `STAGE0_LLM_MAX_CONCURRENCY` | `4` | Deployment ceiling for concurrent requests |
| `STAGE0_LLM_RECORD_PATH` | unset | Sanitized model request/response JSONL |
| `STAGE0_LLM_REPLAY_PATH` | unset | Recording used when provider is `replay` |

Paths are interpreted relative to the process working directory. Run commands
from the repository root, or supply absolute paths in `.env`.
Server bind and logging options belong to Uvicorn; pass them on its command line
or use Uvicorn's supported environment variables.

## Project layout

```text
.
|-- src/stage0_sim/       Python package
|   |-- domain/           Deterministic ECS components and systems
|   |-- application/      Runners, planning, memory, telemetry, datasets
|   |-- adapters/         Fake providers and SQLite persistence
|   |-- api/              FastAPI routes and application
|   `-- web/              Browser UI packaged and served by FastAPI
|-- tests/                Pytest suite
|-- scenarios/            Runnable experiment definitions
|-- docs/                 PRD, implementation plan, and state assessment
|-- data/runs/            Generated local datasets (ignored)
`-- pyproject.toml        Packaging and tool configuration
```

The `src/` layout prevents accidental imports from the working tree and tests the
installed package boundary. Static UI files stay inside the package because the
application must also serve them after wheel installation.

## Development

Run the existing checks from the repository root:

```bash
python -m pytest
python -m ruff check .
python -m mypy
```

Build an installable wheel with:

```bash
python -m pip wheel . --no-deps --wheel-dir dist
```

Generated caches, virtual environments, build metadata, and `data/runs/` are
ignored. Simulation databases are experiment output; delete them only when their
records are no longer needed.

Design and status documents:

- [Concept guide for advanced development](docs/CONCEPT_GUIDE.md)
- [Product requirements](docs/starting_basic_PRD.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Project state assessment](docs/PROJECT_STATE_ASSESSMENT.md)
- [Real LLM tool-agent plan](docs/REAL_LLM_TOOL_AGENT_PLAN.md)
- [Character profile authoring guide](docs/CHARACTER_PROFILE_GUIDE.md)
- [Large-scale world and transport plan](docs/LARGE_SCALE_WORLD_AND_TRANSPORT_PLAN.md)

## Platform support

Runtime code uses `pathlib`, Python APIs, and URL paths rather than shell-specific
filesystem syntax. The package and tests are intended to run on Linux, macOS, and
Windows. Repository text files are normalized to LF for reliable Linux tooling;
Windows command files, if added later, remain CRLF through `.gitattributes`.
