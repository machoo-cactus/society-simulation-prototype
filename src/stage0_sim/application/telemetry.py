from dataclasses import dataclass

from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.runner import SimulationRunner
from stage0_sim.domain.components import (
    ActivityComponent,
    CharacterProfileComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    HomeostasisComponent,
    MemoryComponent,
    MovementComponent,
    PerceptionComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
)
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.world import WorldMap

TELEMETRY_SCHEMA_VERSION = "stage0.telemetry.v2"


@dataclass(frozen=True, slots=True)
class TelemetryMessage:
    sequence: int
    message_type: str
    run_id: str
    simulation_tick: int
    simulation_time: float
    payload: dict[str, JsonValue]
    domain_event_offset: int | None = None
    snapshot_revision: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        content: dict[str, JsonValue] = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "type": self.message_type,
            "run_id": self.run_id,
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "payload": self.payload,
        }
        if self.domain_event_offset is not None:
            content["domain_event_offset"] = self.domain_event_offset
        if self.snapshot_revision is not None:
            content["snapshot_revision"] = self.snapshot_revision
        return content


class TelemetryBroker:
    def __init__(self, runner: SimulationRunner, history_limit: int = 10_000) -> None:
        if history_limit <= 0:
            raise ValueError("history_limit must be greater than zero")
        self.runner = runner
        self._sequence = 0
        self._history_limit = history_limit
        self._messages: dict[int, TelemetryMessage] = {}
        self._domain_event_offset = 0
        self._snapshot_revision = 0
        self._latest_snapshot: TelemetryMessage | None = None
        runner.events.subscribe(self._on_event)

    @property
    def latest_sequence(self) -> int:
        return self._sequence

    @property
    def oldest_sequence(self) -> int:
        return next(iter(self._messages), self._sequence + 1)

    @property
    def domain_event_offset(self) -> int:
        return self._domain_event_offset

    @property
    def snapshot_revision(self) -> int:
        return self._snapshot_revision

    @property
    def latest_snapshot(self) -> TelemetryMessage | None:
        return self._latest_snapshot

    def can_resume_after(self, sequence: int) -> bool:
        return sequence >= self.oldest_sequence - 1

    def messages_after(self, sequence: int) -> tuple[TelemetryMessage, ...]:
        first = max(sequence + 1, self.oldest_sequence)
        return tuple(
            self._messages[candidate]
            for candidate in range(first, self._sequence + 1)
            if candidate in self._messages
        )

    def publish_event(self, event: DomainEvent) -> TelemetryMessage:
        self._domain_event_offset += 1
        return self._publish(
            _message_type_for_event(event.event_type),
            event.simulation_tick,
            event.simulation_time,
            {"event": event.to_dict()},
            domain_event_offset=self._domain_event_offset,
        )

    def _on_event(self, event: DomainEvent) -> None:
        self.publish_event(event)

    def publish_snapshot(self) -> TelemetryMessage:
        self._snapshot_revision += 1
        self._latest_snapshot = TelemetryMessage(
            sequence=self._sequence,
            message_type="world_snapshot",
            run_id=self.runner.events.run_id,
            simulation_tick=self.runner.clock.tick,
            simulation_time=self.runner.clock.simulation_time,
            payload=build_runtime_snapshot(self.runner),
            snapshot_revision=self._snapshot_revision,
        )
        return self._latest_snapshot

    def publish_status(self) -> TelemetryMessage:
        return self._publish(
            "simulation_status",
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
            {
                "status": self.runner.status.value,
                "speed": self.runner.speed,
            },
        )

    def _publish(
        self,
        message_type: str,
        simulation_tick: int,
        simulation_time: float,
        payload: dict[str, JsonValue],
        *,
        domain_event_offset: int | None = None,
    ) -> TelemetryMessage:
        self._sequence += 1
        message = TelemetryMessage(
            sequence=self._sequence,
            message_type=message_type,
            run_id=self.runner.events.run_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            payload=payload,
            domain_event_offset=domain_event_offset,
        )
        self._messages[message.sequence] = message
        while len(self._messages) > self._history_limit:
            self._messages.pop(next(iter(self._messages)))
        return message


