import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stage0_sim.adapters.llm import (
    FakeDialogueGenerator,
    FakeEmbeddingProvider,
    FakePlanner,
)
from stage0_sim.application.cognition import (
    DialogueGenerator,
    EmbeddingProvider,
    Planner,
)
from stage0_sim.application.dialogue import MacroDialogueSystem
from stage0_sim.application.macro_work import MacroWorkCoordinator
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryConfiguration
from stage0_sim.application.memory_recording import (
    MemoryRecordingSystem,
    observation_metadata,
)
from stage0_sim.application.planning import MacroPlanningSystem
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.components import (
    ActionType,
    ActivityComponent,
    ActivityRates,
    ActivityType,
    ConversationComponent,
    DriveComponent,
    DriveThreshold,
    DriveType,
    HomeostasisComponent,
    HomeostasisConfiguration,
    MemoryComponent,
    MovementComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    System1Configuration,
    default_activity_rates,
    default_drive_thresholds,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.affordances import AffordanceExecutionSystem
from stage0_sim.domain.systems.homeostasis import (
    HomeostasisSystem,
    MovementActivitySystem,
)
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.systems.plans import PlanExecutionSystem, TimedPlanActionSystem
from stage0_sim.domain.systems.system1 import System1ArbitrationSystem
from stage0_sim.domain.world import (
    AffordanceAction,
    AffordanceStation,
    Coordinate,
    HomeostasisEffect,
    WorldGrid,
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


class EntityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    components: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    seed: int = 0
    dt: float = Field(default=1.0, gt=0)
    speed: float = Field(default=1.0, gt=0)
    run_id: str | None = Field(default=None, min_length=1)
    world: WorldDefinition | None = None
    homeostasis: HomeostasisSettingsDefinition = Field(
        default_factory=HomeostasisSettingsDefinition
    )
    system1: System1SettingsDefinition = Field(
        default_factory=System1SettingsDefinition
    )
    memory: MemorySettingsDefinition = Field(default_factory=MemorySettingsDefinition)
    entities: list[EntityDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def entity_ids_are_unique(self) -> "ScenarioDefinition":
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity IDs must be unique")
        return self


@dataclass(frozen=True, slots=True)
class ScenarioComponents:
    values: Mapping[str, Mapping[str, Any]]


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
    speed: float | None = None,
    run_id: str | None = None,
    planner: Planner | None = None,
    dialogue_generator: DialogueGenerator | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> SimulationRunner:
    registry = Registry()
    systems = SystemExecutor()
    memory_store = EpisodicMemoryStore(
        embedding_provider or FakeEmbeddingProvider(),
        scenario.memory.to_domain(),
    )
    macro_work = MacroWorkCoordinator(
        planner=planner or FakePlanner(),
        dialogue_generator=dialogue_generator or FakeDialogueGenerator(),
        memory_store=memory_store,
    )
    registry.set_resource(memory_store)
    registry.set_resource(macro_work)
    registry.set_resource(scenario.homeostasis.to_domain())
    registry.set_resource(scenario.system1.to_domain())
    systems.add(MovementActivitySystem())
    systems.add(HomeostasisSystem())
    systems.add(TimedPlanActionSystem())
    systems.add(System1ArbitrationSystem())
    systems.add(MemoryRecordingSystem())
    systems.add(MacroDialogueSystem())
    systems.add(MacroPlanningSystem())
    world = _build_world(scenario.world) if scenario.world is not None else None
    if world is not None:
        registry.set_resource(world)
        systems.add(PathfindingSystem())
        systems.add(PlanExecutionSystem())
        systems.add(AffordanceExecutionSystem())
        systems.add(MovementSystem())

    occupied: set[Coordinate] = set()
    for entity_definition in scenario.entities:
        entity_id = registry.create_entity(entity_definition.id)
        raw_components = dict(entity_definition.components)
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
            if coordinate in occupied:
                raise ValueError(f"multiple entities occupy initial tile {coordinate}")
            occupied.add(coordinate)
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
                    needs_plan=planner_definition.needs_plan,
                ),
            )
            if not registry.has_component(entity_id, PlanComponent):
                registry.add_component(entity_id, PlanComponent())

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
            and "position" in entity_definition.components
            and not registry.has_component(entity_id, MovementComponent)
        ):
            registry.add_component(entity_id, MovementComponent())

        if raw_components:
            registry.add_component(
                entity_id,
                ScenarioComponents(values=raw_components),
            )
    return SimulationRunner(
        RunConfiguration(
            seed=scenario.seed,
            dt=scenario.dt,
            speed=speed if speed is not None else scenario.speed,
            run_id=run_id if run_id is not None else scenario.run_id,
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


class ActivityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityType = ActivityType.IDLE


class PlanActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionType
    target: str | None = None
    duration: float | None = Field(default=None, gt=0)

    def to_domain(self) -> PlanAction:
        return PlanAction(
            action=self.action,
            target=self.target,
            duration=self.duration,
        )


class PlanComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: list[PlanActionDefinition] = Field(default_factory=list)
    current: PlanActionDefinition | None = None


class PlannerComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_goals: list[str] = Field(default_factory=list)
    needs_plan: bool = True


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
