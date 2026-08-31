from dataclasses import dataclass

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.dataset import AgentStateProjector, DatasetRecord
from stage0_sim.application.information import InformationStore
from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.runner import SimulationRunner
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActivityComponent,
    PositionComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.environment import (
    EnvironmentAvailabilityRegistry,
    EnvironmentAvailabilityRules,
    SurfaceConditionRegistry,
    WeatherRuntime,
    wetness_band,
)
from stage0_sim.domain.events import DomainEvent, JsonValue


@dataclass(slots=True)
class _ActivityInterval:
    activity: str
    start_tick: int
    start_time: float


class RunDataCollector:
    def __init__(
        self,
        *,
        store: SQLiteDatasetStore,
        runner: SimulationRunner,
        scenario: dict[str, JsonValue],
        projector: AgentStateProjector | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.projector = projector or AgentStateProjector()
        self.run_id = runner.events.run_id
        self._sequence = 0
        self._finalized = False
        self._activities: dict[str, _ActivityInterval] = {}
        store.begin_run(
            run_id=self.run_id,
            seed=runner.configuration.seed,
            dt=runner.configuration.dt,
            initial_speed=runner.configuration.speed,
            scenario=scenario,
        )
        if runner.registry.has_resource(EpisodicMemoryStore):
            memory_store = runner.registry.get_resource(EpisodicMemoryStore)
            memory_store.bind_persistence(store, self.run_id)
            for record in memory_store.records:
                self._append(
                    "memory_reference",
                    0,
                    record.simulation_time,
                    record.agent_id,
                    {
                        "event_type": "memory.initial",
                        "memory_id": record.id,
                        "importance": record.importance,
                        "text": record.text,
                        "embedding": list(record.embedding),
                        "memory_metadata": record.metadata,
                    },
                    None,
                )
        elif runner.registry.has_resource(InformationStore):
            runner.registry.get_resource(InformationStore).bind_persistence(
                store,
                self.run_id,
            )
        runner.events.subscribe(self._collect)
        runner.subscribe_tick_completed(self._commit_tick)

    def finalize(self, status: str = "completed") -> None:
        if self._finalized:
            return
        self.runner.flush_pending_memory()
        for agent_id in sorted(self._activities):
            self._close_activity(
                agent_id,
                self.runner.clock.tick,
                self.runner.clock.simulation_time,
                source_event_id=None,
            )
        self.store.complete_run(
            self.run_id,
            status=status,
            final_tick=self.runner.clock.tick,
            final_simulation_time=self.runner.clock.simulation_time,
        )
        self._finalized = True

    def _collect(self, event: DomainEvent) -> None:
        if self._finalized:
            return
        self._append(
            "event",
            event.simulation_tick,
            event.simulation_time,
            event.agent_id,
            {
                "event_type": event.event_type,
                "wall_time": event.wall_time.isoformat(),
                "payload": dict(event.payload),
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
            },
            event.event_id,
        )
        self._collect_specialized(event)
        if event.event_type == "simulation.started":
            self._initialize_activities(event)
            self._collect_tick_state(event)
        elif event.event_type == "activity.changed" and event.agent_id is not None:
            self._change_activity(event)
        elif event.event_type == "simulation.stopped":
            self.finalize("stopped")
        if event.event_type in {
            "simulation.started",
            "simulation.paused",
            "simulation.resumed",
        }:
            self.store.flush()

    def _commit_tick(self, event: DomainEvent) -> None:
        if self._finalized:
            return
        self._collect_tick_state(event)
        self.store.flush()

    def _collect_specialized(self, event: DomainEvent) -> None:
        record_type: str | None = None
        if event.event_type == "threshold.breached":
            record_type = "threshold_crossing"
        elif event.event_type.startswith(("plan.", "planner.")):
            record_type = "plan_transition"
        elif event.event_type.startswith("affordance."):
            record_type = "affordance"
        elif event.event_type.startswith("dialogue."):
            record_type = "dialogue"
        elif event.event_type.startswith("memory."):
            record_type = "memory_reference"
        elif event.event_type.startswith("information."):
            record_type = "information_retrieval"
        elif event.event_type.startswith("perception."):
            record_type = "perception"
        elif event.event_type.startswith("speech."):
            record_type = "speech"
        elif event.event_type.startswith("tool."):
            record_type = "tool"
        elif event.event_type.startswith("cognition."):
            record_type = "cognition"
        elif event.event_type.startswith(
            ("travel.", "building.", "vehicle.", "metro.")
        ):
            record_type = "travel"
        elif event.event_type.startswith(
            ("time.", "weather.", "surface_condition.", "availability.")
        ):
            record_type = "environment"
        if record_type is not None:
            payload = {
                "event_type": event.event_type,
                **dict(event.payload),
            }
            if event.event_type == "memory.recorded":
                payload.update(self._memory_payload(event))
            self._append(
                record_type,
                event.simulation_tick,
                event.simulation_time,
                event.agent_id,
                payload,
                event.event_id,
            )
        request_events = {
            "planner.completed": ("plan", "completed"),
            "planner.failed": ("plan", "failed"),
            "planner.cancelled": ("plan", "cancelled"),
            "dialogue.generated": ("dialogue", "completed"),
            "dialogue.failed": ("dialogue", "failed"),
            "dialogue.cancelled": ("dialogue", "cancelled"),
            "cognition.completed": ("character_decision", "completed"),
            "cognition.failed": ("character_decision", "failed"),
            "cognition.cancelled": ("character_decision", "cancelled"),
        }
        if event.event_type in request_events:
            operation, status = request_events[event.event_type]
            self._append(
                "llm_request",
                event.simulation_tick,
                event.simulation_time,
                event.agent_id,
                {
                    "operation": operation,
                    "status": status,
                    **dict(event.payload),
                },
                event.event_id,
            )

    def _collect_tick_state(self, event: DomainEvent) -> None:
        self._append(
            "environment_state",
            event.simulation_tick,
            event.simulation_time,
            None,
            self._environment_state(event.simulation_time),
            event.event_id,
        )
        for agent_id in self.runner.registry.entities():
            state = self.projector.project(self.runner.registry, agent_id)
            if not state:
                continue
            self._append(
                "state_vector",
                event.simulation_tick,
                event.simulation_time,
                agent_id,
                state,
                event.event_id,
            )
            if self.runner.registry.has_component(agent_id, PositionComponent):
                position = self.runner.registry.get_component(
                    agent_id, PositionComponent
                )
                trajectory: dict[str, JsonValue] = {}
                if self.runner.registry.has_component(
                    agent_id, SpatialLocationComponent
                ):
                    location = self.runner.registry.get_component(
                        agent_id, SpatialLocationComponent
                    ).location
                    trajectory["spatial_location"] = {
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
                    if location.local_coordinate is not None:
                        trajectory["position"] = (
                            location.local_coordinate.to_payload()
                        )
                else:
                    trajectory["position"] = position.coordinate.to_payload()
                self._append(
                    "trajectory",
                    event.simulation_tick,
                    event.simulation_time,
                    agent_id,
                    trajectory,
                    event.event_id,
                )

    def _environment_state(
        self,
        simulation_time: float,
    ) -> dict[str, JsonValue]:
        registry = self.runner.registry
        payload: dict[str, JsonValue] = {
            "time": None,
            "weather": None,
            "effects": None,
            "surface_conditions": [],
            "availability": [],
        }
        if registry.has_resource(SimulationCalendar):
            payload["time"] = registry.get_resource(
                SimulationCalendar
            ).payload_at(simulation_time)
        if registry.has_resource(WeatherRuntime):
            weather = registry.get_resource(WeatherRuntime)
            payload["weather"] = weather.current.to_payload()
            payload["effects"] = {
                "walking_speed_multiplier": (
                    weather.effects.walking_speed_multiplier
                ),
                "cycling_speed_multiplier": (
                    weather.effects.cycling_speed_multiplier
                ),
                "visibility_multiplier": (
                    weather.effects.visibility_multiplier
                ),
            }
        if registry.has_resource(SurfaceConditionRegistry):
            surfaces = registry.get_resource(SurfaceConditionRegistry)
            payload["surface_conditions"] = [
                {
                    "surface_id": surface_id,
                    "wetness": value,
                    "band": wetness_band(value).value,
                }
                for surface_id, value in sorted(surfaces.wetness.items())
            ]
        if registry.has_resource(EnvironmentAvailabilityRegistry):
            availability = registry.get_resource(
                EnvironmentAvailabilityRegistry
            )
            kinds = (
                {
                    rule.resource_id: rule.resource_kind
                    for rule in registry.get_resource(
                        EnvironmentAvailabilityRules
                    ).rules
                }
                if registry.has_resource(EnvironmentAvailabilityRules)
                else {}
            )
            payload["availability"] = [
                {
                    "resource_id": resource_id,
                    "resource_kind": kinds.get(resource_id),
                    **state.to_payload(),
                }
                for resource_id, state in sorted(availability.states.items())
            ]
        return payload

    def _initialize_activities(self, event: DomainEvent) -> None:
        for agent_id, activity in self.runner.registry.query(ActivityComponent):
            self._activities[agent_id] = _ActivityInterval(
                activity=activity.current.value,
                start_tick=event.simulation_tick,
                start_time=event.simulation_time,
            )

    def _change_activity(self, event: DomainEvent) -> None:
        agent_id = event.agent_id
        if agent_id is None:
            return
        self._close_activity(
            agent_id,
            event.simulation_tick,
            event.simulation_time,
            source_event_id=event.event_id,
        )
        current = event.payload.get("current")
        if isinstance(current, str):
            self._activities[agent_id] = _ActivityInterval(
                activity=current,
                start_tick=event.simulation_tick,
                start_time=event.simulation_time,
            )

    def _close_activity(
        self,
        agent_id: str,
        end_tick: int,
        end_time: float,
        *,
        source_event_id: str | None,
    ) -> None:
        interval = self._activities.pop(agent_id, None)
        if interval is None:
            return
        self._append(
            "activity_interval",
            end_tick,
            end_time,
            agent_id,
            {
                "activity": interval.activity,
                "start_tick": interval.start_tick,
                "end_tick": end_tick,
                "start_time": interval.start_time,
                "end_time": end_time,
                "duration": round(max(0.0, end_time - interval.start_time), 12),
            },
            source_event_id,
        )

    def _memory_payload(self, event: DomainEvent) -> dict[str, JsonValue]:
        memory_id = event.payload.get("memory_id")
        if not isinstance(memory_id, str):
            return {}
        store = self.runner.registry.get_resource(EpisodicMemoryStore)
        record = next(
            (candidate for candidate in store.records if candidate.id == memory_id),
            None,
        )
        if record is None:
            return {}
        return {
            "text": record.text,
            "embedding": list(record.embedding),
            "memory_metadata": record.metadata,
        }

    def _append(
        self,
        record_type: str,
        tick: int,
        simulation_time: float,
        agent_id: str | None,
        payload: dict[str, JsonValue],
        source_event_id: str | None,
    ) -> None:
        self._sequence += 1
        self.store.append(
            DatasetRecord(
                run_id=self.run_id,
                sequence=self._sequence,
                record_type=record_type,
                simulation_tick=tick,
                simulation_time=simulation_time,
                agent_id=agent_id,
                payload=payload,
                source_event_id=source_event_id,
            )
        )
