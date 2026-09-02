# Character Agent Actions and Decision Flow

**Owner:** Character-controller capabilities, action requirements, decision
context, information retrieval, deterministic commit, and physical outcomes.

This document describes what a character controller can request and how that
request becomes simulation behavior. It covers ordinary characters and the
restricted service-NPC controller.

The simulated person is the **character**. The software or model that selects
the next request is the **character controller**. The controller proposes an
action; it never directly changes physical state or declares success.

## Core authority rule

```text
controller tool call
  -> validated immutable intent
  -> queued action or bounded read
  -> deterministic domain-system execution
  -> completed, failed, or cancelled outcome
```

`tool.accepted` and `tool.committed` mean that the request passed the
application boundary. They do not mean the character moved, spoke, picked
something up, completed an activity, or completed a transaction.

Only domain systems decide physical outcomes. They revalidate current
authoritative ECS, topology, `SpatialIndex`, capacity, possession, posture,
relation, and availability state immediately before changing it.

## Controller availability and eligibility

Ordinary character controllers may be offered:

- `navigate_to`
- `perform`
- `interact_with`
- `say`
- `wait`
- `skip`
- `transact`
- `check_environment`

The actual offered set is the intersection of the implemented tools and the
scenario/entity controller allowlist. A controller cannot call an implemented
tool that was not offered for that decision.

Service NPC roles are restricted to:

- `serve_transaction`
- `say`
- `wait`
- `skip`

The cognition scheduler considers a character only when all applicable gates
pass:

| Gate | Requirement |
| --- | --- |
| Controller | Enabled |
| Pending request | No decision already pending |
| Decision time | `next_decision_time` reached, unless a relevant environment update triggers reconsideration |
| Plan | No current or queued plan action |
| System 1 | Normal; no active survival correction |
| Affordance | No active affordance execution |
| Speech | No pending speech |
| NPC service | An NPC has an assigned request requiring authorization |
| Budget | Request, input-token, and output-token budgets remain available |

Decision triggers are `idle`, `time_update`, or `environment_update`.
System 1 has absolute priority and can prevent a decision, reject a settled
decision, or cancel incompatible work.

## Information supplied to a decision

### Stable and frozen character context

The request identifies the character profile, template version, and content
hash. It also includes the prepared scenario-specific character situation and
its hashes.

The profile is stable identity/context. The situation is frozen temporary
scenario context. Neither is physical authority: descriptive possessions,
capabilities, intentions, or locations do not create ECS state or permission.

### Current self-observation

An ordinary character receives:

| Field | Contents |
| --- | --- |
| Identity | Agent ID and display name |
| Time | Simulation time and available civil-calendar fields |
| Location | Current zone and hierarchical spatial location, including room/building/city context and local coordinate when applicable |
| Activity | Current authoritative activity |
| Homeostasis | Own satiety, energy, and stress |
| Travel | Currently available travel modes |
| Possessions | Own fungible holdings with item names, units, and quantities |
| Goals | Own structured goals, status, priority, and tags |
| Targets | Known destinations, observable characters, visible physical objects, stations, and transaction points |
| Perception | Newly delivered observer-specific facts |
| Environment | Available time, weather, surface-condition, and resource-availability information |
| Outcome | Recent controller/action outcome summary |

### Targets

A target record can describe:

- a room, zone, building, or outdoor place known through the character's
  topology knowledge;
- a station or transaction point, including supported activities or visible
  offers;
- a currently visible physical object;
- a visible or previously known character.

Physical-object targets may expose:

- currently supported `READ` or `DRINK` activities;
- currently available physical interaction verbs;
- public open/closed and locked state;
- public spatial relation, parent, and slot;
- visible custody or `HELD_BY` state.

The available interaction list is computed from current state. It is not a
promise that the interaction will still succeed after the model responds.

Character targets may remain known after leaving view, but include their last
observed tick rather than live private state.

### Perception and privacy

Facts are observer-specific and originate from authoritative events/state.
Characters may receive visible movement, posture, activity, physical-object
state changes, literal delivered speech, and public environment evidence.

The controller does not receive another character's private:

- destination or plan;
- controller reason or hidden reasoning;
- homeostasis values or System 1 drive;
- prompt, model request, or model response;
- profile, situation, memory, or information-retrieval content;
- ownership data that is not publicly perceptible;
- contents hidden inside a closed or opaque container.

Telemetry, research records, and dataset projections are never controller
context merely because they exist.

## Automatic information and memory retrieval

Before invoking an ordinary character controller, the coordinator builds one
deterministic information query from:

- cognition trigger;
- current civil time, when available;
- current environment values;
- current goal descriptions;
- newly perceived fact text;
- sorted present/known target names, kinds, and IDs;
- allowed tool names.

Character target IDs become referenced entity IDs. Other target IDs become
referenced place/resource IDs.

Retrieval is limited to the character's own information namespace and these
document kinds:

- `character.dossier`
- `memory.episode`

Visibility policy is checked before ranking. A character cannot retrieve
another character's private dossier or memory.

### Ranking

Information documents are divided into deterministic content anchors. Candidate
anchors are ranked by:

| Provider state | Score |
| --- | --- |
| Embeddings available | `0.5 * lexical + 0.3 * referenced-ID match + 0.2 * semantic similarity` |
| No embedding provider | `0.7 * lexical + 0.3 * referenced-ID match` |

Results are ordered by descending score, then document kind, document ID, and
source path. The decision retrieval budget is 512 estimated tokens. A first
oversized capsule may be deterministically truncated; later oversized capsules
are skipped.

Each supplied capsule includes:

- document ID and kind;
- source path within the document;
- rendered content;
- provenance and reference IDs;
- valid-time range and recorded time;
- revision and retrieval score.

Retrieved `memory.episode` capsules also provide their summary in the request's
memory list.

If information retrieval fails, the decision fails explicitly with
`information_retrieval_failed`; the controller is not invoked with an invented
empty success result.

### Legacy memory-only fallback

If no unified information retriever is configured but an episodic memory store
is available, memory retrieval uses current goals and perceived facts as the
query, returns at most five memories, and ranks them using configured semantic,
recency, and importance weights. The standard runtime uses the unified
information retriever.

## Prompt and model-tool flow

The model request contains four messages:

1. controller rules and authority/privacy constraints;
2. retrieved information capsules, or the full character description when
   retrieval was not performed;
3. the frozen scenario-specific situation;
4. canonical JSON containing trigger, observation, memory summaries,
   information query, and allowed tool names.

The model must return exactly one tool call. Prose alone, zero tool calls, or
multiple tool calls are rejected.

`check_environment` is the only read-only controller tool. A read result is
appended as a tool message and the model receives another round. Read rounds
are bounded by `max_read_tool_rounds` (default one). A final non-read tool call
is still required.

All character requests created in one tick settle behind the global cognition
barrier. Simulation time does not advance while the batch is waiting. Settled
decisions commit in stable order by requested tick, character ID, and decision
ID.

Before intent creation, the coordinator rejects a result if:

- its decision ID is no longer current;
- the character state revision changed;
- System 1 activated while the request was outstanding.

Actions committed after the system phase normally begin deterministic
execution on the following tick.

## Tool reference

### `navigate_to`

```json
{
  "target_id": "known-target-id",
  "preferred_mode": "WALK",
  "reason": "private short reason"
}
```

`preferred_mode` is optional and may be `WALK`, `CYCLE`, `CAR`, or `METRO`.

**Proposal requirements**

- Target is observable/known in the request.
- Target kind is room, zone, station, transaction point, building, outdoor
  place, or physical object.
- A preferred mode, if supplied, is currently listed as available.

**Execution**

- Queues one `NAVIGATE` action.
- The topology planner resolves a deterministic route.
- Physical objects resolve to reachable approach or occupancy poses rather
  than their blocked center.
- Local routing checks the character's complete 5x5-microcell footprint.
- Cross-space routing can combine room movement, portals, entrances, sparse
  city travel, and vehicles.
- A linked closed, unlocked door is opened through a real `OPEN` interaction
  before traversal. A locked door blocks the route.

**Terminal outcomes**

- Completes after authoritative arrival.
- Fails for an unknown/unreachable destination, unavailable mode/transition,
  no path, blocked/locked door, or invalidated topology that cannot be
  replanned.
- May be interrupted or cancelled by System 1 or other lifecycle changes.

Navigation does not itself pick up, use, sit on, or otherwise interact with the
target.

### `perform`

```json
{
  "action": "READ",
  "target_id": "book-id",
  "duration_seconds": 30,
  "reason": "private short reason"
}
```

Available actions are `WORK`, `READ`, `DRINK`, `EAT`, `SLEEP`, and `RELAX`.

