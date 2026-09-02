from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from stage0_sim.application.element_library import (
    ElementLibrary,
    ElementLibraryError,
)
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    BuildingInstanceDefinition,
    BuildingOverrideDefinition,
    CityWorldSourceDefinition,
    ElementKind,
    ElementReference,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    ObjectOverrideDefinition,
    RoomElementDefinition,
    RoomOverrideDefinition,
    ScenarioElementDefinition,
    ScenarioSourceDefinition,
    derive_instance_id,
    element_content_hash,
)
from stage0_sim.application.migrations.constants import SCENARIO_SCHEMA_VERSION
from stage0_sim.application.scenario import (
    BuildingDefinition,
    BuildingEntranceDefinition,
    BuildingPortalRuntimeDefinition,
    CityWorldDefinition,
    CoordinateDefinition,
    DistrictDefinition,
    NpcRoleDefinition,
    OutdoorPlaceDefinition,
    PhysicalParentRelationDefinition,
    PhysicalPlacementDefinition,
    RoomDefinition,
    ScenarioDefinition,
    StationDefinition,
    TransactionPointDefinition,
    TransactionStaffingDefinition,
    WorldDefinition,
    WorldObjectDefinition,
)
from stage0_sim.domain.events import JsonValue


class ScenarioResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedElement:
    id: str
    kind: ElementKind
    content_hash: str
    definition: ScenarioElementDefinition

    def to_payload(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            {
            "id": self.id,
            "kind": self.kind.value,
            "content_hash": self.content_hash,
            "definition": self.definition.model_dump(mode="json"),
            },
        )


@dataclass(frozen=True, slots=True)
class ResolvedScenario:
    source: ScenarioSourceDefinition
    scenario: ScenarioDefinition
    elements: dict[str, ResolvedElement]

    def provenance_payload(self) -> dict[str, JsonValue]:
        return {
            element_id: element.to_payload()
            for element_id, element in sorted(self.elements.items())
        }


def resolve_scenario(
    source: ScenarioSourceDefinition,
    library: ElementLibrary,
) -> ResolvedScenario:
    resolver = _ScenarioResolver(library)
    if not isinstance(source.world, CityWorldSourceDefinition):
        scenario = ScenarioDefinition.model_validate(
            source.model_dump(mode="json")
        )
        return ResolvedScenario(
            source=source,
            scenario=scenario,
            elements={},
        )
    world, npc_roles = resolver.resolve_city(source.world)
    payload = source.model_dump(mode="json", exclude={"world"})
    payload["world"] = world.model_dump(mode="json")
    ordered_roles = _ordered_values(
        npc_roles,
        source.world.npc_role_order,
        "NPC role",
    )
    payload["npc_roles"] = [
        role.model_dump(mode="json")
        for role in ordered_roles
    ]
    try:
        scenario = ScenarioDefinition.model_validate(payload)
    except ValidationError as error:
        raise ScenarioResolutionError(
            f"resolved scenario validation failed: {error}"
        ) from error
    return ResolvedScenario(
        source=source,
        scenario=scenario,
        elements=dict(sorted(resolver.resolved.items())),
    )


def load_and_resolve_scenario(
    path: Path,
    library: ElementLibrary,
) -> ResolvedScenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScenarioResolutionError(
            f"could not read scenario {path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ScenarioResolutionError(
            f"scenario is not valid JSON: {error}"
        ) from error
    if not isinstance(raw, dict) or raw.get(
        "schema_version"
    ) != SCENARIO_SCHEMA_VERSION:
        raise ScenarioResolutionError(
            f"scenario schema version {SCENARIO_SCHEMA_VERSION} is required; "
            "run 'stage0-sim migrate content'"
        )
    try:
        source = ScenarioSourceDefinition.model_validate(raw)
    except ValidationError as error:
        raise ScenarioResolutionError(
            f"scenario source validation failed: {error}"
        ) from error
    return resolve_scenario(source, library)


