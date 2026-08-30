# Stage 0 Simulation Concept Guide

**Audience:** Developers, architects, reviewers, and coding agents  
**Purpose:** Provide one definitive mental model of the project's goals,
vocabulary, current implementation, planned direction, and engineering rules  
**Status date:** 2026-08-27

## 1. How to use this guide

This document is the conceptual entry point for advanced development. Read it
before changing simulation behavior, cognition, memory, perception, telemetry,
or persistence.

The README explains how to install, run, and experiment with the project. This
guide explains:

- what the project is trying to prove;
- what each important term means;
- which behavior exists now and which behavior is planned;
- where authority belongs;
- how data moves through the simulation;
- which invariants new code must preserve;
- where new functionality should be attached.

Status labels have precise meanings:

- **Implemented:** Present in the current source and exercised by tests.
- **Partial:** A useful implementation exists, but the full intended abstraction
  is not complete.
- **Planned:** Designed but not yet implemented.
- **Out of scope:** Deliberately excluded from Stage 0.

When this guide and the implementation differ, source code and tests define
current behavior. The discrepancy should then be corrected in this guide rather
than silently accepted.

## 2. Project in one paragraph

Stage 0 is a deterministic, fully materialized simulation of people acting in
local grid worlds and sparse hierarchical cities. Every simulated person has
continuous physiological state, explicit location, deterministic movement and
travel, physical affordances, situated perception, episodic memory, and
non-negotiable survival behavior. Slow cognition is separated from physical
simulation. Characters may use scripted, replayed, fake-API, or real
OpenAI-compatible tool controllers while the simulation remains the only
authority over outcomes.

## 3. Why Stage 0 exists

The broader research direction is multi-fidelity simulation: use detailed
execution only where necessary and cheaper approximations elsewhere. That later
architecture needs a trustworthy detailed baseline.

Stage 0 therefore establishes the **ground-truth execution layer**:

- all characters exist continuously;
- all positions and physiological values are explicit;
- every physical transition occurs through simulation rules;
- all intentional actions are represented as constrained commands;
- complete trajectories and events can be recorded;
- no state is statistically fabricated or retrospectively invented.

Stage 0 data may later be used to estimate transition models, calibrate
statistical populations, and compare approximate simulations against detailed
execution. Stage 0 itself does not perform those approximations.

## 4. Primary goals

### 4.1 Deterministic embodied behavior

Characters should occupy a real location, move along valid paths, compete for
space and resources, and perform activities with explicit duration and effects.
The simulation, not prose, decides whether an action succeeds.

### 4.2 Continuous homeostasis

Satiety, energy, and stress change over simulated time. Physical and
physiological updates require no LLM call.

### 4.3 Absolute survival priority

Critical physiological needs preempt ordinary plans. A model cannot persuade,
prompt, or command a critically hungry character to continue working.

### 4.4 Separated cognition

Potentially slow cognition runs outside the ordered physical system pass. Model
latency must not become part of physical rules.

### 4.5 Situated knowledge

Characters know the world through their own state, observations,
communication, initialized common knowledge, and memory—not through omniscient
simulation internals.

### 4.6 Reproducible research data

Runs should produce versioned, analyzable records independent of the browser
interface.

## 5. Explicit non-goals

The following are not part of the current Stage 0 target:

- statistically represented background populations;
- just-in-time creation of previously nonexistent character histories;
- collapsing detailed characters into probability distributions;
- distributed cluster execution;
- natural-language interpretation of physical outcomes;
- allowing an LLM to edit arbitrary ECS state;
- exposing shell, filesystem, database, or unrestricted network tools to a
  character controller;
- treating human-facing telemetry as character perception;
- allowing narrated claims to become simulation facts.

## 6. Canonical terminology

These definitions should be used consistently in code, documentation, prompts,
events, and discussion.

### 6.1 Character

A **character** is a simulated person within the world.

A character may have:

- a position;
- physiological state;
- an activity;
- movement state;
- a plan;
- survival-drive state;
- episodic memory;
- conversation state;
- a structured profile, senses, knowledge, and an optional LLM controller.

Character profiles are now reusable, structured scenario records. The built-in
`human-v1` template covers identity, appearance, personality, background,
motivations, capabilities, preferences, relationships, and ordered custom
experimental sections. Reusable profiles live in independent character JSON
files; browser operators may assign library characters to each scenario entity
slot before starting a run.