| Action | Requirements | Outcome |
| --- | --- | --- |
| `WORK` | Positive `duration_seconds`. Optional target must be a valid reachable work station or the character must be in the targeted zone. | Runs a bounded `WORKING` activity, then completes and restores the previous activity. |
| `READ` | Target and positive duration. Target has `ReadableComponent` and is held or physically reachable/exposed. | Runs a bounded reading action. The readable object is not consumed. |
| `DRINK` | Target and positive duration. Target has a remaining consumable serving and is held or physically reachable/exposed. | Runs `DRINKING`; at completion exactly one serving is atomically consumed and `drink.completed` is emitted. |
| `EAT` | Character is at the approach pose of an available station supporting `EAT`; target may identify it or the lowest stable-ID nearby match is selected. | Runs the station-defined duration and interpolates its configured homeostasis effect. |
| `SLEEP` | Same station approach, support, availability, and capacity requirements for `SLEEP`. | Runs the station-defined duration and applies its configured energy effect. |
| `RELAX` | Same station approach, support, availability, and capacity requirements for `RELAX`. | Runs the station-defined duration and applies its configured stress effect. |

For station affordances, the station definition owns duration and physiological
effect. A controller-supplied duration does not override the configured
affordance duration.

The system rechecks station availability, supported action, approach pose, and
capacity before starting and while progressing.

### `interact_with`

```json
{
  "verb": "PLACE_ON",
  "target_id": "book-id",
  "destination_id": "table-id",
  "slot_id": "surface-1",
  "reason": "private short reason"
}
```

The target must be observable and advertise the requested verb in
`available_interactions`. The tool performs a second schema check through an
immutable `InteractionSpecification`; the domain interaction system then
revalidates all physical conditions.

| Verb | Required arguments and state | Atomic outcome |
| --- | --- | --- |
| `PICK_UP` | Portable target, exposed/reachable, not already held, enough free hands; two-handed objects require both hands. | Changes relation to `HELD_BY`, fills hand slots, assigns custody, and synchronizes the object pose with the carrier. |
| `PUT_DOWN` | Target is held by the actor. | Selects a deterministic collision-free floor pose, changes relation to `ON_FLOOR`, clears custody and occupied hands. |
| `PLACE_ON` | Held target; observable/reachable destination with support capability; optional compatible slot. | Changes relation to `ON_SUPPORT`, reserves the selected slot, clears hands/custody, and updates pose/index state. |
| `PLACE_IN` | Held target; observable/reachable open container; optional compatible slot. | Changes relation to `IN_CONTAINER`, reserves the selected slot, clears hands/custody, and updates exposure/pose state. |
| `OPEN` | Reachable openable target that is closed and unlocked. | Sets open state and updates effective movement/vision obstruction and topology revision. |
| `CLOSE` | Reachable openable target that is open. Restored obstruction must not collide with an entity. | Sets closed state and restores configured obstruction. |
| `SIT` | Actor is standing; reachable target has a compatible unoccupied occupancy slot and pose. | Changes posture to `SITTING` and relation to `OCCUPIES_SLOT`. |
| `LIE_DOWN` | Actor is standing; reachable target has a compatible unoccupied occupancy slot and pose. | Changes posture to `LYING` and relation to `OCCUPIES_SLOT`. |
| `STAND` | Actor is sitting on the specified support and a valid exit pose exists. | Releases the slot, moves to the deterministic exit pose, and changes posture to `STANDING`. |
| `GET_UP` | Actor is lying on the specified support and a valid exit pose exists. | Releases the slot, moves to the deterministic exit pose, and changes posture to `STANDING`. |
| `USE` | Reachable target has `UsableComponent`. | Completes with its configured `use_kind` as interaction evidence. Generic `USE` does not invent additional device-specific state. |

`PLACE_ON` and `PLACE_IN` require `destination_id`. `slot_id` is optional for
placement and posture verbs; when omitted, compatible slots are considered in
stable ID order.

Common interaction failure reasons are:

- target: `unknown_target`, `target_not_observable`,
  `target_not_reachable`, `different_room`;
- capability/state: `capability_missing`, `interaction_not_available`,
  `object_not_portable`, `object_already_held`, `object_not_held`,
  `object_locked`, `object_already_open`, `object_already_closed`,
  `close_blocked`, `use_not_supported`;
- hands/placement: `hands_full`, `destination_required`,
  `destination_not_reachable`, `slot_not_found`, `slot_incompatible`,
  `slot_at_capacity`, `container_closed`, `relation_cycle`;
- posture: `posture_invalid`, `occupancy_pose_unavailable`,
  `exit_pose_unavailable`;
- priority: `system1_preemption`.

No failed or cancelled interaction may leave a partial hand, custody, relation,
slot, posture, open-state, or spatial-index mutation.

### `say`

```json
{
  "target_id": "character-id",
  "text": "Exact words spoken in the world.",
  "reason": "private short reason"
}
```

**Requirements**

- Target is an observable character target.
- Text is non-empty and at most 500 characters.
- No other speech is pending.

