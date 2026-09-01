from dataclasses import dataclass
from typing import Protocol

from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import SpatialLocationComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import (
    EnvironmentAvailabilityRegistry,
    SurfaceConditionRegistry,
    WeatherRuntime,
)
from stage0_sim.domain.events import JsonValue

ENVIRONMENT_TOPICS = frozenset(
    {"time", "weather", "surface_conditions", "availability"}
)


@dataclass(frozen=True, slots=True)
class EnvironmentAccessRequest:
    character_id: str
    topics: frozenset[str]
    simulation_time: float


class EnvironmentAccessPolicy(Protocol):
    def allowed_topics(
        self,
        registry: Registry,
        request: EnvironmentAccessRequest,
    ) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class AlwaysAvailableEnvironmentPolicy:
    def allowed_topics(
        self,
        registry: Registry,
        request: EnvironmentAccessRequest,
    ) -> frozenset[str]:
        del registry
        return request.topics & ENVIRONMENT_TOPICS


@dataclass(frozen=True, slots=True)
class EnvironmentInformationResult:
    values: dict[str, JsonValue]
    unavailable_topics: tuple[str, ...]

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "values": self.values,
            "unavailable_topics": list(self.unavailable_topics),
        }


class EnvironmentInformationService:
    def __init__(
        self,
        registry: Registry,
        policy: EnvironmentAccessPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or AlwaysAvailableEnvironmentPolicy()

    def query(
        self,
        character_id: str,
        simulation_time: float,
        topics: frozenset[str] = ENVIRONMENT_TOPICS,
        *,
        availability_resource_ids: frozenset[str] | None = None,
    ) -> EnvironmentInformationResult:
        unknown = topics - ENVIRONMENT_TOPICS
        if unknown:
            raise ValueError(f"unknown environment topics: {sorted(unknown)}")
        request = EnvironmentAccessRequest(character_id, topics, simulation_time)
        allowed = self.policy.allowed_topics(self.registry, request)
        values: dict[str, JsonValue] = {}
        if "time" in allowed and self.registry.has_resource(SimulationCalendar):
            values["time"] = self.registry.get_resource(
                SimulationCalendar
            ).payload_at(simulation_time)
        if "weather" in allowed and self.registry.has_resource(WeatherRuntime):
            weather = self.registry.get_resource(WeatherRuntime)
            values["weather"] = {
                **weather.current.to_payload(),
                "walking_speed_multiplier": weather.effects.walking_speed_multiplier,
                "cycling_speed_multiplier": weather.effects.cycling_speed_multiplier,
                "visibility_multiplier": weather.effects.visibility_multiplier,
            }
        surface_id = self._surface_id(character_id)
        if (
            "surface_conditions" in allowed
            and surface_id is not None
            and self.registry.has_resource(SurfaceConditionRegistry)
        ):
            values["surface_conditions"] = self.registry.get_resource(
                SurfaceConditionRegistry
            ).payload(surface_id)
        if (
            "availability" in allowed
            and self.registry.has_resource(EnvironmentAvailabilityRegistry)
        ):
            availability = self.registry.get_resource(
                EnvironmentAvailabilityRegistry
            )
            values["availability"] = {
                resource_id: state.to_payload()
                for resource_id, state in sorted(availability.states.items())
                if availability_resource_ids is None
                or resource_id in availability_resource_ids
            }
        return EnvironmentInformationResult(
            values,
            tuple(sorted(topics - allowed)),
        )

    def _surface_id(self, character_id: str) -> str | None:
        if not self.registry.has_component(character_id, SpatialLocationComponent):
            return None
        location = self.registry.get_component(
            character_id, SpatialLocationComponent
        ).location
        if location.local_coordinate is not None:
            return None
        if location.edge_id is not None:
            return f"edge:{location.edge_id}"
        return f"place:{location.place_id}"
