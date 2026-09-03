import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from stage0_sim.application.agents import (
    AgentWorkCoordinator,
    CognitionScheduler,
    RoutedCharacterController,
    ToolCallingCharacterController,
)
from stage0_sim.application.agents.contracts import ModelClient
from stage0_sim.application.agents.profile_renderer import (
    CharacterDescriptionRenderer,
)
from stage0_sim.application.agents.tools import ToolRegistry
from stage0_sim.application.character_profiles import (
    CharacterAppearanceDefinition,
    CharacterBackgroundDefinition,
    CharacterBodyMeasurementsDefinition,
    CharacterCapabilitiesDefinition,
    CharacterCommunicationDefinition,
    CharacterCustomFieldDefinition,
    CharacterCustomSectionDefinition,
    CharacterDecisionCopingDefinition,
    CharacterDispositionsDefinition,
    CharacterFamilyDefinition,
    CharacterFamilyMemberDefinition,
    CharacterFinancialSituationDefinition,
    CharacterHealthAllergyDefinition,
    CharacterHealthConditionDefinition,
    CharacterHealthDefinition,
    CharacterIdentityDefinition,
    CharacterLifeStructureDefinition,
    CharacterMedicationDefinition,
    CharacterMotivationsDefinition,
    CharacterPersonalityDefinition,
    CharacterPreferencesDefinition,
    CharacterPresentationDefinition,
    CharacterProfileDefinition,
    CharacterRelationshipDefinition,
)
from stage0_sim.application.cognition import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)
from stage0_sim.application.environment import EnvironmentInformationService
from stage0_sim.application.goals import GoalEvaluationSystem
from stage0_sim.application.information import InformationRetriever, InformationStore
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryConfiguration
from stage0_sim.application.memory_recording import (
    MemoryRecordingSystem,
    MemoryWorkCoordinator,
    observation_metadata,
)
from stage0_sim.application.migrations.constants import SCENARIO_SCHEMA_VERSION
from stage0_sim.application.navigation import (
    InformationKnownTopologyProjection,
    NavigationKnowledgeRecordingSystem,
    NavigationPlanningSystem,
    NavigationService,
)
from stage0_sim.application.npcs import NpcStaffingSystem
from stage0_sim.application.perception import (
    PerceptionConfiguration,
    PerceptionSystem,
)
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActionOrigin,
    ActionOutcome,
    ActionOutcomeCriterion,
    ActionType,
    ActivityComponent,
    ActivityRates,
    ActivityType,
    CarriedLoadComponent,
    CharacterEmbodimentComponent,
    CharacterHandStateComponent,
    CharacterPostureComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ConsumableComponent,
    ContainerComponent,
    ContentAccessMode,
    ContentEndpoint,
    ContentEndpointComponent,
    ContentEndpointKind,
    ControllerComponent,
    ConversationComponent,
    CustodyComponent,
    DriveComponent,
    DriveThreshold,
    DriveType,
    EffectiveSensesComponent,
    EffectOperation,
    EquipmentSlot,
    EquipmentStateComponent,
    EventMatchCriterion,
    GoalComparator,
    GoalCompletionPolicy,
    GoalComponent,
    GoalCriterionEffect,
    GoalLocationKind,
    GoalRuntime,
    GoalStateComponent,
    GoalStatus,
    HomeostasisComponent,
    HomeostasisConfiguration,
    InformationNamespaceComponent,
    InteractionCountCriterion,
    InteractionSpecification,
    InteractionType,
    InteractionVerb,
    KnownTextAddressesComponent,
    LineageIdGenerator,
    LocationMatchCriterion,
    MemoryComponent,
    MovementComponent,
    NavigationComponent,
    ObjectDimensions,
    ObjectEffect,
    ObjectIntrinsicComponent,
    ObjectSizeClass,
    OccupancySlot,
    OccupancySlotsComponent,
    OpenableComponent,
    OwnershipComponent,
    PerceptionComponent,
    PhysicalInteractionRegistry,
    PhysicalInteractionTarget,
    PhysicalObjectIdentityComponent,
    PhysicalPose,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PlanAction,
    PlanComponent,
    PortableComponent,
    PositionComponent,
    PossessionsComponent,
    PossessionThresholdCriterion,
    ReadableComponent,
    ScentSourceComponent,
    SenseEffectTarget,
    SensesComponent,
    SenseTransmission,
    SimulationTimeCriterion,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    StateComparisonCriterion,
    SupportComponent,
    System1Configuration,
    TravelComponent,
    UsableComponent,
    WearableComponent,
    default_activity_rates,
    default_drive_thresholds,
    validate_spatial_relation_acyclicity,
)
from stage0_sim.domain.components import (
    GoalDefinition as DomainGoalDefinition,
)
from stage0_sim.domain.content import (
    TextAccessGrant,
    TextAccessPolicy,
    TextAddress,
    TextArtifact,
    TextArtifactMode,
    TextAttribution,
    TextAttributionDisplay,
    TextBlock,
    TextBlockDraft,
    TextBlockKind,
    TextCollection,
    TextCollectionKind,
    TextContentRegistry,
    TextMediaKind,
    TextOperation,
    TextPrincipal,
    TextPrincipalKind,
)
from stage0_sim.domain.economy import (
    ItemAmount,
    ItemCatalog,
    ItemDefinition,
    TransactionOffer,
    TransactionOperation,
    TransactionPoint,
    TransactionPointRegistry,
    TransactionPointState,
    TransactionStaffing,
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
from stage0_sim.domain.lineage import active_goal_links, queue_plan_actions
from stage0_sim.domain.npcs import (
    NpcControlMode,
    NpcPoolRegistry,
    NpcRole,
    NpcRoleRegistry,
    NpcStaffingAssignment,
    NpcStaffingState,
)
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.affordances import AffordanceExecutionSystem
from stage0_sim.domain.systems.calendar import CalendarUpdateSystem
from stage0_sim.domain.systems.effects import (
    CharacterEffectResolutionSystem,
    resolve_character_effects,
)
from stage0_sim.domain.systems.engagements import (
    EngagementExecutionSystem,
    build_v1_handler_registry,
)
from stage0_sim.domain.systems.environment import (
    EnvironmentAvailabilitySystem,
    SurfaceConditionSystem,
    WeatherUpdateSystem,
)
from stage0_sim.domain.systems.homeostasis import (
    HomeostasisSystem,
    MovementActivitySystem,
)
from stage0_sim.domain.systems.interactions import InteractionExecutionSystem
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.systems.plans import PlanExecutionSystem, TimedPlanActionSystem
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.systems.speech import SpeechSystem
from stage0_sim.domain.systems.system1 import System1ArbitrationSystem
from stage0_sim.domain.systems.text_actions import TextActionExecutionSystem
from stage0_sim.domain.systems.transactions import TransactionExecutionSystem
from stage0_sim.domain.systems.travel import TravelSystem
from stage0_sim.domain.text_actions import (
    TextAttributionRequest,
    TextReadSpecification,
    TextWriteSpecification,
)
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    AffordanceAction,
    AffordanceStation,
    Building,
    BuildingEntrance,
    BuildingPortal,
    CardinalOrientation,
    CityBounds,
    CityWorld,
    CityZone,
    ContainerTopology,
    Coordinate,
    District,
    Footprint,
    GridTopology,
    HomeostasisEffect,
    LocalCoordinateSystem,
    MapPoint,
    MovementObstruction,
    OutdoorPlace,
    Room,
    Space,
    SpaceRegistry,
    SparseGraphTopology,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialMetric,
    SpatialScale,
    Transition,
    TransportEdge,
    TransportNode,
    TravelMode,
    TraversalContext,
    Vehicle,
    VehicleRegistry,
    VehicleState,
    VisionObstruction,
    WorldGrid,
    WorldLocation,
    WorldMap,
    WorldObject,
    Zone,
    default_affordance_action,
)

__all__ = [
    "CharacterAppearanceDefinition",
    "CharacterBackgroundDefinition",
    "CharacterBodyMeasurementsDefinition",
    "CharacterCapabilitiesDefinition",
    "CharacterCommunicationDefinition",
    "CharacterCustomFieldDefinition",
    "CharacterCustomSectionDefinition",
    "CharacterDecisionCopingDefinition",
    "CharacterDispositionsDefinition",
    "CharacterFamilyDefinition",
    "CharacterFamilyMemberDefinition",
    "CharacterFinancialSituationDefinition",
    "CharacterHealthAllergyDefinition",
    "CharacterHealthConditionDefinition",
    "CharacterHealthDefinition",
    "CharacterIdentityDefinition",
    "CharacterLifeStructureDefinition",
    "CharacterMedicationDefinition",
    "CharacterMotivationsDefinition",
    "CharacterPersonalityDefinition",
    "CharacterPreferencesDefinition",
    "CharacterPresentationDefinition",
    "CharacterProfileDefinition",
    "CharacterRelationshipDefinition",
    "ScenarioDefinition",
    "ScenarioLoadError",
    "create_runner",
    "load_scenario",
]
from stage0_sim.domain.world.routing import RecursiveRoutePlanner


class CoordinateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int

    def to_domain(self) -> Coordinate:
        return Coordinate(self.x, self.y)


class SpatialMetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    microcells_per_legacy_cell: Literal[9] = 9

    def to_domain(self) -> SpatialMetric:
        return SpatialMetric(self.microcells_per_legacy_cell)


class FootprintDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells: list[CoordinateDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def cells_are_unique(self) -> "FootprintDefinition":
        points = [(cell.x, cell.y) for cell in self.cells]
        if len(points) != len(set(points)):
            raise ValueError("footprint cells must be unique")
        return self

    def to_domain(self) -> Footprint:
        return Footprint(frozenset(cell.to_domain() for cell in self.cells))


class PhysicalObstructionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement: MovementObstruction = MovementObstruction.NONE
    vision: VisionObstruction = VisionObstruction.TRANSPARENT
    hearing: SenseTransmission = SenseTransmission.PASS
    smell: SenseTransmission = SenseTransmission.PASS


class ObjectDimensionsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length_cm: float = Field(gt=0)
    width_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)

    def to_domain(self) -> ObjectDimensions:
        return ObjectDimensions(
            length_cm=self.length_cm,
            width_cm=self.width_cm,
            height_cm=self.height_cm,
        )


class ObjectIntrinsicsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mass_kg: float | None = Field(default=None, gt=0)
    dimensions_cm: ObjectDimensionsDefinition | None = None
    size_class: ObjectSizeClass | None = None

    def to_domain(self) -> ObjectIntrinsicComponent:
        return ObjectIntrinsicComponent(
            mass_kg=self.mass_kg,
            dimensions=(
                self.dimensions_cm.to_domain()
                if self.dimensions_cm is not None
                else None
            ),
            size_class=self.size_class,
        )


class OccupancySlotDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    accepted_relations: list[PhysicalRelationKind] = Field(min_length=1)
    capacity: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def relations_are_unique(self) -> "OccupancySlotDefinition":
        if len(self.accepted_relations) != len(set(self.accepted_relations)):
            raise ValueError("occupancy slot relation kinds must be unique")
        OccupancySlot(
            id=self.id,
            accepted_relations=frozenset(self.accepted_relations),
            capacity=self.capacity,
        )
        return self

    def to_domain(self) -> OccupancySlot:
        return OccupancySlot(
            id=self.id,
            accepted_relations=frozenset(self.accepted_relations),
            capacity=self.capacity,
        )


class PortableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    two_handed: bool = False


class ReadableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)


class TextPrincipalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TextPrincipalKind
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def shape_is_valid(self) -> "TextPrincipalDefinition":
        TextPrincipal(self.kind, self.id)
        return self

    def to_domain(self) -> TextPrincipal:
        return TextPrincipal(self.kind, self.id)


class TextAccessGrantDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: TextOperation
    principals: list[TextPrincipalDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def principals_are_unique(self) -> "TextAccessGrantDefinition":
        values = [principal.to_domain() for principal in self.principals]
        if len(values) != len(set(values)):
            raise ValueError("text access grant principals must be unique")
        return self


class TextAccessPolicyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[TextAccessGrantDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def operations_are_unique(self) -> "TextAccessPolicyDefinition":
        operations = [grant.operation for grant in self.grants]
        if len(operations) != len(set(operations)):
            raise ValueError("text access policy operations must be unique")
        return self

    def to_domain(self) -> TextAccessPolicy:
        return TextAccessPolicy(
            tuple(
                TextAccessGrant(
                    grant.operation,
                    tuple(
                        principal.to_domain()
                        for principal in grant.principals
                    ),
                )
                for grant in self.grants
            )
        )


class ContentEndpointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: ContentEndpointKind
    resource_id: str = Field(min_length=1)
    operations: list[TextOperation] = Field(min_length=1)
    access_mode: ContentAccessMode = ContentAccessMode.EXPOSED_REACHABLE
    lists_items: bool = False
    originates_messages: bool = False
    notifies_owner: bool = False
    created_media_kind: TextMediaKind | None = None
    created_mode: TextArtifactMode | None = None
    created_access_policy: TextAccessPolicyDefinition | None = None

    @model_validator(mode="after")
    def shape_is_valid(self) -> "ContentEndpointDefinition":
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("content endpoint operations must be unique")
        self.to_domain()
        return self

    def to_domain(self) -> ContentEndpoint:
        return ContentEndpoint(
            id=self.id,
            label=self.label,
            kind=self.kind,
            resource_id=self.resource_id,
            operations=tuple(self.operations),
            access_mode=self.access_mode,
            lists_items=self.lists_items,
            originates_messages=self.originates_messages,
            notifies_owner=self.notifies_owner,
            created_media_kind=self.created_media_kind,
            created_mode=self.created_mode,
            created_access_policy=(
                self.created_access_policy.to_domain()
                if self.created_access_policy is not None
                else None
            ),
        )


class ContentEndpointsComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoints: list[ContentEndpointDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def endpoint_ids_are_unique(self) -> "ContentEndpointsComponentDefinition":
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("content endpoint IDs must be unique")
        return self

    def to_domain(self) -> ContentEndpointComponent:
        return ContentEndpointComponent(
            tuple(endpoint.to_domain() for endpoint in self.endpoints)
        )


class ConsumableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    servings: int = Field(default=1, gt=0)


class UsableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_kind: str = Field(min_length=1)


class OpenableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initially_locked: bool = False


class ObjectEffectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    target: SenseEffectTarget
    operation: EffectOperation
    value: float

    def to_domain(self) -> ObjectEffect:
        return ObjectEffect(
            id=self.id,
            target=self.target,
            operation=self.operation,
            value=self.value,
        )


class WearableCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compatible_slots: list[EquipmentSlot] = Field(min_length=1)
    effects: list[ObjectEffectDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def values_are_unique(self) -> "WearableCapabilityDefinition":
        if len(self.compatible_slots) != len(set(self.compatible_slots)):
            raise ValueError("wearable compatible slots must be unique")
        effect_ids = [effect.id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("wearable effect IDs must be unique")
        return self

    def to_domain(
        self,
        metric: SpatialMetric | None = None,
    ) -> WearableComponent:
        scale = (metric or SpatialMetric()).microcells_per_legacy_cell
        return WearableComponent(
            compatible_slots=frozenset(self.compatible_slots),
            effects=tuple(
                replace(
                    effect.to_domain(),
                    value=(
                        effect.value * scale
                        if effect.operation is EffectOperation.ADD
                        else effect.value
                    ),
                )
                for effect in self.effects
            ),
        )


class ScentSourceCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    emission_range: int = Field(gt=0)

    def to_domain(
        self,
        metric: SpatialMetric | None = None,
    ) -> ScentSourceComponent:
        return ScentSourceComponent(
            scent_id=self.scent_id,
            description=self.description,
            emission_range=(metric or SpatialMetric()).scale_legacy_range(
                self.emission_range
            ),
        )


class SupportCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def slots_are_unique(self) -> "SupportCapabilityDefinition":
        _validate_capability_slot_ids(self.slot_ids, "support")
        return self


class ContainerCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def slots_are_unique(self) -> "ContainerCapabilityDefinition":
        _validate_capability_slot_ids(self.slot_ids, "container")
        return self


def _validate_capability_slot_ids(slot_ids: list[str], label: str) -> None:
    if any(not slot_id for slot_id in slot_ids):
        raise ValueError(f"{label} slot IDs must not be empty")
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError(f"{label} slot IDs must be unique")


class PhysicalCapabilitiesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[OccupancySlotDefinition] = Field(default_factory=list)
    support: SupportCapabilityDefinition | None = None
    container: ContainerCapabilityDefinition | None = None
    portable: PortableCapabilityDefinition | None = None
    readable: ReadableCapabilityDefinition | None = None
    content_endpoints: list[ContentEndpointDefinition] = Field(
        default_factory=list
    )
    consumable: ConsumableCapabilityDefinition | None = None
    usable: UsableCapabilityDefinition | None = None
    openable: OpenableCapabilityDefinition | None = None
    wearable: WearableCapabilityDefinition | None = None
    scent_source: ScentSourceCapabilityDefinition | None = None

    @model_validator(mode="after")
    def slot_references_are_valid(self) -> "PhysicalCapabilitiesDefinition":
        slot_ids = [slot.id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("physical capability slot IDs must be unique")
        slots_by_id = {slot.id: slot for slot in self.slots}
        for label, capability, required_relation in (
            (
                "support",
                self.support,
                PhysicalRelationKind.ON_SUPPORT,
            ),
            (
                "container",
                self.container,
                PhysicalRelationKind.IN_CONTAINER,
            ),
        ):
            if capability is None:
                continue
            unknown = set(capability.slot_ids) - slots_by_id.keys()
            if unknown:
                raise ValueError(
                    f"{label} references unknown slot IDs: {sorted(unknown)}"
                )
            incompatible = [
                slot_id
                for slot_id in capability.slot_ids
                if required_relation
                not in slots_by_id[slot_id].accepted_relations
            ]
            if incompatible:
                raise ValueError(
                    f"{label} slots do not accept {required_relation.value}: "
                    f"{sorted(incompatible)}"
                )
        if self.wearable is not None and self.portable is None:
            raise ValueError("wearable objects require the portable capability")
        endpoint_ids = [endpoint.id for endpoint in self.content_endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("physical content endpoint IDs must be unique")
        return self


class PhysicalObjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footprint: FootprintDefinition
    intrinsics: ObjectIntrinsicsDefinition = Field(
        default_factory=ObjectIntrinsicsDefinition
    )
    obstruction: PhysicalObstructionDefinition = Field(
        default_factory=PhysicalObstructionDefinition
    )
    capabilities: PhysicalCapabilitiesDefinition = Field(
        default_factory=PhysicalCapabilitiesDefinition
    )
    initial_open: bool | None = None
    owner_id: str | None = Field(default=None, min_length=1)
    custodian_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def open_state_is_valid(self) -> "PhysicalObjectDefinition":
        openable = self.capabilities.openable
        if self.initial_open is not None and openable is None:
            raise ValueError("initial_open requires the openable capability")
        if self.initial_open and openable is not None and openable.initially_locked:
            raise ValueError("an initially open object cannot be initially locked")
        return self


class PhysicalParentRelationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PhysicalRelationKind = PhysicalRelationKind.ON_FLOOR
    parent_id: str | None = Field(default=None, min_length=1)
    slot_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def relation_shape_is_valid(self) -> "PhysicalParentRelationDefinition":
        parent_id = self.parent_id or "__unresolved_room__"
        SpatialParentRelationComponent(
            parent_id=parent_id,
            kind=self.kind,
            slot_id=self.slot_id,
        )
        return self


class PhysicalPlacementDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: CoordinateDefinition
    orientation: CardinalOrientation = CardinalOrientation.NORTH
    parent_relation: PhysicalParentRelationDefinition = Field(
        default_factory=PhysicalParentRelationDefinition
    )


class ItemCatalogEntryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)

    def to_domain(self) -> ItemDefinition:
        return ItemDefinition(id=self.id, name=self.name, unit=self.unit)


class ItemAmountDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)

    def to_domain(self) -> ItemAmount:
        return ItemAmount(item_id=self.item_id, quantity=self.quantity)


class NpcRoleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    briefing: str = ""
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "serve_transaction",
            "say",
            "wait",
            "skip",
        ]
    )
    vision_range: int = Field(default=6, ge=0)
    recognition_range: int = Field(default=4, ge=0)
    hearing_range: int = Field(default=10, ge=0)
    smell_range: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def tools_are_restricted(self) -> "NpcRoleDefinition":
        allowed = {"serve_transaction", "say", "wait", "skip"}
        unknown = set(self.tool_allowlist) - allowed
        if unknown:
            raise ValueError(f"unknown NPC role tools: {sorted(unknown)}")
        if len(self.tool_allowlist) != len(set(self.tool_allowlist)):
            raise ValueError("NPC role tools must be unique")
        if self.recognition_range > self.vision_range:
            raise ValueError("NPC role recognition range must not exceed vision range")
        return self

    def to_domain(self) -> NpcRole:
        return NpcRole(
            id=self.id,
            name=self.name,
            briefing=self.briefing,
            tool_allowlist=tuple(self.tool_allowlist),
            vision_range=self.vision_range,
            recognition_range=self.recognition_range,
            hearing_range=self.hearing_range,
            smell_range=self.smell_range,
        )


class TransactionOfferDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    character_gives: list[ItemAmountDefinition] = Field(default_factory=list)
    character_receives: list[ItemAmountDefinition] = Field(default_factory=list)
    duration: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def transfers_something(self) -> "TransactionOfferDefinition":
        if not self.character_gives and not self.character_receives:
            raise ValueError("transaction offer must transfer at least one item")
        for field_name, amounts in (
            ("character_gives", self.character_gives),
            ("character_receives", self.character_receives),
        ):
            item_ids = [amount.item_id for amount in amounts]
            if len(item_ids) != len(set(item_ids)):
                raise ValueError(
                    f"transaction offer has duplicate {field_name} items"
                )
        return self

    def to_domain(self) -> TransactionOffer:
        return TransactionOffer(
            id=self.id,
            name=self.name,
            character_gives=tuple(
                amount.to_domain() for amount in self.character_gives
            ),
            character_receives=tuple(
                amount.to_domain() for amount in self.character_receives
            ),
            duration=self.duration,
        )


class TransactionStaffingDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    staff_position: CoordinateDefinition
    request_timeout: float = Field(default=60.0, gt=0)

    def to_domain(self) -> TransactionStaffing:
        return TransactionStaffing(
            role_id=self.role_id,
            staff_position=self.staff_position.to_domain(),
            request_timeout=self.request_timeout,
        )


class TransactionPointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    position: CoordinateDefinition
    offers: list[TransactionOfferDefinition] = Field(min_length=1)
    holdings: dict[str, int] = Field(default_factory=dict)
    available: bool = True
    capacity: int = Field(default=1, gt=0)
    operation: TransactionOperation = TransactionOperation.AUTOMATED
    staffing: TransactionStaffingDefinition | None = None
    environment: "EnvironmentalAvailabilityDefinition" = Field(
        default_factory=lambda: EnvironmentalAvailabilityDefinition()
    )

    @model_validator(mode="after")
    def holdings_and_offers_are_valid(self) -> "TransactionPointDefinition":
        if any(not item_id for item_id in self.holdings):
            raise ValueError("transaction point holding item IDs must not be empty")
        if any(
            isinstance(quantity, bool) or quantity < 0
            for quantity in self.holdings.values()
        ):
            raise ValueError(
                "transaction point holding quantities must be non-negative integers"
            )
        offer_ids = [offer.id for offer in self.offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("transaction point offer IDs must be unique")
        if self.operation is TransactionOperation.STAFFED:
            if self.staffing is None:
                raise ValueError("staffed transaction point requires staffing")
            distance = (
                abs(self.position.x - self.staffing.staff_position.x)
                + abs(self.position.y - self.staffing.staff_position.y)
            )
            if distance != 1:
                raise ValueError(
                    "staff position must be adjacent to the transaction point"
                )
        elif self.staffing is not None:
            raise ValueError(
                "automated transaction point must not define staffing"
            )
        return self

    def to_domain(self) -> TransactionPoint:
        return TransactionPoint(
            id=self.id,
            name=self.name,
            position=self.position.to_domain(),
            offers=tuple(offer.to_domain() for offer in self.offers),
            available=self.available,
            capacity=self.capacity,
            operation=self.operation,
            staffing=(
                self.staffing.to_domain()
                if self.staffing is not None
                else None
            ),
        )


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
    spatial_metric: SpatialMetricDefinition = Field(
        default_factory=SpatialMetricDefinition
    )
    blocked: list[CoordinateDefinition] = Field(default_factory=list)
    zones: list[ZoneDefinition] = Field(default_factory=list)
    stations: list[StationDefinition] = Field(default_factory=list)
    transaction_points: list[TransactionPointDefinition] = Field(
        default_factory=list
    )


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
    room_id: str = Field(min_length=1)
    local_coordinate: CoordinateDefinition
    neighborhood_node_id: str = Field(min_length=1)
    door_object_id: str | None = Field(default=None, min_length=1)


class RoomDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    building_id: str = Field(min_length=1)
    offset: CoordinateDefinition = Field(
        default_factory=lambda: CoordinateDefinition(x=0, y=0)
    )
    world: WorldDefinition


class BuildingPortalRuntimeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    building_id: str = Field(min_length=1)
    from_room_id: str = Field(min_length=1)
    from_coordinate: CoordinateDefinition
    to_room_id: str = Field(min_length=1)
    to_coordinate: CoordinateDefinition
    bidirectional: bool = True
    available: bool = True
    door_object_id: str | None = Field(default=None, min_length=1)


class WorldObjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition_id: str = Field(min_length=1)
    object_kind: Literal["physical", "affordance", "transaction"]
    building_id: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    position: CoordinateDefinition
    physical: PhysicalObjectDefinition | None = None
    placement: PhysicalPlacementDefinition | None = None

    @model_validator(mode="after")
    def physical_shape_is_valid(self) -> "WorldObjectDefinition":
        if (self.physical is None) != (self.placement is None):
            raise ValueError(
                "world objects must define physical and placement together"
            )
        if self.object_kind == "physical" and self.physical is None:
            raise ValueError("physical world objects require physical data")
        return self


class BuildingDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    district_id: str = Field(min_length=1)
    city_position: MapPointDefinition
    room_ids: list[str] = Field(min_length=1)
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
    rooms: list[RoomDefinition]
    portals: list[BuildingPortalRuntimeDefinition] = Field(default_factory=list)
    objects: list[WorldObjectDefinition] = Field(default_factory=list)
    outdoor_places: list[OutdoorPlaceDefinition] = Field(default_factory=list)
    transport: TransportDefinition

    @model_validator(mode="after")
    def references_are_valid(self) -> "CityWorldDefinition":
        district_ids = {item.id for item in self.districts}
        node_ids = {item.id for item in self.transport.nodes}
        building_ids = {item.id for item in self.buildings}
        rooms_by_id = {item.id: item for item in self.rooms}
        room_ids = set(rooms_by_id)
        object_ids = {item.id for item in self.objects}
        interior_destination_ids = [
            *(
                zone.id
                for room in self.rooms
                for zone in room.world.zones
            ),
            *object_ids,
        ]
        all_ids = [
            self.city.id,
            *(item.id for item in self.districts),
            *(item.id for item in self.buildings),
            *(item.id for item in self.rooms),
            *(item.id for item in self.portals),
            *interior_destination_ids,
            *(item.id for item in self.outdoor_places),
            *(item.id for item in self.transport.nodes),
            *(item.id for item in self.transport.edges),
            *(item.id for item in self.transport.vehicles),
            *(
                entrance.id
                for building in self.buildings
                for entrance in building.entrances
            ),
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("city world IDs must be globally unique")
        for building in self.buildings:
            if building.district_id not in district_ids:
                raise ValueError(
                    f"building {building.id} references unknown district"
                )
            if len(building.room_ids) != len(set(building.room_ids)):
                raise ValueError(
                f"building {building.id} room IDs must be unique"
                )
            if any(
                room_id not in room_ids
                or rooms_by_id[room_id].building_id != building.id
                for room_id in building.room_ids
            ):
                raise ValueError(
                f"building {building.id} references an invalid room"
                )
            expected_room_ids = {
                room.id
                for room in self.rooms
                if room.building_id == building.id
            }
            if set(building.room_ids) != expected_room_ids:
                raise ValueError(
                f"building {building.id} room IDs are incomplete"
                )
            for entrance in building.entrances:
                if (
                    entrance.room_id not in rooms_by_id
                    or rooms_by_id[entrance.room_id].building_id != building.id
                ):
                    raise ValueError(
                        f"entrance {entrance.id} references invalid room"
                    )
                coordinate = entrance.local_coordinate.to_domain()
                room_world = rooms_by_id[entrance.room_id].world
                if not _definition_grid_is_walkable(room_world, coordinate):
                    raise ValueError(
                        f"entrance {entrance.id} is not on a walkable room tile"
                    )
                if entrance.neighborhood_node_id not in node_ids:
                    raise ValueError(
                        f"entrance {entrance.id} references unknown node"
                    )
        for room in self.rooms:
            if room.building_id not in building_ids:
                raise ValueError(f"room {room.id} references unknown building")
        portal_endpoints: set[tuple[str, int, int, str, int, int]] = set()
        for portal in self.portals:
            if portal.building_id not in building_ids:
                raise ValueError(
                f"portal {portal.id} references unknown building"
                )
            if portal.from_room_id == portal.to_room_id:
                raise ValueError(
                f"portal {portal.id} must connect distinct rooms"
                )
            try:
                from_room = rooms_by_id[portal.from_room_id]
                to_room = rooms_by_id[portal.to_room_id]
            except KeyError as error:
                raise ValueError(
                f"portal {portal.id} references unknown room"
                ) from error
            if (
                from_room.building_id != portal.building_id
                or to_room.building_id != portal.building_id
            ):
                raise ValueError(
                f"portal {portal.id} rooms must belong to its building"
                )
            from_coordinate = portal.from_coordinate.to_domain()
            to_coordinate = portal.to_coordinate.to_domain()
            if not _definition_grid_is_walkable(
                from_room.world, from_coordinate
            ) or not _definition_grid_is_walkable(
                to_room.world, to_coordinate
            ):
                raise ValueError(
                f"portal {portal.id} endpoints must be walkable"
                )
            endpoint = (
                portal.from_room_id,
                from_coordinate.x,
                from_coordinate.y,
                portal.to_room_id,
                to_coordinate.x,
                to_coordinate.y,
            )
            reverse = (
                portal.to_room_id,
                to_coordinate.x,
                to_coordinate.y,
                portal.from_room_id,
                from_coordinate.x,
                from_coordinate.y,
            )
            if endpoint in portal_endpoints or reverse in portal_endpoints:
                raise ValueError(
                f"portal {portal.id} duplicates another portal endpoint"
                )
            portal_endpoints.add(endpoint)
        object_by_id = {item.id: item for item in self.objects}
        for building in self.buildings:
            for entrance in building.entrances:
                if entrance.door_object_id is None:
                    continue
                door = object_by_id.get(entrance.door_object_id)
                if door is None or door.room_id != entrance.room_id:
                    raise ValueError(
                        f"entrance {entrance.id} references invalid door object"
                    )
        for portal in self.portals:
            if portal.door_object_id is None:
                continue
            door = object_by_id.get(portal.door_object_id)
            if door is None or door.room_id not in {
                portal.from_room_id,
                portal.to_room_id,
            }:
                raise ValueError(
                    f"portal {portal.id} references invalid door object"
                )
        for room in self.rooms:
            for station in room.world.stations:
                object_definition = object_by_id.get(station.id)
                if (
                object_definition is None
                or object_definition.object_kind != "affordance"
                or object_definition.room_id != room.id
                or object_definition.position != station.position
                ):
                    raise ValueError(
                        f"station {station.id} lacks matching room object"
                    )
            for point in room.world.transaction_points:
                object_definition = object_by_id.get(point.id)
                if (
                object_definition is None
                or object_definition.object_kind != "transaction"
                or object_definition.room_id != room.id
                or object_definition.position != point.position
                ):
                    raise ValueError(
                        f"transaction point {point.id} lacks matching room object"
                    )
        for item in self.objects:
            object_room = rooms_by_id.get(item.room_id)
            if (
                object_room is None
                or object_room.building_id != item.building_id
                or item.building_id not in building_ids
            ):
                raise ValueError(
                f"object {item.id} references invalid hierarchy"
                )
            if item.physical is not None and item.placement is not None:
                metric = object_room.world.spatial_metric.to_domain()
                cells = item.physical.footprint.to_domain().translated_cells(
                    item.placement.anchor.to_domain(),
                    item.placement.orientation,
                )
                width = metric.scale_legacy_extent(object_room.world.width)
                height = metric.scale_legacy_extent(object_room.world.height)
                if any(
                    cell.x < 0
                    or cell.y < 0
                    or cell.x >= width
                    or cell.y >= height
                    for cell in cells
                ):
                    raise ValueError(
                        f"physical object {item.id} footprint is outside its room"
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


def _definition_grid_is_walkable(
    world: WorldDefinition,
    coordinate: Coordinate,
) -> bool:
    return (
        0 <= coordinate.x < world.width
        and 0 <= coordinate.y < world.height
        and coordinate
        not in {item.to_domain() for item in world.blocked}
    )


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
    voice_range: int = Field(default=10, ge=0)
    whisper_range: int = Field(default=2, ge=0)
    blocked_tiles_are_opaque: bool = True
    inbox_limit: int = Field(default=100, gt=0)
    fact_max_age_seconds: float = Field(default=300.0, gt=0)
    renderer: str = "deterministic"

    @model_validator(mode="after")
    def recognition_is_within_vision(self) -> "PerceptionSettingsDefinition":
        if self.recognition_range > self.vision_range:
            raise ValueError("recognition range must not exceed vision range")
        if not self.blocked_tiles_are_opaque:
            raise ValueError(
                "blocked room cells must block structural perception"
            )
        return self

    def to_domain(self) -> PerceptionConfiguration:
        if self.renderer != "deterministic":
            raise ValueError("only the deterministic perception renderer is supported")
        return PerceptionConfiguration(
            vision_range=self.vision_range,
            recognition_range=self.recognition_range,
            voice_range=self.voice_range,
            whisper_range=self.whisper_range,
            blocked_tiles_are_opaque=self.blocked_tiles_are_opaque,
            inbox_limit=self.inbox_limit,
            fact_max_age_seconds=self.fact_max_age_seconds,
        )


class EngagementCompilerSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_profile: str = "default"
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_tokens: int = Field(default=768, gt=0)
    max_concurrency: int = Field(default=2, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_total_output_tokens: int | None = Field(default=None, gt=0)


class CognitionSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_profile: str = "default"
    npc_control_mode: NpcControlMode = NpcControlMode.AUTO
    decision_timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    max_read_tool_rounds: int = Field(default=1, ge=0, le=4)
    max_state_changing_tools: int = Field(default=1, ge=1, le=1)
    max_concurrency: int = Field(default=4, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_total_output_tokens: int | None = Field(default=None, gt=0)
    engagement_compiler: EngagementCompilerSettingsDefinition = Field(
        default_factory=EngagementCompilerSettingsDefinition
    )
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "navigate_to",
            "perform",
            "say",
            "engage",
            "wait",
            "skip",
            "transact",
            "check_environment",
            "read_text",
            "write_text",
        ]
    )

    @model_validator(mode="after")
    def supported_values(self) -> "CognitionSettingsDefinition":
        unknown = set(self.tool_allowlist) - {
            "navigate_to",
            "perform",
            "say",
            "engage",
            "wait",
            "skip",
            "transact",
            "interact_with",
            "check_environment",
            "read_text",
            "write_text",
        }
        if unknown:
            raise ValueError(f"unknown cognition tools: {sorted(unknown)}")
        return self


class EngagementSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_groups: int = Field(default=4, ge=1, le=16)
    max_invocations_per_group: int = Field(default=4, ge=1, le=16)
    max_public_text_chars: int = Field(default=280, ge=1, le=2000)
    short_activity_seconds: float = Field(default=5.0, gt=0)
    medium_activity_seconds: float = Field(default=15.0, gt=0)
    long_activity_seconds: float = Field(default=30.0, gt=0)
    low_effort_energy_cost: float = Field(default=1.0, ge=0)
    medium_effort_energy_cost: float = Field(default=3.0, ge=0)
    high_effort_energy_cost: float = Field(default=6.0, ge=0)
    calming_stress_delta: float = Field(default=-2.0, le=0)
    activating_stress_delta: float = Field(default=2.0, ge=0)
    quiet_sound_range: int = Field(default=2, ge=0)
    normal_sound_range: int = Field(default=10, ge=0)
    loud_sound_range: int = Field(default=20, ge=0)
    alarming_listener_stress_delta: float = Field(default=2.0, ge=0)

    @model_validator(mode="after")
    def bands_are_ordered(self) -> "EngagementSettingsDefinition":
        durations = (
            self.short_activity_seconds,
            self.medium_activity_seconds,
            self.long_activity_seconds,
        )
        if durations != tuple(sorted(durations)):
            raise ValueError("engagement activity durations must be ordered")
        energy_costs = (
            self.low_effort_energy_cost,
            self.medium_effort_energy_cost,
            self.high_effort_energy_cost,
        )
        if energy_costs != tuple(sorted(energy_costs)):
            raise ValueError("engagement effort energy costs must be ordered")
        sound_ranges = (
            self.quiet_sound_range,
            self.normal_sound_range,
            self.loud_sound_range,
        )
        if sound_ranges != tuple(sorted(sound_ranges)):
            raise ValueError("engagement sound ranges must be ordered")
        return self


class CharacterProfileTemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=1)
    sections: list[str] = Field(
        default_factory=lambda: [
            "identity",
            "body_measurements",
            "appearance",
            "health",
            "personality",
            "background",
            "financial_situation",
            "motivations",
            "capabilities",
            "preferences",
            "presentation",
            "dispositions",
            "communication",
            "decision_coping",
            "life_structure",
            "family",
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
    synthesis_guidance: str = ""
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


class CharacterSituationSynthesisSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class InitialTextAttributionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authoritative_actor_id: str = Field(min_length=1)
    display: TextAttributionDisplay = TextAttributionDisplay.VERIFIED
    sender_address_id: str | None = Field(default=None, min_length=1)
    display_label: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def shape_is_valid(self) -> "InitialTextAttributionDefinition":
        self.to_domain()
        return self

    def to_domain(self) -> TextAttribution:
        return TextAttribution(
            self.authoritative_actor_id,
            self.display,
            self.sender_address_id,
            self.display_label,
        )


class InitialTextBlockDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: TextBlockKind = TextBlockKind.PARAGRAPH
    text: str = Field(max_length=65_536)

    def to_domain(self) -> TextBlock:
        return TextBlock(self.id, 1, self.text, self.kind)


class InitialTextArtifactDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    media_kind: TextMediaKind
    mode: TextArtifactMode
    blocks: list[InitialTextBlockDefinition] = Field(min_length=1)
    access_policy: TextAccessPolicyDefinition
    attribution: InitialTextAttributionDefinition

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> "InitialTextArtifactDefinition":
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("initial text block IDs must be unique")
        return self

    def to_domain(self) -> TextArtifact:
        return TextArtifact.create(
            id=self.id,
            media_kind=self.media_kind,
            mode=self.mode,
            blocks=tuple(block.to_domain() for block in self.blocks),
            access_policy=self.access_policy.to_domain(),
            operation_id=f"scenario-create:{self.id}",
            attribution=self.attribution.to_domain(),
            simulation_tick=0,
            simulation_time=0.0,
        )


class InitialTextCollectionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: TextCollectionKind
    members: list[str] = Field(default_factory=list)
    capacity: int = Field(default=1_000, gt=0, le=100_000)
    access_policy: TextAccessPolicyDefinition

    @model_validator(mode="after")
    def members_are_unique(self) -> "InitialTextCollectionDefinition":
        if len(self.members) != len(set(self.members)):
            raise ValueError("initial text collection members must be unique")
        return self

    def to_domain(self) -> TextCollection:
        return TextCollection(
            self.id,
            self.kind,
            1,
            tuple(self.members),
            self.capacity,
            self.access_policy.to_domain(),
        )


class InitialTextAddressDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    owner: TextPrincipalDefinition
    mailbox_id: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    accepted_senders: list[TextPrincipalDefinition] = Field(min_length=1)
    sent_collection_id: str | None = Field(default=None, min_length=1)

    def to_domain(self) -> TextAddress:
        return TextAddress(
            self.id,
            self.owner.to_domain(),
            self.mailbox_id,
            self.display_label,
            tuple(
                principal.to_domain()
                for principal in self.accepted_senders
            ),
            self.sent_collection_id,
        )


class InitialTextGroupDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    member_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def members_are_unique(self) -> "InitialTextGroupDefinition":
        if len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError("initial text group members must be unique")
        return self


class TextContentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[InitialTextArtifactDefinition] = Field(
        default_factory=list
    )
    collections: list[InitialTextCollectionDefinition] = Field(
        default_factory=list
    )
    addresses: list[InitialTextAddressDefinition] = Field(
        default_factory=list
    )
    groups: list[InitialTextGroupDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_valid(self) -> "TextContentDefinition":
        for values, label in (
            ([item.id for item in self.artifacts], "artifact"),
            ([item.id for item in self.collections], "collection"),
            ([item.id for item in self.addresses], "address"),
            ([item.id for item in self.groups], "group"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"text {label} IDs must be unique")
        artifact_ids = {artifact.id for artifact in self.artifacts}
        collection_ids = {
            collection.id for collection in self.collections
        }
        for collection in self.collections:
            unknown = set(collection.members) - artifact_ids
            if unknown:
                raise ValueError(
                    f"text collection {collection.id} references unknown "
                    f"artifacts: {sorted(unknown)}"
                )
        for address in self.addresses:
            references = {
                address.mailbox_id,
                *(
                    [address.sent_collection_id]
                    if address.sent_collection_id is not None
                    else []
                ),
            }
            unknown = references - collection_ids
            if unknown:
                raise ValueError(
                    f"text address {address.id} references unknown "
                    f"collections: {sorted(unknown)}"
                )
        return self

    def to_domain(self) -> TextContentRegistry:
        return TextContentRegistry(
            artifacts=tuple(
                artifact.to_domain() for artifact in self.artifacts
            ),
            collections=tuple(
                collection.to_domain() for collection in self.collections
            ),
            addresses=tuple(
                address.to_domain() for address in self.addresses
            ),
            groups={
                group.id: tuple(group.member_ids)
                for group in self.groups
            },
        )


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[8] = SCENARIO_SCHEMA_VERSION
    name: str = Field(min_length=1)
    seed: int = 0
    dt: float = Field(default=1.0, gt=0)
    speed: float = Field(default=1.0, gt=0)
    run_id: str | None = Field(default=None, min_length=1)
    items: list[ItemCatalogEntryDefinition] = Field(default_factory=list)
    npc_roles: list[NpcRoleDefinition] = Field(default_factory=list)
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
    engagement: EngagementSettingsDefinition = Field(
        default_factory=EngagementSettingsDefinition
    )
    text_content: TextContentDefinition = Field(
        default_factory=TextContentDefinition
    )
    character_situation_synthesis: CharacterSituationSynthesisSettingsDefinition = (
        Field(default_factory=CharacterSituationSynthesisSettingsDefinition)
    )
    entities: list[EntityDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def entity_ids_are_unique(self) -> "ScenarioDefinition":
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("item catalog IDs must be unique")
        known_item_ids = set(item_ids)
        npc_role_ids = [role.id for role in self.npc_roles]
        if len(npc_role_ids) != len(set(npc_role_ids)):
            raise ValueError("NPC role IDs must be unique")
        known_npc_role_ids = set(npc_role_ids)
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
            raw_possessions = entity.components.get("possessions")
            if raw_possessions is not None:
                possessions = _validate_component(
                    PossessionsComponentDefinition,
                    raw_possessions,
                    entity.id,
                )
                unknown = set(possessions.holdings) - known_item_ids
                if unknown:
                    raise ValueError(
                        f"entity {entity.id} possessions reference unknown items: "
                        f"{sorted(unknown)}"
                    )
        worlds = (
            [self.world]
            if isinstance(self.world, WorldDefinition)
            else [room.world for room in self.world.rooms]
            if isinstance(self.world, CityWorldDefinition)
            else []
        )
        for world in worlds:
            for point in world.transaction_points:
                referenced = set(point.holdings)
                referenced.update(
                    amount.item_id
                    for offer in point.offers
                    for amount in (
                        *offer.character_gives,
                        *offer.character_receives,
                    )
                )
                unknown = referenced - known_item_ids
                if unknown:
                    raise ValueError(
                        f"transaction point {point.id} references unknown items: "
                        f"{sorted(unknown)}"
                    )
                if point.staffing is not None:
                    if point.staffing.role_id not in known_npc_role_ids:
                        raise ValueError(
                            f"transaction point {point.id} references unknown "
                            f"NPC role: {point.staffing.role_id}"
                        )
                    staff_position = point.staffing.staff_position.to_domain()
                    if not (
                        0 <= staff_position.x < world.width
                        and 0 <= staff_position.y < world.height
                    ):
                        raise ValueError(
                            f"transaction point {point.id} staff position "
                            "must be inside its room grid"
                        )
                    if staff_position in {
                        coordinate.to_domain()
                        for coordinate in world.blocked
                    }:
                        raise ValueError(
                            f"transaction point {point.id} staff position "
                            "must be walkable"
                        )
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
        for point in world.transaction_points:
            rules.append(
                _availability_rule(
                    point.id,
                    "transaction_point",
                    point.available,
                    point.environment,
                )
            )
    elif isinstance(world, CityWorldDefinition):
        for room in world.rooms:
            for station in room.world.stations:
                rules.append(
                    _availability_rule(
                        station.id,
                        "station",
                        station.available,
                        station.environment,
                    )
                )
            for point in room.world.transaction_points:
                rules.append(
                    _availability_rule(
                        point.id,
                        "transaction_point",
                        point.available,
                        point.environment,
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


@dataclass(frozen=True, slots=True)
class ResolvedCharacterSituation:
    character_id: str
    profile_content_hash: str
    input_hash: str
    content_hash: str
    description: str
    data: Mapping[str, JsonValue]
    generation: Mapping[str, JsonValue]


class ScenarioLoadError(ValueError):
    pass


def load_scenario(path: Path) -> ScenarioDefinition:
    try:
        raw_scenario = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScenarioLoadError(f"could not read scenario {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ScenarioLoadError(f"scenario is not valid JSON: {error}") from error

    if (
        not isinstance(raw_scenario, dict)
        or raw_scenario.get("schema_version") != SCENARIO_SCHEMA_VERSION
    ):
        raise ScenarioLoadError(
            f"scenario schema version {SCENARIO_SCHEMA_VERSION} is required; run "
            "'stage0-sim migrate content'"
        )
    try:
        return ScenarioDefinition.model_validate(raw_scenario)
    except ValidationError as error:
        raise ScenarioLoadError(f"scenario validation failed: {error}") from error


def create_runner(
    scenario: ScenarioDefinition,
    *,
    resolved_characters: Mapping[str, ResolvedCharacterProfile] | None = None,
    resolved_situations: Mapping[str, ResolvedCharacterSituation] | None = None,
    speed: float | None = None,
    run_id: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    model_client: ModelClient | None = None,
    model_max_output_tokens: int | None = None,
    model_max_concurrency: int | None = None,
    npc_control_mode: NpcControlMode | str | None = None,
) -> SimulationRunner:
    from stage0_sim.application.data_capture import BufferedResearchRecorder

    research_recorder = BufferedResearchRecorder()
    registry = Registry()
    systems = SystemExecutor()
    information_store = InformationStore()
    text_content = scenario.text_content.to_domain()
    resolved_embedding_provider = (
        embedding_provider or DeterministicEmbeddingProvider()
    )
    memory_store = EpisodicMemoryStore(
        resolved_embedding_provider,
        scenario.memory.to_domain(),
        information_store,
        research_recorder=research_recorder,
    )
    information_retriever = InformationRetriever(
        information_store,
        resolved_embedding_provider,
        research_recorder=research_recorder,
    )
    memory_work = MemoryWorkCoordinator(
        memory_store=memory_store,
        research_recorder=research_recorder,
    )
    registry.set_resource(information_store)
    registry.set_resource(text_content)
    registry.set_resource(information_retriever)
    registry.set_resource(memory_store)
    registry.set_resource(memory_work)
    registry.set_resource(LineageIdGenerator())
    registry.set_resource(
        ItemCatalog(tuple(item.to_domain() for item in scenario.items))
    )
    metric = SpatialMetric()
    registry.set_resource(
        NpcRoleRegistry(
            {
                role.id: replace(
                    role.to_domain(),
                    vision_range=metric.scale_legacy_range(role.vision_range),
                    recognition_range=metric.scale_legacy_range(
                        role.recognition_range
                    ),
                    hearing_range=metric.scale_legacy_range(role.hearing_range),
                    smell_range=metric.scale_legacy_range(role.smell_range),
                )
                for role in scenario.npc_roles
            }
        )
    )
    registry.set_resource(scenario.homeostasis.to_domain())
    registry.set_resource(scenario.system1.to_domain())
    perception_configuration = scenario.perception.to_domain()
    registry.set_resource(
        replace(
            perception_configuration,
            vision_range=metric.scale_legacy_range(
                perception_configuration.vision_range
            ),
            recognition_range=metric.scale_legacy_range(
                perception_configuration.recognition_range
            ),
            voice_range=metric.scale_legacy_range(
                perception_configuration.voice_range
            ),
            whisper_range=metric.scale_legacy_range(
                perception_configuration.whisper_range
            ),
        )
    )
    registry.set_resource(SurfaceConditionRegistry())
    registry.set_resource(EnvironmentAvailabilityRegistry())
    registry.set_resource(SpatialIndex())
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
    systems.add(TextActionExecutionSystem())
    systems.add(System1ArbitrationSystem())
    systems.add(SpeechSystem())
    systems.add(MemoryRecordingSystem())
    systems.add(GoalEvaluationSystem())
    city_world = (
        _build_city_world(scenario.world)
        if isinstance(scenario.world, CityWorldDefinition)
        else None
    )
    world = (
        _initial_city_room_world(scenario, city_world)
        if city_world is not None
        else _build_world(scenario.world)
        if isinstance(scenario.world, WorldDefinition)
        else None
    )
    requested_npc_mode = (
        NpcControlMode(npc_control_mode)
        if npc_control_mode is not None
        else scenario.cognition.npc_control_mode
    )
    effective_npc_mode = (
        NpcControlMode.MODEL
        if requested_npc_mode is NpcControlMode.AUTO
        and model_client is not None
        else NpcControlMode.DETERMINISTIC
        if requested_npc_mode is NpcControlMode.AUTO
        else requested_npc_mode
    )
    staffing_states: dict[str, NpcStaffingState] = {}
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
        point_definitions = (
            scenario.world.transaction_points
            if isinstance(scenario.world, WorldDefinition)
            else [
                point
                for room in scenario.world.rooms
                for point in room.world.transaction_points
            ]
            if isinstance(scenario.world, CityWorldDefinition)
            else []
        )
        registry.set_resource(
            TransactionPointRegistry(
                {
                    point.id: TransactionPointState(dict(point.holdings))
                    for point in point_definitions
                }
            )
        )
        if isinstance(scenario.world, CityWorldDefinition):
            _materialize_physical_objects(registry, scenario.world)
            registry.set_resource(
                _build_physical_interaction_registry(
                    registry,
                    city_world,
                    scenario.world,
                )
            )
        if isinstance(scenario.world, WorldDefinition):
            point_locations = [
                ("implicit-building", point)
                for point in scenario.world.transaction_points
            ]
        elif isinstance(scenario.world, CityWorldDefinition):
            point_locations = [
                (room.id, point)
                for room in scenario.world.rooms
                for point in room.world.transaction_points
            ]
        else:
            point_locations = []
        for place_id, point in point_locations:
            if point.staffing is None:
                continue
            staffing_states[point.id] = NpcStaffingState(
                NpcStaffingAssignment(
                    point_id=point.id,
                    role_id=point.staffing.role_id,
                    place_id=place_id,
                    staff_position=_transaction_staff_position(
                        registry,
                        point,
                    ),
                    request_timeout=point.staffing.request_timeout,
                )
            )
        if (
            staffing_states
            and effective_npc_mode is NpcControlMode.MODEL
            and model_client is None
        ):
            raise ValueError(
                "model NPC control requires an explicit model client"
            )
        registry.set_resource(
            NpcPoolRegistry(
                staffings=staffing_states,
                requested_mode=requested_npc_mode,
                effective_mode=effective_npc_mode,
            )
        )
        space_registry = _build_space_registry(
            world,
            city_world,
            (
                registry.get_resource(PhysicalInteractionRegistry)
                if registry.has_resource(PhysicalInteractionRegistry)
                else None
            ),
            registry.get_resource(SpatialIndex),
        )
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
        systems.add(InteractionExecutionSystem())
        systems.add(CharacterEffectResolutionSystem())
        systems.add(AffordanceExecutionSystem())
        systems.add(TransactionExecutionSystem())
        systems.add(
            EngagementExecutionSystem(build_v1_handler_registry())
        )
        if staffing_states:
            systems.add(NpcStaffingSystem())
        systems.add(MovementSystem())
        systems.add(NavigationKnowledgeRecordingSystem())
        systems.add(PerceptionSystem())

    tool_registry = ToolRegistry()
    normal_tool_agent_enabled = any(
        bool(entity.components.get("controller", {}).get("enabled", False))
        for entity in scenario.entities
    )
    tool_agent_enabled = normal_tool_agent_enabled or bool(staffing_states)
    if tool_agent_enabled:
        if (
            model_client is None
            and (
                normal_tool_agent_enabled
                or effective_npc_mode is NpcControlMode.MODEL
            )
        ):
            raise ValueError(
                "tool-agent cognition requires an explicit model client; "
                "configure STAGE0_LLM_PROVIDER or pass model_client"
            )
        model_controller = (
            ToolCallingCharacterController(
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
                max_read_tool_rounds=(
                    scenario.cognition.max_read_tool_rounds
                ),
                research_recorder=research_recorder,
            )
            if model_client is not None
            else None
        )
        controller = RoutedCharacterController(
            model_controller=model_controller,
            npc_mode=effective_npc_mode,
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
                research_recorder=research_recorder,
            )
        )
        if model_client is not None:
            from stage0_sim.application.engagements.compiler import (
                EngagementCompiler,
            )
            from stage0_sim.application.engagements.coordinator import (
                EngagementWorkCoordinator,
            )

            compiler_settings = (
                scenario.cognition.engagement_compiler.model_copy(
                    update={
                        "max_output_tokens": (
                            min(
                                scenario.cognition.engagement_compiler.max_output_tokens,
                                model_max_output_tokens,
                            )
                            if model_max_output_tokens is not None
                            else scenario.cognition.engagement_compiler.max_output_tokens
                        )
                    }
                )
            )
            registry.set_resource(
                EngagementWorkCoordinator(
                    EngagementCompiler(
                        model_client,
                        compiler_settings=compiler_settings,
                        engagement_settings=scenario.engagement,
                    ),
                    max_concurrency=(
                        min(
                            compiler_settings.max_concurrency,
                            model_max_concurrency,
                        )
                        if model_max_concurrency is not None
                        else compiler_settings.max_concurrency
                    ),
                    request_timeout_seconds=(
                        compiler_settings.timeout_seconds
                    ),
                    max_requests=compiler_settings.max_requests,
                    max_input_tokens=compiler_settings.max_input_tokens,
                    max_output_tokens=(
                        compiler_settings.max_total_output_tokens
                    ),
                    research_recorder=research_recorder,
                )
            )
        systems.add(CognitionScheduler())

    occupied: set[tuple[str, Coordinate]] = set()
    goal_ids: set[str] = set()
    initial_plan_actions: dict[str, tuple[PlanAction, ...]] = {}
    for entity_definition in scenario.entities:
        entity_id = registry.create_entity(entity_definition.id)
        raw_components = dict(entity_definition.components)
        entity_world = world
        position_is_runtime = False
        spatial_values = raw_components.pop("spatial_location", None)
        if spatial_values is not None:
            if city_world is None:
                raise ValueError(
                    "spatial_location requires a city world definition"
                )
            spatial_definition = _validate_component(
                SpatialLocationDefinition, spatial_values, entity_id
            )
            spatial_location = _runtime_world_location(
                spatial_definition.to_domain(),
                metric,
            )
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
                entity_world = city_world.room_world(
                    spatial_location.place_id
                )
                raw_components.setdefault(
                    "position",
                    {
                        "x": spatial_location.local_coordinate.x,
                        "y": spatial_location.local_coordinate.y,
                    },
                )
                position_is_runtime = True
        if "position" in raw_components:
            if entity_world is None:
                raise ValueError("entity positions require a world definition")
            position_definition = _validate_component(
                PositionDefinition, raw_components.pop("position"), entity_id
            )
            coordinate = position_definition.to_domain()
            if not position_is_runtime:
                coordinate = _legacy_anchor(coordinate, metric)
            coordinate = _initial_interaction_approach(
                registry,
                entity_world,
                coordinate,
            )
            if registry.has_component(
                entity_id,
                SpatialLocationComponent,
            ):
                spatial = registry.get_component(
                    entity_id,
                    SpatialLocationComponent,
                )
                if spatial.location.local_coordinate is not None:
                    spatial.location = replace(
                        spatial.location,
                        local_coordinate=coordinate,
                    )
            if not entity_world.grid.is_walkable(coordinate):
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
            if entity_world is None:
                raise ValueError("entity movement requires a world definition")
            if not registry.has_component(entity_id, PositionComponent):
                raise ValueError(f"moving entity {entity_id} requires a position component")
            movement_definition = _validate_component(
                MovementDefinition, raw_components.pop("movement"), entity_id
            )
            destination = (
                _legacy_anchor(
                    movement_definition.destination.to_domain(),
                    metric,
                )
                if movement_definition.destination is not None
                else None
            )
            if (
                destination is not None
                and not entity_world.grid.is_walkable(destination)
            ):
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

        if "possessions" in raw_components:
            possessions_definition = _validate_component(
                PossessionsComponentDefinition,
                raw_components.pop("possessions"),
                entity_id,
            )
            registry.add_component(
                entity_id,
                PossessionsComponent(dict(possessions_definition.holdings)),
            )

        if "plan" in raw_components:
            plan_definition = _validate_component(
                PlanComponentDefinition, raw_components.pop("plan"), entity_id
            )
            initial_plan_actions[entity_id] = tuple(
                action.to_domain()
                for action in (
                    *(
                        (plan_definition.current,)
                        if plan_definition.current is not None
                        else ()
                    ),
                    *plan_definition.queue,
                )
            )
            registry.add_component(entity_id, PlanComponent())

        goals_definition: GoalsComponentDefinition | None = None
        if "goals" in raw_components:
            goals_definition = _validate_component(
                GoalsComponentDefinition,
                raw_components.pop("goals"),
                entity_id,
            )
            structured_definitions = [
                goal.to_domain() for goal in goals_definition.goals
            ]
            duplicate_goal_ids = goal_ids.intersection(
                goal.id for goal in structured_definitions
            )
            if duplicate_goal_ids:
                raise ValueError(
                    "structured goal IDs must be unique across the scenario: "
                    f"{sorted(duplicate_goal_ids)}"
                )
            goal_ids.update(goal.id for goal in structured_definitions)
            registry.add_component(
                entity_id,
                GoalComponent(
                    [
                        GoalRuntime(
                            definition=goal,
                            status=GoalStatus.PENDING,
                        )
                        for goal in structured_definitions
                    ]
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
                description=(
                    resolved_situations[entity_id].description
                    if resolved_situations is not None
                    and entity_id in resolved_situations
                    else slot_definition.briefing
                ),
                content_hash=(
                    resolved_situations[entity_id].content_hash
                    if resolved_situations is not None
                    and entity_id in resolved_situations
                    else ""
                ),
                input_hash=(
                    resolved_situations[entity_id].input_hash
                    if resolved_situations is not None
                    and entity_id in resolved_situations
                    else ""
                ),
                data=(
                    dict(resolved_situations[entity_id].data)
                    if resolved_situations is not None
                    and entity_id in resolved_situations
                    else {}
                ),
                generation=(
                    dict(resolved_situations[entity_id].generation)
                    if resolved_situations is not None
                    and entity_id in resolved_situations
                    else {}
                ),
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
                    "synthesized": (
                        dict(resolved_situations[entity_id].data)
                        if resolved_situations is not None
                        and entity_id in resolved_situations
                        else {}
                    ),
                    "generation": (
                        dict(resolved_situations[entity_id].generation)
                        if resolved_situations is not None
                        and entity_id in resolved_situations
                        else {}
                    ),
                    "content_hash": (
                        resolved_situations[entity_id].content_hash
                        if resolved_situations is not None
                        and entity_id in resolved_situations
                        else ""
                    ),
                    "input_hash": (
                        resolved_situations[entity_id].input_hash
                        if resolved_situations is not None
                        and entity_id in resolved_situations
                        else ""
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

        content_endpoint_values = raw_components.pop(
            "content_endpoints", None
        )
        if content_endpoint_values is not None:
            endpoint_definition = _validate_component(
                ContentEndpointsComponentDefinition,
                content_endpoint_values,
                entity_id,
            )
            registry.add_component(
                entity_id,
                endpoint_definition.to_domain(),
            )

        known_address_values = raw_components.pop(
            "known_text_addresses", None
        )
        if known_address_values is not None:
            known_addresses = _validate_component(
                KnownTextAddressesDefinition,
                known_address_values,
                entity_id,
            )
            unknown_addresses = set(
                known_addresses.address_ids
            ) - text_content.addresses.keys()
            if unknown_addresses:
                raise ValueError(
                    f"entity {entity_id} references unknown text addresses: "
                    f"{sorted(unknown_addresses)}"
                )
            registry.add_component(
                entity_id,
                KnownTextAddressesComponent(
                    tuple(known_addresses.address_ids)
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
        embodiment_values = raw_components.pop("embodiment", None)
        embodiment_definition = (
            _validate_component(
                CharacterEmbodimentDefinition,
                embodiment_values,
                entity_id,
            )
            if embodiment_values is not None
            else CharacterEmbodimentDefinition()
        )
        registry.add_component(
            entity_id,
            embodiment_definition.to_domain(),
        )
        registry.add_component(entity_id, EquipmentStateComponent())
        registry.add_component(entity_id, CarriedLoadComponent())
        if registry.has_component(entity_id, PositionComponent):
            base_senses = SensesComponent(
                vision_range=metric.scale_legacy_range(
                    senses_definition.vision_range
                ),
                recognition_range=metric.scale_legacy_range(
                    senses_definition.recognition_range
                ),
                hearing_range=metric.scale_legacy_range(
                    senses_definition.hearing_range
                ),
                smell_range=metric.scale_legacy_range(
                    senses_definition.smell_range
                ),
            )
            registry.add_component(
                entity_id,
                base_senses,
            )
            registry.add_component(
                entity_id,
                EffectiveSensesComponent(
                    vision_range=base_senses.vision_range,
                    recognition_range=base_senses.recognition_range,
                    hearing_range=base_senses.hearing_range,
                    smell_range=base_senses.smell_range,
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
            registry.has_component(entity_id, HomeostasisComponent)
            and registry.has_component(entity_id, SpatialLocationComponent)
            and not registry.has_component(entity_id, PlanComponent)
        ):
            registry.add_component(entity_id, PlanComponent())
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
    _materialize_character_physics(registry)
    _validate_content_endpoint_bindings(registry)
    _validate_initial_equipment(registry)
    for entity_id in registry.query_entities(SensesComponent):
        resolve_character_effects(registry, entity_id)
    _validate_initial_carried_load(registry)
    runner = SimulationRunner(
        RunConfiguration(
            seed=scenario.seed,
            dt=scenario.dt,
            speed=speed if speed is not None else scenario.speed,
            run_id=run_id if run_id is not None else scenario.run_id,
            npc_control_mode=requested_npc_mode,
            effective_npc_control_mode=effective_npc_mode,
        ),
        registry=registry,
        systems=systems,
        research_recorder=research_recorder,
    )
    for entity_id, actions in initial_plan_actions.items():
        if actions:
            queued = queue_plan_actions(
                runner.context,
                entity_id,
                registry.get_component(entity_id, PlanComponent),
                actions,
                origin=ActionOrigin.SCENARIO,
                goal_links=active_goal_links(runner.context, entity_id),
            )
            first = queued[0]
            if (
                first.action is ActionType.NAVIGATE
                and first.target is not None
                and registry.has_component(entity_id, NavigationComponent)
            ):
                registry.get_component(
                    entity_id, NavigationComponent
                ).request(
                    first.target,
                    preferred_mode=first.mode,
                    action_instance=first,
                )
    if world is not None:
        _synthesize_navigation_knowledge(registry, information_store)
    return runner


def _validate_content_endpoint_bindings(registry: Registry) -> None:
    content = registry.get_resource(TextContentRegistry)
    for entity_id, component in registry.query(ContentEndpointComponent):
        for endpoint in component.endpoints:
            if endpoint.kind is ContentEndpointKind.ARTIFACT:
                if endpoint.resource_id not in content.artifacts:
                    raise ValueError(
                        f"content endpoint {entity_id}:{endpoint.id} references "
                        f"unknown artifact {endpoint.resource_id}"
                    )
            elif endpoint.resource_id not in content.collections:
                raise ValueError(
                    f"content endpoint {entity_id}:{endpoint.id} references "
                    f"unknown collection {endpoint.resource_id}"
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

    @model_validator(mode="after")
    def shape_matches_derived_scale(self) -> "SpatialLocationDefinition":
        if self.local_coordinate is not None:
            if self.scale is not SpatialScale.BUILDING:
                raise ValueError(
                    "room coordinates require BUILDING compatibility scale"
                )
            if (
                self.edge_id is not None
                or self.edge_progress is not None
                or self.network_node_id is not None
            ):
                raise ValueError(
                    "room coordinates cannot include network position fields"
                )
            return self
        if self.scale is SpatialScale.BUILDING:
            raise ValueError(
                "BUILDING compatibility scale requires a room coordinate"
            )
        if (self.edge_id is None) != (self.edge_progress is None):
            raise ValueError("edge_id and edge_progress must be provided together")
        if self.network_node_id is not None and self.edge_id is not None:
            raise ValueError("city location cannot be both on a node and edge")
        return self

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


class ControllerDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "navigate_to",
            "perform",
            "say",
            "engage",
            "wait",
            "skip",
            "transact",
            "check_environment",
            "read_text",
            "write_text",
        ]
    )

    @model_validator(mode="after")
    def tools_are_supported(self) -> "ControllerDefinition":
        unknown = set(self.tool_allowlist) - {
            "navigate_to",
            "perform",
            "say",
            "engage",
            "wait",
            "skip",
            "transact",
            "interact_with",
            "check_environment",
            "read_text",
            "write_text",
        }
        if unknown:
            raise ValueError(f"unknown controller tools: {sorted(unknown)}")
        return self


class SensesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision_range: int = Field(default=8, ge=0)
    recognition_range: int = Field(default=5, ge=0)
    hearing_range: int = Field(default=10, ge=0)
    smell_range: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def recognition_is_within_vision(self) -> "SensesDefinition":
        if self.recognition_range > self.vision_range:
            raise ValueError("recognition range must not exceed vision range")
        return self


class EquipmentSlotCapacityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: EquipmentSlot
    capacity: int = Field(default=1, gt=0)


class CharacterEmbodimentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_single_object_mass_kg: float = Field(default=25.0, gt=0)
    max_carried_mass_kg: float = Field(default=35.0, gt=0)
    equipment_slots: list[EquipmentSlotCapacityDefinition] = Field(
        default_factory=lambda: [
            EquipmentSlotCapacityDefinition(slot=slot) for slot in EquipmentSlot
        ]
    )

    @model_validator(mode="after")
    def shape_is_valid(self) -> "CharacterEmbodimentDefinition":
        if self.max_single_object_mass_kg > self.max_carried_mass_kg:
            raise ValueError(
                "maximum single-object mass must not exceed total carried mass"
            )
        slots = [item.slot for item in self.equipment_slots]
        if len(slots) != len(set(slots)):
            raise ValueError("character equipment slots must be unique")
        return self

    def to_domain(self) -> CharacterEmbodimentComponent:
        return CharacterEmbodimentComponent(
            max_single_object_mass_kg=self.max_single_object_mass_kg,
            max_carried_mass_kg=self.max_carried_mass_kg,
            equipment_slot_capacities={
                item.slot: item.capacity for item in self.equipment_slots
            },
        )


class ActivityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityType = ActivityType.IDLE


class PossessionsComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holdings: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def quantities_are_non_negative(self) -> "PossessionsComponentDefinition":
        if any(not item_id for item_id in self.holdings):
            raise ValueError("possession item IDs must not be empty")
        if any(
            isinstance(quantity, bool) or quantity < 0
            for quantity in self.holdings.values()
        ):
            raise ValueError(
                "possession quantities must be non-negative integers"
            )
        return self


class InteractionSpecificationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verb: InteractionVerb
    target_id: str = Field(min_length=1)
    destination_id: str | None = Field(default=None, min_length=1)
    slot_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def shape_is_valid(self) -> "InteractionSpecificationDefinition":
        InteractionSpecification(
            self.verb,
            self.target_id,
            self.destination_id,
            self.slot_id,
        )
        return self

    def to_domain(self) -> InteractionSpecification:
        return InteractionSpecification(
            self.verb,
            self.target_id,
            self.destination_id,
            self.slot_id,
        )


class TextAttributionRequestDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display: TextAttributionDisplay = TextAttributionDisplay.VERIFIED
    sender_address_id: str | None = Field(default=None, min_length=1)
    display_label: str | None = Field(default=None, min_length=1)

    def to_domain(self) -> TextAttributionRequest:
        return TextAttributionRequest(
            self.display,
            self.sender_address_id,
            self.display_label,
        )


class TextBlockDraftDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=65_536)
    kind: TextBlockKind = TextBlockKind.PARAGRAPH

    def to_domain(self) -> TextBlockDraft:
        return TextBlockDraft(self.text, self.kind)


class TextReadSpecificationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    block_ids: list[str] = Field(default_factory=list)

    def to_domain(self) -> TextReadSpecification:
        return TextReadSpecification(
            self.target_id,
            self.endpoint_id,
            self.artifact_id,
            tuple(self.block_ids),
        )


class TextWriteSpecificationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: TextOperation
    target_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    attribution: TextAttributionRequestDefinition = Field(
        default_factory=TextAttributionRequestDefinition
    )
    artifact_id: str | None = Field(default=None, min_length=1)
    expected_artifact_revision: int | None = Field(default=None, gt=0)
    expected_collection_revision: int | None = Field(default=None, gt=0)
    expected_sent_collection_revision: int | None = Field(default=None, gt=0)
    block_id: str | None = Field(default=None, min_length=1)
    expected_block_revision: int | None = Field(default=None, gt=0)
    blocks: list[TextBlockDraftDefinition] = Field(default_factory=list)
    text: str | None = Field(default=None, max_length=65_536)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    recipient_address_id: str | None = Field(default=None, min_length=1)
    artifact_id_hint: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def shape_is_valid(self) -> "TextWriteSpecificationDefinition":
        self.to_domain()
        return self

    def to_domain(self) -> TextWriteSpecification:
        return TextWriteSpecification(
            operation=self.operation,
            target_id=self.target_id,
            endpoint_id=self.endpoint_id,
            attribution=self.attribution.to_domain(),
            artifact_id=self.artifact_id,
            expected_artifact_revision=self.expected_artifact_revision,
            expected_collection_revision=self.expected_collection_revision,
            expected_sent_collection_revision=(
                self.expected_sent_collection_revision
            ),
            block_id=self.block_id,
            expected_block_revision=self.expected_block_revision,
            blocks=tuple(block.to_domain() for block in self.blocks),
            text=self.text,
            start=self.start,
            end=self.end,
            recipient_address_id=self.recipient_address_id,
            artifact_id_hint=self.artifact_id_hint,
        )


class PlanActionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionType
    target: str | None = None
    duration: float | None = Field(default=None, gt=0)
    mode: TravelMode | None = None
    offer_id: str | None = Field(default=None, min_length=1)
    interaction: InteractionSpecificationDefinition | None = None
    text_read: TextReadSpecificationDefinition | None = None
    text_write: TextWriteSpecificationDefinition | None = None

    @model_validator(mode="after")
    def travel_fields_are_consistent(self) -> "PlanActionDefinition":
        if self.action is ActionType.READ_TEXT:
            if self.text_read is None or self.text_write is not None:
                raise ValueError("READ_TEXT requires only text_read")
        elif self.action is ActionType.WRITE_TEXT:
            if self.text_write is None or self.text_read is not None:
                raise ValueError("WRITE_TEXT requires only text_write")
        elif self.text_read is not None or self.text_write is not None:
            raise ValueError("text specifications require a text action")
        elif self.action is ActionType.INTERACT:
            if self.interaction is None:
                raise ValueError("INTERACT requires interaction")
            if self.target is not None and self.target != self.interaction.target_id:
                raise ValueError("INTERACT target must match interaction target")
        elif self.interaction is not None:
            raise ValueError("interaction is only valid for INTERACT")
        elif self.action is ActionType.NAVIGATE:
            if self.target is None:
                raise ValueError("NAVIGATE requires target")
        elif self.action is ActionType.TRANSACT:
            if self.target is None or self.offer_id is None:
                raise ValueError("TRANSACT requires target and offer_id")
            if self.mode is not None:
                raise ValueError("mode is only valid for NAVIGATE")
        elif self.action is ActionType.SERVE_TRANSACTION:
            if self.target is None:
                raise ValueError(
                    "SERVE_TRANSACTION requires a transaction request target"
                )
            if self.mode is not None or self.offer_id is not None:
                raise ValueError(
                    "SERVE_TRANSACTION does not accept mode or offer_id"
                )
        elif self.mode is not None:
            raise ValueError("mode is only valid for NAVIGATE")
        if self.action is not ActionType.TRANSACT and self.offer_id is not None:
            raise ValueError("offer_id is only valid for TRANSACT")
        return self

    def to_domain(self) -> PlanAction:
        return PlanAction(
            action=self.action,
            target=self.target,
            duration=self.duration,
            mode=self.mode,
            offer_id=self.offer_id,
            interaction=(
                self.interaction.to_domain()
                if self.interaction is not None
                else None
            ),
            text_read=(
                self.text_read.to_domain()
                if self.text_read is not None
                else None
            ),
            text_write=(
                self.text_write.to_domain()
                if self.text_write is not None
                else None
            ),
        )
class PlanComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: list[PlanActionDefinition] = Field(default_factory=list)
    current: PlanActionDefinition | None = None


type CriterionValue = StrictBool | StrictInt | StrictFloat | StrictStr


class EventMatchCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event_match"]
    event_type: str = Field(min_length=1)
    payload_subset: dict[str, JsonValue] = Field(default_factory=dict)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    def to_domain(self) -> EventMatchCriterion:
        return EventMatchCriterion(
            event_type=self.event_type,
            payload_subset=self.payload_subset,
            effect=self.effect,
        )


class StateComparisonCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["state_comparison"]
    component: GoalStateComponent
    field: str = Field(min_length=1)
    comparator: GoalComparator
    value: CriterionValue
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    @model_validator(mode="after")
    def field_and_comparator_are_safe(
        self,
    ) -> "StateComparisonCriterionDefinition":
        fields = {
            GoalStateComponent.HOMEOSTASIS: {
                "satiety",
                "energy",
                "stress",
            },
            GoalStateComponent.ACTIVITY: {"current"},
            GoalStateComponent.CONTROLLER: {
                "enabled",
                "state_revision",
                "last_outcome",
                "request_pending",
            },
        }
        if self.field not in fields[self.component]:
            raise ValueError(
                f"field {self.field!r} is not available on "
                f"{self.component.value}"
            )
        if self.comparator not in {GoalComparator.EQ, GoalComparator.NE} and (
            isinstance(self.value, bool)
            or not isinstance(self.value, int | float)
        ):
            raise ValueError("ordered state comparisons require a numeric value")
        return self

    def to_domain(self) -> StateComparisonCriterion:
        return StateComparisonCriterion(
            component=self.component,
            field=self.field,
            comparator=self.comparator,
            value=self.value,
            effect=self.effect,
        )


class LocationMatchCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["location_match"]
    location_id: str = Field(min_length=1)
    location_kind: GoalLocationKind = GoalLocationKind.ANY
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    def to_domain(self) -> LocationMatchCriterion:
        return LocationMatchCriterion(
            location_id=self.location_id,
            location_kind=self.location_kind,
            effect=self.effect,
        )


class PossessionThresholdCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["possession_threshold"]
    item_id: str = Field(min_length=1)
    comparator: GoalComparator = GoalComparator.GTE
    quantity: int = Field(ge=0)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    @field_validator("comparator")
    @classmethod
    def comparator_is_ordered(
        cls, value: GoalComparator
    ) -> GoalComparator:
        if value is GoalComparator.NE:
            raise ValueError("possession threshold does not support ne")
        return value

    def to_domain(self) -> PossessionThresholdCriterion:
        return PossessionThresholdCriterion(
            item_id=self.item_id,
            comparator=self.comparator,
            quantity=self.quantity,
            effect=self.effect,
        )


class ActionOutcomeCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["action_outcome"]
    action: ActionType
    outcome: ActionOutcome
    target: str | None = Field(default=None, min_length=1)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    def to_domain(self) -> ActionOutcomeCriterion:
        return ActionOutcomeCriterion(
            action=self.action,
            outcome=self.outcome,
            target=self.target,
            effect=self.effect,
        )


class InteractionCountCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interaction_count"]
    interaction_type: InteractionType
    minimum_count: int = Field(gt=0)
    target_id: str | None = Field(default=None, min_length=1)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    def to_domain(self) -> InteractionCountCriterion:
        return InteractionCountCriterion(
            interaction_type=self.interaction_type,
            minimum_count=self.minimum_count,
            target_id=self.target_id,
            effect=self.effect,
        )


class SimulationTimeCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["simulation_time"]
    comparator: GoalComparator = GoalComparator.GTE
    simulation_time: float = Field(ge=0)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS

    @field_validator("comparator")
    @classmethod
    def comparator_is_ordered(
        cls, value: GoalComparator
    ) -> GoalComparator:
        if value in {GoalComparator.EQ, GoalComparator.NE}:
            raise ValueError(
                "simulation time threshold requires an ordered comparator"
            )
        return value

    def to_domain(self) -> SimulationTimeCriterion:
        return SimulationTimeCriterion(
            comparator=self.comparator,
            simulation_time=self.simulation_time,
            effect=self.effect,
        )


type GoalCriterionDefinition = Annotated[
    EventMatchCriterionDefinition
    | StateComparisonCriterionDefinition
    | LocationMatchCriterionDefinition
    | PossessionThresholdCriterionDefinition
    | ActionOutcomeCriterionDefinition
    | InteractionCountCriterionDefinition
    | SimulationTimeCriterionDefinition,
    Field(discriminator="type"),
]


class GoalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: int = Field(default=0, ge=0, le=100)
    tags: list[str] = Field(default_factory=list)
    activation_time: float | None = Field(default=None, ge=0)
    deadline_time: float | None = Field(default=None, ge=0)
    completion_policy: GoalCompletionPolicy = GoalCompletionPolicy.ALL
    criteria: list[GoalCriterionDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def window_and_tags_are_valid(self) -> "GoalDefinition":
        if (
            self.activation_time is not None
            and self.deadline_time is not None
            and self.deadline_time < self.activation_time
        ):
            raise ValueError("deadline_time must not precede activation_time")
        if any(not tag.strip() for tag in self.tags):
            raise ValueError("goal tags must not be empty")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("goal tags must be unique")
        return self

    def to_domain(self) -> DomainGoalDefinition:
        return DomainGoalDefinition(
            id=self.id,
            description=self.description,
            priority=self.priority,
            tags=tuple(self.tags),
            activation_time=self.activation_time,
            deadline_time=self.deadline_time,
            completion_policy=self.completion_policy,
            criteria=tuple(criterion.to_domain() for criterion in self.criteria),
        )


class GoalsComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goals: list[GoalDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def goal_ids_are_unique(self) -> "GoalsComponentDefinition":
        goal_ids = [goal.id for goal in self.goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("structured goal IDs must be unique")
        return self


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


class KnownTextAddressesDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def address_ids_are_unique(self) -> "KnownTextAddressesDefinition":
        if len(self.address_ids) != len(set(self.address_ids)):
            raise ValueError("known text address IDs must be unique")
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


def _materialize_physical_objects(
    registry: Registry,
    world: CityWorldDefinition,
) -> None:
    spatial_index = registry.get_resource(SpatialIndex)
    physical_objects = [
        item
        for item in world.objects
        if item.physical is not None and item.placement is not None
    ]
    for item in sorted(physical_objects, key=lambda value: value.id):
        physical = item.physical
        placement = item.placement
        if physical is None or placement is None:
            raise AssertionError("physical object filtering lost model narrowing")
        relation = placement.parent_relation
        if relation.parent_id is None:
            raise ValueError(
                f"physical object {item.id} has an unresolved parent relation"
            )
        initial_open = (
            physical.initial_open or False
            if physical.capabilities.openable is not None
            else False
        )
        state = PhysicalStateComponent(
            pose=PhysicalPose(
                room_id=item.room_id,
                anchor=placement.anchor.to_domain(),
                orientation=placement.orientation,
            ),
            footprint=physical.footprint.to_domain(),
            movement_obstruction=(
                MovementObstruction.NONE
                if initial_open
                else physical.obstruction.movement
            ),
            vision_obstruction=(
                VisionObstruction.TRANSPARENT
                if initial_open
                else physical.obstruction.vision
            ),
            hearing_transmission=(
                SenseTransmission.PASS
                if initial_open
                else physical.obstruction.hearing
            ),
            smell_transmission=(
                SenseTransmission.PASS
                if initial_open
                else physical.obstruction.smell
            ),
        )
        if relation.kind not in {
            PhysicalRelationKind.IN_CONTAINER,
            PhysicalRelationKind.HELD_BY,
            PhysicalRelationKind.ATTACHED_TO,
        }:
            spatial_index.add(
                SpatialIndexEntry(item.id, state),
                authorized_overlaps=(
                    frozenset({relation.parent_id})
                    if relation.kind
                    in {
                        PhysicalRelationKind.ON_SUPPORT,
                        PhysicalRelationKind.OCCUPIES_SLOT,
                    }
                    and spatial_index.contains(relation.parent_id)
                    else frozenset()
                ),
            )
        registry.create_entity(item.id)
        registry.add_component(
            item.id,
            PhysicalObjectIdentityComponent(item.definition_id, item.name),
        )
        registry.add_component(item.id, physical.intrinsics.to_domain())
        registry.add_component(item.id, state)
        registry.add_component(
            item.id,
            SpatialParentRelationComponent(
                parent_id=relation.parent_id,
                kind=relation.kind,
                slot_id=relation.slot_id,
            ),
        )
        capabilities = physical.capabilities
        if capabilities.slots:
            registry.add_component(
                item.id,
                OccupancySlotsComponent(
                    tuple(slot.to_domain() for slot in capabilities.slots)
                ),
            )
        if capabilities.support is not None:
            registry.add_component(
                item.id,
                SupportComponent(tuple(capabilities.support.slot_ids)),
            )
        if capabilities.container is not None:
            registry.add_component(
                item.id,
                ContainerComponent(tuple(capabilities.container.slot_ids)),
            )
        if capabilities.portable is not None:
            registry.add_component(
                item.id,
                PortableComponent(capabilities.portable.two_handed),
            )
        if capabilities.readable is not None:
            registry.add_component(
                item.id,
                ReadableComponent(capabilities.readable.document_id),
            )
        if capabilities.content_endpoints:
            registry.add_component(
                item.id,
                ContentEndpointComponent(
                    tuple(
                        endpoint.to_domain()
                        for endpoint in capabilities.content_endpoints
                    )
                ),
            )
        if capabilities.consumable is not None:
            registry.add_component(
                item.id,
                ConsumableComponent(
                    capabilities.consumable.item_id,
                    capabilities.consumable.servings,
                ),
            )
        if capabilities.usable is not None:
            registry.add_component(
                item.id,
                UsableComponent(capabilities.usable.use_kind),
            )
        if capabilities.wearable is not None:
            registry.add_component(
                item.id,
                capabilities.wearable.to_domain(SpatialMetric()),
            )
        if capabilities.scent_source is not None:
            registry.add_component(
                item.id,
                capabilities.scent_source.to_domain(SpatialMetric()),
            )
        if capabilities.openable is not None:
            registry.add_component(
                item.id,
                OpenableComponent(
                    is_open=initial_open,
                    is_locked=capabilities.openable.initially_locked,
                    closed_movement_obstruction=(
                        physical.obstruction.movement
                    ),
                    closed_vision_obstruction=physical.obstruction.vision,
                    closed_hearing_transmission=physical.obstruction.hearing,
                    closed_smell_transmission=physical.obstruction.smell,
                ),
            )
        if physical.owner_id is not None:
            registry.add_component(
                item.id,
                OwnershipComponent(physical.owner_id),
            )
        if physical.custodian_id is not None:
            registry.add_component(
                item.id,
                CustodyComponent(physical.custodian_id),
            )
    _validate_materialized_physical_relations(registry, physical_objects)


def _validate_materialized_physical_relations(
    registry: Registry,
    physical_objects: list[WorldObjectDefinition],
) -> None:
    occupancy: dict[tuple[str, str], int] = {}
    relations = {
        item.id: registry.get_component(
            item.id,
            SpatialParentRelationComponent,
        )
        for item in physical_objects
    }
    validate_spatial_relation_acyclicity(relations)
    for item in sorted(physical_objects, key=lambda value: value.id):
        relation = relations[item.id]
        if relation.kind is PhysicalRelationKind.ON_FLOOR:
            if relation.parent_id != item.room_id:
                raise ValueError(
                    f"physical object {item.id} floor parent must be its room"
                )
            continue
        if relation.kind in {
            PhysicalRelationKind.ATTACHED_TO,
            PhysicalRelationKind.HELD_BY,
        }:
            continue
        if not registry.has_component(
            relation.parent_id,
            OccupancySlotsComponent,
        ):
            raise ValueError(
                f"physical object {item.id} parent has no occupancy slots"
            )
        if relation.slot_id is None:
            raise AssertionError("slotted relation validation lost slot_id")
        slot = registry.get_component(
            relation.parent_id,
            OccupancySlotsComponent,
        ).slot(relation.slot_id)
        if relation.kind not in slot.accepted_relations:
            raise ValueError(
                f"slot {relation.slot_id} does not accept {relation.kind.value}"
            )
        capability_type: type[SupportComponent] | type[ContainerComponent] | None
        if relation.kind is PhysicalRelationKind.ON_SUPPORT:
            capability_type = SupportComponent
        elif relation.kind is PhysicalRelationKind.IN_CONTAINER:
            capability_type = ContainerComponent
        else:
            capability_type = None
        if capability_type is not None:
            if not registry.has_component(relation.parent_id, capability_type):
                raise ValueError(
                    f"physical object {item.id} parent lacks "
                    f"{capability_type.__name__}"
                )
            capability = registry.get_component(
                relation.parent_id,
                capability_type,
            )
            if relation.slot_id not in capability.slot_ids:
                raise ValueError(
                    f"physical object {item.id} uses a slot outside its "
                    f"{capability_type.__name__}"
                )
        key = (relation.parent_id, relation.slot_id)
        occupancy[key] = occupancy.get(key, 0) + 1
    for (parent_id, slot_id), count in sorted(occupancy.items()):
        slot = registry.get_component(
            parent_id,
            OccupancySlotsComponent,
        ).slot(slot_id)
        if count > slot.capacity:
            raise ValueError(
                f"physical slot {parent_id}.{slot_id} exceeds capacity"
            )


def _legacy_anchor(
    coordinate: Coordinate,
    metric: SpatialMetric | None = None,
) -> Coordinate:
    return (metric or SpatialMetric()).center_legacy_coordinate(
        coordinate
    ).to_coordinate()


def _runtime_world_location(
    location: WorldLocation,
    metric: SpatialMetric,
) -> WorldLocation:
    if location.local_coordinate is None:
        return location
    return replace(
        location,
        local_coordinate=_legacy_anchor(location.local_coordinate, metric),
    )


def _runtime_transaction_point(
    definition: TransactionPointDefinition,
    metric: SpatialMetric,
) -> TransactionPoint:
    point = definition.to_domain()
    return replace(
        point,
        position=_legacy_anchor(point.position, metric),
        coordinate_scale=metric.microcells_per_legacy_cell,
        staffing=(
            replace(
                point.staffing,
                staff_position=_legacy_anchor(
                    point.staffing.staff_position,
                    metric,
                ),
            )
            if point.staffing is not None
            else None
        ),
    )


def _initial_interaction_approach(
    registry: Registry,
    world: WorldMap,
    coordinate: Coordinate,
) -> Coordinate:
    if not registry.has_resource(PhysicalInteractionRegistry):
        return coordinate
    target_ids = [
        station.id
        for station in world.stations
        if station.position == coordinate
    ]
    target_ids.extend(
        point.id
        for point in world.transaction_points
        if point.position == coordinate
    )
    interactions = registry.get_resource(PhysicalInteractionRegistry)
    candidates = [
        (target_id, approach)
        for target_id in sorted(target_ids)
        for approach in interactions.approach_anchors(target_id)
    ]
    if not candidates:
        return coordinate
    return min(
        candidates,
        key=lambda item: (
            abs(item[1].x - coordinate.x)
            + abs(item[1].y - coordinate.y),
            item[0],
            item[1].y,
            item[1].x,
        ),
    )[1]


def _materialize_character_physics(registry: Registry) -> None:
    spatial_index = registry.get_resource(SpatialIndex)
    for entity_id in registry.query_entities(
        PositionComponent,
        SpatialLocationComponent,
    ):
        if registry.has_component(entity_id, PhysicalObjectIdentityComponent):
            continue
        location = registry.get_component(
            entity_id,
            SpatialLocationComponent,
        ).location
        if location.local_coordinate is None:
            continue
        world = local_world_for_agent(registry, entity_id)
        if world is None:
            raise ValueError(
                f"entity {entity_id} has no local world for physical placement"
            )
        position = registry.get_component(entity_id, PositionComponent)
        state = PhysicalStateComponent(
            pose=PhysicalPose(location.place_id, position.coordinate),
            footprint=STANDING_CHARACTER_FOOTPRINT,
            movement_obstruction=MovementObstruction.HARD,
            vision_obstruction=VisionObstruction.TRANSPARENT,
        )
        if not world.grid.are_walkable(state.occupied_cells):
            raise ValueError(
                f"entity {entity_id} standing footprint is not fully walkable"
            )
        spatial_index.add(SpatialIndexEntry(entity_id, state, dynamic=True))
        registry.add_component(entity_id, state)
        registry.add_component(entity_id, CharacterHandStateComponent())
        registry.add_component(entity_id, CharacterPostureComponent())
    for object_id, relation in registry.query(
        SpatialParentRelationComponent
    ):
        if (
            relation.kind is not PhysicalRelationKind.HELD_BY
            or not registry.has_component(
                relation.parent_id,
                CharacterHandStateComponent,
            )
        ):
            continue
        hands = registry.get_component(
            relation.parent_id,
            CharacterHandStateComponent,
        )
        if relation.slot_id in {"left", "both"}:
            if hands.left_hand_object_id is not None:
                raise ValueError(
                    f"character {relation.parent_id} left hand is over capacity"
                )
            hands.left_hand_object_id = object_id
        if relation.slot_id in {"right", "both"}:
            if hands.right_hand_object_id is not None:
                raise ValueError(
                    f"character {relation.parent_id} right hand is over capacity"
                )
            hands.right_hand_object_id = object_id


def _validate_initial_equipment(registry: Registry) -> None:
    occupancy: dict[tuple[str, EquipmentSlot], int] = {}
    for object_id, relation in registry.query(SpatialParentRelationComponent):
        if relation.kind is not PhysicalRelationKind.ATTACHED_TO:
            continue
        if relation.slot_id is None:
            if registry.has_component(object_id, WearableComponent):
                raise ValueError(
                    f"wearable object {object_id} attachment requires an equipment slot"
                )
            continue
        if not registry.has_component(object_id, WearableComponent):
            raise ValueError(
                f"slotted attachment {object_id} requires a wearable capability"
            )
        try:
            slot = EquipmentSlot(relation.slot_id)
        except ValueError as error:
            raise ValueError(
                f"physical object {object_id} uses unknown equipment slot "
                f"{relation.slot_id}"
            ) from error
        if not registry.has_component(
            relation.parent_id,
            CharacterEmbodimentComponent,
        ):
            raise ValueError(
                f"equipped object {object_id} parent is not an embodied character"
            )
        wearable = registry.get_component(object_id, WearableComponent)
        if slot not in wearable.compatible_slots:
            raise ValueError(
                f"equipped object {object_id} is incompatible with {slot.value}"
            )
        embodiment = registry.get_component(
            relation.parent_id,
            CharacterEmbodimentComponent,
        )
        capacity = embodiment.equipment_slot_capacities.get(slot)
        if capacity is None:
            raise ValueError(
                f"character {relation.parent_id} does not support {slot.value}"
            )
        key = (relation.parent_id, slot)
        occupancy[key] = occupancy.get(key, 0) + 1
        if occupancy[key] > capacity:
            raise ValueError(
                f"character {relation.parent_id} equipment slot "
                f"{slot.value} exceeds capacity"
            )
        if registry.has_component(object_id, PhysicalStateComponent):
            actor_state = registry.get_component(
                relation.parent_id,
                PhysicalStateComponent,
            )
            object_state = registry.get_component(
                object_id,
                PhysicalStateComponent,
            )
            registry.set_component(
                object_id,
                replace(
                    object_state,
                    pose=replace(
                        object_state.pose,
                        room_id=actor_state.pose.room_id,
                        anchor=actor_state.pose.anchor,
                    ),
                ),
            )


def _validate_initial_carried_load(registry: Registry) -> None:
    for character_id in registry.query_entities(
        CharacterEmbodimentComponent,
        CarriedLoadComponent,
    ):
        embodiment = registry.get_component(
            character_id,
            CharacterEmbodimentComponent,
        )
        load = registry.get_component(character_id, CarriedLoadComponent)
        if load.known_mass_kg > embodiment.max_carried_mass_kg:
            raise ValueError(
                f"character {character_id} initial carried mass exceeds capacity"
            )
        for object_id, relation in registry.query(
            SpatialParentRelationComponent
        ):
            if relation.parent_id != character_id or relation.kind not in {
                PhysicalRelationKind.HELD_BY,
                PhysicalRelationKind.ATTACHED_TO,
            }:
                continue
            if not registry.has_component(
                object_id,
                ObjectIntrinsicComponent,
            ):
                continue
            mass = registry.get_component(
                object_id,
                ObjectIntrinsicComponent,
            ).mass_kg
            if (
                mass is not None
                and mass > embodiment.max_single_object_mass_kg
            ):
                raise ValueError(
                    f"character {character_id} initially carries over-limit "
                    f"object {object_id}"
                )


def _build_physical_interaction_registry(
    registry: Registry,
    city: CityWorld | None,
    source: CityWorldDefinition,
) -> PhysicalInteractionRegistry:
    if city is None:
        return PhysicalInteractionRegistry({}, {})
    spatial_index = registry.get_resource(SpatialIndex)
    targets: dict[str, PhysicalInteractionTarget] = {}
    for entity_id in registry.query_entities(
        PhysicalObjectIdentityComponent,
        PhysicalStateComponent,
    ):
        state = registry.get_component(entity_id, PhysicalStateComponent)
        world = city.room_world(state.pose.room_id)
        approaches = _interaction_approach_anchors(
            world,
            spatial_index,
            state,
        )
        occupancy: dict[str, tuple[Coordinate, ...]] = {}
        if registry.has_component(entity_id, OccupancySlotsComponent):
            for slot in registry.get_component(
                entity_id,
                OccupancySlotsComponent,
            ).slots:
                if PhysicalRelationKind.OCCUPIES_SLOT in slot.accepted_relations:
                    occupancy[slot.id] = _interaction_occupancy_anchors(
                        world,
                        spatial_index,
                        entity_id,
                        state,
                    )
        targets[entity_id] = PhysicalInteractionTarget(
            target_id=entity_id,
            room_id=state.pose.room_id,
            approach_anchors=approaches,
            occupancy_anchors=occupancy,
        )
    transaction_staff_anchors: dict[str, tuple[Coordinate, ...]] = {}
    for room in source.rooms:
        runtime_world = city.room_world(room.id)
        metric = room.world.spatial_metric.to_domain()
        for point in room.world.transaction_points:
            if point.staffing is None or point.id not in targets:
                continue
            target = targets[point.id]
            customer_fallback = runtime_world.transaction_point(
                point.id
            ).position
            staff_fallback = _legacy_anchor(
                point.staffing.staff_position.to_domain(),
                metric,
            )
            pairs = [
                (customer, staff)
                for customer in target.approach_anchors
                for staff in target.approach_anchors
                if customer != staff
                and STANDING_CHARACTER_FOOTPRINT.translated_cells(
                    customer
                ).isdisjoint(
                    STANDING_CHARACTER_FOOTPRINT.translated_cells(staff)
                )
            ]
            if not pairs:
                continue
            customer, staff = min(
                pairs,
                key=lambda pair: (
                    abs(pair[0].x - customer_fallback.x)
                    + abs(pair[0].y - customer_fallback.y)
                    + abs(pair[1].x - staff_fallback.x)
                    + abs(pair[1].y - staff_fallback.y),
                    pair[0].y,
                    pair[0].x,
                    pair[1].y,
                    pair[1].x,
                ),
            )
            compatible_customers = tuple(
                candidate
                for candidate in target.approach_anchors
                if STANDING_CHARACTER_FOOTPRINT.translated_cells(
                    candidate
                ).isdisjoint(
                    STANDING_CHARACTER_FOOTPRINT.translated_cells(staff)
                )
            )
            targets[point.id] = replace(
                target,
                approach_anchors=tuple(
                    sorted(
                        compatible_customers,
                        key=lambda item: (
                            0 if item == customer else 1,
                            abs(item.x - customer_fallback.x)
                            + abs(item.y - customer_fallback.y),
                            item.y,
                            item.x,
                        ),
                    )[: point.capacity]
                ),
            )
            transaction_staff_anchors[point.id] = (staff,)
    transition_doors = {
        entrance.id: entrance.door_object_id
        for building in source.buildings
        for entrance in building.entrances
        if entrance.door_object_id is not None
    }
    transition_doors.update(
        {
            portal.id: portal.door_object_id
            for portal in source.portals
            if portal.door_object_id is not None
        }
    )
    return PhysicalInteractionRegistry(
        targets,
        transition_doors,
        transaction_staff_anchors,
    )


def _interaction_approach_anchors(
    world: WorldMap,
    spatial_index: SpatialIndex,
    target: PhysicalStateComponent,
) -> tuple[Coordinate, ...]:
    target_cells = target.occupied_cells
    envelope = target.footprint.contact_envelope(
        target.pose.anchor,
        target.pose.orientation,
    )
    candidates = {
        Coordinate(contact.x - offset.x, contact.y - offset.y)
        for contact in envelope
        for offset in STANDING_CHARACTER_FOOTPRINT.cells
    }
    valid = []
    for anchor in candidates:
        state = PhysicalStateComponent(
            pose=PhysicalPose(target.pose.room_id, anchor),
            footprint=STANDING_CHARACTER_FOOTPRINT,
            movement_obstruction=MovementObstruction.HARD,
        )
        if state.occupied_cells & target_cells:
            continue
        if not state.occupied_cells & envelope:
            continue
        if not world.grid.are_walkable(state.occupied_cells):
            continue
        if not spatial_index.can_place(state):
            continue
        valid.append(anchor)
    return tuple(
        sorted(
            valid,
            key=lambda item: (
                abs(item.x - target.pose.anchor.x)
                + abs(item.y - target.pose.anchor.y),
                item.y,
                item.x,
            ),
        )
    )


def _interaction_occupancy_anchors(
    world: WorldMap,
    spatial_index: SpatialIndex,
    target_id: str,
    target: PhysicalStateComponent,
) -> tuple[Coordinate, ...]:
    candidates = {
        target.pose.anchor,
        *target.occupied_cells,
    }
    valid = []
    for anchor in candidates:
        state = PhysicalStateComponent(
            pose=PhysicalPose(target.pose.room_id, anchor),
            footprint=STANDING_CHARACTER_FOOTPRINT,
            movement_obstruction=MovementObstruction.HARD,
        )
        if not world.grid.are_walkable(state.occupied_cells):
            continue
        if not spatial_index.can_place(
            state,
            authorized_overlaps=frozenset({target_id}),
        ):
            continue
        valid.append(anchor)
    return tuple(
        sorted(
            valid,
            key=lambda item: (
                abs(item.x - target.pose.anchor.x)
                + abs(item.y - target.pose.anchor.y),
                item.y,
                item.x,
            ),
        )
    )


def _transaction_staff_position(
    registry: Registry,
    definition: TransactionPointDefinition,
) -> Coordinate:
    if definition.staffing is None:
        raise ValueError("transaction staff position requires staffing")
    fallback = _legacy_anchor(definition.staffing.staff_position.to_domain())
    if not registry.has_resource(PhysicalInteractionRegistry):
        return fallback
    interactions = registry.get_resource(PhysicalInteractionRegistry)
    configured = interactions.transaction_staff_positions(definition.id)
    if configured:
        return configured[0]
    approaches = interactions.approach_anchors(definition.id)
    if not approaches:
        return fallback
    customer_fallback = _legacy_anchor(definition.position.to_domain())
    customer = min(
        approaches,
        key=lambda item: (
            abs(item.x - customer_fallback.x)
            + abs(item.y - customer_fallback.y),
            item.y,
            item.x,
        ),
    )
    staff_candidates = tuple(
        coordinate
        for coordinate in approaches
        if coordinate != customer
        and STANDING_CHARACTER_FOOTPRINT.translated_cells(
            coordinate
        ).isdisjoint(
            STANDING_CHARACTER_FOOTPRINT.translated_cells(customer)
        )
    )
    return min(
        staff_candidates or approaches,
        key=lambda item: (
            abs(item.x - fallback.x) + abs(item.y - fallback.y),
            item.y,
            item.x,
        ),
    )


def _connection_approach(
    interactions: PhysicalInteractionRegistry | None,
    door_id: str | None,
    room_id: str,
    fallback: Coordinate,
) -> Coordinate:
    if interactions is None or door_id is None:
        return fallback
    target = interactions.targets.get(door_id)
    if target is None or target.room_id != room_id or not target.approach_anchors:
        return fallback
    return min(
        target.approach_anchors,
        key=lambda item: (
            abs(item.x - fallback.x) + abs(item.y - fallback.y),
            item.y,
            item.x,
        ),
    )


def _is_runtime_legacy_center(
    world: WorldMap,
    coordinate: Coordinate,
) -> bool:
    if world.coordinate_system is LocalCoordinateSystem.LEGACY_CELL:
        return True
    scale = world.microcells_per_legacy_cell
    center = scale // 2
    return coordinate.x % scale == center and coordinate.y % scale == center


def _build_world(definition: WorldDefinition) -> WorldMap:
    metric = definition.spatial_metric.to_domain()
    grid = WorldGrid(
        width=metric.scale_legacy_extent(definition.width),
        height=metric.scale_legacy_extent(definition.height),
        blocked=frozenset(
            microcell
            for coordinate in definition.blocked
            for microcell in metric.legacy_cell_microcells(
                coordinate.to_domain()
            )
        ),
    )
    zones = tuple(
        Zone(
            id=zone.id,
            name=zone.name,
            zone_type=zone.type,
            tiles=frozenset(
                microcell
                for legacy_tile in (
                    zone.bounds.tiles()
                    if zone.bounds is not None
                    else frozenset(
                        tile.to_domain() for tile in zone.tiles or []
                    )
                )
                for microcell in metric.legacy_cell_microcells(legacy_tile)
            ),
        )
        for zone in definition.zones
    )
    stations = tuple(
        AffordanceStation(
            id=station.id,
            name=station.name,
            position=_legacy_anchor(station.position.to_domain(), metric),
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
    transaction_points = tuple(
        _runtime_transaction_point(point, metric)
        for point in definition.transaction_points
    )
    return WorldMap(
        grid=grid,
        zones=zones,
        stations=stations,
        transaction_points=transaction_points,
        coordinate_system=LocalCoordinateSystem.MICROCELL,
        microcells_per_legacy_cell=metric.microcells_per_legacy_cell,
    )


def _build_city_world(definition: CityWorldDefinition) -> CityWorld:
    bounds = definition.city.bounds_meters
    room_worlds = {
        room.id: _build_world(room.world)
        for room in definition.rooms
    }
    rooms = tuple(
        Room(
            id=room.id,
            key=room.key,
            name=room.name,
            room_type=room.type,
            building_id=room.building_id,
            offset=room.world.spatial_metric.to_domain().scale_legacy_coordinate(
                room.offset.to_domain()
            ),
            world=room_worlds[room.id],
        )
        for room in definition.rooms
    )
    objects = tuple(
        WorldObject(
            id=item.id,
            name=item.name,
            object_kind=item.object_kind,
            building_id=item.building_id,
            room_id=item.room_id,
            position=_legacy_anchor(
                item.position.to_domain(),
                next(
                    room.world.spatial_metric.to_domain()
                    for room in definition.rooms
                    if room.id == item.room_id
                ),
            ),
            station=(
                room_worlds[item.room_id].station(item.id)
                if item.object_kind == "affordance"
                else None
            ),
            transaction_point=(
                room_worlds[item.room_id].transaction_point(item.id)
                if item.object_kind == "transaction"
                else None
            ),
        )
        for item in definition.objects
    )
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
        city_zones=tuple(
            CityZone(item.id, item.name, item.center.to_domain())
            for item in definition.districts
        ),
        buildings=tuple(
            Building(
                id=item.id,
                name=item.name,
                district_id=item.district_id,
                city_position=item.city_position.to_domain(),
                room_ids=tuple(item.room_ids),
                entrances=tuple(
                    BuildingEntrance(
                        id=entrance.id,
                        room_id=entrance.room_id,
                        local_coordinate=_legacy_anchor(
                            entrance.local_coordinate.to_domain(),
                            next(
                                room.world.spatial_metric.to_domain()
                                for room in definition.rooms
                                if room.id == entrance.room_id
                            ),
                        ),
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
        rooms=rooms,
        portals=tuple(
            BuildingPortal(
                id=item.id,
                building_id=item.building_id,
                from_room_id=item.from_room_id,
                from_coordinate=_legacy_anchor(
                    item.from_coordinate.to_domain(),
                    next(
                        room.world.spatial_metric.to_domain()
                        for room in definition.rooms
                        if room.id == item.from_room_id
                    ),
                ),
                to_room_id=item.to_room_id,
                to_coordinate=_legacy_anchor(
                    item.to_coordinate.to_domain(),
                    next(
                        room.world.spatial_metric.to_domain()
                        for room in definition.rooms
                        if room.id == item.to_room_id
                    ),
                ),
                bidirectional=item.bidirectional,
                available=item.available,
            )
            for item in definition.portals
        ),
        objects=tuple(sorted(objects, key=lambda item: item.id)),
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
    physical_interactions: PhysicalInteractionRegistry | None = None,
    spatial_index: SpatialIndex | None = None,
) -> SpaceRegistry:
    registry = SpaceRegistry()
    if city is None:
        topology = GridTopology("implicit-building", world, spatial_index)
        registry.register_space(
            Space(
                id="implicit-building",
                topology=topology,
                kind="building",
            )
        )
        _register_map_destinations(
            registry,
            topology,
            world,
            physical_interactions=physical_interactions,
        )
        return registry

    city_topology = SparseGraphTopology(city.id, city)
    registry.register_space(
        Space(
            id=city.id,
            topology=city_topology,
            kind="city",
        )
    )
    for city_zone in sorted(city.city_zones, key=lambda item: item.id):
        registry.register_space(
            Space(
                id=city_zone.id,
                topology=ContainerTopology(city_zone.id),
                kind="city_zone",
            )
        )
        registry.register_containment(city.id, city_zone.id)
    for building in sorted(city.buildings, key=lambda item: item.id):
        registry.register_space(
            Space(
                id=building.id,
                topology=ContainerTopology(building.id),
                kind="building",
                metadata={"city_zone_id": building.district_id},
            )
        )
        registry.register_containment(building.district_id, building.id)

    room_topologies: dict[str, GridTopology] = {}
    for room in sorted(city.rooms, key=lambda item: item.id):
        building = city.building(room.building_id)
        topology = GridTopology(room.id, room.world, spatial_index)
        room_topologies[room.id] = topology
        registry.register_space(
            Space(
                id=room.id,
                topology=topology,
                kind="room",
                metadata={
                    "building_id": building.id,
                    "room_key": room.key,
                    "offset": room.offset.to_payload(),
                },
            )
        )
        registry.register_containment(building.id, room.id)
        _register_map_destinations(
            registry,
            topology,
            room.world,
            space_destination_id=room.id,
            physical_interactions=physical_interactions,
        )

    for portal in sorted(city.portals, key=lambda item: item.id):
        door_id = (
            physical_interactions.door_for_transition(portal.id)
            if physical_interactions is not None
            else None
        )
        registry.register_transition(
            Transition(
                id=portal.id,
                from_locator=room_topologies[
                    portal.from_room_id
                ].locator(
                    _connection_approach(
                        physical_interactions,
                        door_id,
                        portal.from_room_id,
                        portal.from_coordinate,
                    )
                ),
                to_locator=room_topologies[
                    portal.to_room_id
                ].locator(
                    _connection_approach(
                        physical_interactions,
                        door_id,
                        portal.to_room_id,
                        portal.to_coordinate,
                    )
                ),
                traversal_kind="room_portal",
                executor_id="portal",
                cost_model_id="portal",
                bidirectional=portal.bidirectional,
                metadata={
                    "building_id": portal.building_id,
                    "available": portal.available,
                    "door_object_id": door_id,
                },
            )
        )

    for building in sorted(city.buildings, key=lambda item: item.id):
        for entrance in sorted(building.entrances, key=lambda item: item.id):
            door_id = (
                physical_interactions.door_for_transition(entrance.id)
                if physical_interactions is not None
                else None
            )
            room_locator = room_topologies[entrance.room_id].locator(
                _connection_approach(
                    physical_interactions,
                    door_id,
                    entrance.room_id,
                    entrance.local_coordinate,
                )
            )
            city_locator = city_topology.node_locator(entrance.network_node_id)
            registry.register_transition(
                Transition(
                    id=entrance.id,
                    from_locator=room_locator,
                    to_locator=city_locator,
                    traversal_kind="building_entrance",
                    executor_id="travel",
                    cost_model_id="entrance",
                    bidirectional=True,
                    metadata={
                        "building_id": building.id,
                        "room_id": entrance.room_id,
                        "network_node_id": entrance.network_node_id,
                        "door_object_id": door_id,
                    },
                )
            )
            registry.register_destination(building.id, room_locator)

    for place in sorted(city.outdoor_places, key=lambda item: item.id):
        registry.register_destination(
            place.id,
            city_topology.node_locator(place.network_node_id),
        )
    if physical_interactions is not None:
        for target_id in sorted(physical_interactions.targets):
            target = physical_interactions.targets[target_id]
            target_topology = room_topologies.get(target.room_id)
            if target_topology is None:
                continue
            for coordinate in target.approach_anchors:
                registry.register_destination(
                    target_id,
                    target_topology.locator(coordinate),
                )
    return registry


def _register_map_destinations(
    registry: SpaceRegistry,
    topology: GridTopology,
    world: WorldMap,
    *,
    space_destination_id: str | None = None,
    physical_interactions: PhysicalInteractionRegistry | None = None,
) -> None:
    if space_destination_id is not None:
        for y in range(world.grid.height):
            for x in range(world.grid.width):
                coordinate = Coordinate(x, y)
                if (
                    world.grid.is_walkable(coordinate)
                    and _is_runtime_legacy_center(world, coordinate)
                ):
                    registry.register_destination(
                        space_destination_id,
                        topology.locator(coordinate),
                    )
    for zone in sorted(world.zones, key=lambda item: item.id):
        for coordinate in sorted(zone.tiles, key=lambda item: (item.y, item.x)):
            if not _is_runtime_legacy_center(world, coordinate):
                continue
            registry.register_destination(
                zone.id,
                topology.locator(coordinate),
            )
    for station in sorted(world.stations, key=lambda item: item.id):
        approaches = (
            physical_interactions.approach_anchors(station.id)
            if physical_interactions is not None
            else ()
        )
        for coordinate in approaches or (station.position,):
            registry.register_destination(
                station.id,
                topology.locator(coordinate),
            )
    for point in sorted(world.transaction_points, key=lambda item: item.id):
        approaches = (
            physical_interactions.approach_anchors(point.id)
            if physical_interactions is not None
            else ()
        )
        for coordinate in approaches or (point.position,):
            registry.register_destination(
                point.id,
                topology.locator(coordinate),
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
                        ActionType.NAVIGATE,
                        ActionType.TRANSACT,
                        ActionType.INTERACT,
                    }
                    or action.target is None
                    or action.target in known_ids
                ):
                    continue
                requested[action.target] = action.mode
        for target_id in sorted(requested):
            document_id = (
                f"navigation-plan-context:{character_id}:{target_id}"
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


def _initial_city_room_world(
    scenario: ScenarioDefinition,
    city: CityWorld,
) -> WorldMap:
    for entity in scenario.entities:
        raw = entity.components.get("spatial_location")
        if not raw or raw.get("local_coordinate") is None:
            continue
        place_id = raw.get("place_id")
        if isinstance(place_id, str):
            return city.room_world(place_id)
    if not city.rooms:
        raise ValueError("city world requires at least one room")
    return city.rooms[0].world


def _validate_spatial_location(
    city: CityWorld,
    location: WorldLocation,
) -> None:
    if location.local_coordinate is not None:
        room_world = city.room_world(location.place_id)
        if (
            not room_world.grid.is_walkable(location.local_coordinate)
        ):
            raise ValueError(
                f"invalid local coordinate for room {location.place_id}"
            )
    elif location.network_node_id is not None:
        city.node(location.network_node_id)
    elif location.edge_id is not None:
        city.edge(location.edge_id)
    else:
        raise ValueError("city location requires a node or edge")
