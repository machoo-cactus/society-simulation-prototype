from __future__ import annotations

import json
import types
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, ForwardRef, Literal, Union, cast, get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from stage0_sim.application import elements as element_models
from stage0_sim.application import scenario as scenario_models
from stage0_sim.application.elements import (
    BuildingInstanceDefinition,
    BuildingOverrideDefinition,
    CityWorldSourceDefinition,
    CityZoneSourceDefinition,
    ElementReference,
    ObjectOverrideDefinition,
    OutdoorPlaceSourceDefinition,
    RoomOverrideDefinition,
    ScenarioSourceDefinition,
)
from stage0_sim.application.migrations.constants import ELEMENT_SCHEMA_VERSION

type PathPart = str | int
type FieldPath = tuple[PathPart, ...]


@dataclass(frozen=True, slots=True)
class ScenarioFieldSchema:
    kind: str
    label: str
    field_name: str = ""
    annotation: Any = Any
    model: type[BaseModel] | None = None
    children: tuple[ScenarioFieldSchema, ...] = ()
    item: ScenarioFieldSchema | None = None
    value: ScenarioFieldSchema | None = None
    variants: tuple[tuple[str, str, ScenarioFieldSchema | None], ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    input_type: str = "text"
    required: bool = False
    minimum: float | int | None = None
    maximum: float | int | None = None
    minimum_exclusive: bool = False
    maximum_exclusive: bool = False
    arbitrary_json: bool = False
    extensible: bool = False


@dataclass(slots=True)
class ScenarioEditorNode:
    schema: ScenarioFieldSchema
    id: str = field(default_factory=lambda: uuid4().hex)
    value: str = ""
    choice: str = ""
    key: str = ""
    children: list[ScenarioEditorNode] = field(default_factory=list)
    items: list[ScenarioEditorNode] = field(default_factory=list)
    variants: dict[str, ScenarioEditorNode | None] = field(default_factory=dict)
    path: FieldPath = ()
    errors: list[str] = field(default_factory=list)

    @property
    def control_id(self) -> str:
        return f"scenario-field-{self.id}"


@dataclass(frozen=True, slots=True)
class ScenarioEditorError:
    message: str
    control_id: str
    node_id: str = ""


@dataclass(slots=True)
class ScenarioEditorViewState:
    selected_node_id: str = ""
    scope_node_id: str = ""
    zoom: float = 1.0
    camera_x: float = 0.5
    camera_y: float = 0.5


@dataclass(slots=True)
class ScenarioEditorDraft:
    token: str
    session_id: str
    resource_id: str
    original_id: str | None
    original_hash: str
    root: ScenarioEditorNode
    errors: list[ScenarioEditorError] = field(default_factory=list)
    view: ScenarioEditorViewState = field(default_factory=ScenarioEditorViewState)


class ScenarioEditorDraftStore:
    def __init__(
        self,
        *,
        maximum_drafts: int = 128,
        maximum_session_drafts: int = 16,
    ) -> None:
        self._drafts: OrderedDict[str, ScenarioEditorDraft] = OrderedDict()
        self.maximum_drafts = maximum_drafts
        self.maximum_session_drafts = maximum_session_drafts

    def create(
        self,
        session_id: str,
        scenario: ScenarioSourceDefinition,
        *,
        resource_id: str = "",
        original_id: str | None = None,
        original_hash: str = "",
    ) -> ScenarioEditorDraft:
        token = uuid4().hex
        draft = ScenarioEditorDraft(
            token=token,
            session_id=session_id,
            resource_id=resource_id,
            original_id=original_id,
            original_hash=original_hash,
            root=node_from_value(
                SCENARIO_EDITOR_SCHEMA,
                scenario.model_dump(mode="json"),
            ),
        )
        self._drafts[token] = draft
        self._trim(session_id)
        return draft

    def get(self, session_id: str, token: str) -> ScenarioEditorDraft | None:
        draft = self._drafts.get(token)
        if draft is None or draft.session_id != session_id:
            return None
        self._drafts.move_to_end(token)
        return draft

    def delete(self, session_id: str, token: str) -> None:
        draft = self._drafts.get(token)
        if draft is not None and draft.session_id == session_id:
            del self._drafts[token]

    def _trim(self, session_id: str) -> None:
        session_tokens = [
            token for token, draft in self._drafts.items() if draft.session_id == session_id
        ]
        while len(session_tokens) > self.maximum_session_drafts:
            del self._drafts[session_tokens.pop(0)]
        while len(self._drafts) > self.maximum_drafts:
            self._drafts.popitem(last=False)


@dataclass(slots=True)
class ElementEditorDraft:
    token: str
    session_id: str
    resource_id: str
    original_id: str | None
    original_hash: str
    kind: element_models.ElementKind
    root: ScenarioEditorNode
    errors: list[ScenarioEditorError] = field(default_factory=list)
    raw_json: str = ""


class ElementEditorDraftStore:
    def __init__(
        self,
        *,
        maximum_drafts: int = 128,
        maximum_session_drafts: int = 16,
    ) -> None:
        self._drafts: OrderedDict[str, ElementEditorDraft] = OrderedDict()
        self.maximum_drafts = maximum_drafts
        self.maximum_session_drafts = maximum_session_drafts

    def create(
        self,
        session_id: str,
        kind: element_models.ElementKind,
        value: dict[str, Any],
        *,
        resource_id: str = "",
        original_id: str | None = None,
        original_hash: str = "",
    ) -> ElementEditorDraft:
        token = uuid4().hex
        draft = ElementEditorDraft(
            token=token,
            session_id=session_id,
            resource_id=resource_id,
            original_id=original_id,
            original_hash=original_hash,
            kind=kind,
            root=node_from_value(ELEMENT_EDITOR_SCHEMAS[kind], value),
            raw_json=json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
        self._drafts[token] = draft
        self._trim(session_id)
        return draft

    def get(self, session_id: str, token: str) -> ElementEditorDraft | None:
        draft = self._drafts.get(token)
        if draft is None or draft.session_id != session_id:
            return None
        self._drafts.move_to_end(token)
        return draft

    def delete(self, session_id: str, token: str) -> None:
        draft = self._drafts.get(token)
        if draft is not None and draft.session_id == session_id:
            del self._drafts[token]

    def _trim(self, session_id: str) -> None:
        session_tokens = [
            token for token, draft in self._drafts.items() if draft.session_id == session_id
        ]
        while len(session_tokens) > self.maximum_session_drafts:
            del self._drafts[session_tokens.pop(0)]
        while len(self._drafts) > self.maximum_drafts:
            self._drafts.popitem(last=False)


type StructuredEditorDraft = ScenarioEditorDraft | ElementEditorDraft


KNOWN_ENTITY_COMPONENT_MODELS: dict[str, type[BaseModel] | None] = {
    "position": scenario_models.PositionDefinition,
    "spatial_location": scenario_models.SpatialLocationDefinition,
    "movement": scenario_models.MovementDefinition,
    "homeostasis": scenario_models.HomeostasisComponentDefinition,
    "activity": scenario_models.ActivityDefinition,
    "possessions": scenario_models.PossessionsComponentDefinition,
    "character_slot": scenario_models.CharacterSlotDefinition,
    "plan": scenario_models.PlanComponentDefinition,
    "goals": scenario_models.GoalsComponentDefinition,
    "information": scenario_models.InformationComponentDefinition,
    "content_endpoints": scenario_models.ContentEndpointsComponentDefinition,
    "known_text_addresses": scenario_models.KnownTextAddressesDefinition,
    "controller": scenario_models.ControllerDefinition,
    "senses": scenario_models.SensesDefinition,
    "embodiment": scenario_models.CharacterEmbodimentDefinition,
    "memory": scenario_models.MemoryComponentDefinition,
    "conversation": scenario_models.ConversationComponentDefinition,
    "metadata": None,
}

ARBITRARY_JSON_FIELDS: frozenset[tuple[type[BaseModel], str]] = frozenset(
    {
        (
            scenario_models.InitialInformationDocumentDefinition,
            "content",
        ),
        (
            scenario_models.InitialInformationSourceDefinition,
            "metadata",
        ),
        (scenario_models.TransportDefinition, "metro_lines"),
        (scenario_models.CharacterCustomFieldDefinition, "value"),
        (scenario_models.GoalsComponentDefinition, "goals"),
    }
)

EXTENSIBLE_PROFILE_MODELS: frozenset[type[BaseModel]] = frozenset(
    {
        scenario_models.CharacterIdentityDefinition,
        scenario_models.CharacterBodyMeasurementsDefinition,
        scenario_models.CharacterAppearanceDefinition,
        scenario_models.CharacterHealthConditionDefinition,
        scenario_models.CharacterHealthAllergyDefinition,
        scenario_models.CharacterMedicationDefinition,
        scenario_models.CharacterHealthDefinition,
        scenario_models.CharacterPersonalityDefinition,
        scenario_models.CharacterBackgroundDefinition,
        scenario_models.CharacterFinancialSituationDefinition,
        scenario_models.CharacterMotivationsDefinition,
        scenario_models.CharacterCapabilitiesDefinition,
        scenario_models.CharacterPreferencesDefinition,
        scenario_models.CharacterPresentationDefinition,
        scenario_models.CharacterDispositionsDefinition,
        scenario_models.CharacterCommunicationDefinition,
        scenario_models.CharacterDecisionCopingDefinition,
        scenario_models.CharacterLifeStructureDefinition,
        scenario_models.CharacterFamilyMemberDefinition,
        scenario_models.CharacterFamilyDefinition,
        scenario_models.CharacterRelationshipDefinition,
        scenario_models.CharacterCustomFieldDefinition,
        scenario_models.CharacterCustomSectionDefinition,
        scenario_models.CharacterProfileDefinition,
    }
)

# This registry is deliberately explicit. The coverage test fails when a
# scenario model gains a field until the editor classification is reviewed.
SCENARIO_EDITOR_MODEL_FIELDS: dict[type[BaseModel], frozenset[str]] = {
    ElementReference: frozenset(["kind", "id", "content_hash"]),
    element_models.NpcRoleElementDefinition: frozenset(
        [
            "schema_version",
            "id",
            "name",
            "description",
            "kind",
            "briefing",
            "tool_allowlist",
            "vision_range",
            "recognition_range",
            "hearing_range",
            "smell_range",
        ]
    ),
    element_models.ObjectElementDefinition: frozenset(
        [
            "schema_version",
            "id",
            "name",
            "description",
            "kind",
            "object_type",
            "physical",
            "supported_actions",
            "actions",
            "offers",
            "holdings",
            "available",
            "capacity",
            "operation",
            "npc_role",
            "request_timeout",
            "environment",
        ]
    ),
    element_models.ObjectPlacementDefinition: frozenset(
        ["key", "id", "element", "position", "placement", "staff_position"]
    ),
    element_models.RoomElementDefinition: frozenset(
        [
            "schema_version",
            "id",
            "name",
            "description",
            "kind",
            "room_type",
            "width",
            "height",
            "spatial_metric",
            "blocked",
            "zones",
            "objects",
        ]
    ),
    element_models.RoomPlacementDefinition: frozenset(
        ["key", "element", "offset"]
    ),
    element_models.BuildingPortalDefinition: frozenset(
        [
            "key",
            "from_room_key",
            "from_coordinate",
            "to_room_key",
            "to_coordinate",
            "bidirectional",
            "available",
            "door_object_id",
        ]
    ),
    element_models.BuildingEntranceElementDefinition: frozenset(
        ["key", "id", "room_key", "local_coordinate", "door_object_id"]
    ),
    element_models.BuildingElementDefinition: frozenset(
        [
            "schema_version",
            "id",
            "name",
            "description",
            "kind",
            "available",
            "environment",
            "rooms",
            "portals",
            "entrances",
        ]
    ),
    ObjectOverrideDefinition: frozenset(
        [
            "name",
            "available",
            "environment",
            "holdings",
            "offers",
            "npc_role",
        ]
    ),
    RoomOverrideDefinition: frozenset(
        [
            "name",
            "room_type",
            "object_overrides",
            "disabled_object_keys",
        ]
    ),
    BuildingOverrideDefinition: frozenset(
        [
            "name",
            "available",
            "environment",
            "room_overrides",
            "disabled_room_keys",
        ]
    ),
    BuildingInstanceDefinition: frozenset(
        [
            "id",
            "element",
            "city_position",
            "entrance_node_ids",
            "overrides",
        ]
    ),
    OutdoorPlaceSourceDefinition: frozenset(
        [
            "id",
            "name",
            "city_position",
            "network_node_id",
            "available",
            "environment",
        ]
    ),
    CityZoneSourceDefinition: frozenset(
        ["id", "name", "center", "buildings", "outdoor_places"]
    ),
    CityWorldSourceDefinition: frozenset(
        [
            "type",
            "city",
            "city_zones",
            "transport",
            "building_order",
            "outdoor_place_order",
            "npc_role_order",
        ]
    ),
    ScenarioSourceDefinition: frozenset(
        [
            "schema_version",
            "name",
            "seed",
            "dt",
            "speed",
            "run_id",
            "items",
            "calendar",
            "weather",
            "world",
            "homeostasis",
            "system1",
            "memory",
            "perception",
            "cognition",
            "engagement",
            "text_content",
            "character_situation_synthesis",
            "entities",
        ]
    ),
    scenario_models.CoordinateDefinition: frozenset(["x", "y"]),
    scenario_models.SpatialMetricDefinition: frozenset(
        ["microcells_per_legacy_cell"]
    ),
    scenario_models.FootprintDefinition: frozenset(["cells"]),
    scenario_models.PhysicalObstructionDefinition: frozenset(
        ["movement", "vision", "hearing", "smell"]
    ),
    scenario_models.ObjectDimensionsDefinition: frozenset(
        ["length_cm", "width_cm", "height_cm"]
    ),
    scenario_models.ObjectIntrinsicsDefinition: frozenset(
        ["mass_kg", "dimensions_cm", "size_class"]
    ),
    scenario_models.OccupancySlotDefinition: frozenset(
        ["id", "accepted_relations", "capacity"]
    ),
    scenario_models.PortableCapabilityDefinition: frozenset(["two_handed"]),
    scenario_models.ReadableCapabilityDefinition: frozenset(["document_id"]),
    scenario_models.TextPrincipalDefinition: frozenset(["kind", "id"]),
    scenario_models.TextAccessGrantDefinition: frozenset(
        ["operation", "principals"]
    ),
    scenario_models.TextAccessPolicyDefinition: frozenset(["grants"]),
    scenario_models.ContentEndpointDefinition: frozenset(
        [
            "id",
            "label",
            "kind",
            "resource_id",
            "operations",
            "access_mode",
            "lists_items",
            "originates_messages",
            "notifies_owner",
            "created_media_kind",
            "created_mode",
            "created_access_policy",
        ]
    ),
    scenario_models.ContentEndpointsComponentDefinition: frozenset(
        ["endpoints"]
    ),
    scenario_models.ConsumableCapabilityDefinition: frozenset(
        ["item_id", "servings"]
    ),
    scenario_models.UsableCapabilityDefinition: frozenset(["use_kind"]),
    scenario_models.OpenableCapabilityDefinition: frozenset(
        ["initially_locked"]
    ),
    scenario_models.ObjectEffectDefinition: frozenset(
        ["id", "target", "operation", "value"]
    ),
    scenario_models.WearableCapabilityDefinition: frozenset(
        ["compatible_slots", "effects"]
    ),
    scenario_models.ScentSourceCapabilityDefinition: frozenset(
        ["scent_id", "description", "emission_range"]
    ),
    scenario_models.SupportCapabilityDefinition: frozenset(["slot_ids"]),
    scenario_models.ContainerCapabilityDefinition: frozenset(["slot_ids"]),
    scenario_models.PhysicalCapabilitiesDefinition: frozenset(
        [
            "slots",
            "support",
            "container",
            "portable",
            "readable",
            "content_endpoints",
            "consumable",
            "usable",
            "openable",
            "wearable",
            "scent_source",
        ]
    ),
    scenario_models.PhysicalObjectDefinition: frozenset(
        [
            "footprint",
            "intrinsics",
            "obstruction",
            "capabilities",
            "initial_open",
            "owner_id",
            "custodian_id",
        ]
    ),
    scenario_models.PhysicalParentRelationDefinition: frozenset(
        ["kind", "parent_id", "slot_id"]
    ),
    scenario_models.PhysicalPlacementDefinition: frozenset(
        ["anchor", "orientation", "parent_relation"]
    ),
    scenario_models.ItemCatalogEntryDefinition: frozenset(
        ["id", "name", "unit"]
    ),
    scenario_models.ItemAmountDefinition: frozenset(["item_id", "quantity"]),
    scenario_models.NpcRoleDefinition: frozenset(
        [
            "id",
            "name",
            "briefing",
            "tool_allowlist",
            "vision_range",
            "recognition_range",
            "hearing_range",
            "smell_range",
        ]
    ),
    scenario_models.TransactionOfferDefinition: frozenset(
        [
            "id",
            "name",
            "character_gives",
            "character_receives",
            "duration",
        ]
    ),
    scenario_models.TransactionPointDefinition: frozenset(
        [
            "id",
            "name",
            "position",
            "offers",
            "holdings",
            "available",
            "capacity",
            "operation",
            "staffing",
            "environment",
        ]
    ),
    scenario_models.TransactionStaffingDefinition: frozenset(
        ["role_id", "staff_position", "request_timeout"]
    ),
    scenario_models.BoundsDefinition: frozenset(["x", "y", "width", "height"]),
    scenario_models.ZoneDefinition: frozenset(["id", "name", "type", "bounds", "tiles"]),
    scenario_models.StationDefinition: frozenset(
        [
            "id",
            "name",
            "position",
            "supported_actions",
            "actions",
            "available",
            "capacity",
            "environment",
        ]
    ),
    scenario_models.OpeningWindowDefinition: frozenset(
        ["weekdays", "opens", "closes"]
    ),
    scenario_models.WeeklyScheduleDefinition: frozenset(["windows"]),
    scenario_models.EnvironmentalAvailabilityDefinition: frozenset(
        ["schedule", "closed_weather"]
    ),
    scenario_models.HomeostasisEffectDefinition: frozenset(
        [
            "satiety_delta",
            "energy_delta",
            "stress_delta",
            "hydration_delta",
            "social_connection_delta",
            "happiness_delta",
            "fear_delta",
            "satiety_target",
            "energy_target",
            "stress_target",
            "hydration_target",
            "social_connection_target",
            "happiness_target",
            "fear_target",
        ]
    ),
    scenario_models.StationActionDefinition: frozenset(["action", "duration", "effect"]),
    scenario_models.WorldDefinition: frozenset(
        [
            "width",
            "height",
            "spatial_metric",
            "blocked",
            "zones",
            "stations",
            "transaction_points",
        ]
    ),
    scenario_models.MapPointDefinition: frozenset(["x", "y"]),
    scenario_models.CityBoundsDefinition: frozenset(["min_x", "min_y", "max_x", "max_y"]),
    scenario_models.CityDefinition: frozenset(["id", "name", "bounds_meters"]),
    scenario_models.DistrictDefinition: frozenset(["id", "name", "center"]),
    scenario_models.BuildingEntranceDefinition: frozenset(
        [
            "id",
            "room_id",
            "local_coordinate",
            "neighborhood_node_id",
            "door_object_id",
        ]
    ),
    scenario_models.RoomDefinition: frozenset(
        ["id", "key", "name", "type", "building_id", "offset", "world"]
    ),
    scenario_models.BuildingPortalRuntimeDefinition: frozenset(
        [
            "id",
            "building_id",
            "from_room_id",
            "from_coordinate",
            "to_room_id",
            "to_coordinate",
            "bidirectional",
            "available",
            "door_object_id",
        ]
    ),
    scenario_models.WorldObjectDefinition: frozenset(
        [
            "id",
            "definition_id",
            "name",
            "object_kind",
            "building_id",
            "room_id",
            "position",
            "physical",
            "placement",
        ]
    ),
    scenario_models.BuildingDefinition: frozenset(
        [
            "id",
            "name",
            "district_id",
            "city_position",
            "room_ids",
            "entrances",
            "available",
            "environment",
        ]
    ),
    scenario_models.OutdoorPlaceDefinition: frozenset(
        [
            "id",
            "name",
            "district_id",
            "city_position",
            "network_node_id",
            "available",
            "environment",
        ]
    ),
    scenario_models.TransportNodeDefinition: frozenset(["id", "kind", "position", "place_id"]),
    scenario_models.TransportEdgeDefinition: frozenset(
        [
            "id",
            "from_node_id",
            "to_node_id",
            "allowed_modes",
            "distance_meters",
            "geometry",
            "speed_limit_mps",
            "bidirectional",
            "available",
            "environment",
        ]
    ),
    scenario_models.VehicleLocationDefinition: frozenset(["scale", "place_id", "network_node_id"]),
    scenario_models.VehicleDefinition: frozenset(
        [
            "id",
            "type",
            "name",
            "capacity",
            "location",
            "available",
            "environment",
        ]
    ),
    scenario_models.TransportDefinition: frozenset(
        [
            "nodes",
            "edges",
            "metro_lines",
            "vehicles",
            "walking_speed_mps",
            "cycling_speed_mps",
            "car_speed_mps",
            "metro_speed_mps",
        ]
    ),
    scenario_models.CityWorldDefinition: frozenset(
        [
            "type",
            "city",
            "districts",
            "buildings",
            "rooms",
            "portals",
            "objects",
            "outdoor_places",
            "transport",
        ]
    ),
    scenario_models.ActivityRatesDefinition: frozenset(
        [
            "satiety",
            "energy",
            "stress",
            "hydration",
            "social_connection",
            "happiness",
            "fear",
        ]
    ),
    scenario_models.HomeostasisSettingsDefinition: frozenset(
        {
            "activity_coefficients",
            "drink_hydration_delta",
            "read_happiness_delta",
            "social_connection_delta",
            "social_happiness_delta",
            "alarming_fear_delta",
            "calming_happiness_delta",
            "calming_fear_delta",
        }
    ),
    scenario_models.DriveThresholdDefinition: frozenset(
        ["critical", "recovery", "critical_when_high"]
    ),
    scenario_models.System1SettingsDefinition: frozenset(
        ["thresholds", "enabled_drives", "tie_break_order"]
    ),
    scenario_models.MemorySettingsDefinition: frozenset(
        ["semantic_weight", "recency_weight", "importance_weight", "recency_half_life"]
    ),
    scenario_models.PerceptionSettingsDefinition: frozenset(
        [
            "vision_range",
            "recognition_range",
            "voice_range",
            "whisper_range",
            "blocked_tiles_are_opaque",
            "inbox_limit",
            "fact_max_age_seconds",
            "renderer",
        ]
    ),
    scenario_models.CognitionSettingsDefinition: frozenset(
        [
            "model_profile",
            "npc_control_mode",
            "decision_timeout_seconds",
            "max_output_tokens",
            "max_read_tool_rounds",
            "max_state_changing_tools",
            "max_concurrency",
            "max_requests",
            "max_input_tokens",
            "max_total_output_tokens",
            "engagement_compiler",
            "tool_allowlist",
        ]
    ),
    scenario_models.EngagementCompilerSettingsDefinition: frozenset(
        [
            "model_profile",
            "timeout_seconds",
            "max_output_tokens",
            "max_concurrency",
            "max_requests",
            "max_input_tokens",
            "max_total_output_tokens",
        ]
    ),
    scenario_models.EngagementSettingsDefinition: frozenset(
        [
            "max_groups",
            "max_invocations_per_group",
            "max_public_text_chars",
            "short_activity_seconds",
            "medium_activity_seconds",
            "long_activity_seconds",
            "low_effort_energy_cost",
            "medium_effort_energy_cost",
            "high_effort_energy_cost",
            "calming_stress_delta",
            "activating_stress_delta",
            "quiet_sound_range",
            "normal_sound_range",
            "loud_sound_range",
            "alarming_listener_stress_delta",
        ]
    ),
    scenario_models.CharacterProfileTemplateDefinition: frozenset(["schema_version", "sections"]),
    scenario_models.CharacterSelectionConstraintsDefinition: frozenset(
        [
            "minimum_age",
            "maximum_age",
            "allowed_genders",
            "allowed_template_ids",
        ]
    ),
    scenario_models.CharacterSlotDefinition: frozenset(
        [
            "label",
            "briefing",
            "synthesis_guidance",
            "default_character_id",
            "constraints",
        ]
    ),
    scenario_models.EntityDefinition: frozenset(["id", "components"]),
    scenario_models.CalendarSettingsDefinition: frozenset(
        ["start_datetime", "update_interval_seconds"]
    ),
    scenario_models.WeatherStateDefinition: frozenset(
        [
            "condition",
            "temperature_c",
            "precipitation_mm_per_hour",
            "wind_speed_mps",
            "wind_direction_degrees",
            "visibility_meters",
        ]
    ),
    scenario_models.WeatherTransitionDefinition: frozenset(
        ["at_seconds", "state"]
    ),
    scenario_models.WeatherEffectsDefinition: frozenset(
        [
            "walking_speed_multiplier",
            "cycling_speed_multiplier",
            "visibility_multiplier",
            "wetness_gain_per_mm_hour_second",
            "base_drying_per_second",
            "wind_drying_per_mps_second",
            "temperature_drying_per_degree_second",
        ]
    ),
    scenario_models.WeatherSettingsDefinition: frozenset(
        ["initial", "transitions", "effects"]
    ),
    scenario_models.CharacterSituationSynthesisSettingsDefinition: frozenset(
        ["enabled"]
    ),
    scenario_models.InitialTextAttributionDefinition: frozenset(
        [
            "authoritative_actor_id",
            "display",
            "sender_address_id",
            "display_label",
        ]
    ),
    scenario_models.InitialTextBlockDefinition: frozenset(
        ["id", "kind", "text"]
    ),
    scenario_models.InitialTextArtifactDefinition: frozenset(
        [
            "id",
            "media_kind",
            "mode",
            "blocks",
            "access_policy",
            "attribution",
        ]
    ),
    scenario_models.InitialTextCollectionDefinition: frozenset(
        ["id", "kind", "members", "capacity", "access_policy"]
    ),
    scenario_models.InitialTextAddressDefinition: frozenset(
        [
            "id",
            "owner",
            "mailbox_id",
            "display_label",
            "accepted_senders",
            "sent_collection_id",
        ]
    ),
    scenario_models.InitialTextGroupDefinition: frozenset(
        ["id", "member_ids"]
    ),
    scenario_models.TextContentDefinition: frozenset(
        ["artifacts", "collections", "addresses", "groups"]
    ),
    scenario_models.ScenarioDefinition: frozenset(
        [
            "schema_version",
            "name",
            "seed",
            "dt",
            "speed",
            "run_id",
            "items",
            "npc_roles",
            "calendar",
            "weather",
            "world",
            "homeostasis",
            "system1",
            "memory",
            "perception",
            "cognition",
            "engagement",
            "text_content",
            "character_situation_synthesis",
            "entities",
        ]
    ),
    scenario_models.PositionDefinition: frozenset(["x", "y"]),
    scenario_models.MovementDefinition: frozenset({"destination"}),
    scenario_models.HomeostasisComponentDefinition: frozenset(
        [
            "satiety",
            "energy",
            "stress",
            "hydration",
            "social_connection",
            "happiness",
            "fear",
        ]
    ),
    scenario_models.SpatialLocationDefinition: frozenset(
        ["scale", "place_id", "local_coordinate", "network_node_id", "edge_id", "edge_progress"]
    ),
    scenario_models.CharacterIdentityDefinition: frozenset(
        ["display_name", "age", "birth_date", "gender", "pronouns", "occupation"]
    ),
    scenario_models.CharacterBodyMeasurementsDefinition: frozenset(
        [
            "measured_on",
            "height_cm",
            "weight_kg",
            "chest_cm",
            "waist_cm",
            "hips_cm",
            "inseam_cm",
            "shoe_size_system",
            "shoe_size_value",
        ]
    ),
    scenario_models.CharacterAppearanceDefinition: frozenset(
        ["summary", "height", "build", "hair", "eyes", "clothing", "distinguishing_features"]
    ),
    scenario_models.CharacterPersonalityDefinition: frozenset(
        ["summary", "traits", "temperament", "social_style", "speech_style", "strengths", "flaws"]
    ),
    scenario_models.CharacterBackgroundDefinition: frozenset(
        ["birthplace", "residence", "education", "history"]
    ),
    scenario_models.CharacterFinancialSituationDefinition: frozenset(
        [
            "as_of_date",
            "currency",
            "annual_gross_income",
            "income_band",
            "liquid_assets",
            "total_assets",
            "total_debt",
            "monthly_fixed_expenses",
            "housing_tenure",
            "financial_dependents",
        ]
    ),
    scenario_models.CharacterMotivationsDefinition: frozenset(
        ["values", "fears", "needs"]
    ),
    scenario_models.CharacterCapabilitiesDefinition: frozenset(
        ["skills", "knowledge_areas", "limitations"]
    ),
    scenario_models.CharacterPreferencesDefinition: frozenset(
        ["likes", "dislikes", "habits", "routines"]
    ),
    scenario_models.CharacterPresentationDefinition: frozenset(
        [
            "aesthetic_identity",
            "wardrobe_palette",
            "preferred_silhouettes",
            "preferred_fabrics",
            "formality_range",
            "comfort_priorities",
            "grooming_norms",
            "usual_accessories",
            "practical_constraints",
            "purchase_habits",
            "context_variations",
        ]
    ),
    scenario_models.CharacterDispositionsDefinition: frozenset(
        [
            "summary",
            "emotional_baseline",
            "sociability",
            "assertiveness",
            "patience",
            "conscientiousness",
            "openness",
            "adaptability",
            "risk_tolerance",
            "ambiguity_tolerance",
            "impulse_control",
            "conflict_style",
            "cooperation_style",
            "trust_formation",
            "boundary_setting",
            "help_seeking",
            "pressure_response",
            "fatigue_response",
            "novelty_response",
            "authority_response",
            "crowd_response",
        ]
    ),
    scenario_models.CharacterCommunicationDefinition: frozenset(
        [
            "cadence",
            "vocabulary",
            "directness",
            "politeness",
            "humor",
            "gesture",
            "posture",
            "facial_expressiveness",
            "listening_style",
            "disagreement_style",
            "apology_style",
            "with_intimates",
            "with_colleagues",
            "with_strangers",
            "with_authority",
        ]
    ),
    scenario_models.CharacterDecisionCopingDefinition: frozenset(
        [
            "information_seeking",
            "planning_horizon",
            "default_heuristics",
            "error_sensitivity",
            "persistence",
            "recovery_habits",
            "self_soothing",
            "stress_signals",
            "disposition_shifts",
        ]
    ),
    scenario_models.CharacterLifeStructureDefinition: frozenset(
        [
            "household",
            "recurring_obligations",
            "material_habits",
            "typical_possessions",
            "cultural_practices",
            "interests",
            "social_patterns",
        ]
    ),
    scenario_models.CharacterFamilyMemberDefinition: frozenset(
        [
            "member_id",
            "linked_character_id",
            "display_name",
            "relationship",
            "birth_date",
            "living_status",
            "residence",
            "household_member",
            "financial_dependent",
        ]
    ),
    scenario_models.CharacterFamilyDefinition: frozenset(["members"]),
    scenario_models.CharacterHealthConditionDefinition: frozenset(
        ["name", "status", "diagnosed_on", "notes"]
    ),
    scenario_models.CharacterHealthAllergyDefinition: frozenset(
        ["substance", "reaction", "severity"]
    ),
    scenario_models.CharacterMedicationDefinition: frozenset(
        ["name", "dose", "schedule", "purpose"]
    ),
    scenario_models.CharacterHealthDefinition: frozenset(
        [
            "as_of_date",
            "blood_type",
            "conditions",
            "allergies",
            "medications",
            "disabilities",
            "vision",
            "hearing",
            "mobility",
            "past_procedures",
            "dietary_restrictions",
        ]
    ),
    scenario_models.CharacterRelationshipDefinition: frozenset(
        ["target_id", "relationship", "sentiment", "notes"]
    ),
    scenario_models.CharacterCustomFieldDefinition: frozenset(
        ["key", "label", "value", "prompt_visible", "ui_visible"]
    ),
    scenario_models.CharacterCustomSectionDefinition: frozenset(
        ["id", "title", "prompt_visible", "ui_visible", "fields"]
    ),
    scenario_models.CharacterProfileDefinition: frozenset(
        [
            "template_id",
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
            "custom_sections",
        ]
    ),
    scenario_models.ControllerDefinition: frozenset(["enabled", "tool_allowlist"]),
    scenario_models.SensesDefinition: frozenset(
        ["vision_range", "recognition_range", "hearing_range", "smell_range"]
    ),
    scenario_models.EquipmentSlotCapacityDefinition: frozenset(
        ["slot", "capacity"]
    ),
    scenario_models.CharacterEmbodimentDefinition: frozenset(
        [
            "max_single_object_mass_kg",
            "max_carried_mass_kg",
            "equipment_slots",
        ]
    ),
    scenario_models.ActivityDefinition: frozenset({"type"}),
    scenario_models.PossessionsComponentDefinition: frozenset(["holdings"]),
    scenario_models.PlanActionDefinition: frozenset(
        [
            "action",
            "target",
            "duration",
            "mode",
            "offer_id",
            "interaction",
            "text_read",
            "text_write",
        ]
    ),
    scenario_models.InteractionSpecificationDefinition: frozenset(
        ["verb", "target_id", "destination_id", "slot_id"]
    ),
    scenario_models.TextAttributionRequestDefinition: frozenset(
        ["display", "sender_address_id", "display_label"]
    ),
    scenario_models.TextBlockDraftDefinition: frozenset(["text", "kind"]),
    scenario_models.TextReadSpecificationDefinition: frozenset(
        ["target_id", "endpoint_id", "artifact_id", "block_ids"]
    ),
    scenario_models.TextWriteSpecificationDefinition: frozenset(
        [
            "operation",
            "target_id",
            "endpoint_id",
            "attribution",
            "artifact_id",
            "expected_artifact_revision",
            "expected_collection_revision",
            "expected_sent_collection_revision",
            "block_id",
            "expected_block_revision",
            "blocks",
            "text",
            "start",
            "end",
            "recipient_address_id",
            "artifact_id_hint",
        ]
    ),
    scenario_models.PlanComponentDefinition: frozenset(["queue", "current"]),
    scenario_models.GoalsComponentDefinition: frozenset(
        ["goals"]
    ),
    scenario_models.InitialInformationSourceDefinition: frozenset(
        ["type", "observer_id", "reference_ids", "metadata"]
    ),
    scenario_models.InitialInformationVisibilityDefinition: frozenset(
        ["level", "owner_ids", "reader_ids"]
    ),
    scenario_models.InitialInformationTimeRangeDefinition: frozenset(["start", "end"]),
    scenario_models.InitialInformationDocumentDefinition: frozenset(
        [
            "id",
            "kind",
            "schema_id",
            "subject_ids",
            "content",
            "source",
            "valid_time",
            "recorded_at",
            "visibility",
        ]
    ),
    scenario_models.InformationComponentDefinition: frozenset({"documents"}),
    scenario_models.KnownTextAddressesDefinition: frozenset(
        ["address_ids"]
    ),
    scenario_models.InitialMemoryDefinition: frozenset(["text", "simulation_time", "importance"]),
    scenario_models.MemoryComponentDefinition: frozenset(["top_k", "initial_episodes"]),
    scenario_models.ConversationComponentDefinition: frozenset({"turns"}),
}


def scenario_editor_coverage_errors() -> tuple[str, ...]:
    errors: list[str] = []
    for model, registered in SCENARIO_EDITOR_MODEL_FIELDS.items():
        actual = frozenset(model.model_fields)
        if actual != registered:
            errors.append(
                f"{model.__name__}: registered={sorted(registered)!r}, actual={sorted(actual)!r}"
            )
    for model, field_name in ARBITRARY_JSON_FIELDS:
        if model not in SCENARIO_EDITOR_MODEL_FIELDS:
            errors.append(f"{model.__name__}.{field_name}: model is not registered")
        elif field_name not in model.model_fields:
            errors.append(f"{model.__name__}.{field_name}: unknown field")
    component_definition_names = {
        "PositionDefinition",
        "MovementDefinition",
        "SpatialLocationDefinition",
        "CharacterSlotDefinition",
        "ControllerDefinition",
        "SensesDefinition",
        "CharacterEmbodimentDefinition",
        "ActivityDefinition",
        "KnownTextAddressesDefinition",
    }
    discovered_components = {
        model
        for name, model in vars(scenario_models).items()
        if isinstance(model, type)
        and issubclass(model, BaseModel)
        and (
            name.endswith("ComponentDefinition")
            or name in component_definition_names
        )
    }
    registered_components = {
        model
        for model in KNOWN_ENTITY_COMPONENT_MODELS.values()
        if model is not None
    }
    if discovered_components != registered_components:
        errors.append(
            "entity components: registered="
            f"{sorted(item.__name__ for item in registered_components)!r}, "
            "discovered="
            f"{sorted(item.__name__ for item in discovered_components)!r}"
        )
    errors.extend(
        _recursive_descriptor_coverage_errors(
            (
                SCENARIO_EDITOR_SCHEMA,
                SCENARIO_V5_EDITOR_SCHEMA,
            ),
            (
                ScenarioSourceDefinition,
                scenario_models.ScenarioDefinition,
            ),
            include_entity_components=True,
        )
    )
    return tuple(errors)


def element_editor_coverage_errors() -> tuple[str, ...]:
    return tuple(
        _recursive_descriptor_coverage_errors(
            tuple(ELEMENT_EDITOR_SCHEMAS.values()),
            tuple(ELEMENT_EDITOR_MODELS.values()),
        )
    )


def _recursive_descriptor_coverage_errors(
    schemas: tuple[ScenarioFieldSchema, ...],
    roots: tuple[type[BaseModel], ...],
    *,
    include_entity_components: bool = False,
) -> list[str]:
    errors: list[str] = []
    described_models: set[type[BaseModel]] = set()
    visited_schemas: set[int] = set()

    def visit_schema(schema: ScenarioFieldSchema) -> None:
        if id(schema) in visited_schemas:
            return
        visited_schemas.add(id(schema))
        if schema.model is not None:
            described_models.add(schema.model)
            described_fields = frozenset(
                child.field_name for child in schema.children
            )
            actual_fields = frozenset(schema.model.model_fields)
            if described_fields != actual_fields:
                errors.append(
                    f"{schema.model.__name__} descriptor: "
                    f"described={sorted(described_fields)!r}, "
                    f"actual={sorted(actual_fields)!r}"
                )
        for child in schema.children:
            visit_schema(child)
        if schema.item is not None:
            visit_schema(schema.item)
        if schema.value is not None:
            visit_schema(schema.value)
        for _key, _label, variant in schema.variants:
            if variant is not None:
                visit_schema(variant)

    for schema in schemas:
        visit_schema(schema)

    expected_models: set[type[BaseModel]] = set()

    def visit_annotation(annotation: Any) -> None:
        if isinstance(annotation, ForwardRef):
            return
        origin = get_origin(annotation)
        if origin is not None:
            for argument in get_args(annotation):
                if argument is not type(None):
                    visit_annotation(argument)
            return
        if not _is_model(annotation) or annotation in expected_models:
            return
        expected_models.add(annotation)
        for field_name, model_field in annotation.model_fields.items():
            if (annotation, field_name) in ARBITRARY_JSON_FIELDS:
                continue
            visit_annotation(model_field.annotation)

    for root in roots:
        visit_annotation(root)
    if include_entity_components:
        for component_model in KNOWN_ENTITY_COMPONENT_MODELS.values():
            if component_model is not None:
                visit_annotation(component_model)
    missing = expected_models - described_models
    if missing:
        errors.append(
            "recursive descriptor models missing: "
            f"{sorted(model.__name__ for model in missing)!r}"
        )
    return errors


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return f"{name[:-3]}y"
    if name.endswith("s"):
        return name[:-1]
    return name


def _field_limits(
    field_info: FieldInfo,
) -> tuple[
    float | int | None,
    float | int | None,
    bool,
    bool,
]:
    minimum: float | int | None = None
    maximum: float | int | None = None
    minimum_exclusive = False
    maximum_exclusive = False
    for item in field_info.metadata:
        if getattr(item, "ge", None) is not None:
            minimum = item.ge
        if getattr(item, "gt", None) is not None:
            minimum = item.gt
            minimum_exclusive = True
        if getattr(item, "le", None) is not None:
            maximum = item.le
        if getattr(item, "lt", None) is not None:
            maximum = item.lt
            maximum_exclusive = True
    return minimum, maximum, minimum_exclusive, maximum_exclusive


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_enum(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _field_schema(
    annotation: Any,
    *,
    label: str,
    field_name: str = "",
    field_info: FieldInfo | None = None,
    owner: type[BaseModel] | None = None,
) -> ScenarioFieldSchema:
    if isinstance(annotation, ForwardRef):
        annotation = getattr(scenario_models, annotation.__forward_arg__)
    elif isinstance(annotation, str):
        annotation = getattr(scenario_models, annotation)
    required = bool(field_info and field_info.is_required())
    minimum, maximum, minimum_exclusive, maximum_exclusive = (
        _field_limits(field_info) if field_info is not None else (None, None, False, False)
    )
    if owner is not None and (owner, field_name) in ARBITRARY_JSON_FIELDS:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="json",
            required=required,
            arbitrary_json=True,
        )
    if owner is scenario_models.EntityDefinition and field_name == "components":
        return _components_schema(label, field_name)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, types.UnionType} and type(None) in args:
        non_none = tuple(item for item in args if item is not type(None))
        if len(non_none) == 1:
            child = _field_schema(
                non_none[0],
                label=label,
                field_name=field_name,
                field_info=field_info,
                owner=owner,
            )
            return ScenarioFieldSchema(
                kind="optional",
                label=label,
                field_name=field_name,
                annotation=annotation,
                item=child,
            )
        variants: list[tuple[str, str, ScenarioFieldSchema | None]] = [
            ("none", "Not included", None)
        ]
        for item in non_none:
            key = "grid" if item is scenario_models.WorldDefinition else "city"
            variant_label = (
                "Grid world" if item is scenario_models.WorldDefinition else "City world"
            )
            variants.append(
                (
                    key,
                    variant_label,
                    _field_schema(item, label=variant_label),
                )
            )
        return ScenarioFieldSchema(
            kind="union",
            label=label,
            field_name=field_name,
            annotation=annotation,
            variants=tuple(variants),
        )
    if origin in {list, set}:
        item_annotation = args[0]
        return ScenarioFieldSchema(
            kind="list",
            label=label,
            field_name=field_name,
            annotation=annotation,
            item=_field_schema(
                item_annotation,
                label=_humanize(_singular(field_name) or "Item"),
            ),
        )
    if origin is dict:
        key_annotation, value_annotation = args
        key_options: tuple[tuple[str, str], ...] = ()
        if _is_enum(key_annotation):
            key_options = tuple(
                (str(item.value), _humanize(str(item.value))) for item in key_annotation
            )
        return ScenarioFieldSchema(
            kind="mapping",
            label=label,
            field_name=field_name,
            annotation=annotation,
            value=_field_schema(
                value_annotation,
                label=_humanize(_singular(field_name) or "Value"),
            ),
            options=key_options,
        )
    if _is_model(annotation):
        if annotation not in SCENARIO_EDITOR_MODEL_FIELDS:
            raise TypeError(f"unregistered scenario editor model: {annotation.__name__}")
        children = tuple(
            _field_schema(
                model_field.annotation,
                label=_humanize(model_field_name),
                field_name=model_field_name,
                field_info=model_field,
                owner=annotation,
            )
            for model_field_name, model_field in annotation.model_fields.items()
        )
        return ScenarioFieldSchema(
            kind="model",
            label=label,
            field_name=field_name,
            annotation=annotation,
            model=annotation,
            children=children,
            extensible=annotation in EXTENSIBLE_PROFILE_MODELS,
        )
    if _is_enum(annotation):
        options = tuple((str(item.value), _humanize(str(item.value))) for item in annotation)
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="select",
            required=required,
            options=options,
        )
    if origin is Literal:
        options = tuple((str(item), _humanize(str(item))) for item in args)
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="select",
            required=required,
            options=options,
        )
    if annotation is bool:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="boolean",
            required=required,
            options=(("true", "Yes"), ("false", "No")),
        )
    if annotation in {int, float}:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="number",
            required=required,
            minimum=minimum,
            maximum=maximum,
            minimum_exclusive=minimum_exclusive,
            maximum_exclusive=maximum_exclusive,
        )
    if annotation is datetime:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="datetime",
            required=required,
        )
    if annotation is time:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="time",
            required=required,
        )
    if annotation is str:
        return ScenarioFieldSchema(
            kind="scalar",
            label=label,
            field_name=field_name,
            annotation=annotation,
            input_type="text",
            required=required,
        )
    raise TypeError(
        f"scenario editor field {owner.__name__ if owner else ''}.{field_name} "
        f"has unclassified annotation {annotation!r}"
    )


