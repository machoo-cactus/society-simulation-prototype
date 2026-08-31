import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stage0_sim.adapters.llm import (
    FakeDialogueGenerator,
    FakeEmbeddingProvider,
    FakePlanner,
)
from stage0_sim.application.agents import (
    AgentWorkCoordinator,
    CognitionScheduler,
    ToolCallingCharacterController,
)
from stage0_sim.application.agents.contracts import ModelClient
from stage0_sim.application.agents.profile_renderer import (
    CharacterDescriptionRenderer,
)
from stage0_sim.application.agents.tools import ToolRegistry
from stage0_sim.application.cognition import (
    DialogueGenerator,
    EmbeddingProvider,
    Planner,
)
from stage0_sim.application.dialogue import MacroDialogueSystem
from stage0_sim.application.environment import EnvironmentInformationService
from stage0_sim.application.information import InformationRetriever, InformationStore
from stage0_sim.application.macro_work import MacroWorkCoordinator
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryConfiguration
from stage0_sim.application.memory_recording import (
    MemoryRecordingSystem,
    observation_metadata,
)
from stage0_sim.application.navigation import (
    InformationKnownTopologyProjection,
    NavigationKnowledgeRecordingSystem,
    NavigationPlanningSystem,
    NavigationService,
    RecursiveRoutePlanner,
)
from stage0_sim.application.perception import (
    PerceptionConfiguration,
    PerceptionSystem,
)
from stage0_sim.application.planning import MacroPlanningSystem
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActionType,
    ActivityComponent,
    ActivityRates,
    ActivityType,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    DriveThreshold,
    DriveType,
    HomeostasisComponent,
    HomeostasisConfiguration,
    InformationNamespaceComponent,
    MemoryComponent,
    MovementComponent,
    NavigationComponent,
    PerceptionComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    SensesComponent,
    SpatialLocationComponent,
    System1Configuration,
    TravelComponent,
    default_activity_rates,
    default_drive_thresholds,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import (
    AvailabilityRule,
    EnvironmentAvailabilityRegistry,
    EnvironmentAvailabilityRules,
    SurfaceConditionRegistry,
    WeatherCondition,
    WeatherEffects,
    WeatherRuntime,
    WeatherState,
    WeatherTimeline,
    WeatherTransition,
    WeeklyOpeningWindow,
    WeeklySchedule,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    InformationSource,
    TimeRange,
    VisibilityLevel,
    VisibilityPolicy,
    character_dossier_document_id,
    character_information_namespace_id,
)
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.affordances import AffordanceExecutionSystem
from stage0_sim.domain.systems.calendar import CalendarUpdateSystem
from stage0_sim.domain.systems.environment import (
    EnvironmentAvailabilitySystem,
    SurfaceConditionSystem,
    WeatherUpdateSystem,
)
from stage0_sim.domain.systems.homeostasis import (
    HomeostasisSystem,
    MovementActivitySystem,
)
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.systems.plans import PlanExecutionSystem, TimedPlanActionSystem
from stage0_sim.domain.systems.speech import SpeechSystem
from stage0_sim.domain.systems.system1 import System1ArbitrationSystem
from stage0_sim.domain.systems.travel import TravelSystem
from stage0_sim.domain.world import (
    AffordanceAction,
    AffordanceStation,
    Building,
    BuildingEntrance,
    CityBounds,
    CityWorld,
    Coordinate,
    District,
    GridTopology,
    HomeostasisEffect,
    MapPoint,
    OutdoorPlace,
    Space,
    SpaceRegistry,
    SparseGraphTopology,
    SpatialScale,
    Transition,
    TransportEdge,
    TransportNode,
    TravelMode,
    TraversalContext,
    Vehicle,
    VehicleRegistry,
    VehicleState,
    WorldGrid,
    WorldLocation,
    WorldMap,
    Zone,
    default_affordance_action,
)


class CoordinateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int

    def to_domain(self) -> Coordinate:
        return Coordinate(self.x, self.y)


class BoundsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def tiles(self) -> frozenset[Coordinate]:
        return frozenset(
            Coordinate(x, y)
            for y in range(self.y, self.y + self.height)
            for x in range(self.x, self.x + self.width)
        )


WEEKDAY_NUMBERS = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}
WeekdayName = Literal[
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]


class OpeningWindowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekdays: list[WeekdayName] = Field(min_length=1)
    opens: time
    closes: time

    def to_domain(self) -> WeeklyOpeningWindow:
        return WeeklyOpeningWindow(
            frozenset(WEEKDAY_NUMBERS[day] for day in self.weekdays),
            self.opens.hour * 60 + self.opens.minute,
            self.closes.hour * 60 + self.closes.minute,
        )


class WeeklyScheduleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    windows: list[OpeningWindowDefinition] = Field(min_length=1)

    def to_domain(self) -> WeeklySchedule:
        return WeeklySchedule(tuple(window.to_domain() for window in self.windows))


class EnvironmentalAvailabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: WeeklyScheduleDefinition | None = None
    closed_weather: list[WeatherCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def weather_conditions_are_unique(self) -> "EnvironmentalAvailabilityDefinition":
        if len(self.closed_weather) != len(set(self.closed_weather)):
            raise ValueError("closed_weather conditions must be unique")
        return self


class ZoneDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    bounds: BoundsDefinition | None = None
    tiles: list[CoordinateDefinition] | None = None

    @model_validator(mode="after")
    def has_one_tile_shape(self) -> "ZoneDefinition":
        if (self.bounds is None) == (self.tiles is None):
            raise ValueError("zone must define exactly one of bounds or tiles")
        if self.tiles is not None and not self.tiles:
            raise ValueError("zone tiles must not be empty")
        return self


class StationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    position: CoordinateDefinition
    supported_actions: list[ActionType] | None = None
    actions: list["StationActionDefinition"] | None = None
    available: bool = True
    capacity: int = Field(default=1, gt=0)
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )

    @model_validator(mode="after")
    def has_one_action_format(self) -> "StationDefinition":
        if (self.supported_actions is None) == (self.actions is None):
            raise ValueError(
                "station must define exactly one of supported_actions or actions"
            )
        if self.supported_actions == [] or self.actions == []:
            raise ValueError("station actions must not be empty")
        return self


class HomeostasisEffectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satiety_delta: float = 0.0
    energy_delta: float = 0.0
    stress_delta: float = 0.0
    satiety_target: float | None = Field(default=None, ge=0, le=100)
    energy_target: float | None = Field(default=None, ge=0, le=100)
    stress_target: float | None = Field(default=None, ge=0, le=100)

    def to_domain(self) -> HomeostasisEffect:
        return HomeostasisEffect(
            satiety_delta=self.satiety_delta,
            energy_delta=self.energy_delta,
            stress_delta=self.stress_delta,
            satiety_target=self.satiety_target,
            energy_target=self.energy_target,
            stress_target=self.stress_target,
        )


class StationActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionType
    duration: float = Field(gt=0)
    effect: HomeostasisEffectDefinition = Field(
        default_factory=HomeostasisEffectDefinition
    )

    def to_domain(self) -> AffordanceAction:
        return AffordanceAction(
            action=self.action.value,
            duration=self.duration,
            effect=self.effect.to_domain(),
        )


class WorldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    blocked: list[CoordinateDefinition] = Field(default_factory=list)
    zones: list[ZoneDefinition] = Field(default_factory=list)
    stations: list[StationDefinition] = Field(default_factory=list)


class MapPointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float

    def to_domain(self) -> MapPoint:
        return MapPoint(self.x, self.y)


class CityBoundsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> "CityBoundsDefinition":
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("city bounds maximums must exceed minimums")
        return self


class CityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    bounds_meters: CityBoundsDefinition


class DistrictDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    center: MapPointDefinition


class BuildingEntranceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    local_coordinate: CoordinateDefinition
    neighborhood_node_id: str = Field(min_length=1)


class BuildingDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    district_id: str = Field(min_length=1)
    city_position: MapPointDefinition
    local_map_id: str = Field(min_length=1)
    entrances: list[BuildingEntranceDefinition] = Field(min_length=1)
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )


class OutdoorPlaceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    district_id: str = Field(min_length=1)
    city_position: MapPointDefinition
    network_node_id: str = Field(min_length=1)
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )


class TransportNodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    position: MapPointDefinition
    place_id: str | None = None


class TransportEdgeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    allowed_modes: list[TravelMode] = Field(min_length=1)
    distance_meters: float = Field(gt=0)
    geometry: list[MapPointDefinition] = Field(min_length=2)
    speed_limit_mps: float | None = Field(default=None, gt=0)
    bidirectional: bool = False
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )


class VehicleLocationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: SpatialScale
    place_id: str = Field(min_length=1)
    network_node_id: str = Field(min_length=1)


class VehicleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: TravelMode
    name: str = Field(min_length=1)
    capacity: int = Field(gt=0)
    location: VehicleLocationDefinition
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )


class TransportDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[TransportNodeDefinition] = Field(default_factory=list)
    edges: list[TransportEdgeDefinition] = Field(default_factory=list)
    metro_lines: list[dict[str, Any]] = Field(default_factory=list)
    vehicles: list[VehicleDefinition] = Field(default_factory=list)
    walking_speed_mps: float = Field(default=1.4, gt=0)
    cycling_speed_mps: float = Field(default=4.5, gt=0)
    car_speed_mps: float = Field(default=13.9, gt=0)
    metro_speed_mps: float = Field(default=16.0, gt=0)


class CityWorldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["city"]
    city: CityDefinition
    districts: list[DistrictDefinition]
    buildings: list[BuildingDefinition]
    outdoor_places: list[OutdoorPlaceDefinition] = Field(default_factory=list)
    local_maps: dict[str, WorldDefinition]
    transport: TransportDefinition

    @model_validator(mode="after")
    def references_are_valid(self) -> "CityWorldDefinition":
        district_ids = {item.id for item in self.districts}
        map_ids = set(self.local_maps)
        node_ids = {item.id for item in self.transport.nodes}
        all_ids = [
            self.city.id,
            *(item.id for item in self.districts),
            *(item.id for item in self.buildings),
            *(item.id for item in self.outdoor_places),
            *self.local_maps,
            *(item.id for item in self.transport.nodes),
            *(item.id for item in self.transport.edges),
            *(item.id for item in self.transport.vehicles),
            *(
                entrance.id
                for building in self.buildings
                for entrance in building.entrances
            ),
            *(
                zone.id
                for local_map in self.local_maps.values()
                for zone in local_map.zones
            ),
            *(
                station.id
                for local_map in self.local_maps.values()
                for station in local_map.stations
            ),
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("city world IDs must be globally unique")
        for building in self.buildings:
            if building.district_id not in district_ids:
                raise ValueError(
                    f"building {building.id} references unknown district"
                )
            if building.local_map_id not in map_ids:
                raise ValueError(
                f"building {building.id} references unknown local map"
                )
            local_map = self.local_maps[building.local_map_id]
            for entrance in building.entrances:
                coordinate = entrance.local_coordinate.to_domain()
                if not (
                    0 <= coordinate.x < local_map.width
                    and 0 <= coordinate.y < local_map.height
                ):
                    raise ValueError(
                        f"entrance {entrance.id} is outside local map"
                    )
                if entrance.neighborhood_node_id not in node_ids:
                    raise ValueError(
                        f"entrance {entrance.id} references unknown node"
                    )
        for place in self.outdoor_places:
            if place.district_id not in district_ids:
                raise ValueError(
                f"outdoor place {place.id} references unknown district"
                )
            if place.network_node_id not in node_ids:
                raise ValueError(
                f"outdoor place {place.id} references unknown node"
                )
        for edge in self.transport.edges:
            if edge.from_node_id not in node_ids or edge.to_node_id not in node_ids:
                raise ValueError(f"edge {edge.id} references unknown node")
            start = edge.geometry[0]
            end = edge.geometry[-1]
            node_map = {item.id: item for item in self.transport.nodes}
            from_node = node_map[edge.from_node_id].position
            to_node = node_map[edge.to_node_id].position
            if (start.x, start.y) != (from_node.x, from_node.y) or (
                end.x,
                end.y,
            ) != (to_node.x, to_node.y):
                raise ValueError(
                    f"edge {edge.id} geometry endpoints must match nodes"
                )
        for vehicle in self.transport.vehicles:
            if vehicle.location.network_node_id not in node_ids:
                raise ValueError(
                    f"vehicle {vehicle.id} references unknown node"
                )
            if vehicle.type not in {TravelMode.CAR, TravelMode.CYCLE}:
                raise ValueError(
                    f"vehicle {vehicle.id} must be CAR or CYCLE"
                )
        return self


class ActivityRatesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satiety: float
    energy: float
    stress: float

    def to_domain(self) -> ActivityRates:
        return ActivityRates(
            satiety=self.satiety,
            energy=self.energy,
            stress=self.stress,
        )


class HomeostasisSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_coefficients: dict[ActivityType, ActivityRatesDefinition] = Field(
        default_factory=dict
    )

    def to_domain(self) -> HomeostasisConfiguration:
        rates = default_activity_rates()
        rates.update(
            {
                activity: definition.to_domain()
                for activity, definition in self.activity_coefficients.items()
            }
        )
        return HomeostasisConfiguration(activity_rates=rates)


class DriveThresholdDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical: float = Field(ge=0, le=100)
    recovery: float = Field(ge=0, le=100)
    critical_when_high: bool = False

    def to_domain(self) -> DriveThreshold:
        return DriveThreshold(
            critical=self.critical,
            recovery=self.recovery,
            critical_when_high=self.critical_when_high,
        )


class System1SettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thresholds: dict[DriveType, DriveThresholdDefinition] = Field(default_factory=dict)
    tie_break_order: list[DriveType] = Field(default_factory=lambda: list(DriveType))

    def to_domain(self) -> System1Configuration:
        thresholds = default_drive_thresholds()
        thresholds.update(
            {
                drive: definition.to_domain()
                for drive, definition in self.thresholds.items()
            }
        )
        return System1Configuration(
            thresholds=thresholds,
            tie_break_order=tuple(self.tie_break_order),
        )


class MemorySettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_weight: float = Field(default=0.6, ge=0)
    recency_weight: float = Field(default=0.25, ge=0)
    importance_weight: float = Field(default=0.15, ge=0)
    recency_half_life: float = Field(default=3600.0, gt=0)

    def to_domain(self) -> MemoryConfiguration:
        return MemoryConfiguration(
            semantic_weight=self.semantic_weight,
            recency_weight=self.recency_weight,
            importance_weight=self.importance_weight,
            recency_half_life=self.recency_half_life,
        )


class PerceptionSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision_range: int = Field(default=8, ge=0)
    recognition_range: int = Field(default=5, ge=0)
    hearing_range: int = Field(default=10, ge=0)
    whisper_range: int = Field(default=2, ge=0)
    blocked_tiles_are_opaque: bool = True
    inbox_limit: int = Field(default=100, gt=0)
    fact_max_age_seconds: float = Field(default=300.0, gt=0)
    renderer: str = "deterministic"

    def to_domain(self) -> PerceptionConfiguration:
        if self.renderer != "deterministic":
            raise ValueError("only the deterministic perception renderer is supported")
        return PerceptionConfiguration(
            vision_range=self.vision_range,
            recognition_range=self.recognition_range,
            hearing_range=self.hearing_range,
            whisper_range=self.whisper_range,
            blocked_tiles_are_opaque=self.blocked_tiles_are_opaque,
            inbox_limit=self.inbox_limit,
            fact_max_age_seconds=self.fact_max_age_seconds,
        )


class CognitionSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller: str = "legacy"
    execution_mode: Literal["global_barrier", "background"] = "global_barrier"
    model_profile: str = "default"
    decision_timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    max_read_tool_rounds: int = Field(default=1, ge=0, le=4)
    max_state_changing_tools: int = Field(default=1, ge=1, le=1)
    max_concurrency: int = Field(default=4, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_total_output_tokens: int | None = Field(default=None, gt=0)
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "navigate_to",
            "go_to",
            "perform",
            "say",
            "wait",
            "skip",
            "travel_to",
            "check_environment",
        ]
    )

    @model_validator(mode="after")
    def supported_values(self) -> "CognitionSettingsDefinition":
        if self.controller not in {"legacy", "tool-agent"}:
            raise ValueError("cognition controller must be legacy or tool-agent")
        unknown = set(self.tool_allowlist) - {
            "navigate_to",
            "go_to",
            "perform",
            "say",
            "wait",
            "skip",
            "travel_to",
            "check_environment",
        }
        if unknown:
            raise ValueError(f"unknown cognition tools: {sorted(unknown)}")
        return self


class CharacterProfileTemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    sections: list[str] = Field(
        default_factory=lambda: [
            "identity",
            "appearance",
            "personality",
            "background",
            "motivations",
            "capabilities",
            "preferences",
            "relationships",
        ]
    )

    @model_validator(mode="after")
    def sections_are_unique(self) -> "CharacterProfileTemplateDefinition":
        if len(self.sections) != len(set(self.sections)):
            raise ValueError("character profile template sections must be unique")
        return self


HUMAN_V1_TEMPLATE = CharacterProfileTemplateDefinition()


class CharacterSelectionConstraintsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_age: int | None = Field(default=None, ge=0, le=150)
    maximum_age: int | None = Field(default=None, ge=0, le=150)
    allowed_genders: list[str] = Field(default_factory=list)
    allowed_template_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def constraints_are_consistent(self) -> "CharacterSelectionConstraintsDefinition":
        if (
            self.minimum_age is not None
            and self.maximum_age is not None
            and self.minimum_age > self.maximum_age
        ):
            raise ValueError("minimum_age must not exceed maximum_age")
        self.allowed_genders = _normalized_constraint_values(
            self.allowed_genders,
            "allowed_genders",
            casefold=True,
        )
        self.allowed_template_ids = _normalized_constraint_values(
            self.allowed_template_ids,
            "allowed_template_ids",
        )
        return self


class CharacterSlotDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    briefing: str = ""
    default_character_id: str | None = Field(default=None, min_length=1)
    constraints: CharacterSelectionConstraintsDefinition = Field(
        default_factory=CharacterSelectionConstraintsDefinition
    )