The code currently uses ECS entity IDs such as `agent-001`. The conceptual term
for a simulated person is **character**, not "LLM agent." This distinction avoids
confusing the simulated person with the software controlling it.

### 6.2 Character controller agent

A **character controller agent** is the planned LLM-based software agent that
selects intentional actions for one character.

It:

- receives a bounded representation of the character's self-state, perception,
  knowledge, memory, and goals;
- chooses from registered tools;
- proposes an action or utterance;
- has no direct authority over world state.

It does **not** literally become the character or freely roleplay all outcomes.
It acts as the character's executive controller.

### 6.3 Character controller

A **character controller** is the provider-neutral application interface for
selecting a character decision. A character controller may be implemented by:

- a real LLM controller agent;
- a deterministic scripted controller;
- a replay controller;
- a test fake.

The term describes the role, not the technology.

### 6.4 Model client

A **model client** is an adapter that communicates with a particular model API
or local inference server. It converts provider-neutral requests into
provider-specific calls and normalizes responses.

Model clients do not contain world rules or mutate characters.

### 6.5 Narrator

A **narrator** is an optional planned translation component that rewrites already
filtered perceptual facts into natural language.

A narrator:

- does not decide who perceived an event;
- does not receive private or omniscient state;
- does not create authoritative facts;
- does not authorize tools;
- is not required for correct sensing.

The canonical first renderer is deterministic. An LLM narrator is an optional
surface-realization adapter.

### 6.6 Operator

An **operator** is a human using the browser, API, CLI, or datasets. Operators may
have an omniscient debugging view. Operator visibility must not be confused with
character visibility.

### 6.7 World

The **world** is the authoritative spatial and object state for a run. It
contains:

- grid dimensions;
- walkable and blocked tiles;
- zones;
- affordance stations;
- character entities;
- simulation time and tick.

### 6.8 Entity

An **entity** is an ECS identity. Characters are entities, but future objects may
also be entities. Therefore, "entity exists" does not automatically mean "valid
character target."

### 6.9 Component

A **component** is typed state attached to an entity, such as position,
homeostasis, movement, or plan state. Components hold data and small local
invariants; they should not orchestrate the simulation.

### 6.10 System

A **system** is deterministic logic that processes relevant entities during an
ordered micro-tick. Systems may update domain state and emit events.

System ordering is part of simulation semantics and must be explicit.

### 6.11 Zone

A **zone** is a named set of grid tiles with a functional meaning, such as an
Office, Kitchen, Bedroom, or Lounge.

Zones provide spatial context. Entering a zone does not imply a private
intention. A character entering a Bedroom is observed entering the Bedroom; an
observer must not automatically infer that the character plans to sleep.

### 6.12 Affordance station

An **affordance station** is a world object at a fixed grid position that exposes
one or more deterministic actions.

Examples:

- fridge -> `EAT`;
- bed -> `SLEEP`;
- sofa -> `RELAX`;
- desk -> `WORK`.

Stations define availability, capacity, action duration, and deterministic
effects.

### 6.13 Affordance

An **affordance** is an action the world permits under explicit preconditions.
It is a domain capability, not a natural-language suggestion.

### 6.14 Activity

An **activity** is what a character is physically doing during simulated time,
for example `IDLE`, `WALKING`, `WORKING`, `EATING`, `SLEEPING`, or `RELAXING`.

Activity affects homeostatic rates.

### 6.15 Plan

A **plan** is the character's current and queued sequence of intentional
`PlanAction` values.

Plans are private character state. They are visible to the operator but must not
be exposed automatically to other characters.

### 6.16 Plan action

A **plan action** is a validated command from the closed domain vocabulary:

- `MOVE_TO`
- `WORK`
- `SOCIALIZE`
- `READ`
- `EAT`
- `SLEEP`
- `RELAX`
- `IDLE`
- `TRAVEL_TO`

The tool-agent design translates tool calls into these actions or typed domain
intents.

### 6.17 Tool

A **tool** is a typed capability offered to a character controller.

Initial tools:

- `go_to`
- `travel_to`
- `perform`
- `say`
- `wait`

A tool call expresses what the controller wants the character to attempt. It
does not prove that the action happened.

### 6.18 Intent

An **intent** is an immutable, validated application-to-domain request derived
from a tool call.

An intent is the boundary between nondeterministic cognition and deterministic
simulation execution. Provider code proposes; domain code disposes.

### 6.19 Speech

**Speech** is an in-world utterance produced by a character. Literal
tool-controller speech is emitted only through `say`.

The controller's rationale, prompt, and hidden reasoning are not speech.