def _components_schema(label: str, field_name: str) -> ScenarioFieldSchema:
    children: list[ScenarioFieldSchema] = []
    for component_name, model in KNOWN_ENTITY_COMPONENT_MODELS.items():
        if component_name == "metadata":
            component = ScenarioFieldSchema(
                kind="scalar",
                label="Metadata",
                field_name=component_name,
                annotation=dict[str, Any],
                input_type="json",
                arbitrary_json=True,
            )
        else:
            assert model is not None
            component = _field_schema(
                model,
                label=_humanize(component_name),
                field_name=component_name,
            )
        children.append(
            ScenarioFieldSchema(
                kind="optional",
                label=component.label,
                field_name=component_name,
                item=component,
            )
        )
    unknown = ScenarioFieldSchema(
        kind="mapping",
        label="Unknown Passthrough Components",
        field_name="unknown_components",
        value=ScenarioFieldSchema(
            kind="scalar",
            label="Component JSON",
            input_type="json",
            arbitrary_json=True,
        ),
    )
    children.append(unknown)
    return ScenarioFieldSchema(
        kind="components",
        label=label,
        field_name=field_name,
        children=tuple(children),
    )


SCENARIO_EDITOR_SCHEMA = _field_schema(
    ScenarioSourceDefinition,
    label="Scenario Source Definition",
)
SCENARIO_V5_EDITOR_SCHEMA = _field_schema(
    scenario_models.ScenarioDefinition,
    label="Scenario Version 6 Definition",
)
ELEMENT_EDITOR_MODELS: dict[
    element_models.ElementKind,
    type[element_models.ElementDefinitionBase],
] = {
    element_models.ElementKind.NPC_ROLE: element_models.NpcRoleElementDefinition,
    element_models.ElementKind.OBJECT: element_models.ObjectElementDefinition,
    element_models.ElementKind.ROOM: element_models.RoomElementDefinition,
    element_models.ElementKind.BUILDING: element_models.BuildingElementDefinition,
}
ELEMENT_EDITOR_SCHEMAS: dict[
    element_models.ElementKind,
    ScenarioFieldSchema,
] = {
    kind: _field_schema(model, label=f"{_humanize(kind.value)} Definition")
    for kind, model in ELEMENT_EDITOR_MODELS.items()
}


