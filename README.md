# Stage 0 Simulation Sandbox

Ground-truth prototype for deterministic spatial simulation, continuous homeostasis, System 1 preemption, System 2 planning, episodic memory, and realtime telemetry.

See [starting_basic_PRD.md](starting_basic_PRD.md) for requirements and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the delivery plan.

## Prerequisites

- Python 3.12 or newer

## Backend setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn stage0_sim.api.app:app --reload
```

The API is available at `http://127.0.0.1:8000`; its health endpoint is `/health`.
The browser interface is served by the same Python process at `http://127.0.0.1:8000/ui/`.

## Headless simulation

Phase 1 provides a deterministic fixed-step runner and a minimal JSON scenario:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\minimal.json --ticks 10
```

The command writes canonical JSONL events to stdout. Canonical events omit run identity and
wall-clock timestamps so runs with the same scenario and seed can be compared byte-for-byte.
Use `--output .\events.jsonl` to write a file, `--full-events` for complete event envelopes, or
`--realtime --speed 4` to pace simulated time at four times wall-clock speed. Wall-clock pacing
never changes the fixed `dt` or authoritative simulation time.

The Phase 2 navigation scenario defines the Kitchen, Office, Bedroom, and Lounge, places one
station in each zone, and routes an agent through the grid using deterministic A*:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\navigation.json --ticks 20
```

Paths treat other agents as occupied tiles. Movement conflicts are resolved in stable agent-ID
order, and blocked paths emit `path.failed` or `path.invalidated` before deterministic retries.

Phase 3 scenarios can configure per-second satiety, energy, and stress derivatives for each
activity. The homeostasis example starts an agent in `WORKING` and emits the authoritative meter
state after each fixed-step Euler update:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\homeostasis.json --ticks 10
```

All meters use normalized values in `[0, 100]` and are clamped after every update. Internally,
satiety decreases as hunger rises; eating therefore increases satiety without contradictory
hunger semantics.

The Phase 4 scenario starts an agent with critical satiety while it is working. On the first tick,
System 1 clears the work plan and path, selects the nearest reachable fridge by A* path cost, and
locks navigation to that corrective station:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\system1-preemption.json --ticks 10
```

Low satiety and energy use lower-bound critical thresholds, while high stress uses an upper-bound
threshold. Separate recovery thresholds keep System 1 active after the immediate critical breach
has cleared, preventing oscillation.

Phase 5 station actions define explicit durations and deterministic effects or final targets.
Corrective actions progress over simulated time, emit lifecycle events, and release System 1 only
after its configured recovery threshold is crossed:

```json
{
  "action": "EAT",
  "duration": 5,
  "effect": {"satiety_delta": 60}
}
```

The preemption scenario now continues through `affordance.started`,
`affordance.progressed`, `system1.resolved`, and `affordance.completed`.
Station-driven meter updates also use the canonical `homeostasis.changed` event and replace normal
activity integration for that tick, preventing recovery effects from being counted twice.

