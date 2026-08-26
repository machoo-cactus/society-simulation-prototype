from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    ConversationComponent,
    DriveComponent,
    HomeostasisComponent,
    MovementComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
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
        if registry.has_component(agent_id, PositionComponent):
            state["position"] = registry.get_component(
                agent_id, PositionComponent
            ).coordinate.to_payload()
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
    return content