def build_ui_bootstrap(runner: SimulationRunner) -> dict[str, JsonValue]:
    registry = runner.registry
    world_payload: dict[str, JsonValue] | None = None
    if registry.has_resource(WorldMap):
        world = registry.get_resource(WorldMap)
        world_payload = {
            "width": world.grid.width,
            "height": world.grid.height,
            "blocked": [
                coordinate.to_payload() for coordinate in sorted(world.grid.blocked)
            ],
            "zones": [
                {
                    "id": zone.id,
                    "name": zone.name,
                    "type": zone.zone_type,
                    "tiles": [
                        coordinate.to_payload() for coordinate in sorted(zone.tiles)
                    ],
                }
                for zone in sorted(world.zones, key=lambda item: item.id)
            ],
            "stations": [
                {
                    "id": station.id,
                    "name": station.name,
                    "position": station.position.to_payload(),
                    "actions": list(station.supported_actions),
                    "available": station.available,
                    "capacity": station.capacity,
                }
                for station in sorted(world.stations, key=lambda item: item.id)
            ],
        }
    return {
        "world": world_payload,
        "agents": [
            _build_agent_static_snapshot(runner, entity_id)
            for entity_id in registry.entities()
        ],
    }


def build_world_snapshot(runner: SimulationRunner) -> dict[str, JsonValue]:
    bootstrap = build_ui_bootstrap(runner)
    runtime = build_runtime_snapshot(runner)
    static_agents = {
        str(agent["id"]): agent
        for agent in bootstrap["agents"]  # type: ignore[union-attr]
        if isinstance(agent, dict) and isinstance(agent.get("id"), str)
    }
    runtime_agents = runtime["agents"]
    if isinstance(runtime_agents, list):
        for agent in runtime_agents:
            if not isinstance(agent, dict) or not isinstance(agent.get("id"), str):
                continue
            static = static_agents.get(str(agent["id"]))
            if static is not None:
                agent.update(static)
    return {
        **runtime,
        "world": bootstrap["world"],
    }


def build_runtime_snapshot(runner: SimulationRunner) -> dict[str, JsonValue]:
    registry = runner.registry
    station_states: list[JsonValue] = []
    if registry.has_resource(WorldMap):
        station_states = [
            {"id": station.id, "available": station.available}
            for station in sorted(
                registry.get_resource(WorldMap).stations,
                key=lambda item: item.id,
            )
        ]
    return {
        "status": runner.status.value,
        "speed": runner.speed,
        "tick": runner.clock.tick,
        "simulation_time": runner.clock.simulation_time,
        "world": {"station_states": station_states},
        "agents": [
            build_agent_snapshot(runner, entity_id, include_profile=False)
            for entity_id in registry.entities()
        ],
    }


