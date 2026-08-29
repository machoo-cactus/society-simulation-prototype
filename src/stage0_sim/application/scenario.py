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
from stage0_sim.application.macro_work import MacroWorkCoordinator
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryConfiguration
from stage0_sim.application.memory_recording import (
    MemoryRecordingSystem,
    observation_metadata,
)
from stage0_sim.application.perception import (
    PerceptionConfiguration,
    PerceptionSystem,
)
from stage0_sim.application.planning import MacroPlanningSystem
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.components import (
    ActionType,
    ActivityComponent,
    ActivityRates,
    ActivityType,
    CharacterProfileComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    DriveThreshold,
    DriveType,
    HomeostasisComponent,
    HomeostasisConfiguration,
    MemoryComponent,
    MovementComponent,
    PerceptionComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
    SensesComponent,
    System1Configuration,
    default_activity_rates,
    default_drive_thresholds,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.affordances import AffordanceExecutionSystem
from stage0_sim.domain.systems.homeostasis import (
    HomeostasisSystem,
    MovementActivitySystem,
)
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.systems.plans import PlanExecutionSystem, TimedPlanActionSystem
from stage0_sim.domain.systems.speech import SpeechSystem
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
    model_profile: str = "default"
    decision_timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    max_read_tool_rounds: int = Field(default=0, ge=0)
    max_state_changing_tools: int = Field(default=1, ge=1, le=1)
    max_concurrency: int = Field(default=4, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_total_output_tokens: int | None = Field(default=None, gt=0)
    tool_allowlist: list[str] = Field(
        default_factory=lambda: ["go_to", "perform", "say", "wait"]
    )

    @model_validator(mode="after")
    def supported_values(self) -> "CognitionSettingsDefinition":
        if self.controller not in {"legacy", "tool-agent"}:
            raise ValueError("cognition controller must be legacy or tool-agent")
        if self.max_read_tool_rounds != 0:
            raise ValueError("read-only tool rounds are not implemented")
        unknown = set(self.tool_allowlist) - {"go_to", "perform", "say", "wait"}
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
    perception: PerceptionSettingsDefinition = Field(
        default_factory=PerceptionSettingsDefinition
    )
    cognition: CognitionSettingsDefinition = Field(
        default_factory=CognitionSettingsDefinition
    )
    character_profile_templates: dict[
        str, CharacterProfileTemplateDefinition
    ] = Field(
        default_factory=lambda: {
            "human-v1": CharacterProfileTemplateDefinition()
        }
    )
    character_profiles: dict[str, "CharacterProfileDefinition"] = Field(
        default_factory=dict
    )
    entities: list[EntityDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def entity_ids_are_unique(self) -> "ScenarioDefinition":
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity IDs must be unique")
        for profile_id, profile in self.character_profiles.items():
            if profile.profile_ref is not None:
                raise ValueError(
                    f"catalog character profile {profile_id} cannot use profile_ref"
                )
            if profile.template_id not in self.character_profile_templates:
                raise ValueError(
                    f"character profile {profile_id} uses unknown template "
                    f"{profile.template_id}"
                )
        for entity in self.entities:
            raw_profile = entity.components.get("character_profile")
            if not raw_profile:
                continue
            reference = raw_profile.get("profile_ref")
            if isinstance(reference, str) and reference not in self.character_profiles:
                raise ValueError(
                    f"entity {entity.id} references unknown character profile "
                    f"{reference}"
                )
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
    model_client: ModelClient | None = None,
    model_max_output_tokens: int | None = None,
    model_max_concurrency: int | None = None,
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
    registry.set_resource(scenario.perception.to_domain())
    systems.add(MovementActivitySystem())
    systems.add(HomeostasisSystem())
    systems.add(TimedPlanActionSystem())
    systems.add(System1ArbitrationSystem())
    systems.add(SpeechSystem())
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
            )
        )
        systems.add(CognitionScheduler())

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

        profile_values = raw_components.pop("character_profile", None)
        metadata_values = raw_components.get("metadata", {})
        profile_definition, profile_id = _resolve_character_profile(
            scenario,
            entity_id,
            profile_values,
            str(metadata_values.get("display_name", entity_id)),
        )
        template = scenario.character_profile_templates[
            profile_definition.template_id
        ]
        profile_payload = profile_definition.model_dump(
            mode="json",
            exclude={
                "profile_ref",
                "display_name",
                "role",
                "traits",
                "values",
                "goals",
            },
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
                goals=tuple(profile_definition.motivations.goals),
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


class CharacterIdentityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    age: int | None = Field(default=None, ge=0, le=150)
    gender: str = ""
    pronouns: str = ""
    occupation: str = ""


class CharacterAppearanceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    height: str = ""
    build: str = ""
    hair: str = ""
    eyes: str = ""
    clothing: str = ""
    distinguishing_features: list[str] = Field(default_factory=list)


class CharacterPersonalityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    traits: list[str] = Field(default_factory=list)
    temperament: str = ""
    social_style: str = ""
    speech_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    flaws: list[str] = Field(default_factory=list)


class CharacterBackgroundDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    birthplace: str = ""
    residence: str = ""
    education: str = ""
    history: str = ""


class CharacterMotivationsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    current_priorities: list[str] = Field(default_factory=list)


class CharacterCapabilitiesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    knowledge_areas: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CharacterPreferencesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)


class CharacterRelationshipDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    sentiment: str = ""
    notes: str = ""


class CharacterCustomFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: JsonValue
    prompt_visible: bool = True
    ui_visible: bool = True


class CharacterCustomSectionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class CharacterProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_ref: str | None = Field(default=None, min_length=1)
    template_id: str = "human-v1"
    identity: CharacterIdentityDefinition | None = None
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
    display_name: str | None = None
    role: str = ""
    traits: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_relationships(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        relationships = value.get("relationships")
        if isinstance(relationships, dict):
            normalized = dict(value)
            normalized["relationships"] = [
                {
                    "target_id": target_id,
                    "relationship": relationship,
                }
                for target_id, relationship in relationships.items()
            ]
            return normalized
        return value

    @model_validator(mode="after")
    def normalize_legacy_shape(self) -> "CharacterProfileDefinition":
        if self.identity is None:
            if self.display_name is None:
                if self.profile_ref is not None:
                    return self
                raise ValueError("character profile requires identity.display_name")
            self.identity = CharacterIdentityDefinition(
                display_name=self.display_name,
                occupation=self.role,
            )
        if self.traits and not self.personality.traits:
            self.personality.traits = list(self.traits)
        if self.values and not self.motivations.values:
            self.motivations.values = list(self.values)
        if self.goals and not self.motivations.goals:
            self.motivations.goals = list(self.goals)
        section_ids = [section.id for section in self.custom_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("custom section IDs must be unique")
        return self


class ControllerDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tool_allowlist: list[str] = Field(
        default_factory=lambda: ["go_to", "perform", "say", "wait"]
    )

    @model_validator(mode="after")
    def tools_are_supported(self) -> "ControllerDefinition":
        unknown = set(self.tool_allowlist) - {"go_to", "perform", "say", "wait"}
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


def _resolve_character_profile(
    scenario: ScenarioDefinition,
    entity_id: str,
    raw_profile: dict[str, Any] | None,
    fallback_display_name: str,
) -> tuple[CharacterProfileDefinition, str]:
    if raw_profile is None:
        return (
            CharacterProfileDefinition(
                identity=CharacterIdentityDefinition(
                    display_name=fallback_display_name
                )
            ),
            f"inline:{entity_id}",
        )
    reference = raw_profile.get("profile_ref")
    if isinstance(reference, str):
        base = scenario.character_profiles.get(reference)
        if base is None:
            raise ValueError(
                f"entity {entity_id} references unknown character profile {reference}"
            )
        base_payload = base.model_dump(
            mode="python",
            exclude={
                "profile_ref",
                "display_name",
                "role",
                "traits",
                "values",
                "goals",
            },
        )
        overrides = {
            key: value
            for key, value in raw_profile.items()
            if key != "profile_ref"
        }
        resolved = CharacterProfileDefinition.model_validate(
            _deep_merge(base_payload, overrides)
        )
        return resolved, reference
    resolved = _validate_component(
        CharacterProfileDefinition, raw_profile, entity_id
    )
    return resolved, f"inline:{entity_id}"


def _deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


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
