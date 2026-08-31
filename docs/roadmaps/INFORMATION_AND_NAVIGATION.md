# Unified Character Information, Memory, and Navigation Roadmap

**Status:** Active roadmap; information and navigation foundations implemented
**Date:** 2026-08-30  
**Scope:** Character information, memory, controller context, spatial knowledge,
and navigation across nested environments  
**Near-term non-scope:** Implementing online behavior or a live internet model

**Implemented cutover (2026-08-30):** Canonical information documents,
retrieval, topology contracts/adapters, recursive route composition,
character-known topology projection, and the additive `NAVIGATE` execution path
are implemented. `navigate_to` is the primary controller tool;
`go_to`/`travel_to` remain accepted compatibility translations. Existing
`MOVE_TO`, `TRAVEL_TO`, `MovementComponent`, and `TravelComponent` execution
remain supported.

The initial physical compiler refines grid legs into existing movement and
collapses city/building transition legs into existing travel, followed by local
movement inside the destination building. The general planner can compose
arbitrary registered transitions, but execution adapters beyond current grid,
building-entrance, and sparse-city travel mechanics still require an executor.
Completed navigation and standalone travel deterministically author private,
character-owned `knowledge.route` documents from direct experience. These
documents retain the final locator, traversed transition IDs, simulation time,
and causal event references, and immediately participate in known-topology
projection without embedding or model calls.

## 1. Executive proposal

Use two general-purpose substrates:

1. a unified **information-document system** for character definitions,
   knowledge, observations, memories, and summaries;
2. a unified **space-transition system** for navigation within and between
   rooms, buildings, neighborhoods, cities, and future non-physical spaces.

The information system keeps authored data coherent. A character is not
manually divided into capability records, preference records, constraint
records, and many other prompt-oriented fragments. Instead, the source of truth
is a small collection of self-describing documents. Retrieval chunks, tags,
embeddings, summaries, and relationship indexes are derived projections that
can be rebuilt.

The navigation system removes the architectural distinction between local
movement and long-distance travel. A room, building, neighborhood, or city is a
space with its own topology. Doors, entrances, roads, transit connections, and
other crossings are transitions between locators. Existing grids and transport
graphs become implementations of the same topology interface.

These substrates meet at the epistemic boundary:

```text
authoritative information and topology
  -> character-specific observations and acquired information
  -> character information store
  -> bounded context or known-topology projection
  -> controller decision
  -> attempted action
  -> physical execution and observable outcome
```

Character information must not become an action permission system. Age,
licences, skills, social rules, preferences, allergies, and similar information
may affect decisions and outcomes, but they do not prohibit an action merely
because the action is unwise, illegal, unusual, or dangerous.

## 2. Corrections to the earlier proposal

The earlier proposal over-specialized the architecture in two ways.

First, it proposed many kinds of character context records with dedicated
retrieval rules and "hard constraints." That would make character authoring
increasingly bureaucratic and would turn descriptive information into policy.
Adding a new kind of information should not normally require a new field in
several Python models, a new prompt section, and a new retrieval branch.

Second, it treated local movement, city travel, place discovery, and future
online navigation as related but separate systems. This preserves current
implementation boundaries but does not provide a sufficiently general model.

The revised design therefore makes the following distinctions:

- **canonical documents versus derived indexes**, rather than many canonical
  information fragments;
- **information consistency versus behavioral permission**, rather than
  treating important facts as action constraints;
- **spaces and transitions versus fixed spatial scales**, rather than separate
  movement architectures for each scale;
- **authoritative topology versus known topology**, rather than exposing all
  destinations or making navigation depend on omniscient controller context.

## 3. Design principles

### 3.1 Authored information stays coherent

A contributor should be able to add a new descriptive field to a character
without changing application code. The field should be retained, displayed,
indexed, and made retrievable through general mechanisms.

Specialized code is required only when a value gains specialized simulation
semantics. For example:

- adding a favorite color should require no code;
- adding a new biographical note should require no code;
- adding a new physiological variable requires domain behavior because it
  changes simulation state;
- adding a new directly executable action requires deterministic execution,
  events, persistence, and tests.