def build_agent_snapshot(
    runner: SimulationRunner,
    agent_id: str,
    *,
    include_profile: bool = True,
) -> dict[str, JsonValue]:
    registry = runner.registry
    payload: dict[str, JsonValue] = {"id": agent_id}
    if (
        include_profile
        and registry.has_component(agent_id, CharacterProfileComponent)
    ):
        profile = registry.get_component(agent_id, CharacterProfileComponent)
        payload["character_profile"] = {
            "profile_id": profile.profile_id,
            "template_id": profile.template_id,
            "template_version": profile.template_version,
            "content_hash": profile.content_hash,
            "display_name": profile.display_name,
            "description": profile.description,
            "data": profile.ui_data,
        }
    if registry.has_component(agent_id, PositionComponent):
        payload["position"] = registry.get_component(
            agent_id, PositionComponent
        ).coordinate.to_payload()
    if registry.has_component(agent_id, HomeostasisComponent):
        payload["homeostasis"] = registry.get_component(
            agent_id, HomeostasisComponent
        ).snapshot()
    if registry.has_component(agent_id, ActivityComponent):
        payload["activity"] = registry.get_component(
            agent_id, ActivityComponent
        ).current.value
    if registry.has_component(agent_id, MovementComponent):
        movement = registry.get_component(agent_id, MovementComponent)
        payload["movement"] = {
            "destination": (
                movement.destination.to_payload()
                if movement.destination is not None
                else None
            ),
            "path": [coordinate.to_payload() for coordinate in movement.path],
        }
    if registry.has_component(agent_id, DriveComponent):
        drive = registry.get_component(agent_id, DriveComponent)
        payload["system1"] = {
            "state": drive.state.value,
            "active_drive": (
                drive.active_drive.value if drive.active_drive is not None else None
            ),
            "target_station_id": drive.target_station_id,
        }
    if registry.has_component(agent_id, PlanComponent):
        plan = registry.get_component(agent_id, PlanComponent)
        payload["plan"] = {
            "current": _plan_action_payload(plan.current),
            "queue": [_plan_action_payload(action) for action in plan.queue],
            "remaining_duration": plan.remaining_duration,
        }
    if registry.has_component(agent_id, PlannerComponent):
        planner = registry.get_component(agent_id, PlannerComponent)
        payload["planner"] = {
            "needs_plan": planner.needs_plan,
            "request_count": planner.request_count,
            "failure_count": planner.failure_count,
        }
    if registry.has_component(agent_id, ControllerComponent):
        controller = registry.get_component(agent_id, ControllerComponent)
        payload["controller"] = {
            "enabled": controller.enabled,
            "request_pending": controller.request_pending,
            "state_revision": controller.state_revision,
            "current_decision_id": controller.current_decision_id,
            "last_outcome": controller.last_outcome,
        }
    if registry.has_component(agent_id, PerceptionComponent):
        perception = registry.get_component(agent_id, PerceptionComponent)
        visible_now: list[JsonValue] = list(sorted(perception.visible_now))
        payload["perception"] = {
            "inbox_count": len(perception.inbox),
            "visible_now": visible_now,
            "known_character_count": len(perception.knowledge),
        }
    if registry.has_component(agent_id, MemoryComponent):
        store = registry.get_resource(EpisodicMemoryStore)
        payload["memory"] = {
            "count": sum(
                record.agent_id == agent_id for record in store.records
            )
        }
    if registry.has_component(agent_id, ConversationComponent):
        conversation = registry.get_component(agent_id, ConversationComponent)
        payload["conversation"] = {
            "turn_count": len(conversation.turns),
            "latest_turn": conversation.turns[-1] if conversation.turns else None,
            "request_pending": conversation.request_pending,
        }
    return payload


def _build_agent_static_snapshot(
    runner: SimulationRunner,
    agent_id: str,
) -> dict[str, JsonValue]:
    registry = runner.registry
    payload: dict[str, JsonValue] = {"id": agent_id}
    if registry.has_component(agent_id, CharacterProfileComponent):
        profile = registry.get_component(agent_id, CharacterProfileComponent)
        payload["character_profile"] = {
            "profile_id": profile.profile_id,
            "template_id": profile.template_id,
            "template_version": profile.template_version,
            "content_hash": profile.content_hash,
            "display_name": profile.display_name,
            "description": profile.description,
            "data": profile.ui_data,
        }
    return payload


def _plan_action_payload(action: PlanAction | None) -> JsonValue:
    if action is None:
        return None
    payload: dict[str, JsonValue] = {"action": action.action.value}
    if action.target is not None:
        payload["target"] = action.target
    if action.duration is not None:
        payload["duration"] = action.duration
    return payload


def _message_type_for_event(event_type: str) -> str:
    if event_type == "homeostasis.changed":
        return "homeostasis_delta"
    if event_type.startswith(("plan.", "planner.")):
        return "plan_changed"
    if event_type.startswith("system1.") or event_type == "threshold.breached":
        return "system1_event"
    if event_type.startswith("dialogue."):
        return "dialogue_event"
    if event_type.startswith(("cognition.", "tool.")):
        return "cognition_event"
    if event_type.startswith(("speech.", "perception.")):
        return "perception_event"
    if event_type.startswith(("agent.", "path.", "activity.", "affordance.")):
        return "agent_delta"
    return "event"
