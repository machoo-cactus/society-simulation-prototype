# Actions, Tools, and Events

**Owner:** Closed controller-tool, action, goal-criterion, and domain-event
vocabulary.

For complete per-tool requirements, outcomes, failure stages, observation
contents, retrieval behavior, and the controller-to-domain execution flow, see
[Character agent actions and decision flow](CHARACTER_AGENT_ACTIONS.md).

## Character-controller tools

| Tool | Arguments | Meaning |
| --- | --- | --- |
| `navigate_to` | `target_id`, optional `preferred_mode`, optional private `reason` | Attempt navigation to a known place or physical-object approach pose |
| `perform` | `action`, optional `target_id`, `duration_seconds`, `reason` | Attempt `WORK`, `READ`, `DRINK`, `EAT`, `SLEEP`, or `RELAX` |
| `interact_with` | closed `verb`, `target_id`, optional `destination_id` and `slot_id`, optional private `reason` | Propose one observable physical interaction |
| `say` | `target_id`, `text`, optional `reason` | Speak literal in-world words |
| `engage` | `intent`, `reference_ids`, optional private `reason` | Attempt a free-form behavior only when no specialized offered tool is exact |
| `wait` | `duration_seconds`, optional `reason` | Create an embodied `IDLE` action |
| `skip` | `reconsider_after_seconds`, optional `reason` | Create no action; defer cognition eligibility |
| `transact` | `point_id`, `offer_id`, optional `reason` | Attempt one configured exchange |
| `check_environment` | `topics` | Read available time/weather/surface/availability information; read-only |
| `read_text` | `target_id`, `endpoint_id`, `artifact_id`, optional `block_ids`, optional private `reason` | Attempt an embodied read of an advertised text endpoint |
| `write_text` | strict operation-discriminated create/append/replace/edit/delete arguments, endpoint IDs, expected revisions, attribution request, optional private `reason` | Attempt an embodied deterministic text mutation or one-recipient in-world message |
| `serve_transaction` | `request_id`, optional `reason` | NPC-only authorization for an assigned staffed request |

Travel modes are `WALK`, `CYCLE`, `CAR`, and `METRO`. Reasons are private
controller metadata. A tool proposal, acceptance, or commit never proves a
physical outcome.

Scenario-level and ordinary character-controller allowlists may include all
tools above except `serve_transaction`; NPC roles may include only
`serve_transaction`, `say`, `wait`, and `skip`.

Specialized tools are preferred: ordinary speech uses `say`, advertised
physical verbs use `interact_with`, established activities use `perform`, and
configured exchanges use `transact`. `engage` is the supported residual path,
not a way to bypass those contracts. Its schema stays limited to `intent`,
`reference_ids`, and private `reason`; compiler capabilities evolve downstream.

## Plan actions

The complete `ActionType` vocabulary is:

`WORK`, `SOCIALIZE`, `READ`, `READ_TEXT`, `WRITE_TEXT`, `DRINK`, `EAT`,
`SLEEP`, `RELAX`, `IDLE`, `NAVIGATE`, `INTERACT`, `ENGAGE`, `TRANSACT`, and
`SERVE_TRANSACTION`.

`NAVIGATE` is the only navigation action. `TRANSACT` requires a target point
and offer ID. `SERVE_TRANSACTION` requires a transaction-request target.
Runtime queues contain `ActionInstance`, not raw compatibility actions.

Physical interaction verbs are `PICK_UP`, `PUT_DOWN`, `PLACE_ON`, `PLACE_IN`,
`OPEN`, `CLOSE`, `SIT`, `STAND`, `LIE_DOWN`, `GET_UP`, `USE`, `EQUIP`, and
`UNEQUIP`.

| Verb | Deterministic effect attempted |
| --- | --- |
| `PICK_UP`, `PUT_DOWN` | Move a portable object between a free hand/custody relation and a collision-free floor pose |
| `PLACE_ON`, `PLACE_IN` | Move a held object to a compatible support/container slot; a closed container rejects placement |
| `OPEN`, `CLOSE` | Change an openable object's effective obstruction; locked objects do not open and blocked objects do not close |
| `SIT`, `LIE_DOWN` | Occupy a compatible slot and adopt the requested posture |
| `STAND`, `GET_UP` | Leave the current sitting or lying support for a valid exit pose |
| `USE` | Invoke an explicitly configured usable capability |
| `EQUIP`, `UNEQUIP` | Move a wearable between held state and a compatible character equipment slot; effective typed sense modifiers are derived after commit |

Action origins are `scenario`, `controller`, `system1`, and `operator`.
Goal links are `declared` or `contextual`.

## Action and plan lifecycle

```text
plan.created | plan.revised
  -> action.queued
  -> action.started
  -> action.completed | action.failed | action.cancelled
```

`plan.cleared` closes incompatible plan state. The `action.*` family is the
canonical action lifecycle.

Physical interaction evidence has its own exact lifecycle:

```text
interaction.requested
  -> interaction.failed
  |  interaction.started
       -> interaction.completed | interaction.cancelled
```

The request specification and action/decision/tool lineage are immutable.
Execution revalidates live ECS and `SpatialIndex` state before its atomic
deterministic commit. A queued, accepted, or committed tool is not proof that
an interaction started or completed. Door objects linked to entrances or
portals use these same events and rules; closed unlocked doors can be opened
before traversal, while locked doors block it.

Text execution has separate exact lifecycles while retaining the canonical
action lifecycle:

```text
text.read_requested
  -> text.read_started
       -> text.read_completed | text.read_failed | text.read_cancelled

text.write_requested
  -> text.write_started
       -> text.write_completed | text.write_failed | text.write_cancelled

text.delivery_requested
  -> text.delivery_completed | text.delivery_failed
```

Ordinary event payloads contain safe IDs, operation names, revisions, hashes,
lengths, status, and policy-approved displayed attribution. Text bodies,
deleted text, and authoritative anonymous-author IDs are private.

Generic engagement has a separate compilation/execution family while retaining
the canonical `action.*` lifecycle:

```text
engagement.requested
  -> engagement.compilation_requested
       -> engagement.compilation_failed | engagement.compilation_cancelled
       |  engagement.compilation_completed
            -> engagement.started
                 -> engagement.group_completed | engagement.group_failed
                 -> engagement.completed | engagement.partial
                    | engagement.failed | engagement.cancelled
```

`engagement.capability_committed` is emitted for each committed invocation.
Groups are required-atomic: every invocation in a group validates before any
of that group commits. Separate groups are independent and ordered, so one may
complete while another fails; this produces `engagement.partial`. The
associated `ENGAGE` action completes when at least one group committed, fails
when none committed, and cancels on System 1, stop, or lifecycle interruption.
Compiler acceptance, proposed summary, or capability selection is not proof
that any group started or effect succeeded.

## Structured goal criteria

Criteria are `event_match`, `state_comparison`, `location_match`,
`possession_threshold`, `action_outcome`, `interaction_count`, and
`simulation_time`. Goal lifecycle events are `goal.activated`,
`goal.progressed`, `goal.succeeded`, `goal.failed`, `goal.expired`, and
`goal.retired`. `goal.finalized` is a dataset finalization projection, not an
authored event target.

## Domain-event families

These names are current. Payloads are structured and may include event,
causation, correlation, entity, plan, action, decision, tool, interaction, and
other typed lineage IDs.

| Family | Events |
| --- | --- |
| Run | `simulation.started`, `simulation.paused`, `simulation.resumed`, `simulation.speed_changed`, `simulation.tick`, `simulation.stopped` |
| Cognition | `cognition.eligible`, `cognition.requested`, `cognition.barrier_started`, `cognition.barrier_settled`, `cognition.completed`, `cognition.failed`, `cognition.cancelled`, `cognition.skipped`, `cognition.budget_exhausted` |
| Tools/information | `tool.proposed`, `tool.accepted`, `tool.rejected`, `tool.committed`, `tool.read_requested`, `tool.read_completed`, `information.retrieved`, `information.retrieval_failed` |
| Text content | `text.read_requested`, `text.read_started`, `text.read_completed`, `text.read_failed`, `text.read_cancelled`, `text.write_requested`, `text.write_started`, `text.write_completed`, `text.write_failed`, `text.write_cancelled`, `text.delivery_requested`, `text.delivery_completed`, `text.delivery_failed` |
| Plans/actions | `plan.created`, `plan.revised`, `plan.cleared`, `action.queued`, `action.started`, `action.completed`, `action.failed`, `action.cancelled` |
| Engagements | `engagement.requested`, `engagement.compilation_requested`, `engagement.compilation_completed`, `engagement.compilation_failed`, `engagement.compilation_cancelled`, `engagement.started`, `engagement.group_completed`, `engagement.group_failed`, `engagement.capability_committed`, `engagement.completed`, `engagement.partial`, `engagement.failed`, `engagement.cancelled` |
| Physical interactions | `interaction.requested`, `interaction.started`, `interaction.completed`, `interaction.failed`, `interaction.cancelled`, `drink.completed` |
| Physiology/System 1 | `activity.changed`, `homeostasis.changed`, `homeostasis.mutated`, `character.effects_changed`, `threshold.breached`, `system1.activated`, `system1.drive_changed`, `system1.target_selected`, `system1.state_changed`, `system1.resolved`, `system1.blocked` |
| Local movement | `path.requested`, `path.planned`, `path.completed`, `path.failed`, `path.invalidated`, `agent.moved`, `portal.traversed` |
| Navigation/travel | `navigation.requested`, `navigation.planned`, `navigation.leg_started`, `navigation.arrived`, `navigation.failed`, `navigation.interrupted`, `travel.requested`, `travel.route_planned`, `travel.route_failed`, `travel.started`, `travel.leg_started`, `travel.leg_completed`, `travel.progressed`, `travel.mode_changed`, `travel.blocked`, `travel.interrupted`, `travel.arrived` |
| Places/vehicles | `building.entered`, `building.exited`, `building.entry_blocked`, `vehicle.boarded`, `vehicle.moved`, `vehicle.exited`, `metro.boarded`, `metro.alighted` |
| Affordances | `affordance.started`, `affordance.progressed`, `affordance.completed`, `affordance.failed`, `affordance.cancelled` |
| Transactions/NPCs | `transaction.requested`, `transaction.awaiting_staff`, `transaction.staff_assigned`, `transaction.authorized`, `transaction.started`, `transaction.progressed`, `transaction.completed`, `transaction.failed`, `transaction.cancelled`, `transaction.timed_out`, `npc.spawned`, `npc.spawn_blocked` |
| Speech/perception/memory | `speech.started`, `speech.delivered`, `speech.failed`, `perception.delivered`, `perception.dropped`, `memory.requested`, `memory.recorded`, `memory.failed`, `memory.cancelled` |
| Environment | `time.updated`, `weather.changed`, `surface_condition.changed`, `availability.changed` |

Failure-shaped events remain failures; do not replace them with success-shaped
fallbacks. Event text is diagnostic only—the payload and lineage are the
machine-readable contract.
