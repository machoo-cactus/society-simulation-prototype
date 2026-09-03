# Runtime Semantics

**Owner:** Run lifecycle, fixed-tick ordering, cognition settlement, System 1,
action execution, perception, and determinism.

## Lifecycle

Scenario authoring, staging, and execution are separate:

1. A schema-version-9 source is validated.
2. Character assignments and element references are resolved and frozen.
3. Optional character-situation synthesis completes transactionally.
4. A prepared scenario is staged without starting or advancing time.
5. Start creates a new runner.
6. Pause, resume, single-step, speed change, and stop are explicit operations.

Stopping finalizes research capture. A manual checkpoint may be saved only
while the runner is paused, cognition is idle, no tick is advancing, and all
provider, memory, and engagement queues are empty. Checkpoints restore paused;
resume remains a separate operator action.

An exact persisted head checkpoint can suspend on graceful shutdown and reclaim
the same run and dataset after restart. Restoring any historical or otherwise
non-resumable checkpoint creates a new branch run. Branches retain root run,
immediate parent run, and parent checkpoint provenance without truncating or
rewriting the source dataset.

Checkpoint restoration fails closed on integrity errors, unsupported
checkpoint or runtime compatibility versions, incompatible authoritative type
or ordered-system manifests, and persisted dataset-head mismatches. Frozen
prepared inputs are restored directly rather than re-resolved from mutable
libraries.

## One tick

`SimulationRunner.advance_one_tick()` performs:

```text
advance fixed simulation clock
capture tick_pre_systems
run registered systems by numeric order, then registration order
capture tick_post_systems
drain memory work
settle the complete cognition batch behind the global barrier
commit settled decisions in stable order
settle engagement compilation work created by those decisions
emit simulation.tick
capture tick_post_cognition
notify tick-completed handlers
```

Provider latency can extend wall-clock duration, but simulation time remains
frozen at the barrier. The global barrier is the only cognition timing model.

## Ordered systems

Only systems applicable to the materialized scenario are registered. Current
orders are:

| Order | System |
| ---: | --- |
| 70 | `weather_update` |
| 75 | `surface_conditions` |
| 80 | `environment_availability` |
| 85 | `navigation_planning` |
| 90 | `plan_execution` |
| 100 | `pathfinding` |
| 145 | `interaction_execution` |
| 147 | `character_effect_resolution` |
| 150 | `movement_activity` |
| 160 | `homeostasis` |
| 165 | `timed_plan_action` |
| 170 | `system1_arbitration` |
| 175 | `speech`, then `travel` when both are registered |
| 180 | `affordance_execution` |
| 185 | `transaction_execution` |
| 190 | `npc_staffing` |
| 200 | `movement` |
| 240 | `calendar_update`, then `navigation_knowledge_recording` when both are registered |
| 245 | `engagement_execution` |
| 250 | `perception` |
| 280 | `goal_evaluation` |
| 290 | `memory_recording` |
| 310 | `cognition_scheduler` |

Registration order breaks equal numeric orders and is therefore semantic.

## Cognition barrier and controller boundary

A character controller receives bounded self-state, observer-specific
perception, timestamped knowledge, retrieved information/memories, goals,
observable targets, and allowed tool schemas. It returns exactly one tool call.
Read-only `check_environment` rounds are bounded; a final state-changing tool is
still required.

The application validates tool offering, schema, references, current state,
decision ID, and state revision, then creates an immutable intent. A committed
tool is not a successful action. Domain systems alone produce terminal
`action.completed`, `action.failed`, or `action.cancelled` outcomes.

Requests created in one tick run concurrently up to the configured limit.
Nothing commits before the batch settles, and commits use stable decision
ordering. Timeout, malformed output, budget exhaustion, stop cancellation, and
stale results are explicit. Correctness never depends on successful network
cancellation.

### Generic engagement compilation

Controllers prefer an exact specialized tool. `engage` is the first-class
fallback and accepts only attempted `intent`, observable `reference_ids`, and
optional private `reason`. The request creates a normal `ENGAGE`
`ActionInstance`; it does not bypass plan/action lineage.

After settled controller decisions commit, the same frozen global barrier
drains the separate engagement compiler queue. The compiler uses the configured
`ModelClient` but a distinct `engagement_compilation.v1` prompt, model profile,
timeout, concurrency, and request/input/output budgets. Exactly one
`compile_engagement` tool call is accepted. The compiler may return a compiled
program, `specialized_tool_required`, or `unsupported`; none is converted into
a success-shaped fallback. Recording/replay includes controller and compiler
turns.

Compiled programs currently use `expressive_behavior`,
`auditory_expression`, and `bounded_activity`. Compiler selection is
stochastic; application schema/reference validation and domain execution are
deterministic. A required-atomic group is revalidated as a unit immediately
before commit. Separate valid groups execute in stable order, so some may
complete while later groups fail; terminal engagement/action outcomes then
report completed, partial, failed, or cancelled state. A partial engagement is
still a completed `ENGAGE` action because at least one group committed; a
fully failed engagement produces `action.failed`.

System 1 activation, stop, stale state, or lost action lineage cancels pending
compilation or execution and clears incompatible engagement state. An
initiator may attempt an engagement affecting another character without target
consent. The target receives only domain-resolved immediate effects and
observer-specific evidence; future cooperation remains that character
controller's decision.

## System 1

System 1 is deterministic survival arbitration with absolute priority.
Satiety and energy are critical when low; stress is critical when high.
Hydration may be enabled per scenario and is critical when low. Happiness,
social connection, and fear remain non-preemptive controller context because
the runtime does not yet define danger sources or seek-safety behavior.
Enabled drives, threshold and recovery values, and tie-break order are
scenario-configurable and use hysteresis.

When critical, System 1:

1. records the breach;
2. clears incompatible actions, movement, and cognition;
3. selects a drive by severity and stable tie-break order;
4. selects a reachable corrective station by path cost, capacity, and stable ID;
5. owns the corrective navigation/action until recovery;
6. emits `system1.blocked` if correction cannot proceed.

A character controller cannot override this workflow.

## Actions, navigation, and interactions

Runtime queues contain `ActionInstance` values with immutable specifications,
origin, plan revision, causal identity, optional goal links, and decision/tool
lineage. `NAVIGATE` compiles a known-topology route into local movement, portal,
and sparse travel primitives. Execution always revalidates against
authoritative topology.

Affordances and transactions are deterministic state machines. Transactions
recheck volatile preconditions immediately before an atomic transfer. Staffed
points require a run-scoped NPC authorization through the same tool, intent,
plan, and validation boundary.

Physical interactions follow the same rule. A committed `INTERACT` action
retains its immutable verb, target, destination, slot, action, decision, and
tool lineage. `InteractionExecutionSystem` emits
`interaction.requested`, revalidates current observability, reachability,
capability, hands, posture, slot capacity, relation acyclicity, open/locked
state, and collision constraints, then emits `interaction.started`. Completion
performs one deterministic state transition across the relevant ECS
components and `SpatialIndex`; changed preconditions or preemption produce
`interaction.failed` or `interaction.cancelled` rather than partial success.

`PICK_UP`, `PUT_DOWN`, `PLACE_ON`, and `PLACE_IN` update live parent relations,
hands, custody, slots, pose, and index membership. `OPEN` and `CLOSE` update
effective movement/vision/hearing/smell obstruction; a locked object cannot be opened, and
closing fails if restoring its obstruction would collide. `SIT`, `STAND`,
`LIE_DOWN`, and `GET_UP` update posture and occupancy relations only when a
valid occupancy or exit pose exists. `USE` requires an explicit usable
capability.

`EQUIP` moves a held wearable into a compatible character equipment slot using
a live slotted `ATTACHED_TO` relation. `UNEQUIP` requires enough free hands and
returns it to `HELD_BY`. The order-147 resolver derives equipment state,
carried mass, and effective vision, recognition, hearing, and smell ranges from
live relations. Known mass can reject pickup when single-object or total-load
limits would be exceeded; unknown migrated mass preserves legacy eligibility.

Entrances and portals may link to a door object. Navigation compiles an
`OPEN` interaction before the linked traversal. A closed unlocked door must
open successfully; a locked door blocks the route. Traversal still revalidates
the live topology and availability instead of trusting immutable `CityWorld`
metadata.

Text reads and writes are embodied deterministic actions. `READ_TEXT` pins the
current artifact revision when execution starts, consumes simulation time,
revalidates endpoint and policy access, then creates a private read receipt for
the character's next decision. `WRITE_TEXT` carries immutable proposed text
and expected artifact, block, and collection revisions. The text execution
system commits only after final live validation; stale revisions, lost
physical access, policy changes, capacity conflicts, System 1, or stop produce
explicit failure or cancellation.

Message sending is an in-world atomic write across one message artifact,
recipient mailbox membership, sender sent-mail membership, and unread state.
It does not call an external provider and does not inject the body directly
into recipient perception.

Local physical room grids use 9 microcells per legacy cell. Anchors, paths,
footprints, and occupied cells are microcells; compatibility positions may be
reported in legacy cells. Characters use a fixed 5×5-microcell body footprint
and cardinal orientation. Semantic mass and dimensions do not alter the rendered footprint. The model is
discrete and does not implement continuous rigid-body dynamics, torque,
arbitrary rotation, or weight-driven movement/physiology.

## Perception and privacy

Three representations remain distinct:

1. authoritative event/state;
2. privacy-safe perceptible fact;
3. observer-specific perceived fact.

The safe disclosure default is admin-only. Other characters may perceive
execution evidence—movement, visible activity, delivered speech, public time or
weather—but not private destinations, reasons, plans, vitals, drives, prompts,
profiles, memories, or model content. Speech communicates literal words only
through `say`; intended recipients are not guaranteed to hear them.

Vision, speech hearing, and object scent use stable footprint-aware supercover
sweeps. Blocked room cells block all three modalities. Physical structures may
independently pass or block each modality: for example, a window may pass
vision while blocking hearing and smell. Mirrors are opaque recognizable
surfaces and do not create reflected observations. Scent sources are
room-local, range-bounded, and blocked structurally; diffusion, airflow,
attenuation, and lingering fields are not modeled.

Engagement auditory effects reuse the domain hearing sweep. The compiler does
not choose actual recipients: live range, listener senses, local place, body
footprints, and hearing obstruction do. Only resolved recipients receive
bounded listener effects, heard facts, and resulting memories. Visual
engagement evidence likewise requires observer-specific visibility.

Nearby observers may perceive that a character is reading or writing and the
involved visible object. Text bodies, deleted revisions, mailbox contents, and
the authoritative actor behind reader-visible anonymous content remain
private. Metadata-only message-arrival notifications are delivered only to an
authorized mailbox owner.

## Determinism

Fixed `dt`, stable iteration, ordered systems, deterministic pathfinding, stable
conflict resolution, and deterministic commit order define canonical execution.
Wall timestamps, live provider text, provider IDs, latency, and external
failures are nondeterministic inputs and are recorded as such. Recording/replay
is required to reproduce live-provider choices.

Tier 2+ consequences remain outside the current engagement catalog: injury,
theft or custody transfer, forced movement, relationships or reputation, and
arbitrary object/component mutation require dedicated domain models and cannot
be established by compiler prose.

See [Actions, tools, and events](ACTIONS_AND_EVENTS.md) for the closed
vocabulary.
