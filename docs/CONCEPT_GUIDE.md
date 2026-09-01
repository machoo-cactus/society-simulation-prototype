# Concept Guide

**Owner:** Project purpose, shared terminology, and the compact mental model.
Detailed contracts belong in the linked task guides.

## Purpose

Stage 0 is the ground-truth execution layer for later multi-fidelity research.
Every active character, location, physiological value, intention, and physical
transition is explicit. The simulation does not fabricate histories or replace
materialized characters with statistical populations.

The project is designed to test:

- deterministic embodied behavior;
- continuous homeostasis;
- absolute System 1 survival priority;
- slow cognition separated from physical authority;
- situated rather than omniscient character knowledge;
- reproducible research capture;
- strict, measurable scenario goals.

## Terms

| Term | Meaning |
| --- | --- |
| **character** | A simulated person represented by ECS components |
| **character controller** | Provider-neutral software selecting one typed intentional choice |
| **model client** | Adapter that sends provider-neutral requests to a model service and normalizes results |
| **operator** | Human using the CLI, API, UI, or datasets; may have an omniscient view |
| **component** | Typed entity state and local invariants |
| **system** | Ordered deterministic logic over relevant entities/resources |
| **System 1** | Non-bypassable deterministic survival arbitration |
| **plan** | Current and queued intentional `ActionInstance` values |
| **tool** | Strict capability offered to a character controller |
| **intent** | Immutable validated request between cognition and deterministic execution |
| **affordance** | World-defined physical action with explicit preconditions and effects |
| **perceptible fact** | Privacy-safe evidence derived from authoritative truth |
| **perceived fact** | A perceptible fact delivered to one observer |
| **knowledge** | Timestamped character belief from self-state, perception, communication, initialization, or memory |
| **telemetry** | Omniscient operator projection; never character context |
| **dataset record** | Immutable versioned research observation; never live authority |
| **scenario** | Strict schema-version-4 portable initial configuration |
| **prepared scenario** | Scenario plus frozen resolved characters, elements, assignments, and optional situations |
| **run** | One process-local clock, ECS registry, event stream, and dataset |

NPC service workers are run-scoped characters created from compact NPC-role
elements. They use ordinary embodiment, perception, tools, intents, plans, and
domain validation; they are not hidden building logic or durable character
library profiles.

## Central separation

```text
private/situated context
  -> character controller proposes one typed tool
  -> application validates and creates an intent
  -> deterministic domain systems execute or reject it
  -> privacy-safe evidence is projected
  -> each observer receives only what it can perceive
  -> operator telemetry and research data observe separately
```

A model may choose an intention. It cannot move a body, set physiology, grant an
affordance, decide who heard speech, bypass System 1, or declare success.

Descriptive dossier facts—skills, age, preferences, legal status, allergies,
finances—are information. They do not silently become physical permissions,
inventory, funds, or live physiology. Physical mechanics may use explicitly
modeled state and world preconditions.

## Time

- **Simulation time:** authoritative fixed-step world time.
- **Cognition barrier:** wall-clock wait at a fixed simulation tick while the
  complete provider batch settles.
- **Telemetry time:** operator publication cadence that never advances state.
- **Wall time:** diagnostics, pacing, latency, and timeout metadata.

Real model latency changes how long an operator waits, not how much simulated
time passes or which physical rules apply.

## Truth, privacy, and persistence

Authoritative state, perceptible evidence, observer knowledge, telemetry, and
research data are different representations. Recording a private prompt or
memory does not make it observable. A statement heard by a character becomes a
claim, not automatically authoritative truth.

Datasets store the raw execution record and projections needed for analysis.
They can rehydrate durable memories and information through explicit
interfaces, but they do not restore the complete clock, ECS, RNG, queues, or
pending work required for a live checkpoint.

## Where details live

- [Architecture](ARCHITECTURE.md): authority and dependency boundaries.
- [Runtime semantics](RUNTIME.md): tick, system order, barrier, System 1, and
  perception.
- [Actions, tools, and events](ACTIONS_AND_EVENTS.md): closed vocabulary.
- [Scenario and element authoring](SCENARIO_EDITOR_GUIDE.md): source schema.
- [Character authoring](CHARACTER_PROFILE_GUIDE.md): stable dossier schema.
- [Research data](DATA_COLLECTION.md): persistence, privacy, queries, exports.
- [API and UI workflows](API_AND_UI.md): public routes and operator lifecycle.
- [Development history](legacy/DEVELOPMENT_HISTORY.md): why earlier designs
  changed.