def _normalized_constraint_values(
    values: list[str],
    field_name: str,
    *,
    casefold: bool = False,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        if not clean:
            raise ValueError(f"{field_name} values must not be blank")
        key = clean.casefold() if casefold else clean
        if key in seen:
            raise ValueError(f"{field_name} values must be unique")
        seen.add(key)
        normalized.append(clean)
    return normalized


class EntityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    components: dict[str, dict[str, Any]] = Field(default_factory=dict)


class CalendarSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_datetime: datetime
    update_interval_seconds: float = Field(default=1800.0, gt=0)

    @model_validator(mode="after")
    def start_datetime_has_offset(self) -> "CalendarSettingsDefinition":
        if self.start_datetime.utcoffset() is None:
            raise ValueError("start_datetime must include a UTC offset")
        return self

    def to_domain(self) -> SimulationCalendar:
        return SimulationCalendar(
            start_datetime=self.start_datetime,
            update_interval_seconds=self.update_interval_seconds,
        )


class WeatherStateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: WeatherCondition
    temperature_c: float
    precipitation_mm_per_hour: float = Field(default=0.0, ge=0)
    wind_speed_mps: float = Field(default=0.0, ge=0)
    wind_direction_degrees: float = Field(default=0.0, ge=0, lt=360)
    visibility_meters: float = Field(default=10_000.0, gt=0)

    def to_domain(self) -> WeatherState:
        return WeatherState(
            condition=self.condition,
            temperature_c=self.temperature_c,
            precipitation_mm_per_hour=self.precipitation_mm_per_hour,
            wind_speed_mps=self.wind_speed_mps,
            wind_direction_degrees=self.wind_direction_degrees,
            visibility_meters=self.visibility_meters,
        )


class WeatherTransitionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_seconds: float = Field(gt=0)
    state: WeatherStateDefinition


class WeatherEffectsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    walking_speed_multiplier: float = Field(default=1.0, gt=0)
    cycling_speed_multiplier: float = Field(default=1.0, gt=0)
    visibility_multiplier: float = Field(default=1.0, gt=0)
    wetness_gain_per_mm_hour_second: float = Field(default=0.00002, ge=0)
    base_drying_per_second: float = Field(default=0.00005, ge=0)
    wind_drying_per_mps_second: float = Field(default=0.000005, ge=0)
    temperature_drying_per_degree_second: float = Field(default=0.000001, ge=0)

    def to_domain(self) -> WeatherEffects:
        return WeatherEffects(**self.model_dump())


class WeatherSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial: WeatherStateDefinition
    transitions: list[WeatherTransitionDefinition] = Field(default_factory=list)
    effects: dict[WeatherCondition, WeatherEffectsDefinition] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def transitions_are_unique_and_increasing(self) -> "WeatherSettingsDefinition":
        times = [transition.at_seconds for transition in self.transitions]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("weather transition times must be unique and increasing")
        return self

    def to_domain(self) -> WeatherRuntime:
        return WeatherRuntime(
            WeatherTimeline(
                self.initial.to_domain(),
                tuple(
                    WeatherTransition(
                        transition.at_seconds,
                        transition.state.to_domain(),
                    )
                    for transition in self.transitions
                ),
            ),
            {
                condition: effects.to_domain()
                for condition, effects in self.effects.items()
            },
        )


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=2)
    name: str = Field(min_length=1)
    seed: int = 0
    dt: float = Field(default=1.0, gt=0)
    speed: float = Field(default=1.0, gt=0)
    run_id: str | None = Field(default=None, min_length=1)
    calendar: CalendarSettingsDefinition | None = None
    weather: WeatherSettingsDefinition | None = None
    world: WorldDefinition | CityWorldDefinition | None = None
    homeostasis: HomeostasisSettingsDefinition = Field(
        default_factory=HomeostasisSettingsDefinition
    )
    system1: System1SettingsDefinition = Field(
        default_factory=System1SettingsDefinition
    )
    memory: MemorySettingsDefinition = Field(default_factory=MemorySettingsDefinition)
    perception: PerceptionSettingsDefinition = Field(
        default_factory=PerceptionSettingsDefinition
    )
    cognition: CognitionSettingsDefinition = Field(
        default_factory=CognitionSettingsDefinition
    )
    entities: list[EntityDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def entity_ids_are_unique(self) -> "ScenarioDefinition":
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity IDs must be unique")
        for entity in self.entities:
            if "character_profile" in entity.components:
                raise ValueError(
                    f"entity {entity.id} uses removed character_profile; "
                    "migrate it to character_slot"
                )
            if isinstance(self.world, CityWorldDefinition) and (
                "position" in entity.components
                and "spatial_location" not in entity.components
            ):
                raise ValueError(
                    f"city entity {entity.id} requires spatial_location"
                )
            raw_slot = entity.components.get("character_slot")
            if raw_slot is not None:
                _validate_component(CharacterSlotDefinition, raw_slot, entity.id)
        if self.calendar is None and _scenario_uses_schedules(self.world):
            raise ValueError("weekly environment schedules require a calendar")
        return self


def _scenario_uses_schedules(
    world: WorldDefinition | CityWorldDefinition | None,
) -> bool:
    return any(rule.schedule is not None for rule in _environment_rules(world))


def _environment_rules(
    world: WorldDefinition | CityWorldDefinition | None,
) -> tuple[AvailabilityRule, ...]:
    rules: list[AvailabilityRule] = []
    if isinstance(world, WorldDefinition):
        for station in world.stations:
            rules.append(
                _availability_rule(
                    station.id,
                    "station",
                    station.available,
                    station.environment,
                )
            )
    elif isinstance(world, CityWorldDefinition):
        for local_map in world.local_maps.values():
            for station in local_map.stations:
                rules.append(
                    _availability_rule(
                        station.id,
                        "station",
                        station.available,
                        station.environment,
                    )
                )
        for building in world.buildings:
            rules.append(
                _availability_rule(
                    building.id,
                    "building",
                    building.available,
                    building.environment,
                )
            )
        for place in world.outdoor_places:
            rules.append(
                _availability_rule(
                    place.id,
                    "outdoor",
                    place.available,
                    place.environment,
                )
            )
        for edge in world.transport.edges:
            rules.append(
                _availability_rule(
                    edge.id,
                    "transport_edge",
                    edge.available,
                    edge.environment,
                )
            )
        for vehicle in world.transport.vehicles:
            rules.append(
                _availability_rule(
                    vehicle.id,
                    "vehicle",
                    vehicle.available,
                    vehicle.environment,
                )
            )
    return tuple(sorted(rules, key=lambda rule: (rule.resource_kind, rule.resource_id)))


def _availability_rule(
    resource_id: str,
    resource_kind: str,
    base_available: bool,
    definition: EnvironmentalAvailabilityDefinition,
) -> AvailabilityRule:
    return AvailabilityRule(
        resource_id=resource_id,
        resource_kind=resource_kind,
        base_available=base_available,
        schedule=(
            definition.schedule.to_domain()
            if definition.schedule is not None
            else None
        ),
        closed_weather=frozenset(definition.closed_weather),
    )


@dataclass(frozen=True, slots=True)
class ScenarioComponents:
    values: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ResolvedCharacterProfile:
    character_id: str
    profile: "CharacterProfileDefinition"


class ScenarioLoadError(ValueError):
    pass


def load_scenario(path: Path) -> ScenarioDefinition:
    try:
        raw_scenario = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScenarioLoadError(f"could not read scenario {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ScenarioLoadError(f"scenario is not valid JSON: {error}") from error

    try:
        return ScenarioDefinition.model_validate(raw_scenario)
    except ValidationError as error:
        raise ScenarioLoadError(f"scenario validation failed: {error}") from error


def create_runner(
    scenario: ScenarioDefinition,
    *,
    resolved_characters: Mapping[str, ResolvedCharacterProfile] | None = None,
    speed: float | None = None,
    run_id: str | None = None,
    planner: Planner | None = None,
    dialogue_generator: DialogueGenerator | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    model_client: ModelClient | None = None,
    model_max_output_tokens: int | None = None,
    model_max_concurrency: int | None = None,
) -> SimulationRunner:
    registry = Registry()
    systems = SystemExecutor()
    information_store = InformationStore()
    resolved_embedding_provider = embedding_provider or FakeEmbeddingProvider()
    memory_store = EpisodicMemoryStore(
        resolved_embedding_provider,
        scenario.memory.to_domain(),
        information_store,
    )
    information_retriever = InformationRetriever(
        information_store,
        resolved_embedding_provider,
    )
    macro_work = MacroWorkCoordinator(
        planner=planner or FakePlanner(),
        dialogue_generator=dialogue_generator or FakeDialogueGenerator(),
        memory_store=memory_store,
    )
    registry.set_resource(information_store)
    registry.set_resource(information_retriever)
    registry.set_resource(memory_store)
    registry.set_resource(macro_work)
    registry.set_resource(scenario.homeostasis.to_domain())
    registry.set_resource(scenario.system1.to_domain())
    registry.set_resource(scenario.perception.to_domain())
    registry.set_resource(SurfaceConditionRegistry())
    registry.set_resource(EnvironmentAvailabilityRegistry())
    availability_rules = EnvironmentAvailabilityRules(
        _environment_rules(scenario.world)
    )
    registry.set_resource(availability_rules)
    if scenario.weather is not None:
        registry.set_resource(scenario.weather.to_domain())
        systems.add(WeatherUpdateSystem())
        systems.add(SurfaceConditionSystem())
    if availability_rules.rules:
        systems.add(EnvironmentAvailabilitySystem())
    if scenario.calendar is not None:
        registry.set_resource(scenario.calendar.to_domain())
        systems.add(CalendarUpdateSystem())
    registry.set_resource(EnvironmentInformationService(registry))
    systems.add(MovementActivitySystem())
    systems.add(HomeostasisSystem())
    systems.add(TimedPlanActionSystem())
    systems.add(System1ArbitrationSystem())
    systems.add(SpeechSystem())
    systems.add(MemoryRecordingSystem())
    systems.add(MacroDialogueSystem())
    systems.add(MacroPlanningSystem())
    city_world = (
        _build_city_world(scenario.world)
        if isinstance(scenario.world, CityWorldDefinition)
        else None
    )
    world = (
        _initial_city_local_map(scenario, city_world)
        if city_world is not None
        else _build_world(scenario.world)
        if isinstance(scenario.world, WorldDefinition)
        else None
    )
    if city_world is not None:
        registry.set_resource(city_world)
        registry.set_resource(
            VehicleRegistry(
                {
                    vehicle.id: VehicleState(
                        network_node_id=vehicle.network_node_id
                    )
                    for vehicle in city_world.vehicles
                }
            )
        )
        systems.add(TravelSystem())
    if world is not None:
        registry.set_resource(world)
        space_registry = _build_space_registry(world, city_world)
        registry.set_resource(space_registry)
        known_topology = InformationKnownTopologyProjection(
            information_store,
            space_registry,
            registry,
        )
        registry.set_resource(
            NavigationService(
                registry,
                space_registry,
                known_topology,
            )
        )
        systems.add(PathfindingSystem())
        systems.add(NavigationPlanningSystem())
        systems.add(PlanExecutionSystem())
        systems.add(AffordanceExecutionSystem())
        systems.add(MovementSystem())
        systems.add(NavigationKnowledgeRecordingSystem())
        systems.add(PerceptionSystem())

    tool_registry = ToolRegistry()
    tool_agent_enabled = scenario.cognition.controller == "tool-agent" or any(
        bool(entity.components.get("controller", {}).get("enabled", False))
        for entity in scenario.entities
    )
    if tool_agent_enabled:
        if model_client is None:
            raise ValueError(
                "tool-agent cognition requires an explicit model client; "
                "configure STAGE0_LLM_PROVIDER or pass model_client"
            )
        controller = ToolCallingCharacterController(
            model_client=model_client,
            tool_registry=tool_registry,
            model=scenario.cognition.model_profile,
            timeout_seconds=scenario.cognition.decision_timeout_seconds,
            max_output_tokens=(
                min(
                    scenario.cognition.max_output_tokens,
                    model_max_output_tokens,
                )
                if model_max_output_tokens is not None
                else scenario.cognition.max_output_tokens
            ),
            max_read_tool_rounds=scenario.cognition.max_read_tool_rounds,
        )
        registry.set_resource(
            AgentWorkCoordinator(
                controller,
                tool_registry,
                max_concurrency=(
                    min(
                        scenario.cognition.max_concurrency,
                        model_max_concurrency,
                    )
                    if model_max_concurrency is not None
                    else scenario.cognition.max_concurrency
                ),
                request_timeout_seconds=(
                    scenario.cognition.decision_timeout_seconds
                ),
                max_requests=scenario.cognition.max_requests,
                max_input_tokens=scenario.cognition.max_input_tokens,
                max_output_tokens=scenario.cognition.max_total_output_tokens,
                memory_store=memory_store,
                information_retriever=information_retriever,
                execution_mode=scenario.cognition.execution_mode,
            )
        )
        systems.add(CognitionScheduler())

    occupied: set[tuple[str, Coordinate]] = set()
    for entity_definition in scenario.entities:
        entity_id = registry.create_entity(entity_definition.id)
        raw_components = dict(entity_definition.components)
        spatial_values = raw_components.pop("spatial_location", None)
        if spatial_values is not None:
            if city_world is None:
                raise ValueError(
                    "spatial_location requires a city world definition"
                )
            spatial_definition = _validate_component(
                SpatialLocationDefinition, spatial_values, entity_id
            )
            spatial_location = spatial_definition.to_domain()
            _validate_spatial_location(city_world, spatial_location)
            registry.add_component(
                entity_id,
                SpatialLocationComponent(
                    spatial_location,
                    city_space_id=city_world.id,
                ),
            )
            registry.add_component(entity_id, TravelComponent())
            if spatial_location.local_coordinate is not None:
                raw_components.setdefault(
                    "position",
                    {
                        "x": spatial_location.local_coordinate.x,
                        "y": spatial_location.local_coordinate.y,
                    },
                )
        if "position" in raw_components:
            if world is None:
                raise ValueError("entity positions require a world definition")
            position_definition = _validate_component(
                PositionDefinition, raw_components.pop("position"), entity_id
            )
            coordinate = position_definition.to_domain()
            if not world.grid.is_walkable(coordinate):
                raise ValueError(
                    f"entity {entity_id} position must be on a walkable grid tile"
                )
            occupied_place = (
                str(spatial_values.get("place_id"))
                if isinstance(spatial_values, dict)
                else "implicit-building"
            )
            occupied_key = (occupied_place, coordinate)
            if occupied_key in occupied:
                raise ValueError(f"multiple entities occupy initial tile {coordinate}")
            occupied.add(occupied_key)
            registry.add_component(entity_id, PositionComponent(coordinate))

        if "movement" in raw_components:
            if world is None:
                raise ValueError("entity movement requires a world definition")
            if "position" not in entity_definition.components:
                raise ValueError(f"moving entity {entity_id} requires a position component")
            movement_definition = _validate_component(
                MovementDefinition, raw_components.pop("movement"), entity_id
            )
            destination = (
                movement_definition.destination.to_domain()
                if movement_definition.destination is not None
                else None
            )
            if destination is not None and not world.grid.is_walkable(destination):
                raise ValueError(
                    f"entity {entity_id} destination must be on a walkable grid tile"
                )
            registry.add_component(
                entity_id,
                MovementComponent(destination=destination),
            )

        if "homeostasis" in raw_components:
            homeostasis_definition = _validate_component(
                HomeostasisComponentDefinition,
                raw_components.pop("homeostasis"),
                entity_id,
            )
            registry.add_component(
                entity_id,
                HomeostasisComponent(
                    satiety=homeostasis_definition.satiety,
                    energy=homeostasis_definition.energy,
                    stress=homeostasis_definition.stress,
                ),
            )
            registry.add_component(entity_id, DriveComponent())

        if "activity" in raw_components:
            activity_definition = _validate_component(
                ActivityDefinition, raw_components.pop("activity"), entity_id
            )
            registry.add_component(
                entity_id,
                ActivityComponent(current=activity_definition.type),
            )
        elif "homeostasis" in entity_definition.components:
            registry.add_component(entity_id, ActivityComponent())

        if "plan" in raw_components:
            plan_definition = _validate_component(
                PlanComponentDefinition, raw_components.pop("plan"), entity_id
            )
            registry.add_component(
                entity_id,
                PlanComponent(
                    queue=[action.to_domain() for action in plan_definition.queue],
                    current=(
                        plan_definition.current.to_domain()
                        if plan_definition.current is not None
                        else None
                    ),
                ),
            )

        planner_definition: PlannerComponentDefinition | None = None
        if "planner" in raw_components:
            planner_definition = _validate_component(
                PlannerComponentDefinition,
                raw_components.pop("planner"),
                entity_id,
            )
            registry.add_component(
                entity_id,
                PlannerComponent(
                    daily_goals=tuple(planner_definition.daily_goals),
                    current_priorities=tuple(
                        planner_definition.current_priorities
                    ),
                    needs_plan=planner_definition.needs_plan,
                ),
            )
            if not registry.has_component(entity_id, PlanComponent):
                registry.add_component(entity_id, PlanComponent())

        information_values = raw_components.pop("information", None)
        information_definition = (
            _validate_component(
                InformationComponentDefinition,
                information_values,
                entity_id,
            )
            if information_values is not None
            else InformationComponentDefinition()
        )
        metadata_values = raw_components.get("metadata", {})
        raw_slot = raw_components.pop("character_slot", None)
        slot_definition = (
            _validate_component(
                CharacterSlotDefinition,
                raw_slot,
                entity_id,
            )
            if raw_slot is not None
            else CharacterSlotDefinition(
                label=str(metadata_values.get("display_name", entity_id))
            )
        )
        resolved_character = (resolved_characters or {}).get(entity_id)
        if resolved_character is None:
            profile_definition = CharacterProfileDefinition(
                identity=CharacterIdentityDefinition(
                    display_name=str(
                        metadata_values.get("display_name", entity_id)
                    )
                )
            )
            profile_id = f"implicit:{entity_id}"
        else:
            profile_definition = resolved_character.profile
            profile_id = resolved_character.character_id
        if profile_definition.template_id != "human-v1":
            raise ValueError(
                f"resolved character {profile_id} uses unknown template "
                f"{profile_definition.template_id}"
            )
        template = HUMAN_V1_TEMPLATE
        profile_payload = profile_definition.model_dump(
            mode="json",
        )
        rendered_profile = CharacterDescriptionRenderer().render(
            template_id=profile_definition.template_id,
            template_version=template.schema_version,
            sections=template.sections,
            profile=profile_payload,
        )
        identity = profile_definition.identity
        if identity is None:
            raise ValueError(f"resolved character profile missing identity: {profile_id}")
        registry.add_component(
            entity_id,
            CharacterProfileComponent(
                profile_id=profile_id,
                template_id=profile_definition.template_id,
                template_version=template.schema_version,
                content_hash=rendered_profile.content_hash,
                display_name=identity.display_name,
                description=rendered_profile.markdown,
                ui_data=_profile_ui_payload(profile_payload),
            ),
        )
        registry.add_component(
            entity_id,
            CharacterSituationComponent(
                slot_id=entity_id,
                label=slot_definition.label,
                briefing=slot_definition.briefing,
            ),
        )
        namespace_id = character_information_namespace_id(entity_id)
        dossier = information_store.register(
            InformationDocument.create(
                id=character_dossier_document_id(entity_id),
                namespace_id=namespace_id,
                kind="character.dossier",
                schema_id=(
                    f"character-profile.{profile_definition.template_id}."
                    f"v{template.schema_version}"
                ),
                subject_ids=(entity_id,),
                content=profile_payload,
                source=InformationSource(
                    type="CHARACTER_LIBRARY",
                    reference_ids=(profile_id,),
                    metadata={
                        "profile_id": profile_id,
                        "template_id": profile_definition.template_id,
                        "template_version": template.schema_version,
                    },
                ),
                visibility=VisibilityPolicy(
                    level=VisibilityLevel.PRIVATE,
                    owner_ids=(entity_id,),
                ),
            )
        )
        situation_document = information_store.register(
            InformationDocument.create(
                id=f"character-situation:{entity_id}",
                namespace_id=namespace_id,
                kind="character.situation",
                schema_id="character-situation.v1",
                subject_ids=(entity_id,),
                content={
                    "slot_id": entity_id,
                    "label": slot_definition.label,
                    "briefing": slot_definition.briefing,
                    "daily_goals": (
                        list(planner_definition.daily_goals)
                        if planner_definition is not None
                        else []
                    ),
                    "current_priorities": (
                        list(planner_definition.current_priorities)
                        if planner_definition is not None
                        else []
                    ),
                },
                source=InformationSource(
                    type="SCENARIO_SLOT",
                    reference_ids=(entity_id,),
                ),
                visibility=VisibilityPolicy(
                    level=VisibilityLevel.PRIVATE,
                    owner_ids=(entity_id,),
                ),
            )
        )
        document_ids = [dossier.id, situation_document.id]
        for document_definition in information_definition.documents:
            document = information_store.register(
                InformationDocument.create(
                    id=document_definition.id,
                    namespace_id=namespace_id,
                    kind=document_definition.kind,
                    schema_id=document_definition.schema_id,
                    subject_ids=tuple(
                        document_definition.subject_ids or [entity_id]
                    ),
                    content=document_definition.content,
                    source=InformationSource(
                        type=document_definition.source.type,
                        observer_id=document_definition.source.observer_id,
                        reference_ids=tuple(
                            document_definition.source.reference_ids
                        ),
                        metadata=document_definition.source.metadata,
                    ),
                    valid_time=(
                        document_definition.valid_time.to_domain()
                        if document_definition.valid_time is not None
                        else None
                    ),
                    recorded_at=document_definition.recorded_at,
                    visibility=VisibilityPolicy(
                        level=document_definition.visibility.level,
                        owner_ids=tuple(
                            document_definition.visibility.owner_ids
                            or [entity_id]
                        ),
                        reader_ids=tuple(
                            document_definition.visibility.reader_ids
                        ),
                    ),
                )
            )
            document_ids.append(document.id)
        registry.add_component(
            entity_id,
            InformationNamespaceComponent(
                namespace_id=namespace_id,
                document_ids=tuple(document_ids),
            ),
        )

        controller_values = raw_components.pop("controller", None)
        if controller_values is not None:
            controller_definition = _validate_component(
                ControllerDefinition, controller_values, entity_id
            )
            registry.add_component(
                entity_id,
                ControllerComponent(
                    enabled=controller_definition.enabled,
                    tool_allowlist=tuple(controller_definition.tool_allowlist),
                ),
            )
            if not registry.has_component(entity_id, PlanComponent):
                registry.add_component(entity_id, PlanComponent())

        senses_values = raw_components.pop("senses", None)
        senses_definition = (
            _validate_component(SensesDefinition, senses_values, entity_id)
            if senses_values is not None
            else SensesDefinition(
                vision_range=scenario.perception.vision_range,
                recognition_range=scenario.perception.recognition_range,
            )
        )
        if registry.has_component(entity_id, PositionComponent):
            registry.add_component(
                entity_id,
                SensesComponent(
                    vision_range=senses_definition.vision_range,
                    recognition_range=senses_definition.recognition_range,
                    hearing_multiplier=senses_definition.hearing_multiplier,
                ),
            )
            registry.add_component(entity_id, PerceptionComponent())

        if "memory" in raw_components:
            memory_definition = _validate_component(
                MemoryComponentDefinition,
                raw_components.pop("memory"),
                entity_id,
            )
            registry.add_component(
                entity_id,
                MemoryComponent(top_k=memory_definition.top_k),
            )
            for episode in memory_definition.initial_episodes:
                memory_store.record(
                    agent_id=entity_id,
                    text=episode.text,
                    simulation_time=episode.simulation_time,
                    importance=episode.importance,
                    metadata=observation_metadata("scenario"),
                )

        if "conversation" in raw_components:
            conversation_definition = _validate_component(
                ConversationComponentDefinition,
                raw_components.pop("conversation"),
                entity_id,
            )
            registry.add_component(
                entity_id,
                ConversationComponent(turns=list(conversation_definition.turns)),
            )
        elif not registry.has_component(entity_id, ConversationComponent):
            registry.add_component(entity_id, ConversationComponent())

        if (
            "homeostasis" in entity_definition.components
            and (
                "position" in entity_definition.components
                or "spatial_location" in entity_definition.components
            )
            and not registry.has_component(entity_id, MovementComponent)
        ):
            registry.add_component(entity_id, MovementComponent())

        if raw_components:
            registry.add_component(
                entity_id,
                ScenarioComponents(values=raw_components),
            )
        if (
            city_world is None
            and registry.has_component(entity_id, PositionComponent)
            and not registry.has_component(
                entity_id, SpatialLocationComponent
            )
        ):
            registry.add_component(
                entity_id,
                SpatialLocationComponent(
                    WorldLocation(
                        scale=SpatialScale.BUILDING,
                        place_id="implicit-building",
                        local_coordinate=registry.get_component(
                            entity_id, PositionComponent
                        ).coordinate,
                    )
                ),
            )
        if (
            registry.has_component(entity_id, SpatialLocationComponent)
            and not registry.has_component(entity_id, NavigationComponent)
        ):
            navigation = NavigationComponent()
            if registry.has_component(entity_id, PlanComponent):
                plan = registry.get_component(entity_id, PlanComponent)
                first_action = plan.current or (
                    plan.queue[0] if plan.queue else None
                )
                if (
                    first_action is not None
                    and first_action.action is ActionType.NAVIGATE
                    and first_action.target is not None
                ):
                    navigation.request(
                        first_action.target,
                        preferred_mode=first_action.mode,
                    )
            registry.add_component(entity_id, navigation)
    if world is not None:
        _synthesize_navigation_knowledge(registry, information_store)
    return SimulationRunner(
        RunConfiguration(
            seed=scenario.seed,
            dt=scenario.dt,
            speed=speed if speed is not None else scenario.speed,
            run_id=run_id if run_id is not None else scenario.run_id,
            cognition_execution_mode=scenario.cognition.execution_mode,
        ),
        registry=registry,
        systems=systems,
    )


class PositionDefinition(CoordinateDefinition):
    pass


class MovementDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: CoordinateDefinition | None = None


class HomeostasisComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satiety: float = Field(default=100.0, ge=0, le=100)
    energy: float = Field(default=100.0, ge=0, le=100)
    stress: float = Field(default=0.0, ge=0, le=100)


class SpatialLocationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: SpatialScale
    place_id: str = Field(min_length=1)
    local_coordinate: CoordinateDefinition | None = None
    network_node_id: str | None = None
    edge_id: str | None = None
    edge_progress: float | None = Field(default=None, ge=0, le=1)

    def to_domain(self) -> WorldLocation:
        return WorldLocation(
            scale=self.scale,
            place_id=self.place_id,
            local_coordinate=(
                self.local_coordinate.to_domain()
                if self.local_coordinate is not None
                else None
            ),
            network_node_id=self.network_node_id,
            edge_id=self.edge_id,
            edge_progress=self.edge_progress,
        )


class ExtensibleCharacterProfileModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class CharacterIdentityDefinition(ExtensibleCharacterProfileModel):

    display_name: str = Field(min_length=1)
    age: int | None = Field(default=None, ge=0, le=150)
    gender: str = ""
    pronouns: str = ""
    occupation: str = ""


class CharacterAppearanceDefinition(ExtensibleCharacterProfileModel):

    summary: str = ""
    height: str = ""
    build: str = ""
    hair: str = ""
    eyes: str = ""
    clothing: str = ""
    distinguishing_features: list[str] = Field(default_factory=list)


class CharacterPersonalityDefinition(ExtensibleCharacterProfileModel):

    summary: str = ""
    traits: list[str] = Field(default_factory=list)
    temperament: str = ""
    social_style: str = ""
    speech_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    flaws: list[str] = Field(default_factory=list)


class CharacterBackgroundDefinition(ExtensibleCharacterProfileModel):

    birthplace: str = ""
    residence: str = ""
    education: str = ""
    history: str = ""


class CharacterMotivationsDefinition(ExtensibleCharacterProfileModel):

    values: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def rejects_situational_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = {"goals", "current_priorities"} & set(value)
            if forbidden:
                raise ValueError(
                    "character motivations cannot contain scenario-owned fields: "
                    f"{sorted(forbidden)}"
                )
        return value


class CharacterCapabilitiesDefinition(ExtensibleCharacterProfileModel):

    skills: list[str] = Field(default_factory=list)
    knowledge_areas: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CharacterPreferencesDefinition(ExtensibleCharacterProfileModel):

    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)


class CharacterRelationshipDefinition(ExtensibleCharacterProfileModel):

    target_id: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    sentiment: str = ""
    notes: str = ""


class CharacterCustomFieldDefinition(ExtensibleCharacterProfileModel):

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: JsonValue
    prompt_visible: bool = True
    ui_visible: bool = True


class CharacterCustomSectionDefinition(ExtensibleCharacterProfileModel):

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    prompt_visible: bool = True
    ui_visible: bool = True
    fields: list[CharacterCustomFieldDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def field_keys_are_unique(self) -> "CharacterCustomSectionDefinition":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"custom section {self.id} field keys must be unique")
        return self


class CharacterProfileDefinition(ExtensibleCharacterProfileModel):

    template_id: str = "human-v1"
    identity: CharacterIdentityDefinition
    appearance: CharacterAppearanceDefinition = Field(
        default_factory=CharacterAppearanceDefinition
    )
    personality: CharacterPersonalityDefinition = Field(
        default_factory=CharacterPersonalityDefinition
    )
    background: CharacterBackgroundDefinition = Field(
        default_factory=CharacterBackgroundDefinition
    )
    motivations: CharacterMotivationsDefinition = Field(
        default_factory=CharacterMotivationsDefinition
    )
    capabilities: CharacterCapabilitiesDefinition = Field(
        default_factory=CharacterCapabilitiesDefinition
    )
    preferences: CharacterPreferencesDefinition = Field(
        default_factory=CharacterPreferencesDefinition
    )
    relationships: list[CharacterRelationshipDefinition] = Field(
        default_factory=list
    )
    custom_sections: list[CharacterCustomSectionDefinition] = Field(
        default_factory=list
    )

    @model_validator(mode="before")
    @classmethod
    def rejects_legacy_profile_shape(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = {
                "profile_ref",
                "display_name",
                "role",
                "traits",
                "values",
                "goals",
                "current_priorities",
            } & set(value)
            if forbidden:
                raise ValueError(
                    "character profile contains removed fields: "
                    f"{sorted(forbidden)}"
                )
        return value
    @model_validator(mode="after")
    def custom_sections_are_unique(self) -> "CharacterProfileDefinition":
        section_ids = [section.id for section in self.custom_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("custom section IDs must be unique")
        return self


class ControllerDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "navigate_to",
            "go_to",
            "perform",
            "say",
            "wait",
            "skip",
            "travel_to",
            "check_environment",
        ]
    )

    @model_validator(mode="after")
    def tools_are_supported(self) -> "ControllerDefinition":
        unknown = set(self.tool_allowlist) - {
            "navigate_to",
            "go_to",
            "perform",
            "say",
            "wait",
            "skip",
            "travel_to",
            "check_environment",
        }
        if unknown:
            raise ValueError(f"unknown controller tools: {sorted(unknown)}")
        return self


class SensesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision_range: int = Field(default=8, ge=0)
    recognition_range: int = Field(default=5, ge=0)
    hearing_multiplier: float = Field(default=1.0, gt=0)


class ActivityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityType = ActivityType.IDLE


class PlanActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionType
    target: str | None = None
    duration: float | None = Field(default=None, gt=0)
    mode: TravelMode | None = None

    @model_validator(mode="after")
    def travel_fields_are_consistent(self) -> "PlanActionDefinition":
        if self.action is ActionType.TRAVEL_TO:
            if self.target is None or self.mode is None:
                raise ValueError("TRAVEL_TO requires target and mode")
        elif self.action is ActionType.NAVIGATE:
            if self.target is None:
                raise ValueError("NAVIGATE requires target")
        elif self.mode is not None:
            raise ValueError("mode is only valid for TRAVEL_TO or NAVIGATE")
        return self

    def to_domain(self) -> PlanAction:
        return PlanAction(
            action=self.action,
            target=self.target,
            duration=self.duration,
            mode=self.mode,
        )


class PlanComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: list[PlanActionDefinition] = Field(default_factory=list)
    current: PlanActionDefinition | None = None


class PlannerComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_goals: list[str] = Field(default_factory=list)
    current_priorities: list[str] = Field(default_factory=list)
    needs_plan: bool = True


class InitialInformationSourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="SCENARIO", min_length=1)
    observer_id: str | None = Field(default=None, min_length=1)
    reference_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class InitialInformationVisibilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: VisibilityLevel = VisibilityLevel.PRIVATE
    owner_ids: list[str] = Field(default_factory=list)
    reader_ids: list[str] = Field(default_factory=list)


class InitialInformationTimeRangeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float | None = None
    end: float | None = None

    @model_validator(mode="after")
    def has_bound(self) -> "InitialInformationTimeRangeDefinition":
        if self.start is None and self.end is None:
            raise ValueError("information valid_time requires start or end")
        return self

    def to_domain(self) -> TimeRange:
        return TimeRange(start=self.start, end=self.end)


class InitialInformationDocumentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    schema_id: str = Field(default="generic.v1", min_length=1)
    subject_ids: list[str] = Field(default_factory=list)
    content: JsonValue
    source: InitialInformationSourceDefinition = Field(
        default_factory=InitialInformationSourceDefinition
    )
    valid_time: InitialInformationTimeRangeDefinition | None = None
    recorded_at: float | None = None
    visibility: InitialInformationVisibilityDefinition = Field(
        default_factory=InitialInformationVisibilityDefinition
    )


class InformationComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[InitialInformationDocumentDefinition] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def document_ids_are_unique(self) -> "InformationComponentDefinition":
        document_ids = [document.id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("initial information document IDs must be unique")
        return self


class InitialMemoryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    simulation_time: float = Field(default=0.0, ge=0)
    importance: float = Field(default=0.5, ge=0, le=1)


class MemoryComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=5, gt=0)
    initial_episodes: list[InitialMemoryDefinition] = Field(default_factory=list)


class ConversationComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[str] = Field(default_factory=list)


def _validate_component[DefinitionT: BaseModel](
    component_type: type[DefinitionT],
    content: dict[str, Any],
    entity_id: str,
) -> DefinitionT:
    try:
        return component_type.model_validate(content)
    except ValidationError as error:
        raise ValueError(
            f"invalid {component_type.__name__} for entity {entity_id}: {error}"
        ) from error


def _profile_ui_payload(
    profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    visible = dict(profile)
    raw_sections = visible.get("custom_sections")
    if not isinstance(raw_sections, list):
        return visible
    sections: list[JsonValue] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict) or raw_section.get("ui_visible") is False:
            continue
        section = dict(raw_section)
        raw_fields = section.get("fields")
        if isinstance(raw_fields, list):
            section["fields"] = [
                field
                for field in raw_fields
                if isinstance(field, dict) and field.get("ui_visible") is not False
            ]
        sections.append(section)
    visible["custom_sections"] = sections
    return visible


def _build_world(definition: WorldDefinition) -> WorldMap:
    grid = WorldGrid(
        width=definition.width,
        height=definition.height,
        blocked=frozenset(coordinate.to_domain() for coordinate in definition.blocked),
    )
    zones = tuple(
        Zone(
            id=zone.id,
            name=zone.name,
            zone_type=zone.type,
            tiles=zone.bounds.tiles()
            if zone.bounds is not None
            else frozenset(tile.to_domain() for tile in zone.tiles or []),
        )
        for zone in definition.zones
    )
    stations = tuple(
        AffordanceStation(
            id=station.id,
            name=station.name,
            position=station.position.to_domain(),
            actions=(
                tuple(action.to_domain() for action in station.actions)
                if station.actions is not None
                else tuple(
                    default_affordance_action(action.value)
                    for action in station.supported_actions or []
                )
            ),
            available=station.available,
            capacity=station.capacity,
        )
        for station in definition.stations
    )
    return WorldMap(grid=grid, zones=zones, stations=stations)