### 6.20 Dialogue

**Dialogue** is a sequence of delivered speech interactions between characters.
The legacy generated-dialogue path remains associated with `SOCIALIZE`.
Tool-controller utterances are explicit `say` actions resolved by the speech
and hearing systems.

### 6.21 Observation

An **observation** is information delivered to a character because that
character sensed it or because it describes the character's own state.

An observation is observer-specific. It is not a world snapshot.

### 6.22 Perceptible fact

A **perceptible fact** is a typed, privacy-safe statement that could be sensed,
such as:

- a visible character moved;
- a character entered or left a zone;
- a visible activity began;
- an utterance originated nearby.

Perceptible facts are derived from authoritative events while excluding private
fields.

### 6.23 Perceived fact

A **perceived fact** is a perceptible fact that passed one observer's modality
rules, such as visual line-of-sight or auditory range.

### 6.24 Perception packet

A **perception packet** is a bounded, ordered set of perceived facts for one
character over a defined tick interval.

It may contain deterministic wording and optional narrator wording, but the
structured facts remain authoritative for controller context and tool policy.

### 6.25 Knowledge

**Knowledge** is what a character currently believes based on self-state,
perception, communication, memory, and initialized common knowledge.

Knowledge may be stale. A last-seen location must retain when it was observed.
Knowledge must never update silently from omniscient world state.

### 6.26 Memory

**Memory** is a durable episodic record associated with a character. A memory
contains raw text, simulation time, importance, metadata, and an embedding.

Memory is not automatically true current world state. It represents a recorded
episode or observation.

### 6.27 Telemetry

**Telemetry** is operator-facing realtime data used for visualization,
inspection, and debugging.

Telemetry may be omniscient. It must not be used as character perception.

### 6.28 Domain event

A **domain event** is a structured record that an authoritative state transition
or lifecycle transition occurred.

Each event includes run identity, event identity, simulation tick/time,
wall-clock time, event type, payload, and optional causation/correlation IDs.

### 6.29 Canonical event

A **canonical event** is the deterministic form of a domain event with
run-specific and wall-clock identity removed where necessary. Canonical events
can be compared between equivalent runs.

### 6.30 Dataset record

A **dataset record** is a versioned persisted research record derived from a run.
Dataset records include events, state vectors, trajectories, activity intervals,
plan transitions, affordances, dialogue, memory references, and model metadata.

### 6.31 Scenario

A **scenario** is a validated JSON definition used to construct a run. It
specifies initial world, characters, coefficients, thresholds, memory settings,
and other configuration.

Provider credentials never belong in scenarios.

### 6.32 Run

A **run** is one instantiated simulation with its own run ID, seed, clock,
registry, events, and dataset.

API-managed runner objects currently exist only in process memory. Persisted
datasets do not constitute a resumable simulation checkpoint.

## 7. Clock model

The project separates four notions of time.

### 7.1 Simulation time

**Simulation time** is authoritative in-world time. It advances by fixed `dt`
increments.

### 7.2 Micro-clock

The **micro-clock** is the fixed-step execution rhythm for:

- physiology;
- movement;
- arbitration;
- plan progression;
- affordance execution;
- event production.

Default `dt` is one simulated second.

### 7.3 Macro boundary

The **macro boundary** is the application-controlled point outside the ordered
system pass where cognition, embeddings, and later real model work are handled.

Legacy synchronous providers and asynchronous real providers are both isolated
behind post-tick coordinators. Completed controller results are committed in
stable order at deterministic boundaries.

### 7.4 Telemetry clock

The **telemetry clock** publishes snapshots at approximately 10 Hz wall time. It
reads authoritative state and never advances simulation time.

### 7.5 Wall-clock time

**Wall-clock time** is real elapsed time used for pacing, request latency,
timestamps, and operator experience. It must not alter deterministic physical
outcomes.

## 8. Authority model

The most important architectural rule is:

> Only deterministic domain systems decide what physically happens.

Authority is divided as follows:

| Layer | May do | Must not do |
| --- | --- | --- |
| Domain systems | Mutate authoritative simulation state under explicit rules | Call model providers or inspect prompts |
| Application orchestration | Schedule work, project context, validate and commit intents | Invent physical outcomes |
| Character controller | Select a tool and arguments | Set ECS components directly |
| Model client | Send/normalize provider requests | Apply world rules |
| Perception layer | Decide which public facts an observer senses | Reveal private source fields |
| Narrator | Rephrase filtered facts | Create authoritative facts or permissions |
| Telemetry | Expose state to operators | Feed omniscient state to characters |
| Persistence | Store versioned records | Become the source of live state without explicit rehydration |