### 3.2 Derived retrieval structure is disposable

Chunk boundaries, embeddings, lexical indexes, entity-reference indexes, and
summaries are performance and context projections. They are not the canonical
character definition.

Changing a retrieval strategy must not require migrating the authored character
record unless the underlying meaning changed.

### 3.3 Information does not authorize behavior

Profile and memory information can affect:

- what the character believes;
- what the controller considers;
- which option the character prefers;
- how competently an attempt is executed;
- the physical or physiological result;
- how other characters interpret the behavior.

It must not silently become a permission layer.

Examples:

- a child may attempt to drive;
- an unlicensed adult may attempt to drive;
- an inexperienced driver may drive badly;
- a person may knowingly or accidentally eat an allergen;
- a character may trespass through an unlocked entrance;
- a character may make a choice inconsistent with stated preferences.

The simulation may reject or fail an attempt when the required physical
interaction cannot occur. It should not reject the attempt because a character
description says that it would be illegal, unsafe, out of character, or beyond
the character's nominal skill.

### 3.4 Information can still have structural invariants

Removing behavioral constraints does not mean accepting incoherent
authoritative data.

A canonical character definition may enforce facts such as:

- one stable character ID;
- one birth event for one person;
- one value for a field defined as singular at a given valid time;
- valid units and value types;
- non-overlapping revisions where an information type requires them;
- references to existing entities when a reference is authoritative.

Multiple reports or memories may disagree about a fact. They remain separate
claims with different sources. The authoritative character definition,
however, should not directly assert mutually exclusive canonical values without
surfacing a validation error.

### 3.5 Navigation is recursive

No fixed hierarchy such as:

```text
city -> district -> building -> room
```

should be embedded into the core navigation contracts.

That hierarchy is a useful scenario configuration, but the core model should
support arbitrary nesting and cross-links:

```text
campus -> building -> floor -> room
city -> station -> platform -> train
building -> skybridge -> building
room -> elevator -> floor
```

Future digital spaces may use the same addressing and transition concepts
without sharing physical movement execution.

## 4. Unified information-document system

## 4.1 Canonical unit: `InformationDocument`

Use a general envelope around coherent, self-describing content:

```python
@dataclass(frozen=True, slots=True)
class InformationDocument:
    id: str
    namespace_id: str
    kind: str
    schema_id: str
    subject_ids: tuple[str, ...]
    content: JsonValue
    source: InformationSource
    valid_time: TimeRange | None
    recorded_at: float | None
    visibility: VisibilityPolicy
    revision: int
    content_hash: str
```

The envelope is strict. `content` is extensible according to the selected
schema, including a permissive generic schema for experimental information.

Typical documents include:

| Kind | Example |
|---|---|
| `character.dossier` | Authored identity, history, preferences, capabilities, and other attributes |
| `memory.episode` | One remembered experience with participants, time, and narrative |
| `knowledge.observation` | A retained observation or communicated fact |
| `knowledge.summary` | A consolidation derived from multiple episodes |
| `relationship.history` | Coherent relationship information involving one or more characters |
| `plan.note` | A private retained intention or unresolved commitment |

These are kinds within one storage, indexing, provenance, and retrieval system.
They are not separate context architectures.

## 4.2 The character dossier remains one coherent document

The current `human-v1` structure can remain as an authoring and UI convention,
but it should no longer be treated as an exhaustive Python field list.

A future dossier could look like:

```json
{
  "schema_version": 2,
  "id": "alex-chen",
  "kind": "character.dossier",
  "content": {
    "identity": {
      "display_name": "Alex Chen",
      "birth_event": {
        "date": "1992-04-03",
        "place_id": "place-shanghai"
      }
    },
    "personality": {
      "summary": "Quiet, methodical, and considerate"
    },
    "capabilities": {
      "driving": {
        "experience": "moderate",
        "licences": ["car"]
      }
    },
    "experimental": {
      "spatial_reasoning_style": "landmark-oriented"
    }
  }
}
```

Adding `spatial_reasoning_style` does not require a new Pydantic property or ECS
field. The editor can render unknown nested content through a generic
object/list/scalar editor until a richer UI widget is justified.

