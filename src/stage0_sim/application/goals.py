from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from stage0_sim.domain.components import (
    ActionOutcomeCriterion,
    ActivityComponent,
    ControllerComponent,
    EventMatchCriterion,
    GoalComparator,
    GoalCompletionPolicy,
    GoalComponent,
    GoalCriterion,
    GoalCriterionEffect,
    GoalEvidence,
    GoalRuntime,
    GoalStatus,
    HomeostasisComponent,
    InteractionCountCriterion,
    InteractionType,
    LocationMatchCriterion,
    PositionComponent,
    PossessionsComponent,
    PossessionThresholdCriterion,
    SimulationTimeCriterion,
    SpatialLocationComponent,
    StateComparisonCriterion,
)
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import CityWorld

_MISSING = object()
_STATE_FIELDS = {
    "homeostasis": frozenset({"satiety", "energy", "stress"}),
    "activity": frozenset({"current"}),
    "controller": frozenset(
        {"enabled", "state_revision", "last_outcome", "request_pending"}
    ),
}
_INTERACTION_EVENTS = {
    InteractionType.SPEECH: "speech.started",
    InteractionType.TRANSACTION: "transaction.completed",
}


@dataclass(slots=True)
class GoalEvaluationSystem:
    """Evaluate goals after physical/perception systems without changing them.

    Order 280 is after authoritative physical transitions and perception (250)
    and before memory/controller scheduling (290+). The system mutates
    only GoalComponent runtime state and emits structured lifecycle events.
    """

    name: str = "goal_evaluation"
    order: int = 280
    _event_cursor: int = 0

    def update(self, context: SystemContext) -> None:
        events = context.events.events[self._event_cursor :]
        self._event_cursor = len(context.events.events)
        for character_id, component in context.registry.query(GoalComponent):
            for goal in component.goals:
                self._evaluate_goal(context, character_id, goal, events)

    def _evaluate_goal(
        self,
        context: SystemContext,
        character_id: str,
        goal: GoalRuntime,
        events: tuple[DomainEvent, ...],
    ) -> None:
        if goal.status in {
            GoalStatus.SUCCEEDED,
            GoalStatus.FAILED,
            GoalStatus.EXPIRED,
            GoalStatus.RETIRED,
            GoalStatus.UNKNOWN,
        }:
            return
        definition = goal.definition
        now = context.clock.simulation_time
        if goal.status is GoalStatus.PENDING:
            if definition.activation_time is not None and now < definition.activation_time:
                return
            self._transition(
                context,
                character_id,
                goal,
                GoalStatus.ACTIVE,
                "goal.activated",
                {"reason": "activation_window_open"},
            )
        if not definition.criteria:
            return

        failure_matched = False
        newest_evidence: GoalEvidence | None = None
        newest_success_evidence: GoalEvidence | None = None
        for index, criterion in enumerate(definition.criteria, start=1):
            criterion_id = f"criterion-{index}"
            progress, evidence_context = self._criterion_progress(
                context,
                character_id,
                goal,
                criterion_id,
                criterion,
                events,
            )
            goal.criterion_progress[criterion_id] = progress
            if evidence_context is not None:
                newest_evidence = GoalEvidence(
                    criterion_id=criterion_id,
                    criterion_type=criterion.criterion_type,
                    simulation_tick=context.clock.tick,
                    simulation_time=now,
                    context=evidence_context,
                )
                goal.evidence.append(newest_evidence)
                if criterion.effect is GoalCriterionEffect.SUCCESS:
                    newest_success_evidence = newest_evidence
            if (
                criterion.effect is GoalCriterionEffect.FAILURE
                and progress >= 1.0
            ):
                failure_matched = True

        success_progress = [
            goal.criterion_progress.get(f"criterion-{index}", 0.0)
            for index, criterion in enumerate(definition.criteria, start=1)
            if criterion.effect is GoalCriterionEffect.SUCCESS
        ]
        previous_progress = goal.progress
        if success_progress:
            goal.progress = (
                sum(success_progress) / len(success_progress)
                if definition.completion_policy is GoalCompletionPolicy.ALL
                else max(success_progress)
            )
        if goal.progress > previous_progress:
            self._emit(
                context,
                character_id,
                goal,
                "goal.progressed",
                {
                    "previous_progress": previous_progress,
                    "evidence": (
                        _evidence_payload(newest_success_evidence)
                        if newest_success_evidence is not None
                        else None
                    ),
                },
                previous_status=goal.status,
            )
        if failure_matched:
            self._transition(
                context,
                character_id,
                goal,
                GoalStatus.FAILED,
                "goal.failed",
                {"reason": "failure_criterion_matched"},
            )
            return
        if success_progress and goal.progress >= 1.0:
            self._transition(
                context,
                character_id,
                goal,
                GoalStatus.SUCCEEDED,
                "goal.succeeded",
                {"reason": "completion_policy_satisfied"},
            )
            return
        if definition.deadline_time is not None and now >= definition.deadline_time:
            self._transition(
                context,
                character_id,
                goal,
                GoalStatus.EXPIRED,
                "goal.expired",
                {"reason": "deadline_reached"},
            )

    def _criterion_progress(
        self,
        context: SystemContext,
        character_id: str,
        goal: GoalRuntime,
        criterion_id: str,
        criterion: GoalCriterion,
        events: tuple[DomainEvent, ...],
    ) -> tuple[float, dict[str, JsonValue] | None]:
        if criterion_id in goal.matched_criteria:
            return 1.0, None
        if isinstance(criterion, EventMatchCriterion):
            event = next(
                (
                    item
                    for item in events
                    if item.event_type == criterion.event_type
                    and item.agent_id in {None, character_id}
                    and _is_payload_subset(criterion.payload_subset, item.payload)
                ),
                None,
            )
            return self._event_result(goal, criterion_id, event)
        if isinstance(criterion, StateComparisonCriterion):
            value = _state_value(context, character_id, criterion)
            matched = value is not _MISSING and _compare(
                value, criterion.comparator, criterion.value
            )
            if matched:
                goal.matched_criteria.add(criterion_id)
                return 1.0, {
                    "component": criterion.component.value,
                    "field": criterion.field,
                    "value": cast(JsonValue, value),
                }
            return 0.0, None
        if isinstance(criterion, LocationMatchCriterion):
            matched, observed = _location_matches(
                context, character_id, criterion
            )
            if matched:
                goal.matched_criteria.add(criterion_id)
                return 1.0, {"location": observed}
            return 0.0, None
        if isinstance(criterion, PossessionThresholdCriterion):
            quantity = (
                context.registry.get_component(
                    character_id, PossessionsComponent
                ).holdings.get(criterion.item_id, 0)
                if context.registry.has_component(
                    character_id, PossessionsComponent
                )
                else 0
            )
            if _compare(quantity, criterion.comparator, criterion.quantity):
                goal.matched_criteria.add(criterion_id)
                return 1.0, {
                    "item_id": criterion.item_id,
                    "quantity": quantity,
                }
            return 0.0, None
        if isinstance(criterion, ActionOutcomeCriterion):
            event_type = f"action.{criterion.outcome.value}"
            event = next(
                (
                    item
                    for item in events
                    if item.event_type == event_type
                    and item.agent_id == character_id
                    and item.payload.get("action") == criterion.action.value
                    and (
                        criterion.target is None
                        or item.payload.get("target") == criterion.target
                    )
                ),
                None,
            )
            return self._event_result(goal, criterion_id, event)
        if isinstance(criterion, InteractionCountCriterion):
            matching = [
                item
                for item in events
                if _interaction_matches(item, character_id, criterion)
            ]
            previous = goal.interaction_counts.get(criterion_id, 0)
            count = previous + len(matching)
            goal.interaction_counts[criterion_id] = count
            progress = min(1.0, count / criterion.minimum_count)
            if progress >= 1.0:
                goal.matched_criteria.add(criterion_id)
            if count != previous:
                return progress, {
                    "interaction_type": criterion.interaction_type.value,
                    "count": count,
                    "minimum_count": criterion.minimum_count,
                    "event_ids": [
                        _canonical_event_id(event) for event in matching
                    ],
                }
            return progress, None
        if isinstance(criterion, SimulationTimeCriterion):
            if _compare(
                context.clock.simulation_time,
                criterion.comparator,
                criterion.simulation_time,
            ):
                goal.matched_criteria.add(criterion_id)
                return 1.0, {
                    "simulation_time": context.clock.simulation_time
                }
            return 0.0, None
        raise TypeError(f"unsupported goal criterion: {type(criterion).__name__}")

    @staticmethod
    def _event_result(
        goal: GoalRuntime,
        criterion_id: str,
        event: DomainEvent | None,
    ) -> tuple[float, dict[str, JsonValue] | None]:
        if event is None:
            return 0.0, None
        goal.matched_criteria.add(criterion_id)
        return 1.0, {
            "event_id": _canonical_event_id(event),
            "event_type": event.event_type,
            "payload": dict(event.payload),
        }

    def _transition(
        self,
        context: SystemContext,
        character_id: str,
        goal: GoalRuntime,
        status: GoalStatus,
        event_type: str,
        extra: dict[str, JsonValue],
    ) -> None:
        previous = goal.status
        goal.status = status
        self._emit(
            context,
            character_id,
            goal,
            event_type,
            extra,
            previous_status=previous,
        )

    @staticmethod
    def _emit(
        context: SystemContext,
        character_id: str,
        goal: GoalRuntime,
        event_type: str,
        extra: dict[str, JsonValue],
        *,
        previous_status: GoalStatus,
    ) -> None:
        context.events.emit(
            event_type,
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=character_id,
            payload={
                "goal_id": goal.definition.id,
                "description": goal.definition.description,
                "priority": goal.definition.priority,
                "previous_status": previous_status.value,
                "status": goal.status.value,
                "progress": goal.progress,
                "evidence": [
                    _evidence_payload(item) for item in goal.evidence
                ],
                **extra,
            },
        )


