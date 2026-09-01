from dataclasses import dataclass

from stage0_sim.application.navigation.destinations import (
    DestinationResolutionError,
)
from stage0_sim.application.navigation.planner import NavigationPlanningError
from stage0_sim.application.navigation.service import NavigationService
from stage0_sim.domain.components import (
    ActionType,
    NavigationComponent,
    NavigationStatus,
    PlanComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import Locator


@dataclass(frozen=True, slots=True)
class NavigationPlanningSystem:
    name: str = "navigation_planning"
    order: int = 85

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(NavigationService):
            return
        service = context.registry.get_resource(NavigationService)
        for character_id in context.registry.query_entities(
            PlanComponent,
            NavigationComponent,
        ):
            plan = context.registry.get_component(character_id, PlanComponent)
            navigation = context.registry.get_component(
                character_id,
                NavigationComponent,
            )
            next_action = plan.current or (plan.queue[0] if plan.queue else None)
            if (
                next_action is None
                or next_action.action is not ActionType.NAVIGATE
                or navigation.status is not NavigationStatus.REQUESTED
                or navigation.target_id is None
            ):
                continue
            requested = context.events.emit(
                "navigation.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=character_id,
                payload={
                    "target_id": navigation.target_id,
                    "preferred_mode": (
                        navigation.preferred_mode.value
                        if navigation.preferred_mode is not None
                        else None
                    ),
                    "reason": navigation.reason,
                    **action_lineage_payload(navigation.action_instance),
                },
                correlation_id=(
                    navigation.action_instance.root_correlation_id
                    if navigation.action_instance is not None
                    else None
                ),
            )
            navigation.correlation_id = (
                navigation.action_instance.root_correlation_id
                if navigation.action_instance is not None
                else requested.event_id
            )
            try:
                planned = service.plan(
                    character_id,
                    navigation.target_id,
                    navigation.preferred_mode,
                    authoritative=navigation.reason == "system1",
                )
            except (DestinationResolutionError, NavigationPlanningError) as error:
                reason = error.reason
                navigation.status = NavigationStatus.FAILED
                navigation.failure_reason = reason
                self._emit_failed(context, character_id, navigation, reason)
                continue
            except (KeyError, ValueError) as error:
                navigation.status = NavigationStatus.FAILED
                navigation.failure_reason = "navigation_precondition_failed"
                self._emit_failed(
                    context,
                    character_id,
                    navigation,
                    "navigation_precondition_failed",
                    str(error),
                )
                continue
            navigation.route = planned.route
            navigation.primitives = planned.primitives
            navigation.current_primitive_index = 0
            navigation.completed_route_legs = 0
            navigation.status = NavigationStatus.PLANNED
            context.events.emit(
                "navigation.planned",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=character_id,
                payload={
                    "target_id": planned.destination.id,
                    "destination": self._locator_payload(
                        planned.route.destination
                    ),
                    "topology_revision": (
                        planned.route.planned_from_topology_revision
                    ),
                    "leg_count": len(planned.route.legs),
                    "primitive_count": len(planned.primitives),
                    "legs": [
                        {
                            "origin": self._locator_payload(leg.origin),
                            "destination": self._locator_payload(
                                leg.destination
                            ),
                            "traversal_kind": leg.traversal_kind,
                            "executor_id": leg.executor_id,
                            "transition_id": leg.transition_id,
                            "cost": leg.cost,
                        }
                        for leg in planned.route.legs
                    ],
                    **action_lineage_payload(navigation.action_instance),
                },
                causation_id=requested.event_id,
                correlation_id=navigation.correlation_id,
            )

    @staticmethod
    def _emit_failed(
        context: SystemContext,
        character_id: str,
        navigation: NavigationComponent,
        reason: str,
        message: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "target_id": navigation.target_id,
            "reason": reason,
            **action_lineage_payload(navigation.action_instance),
        }
        if message is not None:
            payload["message"] = message
        context.events.emit(
            "navigation.failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=character_id,
            payload=payload,
            correlation_id=navigation.correlation_id,
        )

    @staticmethod
    def _locator_payload(locator: Locator) -> dict[str, JsonValue]:
        return {
            "space_id": locator.space_id,
            "local_reference": locator.local_reference,
        }
