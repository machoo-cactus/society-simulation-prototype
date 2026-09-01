# Development History

This chronology compresses the architectural history through version **0.2.0**.
Each era states what changed while leaving detailed historical records intact.
Commit subjects were terse; the linked records and code changes provide the
conceptual interpretation.

## 1. Deterministic ECS baseline — 2026-08-25 to 2026-08-27

- **Problem:** Establish a detailed ground-truth sandbox before any
  multi-fidelity approximation.
- **Previous approach:** Only the conceptual PRD existed; it assumed a simple
  grid, dual clocks, an LLM planner, Canvas telemetry, and preliminary meter
  semantics.
- **Replacement:** A fixed-step Python ECS, stable ordered systems, event bus,
  grid/A*, continuous homeostasis, System 1 state machine, affordances,
  macro-work isolation, API/CLI, SQLite, and deterministic tests.
- **Why:** Physical and physiological outcomes needed to be reproducible and
  independent of provider latency or prose.
- **Still valid:** Domain authority, fixed `dt`, stable iteration/ties,
  provider-free physical systems, explicit failures, and absolute System 1
  priority.
- **Implementation:** [`75e34bb`](https://github.com/machoo-cactus/society-simulation-prototype/commit/75e34bb)
  through [`013236d`](https://github.com/machoo-cactus/society-simulation-prototype/commit/013236d).
- **Current replacement:** [Architecture](../ARCHITECTURE.md) and
  [Runtime](../RUNTIME.md).
- **Detailed records:** [Starting PRD](requirements/starting_basic_PRD.md),
  [Implementation Plan](plans/IMPLEMENTATION_PLAN.md), and
  [Project State Assessment](assessments/PROJECT_STATE_ASSESSMENT.md).

## 2. Typed tool-controller boundary — 2026-08-27 to 2026-08-30

- **Problem:** Planner lists and separately generated dialogue could not safely
  represent real provider decisions or observer-specific interaction.
- **Previous approach:** Macro planner/dialogue protocols returned plans or
  text through separate paths, with broad application-created context.
- **Replacement:** Provider-neutral model turns, strict tool schemas, immutable
  intents, deterministic validation/commit, explicit `say`, perception and
  knowledge boundaries, OpenAI-compatible transport, scripted/replay clients,
  and causal tool/action records.
- **Why:** Model output needed one auditable boundary that could propose but
  never directly mutate ECS state or declare success.
- **Still valid:** Exactly one final state-changing proposal, provider isolation,
  observer-specific context, recording/replay, and stale decision/state
  validation.
- **Implementation:** [`d200413`](https://github.com/machoo-cactus/society-simulation-prototype/commit/d200413),
  [`f86f236`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f86f236),
  and [`0068b44`](https://github.com/machoo-cactus/society-simulation-prototype/commit/0068b44).
- **Current replacement:** [Actions, Tools, and Events](../ACTIONS_AND_EVENTS.md)
  and [Runtime](../RUNTIME.md).
- **Detailed records:** [Real LLM Tool-Agent Plan](plans/REAL_LLM_TOOL_AGENT_PLAN.md)
  and [Character Agent/UI Rework Plan](plans/CHARACTER_AGENT_AND_UI_REWORK_PLAN.md).

## 3. First browser reliability rework — 2026-08-30

- **Problem:** A monolithic JavaScript UI inferred lifecycle state from strings,
  disabled valid controls, lost events across reconnects, and mixed snapshots
  with durable history.
- **Previous approach:** One mutable `app.js`, a shared WebSocket replay deque,
  and full high-frequency snapshots.
- **Replacement:** Explicit client states, side-effect-free bootstrap,
  independent telemetry/event/snapshot cursors, ES modules, recovery, and
  clearer sensing/dialogue views.
- **Why:** The operator console needed reliable control and history before
  displaying larger worlds and richer character state.
- **Still valid:** Loading/staging is separate from starting; observation must
  not mutate shared streams; full event envelopes and explicit recovery matter.
- **Implementation:** [`f0f92c8`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f0f92c8).
- **Current replacement:** [API and UI Workflows](../API_AND_UI.md) and
  [UI Architecture and Testing](../UI_TESTING.md).
- **Detailed record:** [UI Reliability and Readability Plan](plans/UI_ARCHITECTURE_RELIABILITY_AND_READABILITY_PLAN.md).

## 4. Sparse city and transport — 2026-08-30

- **Problem:** A building-sized dense grid could not represent explicit
  cross-building or multimodal city travel efficiently.
- **Previous approach:** Every character occupied one coordinate in one
  `WorldMap`; movement targeted local zones/stations only.
- **Replacement:** Hierarchical locations, local room grids, building
  entrances, sparse exterior networks, route legs, vehicles, WALK/CYCLE/CAR/
  METRO modes, deterministic travel events, and scale-aware world projections.
- **Why:** City-sized dense grids waste storage/search and collapse incompatible
  interior, road, transit, and UI scales.
- **Still valid:** Sparse topology, explicit location throughout travel,
  deterministic route execution, and buildings as containers rather than
  magical actors.
- **Implementation:** [`26bd1a7`](https://github.com/machoo-cactus/society-simulation-prototype/commit/26bd1a7).
- **Current replacement:** [Architecture](../ARCHITECTURE.md),
  [Runtime](../RUNTIME.md), and [Scenario Authoring](../SCENARIO_EDITOR_GUIDE.md).
- **Detailed record:** [Large-Scale World and Transport Plan](plans/LARGE_SCALE_WORLD_AND_TRANSPORT_PLAN.md).

## 5. Profiles and character library — 2026-08-30 to 2026-08-31

- **Problem:** Shallow inline profiles were duplicated across scenarios,
  difficult to edit, and mixed enduring identity with temporary state.
- **Previous approach:** Scenario-owned profile catalogs, flat fields, and an
  embedded browser character editor.
- **Replacement:** Structured `human-v1` dossiers, separate prompt/profile/live
  layers, a hash-protected character library, slot assignments, preparation-
  time freezing, a dedicated character page, then schema-version-2 dates and
  expanded stable sections.
- **Why:** Reusable people needed independent lifecycle/provenance without
  allowing library edits to mutate staged or active runs.
- **Still valid:** Stable dossier versus scenario/live state, optimistic
  concurrency, source freezing, descriptive extensions, and no filesystem I/O
  in ordered systems.
- **Implementation:** [`9992634`](https://github.com/machoo-cactus/society-simulation-prototype/commit/9992634),
  [`da5cda3`](https://github.com/machoo-cactus/society-simulation-prototype/commit/da5cda3),
  and [`f3239aa`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f3239aa).
- **Current replacement:** [Character Authoring](../CHARACTER_PROFILE_GUIDE.md).
- **Detailed records:** [Character Agent/UI Rework Plan](plans/CHARACTER_AGENT_AND_UI_REWORK_PLAN.md)
  and [Character Library Separation Plan](plans/CHARACTER_LIBRARY_AND_EDITOR_SEPARATION_PLAN.md).

## 6. Global cognition barrier — 2026-08-30

- **Problem:** Cross-tick provider completion made cognition timing difficult to
  interpret and encouraged repeated text-only responses when no action was
  useful.
- **Previous approach:** Requests ran in background workers while later ticks
  advanced; late results were often rejected as stale.
- **Replacement:** One batch per tick, concurrent provider calls, frozen
  simulation time until all members settle, stable commit order, explicit
  barrier telemetry, required tool use, and `skip` with simulated-time cooldown.
- **Why:** A completed tick needed to mean that all cognition scheduled by that
  pass had reached a terminal outcome.
- **Still valid:** Wall latency is not simulated duration; timeout and logical
  cancellation prevent deadlock; correctness still checks decision IDs and
  state revisions.
- **Implementation:** [`95b5512`](https://github.com/machoo-cactus/society-simulation-prototype/commit/95b5512)
  and surrounding tool-controller work.
- **Current replacement:** [Runtime](../RUNTIME.md).
- **Detailed record:** [Global Cognition Barrier and Skip Plan](plans/GLOBAL_COGNITION_BARRIER_AND_SKIP_TOOL_PLAN.md).

## 7. Unified information and navigation — 2026-08-30

- **Problem:** Prompt-oriented profile/memory fragments and separate local/city
  navigation paths would not scale to rich characters or large worlds.
- **Previous approach:** Dedicated context fragments, fixed spatial levels,
  local movement tools, and separate travel tools.
- **Replacement:** Coherent versioned information documents with derived
  indexes, bounded retrieval, authoritative versus known topology, recursive
  spaces/locators/transitions, one navigation intention, and direct-experience
  route knowledge.
- **Why:** Authored information must stay coherent, retrieval indexes must be
  disposable, and characters must not receive omniscient topology.
- **Still valid:** Information is not permission; truth/claim/memory provenance
  stays distinct; physical execution validates known plans against
  authoritative topology.
- **Implementation:** [`1473e0a`](https://github.com/machoo-cactus/society-simulation-prototype/commit/1473e0a).
- **Current replacement:** [Architecture](../ARCHITECTURE.md) and the
  [active information/navigation roadmap](../roadmaps/INFORMATION_AND_NAVIGATION.md).
- **Detailed records:** [Information and Navigation Plan](plans/INFORMATION_AND_NAVIGATION_PLAN.md)
  and [non-authoritative source prompt](prompts/large-scale-context-notes.txt).

## 8. Server-rendered UI cutover — 2026-08-31

- **Problem:** Even a modular client application duplicated authoritative
  lifecycle/telemetry state and made no-JavaScript operation difficult.
- **Previous approach:** ES-module state, WebSocket-driven rendering, and
  browser-owned recovery/projection logic.
- **Replacement:** Python routes, Jinja HTML/SVG, ordinary forms/links,
  server-owned drafts/sessions, named fragment refresh, preserved browser
  interaction state, and role-driven Playwright tests.
- **Why:** Operator controls needed direct correspondence to application
  services, accessible fallback behavior, and less duplicated state.
- **Still valid:** Progressive enhancement only, authoritative server renders,
  explicit lifecycle operations, stable fragments, and behavioral browser
  testing.
- **Implementation:** [`1dd3297`](https://github.com/machoo-cactus/society-simulation-prototype/commit/1dd3297).
- **Current replacement:** [API/UI](../API_AND_UI.md) and
  [UI Testing](../UI_TESTING.md).
- **Detailed record:** [UI Reliability and Readability Plan](plans/UI_ARCHITECTURE_RELIABILITY_AND_READABILITY_PLAN.md)
  preserves the superseded client architecture.

## 9. Source libraries and environment — 2026-08-31

- **Problem:** Scenario/character content, runtime-writable data, and world
  construction were coupled; environmental state was mostly static.
- **Previous approach:** Root-level catalogs, embedded/repeated definitions,
  fixed profile age/height fields, and no unified time/weather/availability
  projection.
- **Replacement:** Separate scenario, character, and element libraries;
  hash-pinned element graphs; explicit preparation; structured editor;
  calendar/weather/surface/availability systems; bounded environment
  information; tracked samples later moved under `examples\`.
- **Why:** Sources needed independent CRUD, reproducible freezing, reusable
  buildings/rooms/objects/NPC roles, and deterministic environmental effects.
- **Still valid:** Resolve before execution, preserve hashes/provenance, keep
  credentials out of scenarios, and route environment knowledge through
  perception/access policy.
- **Implementation:** [`f3239aa`](https://github.com/machoo-cactus/society-simulation-prototype/commit/f3239aa)
  and [`2d120e6`](https://github.com/machoo-cactus/society-simulation-prototype/commit/2d120e6).
- **Current replacement:** [Configuration](../CONFIGURATION.md),
  [Scenario/Element Authoring](../SCENARIO_EDITOR_GUIDE.md), and
  [Character Authoring](../CHARACTER_PROFILE_GUIDE.md).
- **Detailed record:** [Character Library Separation Plan](plans/CHARACTER_LIBRARY_AND_EDITOR_SEPARATION_PLAN.md).

## 10. Research data and operator expansion — 2026-09-01

- **Problem:** Event logs alone could not support lifecycle joins, privacy-aware
  research, cross-run analysis, or safe dataset operations.
- **Previous approach:** Earlier dataset envelopes and SQLite projections with
  less complete lineage, private trace, interaction, goal, and management
  coverage.
- **Replacement:** Exhaustive phase capture, typed lineage, structured goals,
  normalized/derived projections, explorer filters, private opt-in, analysis
  bundles, ownership leases, reconciliation, aggregate statistics, and guarded
  atomic deletion.
- **Why:** Research needed analyzable ground truth and explicit privacy/data
  management without becoming simulation authority.
- **Still valid:** Raw immutable records are truth; projections are rebuildable;
  complete exports are sensitive; datasets are not checkpoints.
- **Implementation:** [`5f99519`](https://github.com/machoo-cactus/society-simulation-prototype/commit/5f99519).
- **Current replacement:** [Research Data](../DATA_COLLECTION.md).
- **Detailed context:** [Project State Assessment](assessments/PROJECT_STATE_ASSESSMENT.md)
  shows the much smaller earlier persistence boundary.

## 11. Current-only cleanup cutover — 2026-09-01, version 0.2.0

- **Problem:** Multiple compatibility generations remained in schemas,
  cognition modes, tools/actions/events, databases, routes, catalogs, tests,
  package resources, and documentation.
- **Previous approach:** Scenario v3 plus compatibility parsing, character v1,
  background cognition, multiple navigation/action names, dataset v2,
  migratable SQLite, route aliases, root catalogs, and mixed-era docs.
- **Replacement:** Tool-controller/global-barrier only; scenario v4; character
  v2; structured goals; `navigate_to`/`NAVIGATE`; `action.*`; dataset v3;
  fresh-only SQLite schema 8; canonical routes; separated package resources and
  application ports; `examples\` catalogs; grouped tests; Windows CI; concise
  current documentation and indexed legacy provenance.
- **Why:** Removed behavior was no longer a supported product contract and made
  every change carry migration ambiguity.
- **Still valid:** All authority, determinism, privacy, source-freezing,
  accessibility, and research-capture principles established in prior eras.
- **Implementation:** Current working-tree cleanup following
  [`5f99519`](https://github.com/machoo-cactus/society-simulation-prototype/commit/5f99519);
  package version `0.2.0`.
- **Current replacement:** [Documentation map](../README.md) and
  [Status and Roadmap](../STATUS_AND_ROADMAP.md).
- **Detailed records:** This archive preserves the replaced designs; the
  cleanup plan itself remains in session provenance rather than being promoted
  to an active architecture document.
