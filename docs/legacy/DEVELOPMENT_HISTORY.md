# Development History

This is the compressed rationale for Stage 0's major architectural eras.
Detailed plans and superseded requirements remain in Git history. Current
behavior is defined only by the [active documentation](../README.md).

## Deterministic ECS baseline — 2026-08-25 to 2026-08-27

- **Problem:** Establish reproducible physical and physiological ground truth
  before adding model-driven behavior.
- **Replacement:** Fixed-step Python ECS, stable system/entity ordering,
  grid/pathfinding, homeostasis, System 1, events, API/CLI, SQLite, and
  deterministic tests.
- **Why it remains:** Providers may propose decisions but cannot own physical
  outcomes or ordered domain execution.
- **Commits:** [`75e34bb`](https://github.com/machoo-cactus/society-simulation-prototype/commit/75e34bb)
  through [`013236d`](https://github.com/machoo-cactus/society-simulation-prototype/commit/013236d).
- **Current owners:** [Architecture](../ARCHITECTURE.md) and
  [Runtime](../RUNTIME.md).

## Typed tool-controller boundary — 2026-08-27 to 2026-08-30

- **Problem:** Planner lists and separately generated dialogue could not safely
  represent real provider decisions.
- **Replacement:** Provider-neutral model turns, strict tools, immutable
  intents, observer-specific context, deterministic validation/commit,
  OpenAI-compatible transport, recording, and replay.
- **Why it remains:** Model output is an auditable proposal, never proof of
  execution or success.
- **Commits:** [`d200413`](https://github.com/machoo-cactus/society-simulation-prototype/commit/d200413)
  through [`0068b44`](https://github.com/machoo-cactus/society-simulation-prototype/commit/0068b44).
- **Current owners:** [Character controller flow](../CHARACTER_AGENT_ACTIONS.md)
  and [Actions/events](../ACTIONS_AND_EVENTS.md).

## Browser reliability, then server-rendered UI — 2026-08-30 to 2026-08-31

- **Problem:** A monolithic JavaScript/WebSocket UI duplicated lifecycle state,
  lost recovery context, and could not provide a reliable no-JavaScript path.
- **Replacement:** Explicit recovery first, then Python-owned routes and state,
  Jinja HTML/SVG, ordinary forms/links, progressive fragments, and role-driven
  Playwright coverage.
- **Why it remains:** Browser state is presentation and transport; simulation
  authority stays in Python.
- **Commits:** [`f0f92c8`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f0f92c8)
  and [`1dd3297`](https://github.com/machoo-cactus/society-simulation-prototype/commit/1dd3297).
- **Current owners:** [API/UI](../API_AND_UI.md) and
  [UI testing](../UI_TESTING.md).

## Sparse city and unified navigation — 2026-08-30

- **Problem:** One dense city grid and separate local/travel tools did not scale
  to buildings, transport, or character-known topology.
- **Replacement:** Recursive spaces, sparse exterior graphs, room grids,
  locators/transitions, vehicles, one `navigate_to`/`NAVIGATE` boundary, and
  direct-experience route knowledge.
- **Why it remains:** Authoritative topology and character knowledge stay
  separate, while execution uses one deterministic navigation intention.
- **Commits:** [`26bd1a7`](https://github.com/machoo-cactus/society-simulation-prototype/commit/26bd1a7)
  and [`1473e0a`](https://github.com/machoo-cactus/society-simulation-prototype/commit/1473e0a).
- **Current owners:** [Architecture](../ARCHITECTURE.md),
  [Runtime](../RUNTIME.md), and the
  [information/navigation roadmap](../roadmaps/INFORMATION_AND_NAVIGATION.md).

## Character and reusable source libraries — 2026-08-30 to 2026-09-01

- **Problem:** Inline profiles and repeated scenario structures coupled stable
  identity, world archetypes, and temporary run state.
- **Replacement:** Structured `human-v1` dossiers, hash-protected character and
  element libraries, slot assignments, recursive element graphs, typed
  overrides, and preparation-time freezing.
- **Why it remains:** Authored sources are reusable and independently editable,
  but staged/running simulations use immutable resolved inputs.
- **Commits:** [`9992634`](https://github.com/machoo-cactus/society-simulation-prototype/commit/9992634),
  [`da5cda3`](https://github.com/machoo-cactus/society-simulation-prototype/commit/da5cda3),
  [`f3239aa`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f3239aa),
  and [`2d120e6`](https://github.com/machoo-cactus/society-simulation-prototype/commit/2d120e6).
- **Current owners:** [Character authoring](../CHARACTER_PROFILE_GUIDE.md) and
  [Scenario/element authoring](../SCENARIO_EDITOR_GUIDE.md).

## Global cognition barrier — 2026-08-30

- **Problem:** Cross-tick background provider completion made simulation time
  and stale decisions difficult to interpret.
- **Replacement:** Concurrent model requests inside one frozen batch, stable
  commit order, explicit timeout/cancellation, required tools, and `skip`.
- **Why it remains:** A completed tick means cognition scheduled by that pass
  has reached a terminal result; wall latency is not simulated duration.
- **Commit:** [`95b5512`](https://github.com/machoo-cactus/society-simulation-prototype/commit/95b5512).
- **Current owner:** [Runtime](../RUNTIME.md).

## Research data and operator management — 2026-09-01

- **Problem:** Event logs alone could not support privacy-aware analysis,
  normalized lifecycle joins, cross-run aggregation, or guarded deletion.
- **Replacement:** Immutable canonical records, rebuildable projections,
  private-data classifications, data exploration/export, ownership
  reconciliation, aggregate statistics, and explicit deletion controls.
- **Why it remains:** Research data observes execution but is neither a
  character perception source nor a live checkpoint.
- **Commit:** [`5f99519`](https://github.com/machoo-cactus/society-simulation-prototype/commit/5f99519).
- **Current owner:** [Research data](../DATA_COLLECTION.md).

## Current-only cleanup — 2026-09-01

- **Problem:** Compatibility generations remained across cognition, schemas,
  actions/events, databases, routes, tests, catalogs, and documentation.
- **Replacement:** Current-only runtime loading, fresh-only SQLite, canonical
  tools/routes/events, grouped tests, packaged resources, Windows evidence, and
  a separated legacy history.
- **Why it remains:** Rapid development should change the active contract
  directly rather than preserve unused runtime branches.
- **Commit:** [`30708f0`](https://github.com/machoo-cactus/society-simulation-prototype/commit/30708f0).
- **Exception:** Authored content keeps adjacent offline migrators and immutable
  legacy fixtures.

## Fine-grained objects, equipment, and senses — 2026-09-01 to 2026-09-02

- **Problem:** Point-sized characters and objects could not express furniture,
  doors, held items, posture, equipment, or structural sensing.
- **Replacement:** Nine microcells per legacy cell, 5x5 character bodies,
  cardinal footprints, live spatial indexes, slots/relations/hands/custody,
  deterministic interactions, semantic mass/dimensions, equipment effects, and
  modality-specific sensory sweeps.
- **Why it remains:** Physical ECS components and `SpatialIndex` are live truth;
  semantic descriptions never replace collision or interaction authority.
- **Commit:** [`1d7f1a5`](https://github.com/machoo-cactus/society-simulation-prototype/commit/1d7f1a5).
- **Current owners:** [Architecture](../ARCHITECTURE.md) and
  [Runtime](../RUNTIME.md).

## Generic engagement compiler — 2026-09-02 to 2026-09-03

- **Problem:** A closed specialized tool set could not cover creative or
  uncommon behavior without making narrative authoritative.
- **Replacement:** Stable specialized-first `engage`, a separately budgeted
  stochastic compiler, strict capability proposals, atomic groups, partial
  completion, domain handlers, grounded evidence, and replayable private
  compiler traces.
- **Why it remains:** Intent may be open-ended, but executable effects stay
  closed, typed, bounded, revalidated, and domain-owned.
- **Starting commit:** [`e8ac5c3`](https://github.com/machoo-cactus/society-simulation-prototype/commit/e8ac5c3).
- **Current owners:** [Architecture](../ARCHITECTURE.md),
  [Controller flow](../CHARACTER_AGENT_ACTIONS.md), and
  [Actions/events](../ACTIONS_AND_EVENTS.md).

## Revisioned in-world text — 2026-09-03

- **Problem:** Timed reading did not deliver text, and characters could not
  safely create notes, shared documents, posts, or in-world messages.
- **Replacement:** Domain-owned revisioned artifacts, stable blocks,
  collections, ACLs, attribution, content endpoints, embodied read/write
  actions, deterministic conflicts, private knowledge receipts, and mailbox
  delivery.
- **Why it remains:** Mutable world content, character knowledge, perception,
  and research traces are distinct representations with explicit access and
  privacy boundaries.
- **Current owners:** [Text content](../TEXT_CONTENT.md),
  [Architecture](../ARCHITECTURE.md), and
  [Research data](../DATA_COLLECTION.md).

## Agent-first workflow — 2026-09-03

- **Problem:** Session plans, repeated documentation edits, and local full-suite
  validation grew faster than the project and dominated coding-agent runtime.
- **Replacement:** Change classification, bounded analysis, compact plans,
  focused local checks, CI-owned broad regression, canonical volatile
  contracts, compressed history, and optional bounded live-model smoke tests.
- **Why it remains:** A solo coding-agent-first project needs rapid feedback
  without weakening deterministic behavior or release evidence.
- **Current owner:** [Development workflow](../DEVELOPMENT_WORKFLOW.md).