Standard schema paths remain useful for interoperability and specialized UI,
but unspecified paths are preserved rather than rejected merely because the
code has not seen them before.

## 4.3 Schema descriptors define meaning without defining behavior

An information schema registry may optionally describe paths:

```python
@dataclass(frozen=True, slots=True)
class InformationFieldDescriptor:
    path: str
    value_schema: JsonValue
    cardinality: Cardinality
    temporal_mode: TemporalMode
    reference_kind: str | None
    display_label: str | None
    indexing_hint: str | None
```

Descriptors serve:

- structural validation;
- generic editor hints;
- formatting;
- reference validation;
- canonical conflict detection;
- indexing hints.

They do not define whether an action is allowed.

Unregistered fields use safe defaults:

- preserved as JSON;
- included in canonical hashes and snapshots;
- rendered by the generic renderer;
- indexed as part of their containing document;
- treated as multi-valued or opaque unless their containing schema says
  otherwise;
- given no specialized domain mechanics.

This makes extension cheap while retaining an explicit path for stronger
semantics where necessary.

## 4.4 Memory uses the same document substrate

Memory should not be only a tuple of text, timestamp, importance, and embedding.
An episode remains coherent:

```json
{
  "kind": "memory.episode",
  "subject_ids": ["agent-001"],
  "content": {
    "summary": "Jordan warned Alex that the west entrance was closed.",
    "participants": ["agent-001", "agent-002"],
    "perceived_events": ["event-00421"],
    "places": ["building-office", "entrance-west"],
    "details": {
      "speaker": "agent-002",
      "claimed_closure": "entrance-west"
    }
  },
  "source": {
    "type": "DIRECT_PERCEPTION",
    "observer_id": "agent-001"
  },
  "recorded_at": 5400
}
```

The same store can contain:

- frozen initial dossier documents;
- perceived episodes;
- communicated information;
- summaries and consolidations;
- place knowledge;
- relationship history.

Retrieval policies may weight document kinds differently, but they share the
same retrieval contract and result format.

## 4.5 Do not author retrieval fragments

The indexing pipeline derives searchable units from canonical documents:

```text
canonical document
  -> normalize paths and references
  -> derive section-level passages
  -> derive entity and place references
  -> derive lexical terms
  -> optionally derive embeddings
  -> store projection with source document ID and path
```

For example, the dossier remains one file and one canonical document. The index
may internally create passages for:

- identity and background;
- driving-related material;
- food-related preferences and experiences;
- relationship information involving a given character;
- arbitrary experimental sections.

These passages can be changed or regenerated without changing the character
file.

## 4.6 Retrieve anchors, then restore coherence

Plain top-k chunk retrieval often produces disconnected fragments. Use a
two-step context projection:

1. retrieve relevant anchors using semantic, lexical, entity-reference, place,
   time, and source signals;
2. expand each anchor to a coherent neighborhood in its source document.

Neighborhood expansion may include:

- the parent object containing the matched field;
- nearby sibling fields;
- referenced entities already relevant to the decision;
- surrounding events from the same episode;
- a bounded preceding or following section;
- the authoritative value and any conflicting remembered claim, clearly
  labelled by source.

The controller receives context capsules rather than isolated scalar facts:

```text
Context capsule: Driving background
Source: character dossier, revision 4

Alex has moderate practical driving experience and holds a car licence.
He dislikes driving in dense traffic. He previously scraped a parked car while
reversing in a narrow garage.
```

The capsule is a rendered projection. Its statements retain references to the
underlying document IDs and paths for replay and inspection.

## 4.7 Context assembly

One general retrieval request should search the character's information
namespace:

```python
@dataclass(frozen=True, slots=True)
class InformationQuery:
    character_id: str
    text: str
    referenced_entity_ids: tuple[str, ...]
    referenced_place_ids: tuple[str, ...]
    simulation_time: float
    source_scope: tuple[str, ...] | None
    token_budget: int
```

The query is derived from:

- current self-state;
- recent perceived facts;
- current unresolved goals;
- present characters and objects;
- available physical interactions;
- the reason cognition was scheduled.