def _build_city_world(definition: CityWorldDefinition) -> CityWorld:
    bounds = definition.city.bounds_meters
    return CityWorld(
        id=definition.city.id,
        name=definition.city.name,
        bounds=CityBounds(
            bounds.min_x,
            bounds.min_y,
            bounds.max_x,
            bounds.max_y,
        ),
        districts=tuple(
            District(item.id, item.name, item.center.to_domain())
            for item in definition.districts
        ),
        buildings=tuple(
            Building(
                id=item.id,
                name=item.name,
                district_id=item.district_id,
                city_position=item.city_position.to_domain(),
                local_map_id=item.local_map_id,
                entrances=tuple(
                    BuildingEntrance(
                        id=entrance.id,
                        local_coordinate=entrance.local_coordinate.to_domain(),
                        network_node_id=entrance.neighborhood_node_id,
                    )
                    for entrance in item.entrances
                ),
            )
            for item in definition.buildings
        ),
        outdoor_places=tuple(
            OutdoorPlace(
                id=item.id,
                name=item.name,
                district_id=item.district_id,
                city_position=item.city_position.to_domain(),
                network_node_id=item.network_node_id,
            )
            for item in definition.outdoor_places
        ),
        local_maps={
            map_id: _build_world(local_map)
            for map_id, local_map in definition.local_maps.items()
        },
        nodes=tuple(
            TransportNode(
                id=item.id,
                kind=item.kind,
                position=item.position.to_domain(),
                place_id=item.place_id,
            )
            for item in definition.transport.nodes
        ),
        edges=tuple(
            TransportEdge(
                id=item.id,
                from_node_id=item.from_node_id,
                to_node_id=item.to_node_id,
                allowed_modes=frozenset(item.allowed_modes),
                distance_meters=item.distance_meters,
                geometry=tuple(point.to_domain() for point in item.geometry),
                speed_limit_mps=item.speed_limit_mps,
                bidirectional=item.bidirectional,
            )
            for item in definition.transport.edges
        ),
        vehicles=tuple(
            Vehicle(
                id=item.id,
                vehicle_type=item.type,
                name=item.name,
                capacity=item.capacity,
                network_node_id=item.location.network_node_id,
            )
            for item in definition.transport.vehicles
        ),
        walking_speed_mps=definition.transport.walking_speed_mps,
        cycling_speed_mps=definition.transport.cycling_speed_mps,
        car_speed_mps=definition.transport.car_speed_mps,
        metro_speed_mps=definition.transport.metro_speed_mps,
    )