def _field_default(model: type[BaseModel], field_name: str) -> Any:
    field_info = model.model_fields[field_name]
    if field_info.default_factory is not None:
        return field_info.get_default(
            call_default_factory=True,
            validated_data={},
        )
    if field_info.default is not PydanticUndefined:
        return field_info.default
    return PydanticUndefined


def _model_default(schema: ScenarioFieldSchema) -> dict[str, Any]:
    assert schema.model is not None
    result: dict[str, Any] = {}
    for child in schema.children:
        default = _field_default(schema.model, child.field_name)
        if default is not PydanticUndefined:
            if isinstance(default, BaseModel):
                default = default.model_dump(mode="json")
            elif isinstance(default, Enum):
                default = default.value
            result[child.field_name] = default
    return result


def node_from_value(
    schema: ScenarioFieldSchema,
    value: Any = PydanticUndefined,
) -> ScenarioEditorNode:
    node = ScenarioEditorNode(schema=schema)
    if schema.kind == "scalar":
        if value is PydanticUndefined:
            node.value = ""
            refresh_node_paths(node)
            return node
        if schema.arbitrary_json:
            node.value = json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
            )
        elif isinstance(value, Enum):
            node.value = str(value.value)
        elif isinstance(value, bool):
            node.value = "true" if value else "false"
        elif value is None or value is PydanticUndefined:
            node.value = ""
        elif isinstance(value, datetime):
            node.value = value.isoformat()
        else:
            node.value = str(value)
    elif schema.kind == "model":
        raw = _model_default(schema)
        if isinstance(value, BaseModel):
            raw.update(value.model_dump(mode="json"))
        elif isinstance(value, dict):
            raw.update(value)
        node.children = [
            node_from_value(child, raw.get(child.field_name, PydanticUndefined))
            for child in schema.children
        ]
        if schema.extensible:
            extras = {
                key: item
                for key, item in raw.items()
                if key not in {child.field_name for child in schema.children}
            }
            extras_schema = ScenarioFieldSchema(
                kind="scalar",
                label="Extra Fields JSON",
                field_name="__extras__",
                input_type="json",
                arbitrary_json=True,
            )
            node.children.append(node_from_value(extras_schema, extras))
    elif schema.kind == "optional":
        node.choice = "absent" if value is None or value is PydanticUndefined else "present"
        assert schema.item is not None
        node.items = [
            node_from_value(
                schema.item,
                value if node.choice == "present" else PydanticUndefined,
            )
        ]
    elif schema.kind == "union":
        if value is None or value is PydanticUndefined:
            node.choice = "none"
        elif isinstance(value, dict) and value.get("type") == "city":
            node.choice = "city"
        else:
            node.choice = "grid"
        for key, _label, variant_schema in schema.variants:
            node.variants[key] = (
                None
                if variant_schema is None
                else node_from_value(
                    variant_schema,
                    value if key == node.choice else PydanticUndefined,
                )
            )
    elif schema.kind == "list":
        list_values = value if isinstance(value, list) else []
        assert schema.item is not None
        node.items = [node_from_value(schema.item, item) for item in list_values]
    elif schema.kind == "mapping":
        mapping_values = value if isinstance(value, dict) else {}
        assert schema.value is not None
        for key, item in mapping_values.items():
            entry = node_from_value(schema.value, item)
            entry.key = str(key.value if isinstance(key, Enum) else key)
            node.items.append(entry)
    elif schema.kind == "components":
        component_values = value if isinstance(value, dict) else {}
        known = {child.field_name for child in schema.children[:-1]}
        for child in schema.children[:-1]:
            node.children.append(
                node_from_value(
                    child,
                    component_values.get(
                        child.field_name,
                        PydanticUndefined,
                    ),
                )
            )
        unknown_values = {key: item for key, item in component_values.items() if key not in known}
        node.children.append(node_from_value(schema.children[-1], unknown_values))
    else:
        raise TypeError(f"unsupported scenario editor node kind: {schema.kind}")
    refresh_node_paths(node)
    return node