The retriever returns one ordered result type regardless of whether the source
was a profile, memory, observation, or summary:

```python
@dataclass(frozen=True, slots=True)
class RetrievedInformation:
    document_id: str
    document_kind: str
    source_path: str | None
    rendered_content: str
    source: InformationSource
    valid_time: TimeRange | None
    score: float
```

The final controller input can still separate current authoritative self-state
from retrieved information for clarity. That is a prompt presentation choice,
not a fragmented storage architecture.

## 4.8 Truth, belief, and contradiction

The store must distinguish:

- authoritative character definition;
- current authoritative world state;
- a character's observation;
- a statement made by another character;
- a remembered episode;
- a derived summary or inference.

A remembered or communicated statement is not silently promoted to truth.

The same proposition may appear in multiple documents:

```text
dossier: Alex was born in Shanghai.
Jordan's claim: Alex was born in Beijing.
Alex's memory: Jordan incorrectly said that Alex was born in Beijing.
```

The canonical dossier can enforce one birth event. The information store can
still preserve the conflicting claim and memory because they describe what was
said or remembered, not a second authoritative birth.

## 5. Action semantics

## 5.1 Separate information from physical mechanics

The action pipeline should distinguish:

1. **well-formed request:** the tool call names an action and valid references;
2. **attemptable interaction:** the world contains a relevant interaction that
   the character can physically attempt;
3. **execution:** deterministic systems apply the attempt to current world
   state;
4. **outcome:** success, partial success, failure, injury, interruption, or
   another explicit result.

Profile information is not checked between steps 1 and 2 as authorization.

Examples:

| Information | Correct effect |
|---|---|
| Child or unlicensed driver | May influence controller choice, social reaction, or driving outcome; does not automatically prohibit driving |
| Driving experience | May influence execution quality if driving competence is modeled |
| Allergy | Eating remains possible; physiology applies the consequence |
| Dislike of a food | Influences choice, not physical eligibility |
| Locked door without a usable opening method | Ordinary traversal is physically unavailable; another action such as requesting entry or forcing the door may exist |
| "Staff only" sign on an unlocked door | Does not physically block traversal |

If the project retains System 1 preemption, it should remain a separately
declared executive-control mechanism. It must not be implemented by converting
profile or memory facts into action permissions.

## 5.2 Prefer attempts over premature rejection

Where meaningful, the simulation should accept an attempted interaction and
emit an explicit failure rather than preventing the controller from expressing
the attempt.

This is especially important for research data. A failed attempt reveals
decision-making and world interaction, while an invisible permission filter
removes that behavior from the trajectory.

## 6. Unified space-transition navigation

## 6.1 Core concepts

Use four general concepts:

### Space

A container whose internal locations can be related by a topology.

Examples:

- a room represented by continuous coordinates;
- a floor represented by a grid;
- a building represented by rooms and doors;
- a neighborhood represented by a pedestrian graph;
- a city represented by a transport graph.

### Locator

A stable address within a space:

```python
@dataclass(frozen=True, slots=True)
class Locator:
    space_id: str
    local_reference: JsonValue
```

`local_reference` is interpreted by the space's topology adapter. It may be a
grid coordinate, graph node, named anchor, edge position, or another supported
address.

### Transition

A traversable relationship between two locators, possibly in different spaces:

```python
@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    from_locator: Locator
    to_locator: Locator
    traversal_kind: str
    executor_id: str
    cost_model_id: str
    bidirectional: bool
    metadata: dict[str, JsonValue]
```

Examples:

- cross a room;
- pass through a door;
- use an elevator;
- exit a building;
- walk along a sidewalk;
- enter a car;
- drive along a road;
- board a train;
- enter another nested space.

### Route

An ordered sequence of transition and intra-space legs:

```python
@dataclass(frozen=True, slots=True)
class Route:
    origin: Locator
    destination: Locator
    legs: tuple[RouteLeg, ...]
    planned_from_topology_revision: int
```

## 6.2 Spaces may be nested, linked, or overlapping

A `SpaceRegistry` describes containment and connection without imposing fixed
levels:

```text
space-city
  contains space-neighborhood-west
  contains space-building-office

space-building-office
  contains space-floor-ground
  contains space-floor-first

space-floor-ground
  contains space-room-lobby
  contains space-room-cafe
```

Containment is not the only relationship. A skybridge can directly link spaces
in two buildings. A metro station can expose transitions to several platforms
and transport services. An outdoor plaza may overlap a district without being
owned by a building.

## 6.3 Topology adapters preserve useful specialized representations

The master abstraction should not force every environment into one giant graph
or one giant grid.

```python
class SpaceTopology(Protocol):
    def resolve(self, reference: JsonValue) -> Locator: ...
    def plan_local_route(
        self,
        origin: Locator,
        destination: Locator,
        traversal_context: TraversalContext,
    ) -> LocalRoute | None: ...
    def outgoing_transitions(self, locator: Locator) -> tuple[Transition, ...]: ...
```

Initial adapters:

- `GridTopology` wraps the existing interior grid and deterministic A*;
- `SparseGraphTopology` wraps neighborhood and transport networks;
- `ContainerTopology` connects named subspaces and portals;
- `VehicleTopology` represents entering, occupying, and leaving a moving
  vehicle where needed.

Future digital navigation could add another topology and executors, but it is
not needed to validate the physical architecture.

## 6.4 Hierarchical route planning

Route planning becomes recursive:

```text
resolve destination entity to destination locator
  -> find a high-level path through spaces and cross-space transitions
  -> ask each involved topology adapter for its local route
  -> combine local and cross-space legs
  -> execute one leg at a time
```

Example:

```text
desk coordinate
  -> office door
  -> floor corridor
  -> building exit
  -> sidewalk node
  -> parking node
  -> car interior
  -> road network
  -> destination parking
  -> destination entrance
  -> destination corridor
  -> restaurant table
```

The differences between moving across a room and moving between buildings are
topology, transition, cost, and executor differences. They are not different
navigation concepts.

## 6.5 One navigation intention

The conceptual controller action should converge on:

```text
navigate_to(target_id, optional_preferences)
```

The controller selects the destination and may express preferences such as
using a car or avoiding a particular route. The navigation service resolves the
target to locators and produces feasible route options.

Existing `go_to` and `travel_to` tools may remain as compatibility aliases
during migration:

```text
go_to      -> navigate_to with local-only compatibility policy
travel_to  -> navigate_to with current transport-mode arguments
```

They should not remain separate permanent architectures.

## 6.6 Route feasibility is physical, not biographical permission

A transition evaluator may inspect:

- current locator;
- physical connectivity;
- obstruction state;
- vehicle location and operating state;
- capacity;
- whether the character is physically occupying or controlling the required
  object;
- whether a door, gate, or interface accepts the attempted interaction;
- safe interruption points required by the simulation.

It should not reject a route because:

- the character lacks a licence;
- the route would trespass;
- the character is too young according to a legal rule;
- the route is inconsistent with a preference;
- the character has never performed the activity before.

If body dimensions, reach, strength, injury, or another modeled physical
property makes an interaction physically impossible, that is an execution
concern rather than a social permission.

## 7. Authoritative topology and character-known topology

The simulation owns the complete authoritative topology. Characters do not.

Each character's information namespace may contain documents describing known
places, routes, entrances, landmarks, closures, and travel experiences. These
documents use the same information system as profiles and memories:

```json
{
  "kind": "knowledge.place",
  "subject_ids": ["agent-001", "building-office"],
  "content": {
    "place_id": "building-office",
    "known_locators": ["entrance-east"],
    "claims": {
      "district": "central",
      "west_entrance_open": false
    }
  },
  "source": {
    "type": "DIRECT_VISIT"
  },
  "recorded_at": 7200
}
```

Navigation therefore has two related inputs:

1. **epistemic route planning:** what destination and route the character
   believes are available;
2. **authoritative execution:** what the materialized world actually permits.

This supports:

- unknown destinations;
- incomplete maps;
- stale closure information;
- incorrect directions;
- discovering an unexpected obstruction;
- learning a route by travelling it;
- communicating route knowledge between characters.

