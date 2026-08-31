from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    HomeostasisComponent,
    MovementComponent,
    NavigationComponent,
    PerceptionComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    SpatialLocationComponent,
    TravelComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue

DATASET_SCHEMA_VERSION = "stage0.dataset.v1"


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    run_id: str
    sequence: int
    record_type: str
    simulation_tick: int
    simulation_time: float
    agent_id: str | None
    payload: dict[str, JsonValue]
    source_event_id: str | None = None
    schema_version: str = DATASET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        content: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "payload": self.payload,
        }
        if self.agent_id is not None:
            content["agent_id"] = self.agent_id
        if self.source_event_id is not None:
            content["source_event_id"] = self.source_event_id
        return content


class AgentStateProjector:
    """The only ECS-aware boundary used by the versioned dataset collector."""

    def project(self, registry: Registry, agent_id: str) -> dict[str, JsonValue]:
        state: dict[str, JsonValue] = {}
        if registry.has_component(agent_id, CharacterProfileComponent):
            profile = registry.get_component(
                agent_id, CharacterProfileComponent
            )
            state["character_profile"] = {
                "profile_id": profile.profile_id,
                "template_id": profile.template_id,
                "template_version": profile.template_version,
                "content_hash": profile.content_hash,
                "display_name": profile.display_name,
            }
        if registry.has_component(agent_id, CharacterSituationComponent):
            situation = registry.get_component(
                agent_id, CharacterSituationComponent
            )
            state["character_situation"] = {
                "slot_id": situation.slot_id,
                "label": situation.label,
                "briefing": situation.briefing,
            }
        if registry.has_component(agent_id, PositionComponent):
            state["position"] = registry.get_component(
                agent_id, PositionComponent
            ).coordinate.to_payload()
        if registry.has_component(agent_id, SpatialLocationComponent):
            location = registry.get_component(
                agent_id, SpatialLocationComponent
            ).location
            state["spatial_location"] = {
                "scale": location.scale.value,
                "place_id": location.place_id,
                "local_coordinate": (
                    location.local_coordinate.to_payload()
                    if location.local_coordinate is not None
                    else None
                ),
                "network_node_id": location.network_node_id,
                "edge_id": location.edge_id,
                "edge_progress": location.edge_progress,
            }
        if registry.has_component(agent_id, TravelComponent):
            travel = registry.get_component(agent_id, TravelComponent)
            state["travel"] = {
                "destination_id": travel.destination_id,
                "requested_mode": (
                    travel.requested_mode.value
                    if travel.requested_mode is not None
                    else None
                ),
                "status": travel.status.value,
                "current_leg_index": travel.current_leg_index,
                "leg_count": len(travel.route),
                "vehicle_id": travel.vehicle_id,
            }
        if registry.has_component(agent_id, NavigationComponent):
            navigation = registry.get_component(
                agent_id,
                NavigationComponent,
            )
            state["navigation"] = {
                "target_id": navigation.target_id,
                "preferred_mode": (
                    navigation.preferred_mode.value
                    if navigation.preferred_mode is not None
                    else None
                ),
                "status": navigation.status.value,
                "current_primitive_index": (
                    navigation.current_primitive_index
                ),
                "primitive_count": len(navigation.primitives),
                "completed_route_legs": (
                    navigation.completed_route_legs
                ),
                "route_leg_count": (
                    len(navigation.route.legs)
                    if navigation.route is not None
                    else 0
                ),
                "failure_reason": navigation.failure_reason,
            }
        if registry.has_component(agent_id, HomeostasisComponent):
            state["homeostasis"] = registry.get_component(
                agent_id, HomeostasisComponent
            ).snapshot()
        if registry.has_component(agent_id, ActivityComponent):
            state["activity"] = registry.get_component(
                agent_id, ActivityComponent
            ).current.value
        if registry.has_component(agent_id, MovementComponent):
            movement = registry.get_component(agent_id, MovementComponent)
            state["movement"] = {
                "destination": (
                    movement.destination.to_payload()
                    if movement.destination is not None
                    else None
                ),
                "path": [coordinate.to_payload() for coordinate in movement.path],
            }
        if registry.has_component(agent_id, DriveComponent):
            drive = registry.get_component(agent_id, DriveComponent)
            state["system1"] = {
                "state": drive.state.value,
                "active_drive": (
                    drive.active_drive.value
                    if drive.active_drive is not None
                    else None
                ),
                "target_station_id": drive.target_station_id,
            }
        if registry.has_component(agent_id, PlanComponent):
            plan = registry.get_component(agent_id, PlanComponent)
            state["plan"] = {
                "current": _plan_action(plan.current),
                "queue": [_plan_action(action) for action in plan.queue],
                "remaining_duration": plan.remaining_duration,
            }
        if registry.has_component(agent_id, PlannerComponent):
            planner = registry.get_component(agent_id, PlannerComponent)
            state["planner"] = {
                "needs_plan": planner.needs_plan,
                "request_count": planner.request_count,
                "failure_count": planner.failure_count,
                "last_planned_at": planner.last_planned_at,
                "request_pending": planner.request_pending,
            }
        if registry.has_component(agent_id, ControllerComponent):
            controller = registry.get_component(agent_id, ControllerComponent)
            state["controller"] = {
                "enabled": controller.enabled,
                "request_pending": controller.request_pending,
                "state_revision": controller.state_revision,
                "current_decision_id": controller.current_decision_id,
                "last_outcome": controller.last_outcome,
            }
        if registry.has_component(agent_id, PerceptionComponent):
            perception = registry.get_component(agent_id, PerceptionComponent)
            visible_now: list[JsonValue] = list(sorted(perception.visible_now))
            state["perception"] = {
                "inbox_count": len(perception.inbox),
                "visible_now": visible_now,
                "known_character_count": len(perception.knowledge),
            }
        if registry.has_component(agent_id, ConversationComponent):
            conversation = registry.get_component(
                agent_id, ConversationComponent
            )
            state["conversation"] = {
                "turn_count": len(conversation.turns),
                "latest_turn": (
                    conversation.turns[-1] if conversation.turns else None
                ),
                "request_pending": conversation.request_pending,
            }
        return state


def _plan_action(action: PlanAction | None) -> JsonValue:
    if action is None:
        return None
    content: dict[str, JsonValue] = {"action": action.action.value}
    if action.target is not None:
        content["target"] = action.target
    if action.duration is not None:
        content["duration"] = action.duration
    if action.mode is not None:
        content["mode"] = action.mode.value
    return content