def refresh_node_paths(node: ScenarioEditorNode, path: FieldPath = ()) -> None:
    node.path = path
    if node.schema.kind in {"model", "components"}:
        for child in node.children:
            child_path = (
                path
                if child.schema.field_name == "__extras__"
                else (*path, child.schema.field_name)
            )
            refresh_node_paths(child, child_path)
    elif node.schema.kind == "optional":
        refresh_node_paths(node.items[0], path)
    elif node.schema.kind == "union":
        for variant in node.variants.values():
            if variant is not None:
                refresh_node_paths(variant, path)
    elif node.schema.kind == "list":
        for index, item in enumerate(node.items):
            refresh_node_paths(item, (*path, index))
    elif node.schema.kind == "mapping":
        for item in node.items:
            refresh_node_paths(item, (*path, item.key))


def update_draft_from_form(draft: StructuredEditorDraft, form: Any) -> None:
    draft.resource_id = str(form.get("resource_id", draft.resource_id)).strip()
    _update_node_from_form(draft.root, form)
    refresh_node_paths(draft.root)


def _update_node_from_form(node: ScenarioEditorNode, form: Any) -> None:
    if node.schema.kind == "scalar":
        node.value = str(form.get(f"value_{node.id}", node.value))
    elif node.schema.kind in {"optional", "union"}:
        node.choice = str(form.get(f"choice_{node.id}", node.choice))
    if node.schema.kind in {"model", "components"}:
        for child in node.children:
            _update_node_from_form(child, form)
    elif node.schema.kind == "optional":
        _update_node_from_form(node.items[0], form)
    elif node.schema.kind == "union":
        for variant in node.variants.values():
            if variant is not None:
                _update_node_from_form(variant, form)
    elif node.schema.kind in {"list", "mapping"}:
        for item in node.items:
            if node.schema.kind == "mapping":
                item.key = str(form.get(f"key_{item.id}", item.key)).strip()
            _update_node_from_form(item, form)


