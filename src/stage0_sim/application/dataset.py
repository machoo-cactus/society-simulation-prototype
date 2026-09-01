from stage0_sim.application.data_capture import (
    DATASET_SCHEMA_ID,
    DATASET_SCHEMA_VERSION,
    DatasetQueryFilter,
    DatasetQueryPage,
    DatasetRecord,
    DatasetRecordFilter,
    DatasetRecordPage,
    RecordCategory,
    RecordJoinIds,
    RecordRelation,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.domain.components import (
    ActionInstance,
    ActivityComponent,
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    GoalComponent,
    HomeostasisComponent,
    MovementComponent,
    NavigationComponent,
    NpcComponent,
    PendingSpeechComponent,
    PerceptionComponent,
    PlanComponent,
    PositionComponent,
    PossessionsComponent,
    SpatialLocationComponent,
    TransactionExecutionComponent,
    TransactionRequestComponent,
    TravelComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.world import CityWorld

__all__ = [
    "DATASET_SCHEMA_ID",
    "DATASET_SCHEMA_VERSION",
    "AgentStateProjector",
    "DatasetRecord",
    "DatasetRecordFilter",
    "DatasetRecordPage",
    "DatasetQueryFilter",
    "DatasetQueryPage",
    "RecordCategory",
    "RecordJoinIds",
    "RecordRelation",
    "RecordSource",
    "RecordVisibility",
    "RunnerPhase",
]


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
        if registry.has_component(agent_id, NpcComponent):
            npc = registry.get_component(agent_id, NpcComponent)
            state["actor_kind"] = "npc"
            state["npc"] = {
                "role_id": npc.role_id,
                "role_name": npc.role_name,
                "staffed_point_id": npc.staffed_point_id,
                "spawn_sequence": npc.spawn_sequence,
                "spawned_at": npc.spawned_at,
                "control_mode": npc.control_mode.value,
                "transient": npc.transient,
            }
        if registry.has_component(agent_id, CharacterSituationComponent):
            situation = registry.get_component(
                agent_id, CharacterSituationComponent
            )
            state["character_situation"] = {
                "slot_id": situation.slot_id,
                "label": situation.label,
                "briefing": situation.briefing,
                "description": situation.description,
                "content_hash": situation.content_hash,
                "input_hash": situation.input_hash,
                "data": situation.data,
                "generation": situation.generation,
            }
        if registry.has_component(agent_id, PositionComponent):
            state["position"] = registry.get_component(
                agent_id, PositionComponent
            ).coordinate.to_payload()
        if registry.has_component(agent_id, SpatialLocationComponent):
            location = registry.get_component(
                agent_id, SpatialLocationComponent
            ).location
            room_id = None
            building_id = None
            city_zone_id = None
            if registry.has_resource(CityWorld):
                city = registry.get_resource(CityWorld)
                try:
                    room = city.room(location.place_id)
                except KeyError:
                    room = None
                if room is not None:
                    building = city.building(room.building_id)
                    room_id = room.id
                    building_id = building.id
                    city_zone_id = building.district_id
            state["spatial_location"] = {
                "scale": location.scale.value,
                "place_id": location.place_id,
                "room_id": room_id,
                "building_id": building_id,
                "city_zone_id": city_zone_id,
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
                **action_lineage_payload(travel.action_instance),
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
                **action_lineage_payload(navigation.action_instance),
            }
        if registry.has_component(agent_id, HomeostasisComponent):
            state["homeostasis"] = registry.get_component(
                agent_id, HomeostasisComponent
            ).snapshot()
        if registry.has_component(agent_id, PossessionsComponent):
            possessions = registry.get_component(
                agent_id, PossessionsComponent
            )
            state["possessions"] = dict(
                sorted(possessions.holdings.items())
            )
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
                **action_lineage_payload(movement.action_instance),
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
                "correction_action": _plan_action(drive.correction_action),
                "correction_action_started": drive.correction_action_started,
            }
        if registry.has_component(agent_id, PlanComponent):
            plan = registry.get_component(agent_id, PlanComponent)
            state["plan"] = {
                "current": _plan_action(plan.current),
                "queue": [_plan_action(action) for action in plan.queue],
                "remaining_duration": plan.remaining_duration,
                "plan_id": plan.plan_id,
                "plan_revision": plan.plan_revision,
                "origin": plan.origin.value if plan.origin is not None else None,
                "root_correlation_id": plan.root_correlation_id,
            }
        if registry.has_component(agent_id, AffordanceRequestComponent):
            request = registry.get_component(
                agent_id, AffordanceRequestComponent
            )
            state["affordance_request"] = {
                "station_id": request.station_id,
                "action": request.action,
                "source": request.source,
                "status": request.status,
                "failure_reason": request.failure_reason,
                **action_lineage_payload(request.action_instance),
            }
        if registry.has_component(agent_id, AffordanceExecutionComponent):
            execution = registry.get_component(
                agent_id, AffordanceExecutionComponent
            )
            state["affordance_execution"] = {
                "station_id": execution.station_id,
                "action": execution.definition.action,
                "elapsed": execution.elapsed,
                "source": execution.source,
                **action_lineage_payload(execution.action_instance),
            }
        if registry.has_component(agent_id, TransactionRequestComponent):
            transaction_request = registry.get_component(
                agent_id, TransactionRequestComponent
            )
            state["transaction_request"] = {
                "request_id": transaction_request.request_id,
                "point_id": transaction_request.point_id,
                "offer_id": transaction_request.offer_id,
                "source": transaction_request.source,
                "status": transaction_request.status,
                "failure_reason": transaction_request.failure_reason,
                **action_lineage_payload(transaction_request.action_instance),
            }
        if registry.has_component(agent_id, TransactionExecutionComponent):
            transaction_execution = registry.get_component(
                agent_id, TransactionExecutionComponent
            )
            state["transaction_execution"] = {
                "point_id": transaction_execution.point_id,
                "offer_id": transaction_execution.offer.id,
                "elapsed": transaction_execution.elapsed,
                "source": transaction_execution.source,
                "operator_id": transaction_execution.operator_id,
                **action_lineage_payload(
                    transaction_execution.action_instance
                ),
            }
        if registry.has_component(agent_id, PendingSpeechComponent):
            speech = registry.get_component(agent_id, PendingSpeechComponent)
            state["pending_speech"] = {
                "decision_id": speech.decision_id,
                "tool_call_id": speech.tool_call_id,
                "target_id": speech.target_id,
                "channel": speech.channel,
                **action_lineage_payload(speech.action_instance),
            }
        if registry.has_component(agent_id, GoalComponent):
            goal_component = registry.get_component(agent_id, GoalComponent)
            state["goals"] = [
                {
                    "id": goal.definition.id,
                    "description": goal.definition.description,
                    "priority": goal.definition.priority,
                    "tags": list(goal.definition.tags),
                    "activation_time": goal.definition.activation_time,
                    "deadline_time": goal.definition.deadline_time,
                    "completion_policy": (
                        goal.definition.completion_policy.value
                    ),
                    "status": goal.status.value,
                    "progress": goal.progress,
                    "evidence_count": len(goal.evidence),
                }
                for goal in goal_component.goals
            ]
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
            }
        return state


def _plan_action(action: ActionInstance | None) -> JsonValue:
    if action is None:
        return None
    content: dict[str, JsonValue] = {
        "action": action.action_name
    }
    if action.target is not None:
        content["target"] = action.target
    if action.duration is not None:
        content["duration"] = action.duration
    if action.mode is not None:
        content["mode"] = action.mode.value
    if action.offer_id is not None:
        content["offer_id"] = action.offer_id
    content.update(action_lineage_payload(action))
    return content