**Outcome**

- Queues a standalone `SAY` action and pending speech.
- Emits literal speech into the world.
- Delivery depends on local-map, hearing-range, channel, and perception rules.
- Intended recipient is not guaranteed to hear it.

The private reason is not spoken.

### `wait`

```json
{
  "duration_seconds": 30,
  "reason": "private short reason"
}
```

Duration is from 1 through 600 seconds. It queues a bounded `IDLE` action,
changes activity as applicable, and completes when the deterministic timer
expires. `wait` is embodied in-world idleness and appears in the action
lifecycle.

### `skip`

```json
{
  "reconsider_after_seconds": 60,
  "reason": "private short reason"
}
```

The interval is from 5 through 3600 seconds.

`skip` creates no `ActionInstance` and no physical action. It updates
`next_decision_time`, emits `cognition.skipped`, and prevents immediate
reconsideration until the interval expires or a qualifying environment update
triggers cognition.

Use `skip` when no useful intentional action is needed. Use `wait` when the
character intentionally remains idle in the world for a duration.

### `transact`

```json
{
  "point_id": "transaction-point-id",
  "offer_id": "offer-id",
  "reason": "private short reason"
}
```

**Proposal requirements**

- Point is an observable transaction point.
- Point is currently available.
- Offer is visible and currently appears satisfiable from character and point
  holdings.

**Execution requirements**

- Point and offer still exist and are available.
- Character is at the point's physical approach pose.
- Point capacity is available.
- Character can debit everything it gives.
- Point can debit everything the character receives.
- A staffed point has an assigned, present NPC and explicit authorization.

**Outcome**

After the configured duration, both holdings ledgers are rechecked and updated
atomically. Completion records before/after holdings and transferred amounts.
No partial exchange occurs.

Staffed requests may wait for assignment/authorization and fail with an
explicit service timeout.

### `check_environment`

```json
{
  "topics": ["time", "weather", "surface_conditions", "availability"]
}
```

This is a read-only model-tool round, not an in-world action. It returns the
requested subset of currently available environment values and a sorted
`unavailable_topics` list.

It does not queue an action, advance simulation time, or prove that a resource
will remain available. The model must still return one final non-read tool
within the read-round limit.

### `serve_transaction`

```json
{
  "request_id": "transaction-request-id",
  "reason": "private short reason"
}
```

This tool is available only to service NPCs.

**Requirements**

- Request is assigned to the NPC and observable in `service_requests`.
- Request is awaiting authorization.
- NPC role, staffed point, customer, and staff/customer approach positions
  still match.

**Outcome**

The NPC authorizes the request and completes its own
`SERVE_TRANSACTION` action. Authorization does not itself transfer holdings;
the customer's transaction state machine performs the later atomic exchange.

## Internal action vocabulary

Runtime plans use:

`WORK`, `SOCIALIZE`, `READ`, `DRINK`, `EAT`, `SLEEP`, `RELAX`, `IDLE`,
`NAVIGATE`, `INTERACT`, `TRANSACT`, and `SERVE_TRANSACTION`.

Not every internal action has a direct ordinary-controller tool:

- `SOCIALIZE` remains available to authored plans/internal social behavior; the
  controller communicates literal speech through `say`.
- `SAY` is represented as a standalone action instance rather than an
  `ActionType`.
- `skip` deliberately creates no action instance.
- `check_environment` is a read execution, not an action.

## Complete technical flow

### 1. Ordered systems create current evidence

At each fixed tick, deterministic systems update weather, availability,
navigation, plans, pathfinding, interactions, physiology, System 1, speech,
travel, affordances, transactions, movement, perception, goals, memory, and
finally cognition scheduling according to their registered numeric order.

Perception and memory work therefore consume authoritative state/events rather
than controller claims.

### 2. Scheduler freezes a decision request

If the eligibility gates pass, the scheduler:

- assigns a stable decision ID;
- captures controller `state_revision`;
- builds the observer-specific character observation;
- records offered tools and fact IDs;
- marks the controller request pending;
- submits the immutable request to the coordinator.

### 3. Coordinator retrieves information

For an ordinary character, the coordinator constructs the bounded query
described above, retrieves accessible dossier/memory capsules, extracts memory
summaries, records retrieval traces, and enriches the request.

NPCs do not run this general dossier/memory retrieval path. They receive their
restricted role/situation, staffed point, visible characters, and assigned
service requests.

### 4. Model selects a tool

The controller sends provider-neutral messages and strict JSON tool schemas to
the configured scripted, replay, or opt-in real model client.

