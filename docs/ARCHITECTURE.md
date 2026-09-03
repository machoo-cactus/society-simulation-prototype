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
- Physical ECS components plus the `SpatialIndex` are the live truth for room,
  pose, footprint, obstruction, open/locked state, parent relation, custody,
  hands, and posture.
- `CityWorld`, resolved element graphs, and the room/object hierarchy are
  immutable construction, topology, and display metadata. They do not become a
  second copy of mutable object state.
- Scenario JSON is the portable source of initial configuration.
- Prepared scenarios freeze resolved character and element sources before a
  run starts.
- Domain events describe authoritative transitions with structured payloads
  and causal identity.
- Perceptible facts remove private fields; perceived facts are delivered to one
  observer under deterministic modality rules.
- Telemetry is an omniscient operator projection.
- Dataset v5 is an immutable research observation with rebuildable relational
  projections, including normalized engagement, physical object, and relation
  samples.
- SQLite/JSONL datasets remain non-authoritative research observations.
- Application checkpoints separately persist strict, integrity-protected live
  state. Restoration rebuilds operational adapters through normal composition
  before applying whitelisted ECS, resource, event, RNG, and capture state.

Telemetry, raw research traces, model recordings, and UI form state must never
be used as character perception or character-controller context.

## Engagement compilation boundary

`engage` is the stable fallback when no offered specialized controller tool
accurately expresses an attempted behavior. Its complete controller schema is
`intent`, `reference_ids`, and optional private `reason`. Specialized tools
remain preferred when exact.

The application uses the same configured provider-neutral `ModelClient` for
controller decisions and a distinct `engagement_compilation` operation. The
compiler has its own prompt/version, model profile, timeout, concurrency, and
request/input/output budgets. It receives a frozen sanitized scene plus the
versioned capability catalog and must return exactly one strict
`compile_engagement` tool call with no prose. Live compiler choices are
stochastic inputs; recording/replay covers these requests separately by request
hash.

The initial capability catalog contains:

- `expressive_behavior`;
- `auditory_expression`;
- `bounded_activity`.

The compiler proposes groups and bounded bands; it is not domain authority.
Application validation rejects unknown references, arbitrary state paths,
unregistered capabilities, malformed groups, and invalid specialized-tool
fallbacks. Ordered domain handlers revalidate live ECS state before committing.
All invocations in one required-atomic group validate and commit together;
independent groups can fail separately, yielding an explicit partial result.
A compiler-selected capability or summary is never proof of a successful
effect.

Auditory recipients and listener stress effects are resolved from live hearing
range, place, footprints, and hearing obstruction. Perception and memory are
then derived per observer from committed evidence. Raw intent/reason, sanitized
compiler scene, compiler summary/response, rejected proposals, and normalized
private arguments remain private research; character and ordinary operator
views use grounded committed evidence.

## Physical world authority

Local physical execution uses a fixed integer metric of **9 microcells per
legacy cell**. Materialized room grids, physical anchors, movement paths, and
occupied cells are microcells. Compatibility positions and coarse authoring or
operator guides may remain legacy cells and are converted explicitly. A
standing character occupies a fixed 5×5-microcell body footprint.

An object footprint is a non-empty set of local microcell offsets. Its
cardinal `NORTH`, `EAST`, `SOUTH`, or `WEST` orientation rotates those offsets;
adding the pose anchor yields occupied cells. Movement and vision obstruction
are independent. Openable objects replace their closed effective obstruction
with non-blocking, transparent state while open and restore the configured
closed obstruction when closed.

Live parent relations are `ON_FLOOR`, `ON_SUPPORT`, `IN_CONTAINER`, `HELD_BY`,
`ATTACHED_TO`, or `OCCUPIES_SLOT`. Slots declare accepted relation kinds and
capacity. Custody and hand state describe current physical control. Ownership
is descriptive metadata, and abstract `PossessionsComponent` quantities remain
independent from embodied objects; none of these representations silently
implies another.

The `SpatialIndex` maintains room-scoped hard-movement and opaque-vision
occupancy in stable entity order. Its revision changes when indexed effective
topology changes; its topology revision separately tracks non-dynamic topology
changes. Domain systems alone decide collision, placement, movement,
interaction success, and door traversal. API snapshots, telemetry, datasets,
and SVG views are projections of that authority.

This is discrete footprint and interaction physics, not a claim of full
rigid-body simulation, continuous collision dynamics, mass/weight, torque, or
arbitrary rotation.

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

Authoritative in-world text is separate from character information. Mutable
artifacts, stable blocks, immutable revisions, collections, addresses, and
mailboxes live in a run-scoped domain registry. Physical or logical entities
expose explicit content endpoints; endpoint access is the intersection of a
typed operation grant and live physical or terminal access. A completed read
creates a private receipt pinned to one artifact revision and a derived
`world.text.read` information document in the reader's namespace. Later edits
never rewrite that historical knowledge. See [Text content and character
read/write actions](TEXT_CONTENT.md).

## Extension rules

- Components contain typed state and local invariants; systems orchestrate.
- Entity iteration, route ties, capacity conflicts, and commits use stable
  ordering.
- A new action requires deterministic preconditions, execution, events,
  persistence, and tests before it becomes a controller tool.
- A content mutation requires an expected revision, candidate-before-commit
  validation, atomic persistence, immutable authorship, and explicit public
  and private projections. Content prose, ownership, and custody never imply
  permission.
- A new engagement capability adds a strict application catalog descriptor and
  normalizer, a domain handler registered under the same name, persistence and
  privacy classification as needed, plus compiler-validation, domain,
  perception, replay, and dataset tests. It does not add fields to `engage`.
- A provider swap must not require domain changes.
- A new perceptible fact must start from authoritative evidence, assign a
  disclosure class, remove private fields, resolve recipients deterministically,
  and have a leakage test.
- Scenario and dataset contract changes require explicit schema-version review.
- Content contract changes follow the adjacent-transform and repository-check
  workflow in [Content migration](CONTENT_MIGRATION.md); runtime models remain
  current-version only.
- System-order changes are behavior changes; review same-tick transitions,
  perception, preemption, events, and datasets.

See [Runtime semantics](RUNTIME.md), [Actions, tools, and events](ACTIONS_AND_EVENTS.md),
and [Research data](DATA_COLLECTION.md).