def apply_collection_action(
    draft: StructuredEditorDraft,
    action: str,
) -> bool:
    parts = action.split(":")
    if len(parts) not in {2, 3}:
        return False
    operation, node_id = parts[:2]
    collection = find_node(draft.root, node_id)
    if collection is None or collection.schema.kind not in {"list", "mapping"}:
        return False
    if operation == "add":
        item_schema = (
            collection.schema.item if collection.schema.kind == "list" else collection.schema.value
        )
        assert item_schema is not None
        collection.items.append(node_from_value(item_schema))
        refresh_node_paths(draft.root)
        return True
    if len(parts) != 3:
        return False
    item_id = parts[2]
    index = next(
        (item_index for item_index, item in enumerate(collection.items) if item.id == item_id),
        None,
    )
    if index is None:
        return False
    if operation == "remove":
        collection.items.pop(index)
    elif operation == "up" and index > 0:
        collection.items[index - 1], collection.items[index] = (
            collection.items[index],
            collection.items[index - 1],
        )
    elif operation == "down" and index < len(collection.items) - 1:
        collection.items[index + 1], collection.items[index] = (
            collection.items[index],
            collection.items[index + 1],
        )
    else:
        return False
    refresh_node_paths(draft.root)
    return True


def find_node(
    root: ScenarioEditorNode,
    node_id: str,
) -> ScenarioEditorNode | None:
    if root.id == node_id:
        return root
    for child in root.children:
        match = find_node(child, node_id)
        if match is not None:
            return match
    for item in root.items:
        match = find_node(item, node_id)
        if match is not None:
            return match
    for variant in root.variants.values():
        if variant is not None:
            match = find_node(variant, node_id)
            if match is not None:
                return match
    return None


