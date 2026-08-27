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

## Try the included experiments

After installation, `stage0-sim` runs a scenario without the browser:

```bash
stage0-sim run scenarios/minimal.json --ticks 10
stage0-sim run scenarios/navigation.json --ticks 20
stage0-sim run scenarios/homeostasis.json --ticks 60
stage0-sim run scenarios/system1-preemption.json --ticks 20
stage0-sim run scenarios/fake-llm-planning.json --ticks 30
```

| Scenario | What it demonstrates |
| --- | --- |
| `minimal.json` | Fixed-step clock, deterministic events, and basic entities |
| `navigation.json` | Grid zones, A* pathfinding, occupancy, and movement |
| `homeostasis.json` | Activity-dependent satiety, energy, and stress trajectories |
| `system1-preemption.json` | Plan cancellation, survival navigation, affordance recovery, and resumption |
| `fake-llm-planning.json` | Post-tick planning, memory retrieval, and validated routines without an external model |

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
```

Use `python -m stage0_sim.cli` instead of `stage0-sim` if the virtual
environment's executable directory is not on `PATH`.

## Use the browser sandbox

The UI is bundled under `src/stage0_sim/web/` and served at `/ui/` by FastAPI.
It is intentionally plain HTML, CSS, and JavaScript:

- no separate frontend server;
- no Node.js build;
- no duplicated simulation logic in the browser.

From the UI you can:

- start the built-in survival demo or load a scenario JSON file;
- inspect positions, paths, destinations, activities, plans, and memories;
- watch satiety, energy, and stress change;
- pause, resume, single-step, change speed, or stop a run;
- mutate vitals to force survival behavior;
- filter planning, survival, affordance, dialogue, and failure events;
- download the run's versioned JSONL dataset.

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

Every scenario uses schema version 1:

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

Common experiment variables include:

- `seed`, `dt`, and initial simulation speed;
- grid dimensions, blocked cells, zones, and station placement;
- activity-specific homeostasis coefficients;
- critical and recovery thresholds;
- initial positions, vitals, activities, plans, goals, and memories;
- affordance duration, capacity, and deterministic effects;
- memory relevance, recency, and importance weights.

The full examples in `scenarios/` are the most reliable schema reference.
Invalid fields and values are rejected when a scenario is loaded.

## API workflow

The browser uses the same public API available to other clients:

1. `POST /simulation/scenarios` with a scenario document.
2. `POST /simulation/runs` with the returned `scenario_id`.
3. Inspect `/simulation/runs/{run_id}` or its `/snapshot`.
4. Control the run with `/pause`, `/resume`, `/step`, `/speed`, and `/stop`.
5. Stream ordered telemetry from
   `ws://127.0.0.1:8000/simulation/runs/{run_id}/stream`.
6. Export records from `/simulation/runs/{run_id}/export`.

Additional endpoints provide agent inspection, controlled vital mutation, event
history, and dataset summaries. The fake planner, dialogue, and embedding
providers are exposed under `/fake-llm/v1/` for local integration experiments.

API run objects are process-local. Restarting the server does not restore a live
runner, although completed records and episodic memories remain in SQLite.

## Reproducibility and provider isolation

- The micro-clock advances by a fixed simulated `dt`.
- Entity, system, pathfinding, and conflict ordering are deterministic.
- The run seed is stored in the dataset manifest.
- Physical systems enqueue cognition work but never call a provider from the
  ordered system pass.
- System 1 preemption cancels conflicting planner/dialogue work.
- Telemetry samples authoritative state without advancing the simulation.
- Fake providers are deterministic and require no credentials or network calls.

Real model providers are intentionally not included. A remote provider should
run behind the existing macro-work boundary and return results for deterministic
application outside the ordered physical system pass.

## Configuration

Settings use `STAGE0_` environment variables and may be placed in `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STAGE0_CORS_ORIGINS` | `[]` | Optional origins for separately hosted clients |
| `STAGE0_DATA_DIRECTORY` | `data/runs` | API dataset directory |
| `STAGE0_DATASET_DATABASE` | `stage0.sqlite3` | API SQLite filename |

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

- [Product requirements](docs/starting_basic_PRD.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Project state assessment](docs/PROJECT_STATE_ASSESSMENT.md)

## Platform support

Runtime code uses `pathlib`, Python APIs, and URL paths rather than shell-specific
filesystem syntax. The package and tests are intended to run on Linux, macOS, and
Windows. Repository text files are normalized to LF for reliable Linux tooling;
Windows command files, if added later, remain CRLF through `.gitattributes`.