def retire_goal(
    context: SystemContext,
    character_id: str,
    goal_id: str,
    reason: str,
) -> None:
    goal = context.registry.get_component(character_id, GoalComponent).get(
        goal_id
    )
    if goal.status in {
        GoalStatus.SUCCEEDED,
        GoalStatus.FAILED,
        GoalStatus.EXPIRED,
        GoalStatus.RETIRED,
    }:
        return
    previous = goal.status
    goal.status = GoalStatus.RETIRED
    GoalEvaluationSystem._emit(
        context,
        character_id,
        goal,
        "goal.retired",
        {"reason": reason},
        previous_status=previous,
    )


def _state_value(
    context: SystemContext,
    character_id: str,
    criterion: StateComparisonCriterion,
) -> object:
    component_name = criterion.component.value
    if criterion.field not in _STATE_FIELDS[component_name]:
        return _MISSING
    component_type: type[object] = {
        "homeostasis": HomeostasisComponent,
        "activity": ActivityComponent,
        "controller": ControllerComponent,
    }[component_name]
    if not context.registry.has_component(character_id, component_type):
        return _MISSING
    component: object = context.registry.get_component(
        character_id, component_type
    )
    value = getattr(component, criterion.field)
    return value.value if hasattr(value, "value") else value


def _location_matches(
    context: SystemContext,
    character_id: str,
    criterion: LocationMatchCriterion,
) -> tuple[bool, JsonValue]:
    place_id = None
    room_id = None
    building_id = None
    city_zone_id = None
    if context.registry.has_component(character_id, SpatialLocationComponent):
        place_id = context.registry.get_component(
            character_id, SpatialLocationComponent
        ).location.place_id
        if context.registry.has_resource(CityWorld):
            city = context.registry.get_resource(CityWorld)
            try:
                room = city.room(place_id)
            except KeyError:
                room = None
            if room is not None:
                room_id = room.id
                building = city.building(room.building_id)
                building_id = building.id
                city_zone_id = building.district_id
            else:
                try:
                    outdoor = city.outdoor_place(place_id)
                except KeyError:
                    outdoor = None
                if outdoor is not None:
                    city_zone_id = outdoor.district_id
    zone_id = None
    if context.registry.has_component(character_id, PositionComponent):
        world = local_world_for_agent(context.registry, character_id)
        zone = (
            world.zone_at(
                context.registry.get_component(
                    character_id, PositionComponent
                ).coordinate
            )
            if world is not None
            else None
        )
        zone_id = zone.id if zone is not None else None
    observed: dict[str, JsonValue] = {
        "place_id": place_id,
        "room_id": room_id,
        "building_id": building_id,
        "city_zone_id": city_zone_id,
        "zone_id": zone_id,
    }
    place_ids = {place_id, room_id, building_id, city_zone_id}
    if criterion.location_kind.value == "place":
        return criterion.location_id in place_ids, observed
    if criterion.location_kind.value == "zone":
        return zone_id == criterion.location_id, observed
    return criterion.location_id in {*place_ids, zone_id}, observed