The controller may execute bounded `check_environment` rounds. It eventually
returns exactly one final tool call or an explicit model/controller error.

### 5. Global barrier settles the batch

The runner waits for the complete cognition batch without advancing simulation
time. Provider timeout, malformed output, cancellation, and budget exhaustion
are explicit failures.

Completed decisions are sorted deterministically before application.

### 6. Application validates and commits an intent

For each result, the coordinator:

1. emits `cognition.completed`;
2. records any read-tool requests/results;
3. emits `tool.proposed`;
4. checks decision freshness and System 1;
5. validates tool offering, strict arguments, target references, visible
   availability, and advertised interactions;
6. creates an immutable typed intent;
7. emits `tool.accepted`;
8. queues an action/request or updates skip timing;
9. increments controller state revision;
10. emits `tool.committed`.

If any check fails, it emits `tool.rejected` and queues no successful action.

### 7. Domain systems execute and revalidate

On subsequent system updates:

- plan execution emits `action.started`;
- navigation plans and follows live topology;
- interactions use current physical state and commit atomically;
- affordances apply configured progress/effects;
- transactions recheck and transfer holdings atomically;
- speech routes literal words through perception;
- timed actions count down against fixed `dt`.

Terminal domain results are:

- `action.completed`
- `action.failed`
- `action.cancelled`

Subsystem lifecycles such as `navigation.*`, `interaction.*`,
`affordance.*`, `transaction.*`, and `speech.*` provide detailed evidence.

### 8. Outcomes become future context

Committed events update:

- current ECS state;
- observer-specific perceptible facts;
- goal evidence and progress;
- episodic-memory work;
- controller `recent_outcome`;
- operator telemetry and research data.

Only the privacy-safe perception/memory/information paths can return that
evidence to a later controller decision. Dataset or telemetry visibility never
automatically becomes character knowledge.

## Failure stages

| Stage | Representative failures |
| --- | --- |
| Eligibility | controller disabled, pending request, plan active, System 1 active, budget exhausted |
| Retrieval/provider | embedding failure, provider timeout/error, cancellation |
| Model contract | zero/multiple tools, prose-only answer, read-round limit |
| Tool proposal | tool not offered, unknown tool, strict schema error, unobservable target, unavailable offer/mode/interaction |
| Freshness | stale decision ID/revision, System 1 activated before commit |
| Action start | target missing, no path, wrong approach pose, unsupported capability, unavailable/capacity conflict |
| Execution | topology changed, movement conflict, slot filled, container closed, door locked, holdings changed, service timeout |
| Cancellation | System 1 preemption, simulation stop, incompatible plan clearing |

Failures remain failure-shaped. The system does not silently replace a failed
request with waiting, success, or another target.

## Event and lineage tracing

The same decision can be traced through:

```text
cognition.eligible
  -> cognition.requested
  -> information.retrieved
  -> cognition.completed
  -> tool.proposed
  -> tool.accepted | tool.rejected
  -> tool.committed
  -> plan/action/subsystem events
  -> action.completed | action.failed | action.cancelled
```

Structured IDs connect the decision, model request, tool call, plan, action,
interaction, causation, and root correlation. Diagnostic text is not the
machine-readable contract.

## Implementation map

| Concern | Primary implementation |
| --- | --- |
| Tool schemas and proposal validation | `src\stage0_sim\application\agents\tools.py` |
| Decision observation | `src\stage0_sim\application\agents\context.py` |
| Cognition eligibility | `src\stage0_sim\application\agents\scheduler.py` |
| Retrieval, barrier application, freshness, intent commit | `src\stage0_sim\application\agents\coordinator.py` |
| Prompt and information rendering | `src\stage0_sim\application\agents\prompts.py` |
| Read-tool/model rounds | `src\stage0_sim\application\agents\controller.py` |
| Information ranking | `src\stage0_sim\application\information\retrieval.py` |
| Episodic-memory ranking | `src\stage0_sim\application\memory.py` |
| Plan and timed-action execution | `src\stage0_sim\domain\systems\plans.py` |
| Physical interactions | `src\stage0_sim\domain\systems\interactions.py` |
| Navigation and movement | `src\stage0_sim\domain\systems\navigation.py` |
| Affordances | `src\stage0_sim\domain\systems\affordances.py` |
| Transactions | `src\stage0_sim\domain\systems\transactions.py` |
| Perception | `src\stage0_sim\application\perception\system.py` |

See [Actions, tools, and events](ACTIONS_AND_EVENTS.md) for the compact closed
vocabulary and [Runtime semantics](RUNTIME.md) for tick ordering, System 1, and
determinism.