## 9. Deterministic simulation flow

Current high-level tick behavior:

```text
advance fixed clock
  -> start/progress current plan execution
  -> pathfind
  -> apply movement-related activity state
  -> integrate homeostasis
  -> progress timed plan actions
  -> arbitrate System 1
  -> execute affordances
  -> move characters
  -> queue memory work
  -> queue dialogue work
  -> queue planning work
  -> emit simulation.tick
  -> drain safe macro work after the system pass
  -> persist post-cognition boundary
```

The exact system order is code-level semantics. Do not reorder systems based only
on aesthetic preference.

Implemented sensing and cognition insertion:

```text
physical/action state transitions
  -> project privacy-safe perceptible facts
  -> resolve vision and hearing per observer
  -> update perception inbox and timestamped knowledge
  -> capture cognition eligibility and controller context
  -> run providers outside the ordered system pass
```

## 10. Homeostasis

### 10.1 State semantics

Meters are normalized to `[0, 100]`.

| Meter | Healthy direction | Critical condition | Corrective action |
| --- | --- | --- | --- |
| Satiety | Higher is healthier | `satiety <= threshold` | `EAT` |
| Energy | Higher is healthier | `energy <= threshold` | `SLEEP` |
| Stress | Lower is healthier | `stress >= threshold` | `RELAX` |

The UI may describe low satiety as hunger, but internal code uses `satiety` to
avoid contradictory operations such as eating increasing a "hunger" value.

### 10.2 Integration

Each activity has configurable per-second rates. Fixed-step integration updates
and clamps every meter. An affordance may temporarily replace normal activity
integration to avoid double-counting its effects.

### 10.3 No-model invariant

Homeostatic integration never requires an LLM or embedding call.

## 11. System 1 and System 2

### 11.1 System 1

**System 1** is deterministic survival arbitration, not an LLM.

When a meter becomes critical, System 1:

1. records the threshold breach;
2. clears the current ordinary plan and path;
3. cancels incompatible activity or cognition;
4. selects the most severe critical drive with deterministic tie-breaking;
5. selects the nearest available corrective station by reachable path cost and
   capacity;
6. locks behavior to the corrective workflow;
7. remains active until the recovery threshold is crossed.

Hysteresis prevents repeated activation near a threshold.

Possible state progression:

```text
NORMAL
  -> CRITICAL_DETECTED
  -> PREEMPTING
  -> NAVIGATING_TO_CORRECTION
  -> EXECUTING_CORRECTION
  -> RECOVERED
  -> NORMAL
```

If correction is impossible, the character enters observable
`BLOCKED_SURVIVAL`.

### 11.2 System 2

**System 2** is deliberative intentional control when survival is not dominant.

Current implementation:

- deterministic fake/scripted legacy planner and dialogue;
- unified tool-calling character controller;
- validated plan and intent vocabularies;
- explicit `say` speech and `travel_to` city travel;
- observer-specific perceptual context;
- asynchronous OpenAI-compatible model clients;
- provider recording and deterministic replay;
- memory retrieval outside the ordered micro-system stack.

## 12. Movement and spatial grounding

Local maps are rectangular discrete grids where a character occupies one tile.

Schema-version-2 city scenarios add an explicit hierarchy above local grids:

```text
city -> district -> building -> local map -> zone -> tile/station
```

Exterior movement uses sparse typed transport nodes and edges rather than a
city-sized dense grid. `SpatialLocationComponent` remains authoritative across
building, neighborhood, and city scales; `TravelComponent` records route legs,
mode, progress, vehicle, and interruption state.

Movement uses deterministic A* with:

- Manhattan-style grid costs;
- stable neighbor and tie ordering;
- blocked-tile handling;
- current occupancy;
- stable character-ID conflict ordering;
- path invalidation and retry events;
- explicit no-path failure.

An accepted movement request means movement was queued. It does not guarantee
arrival because occupancy or world state may change.

Other characters may later perceive visible movement, but they must not receive
the movement destination automatically.

## 13. Affordance execution

Affordances are deterministic state machines with:

- required character position;
- station availability;
- station capacity;
- supported action;
- duration;
- explicit effects or final targets;
- progress, completion, failure, and cancellation events.

An LLM may request `perform(action="EAT")`, but it cannot specify
`satiety_delta=100`. Effects belong to scenario/world configuration.