def _interaction_matches(
    event: DomainEvent,
    character_id: str,
    criterion: InteractionCountCriterion,
) -> bool:
    if event.event_type != _INTERACTION_EVENTS[criterion.interaction_type]:
        return False
    target = event.payload.get("target_id")
    if event.agent_id != character_id and target != character_id:
        return False
    if criterion.target_id is None:
        return True
    if event.agent_id == character_id:
        return target == criterion.target_id
    return event.agent_id == criterion.target_id and target == character_id


def _is_payload_subset(
    expected: Mapping[str, JsonValue],
    actual: Mapping[str, JsonValue],
) -> bool:
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, Mapping) or not _is_payload_subset(
                expected_value, actual_value
            ):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _compare(left: object, comparator: GoalComparator, right: object) -> bool:
    if comparator is GoalComparator.EQ:
        return left == right
    if comparator is GoalComparator.NE:
        return left != right
    if (
        isinstance(left, bool)
        or isinstance(right, bool)
        or not isinstance(left, int | float)
        or not isinstance(right, int | float)
    ):
        return False
    if comparator is GoalComparator.LT:
        return left < right
    if comparator is GoalComparator.LTE:
        return left <= right
    if comparator is GoalComparator.GT:
        return left > right
    return left >= right


def _evidence_payload(evidence: GoalEvidence) -> dict[str, JsonValue]:
    return {
        "criterion_id": evidence.criterion_id,
        "criterion_type": evidence.criterion_type,
        "simulation_tick": evidence.simulation_tick,
        "simulation_time": evidence.simulation_time,
        "context": evidence.context,
    }


def _canonical_event_id(event: DomainEvent) -> str:
    prefix = f"{event.run_id}:"
    if event.event_id.startswith(prefix):
        return f"event-{event.event_id.removeprefix(prefix)}"
    return event.event_id