def find_node_by_path(
    root: ScenarioEditorNode,
    path: FieldPath,
) -> ScenarioEditorNode | None:
    return next((node for node in _all_nodes(root) if node.path == path), None)


def find_collection_membership(
    root: ScenarioEditorNode,
    node_id: str,
) -> tuple[ScenarioEditorNode, int] | None:
    for node in _all_nodes(root):
        if node.schema.kind not in {"list", "mapping"}:
            continue
        for index, item in enumerate(node.items):
            if item.id == node_id:
                return node, index
    return None


def _all_nodes(root: ScenarioEditorNode) -> list[ScenarioEditorNode]:
    nodes = [root]
    for child in root.children:
        nodes.extend(_all_nodes(child))
    for item in root.items:
        nodes.extend(_all_nodes(item))
    for variant in root.variants.values():
        if variant is not None:
            nodes.extend(_all_nodes(variant))
    return nodes


def _scalar_value(
    node: ScenarioEditorNode,
    errors: list[tuple[ScenarioEditorNode, str]],
) -> Any:
    value = node.value
    if node.schema.arbitrary_json:
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            errors.append((node, f"Must be valid JSON: {error.msg}"))
            return value
    annotation = node.schema.annotation
    if get_origin(annotation) is Literal:
        for option in get_args(annotation):
            if str(option) == value:
                return option
        return value
    if annotation is bool:
        if value == "true":
            return True
        if value == "false":
            return False
        return value
    if annotation is int:
        try:
            return int(value)
        except ValueError:
            return value
    if annotation is float:
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _encode_node(
    node: ScenarioEditorNode,
    errors: list[tuple[ScenarioEditorNode, str]],
) -> Any:
    kind = node.schema.kind
    if kind == "scalar":
        return _scalar_value(node, errors)
    if kind == "model":
        result: dict[str, Any] = {}
        for child in node.children:
            value = _encode_node(child, errors)
            if child.schema.field_name == "__extras__":
                if isinstance(value, dict):
                    duplicate = set(result).intersection(value)
                    if duplicate:
                        errors.append(
                            (
                                child,
                                f"Extra fields duplicate typed fields: {sorted(duplicate)}",
                            )
                        )
                    result.update(value)
                elif value not in ({}, None):
                    errors.append((child, "Extra fields must be a JSON object"))
            else:
                result[child.schema.field_name] = value
        return result
    if kind == "optional":
        if node.choice == "absent":
            return None
        return _encode_node(node.items[0], errors)
    if kind == "union":
        if node.choice == "none":
            return None
        variant = node.variants.get(node.choice)
        if variant is None:
            errors.append((node, "Choose a valid variant"))
            return None
        return _encode_node(variant, errors)
    if kind == "list":
        return [_encode_node(item, errors) for item in node.items]
    if kind == "mapping":
        result = {}
        keys: set[str] = set()
        for item in node.items:
            if not item.key:
                errors.append((item, "Key is required"))
                continue
            if item.key in keys:
                errors.append((item, f"Duplicate key: {item.key}"))
                continue
            keys.add(item.key)
            result[item.key] = _encode_node(item, errors)
        return result
    if kind == "components":
        result = {}
        for child in node.children[:-1]:
            if child.choice == "present":
                result[child.schema.field_name] = _encode_node(child, errors)
        unknown = _encode_node(node.children[-1], errors)
        if isinstance(unknown, dict):
            duplicate = set(result).intersection(unknown)
            if duplicate:
                errors.append(
                    (
                        node.children[-1],
                        f"Unknown components duplicate typed components: {sorted(duplicate)}",
                    )
                )
            result.update(unknown)
        return result
    raise TypeError(f"unsupported scenario editor node kind: {kind}")


