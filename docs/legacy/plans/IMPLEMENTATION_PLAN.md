# Stage 0 Implementation Plan

> Legacy implementation record. The work described here is complete; current
> architecture and operating instructions live in `docs/CONCEPT_GUIDE.md` and
> `README.md`.

## Implementation status (2026-08-26)

All ten Stage 0 phases and milestones M1-M5 are implemented and verified in the
current repository. Provider work uses deterministic macro-work queues: ordered
micro-tick systems only enqueue planning, embedding, and dialogue work, and the
runner drains those queues after system execution at an explicit post-tick
boundary. Survival ticks cancel or defer cognitive work, so System 1 correction
never calls a provider. The acceptance, capacity fallback, integrated dialogue,
durable memory, provider-stack isolation, browser telemetry contract, and
accelerated two-hour stability paths have automated coverage.

Live run/scenario objects remain intentionally process-local. SQLite persists the
canonical dataset and episodic-memory rows and exposes a rehydration boundary,
but a stopped simulation cannot be resumed and API restart does not reconstruct
live runners.

## 1. Baseline decisions

The prototype will be a single-process, deterministic simulation backend with a browser-based telemetry UI. LLM usage must be isolated behind the macro-clock planner so physical movement, physiology, arbitration, and affordance execution run with zero model calls.

### Homeostatic state semantics

The PRD applies a lower-bound critical threshold to every meter, but stress becomes harmful when it is high. Use normalized values in `[0, 100]` with explicit threshold directions:

| Meter | Meaning | Critical condition | Corrective affordance |
| --- | --- | --- | --- |
| Satiety, displayed as Hunger | Higher is healthier | `satiety <= 15` | Fridge / `EAT` |
| Energy | Higher is healthier | `energy <= 15` | Bed / `SLEEP` |
| Stress | Higher is worse | `stress >= 85` | Lounge / `RELAX` |

Use `satiety` internally to avoid contradictory operations such as eating increasing hunger. The UI may display Satiety directly or derive `hunger = 100 - satiety`.

Additional decisions:

- Physics uses a fixed `dt = 1` simulated second.
- Telemetry publishes at 10 Hz but does not run physiology at 10 Hz; it broadcasts or interpolates the latest authoritative state.
- All stochastic behavior uses a seeded RNG, and run metadata records the seed.
- System 1 remains active until its triggering meter crosses a configurable recovery threshold, preventing oscillation.
- Simultaneous critical drives are selected by severity with deterministic tie-breaking.
- LLM failures do not block simulation ticks; they produce explicit planner-failure events.

## 2. Technical architecture

### Implemented stack

- Backend: Python, FastAPI, and asyncio
- Simulation: typed Python components and ordered systems
- Pathfinding: deterministic A*
- Persistence: SQLite for runs, events, plans, and memory metadata
- Vector memory: in-memory cosine index with embeddings persisted in SQLite
- Frontend: package-data HTML, CSS, plain JavaScript, and HTML Canvas
- Realtime transport: WebSocket
- Tests and quality: pytest, Ruff, and strict mypy

Keep the simulation domain independent of FastAPI, storage, WebSockets, and any particular LLM provider.

```text
src/
  stage0_sim/
    domain/
      components/
      systems/
      world/
      events/
    application/
    adapters/
      llm/
      embeddings/
      persistence/
      websocket/
    api/
tests/

src/stage0_sim/web/
  index.html
  styles.css
  app.js
  demo.json
```

### Runtime flow

```text
Fixed micro-tick
  1. Apply active activity effects
  2. Integrate homeostatic state
  3. Clamp meters to valid ranges
  4. Detect critical threshold crossings
  5. Run System 1/System 2 arbitration
  6. Advance deterministic movement
  7. Resolve affordance actions
  8. Update plan/action state
  9. Record domain events and trajectory samples

Macro-clock
  - Ordered systems enqueue bounded memory, plan, and dialogue requests
  - The runner drains queued work only after the ordered system pass
  - Survival/correction ticks defer memory work and cancel cognitive work
  - Results are validated and applied at the explicit post-tick boundary
  - Provider work never directly mutates positions, vitals, or world state

Telemetry clock
  - Publishes snapshots at 10 Hz
  - Reads authoritative simulation state
  - Does not advance simulation state
```

## 3. Domain contracts

### World entities

- `World`: grid dimensions, tiles, zones, stations, agents, simulation time, and tick number
- `Zone`: ID, name, type, and tile bounds or tile set
- `AffordanceStation`: position, supported actions, availability, capacity, deterministic effects, and action duration

### Agent components

- `PositionComponent`
- `HomeostasisComponent`
- `ActivityComponent`
- `MovementComponent`
- `DriveComponent`
- `PlanComponent`
- `MemoryComponent`
- `ConversationComponent`
- `TelemetryMetadataComponent`

### Plan action vocabulary

LLM output must be parsed into a closed vocabulary:

```text
MOVE_TO(zone_or_station)
WORK(duration)
SOCIALIZE(target, duration)
READ(duration)
EAT
SLEEP
RELAX
IDLE(duration)
```

The LLM proposes goals and scheduled actions only. Domain systems validate preconditions and perform all physical effects.

### Event schema

Every event includes:

- run ID and event ID
- simulation tick and simulation timestamp
- wall-clock timestamp
- agent ID when applicable
- event type and structured payload
- causation ID and correlation ID

Initial event types:

- `simulation.started`
- `homeostasis.changed`
- `threshold.breached`
- `system1.activated`
- `plan.cleared`
- `path.requested`
- `path.completed`
- `affordance.started`
- `affordance.completed`
- `system1.resolved`
- `planner.requested`
- `planner.completed`
- `planner.failed`
- `memory.recorded`
- `dialogue.generated`
- `dialogue.requested`
- `dialogue.failed`
- `dialogue.cancelled`
- `memory.requested`
- `memory.failed`

## 4. Ordered implementation phases

### Phase 1: Deterministic simulation foundation

Implement:

- Fixed-step simulation runner
- Simulation clock independent of wall-clock time
- Pause, resume, single-step, and configurable speed
- Seeded run configuration
- ECS registry and ordered system execution
- Structured event bus
- JSON scenario loader
- Minimal command-line runner

**Gate:** A headless world advances reproducibly and emits tick events without an LLM or UI.

### Phase 2: Grid, zones, and pathfinding

Implement:

- Rectangular grid with walkable and blocked tiles
- Zone definitions and station placement
- A* using Manhattan distance
- Collision-aware movement
- Deterministic neighbor ordering and tie-breaking
- Path invalidation when occupancy changes
- Explicit no-path events and retry behavior

Initially permit one agent per tile and resolve simultaneous movement conflicts in stable agent-ID order.

**Gate:** Agents navigate deterministically among the Kitchen, Bedroom, Office, and Lounge.

### Phase 3: Homeostatic ODE system

Implement configurable activity coefficients:

```text
IDLE:     satiety decay, mild energy decay, mild stress recovery
WALKING:  increased satiety and energy decay
WORKING:  increased energy decay and stress accumulation
EATING:   satiety recovery
SLEEPING: energy recovery and stress reduction
RELAXING: stress reduction
```

Start with fixed-step explicit Euler integration:

```text
next = clamp(current + derivative(current, activity) * dt, 0, 100)
```

Store coefficients in scenario configuration for future calibration.

**Gate:** Meter trajectories are smooth and deterministic and have no network or LLM dependency.

### Phase 4: System 1 arbitration and preemption

Implement the interrupt as a state machine:

```text
NORMAL
  -> CRITICAL_DETECTED
  -> PREEMPTING
  -> NAVIGATING_TO_CORRECTION
  -> EXECUTING_CORRECTION
  -> RECOVERED
  -> NORMAL
```

On activation:

1. Record the threshold breach.
2. Clear the System 2 queue immediately.
3. Cancel the current non-survival action and path.
4. Select the highest-priority critical drive.
5. Find the nearest available corrective affordance by actual path cost.
6. Lock action selection to the corrective workflow.
7. Resume System 2 only after recovery criteria are met.

Handle exceptional states explicitly:

- If no corrective station is reachable, enter observable `BLOCKED_SURVIVAL`.
- If a station is unavailable, retry or select the next-nearest station deterministically.
- If another meter becomes critical, recompute drive priority.
- Reject LLM output that contradicts survival behavior without another LLM call.

**Gate:** Forced satiety, energy, and stress scenarios reliably override all planned actions.

### Phase 5: Affordance execution

Implement stations as deterministic state machines with explicit preconditions, durations, and effects.

```text
Fridge:
  action: EAT
  preconditions: station available and agent on station tile
  effect: satiety += 60

Bed:
  action: SLEEP
  effect: energy recovers to 100

Lounge:
  action: RELAX
  effect: stress decreases
```

Prefer time-based recovery during activities over instantaneous mutation where practical while preserving required final outcomes.

**Gate:** Arriving at a station produces reproducible state changes and recovery events.

### Phase 6: System 2 planner and LLM isolation

Create provider-neutral interfaces and a deterministic queued coordinator:

- `Planner`
- `DialogueGenerator`
- `EmbeddingProvider`

Planner context is bounded and structured:

- current vitals and location
- available zones and affordances
- daily goals
- relevant memories
- current simulation time

Require schema-constrained output and validate proposed actions against the world model. Add a deterministic scripted planner for development and CI so physical behavior never requires API credentials.

**Gate:** Non-critical agents follow generated routines while System 1 can always clear them.

**Implemented:** Planner requests are queued by `MacroPlanningSystem`; provider
execution and result application occur at the runner's post-tick boundary.
Failed and cancelled work carries provider/token/latency metadata when known.