## 14. Cognition isolation

Cognition has three separable concerns:

1. **Scheduling:** decide when a character needs deliberation.
2. **Inference:** ask a controller/model for a proposed choice.
3. **Commit:** validate current state and translate the result into an intent or
   plan.

These concerns must remain replaceable independently.

`MacroWorkCoordinator` provides the post-tick boundary for legacy planner,
dialogue, and memory calls. `AgentWorkCoordinator` uses bounded queues and
workers for tool-controller inference and applies completions in deterministic
order.

Correctness must not depend on successful provider cancellation. Every late
result is checked for:

- current decision ID;
- state revision;
- System 1 state;
- current action eligibility;
- current target/preconditions.

## 15. Character-controller model

The character controller receives:

- structured identity and stable profile;
- current self-state;
- currently perceived facts;
- timestamped knowledge;
- recent action outcomes;
- selected episodic memories;
- allowed tool definitions.

It is instructed:

> You are the executive controller for a simulated person. Choose the person's
> next intentional action. You are not the person, the simulation engine, or a
> narrator. Use one available tool. Do not claim the action happened.

The first version permits at most one state-changing tool call per cognition
opportunity.

### 15.1 Initial tools

| Tool | Meaning | Typical domain translation |
| --- | --- | --- |
| `go_to(target_id, reason)` | Attempt to move to a known target | `MOVE_TO` or movement intent |
| `travel_to(target_id, mode, reason)` | Travel to a known city place | `TRAVEL_TO` / travel intent |
| `perform(action, target_id, duration, reason)` | Attempt a supported activity | Timed action or affordance request |
| `say(target_id, text, reason)` | Speak exact in-world words | Speech intent |
| `wait(duration, reason)` | Intentionally remain idle | `IDLE` |

`reason` is private controller metadata. It is never automatically audible or
visible.

### 15.2 Tool call meaning

Tool lifecycle:

```text
proposed
  -> schema validated
  -> authorized
  -> current-state validated
  -> accepted as intent
  -> committed
  -> domain execution
  -> completed / failed / interrupted
```

The model cannot collapse this lifecycle by saying an action succeeded.

## 16. Sensing model

The sensing architecture has three representations:

1. **Authoritative event/state:** complete truth, including private data.
2. **Perceptible fact:** privacy-safe evidence that could be sensed.
3. **Perceived fact:** evidence actually delivered to a particular observer.

### 16.1 Disclosure classes

Disclosure classes:

- `SELF`
- `DIRECT_PARTICIPANTS`
- `LOCAL_VISUAL`
- `LOCAL_AUDITORY`
- `PUBLIC_WORLD`
- `ADMIN_ONLY`

The safe default is `ADMIN_ONLY`.

### 16.2 Vision

Deterministic local vision uses:

- configurable range;
- grid line-of-sight;
- opaque blocked tiles;
- recognition range;
- event transitions plus periodic local scans;
- deterministic deduplication.

Example:

```text
Authoritative private fact:
  Alex selected go_to(home).

Visible execution:
  Alex crossed from an Office tile into a corridor tile.

Jordan's observation:
  Alex left the Office.

Not observable:
  Alex is going home.
```

### 16.3 Hearing

Initial deterministic hearing will use:

- source position;
- speech channel and range;
- shortest traversable-path distance;
- resolved recipients at emission time;
- recognition rules.

Ordinary speech may be overheard by unintended nearby characters. The intended
target is not guaranteed to hear it.

### 16.4 Knowledge

Knowledge changes only through:

- self-state;
- direct perception;
- explicit communication;
- initialized common knowledge.

Last-seen facts retain observation time and may become stale.

### 16.5 Narration

Deterministic templates are canonical. An optional LLM narrator may rephrase one
observer's already-filtered packet. It may not:

- see global private state;
- decide recipients;
- add authoritative entities or events;
- authorize tools;
- replace structured facts.

A global omniscient broadcaster is not part of the recommended initial design.

## 17. Memory model

Episodic memory combines:

- raw text;
- structured metadata;
- simulation timestamp;
- importance;
- embedding;
- character identity.

Retrieval score combines semantic similarity, temporal recency, and importance.
Ties are deterministic.

The active index is in memory. Episodes are also persisted in SQLite and can
rehydrate a fresh index.

Memory generation must respect perception. Once sensing exists, character A must
not form a memory from a private event belonging to character B merely because
the global event bus contained it.