def encode_draft_value(
    draft: StructuredEditorDraft,
) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
    errors: list[tuple[ScenarioEditorNode, str]] = []
    raw = _encode_node(draft.root, errors)
    if not isinstance(raw, dict):
        return {}, ((draft.root.id, "Scenario definition must be an object"),)
    return raw, tuple((node.id, message) for node, message in errors)


def encode_element_draft_value(
    draft: ElementEditorDraft,
) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
    raw, errors = encode_draft_value(draft)
    raw["id"] = draft.resource_id
    raw["schema_version"] = ELEMENT_SCHEMA_VERSION
    raw["kind"] = draft.kind.value
    return raw, errors


def validate_element_draft(
    draft: ElementEditorDraft,
) -> element_models.ScenarioElementDefinition | None:
    clear_draft_errors(draft)
    raw, decode_errors = encode_element_draft_value(draft)
    for node_id, message in decode_errors:
        node = find_node(draft.root, node_id) or draft.root
        _add_node_error(draft, node, message)
    if decode_errors:
        return None
    model = ELEMENT_EDITOR_MODELS[draft.kind]
    try:
        element = model.model_validate(raw)
    except ValidationError as error:
        for item in error.errors(include_url=False):
            location = tuple(item["loc"])
            if location == ("id",):
                draft.errors.append(
                    ScenarioEditorError(
                        message=f"id: {item['msg']}",
                        control_id="element-resource-id",
                    )
                )
                continue
            node = _node_for_path(draft.root, location)
            _add_node_error(draft, node, str(item["msg"]))
        return None
    return cast(element_models.ScenarioElementDefinition, element)


