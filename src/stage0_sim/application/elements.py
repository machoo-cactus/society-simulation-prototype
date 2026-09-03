from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from stage0_sim.application.migrations.constants import (
    ELEMENT_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
)
from stage0_sim.application.scenario import (
    CalendarSettingsDefinition,
    CharacterSituationSynthesisSettingsDefinition,
    CityDefinition,
    CognitionSettingsDefinition,
    CoordinateDefinition,
    EngagementSettingsDefinition,
    EntityDefinition,
    EnvironmentalAvailabilityDefinition,
    HomeostasisSettingsDefinition,
    ItemCatalogEntryDefinition,
    MapPointDefinition,
    MemorySettingsDefinition,
    PerceptionSettingsDefinition,
    PhysicalObjectDefinition,
    PhysicalPlacementDefinition,
    SpatialMetricDefinition,
    StationActionDefinition,
    System1SettingsDefinition,
    TextContentDefinition,
    TransactionOfferDefinition,
    TransportDefinition,
    WeatherSettingsDefinition,
    WorldDefinition,
    ZoneDefinition,
)
from stage0_sim.domain.components import ActionType
from stage0_sim.domain.economy import TransactionOperation

ELEMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
LOCAL_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ElementKind(StrEnum):
    BUILDING = "building"
    ROOM = "room"
    OBJECT = "object"
    NPC_ROLE = "npc_role"


class ElementReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ElementKind
    id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        if not ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "element ID must use lowercase letters, numbers, dots, "
                "underscores, or hyphens"
            )
        return value


class ElementDefinitionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[5] = ELEMENT_SCHEMA_VERSION
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        if not ELEMENT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "element ID must use lowercase letters, numbers, dots, "
                "underscores, or hyphens"
            )
        return value


class NpcRoleElementDefinition(ElementDefinitionBase):
    kind: Literal[ElementKind.NPC_ROLE] = ElementKind.NPC_ROLE
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
    def tools_are_restricted(self) -> NpcRoleElementDefinition:
        allowed = {"serve_transaction", "say", "wait", "skip"}
        unknown = set(self.tool_allowlist) - allowed
        if unknown:
            raise ValueError(f"unknown NPC role tools: {sorted(unknown)}")
        if len(self.tool_allowlist) != len(set(self.tool_allowlist)):
            raise ValueError("NPC role tools must be unique")
        if self.recognition_range > self.vision_range:
            raise ValueError("NPC role recognition range must not exceed vision range")
        return self


class ObjectElementDefinition(ElementDefinitionBase):
    kind: Literal[ElementKind.OBJECT] = ElementKind.OBJECT
    object_type: Literal["affordance", "transaction"] | None = None
    physical: PhysicalObjectDefinition | None = None
    supported_actions: list[ActionType] | None = None
    actions: list[StationActionDefinition] | None = None
    offers: list[TransactionOfferDefinition] = Field(default_factory=list)
    holdings: dict[str, int] = Field(default_factory=dict)
    available: bool = True
    capacity: int = Field(default=1, gt=0)
    operation: TransactionOperation = TransactionOperation.AUTOMATED
    npc_role: ElementReference | None = None
    request_timeout: float = Field(default=60.0, gt=0)
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )

    @model_validator(mode="after")
    def capability_shape_is_valid(self) -> ObjectElementDefinition:
        if self.physical is None:
            raise ValueError("element schema version 4 objects require physical data")
        if self.npc_role is not None and self.npc_role.kind is not ElementKind.NPC_ROLE:
            raise ValueError("object npc_role must reference an npc_role element")
        if self.object_type == "affordance":
            if (self.supported_actions is None) == (self.actions is None):
                raise ValueError(
                    "affordance objects require exactly one action format"
                )
            if self.supported_actions == [] or self.actions == []:
                raise ValueError("affordance object actions must not be empty")
            if self.offers or self.holdings or self.npc_role is not None:
                raise ValueError(
                    "affordance objects cannot define transaction fields"
                )
            if self.operation is not TransactionOperation.AUTOMATED:
                raise ValueError("affordance objects must use AUTOMATED operation")
        elif self.object_type == "transaction":
            if not self.offers:
                raise ValueError("transaction objects require at least one offer")
            if self.supported_actions is not None or self.actions is not None:
                raise ValueError("transaction objects cannot define actions")
            if (
                self.operation is TransactionOperation.STAFFED
                and self.npc_role is None
            ):
                raise ValueError(
                    "staffed transaction objects require an npc_role reference"
                )
            if (
                self.operation is TransactionOperation.AUTOMATED
                and self.npc_role is not None
            ):
                raise ValueError(
                    "automated transaction objects cannot define an npc_role"
                )
        elif (
            self.supported_actions is not None
            or self.actions is not None
            or self.offers
            or self.holdings
            or self.npc_role is not None
            or self.operation is not TransactionOperation.AUTOMATED
        ):
            raise ValueError(
                "objects without an affordance or transaction type cannot "
                "define legacy capability fields"
            )
        if any(not item_id or quantity < 0 for item_id, quantity in self.holdings.items()):
            raise ValueError(
                "object holdings require non-empty item IDs and non-negative quantities"
            )
        return self


class ObjectPlacementDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    element: ElementReference
    position: CoordinateDefinition | None = None
    placement: PhysicalPlacementDefinition | None = None
    staff_position: CoordinateDefinition | None = None

    @field_validator("key")
    @classmethod
    def key_is_safe(cls, value: str) -> str:
        return _validate_local_key(value)

    @model_validator(mode="after")
    def reference_kind_is_valid(self) -> ObjectPlacementDefinition:
        if self.element.kind is not ElementKind.OBJECT:
            raise ValueError("object placement must reference an object element")
        if self.position is None and self.placement is None:
            raise ValueError(
                "object placement requires a legacy position or physical placement"
            )
        return self


class RoomElementDefinition(ElementDefinitionBase):
    kind: Literal[ElementKind.ROOM] = ElementKind.ROOM
    room_type: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    spatial_metric: SpatialMetricDefinition = Field(
        default_factory=SpatialMetricDefinition
    )
    blocked: list[CoordinateDefinition] = Field(default_factory=list)
    zones: list[ZoneDefinition] | None = None
    objects: list[ObjectPlacementDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def layout_is_valid(self) -> RoomElementDefinition:
        _require_unique_local_keys(self.objects, "room object")
        occupied: set[tuple[int, int]] = set()
        for coordinate in self.blocked:
            point = (coordinate.x, coordinate.y)
            if not self.contains(coordinate):
                raise ValueError(f"blocked coordinate {point} is outside the room")
            if point in occupied:
                raise ValueError(f"duplicate blocked coordinate: {point}")
            occupied.add(point)
        for placement in self.objects:
            if placement.position is not None:
                point = (placement.position.x, placement.position.y)
                if not self.contains(placement.position):
                    raise ValueError(
                        f"object {placement.key} position {point} is outside the room"
                    )
                if point in occupied:
                    raise ValueError(
                        f"object {placement.key} position {point} is blocked or occupied"
                    )
                occupied.add(point)
            if placement.staff_position is not None:
                staff_point = (
                    placement.staff_position.x,
                    placement.staff_position.y,
                )
                if not self.contains(placement.staff_position):
                    raise ValueError(
                        f"object {placement.key} staff position {staff_point} "
                        "is outside the room"
                    )
                if staff_point in {
                    (coordinate.x, coordinate.y) for coordinate in self.blocked
                }:
                    raise ValueError(
                        f"object {placement.key} staff position {staff_point} "
                        "is blocked"
                    )
        if self.zones is not None:
            if not self.zones:
                raise ValueError("room zones must not be empty")
            zone_ids = [zone.id for zone in self.zones]
            if len(zone_ids) != len(set(zone_ids)):
                raise ValueError("room zone IDs must be unique")
            for zone in self.zones:
                coordinates = (
                    zone.bounds.tiles()
                    if zone.bounds is not None
                    else frozenset(
                        coordinate.to_domain()
                        for coordinate in zone.tiles or []
                    )
                )
                if any(
                    coordinate.x < 0
                    or coordinate.y < 0
                    or coordinate.x >= self.width
                    or coordinate.y >= self.height
                    for coordinate in coordinates
                ):
                    raise ValueError(
                        f"zone {zone.id} extends outside the room"
                    )
        return self

    def contains(self, coordinate: CoordinateDefinition) -> bool:
        return 0 <= coordinate.x < self.width and 0 <= coordinate.y < self.height


class RoomPlacementDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    element: ElementReference
    offset: CoordinateDefinition = Field(
        default_factory=lambda: CoordinateDefinition(x=0, y=0)
    )

    @field_validator("key")
    @classmethod
    def key_is_safe(cls, value: str) -> str:
        return _validate_local_key(value)

    @model_validator(mode="after")
    def reference_kind_is_valid(self) -> RoomPlacementDefinition:
        if self.element.kind is not ElementKind.ROOM:
            raise ValueError("room placement must reference a room element")
        return self


class BuildingPortalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    from_room_key: str = Field(min_length=1)
    from_coordinate: CoordinateDefinition
    to_room_key: str = Field(min_length=1)
    to_coordinate: CoordinateDefinition
    bidirectional: bool = True
    available: bool = True
    door_object_id: str | None = Field(default=None, min_length=1)

    @field_validator("key", "from_room_key", "to_room_key")
    @classmethod
    def keys_are_safe(cls, value: str) -> str:
        return _validate_local_key(value)


class BuildingEntranceElementDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    room_key: str = Field(min_length=1)
    local_coordinate: CoordinateDefinition
    door_object_id: str | None = Field(default=None, min_length=1)

    @field_validator("key", "room_key")
    @classmethod
    def keys_are_safe(cls, value: str) -> str:
        return _validate_local_key(value)


class BuildingElementDefinition(ElementDefinitionBase):
    kind: Literal[ElementKind.BUILDING] = ElementKind.BUILDING
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )
    rooms: list[RoomPlacementDefinition] = Field(min_length=1)
    portals: list[BuildingPortalDefinition] = Field(default_factory=list)
    entrances: list[BuildingEntranceElementDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def structure_is_valid(self) -> BuildingElementDefinition:
        _require_unique_local_keys(self.rooms, "building room")
        _require_unique_local_keys(self.portals, "building portal")
        _require_unique_local_keys(self.entrances, "building entrance")
        room_keys = {room.key for room in self.rooms}
        for portal in self.portals:
            if portal.from_room_key not in room_keys:
                raise ValueError(
                    f"portal {portal.key} references unknown room "
                    f"{portal.from_room_key}"
                )
            if portal.to_room_key not in room_keys:
                raise ValueError(
                    f"portal {portal.key} references unknown room "
                    f"{portal.to_room_key}"
                )
        for entrance in self.entrances:
            if entrance.room_key not in room_keys:
                raise ValueError(
                    f"entrance {entrance.key} references unknown room "
                    f"{entrance.room_key}"
                )
        return self


ScenarioElementDefinition = Annotated[
    BuildingElementDefinition
    | RoomElementDefinition
    | ObjectElementDefinition
    | NpcRoleElementDefinition,
    Field(discriminator="kind"),
]
SCENARIO_ELEMENT_ADAPTER: TypeAdapter[ScenarioElementDefinition] = TypeAdapter(
    ScenarioElementDefinition
)


class ObjectOverrideDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    available: bool | None = None
    environment: EnvironmentalAvailabilityDefinition | None = None
    holdings: dict[str, int] | None = None
    offers: list[TransactionOfferDefinition] | None = None
    npc_role: ElementReference | None = None

    @model_validator(mode="after")
    def npc_kind_is_valid(self) -> ObjectOverrideDefinition:
        if self.npc_role is not None and self.npc_role.kind is not ElementKind.NPC_ROLE:
            raise ValueError("object override npc_role must reference npc_role")
        if self.holdings is not None and any(
            not item_id or quantity < 0
            for item_id, quantity in self.holdings.items()
        ):
            raise ValueError(
                "object override holdings require non-empty IDs and "
                "non-negative quantities"
            )
        return self


class RoomOverrideDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    room_type: str | None = Field(default=None, min_length=1)
    object_overrides: dict[str, ObjectOverrideDefinition] = Field(
        default_factory=dict
    )
    disabled_object_keys: set[str] = Field(default_factory=set)

    @field_validator("object_overrides", "disabled_object_keys")
    @classmethod
    def object_keys_are_safe(
        cls,
        value: dict[str, ObjectOverrideDefinition] | set[str],
    ) -> dict[str, ObjectOverrideDefinition] | set[str]:
        keys = value.keys() if isinstance(value, Mapping) else value
        for key in keys:
            _validate_local_key(key)
        return value


class BuildingOverrideDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    available: bool | None = None
    environment: EnvironmentalAvailabilityDefinition | None = None
    room_overrides: dict[str, RoomOverrideDefinition] = Field(
        default_factory=dict
    )
    disabled_room_keys: set[str] = Field(default_factory=set)

    @field_validator("room_overrides", "disabled_room_keys")
    @classmethod
    def room_keys_are_safe(
        cls,
        value: dict[str, RoomOverrideDefinition] | set[str],
    ) -> dict[str, RoomOverrideDefinition] | set[str]:
        keys = value.keys() if isinstance(value, Mapping) else value
        for key in keys:
            _validate_local_key(key)
        return value


class BuildingInstanceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    element: ElementReference
    city_position: MapPointDefinition
    entrance_node_ids: dict[str, str]
    overrides: BuildingOverrideDefinition = Field(
        default_factory=BuildingOverrideDefinition
    )

    @model_validator(mode="after")
    def building_reference_is_valid(self) -> BuildingInstanceDefinition:
        if self.element.kind is not ElementKind.BUILDING:
            raise ValueError("building instance must reference a building element")
        for key, node_id in self.entrance_node_ids.items():
            _validate_local_key(key)
            if not node_id:
                raise ValueError("building entrance node IDs must not be empty")
        return self


class OutdoorPlaceSourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    city_position: MapPointDefinition
    network_node_id: str = Field(min_length=1)
    available: bool = True
    environment: EnvironmentalAvailabilityDefinition = Field(
        default_factory=EnvironmentalAvailabilityDefinition
    )


class CityZoneSourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    center: MapPointDefinition
    buildings: list[BuildingInstanceDefinition] = Field(default_factory=list)
    outdoor_places: list[OutdoorPlaceSourceDefinition] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def child_ids_are_unique(self) -> CityZoneSourceDefinition:
        child_ids = [
            *(building.id for building in self.buildings),
            *(place.id for place in self.outdoor_places),
        ]
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("city-zone child IDs must be unique")
        return self


class CityWorldSourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["city"]
    city: CityDefinition
    city_zones: list[CityZoneSourceDefinition] = Field(min_length=1)
    transport: TransportDefinition
    building_order: list[str] = Field(default_factory=list)
    outdoor_place_order: list[str] = Field(default_factory=list)
    npc_role_order: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def hierarchy_ids_are_unique(self) -> CityWorldSourceDefinition:
        ids = [
            self.city.id,
            *(zone.id for zone in self.city_zones),
            *(
                building.id
                for zone in self.city_zones
                for building in zone.buildings
            ),
            *(
                place.id
                for zone in self.city_zones
                for place in zone.outdoor_places
            ),
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("city hierarchy IDs must be globally unique")
        building_ids = [
            building.id
            for zone in self.city_zones
            for building in zone.buildings
        ]
        outdoor_place_ids = [
            place.id
            for zone in self.city_zones
            for place in zone.outdoor_places
        ]
        _validate_optional_order(
            self.building_order,
            building_ids,
            "building",
        )
        _validate_optional_order(
            self.outdoor_place_order,
            outdoor_place_ids,
            "outdoor place",
        )
        if len(self.npc_role_order) != len(set(self.npc_role_order)):
            raise ValueError("NPC role order IDs must be unique")
        return self


class ScenarioSourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[9] = SCENARIO_SCHEMA_VERSION
    name: str = Field(min_length=1)
    seed: int = 0
    dt: float = Field(default=1.0, gt=0)
    speed: float = Field(default=1.0, gt=0)
    run_id: str | None = Field(default=None, min_length=1)
    items: list[ItemCatalogEntryDefinition] = Field(default_factory=list)
    calendar: CalendarSettingsDefinition | None = None
    weather: WeatherSettingsDefinition | None = None
    world: WorldDefinition | CityWorldSourceDefinition | None = None
    homeostasis: HomeostasisSettingsDefinition = Field(
        default_factory=HomeostasisSettingsDefinition
    )
    system1: System1SettingsDefinition = Field(
        default_factory=System1SettingsDefinition
    )
    memory: MemorySettingsDefinition = Field(
        default_factory=MemorySettingsDefinition
    )
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


def element_content_hash(element: ScenarioElementDefinition) -> str:
    payload = element.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_instance_id(parent_id: str, local_key: str) -> str:
    if not parent_id:
        raise ValueError("parent instance ID must not be empty")
    return f"{parent_id}.{_validate_local_key(local_key)}"


def _validate_local_key(value: str) -> str:
    if not LOCAL_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            "local key must use lowercase letters, numbers, underscores, "
            "or hyphens"
        )
    return value


class _HasLocalKey(Protocol):
    key: str


def _require_unique_local_keys(
    values: Sequence[_HasLocalKey],
    label: str,
) -> None:
    keys = [value.key for value in values]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} keys must be unique")


def _validate_optional_order(
    order: list[str],
    ids: list[str],
    label: str,
) -> None:
    if order and (len(order) != len(ids) or set(order) != set(ids)):
        raise ValueError(
            f"{label} order must contain every {label} ID exactly once"
        )