### Phase 7: Episodic memory and retrieval

Record meaningful episodes:

- observations and dialogue turns
- plan completion or failure
- System 1 activation and recovery
- major biological changes
- social interactions

Store raw text, structured metadata, simulation timestamp, importance score, and embedding vector. Rank retrieval with:

```text
score =
  semantic_weight * cosine_similarity +
  recency_weight * recency_decay +
  importance_weight * importance
```

Use deterministic tie-breaking and configurable `top_k`. Embedding generation must remain off the micro-tick path.

**Gate:** Planning and dialogue prompts include relevant recent and semantically related memories.

**Implemented:** Episodic rows are also persisted in SQLite and can rehydrate a
fresh in-memory index. Initial scenario episodes and generated dialogue episodes
are included in canonical export. This is memory rehydration, not simulation
checkpoint/resume.

### Phase 8: Telemetry backend and WebSocket protocol

Expose:

- scenario creation and loading
- run start, pause, resume, and single-step
- simulation speed control
- agent inspection
- controlled test mutation of vitals
- event history
- WebSocket snapshot and event streams

Suggested messages:

- `world_snapshot`
- `agent_delta`
- `homeostasis_delta`
- `plan_changed`
- `system1_event`
- `dialogue_event`
- `simulation_status`

Use sequence numbers so clients can detect dropped or out-of-order updates.

**Gate:** Authoritative state and events stream at 10 Hz without changing simulation results.

### Phase 9: Browser visualization

Implement:

- Canvas grid, obstacles, and zone boundaries
- Affordance icons
- Agent position, heading, current path, and destination
- Agent selection and inspection
- Homeostatic gauges with critical thresholds
- Current activity, System 1 drive, and System 2 queue
- Filterable event and dialogue log
- Simulation controls
- Connection status and latest sequence number

The frontend renders backend state and never owns simulation behavior.

**Gate:** An observer can see an agent abandon work, seek a corrective station, recover, and resume normal planning.

### Phase 10: Ground-truth data export

Persist:

- run configuration and seed
- agent state vectors
- spatial trajectories
- activities and durations
- threshold crossings
- plan transitions
- affordance use
- dialogue and memory references
- LLM request metadata, latency, and token counts

Support JSONL or Parquet export with a versioned schema. Keep telemetry sampling separate from the canonical simulation log.

**Gate:** Completed runs can be analyzed offline without replaying the UI.

## 5. Verification plan

| Target | Automated verification |
| --- | --- |
| Determinism | The same scenario and seed produce identical canonical event logs |
| Biological decay | Meter values match coefficient calculations over fixed ticks |
| Zero-token micro-clock | The LLM adapter records zero calls during decay, movement, and affordance-only runs |
| System 1 preemption | Set satiety to `10`; the plan clears on the next tick and the destination becomes a fridge |
| Absolute priority | A continue-working plan injected while critical is rejected or remains inactive |
| Nearest correction | Multiple stations are selected by shortest reachable A* path |
| Multi-drive arbitration | Simultaneous critical meters resolve by configured severity and tie-break rules |
| Affordance recovery | Arrival and execution produce exact configured state changes |
| Collision behavior | Simultaneous movement resolves consistently without tile overlap |
| Memory retrieval | Relevant recent episodes outrank irrelevant or stale episodes |
| Telemetry fidelity | Snapshot coordinates and vitals equal authoritative backend state at the same sequence |
| LLM failure isolation | A planner timeout emits an event while simulation ticks continue |
| Long-run stability | Accelerated multi-hour runs keep meters bounded and state valid |

The main end-to-end acceptance test is:

1. Start an agent in the Office with a queued `WORK` action.
2. Force satiety to `10`.
3. Advance one micro-tick.
4. Confirm the work action and path are cleared.
5. Confirm System 1 targets the nearest reachable fridge.
6. Advance through arrival and completion of `EAT`.
7. Confirm satiety recovery and System 1 release.
8. Confirm normal planning becomes eligible again.
9. Confirm no LLM request occurred during steps 2 through 7.

## 6. Milestones

| Milestone | Scope | Completion gate |
| --- | --- | --- |
| M1: Headless physical sandbox | Phases 1-3 | Deterministic grid movement and homeostasis tests pass |
| M2: Survival loop | Phases 4-5 | All critical-drive scenarios preempt and recover |
| M3: Cognitive agent | Phases 6-7 | Structured planning and memory retrieval preserve System 1 priority |
| M4: Observable prototype | Phases 8-9 | Browser reflects authoritative state and events at 10 Hz |
| M5: Research baseline | Phase 10 and full verification | Long-run data export is reproducible and success metrics pass |

The critical path M1 -> M2 -> M3 -> M4 -> M5 is complete for the deterministic
fake/scripted-provider baseline. External real-LLM adapters and restart-safe live
run checkpointing remain optional future work rather than Stage 0 claims.