Memory is not a full checkpoint. Rehydrating memories does not restore clock,
position, plan, RNG, queues, or live execution.

## 18. Events, causality, and observability

Events serve several audiences:

- domain coordination;
- debugging;
- telemetry;
- memory episode generation;
- dataset collection;
- future perception projection.

Every event should be precise about:

- what happened;
- which character/entity it concerns;
- simulation tick/time;
- causation;
- correlation;
- structured payload.

Avoid event text as the only representation of meaning.

Event visibility is not implicit. A global event can remain admin-only while a
separate privacy-safe perceptible fact is routed to local observers.

## 19. Telemetry

Telemetry is a realtime operator projection:

- world grid and zones;
- stations;
- character positions and paths;
- homeostatic values;
- System 1 state;
- plans and activities;
- conversation summaries;
- event stream;
- simulation status and sequence number.

The browser transport uses separate cursors for durable telemetry messages,
domain events, and replaceable runtime snapshots. Static world and character
profile bootstrap data is sent separately from high-frequency state. A client
whose replay cursor expires must recover the latest snapshot and backfill domain
events before resuming live display.

The browser does not own simulation behavior.

Telemetry snapshots may reveal more than any character could know. Never pass a
telemetry snapshot directly into an LLM character controller.

## 20. Persistence and datasets

SQLite stores:

- run manifest and seed;
- scenario;
- canonical records;
- state vectors;
- trajectories;
- activity intervals;
- threshold crossings;
- plans and affordances;
- dialogue and memory references;
- provider usage metadata;
- durable episodic memories.

JSONL export begins with a run manifest followed by ordered versioned records.

Telemetry sampling is separate from canonical logging. Increasing browser update
frequency must not change research records.

## 21. Determinism model

### 21.1 Deterministic inputs

- scenario;
- seed;
- fixed `dt`;
- ordered systems;
- stable entity iteration;
- deterministic pathfinding;
- scripted/replayed cognition results.

### 21.2 Nondeterministic inputs

- wall-clock timestamps;
- real model output;
- model latency;
- external service failures;
- operator interventions.

Nondeterministic inputs are captured as explicit records and applied at
deterministic boundaries. A live model run may not reproduce model text from the
seed alone; recording and replay are required for behavioral reproduction.

### 21.3 Canonical comparison

Canonical logs omit run IDs and wall-clock identity where necessary. Provider
record/replay will make accepted tool calls reproducible independently of a live
provider.

## 22. Failure philosophy

Failures must be explicit and observable.

Examples:

- no path -> `path.failed`;
- changed occupancy -> `path.invalidated`;
- station unavailable -> affordance failure;
- impossible survival correction -> `system1.blocked`;
- invalid model output -> tool or cognition rejection;
- provider timeout -> cognition failure;
- narrator failure -> narration failure plus deterministic rendering;
- memory embedding failure -> memory failure.

Do not:

- silently treat failure as success;
- let a provider exception stop physical ticks;
- invent a success-shaped fallback;
- retry indefinitely;
- ask an LLM to reinterpret a deterministic precondition failure.

## 23. Modularity and replacement points

The design expects large future changes. Keep these boundaries narrow:

| Concern | Replaceable interface |
| --- | --- |
| Model vendor/API | `ModelClient` |
| Character decision strategy | `CharacterController` |
| Tool catalog | `ToolRegistry` / `CharacterTool` |
| Prompt strategy | Prompt builder/version |
| Observation projection | Context builder |
| Vision/hearing rules | Modality resolvers |
| Perception wording | `PerceptionRenderer` |
| Optional prose narration | `NarratorClient` |
| Embedding model | `EmbeddingProvider` |
| Active memory index | Memory store/retriever |
| Dataset storage | Persistence adapter |
| Deterministic testing | Scripted/replay adapters |

Do not make a provider swap require changes to domain systems.

## 24. Current implementation status