class _ScenarioResolver:
    def __init__(self, library: ElementLibrary) -> None:
        self.library = library
        self.resolved: dict[str, ResolvedElement] = {}
        self._resolving: list[str] = []

    def resolve_city(
        self,
        source: CityWorldSourceDefinition,
    ) -> tuple[CityWorldDefinition, dict[str, NpcRoleDefinition]]:
        districts: list[DistrictDefinition] = []
        buildings: list[BuildingDefinition] = []
        rooms: list[RoomDefinition] = []
        portals: list[BuildingPortalRuntimeDefinition] = []
        objects: list[WorldObjectDefinition] = []
        outdoor_places: list[OutdoorPlaceDefinition] = []
        npc_roles: dict[str, NpcRoleDefinition] = {}
        for zone in source.city_zones:
            districts.append(
                DistrictDefinition(
                    id=zone.id,
                    name=zone.name,
                    center=zone.center,
                )
            )
            for instance in zone.buildings:
                (
                    building,
                    resolved_rooms,
                    resolved_portals,
                    resolved_objects,
                    roles,
                ) = self._resolve_building(
                    zone.id,
                    instance,
                )
                buildings.append(building)
                rooms.extend(resolved_rooms)
                portals.extend(resolved_portals)
                objects.extend(resolved_objects)
                for role in roles:
                    existing = npc_roles.get(role.id)
                    if existing is not None and existing != role:
                        raise ScenarioResolutionError(
                            f"NPC role {role.id} resolved inconsistently"
                        )
                    npc_roles[role.id] = role
            outdoor_places.extend(
                OutdoorPlaceDefinition(
                    id=place.id,
                    name=place.name,
                    district_id=zone.id,
                    city_position=place.city_position,
                    network_node_id=place.network_node_id,
                    available=place.available,
                    environment=place.environment,
                )
                for place in zone.outdoor_places
            )
        try:
            ordered_buildings = _ordered_values(
                {building.id: building for building in buildings},
                source.building_order,
                "building",
            )
            ordered_outdoor_places = _ordered_values(
                {place.id: place for place in outdoor_places},
                source.outdoor_place_order,
                "outdoor place",
            )
            building_order = {
                building.id: index
                for index, building in enumerate(ordered_buildings)
            }
            rooms.sort(
                key=lambda item: (
                    building_order[item.building_id],
                    item.id,
                )
            )
            portals.sort(
                key=lambda item: (
                    building_order[item.building_id],
                    item.id,
                )
            )
            objects.sort(
                key=lambda item: (
                    building_order[item.building_id],
                    item.room_id,
                    item.id,
                )
            )
            world = CityWorldDefinition(
                type="city",
                city=source.city,
                districts=districts,
                buildings=ordered_buildings,
                rooms=rooms,
                portals=portals,
                objects=objects,
                outdoor_places=ordered_outdoor_places,
                transport=source.transport,
            )
        except ValidationError as error:
            raise ScenarioResolutionError(
                f"resolved city validation failed: {error}"
            ) from error
        return world, npc_roles

    def _resolve_building(
        self,
        city_zone_id: str,
        instance: BuildingInstanceDefinition,
    ) -> tuple[
        BuildingDefinition,
        tuple[RoomDefinition, ...],
        tuple[BuildingPortalRuntimeDefinition, ...],
        tuple[WorldObjectDefinition, ...],
        tuple[NpcRoleDefinition, ...],
    ]:
        element = self._load(
            instance.element,
            ElementKind.BUILDING,
        )
        building = cast(BuildingElementDefinition, element)
        self._validate_building_overrides(building, instance.overrides)
        disabled_rooms = instance.overrides.disabled_room_keys
        required_room_keys = {
            entrance.room_key for entrance in building.entrances
        } | {
            key
            for portal in building.portals
            for key in (portal.from_room_key, portal.to_room_key)
        }
        invalid_disabled = sorted(disabled_rooms & required_room_keys)
        if invalid_disabled:
            raise ScenarioResolutionError(
                f"building {instance.id} cannot disable rooms used by "
                f"entrances or portals: {invalid_disabled}"
            )

        rooms: dict[str, RoomElementDefinition] = {}
        room_offsets: dict[str, CoordinateDefinition] = {}
        room_overrides: dict[str, RoomOverrideDefinition] = {}
        for placement in building.rooms:
            if placement.key in disabled_rooms:
                continue
            room = cast(
                RoomElementDefinition,
                self._load(placement.element, ElementKind.ROOM),
            )
            override = instance.overrides.room_overrides.get(
                placement.key,
                RoomOverrideDefinition(),
            )
            self._validate_room_overrides(room, override, instance.id, placement.key)
            rooms[placement.key] = room
            room_offsets[placement.key] = placement.offset
            room_overrides[placement.key] = override

        occupied_tiles: set[tuple[int, int]] = set()
        resolved_rooms: list[RoomDefinition] = []
        resolved_objects: list[WorldObjectDefinition] = []
        roles: dict[str, NpcRoleDefinition] = {}
        for placement in building.rooms:
            room_key = placement.key
            if room_key not in rooms:
                continue
            room = rooms[room_key]
            offset = room_offsets[room_key]
            override = room_overrides[room_key]
            room_id = derive_instance_id(instance.id, room_key)
            room_tiles = {
                (offset.x + x, offset.y + y)
                for y in range(room.height)
                for x in range(room.width)
            }
            overlap = occupied_tiles & room_tiles
            if overlap:
                raise ScenarioResolutionError(
                    f"building {instance.id} rooms overlap at "
                    f"{sorted(overlap)[0]}"
                )
            occupied_tiles.update(room_tiles)
            stations: list[StationDefinition] = []
            transaction_points: list[TransactionPointDefinition] = []
            disabled_objects = override.disabled_object_keys
            object_ids_by_key = {
                object_placement.key: (
                    object_placement.id
                    or derive_instance_id(room_id, object_placement.key)
                )
                for object_placement in room.objects
                if object_placement.key not in disabled_objects
            }
            for object_placement in room.objects:
                if object_placement.key in disabled_objects:
                    continue
                object_element = cast(
                    ObjectElementDefinition,
                    self._load(
                        object_placement.element,
                        ElementKind.OBJECT,
                    ),
                )
                object_override = override.object_overrides.get(
                    object_placement.key,
                    ObjectOverrideDefinition(),
                )
                object_id = derive_instance_id(
                    room_id,
                    object_placement.key,
                )
                if object_placement.id is not None:
                    object_id = object_placement.id
                position = object_placement.position
                physical = object_element.physical
                physical_placement = object_placement.placement
                if (physical is None) != (physical_placement is None):
                    raise ScenarioResolutionError(
                        f"physical object {object_id} requires matching "
                        "element physical data and room placement"
                    )
                resolved_placement = None
                if physical is not None and physical_placement is not None:
                    parent = physical_placement.parent_relation
                    parent_id = parent.parent_id
                    if parent_id is None:
                        if parent.kind.value != "ON_FLOOR":
                            raise ScenarioResolutionError(
                                f"physical object {object_id} relation "
                                f"{parent.kind.value} requires parent_id"
                            )
                        parent_id = room_id
                    else:
                        parent_id = object_ids_by_key.get(parent_id, parent_id)
                    if parent_id == object_id:
                        raise ScenarioResolutionError(
                            f"physical object {object_id} cannot parent itself"
                        )
                    resolved_placement = PhysicalPlacementDefinition(
                        anchor=physical_placement.anchor,
                        orientation=physical_placement.orientation,
                        parent_relation=PhysicalParentRelationDefinition(
                            kind=parent.kind,
                            parent_id=parent_id,
                            slot_id=parent.slot_id,
                        ),
                    )
                    footprint_cells = physical.footprint.to_domain().translated_cells(
                        physical_placement.anchor.to_domain(),
                        physical_placement.orientation,
                    )
                    width = room.spatial_metric.microcells_per_legacy_cell * room.width
                    height = room.spatial_metric.microcells_per_legacy_cell * room.height
                    if any(
                        cell.x < 0
                        or cell.y < 0
                        or cell.x >= width
                        or cell.y >= height
                        for cell in footprint_cells
                    ):
                        raise ScenarioResolutionError(
                            f"physical object {object_id} footprint is outside room "
                            f"{room_id}"
                        )
                if position is None:
                    if physical_placement is None:
                        raise ScenarioResolutionError(
                            f"object {object_id} has no resolved position"
                        )
                    scale = room.spatial_metric.microcells_per_legacy_cell
                    position = CoordinateDefinition(
                        x=physical_placement.anchor.x // scale,
                        y=physical_placement.anchor.y // scale,
                    )
                if object_element.object_type == "affordance":
                    station = StationDefinition(
                        id=object_id,
                        name=object_override.name or object_element.name,
                        position=position,
                        supported_actions=object_element.supported_actions,
                        actions=object_element.actions,
                        available=(
                            object_override.available
                            if object_override.available is not None
                            else object_element.available
                        ),
                        capacity=object_element.capacity,
                        environment=(
                            object_override.environment
                            or object_element.environment
                        ),
                    )
                    stations.append(station)
                    resolved_objects.append(
                        WorldObjectDefinition(
                            id=object_id,
                            definition_id=object_element.id,
                            name=station.name,
                            object_kind="affordance",
                            building_id=instance.id,
                            room_id=room_id,
                            position=position,
                            physical=physical,
                            placement=resolved_placement,
                        )
                    )
                    continue
                if object_element.object_type is None:
                    resolved_objects.append(
                        WorldObjectDefinition(
                            id=object_id,
                            definition_id=object_element.id,
                            name=object_override.name or object_element.name,
                            object_kind="physical",
                            building_id=instance.id,
                            room_id=room_id,
                            position=position,
                            physical=physical,
                            placement=resolved_placement,
                        )
                    )
                    continue
                role_reference = (
                    object_override.npc_role or object_element.npc_role
                )
                staffing = None
                if role_reference is not None:
                    role_element = cast(
                        NpcRoleElementDefinition,
                        self._load(role_reference, ElementKind.NPC_ROLE),
                    )
                    role = NpcRoleDefinition(
                        id=role_element.id,
                        name=role_element.name,
                        briefing=role_element.briefing,
                        tool_allowlist=role_element.tool_allowlist,
                        vision_range=role_element.vision_range,
                        recognition_range=role_element.recognition_range,
                        hearing_range=role_element.hearing_range,
                        smell_range=role_element.smell_range,
                    )
                    roles[role.id] = role
                    if object_placement.staff_position is None:
                        raise ScenarioResolutionError(
                            f"staffed object {object_id} requires a "
                            "staff_position in its room placement"
                        )
                    staffing = TransactionStaffingDefinition(
                        role_id=role.id,
                        staff_position=object_placement.staff_position,
                        request_timeout=object_element.request_timeout,
                    )
                point = TransactionPointDefinition(
                    id=object_id,
                    name=object_override.name or object_element.name,
                    position=position,
                    offers=object_override.offers or object_element.offers,
                    holdings=(
                        object_override.holdings
                        if object_override.holdings is not None
                        else object_element.holdings
                    ),
                    available=(
                        object_override.available
                        if object_override.available is not None
                        else object_element.available
                    ),
                    capacity=object_element.capacity,
                    operation=object_element.operation,
                    staffing=staffing,
                    environment=(
                        object_override.environment
                        or object_element.environment
                    ),
                )
                transaction_points.append(point)
                resolved_objects.append(
                    WorldObjectDefinition(
                        id=object_id,
                        definition_id=object_element.id,
                        name=point.name,
                        object_kind="transaction",
                        building_id=instance.id,
                        room_id=room_id,
                        position=position,
                        physical=physical,
                        placement=resolved_placement,
                    )
                )
            resolved_rooms.append(
                RoomDefinition(
                    id=room_id,
                    key=room_key,
                    name=override.name or room.name,
                    type=override.room_type or room.room_type,
                    building_id=instance.id,
                    offset=offset,
                    world=WorldDefinition(
                        width=room.width,
                        height=room.height,
                        spatial_metric=room.spatial_metric,
                        blocked=list(room.blocked),
                        zones=list(room.zones or []),
                        stations=stations,
                        transaction_points=transaction_points,
                    ),
                )
            )
        entrance_keys = {entrance.key for entrance in building.entrances}
        supplied_keys = set(instance.entrance_node_ids)
        if supplied_keys != entrance_keys:
            raise ScenarioResolutionError(
                f"building {instance.id} entrance node keys must be "
                f"{sorted(entrance_keys)}, got {sorted(supplied_keys)}"
            )
        entrances = []
        for entrance in building.entrances:
            room = rooms[entrance.room_key]
            if not room.contains(entrance.local_coordinate):
                raise ScenarioResolutionError(
                    f"building element {building.id} entrance {entrance.key} "
                    f"is outside room {entrance.room_key}"
                )
            entrances.append(
                BuildingEntranceDefinition(
                    id=(
                        entrance.id
                        or derive_instance_id(instance.id, entrance.key)
                    ),
                    room_id=derive_instance_id(
                        instance.id, entrance.room_key
                    ),
                    local_coordinate=entrance.local_coordinate,
                    neighborhood_node_id=instance.entrance_node_ids[
                        entrance.key
                    ],
                    door_object_id=self._resolve_door_object_id(
                        entrance.door_object_id,
                        resolved_objects,
                        f"entrance {entrance.key}",
                    ),
                )
            )
        resolved_portals: list[BuildingPortalRuntimeDefinition] = []
        for portal in building.portals:
            from_room = rooms[portal.from_room_key]
            to_room = rooms[portal.to_room_key]
            if not from_room.contains(portal.from_coordinate):
                raise ScenarioResolutionError(
                    f"building element {building.id} portal {portal.key} "
                    f"from-coordinate is outside room {portal.from_room_key}"
                )
            if not to_room.contains(portal.to_coordinate):
                raise ScenarioResolutionError(
                    f"building element {building.id} portal {portal.key} "
                    f"to-coordinate is outside room {portal.to_room_key}"
                )
            if portal.from_coordinate in from_room.blocked:
                raise ScenarioResolutionError(
                    f"building element {building.id} portal {portal.key} "
                    "from-coordinate is blocked"
                )
            if portal.to_coordinate in to_room.blocked:
                raise ScenarioResolutionError(
                    f"building element {building.id} portal {portal.key} "
                    "to-coordinate is blocked"
                )
            resolved_portals.append(
                BuildingPortalRuntimeDefinition(
                    id=derive_instance_id(instance.id, portal.key),
                    building_id=instance.id,
                    from_room_id=derive_instance_id(
                        instance.id, portal.from_room_key
                    ),
                    from_coordinate=portal.from_coordinate,
                    to_room_id=derive_instance_id(
                        instance.id, portal.to_room_key
                    ),
                    to_coordinate=portal.to_coordinate,
                    bidirectional=portal.bidirectional,
                    available=portal.available,
                    door_object_id=self._resolve_door_object_id(
                        portal.door_object_id,
                        resolved_objects,
                        f"portal {portal.key}",
                    ),
                )
            )
        resolved_name = instance.overrides.name or building.name
        resolved_available = (
            instance.overrides.available
            if instance.overrides.available is not None
            else building.available
        )
        resolved_environment = (
            instance.overrides.environment or building.environment
        )
        return (
            BuildingDefinition(
                id=instance.id,
                name=resolved_name,
                district_id=city_zone_id,
                city_position=instance.city_position,
                room_ids=[room.id for room in resolved_rooms],
                entrances=entrances,
                available=resolved_available,
                environment=resolved_environment,
            ),
            tuple(resolved_rooms),
            tuple(resolved_portals),
            tuple(resolved_objects),
            tuple(sorted(roles.values(), key=lambda item: item.id)),
        )

    def _load(
        self,
        reference: ElementReference,
        expected_kind: ElementKind,
    ) -> ScenarioElementDefinition:
        if reference.kind is not expected_kind:
            raise ScenarioResolutionError(
                f"reference {reference.id} has declared kind "
                f"{reference.kind.value}, expected {expected_kind.value}"
            )
        existing = self.resolved.get(reference.id)
        if existing is not None:
            if existing.kind is not expected_kind:
                raise ScenarioResolutionError(
                    f"element {reference.id} was resolved with conflicting kinds"
                )
            if existing.content_hash != reference.content_hash:
                raise ScenarioResolutionError(
                    f"element {reference.id} is referenced with conflicting hashes"
                )
            return existing.definition
        if reference.id in self._resolving:
            cycle = " -> ".join([*self._resolving, reference.id])
            raise ScenarioResolutionError(
                f"element dependency cycle detected: {cycle}"
            )
        self._resolving.append(reference.id)
        try:
            try:
                element = self.library.get(reference.id, expected_kind)
            except ElementLibraryError as error:
                raise ScenarioResolutionError(str(error)) from error
            actual_hash = element_content_hash(element)
            if actual_hash != reference.content_hash:
                raise ScenarioResolutionError(
                    f"element {reference.id} content hash changed: "
                    f"expected {reference.content_hash}, got {actual_hash}"
                )
            self.resolved[reference.id] = ResolvedElement(
                id=reference.id,
                kind=expected_kind,
                content_hash=actual_hash,
                definition=element,
            )
            return element
        finally:
            self._resolving.pop()

    @staticmethod
    def _resolve_door_object_id(
        requested_id: str | None,
        objects: list[WorldObjectDefinition],
        context: str,
    ) -> str | None:
        if requested_id is None:
            return None
        exact = [item.id for item in objects if item.id == requested_id]
        if exact:
            return exact[0]
        matches = [
            item.id
            for item in objects
            if item.definition_id == requested_id
        ]
        if len(matches) != 1:
            raise ScenarioResolutionError(
                f"{context} door object {requested_id} must resolve uniquely"
            )
        return matches[0]

    @staticmethod
    def _validate_building_overrides(
        building: BuildingElementDefinition,
        overrides: BuildingOverrideDefinition,
    ) -> None:
        room_keys = {room.key for room in building.rooms}
        unknown = (
            set(overrides.room_overrides)
            | overrides.disabled_room_keys
        ) - room_keys
        if unknown:
            raise ScenarioResolutionError(
                f"building override references unknown room keys: "
                f"{sorted(unknown)}"
            )

    @staticmethod
    def _validate_room_overrides(
        room: RoomElementDefinition,
        overrides: RoomOverrideDefinition,
        building_id: str,
        room_key: str,
    ) -> None:
        object_keys = {item.key for item in room.objects}
        unknown = (
            set(overrides.object_overrides)
            | overrides.disabled_object_keys
        ) - object_keys
        if unknown:
            raise ScenarioResolutionError(
                f"building {building_id} room {room_key} override references "
                f"unknown object keys: {sorted(unknown)}"
            )


def _ordered_values[T](
    values: dict[str, T],
    order: list[str],
    label: str,
) -> list[T]:
    if not order:
        return list(values.values())
    if len(order) != len(values) or set(order) != set(values):
        raise ScenarioResolutionError(
            f"{label} order does not match resolved {label} IDs"
        )
    return [values[item_id] for item_id in order]