The controller should not receive all global topology. It receives a bounded
projection of relevant known destinations and route information. The physical
execution layer still uses authoritative topology to determine what happens.

## 8. Relationship between information retrieval and navigation

Destination resolution is an information query before it is a route query.

```text
"I want somewhere to eat"
  -> retrieve known place information
  -> identify candidate destination entities
  -> choose one destination
  -> resolve its known locator
  -> plan through known topology
  -> attempt navigation in authoritative topology
```

Place tags may improve retrieval, but tags are derived indexes over place and
knowledge documents. They are not a separate place-awareness architecture.

Likewise, a remembered trip, a communicated address, and an initialized common
map are all information documents with different provenance. The navigation
system consumes the resulting locator and topology claims through one
projection interface.

## 9. Future digital spaces

Online behavior should not drive the near-term implementation. The physical
design should, however, avoid assumptions that make future extension
impossible.

A future website, application, page, account, channel, or social-media profile
could be represented as spaces and addressable nodes connected by transitions.
The transition executor would be different:

- physical transitions change embodied location;
- digital transitions change an active information interface or session;
- authentication failures are service behavior;
- hyperlinks or application controls are digital affordances.

This does not mean physical and online execution should share one domain
system. They share:

- hierarchical addressing;
- nested spaces;
- transitions;
- route composition;
- epistemic knowledge of destinations;
- traversal history.

They use separate topology adapters, executors, clocks, affordances, and
perception rules. No online-specific executor is required in the near-term
work.

## 10. Proposed package boundaries

```text
src/stage0_sim/
  domain/
    information/
      documents.py        canonical document envelope and provenance
      schemas.py          optional path descriptors and invariants
    world/
      topology.py         Space, Locator, Transition, Route
      grid.py             existing local grid topology
      graph.py            sparse graph topology
      spaces.py           registry, containment, and portals
    components/
      information.py      character information namespace references
      spatial.py          authoritative Locator
      navigation.py       active route and route progress

  application/
    information/
      store.py            document persistence and revision access
      indexing.py         derived passages, references, lexical/embedding index
      retrieval.py        unified queries and ranking
      context.py          anchor expansion and bounded rendering
      memory.py           episode creation and consolidation policy
    navigation/
      destinations.py     information result -> destination locator
      planner.py          recursive route composition
      knowledge.py        known-topology projection
      intents.py          navigate intention validation and commit

  adapters/
    information/
      sqlite.py           canonical documents and derived index persistence
      embeddings.py       optional embedding providers
```

The exact filenames may change. The important boundary is that domain topology
does not depend on prompts or embedding providers, while retrieval indexes do
not become authoritative character data.

## 11. Migration from the current implementation

### Phase 1: Canonical information envelope

1. Introduce `InformationDocument` and source/provenance contracts.
2. Wrap each resolved `human-v1` character profile as one
   `character.dossier` document.
3. Preserve the existing character JSON format through a compatibility loader.
4. Store the frozen document and content hash in run datasets.
5. Keep the existing rendered description while the new projection is tested.

### Phase 2: Unified profile and memory retrieval

1. Represent newly recorded episodes as `memory.episode` documents.
2. Build derived passages and reference indexes from dossiers and episodes.
3. Retrieve anchors across both sources through one `InformationQuery`.
4. Expand anchors into coherent context capsules.
5. Record document IDs, paths, scores, and rendered capsules with each
   controller request.
6. Stop sending the complete dossier when bounded retrieval has adequate
   coverage.

### Phase 3: Extensible authoring

1. Allow generic nested data under the dossier content envelope.
2. Make standard `human-v1` sections schema and editor conveniences rather than
   the complete accepted vocabulary.
3. Add optional field descriptors for stronger validation and UI widgets.
4. Preserve unknown content through load, edit, stage, snapshot, and export.

### Phase 4: General topology contracts

**Initial implementation complete.**

1. Introduce `Space`, `Locator`, `Transition`, and topology protocols.
2. Adapt the existing `WorldMap` and A* implementation as `GridTopology`.
3. Adapt the existing city transport graph as `SparseGraphTopology`.
4. Represent building entrances as ordinary cross-space transitions.
5. Migrate `SpatialLocationComponent` from fixed scale fields toward a general
   locator.