| Capability | Status | Notes |
| --- | --- | --- |
| Fixed-step deterministic runner | Implemented | Pause, resume, step, speed, realtime pacing |
| ECS registry and ordered systems | Implemented | Stable entity and system ordering |
| Grid, zones, stations, A* | Implemented | Occupancy-aware movement and retries |
| Sparse city hierarchy and transport | Implemented, initial | Buildings, local maps, WALK/CYCLE/CAR, direct METRO edges |
| Homeostatic integration | Implemented | Satiety, energy, stress |
| System 1 preemption/recovery | Implemented | Three drives, hysteresis, blocked state |
| Deterministic affordances | Implemented | Duration, effects, capacity, failures |
| System 2 plans | Implemented | Closed action vocabulary |
| Fake/scripted planning | Implemented | No external model required |
| Generated social dialogue | Implemented, transitional | Separate dialogue generator after social action |
| Episodic memory/retrieval | Implemented | Durable SQLite episodes and in-memory retrieval |
| Telemetry API/WebSocket/UI | Implemented | Operator-facing, omniscient |
| Ground-truth dataset export | Implemented | SQLite and JSONL |
| Real LLM model client | Implemented, opt-in | OpenAI-compatible async HTTP adapter |
| Typed controller tools | Implemented | `go_to`, `perform`, `say`, `wait` |
| Observer-specific sensing | Implemented | Vision, hearing, inbox, timestamped knowledge |
| Optional narrator | Planned, optional | Non-authoritative translation only |
| Async model worker pool | Implemented | Bounded concurrency and deterministic commit |
| Model response recording/replay | Implemented | Sanitized JSONL recording and deterministic replay |
| Live-run checkpoint/resume | Out of current scope | Dataset persistence is not a checkpoint |

## 25. Source map

```text
src/stage0_sim/
  domain/
    components/       Typed ECS state
    systems/          Ordered deterministic behavior
    world/            Grid, zones, stations, pathfinding
                      plus sparse city/transport graph contracts
    events.py         Domain event envelope and bus
    ecs.py            Entity/component/resource registry

  application/
    runner.py         Clock, lifecycle, tick and macro boundary
    scenario.py       JSON validation and world construction
    planning.py       Current planning requests and validation
    dialogue.py       Current social dialogue scheduling
    macro_work.py     Post-tick provider/memory coordinator
    memory.py         Episodic indexing and retrieval
    collection.py     Canonical run record collection
    telemetry.py      Operator snapshot projection

  adapters/
    llm/              Current deterministic fake/scripted providers
    persistence/      SQLite storage

  api/                FastAPI composition and routes
  web/                Bundled operator browser UI

tests/                Behavioral and boundary tests
scenarios/            Runnable scenario definitions
docs/                 Requirements, plans, assessment, concept guide
```

Controller and perception additions belong in focused `application/agents` and
`application/perception` packages rather than expanding `macro_work.py` into a
monolithic cognition module. Browser API, protocol, UI-state, and transcript
logic remain separate native ES modules under `web/`.

## 26. Rules for extending the project

### 26.1 Adding a domain action

1. Define typed action/intent state.
2. Define deterministic preconditions and effects.
3. Implement execution in a domain system.
4. Emit structured lifecycle events.
5. Add scenario validation if configurable.
6. Add canonical dataset projection.
7. Add tests for success, failure, interruption, and determinism.
8. Only then expose it as an LLM tool.

### 26.2 Adding an LLM tool

1. Define a strict schema with extra fields rejected.
2. Define when the tool is offered.
3. Define authorization and current-state checks.
4. Return an intent; do not mutate the ECS from provider code.
5. Revalidate volatile conditions at commit.
6. Define stable rejection reasons.
7. Ensure System 1 can cancel or reject it.
8. Record proposal, acceptance/rejection, commit, and outcome.
9. Add scripted and replay tests before live-provider tests.

### 26.3 Adding a perceptible event

1. Identify the authoritative source event/state.
2. Assign disclosure class; default to admin-only.
3. Construct a minimal privacy-safe fact.
4. Define supported modalities.
5. Resolve recipients deterministically.
6. Update knowledge only for actual recipients.
7. Add deterministic rendering.
8. Test that private fields cannot leak.

### 26.4 Adding a provider

1. Implement the provider-neutral model/embedding/narrator port.
2. Keep provider SDK objects inside the adapter.
3. Normalize tool calls, usage, errors, IDs, and finish reasons.
4. Enforce timeout and cancellation.
5. Never persist credentials or authorization headers.
6. Pass common contract tests.
7. Make live tests opt-in.

### 26.5 Changing system order

Treat system-order changes as behavior changes. Document:

- old and new ordering;
- affected same-tick observations;
- preemption implications;
- event order changes;
- dataset/replay compatibility.

## 27. Common conceptual mistakes

### Mistake: Calling the character an LLM agent

Use **character** for the simulated person and **character controller agent** for
the LLM software controlling it.

### Mistake: Treating a model tool call as an outcome

It is only a proposed request. The world still resolves it.

### Mistake: Using telemetry as perception