def validate_draft(
    draft: ScenarioEditorDraft,
) -> ScenarioSourceDefinition | None:
    clear_draft_errors(draft)
    decode_errors: list[tuple[ScenarioEditorNode, str]] = []
    raw = _encode_node(draft.root, decode_errors)
    for node, message in decode_errors:
        _add_node_error(draft, node, message)
    if decode_errors:
        return None
    try:
        scenario = ScenarioSourceDefinition.model_validate(raw)
    except ValidationError as error:
        for item in error.errors(include_url=False):
            location = tuple(item["loc"])
            node = _node_for_path(draft.root, location)
            _add_node_error(draft, node, str(item["msg"]))
        return None
    _validate_entity_components(draft, raw)
    if draft.errors:
        return None
    return scenario


def clear_draft_errors(draft: StructuredEditorDraft) -> None:
    for node in _all_nodes(draft.root):
        node.errors.clear()
    draft.errors.clear()


def _validate_entity_components(
    draft: ScenarioEditorDraft,
    raw: dict[str, Any],
) -> None:
    entities = raw.get("entities")
    if not isinstance(entities, list):
        return
    for entity_index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        components = entity.get("components")
        if not isinstance(components, dict):
            continue
        for component_name, model in KNOWN_ENTITY_COMPONENT_MODELS.items():
            if component_name not in components or component_name == "metadata":
                continue
            location: FieldPath = (
                "entities",
                entity_index,
                "components",
                component_name,
            )
            value = components[component_name]
            assert model is not None
            try:
                model.model_validate(value)
            except ValidationError as error:
                for item in error.errors(include_url=False):
                    field_node = _node_for_path(
                        draft.root,
                        (*location, *tuple(item["loc"])),
                    )
                    _add_node_error(draft, field_node, str(item["msg"]))


def _node_for_path(
    root: ScenarioEditorNode,
    path: FieldPath,
) -> ScenarioEditorNode:
    nodes = _all_nodes(root)
    exact = next((node for node in nodes if node.path == path), None)
    if exact is not None:
        return exact
    candidates = [
        node
        for node in nodes
        if len(node.path) <= len(path) and path[: len(node.path)] == node.path
    ]
    return max(candidates, key=lambda item: len(item.path), default=root)


def _add_node_error(
    draft: StructuredEditorDraft,
    node: ScenarioEditorNode,
    message: str,
) -> None:
    node.errors.append(message)
    path = ".".join(str(part) for part in node.path)
    rendered = f"{path}: {message}" if path else message
    draft.errors.append(
        ScenarioEditorError(
            message=rendered,
            control_id=node.control_id,
            node_id=node.id,
        )
    )


def minimal_scenario() -> ScenarioSourceDefinition:
    return ScenarioSourceDefinition(name="Untitled scenario")