### Phase 5: Recursive navigation

**Initial implementation complete, with compatibility execution adapters.**

1. Add destination-to-locator resolution.
2. Plan high-level space transitions and refine local legs through adapters.
3. Introduce a unified navigation intent and route state.
4. Preserve `go_to` and `travel_to` as compatibility translations.
5. Ensure System 1 interruption still resolves to explicit safe locators rather
   than relying on fixed city-scale assumptions.

### Phase 6: Character-known topology

**Projection, scenario initialization, and direct-experience route learning are
complete. Knowledge updates from communication and richer perception remain
future work.**

1. Store place and route knowledge as information documents.
2. Build a known-topology projection for each navigation decision.
3. Prevent controller context from enumerating every authoritative place.
4. Allow planned routes to be incomplete or incorrect.
5. Update knowledge through perception, communication, and completed travel.

## 12. Required validation scenarios

### Information extensibility

- Add an arbitrary nested character field without changing Python code.
- Load, edit, stage, render, retrieve, persist, and export it without loss.
- Add an optional schema descriptor later without changing the canonical value.

### Information coherence

- A retrieved driving passage expands into a coherent driving context capsule.
- Unrelated scalar fragments are not independently dumped into the prompt.
- Conflicting communicated claims remain distinct from the canonical dossier.
- Two canonical simultaneous birth events fail validation.

### Behavior versus information

- A child can attempt to operate an accessible car.
- A missing licence does not make a driving route unavailable.
- Low skill does not reject an attempt, although it may affect a modeled
  outcome.
- A character can consume an allergen and experience the configured
  physiological result.
- A social or legal restriction does not become a physical obstruction.

### Recursive navigation

- A route crosses a room, door, corridor, building entrance, sidewalk network,
  destination entrance, and destination room through one route contract.
- The same planner supports a route entirely within one room.
- A cross-building bridge is represented without forcing travel through city
  scale.
- A route can include a moving vehicle space.

### Epistemic navigation

- A character cannot select an entirely unknown destination merely because it
  exists in the global city.
- A known place with incomplete address information may require further
  information before route planning.
- A stale remembered closure can produce an inferior plan that fails against
  current authoritative topology.
- Traversing a route creates character-owned route knowledge without exposing
  unrelated global topology.

### Determinism and replay

- Canonical information documents and derived passages have stable hashes.
- Retrieval ties use stable document ID and path ordering.
- Route planning ties use stable transition and locator ordering.
- Recorded retrieval and route results can be replayed without calling an
  embedding or model provider.

## 13. Review decisions

The proposal recommends these architectural decisions:

1. Keep coherent documents canonical; make prompt chunks and indexes derived.
2. Make the dossier content extensible without requiring a Python field for
   every descriptive attribute.
3. Use optional schema descriptors for data invariants, rendering, and editor
   hints, not action authorization.
4. Store profiles, memories, observations, summaries, and place knowledge in
   one versioned information-document system.
5. Retrieve anchors and expand their document neighborhoods before rendering
   controller context.
6. Treat skills, age, legality, preferences, and allergies as information or
   outcome inputs, not action permissions.
7. Replace fixed spatial levels with recursive spaces, locators, transitions,
   and topology adapters.
8. Converge local movement and city travel on one navigation intention and
   route representation.
9. Keep authoritative topology separate from character-known topology.
10. Future-proof addressing and transitions for digital spaces without
    implementing online behavior now.

## 14. Final model

```text
Character information
  canonical coherent documents
    -> disposable indexes
    -> retrieved anchors
    -> coherent context capsules

Character memory
  new documents in the same namespace
    -> same indexing and retrieval path
    -> different provenance and temporal meaning

Navigation
  destination information
    -> known locator and known topology
    -> recursive route across spaces and transitions
    -> authoritative physical execution
    -> private direct-experience route documents
    -> observations and new memory documents
```

This architecture keeps authoring flexible, retrieval bounded, character
knowledge non-omniscient, and physical execution authoritative without turning
character descriptions into a growing permission system.