Telemetry is an omniscient operator view. Character observation must pass through
the sensing boundary.

### Mistake: Publishing intentions as visible behavior

Other characters may observe movement or zone departure, not the private
destination or reason.

### Mistake: Asking a narrator what happened

The simulation determines what happened. A narrator may only phrase filtered
facts.

### Mistake: Using the LLM for survival arbitration

System 1 is deterministic and non-bypassable.

### Mistake: Putting provider logic in systems

Systems run on the micro-clock and must remain provider-free.

### Mistake: Assuming persisted datasets resume a run

Resumption would require a versioned checkpoint of clock, ECS, RNG, queues, and
pending work. That is not currently implemented.

## 28. Worked example

Consider Alex working in an Office while Jordan is nearby.

### 28.1 Private cognition

Alex's controller receives:

- own energy is becoming low but not critical;
- current activity is work;
- goal is to go home after work;
- Jordan is currently visible;
- available tools.

It calls:

```json
{
  "name": "go_to",
  "arguments": {
    "target_id": "home",
    "reason": "The workday is complete"
  }
}
```

This tool call and reason are private/admin records.

### 28.2 Domain execution

The call is validated and committed as a movement intent. Pathfinding moves Alex
one tile per tick.

### 28.3 Jordan's perception

When Alex crosses the Office boundary while visible, the perception layer emits
to Jordan:

```text
Alex left the Office.
```

It does not emit:

```text
Alex decided to go home because the workday is complete.
```

If Alex moves beyond line-of-sight, Jordan may receive:

```text
Alex is no longer visible.
```

Jordan's knowledge becomes "Alex was last seen leaving the Office at t=...".

### 28.4 Explicit communication

If Alex instead calls:

```json
{
  "name": "say",
  "arguments": {
    "target_id": "jordan",
    "text": "I'm going home now.",
    "reason": "Let Jordan know I am leaving"
  }
}
```

and Jordan can hear it, Jordan may legitimately know Alex stated an intention to
go home. Jordan still knows a statement, not guaranteed future success.

### 28.5 Survival interruption

If Alex's energy becomes critical before departure:

- System 1 clears the ordinary movement plan;
- the controller cannot override correction;
- observers may see Alex change direction or use a bed;
- they do not automatically receive Alex's numeric energy or private drive;
- a late model result is rejected as stale.

This example captures the central philosophy: private cognition proposes,
deterministic embodiment acts, situated perception informs, and explicit speech
communicates.

## 29. Development review checklist

Before accepting a substantial feature, confirm:

- Does it preserve domain authority?
- Does it preserve fixed-step physical behavior?
- Can it invoke a model from inside an ordered system?
- Can System 1 preempt it?
- Does it expose operator-only data to a character?
- Does it distinguish observed behavior from private intent?
- Are all IDs and payloads typed and validated?
- Are failures explicit?
- Is event order deterministic?
- Is persisted output versioned?
- Can fake/scripted execution test it without network access?
- Can live nondeterminism be recorded and replayed?
- Does it introduce provider-specific types outside adapters?
- Is the README affected as a usage document?
- Is this concept guide affected as a mental-model document?

## 30. Document relationships

- `README.md`: installation, operation, and experimentation.
- `docs/CONCEPT_GUIDE.md`: canonical vocabulary and advanced mental model.
- `docs/starting_basic_PRD.md`: original Stage 0 product requirements.
- `docs/IMPLEMENTATION_PLAN.md`: phased implementation of the prototype.
- `docs/REAL_LLM_TOOL_AGENT_PLAN.md`: detailed sensing and real-LLM controller
  architecture and implementation record.
- `docs/PROJECT_STATE_ASSESSMENT.md`: historical assessment and completion
  record.

This guide should remain shorter than the combined design record but complete
enough that a developer can locate the correct abstraction without reading every
other document first.

## 31. Final mental model

The project models people as continuously embodied characters, not as text
prompts.

```text
character state
  + deterministic world
  + fixed simulation time
  + survival arbitration
  + intentional tools
  + situated perception
  + episodic memory
  = grounded simulated behavior
```

An LLM may choose an intention. It does not move a body, change a meter, grant an
affordance, determine who heard speech, or declare success. The domain performs
those functions.

The operator may inspect everything. A character may know only itself, what it
perceived, what it was told, and what it remembers.

That separation—between cognition and embodiment, truth and perception,
intention and outcome, operator visibility and character knowledge—is the core
philosophy of Stage 0 and the constraint that should guide every future
extension.
