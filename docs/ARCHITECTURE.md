# Architecture and Authority

**Owner:** Current component boundaries, dependency direction, and sources of
truth.

Stage 0 is a deterministic, fully materialized execution layer. It models every
active character, location, physiological value, action, and world transition
explicitly; research records and operator projections observe that execution
but do not control it.

## Dependency direction

```text
domain <- application <- adapters
                       <- API / CLI / server-rendered UI composition
```

| Layer | Owns | Must not own |
| --- | --- | --- |
| `domain` | ECS state, world topology, pathfinding, physiology, System 1, plans, affordances, speech, transactions, ordered deterministic systems | Providers, prompts, HTTP, filesystems, UI state |
| `application` | Runner lifecycle, scenario preparation, cognition scheduling/barrier, context and perception projection, intent validation/commit, memory, telemetry, dataset capture | Provider-specific objects or invented physical outcomes |
| `adapters` | OpenAI-compatible/replay clients, SQLite, filesystem libraries | Domain policy |
| `api` and CLI | Composition, validation boundaries, public transport | A second simulation authority |
| `web` | Accessible operator presentation and progressive browser interactions | Simulation, lifecycle, or telemetry state replicated in JavaScript |

Application-facing ports live in `src\stage0_sim\application\ports\`. Concrete
persistence and filesystem behavior stays in adapters. Model contracts in
`application\agents\contracts.py` are provider-neutral.

## Authoritative state and projections

- The ECS registry and domain resources are live simulation authority.
- Scenario JSON is the portable source of initial configuration.
- Prepared scenarios freeze resolved character and element sources before a
  run starts.
- Domain events describe authoritative transitions with structured payloads
  and causal identity.
- Perceptible facts remove private fields; perceived facts are delivered to one
  observer under deterministic modality rules.
- Telemetry is an omniscient operator projection.
- Dataset v3 is an immutable research observation with rebuildable relational
  projections.
- SQLite/JSONL datasets are not live checkpoints and cannot restore a runner.

Telemetry, raw research traces, model recordings, and UI form state must never
be used as character perception or character-controller context.

## World and information structure

Grid worlds execute on one discrete local map. Sparse cities compose:

```text
city -> city zone -> building instance -> room -> object or tile
```

Buildings, rooms, objects, and NPC roles may come from hash-pinned reusable
element resources. `SpaceRegistry`, locators, transitions, and topology
adapters provide the general navigation boundary; local movement and city
travel remain deterministic executors beneath one `NAVIGATE` intention.

Character information uses coherent source documents and derived retrieval
indexes. Dossiers, memories, observations, summaries, and route knowledge keep
their provenance. Descriptive information may influence decisions but does not
silently grant or deny physical permission.

## Extension rules

- Components contain typed state and local invariants; systems orchestrate.
- Entity iteration, route ties, capacity conflicts, and commits use stable
  ordering.
- A new action requires deterministic preconditions, execution, events,
  persistence, and tests before it becomes a controller tool.
- A provider swap must not require domain changes.
- A new perceptible fact must start from authoritative evidence, assign a
  disclosure class, remove private fields, resolve recipients deterministically,
  and have a leakage test.
- Scenario and dataset contract changes require explicit schema-version review.
- System-order changes are behavior changes; review same-tick transitions,
  perception, preemption, events, and datasets.

See [Runtime semantics](RUNTIME.md), [Actions, tools, and events](ACTIONS_AND_EVENTS.md),
and [Research data](DATA_COLLECTION.md).