Phase 6 adds an event-driven macro planner with provider-neutral planner, dialogue, and embedding
interfaces. Since no external LLM is required, the default provider is deterministic and local:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\fake-llm-planning.json --ticks 10
```

The fake planner sends the agent to the first available work station and starts a validated work
block. Agents without a `planner` component make zero planner calls, and System 1 always clears
generated plans before physical movement can contradict survival.

When the API server is running, the same provider is available through:

- `POST /fake-llm/v1/plan`
- `POST /fake-llm/v1/dialogue`
- `POST /fake-llm/v1/embeddings`

These endpoints return deterministic structured responses and require no API key or network
connection to an external model provider.

Phase 7 adds per-agent episodic memory with raw text, structured event metadata, simulation time,
importance, and provider-generated embeddings. Retrieval ranks candidates by configurable cosine
similarity, recency decay, and importance, with deterministic tie-breaking. Planner and dialogue
contexts receive only the configured `top_k` memories.

```json
{
  "memory": {
    "top_k": 3,
    "initial_episodes": [
      {"text": "Focused office work was productive.", "importance": 0.7}
    ]
  }
}
```

The fake embedding provider remains the default during prototype development. Memory and cognition
depend only on provider protocols, so the final local llama.cpp integration can use its
OpenAI-compatible chat and embedding endpoints without changing domain systems, retrieval scoring,
or planner validation. Embeddings are generated only for meaningful episodes and macro-clock
retrieval; routine physiology and movement ticks make no embedding calls.

## Simulation API and telemetry

Phase 8 exposes authoritative run management under `/simulation`:

- `POST /simulation/scenarios`
- `POST /simulation/runs`
- `GET /simulation/runs/{run_id}` and `/snapshot`
- `POST /simulation/runs/{run_id}/pause`, `/resume`, `/step`, `/speed`, and `/stop`
- `GET /simulation/runs/{run_id}/agents/{agent_id}`
- `PATCH /simulation/runs/{run_id}/agents/{agent_id}/vitals`
- `GET /simulation/runs/{run_id}/events`
- `WS /simulation/runs/{run_id}/stream`

The WebSocket stream carries monotonically increasing sequence numbers and publishes
`world_snapshot`, `agent_delta`, `homeostasis_delta`, `plan_changed`, `system1_event`,
`dialogue_event`, `simulation_status`, and general `event` messages. Snapshots publish at 10 Hz
from authoritative state; the telemetry clock never advances simulation time.

Scenario files use schema version 1:

```json
{
  "schema_version": 1,
  "name": "example",
  "seed": 42,
  "dt": 1.0,
  "speed": 1.0,
  "entities": [{"id": "agent-001", "components": {}}]
}
```

## Browser visualization

Phase 9 is packaged and served directly by FastAPI, so it requires no Node.js or npm installation.
Open `/ui/`, then start the built-in survival demo or load any compatible scenario JSON file.

The responsive canvas displays zones, obstacles, stations, agent positions, movement direction,
paths, and destinations. Selecting an agent shows its homeostatic gauges, activity, System 1
state, active drive, current plan, and memory count. Controls support pause, resume, single-step,
speed changes, stopping, and controlled vital mutation. The event log can be filtered by survival,
planning, affordance, dialogue, or failure events.

The browser protocol adapter validates envelopes and snapshots defensively, tolerates absent
optional fields and unknown message types, detects sequence gaps, refreshes authoritative state,
and reconnects with bounded exponential backoff. Protocol warnings remain visible rather than
silently breaking rendering.

## Ground-truth datasets

Phase 10 records every API and CLI run to SQLite using schema `stage0.dataset.v1`. The canonical
collector subscribes to domain events and completed micro-ticks, not 10 Hz telemetry, and stores:

- the scenario, seed, fixed `dt`, initial speed, and completion metadata
- initial and per-tick agent state vectors and spatial trajectories
- activity intervals with start, end, and simulated duration
- threshold crossings, plan transitions, and affordance lifecycle records
- dialogue and memory references, including recorded memory text and embeddings
- planner provider, status, latency, and input/output token metadata
- the complete structured domain-event envelope and unknown payload fields

The ECS-aware projection is isolated in `AgentStateProjector`; adding an internal component does
not alter or break the versioned dataset contract. SQLite migrations reject newer unsupported
schemas explicitly, while JSON payloads preserve additive fields for forward-compatible analysis.

Headless CLI runs persist automatically under `data/runs`. A custom database and versioned JSONL
export can be selected explicitly:

```powershell
.\.venv\Scripts\stage0-sim.exe run .\scenarios\system1-preemption.json `
  --ticks 10 `
  --database .\data\runs\experiment.sqlite3 `
  --export .\data\runs\experiment.jsonl
```

API runs expose `GET /simulation/runs/{run_id}/data` for record counts and
`GET /simulation/runs/{run_id}/export` for newline-delimited JSON. Each export starts with a run
manifest followed by monotonically ordered canonical records, allowing offline analysis without
the browser or telemetry replay.

## Quality commands

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
```
