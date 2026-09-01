# Actions, Tools, and Events

**Owner:** Closed controller-tool, action, goal-criterion, and domain-event
vocabulary.

## Character-controller tools

| Tool | Arguments | Meaning |
| --- | --- | --- |
| `navigate_to` | `target_id`, optional `preferred_mode`, optional private `reason` | Attempt navigation to a known zone, station, building, or outdoor place |
| `perform` | `action`, optional `target_id`, `duration_seconds`, `reason` | Attempt `WORK`, `READ`, `EAT`, `SLEEP`, or `RELAX` |
| `say` | `target_id`, `text`, optional `reason` | Speak literal in-world words |
| `wait` | `duration_seconds`, optional `reason` | Create an embodied `IDLE` action |
| `skip` | `reconsider_after_seconds`, optional `reason` | Create no action; defer cognition eligibility |
| `transact` | `point_id`, `offer_id`, optional `reason` | Attempt one configured exchange |
| `check_environment` | `topics` | Read available time/weather/surface/availability information; read-only |
| `serve_transaction` | `request_id`, optional `reason` | NPC-only authorization for an assigned staffed request |

Travel modes are `WALK`, `CYCLE`, `CAR`, and `METRO`. Reasons are private
controller metadata. A tool proposal, acceptance, or commit never proves a
physical outcome.

Scenario-level and ordinary character-controller allowlists may include all
tools above except `serve_transaction`; NPC roles may include only
`serve_transaction`, `say`, `wait`, and `skip`.

## Plan actions

The complete `ActionType` vocabulary is:

`WORK`, `SOCIALIZE`, `READ`, `EAT`, `SLEEP`, `RELAX`, `IDLE`, `NAVIGATE`,
`TRANSACT`, and `SERVE_TRANSACTION`.

`NAVIGATE` is the only navigation action. `TRANSACT` requires a target point
and offer ID. `SERVE_TRANSACTION` requires a transaction-request target.
Runtime queues contain `ActionInstance`, not raw compatibility actions.

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
| Plans/actions | `plan.created`, `plan.revised`, `plan.cleared`, `action.queued`, `action.started`, `action.completed`, `action.failed`, `action.cancelled` |
| Physiology/System 1 | `activity.changed`, `homeostasis.changed`, `homeostasis.mutated`, `threshold.breached`, `system1.activated`, `system1.drive_changed`, `system1.target_selected`, `system1.state_changed`, `system1.resolved`, `system1.blocked` |
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