def _build_space_registry(
    world: WorldMap,
    city: CityWorld | None,
) -> SpaceRegistry:
    registry = SpaceRegistry()
    if city is None:
        topology = GridTopology("implicit-building", world)
        registry.register_space(
            Space(
                id="implicit-building",
                topology=topology,
                kind="building",
            )
        )
        _register_map_destinations(registry, topology, world)
        return registry

    city_topology = SparseGraphTopology(city.id, city)
    registry.register_space(
        Space(
            id=city.id,
            topology=city_topology,
            kind="city",
        )
    )
    building_topologies: dict[str, GridTopology] = {}
    for building in sorted(city.buildings, key=lambda item: item.id):
        local_map = city.local_map_for_building(building.id)
        topology = GridTopology(building.id, local_map)
        building_topologies[building.id] = topology
        registry.register_space(
            Space(
                id=building.id,
                topology=topology,
                kind="building",
                metadata={"local_map_id": building.local_map_id},
            )
        )
        registry.register_containment(city.id, building.id)
        _register_map_destinations(registry, topology, local_map)

    for building in sorted(city.buildings, key=lambda item: item.id):
        topology = building_topologies[building.id]
        for entrance in sorted(building.entrances, key=lambda item: item.id):
            building_locator = topology.locator(entrance.local_coordinate)
            city_locator = city_topology.node_locator(entrance.network_node_id)
            registry.register_transition(
                Transition(
                    id=entrance.id,
                    from_locator=building_locator,
                    to_locator=city_locator,
                    traversal_kind="building_entrance",
                    executor_id="travel",
                    cost_model_id="entrance",
                    bidirectional=True,
                    metadata={
                        "building_id": building.id,
                        "network_node_id": entrance.network_node_id,
                    },
                )
            )
            registry.register_destination(building.id, building_locator)

    for place in sorted(city.outdoor_places, key=lambda item: item.id):
        registry.register_destination(
            place.id,
            city_topology.node_locator(place.network_node_id),
        )
    return registry


def _register_map_destinations(
    registry: SpaceRegistry,
    topology: GridTopology,
    world: WorldMap,
) -> None:
    for zone in sorted(world.zones, key=lambda item: item.id):
        for coordinate in sorted(zone.tiles, key=lambda item: (item.y, item.x)):
            registry.register_destination(
                zone.id,
                topology.locator(coordinate),
            )
    for station in sorted(world.stations, key=lambda item: item.id):
        registry.register_destination(
            station.id,
            topology.locator(station.position),
        )


def _synthesize_navigation_knowledge(
    registry: Registry,
    information: InformationStore,
) -> None:
    topology = registry.get_resource(SpaceRegistry)
    service = registry.get_resource(NavigationService)
    planner = RecursiveRoutePlanner()
    for character_id in registry.query_entities(
        InformationNamespaceComponent,
        SpatialLocationComponent,
    ):
        spatial = registry.get_component(
            character_id,
            SpatialLocationComponent,
        )
        origin = spatial.locator
        if origin is None:
            continue
        known_ids = {
            destination.id
            for destination in service.known_topology.destinations(character_id)
        }
        requested: dict[str, TravelMode | None] = {}
        current_place_id = spatial.location.place_id
        if (
            current_place_id not in known_ids
            and topology.destination_locators(current_place_id)
        ):
            requested[current_place_id] = None
        if registry.has_component(character_id, PlanComponent):
            plan = registry.get_component(character_id, PlanComponent)
            actions = (
                *((plan.current,) if plan.current is not None else ()),
                *plan.queue,
            )
            for action in actions:
                if (
                    action.action
                    not in {
                        ActionType.MOVE_TO,
                        ActionType.TRAVEL_TO,
                        ActionType.NAVIGATE,
                    }
                    or action.target is None
                    or action.target in known_ids
                ):
                    continue
                requested[action.target] = action.mode
        for target_id in sorted(requested):
            document_id = (
                f"navigation-compatibility:{character_id}:{target_id}"
            )
            if information.has(document_id):
                continue
            target_locators = topology.destination_locators(target_id)
            if target_id == current_place_id:
                route_destination = origin
                transition_ids = tuple(
                    sorted(
                        {
                            transition.id.removesuffix(":reverse")
                            for transition in (
                                topology.registered_transitions_from_space(
                                    origin.space_id
                                )
                            )
                        }
                    )
                )
            elif target_locators:
                requested_mode = requested[target_id]
                try:
                    route = planner.plan(
                        topology,
                        origin,
                        target_locators,
                        TraversalContext(
                            character_id=character_id,
                            requested_mode=(
                                requested_mode.value
                                if requested_mode is not None
                                else TravelMode.WALK.value
                            ),
                        ),
                    )
                except (KeyError, ValueError):
                    continue
                route_destination = route.destination
                transition_ids = tuple(
                    sorted(
                        {
                            leg.transition_id.removesuffix(":reverse")
                            for leg in route.legs
                            if leg.transition_id is not None
                        }
                    )
                )
            else:
                continue
            document = information.register(
                InformationDocument.create(
                    id=document_id,
                    namespace_id=character_information_namespace_id(
                        character_id
                    ),
                    kind="knowledge.place",
                    schema_id="navigation-knowledge.v1",
                    subject_ids=(character_id, target_id),
                    content={
                        "destination_id": target_id,
                        "locators": [
                            {
                                "space_id": route_destination.space_id,
                                "local_reference": (
                                    route_destination.local_reference
                                ),
                            }
                        ],
                        "transition_ids": list(transition_ids),
                        "compatibility_synthesized": True,
                    },
                    source=InformationSource(
                        type="SCENARIO_NAVIGATION_COMPATIBILITY",
                        reference_ids=(target_id,),
                    ),
                    visibility=VisibilityPolicy(
                        level=VisibilityLevel.PRIVATE,
                        owner_ids=(character_id,),
                    ),
                )
            )
            namespace = registry.get_component(
                character_id,
                InformationNamespaceComponent,
            )
            registry.set_component(
                character_id,
                InformationNamespaceComponent(
                    namespace_id=namespace.namespace_id,
                    document_ids=(*namespace.document_ids, document.id),
                ),
            )


def _initial_city_local_map(
    scenario: ScenarioDefinition,
    city: CityWorld,
) -> WorldMap:
    for entity in scenario.entities:
        raw = entity.components.get("spatial_location")
        if not raw or raw.get("scale") != SpatialScale.BUILDING.value:
            continue
        place_id = raw.get("place_id")
        if isinstance(place_id, str):
            return city.local_map_for_building(place_id)
    return city.local_maps[sorted(city.local_maps)[0]]


def _validate_spatial_location(
    city: CityWorld,
    location: WorldLocation,
) -> None:
    if location.scale is SpatialScale.BUILDING:
        local_map = city.local_map_for_building(location.place_id)
        if (
            location.local_coordinate is None
            or not local_map.grid.is_walkable(location.local_coordinate)
        ):
            raise ValueError(
                f"invalid local coordinate for building {location.place_id}"
            )
    elif location.network_node_id is not None:
        city.node(location.network_node_id)
    elif location.edge_id is not None:
        city.edge(location.edge_id)
    else:
        raise ValueError("city location requires a node or edge")
