from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from stage0_sim.api.operator_sessions import OperatorSession
from stage0_sim.api.scenario_forms import (
    ScenarioEditorDraft,
    ScenarioEditorNode,
    encode_draft_value,
    find_node,
    find_node_by_path,
)
from stage0_sim.api.ui import (
    _camera_room,
    _city_view,
    _grid_view,
)
from stage0_sim.application.element_library import ElementLibrary
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    CityWorldSourceDefinition,
    ElementKind,
    ScenarioSourceDefinition,
)
from stage0_sim.application.scenario import CityWorldDefinition
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)


@dataclass(frozen=True, slots=True)
class EditorWorldItem:
    node_id: str
    kind: str
    label: str
    group: str
    scope_node_id: str = ""
    unplaced: bool = False


@dataclass(frozen=True, slots=True)
class EditorWorldPresentation:
    world_node: ScenarioEditorNode
    selected_node: ScenarioEditorNode | None
    scope_node: ScenarioEditorNode | None
    view: dict[str, Any] | None
    groups: tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...]
    breadcrumbs: tuple[tuple[str, str], ...]
    decode_issues: tuple[tuple[str, str], ...]


def build_editor_world(
    draft: ScenarioEditorDraft,
    element_library: ElementLibrary | None = None,
) -> EditorWorldPresentation:
    world_node = _required_node(draft, ("world",))
    raw, decode_issues = encode_draft_value(draft)
    selected = find_node(draft.root, draft.view.selected_node_id)
    scope = find_node(draft.root, draft.view.scope_node_id)
    session = OperatorSession(
        zoom=draft.view.zoom,
        camera_x=draft.view.camera_x,
        camera_y=draft.view.camera_y,
    )
    world = raw.get("world")
    entities = raw.get("entities")
    entity_nodes = _list_items(draft, ("entities",))
    entity_values = entities if isinstance(entities, list) else []

    if not isinstance(world, dict):
        return EditorWorldPresentation(
            world_node=world_node,
            selected_node=selected,
            scope_node=None,
            view=None,
            groups=_groups(
                (
                    "entities",
                    "Entities",
                    _required_node(draft, ("entities",)).id,
                    _entity_items(entity_nodes, entity_values, set()),
                ),
            ),
            breadcrumbs=(),
            decode_issues=decode_issues,
        )

    if world.get("type") == "city":
        hierarchy = _source_city_hierarchy(draft, world)
        scoped_building = (
            hierarchy["building_by_node"].get(scope.id)
            if scope is not None
            else None
        )
        if scoped_building is not None and element_library is not None:
            preview = _resolved_building_preview(
                draft,
                raw,
                scoped_building,
                entity_nodes,
                entity_values,
                session,
                element_library,
            )
            if preview is not None:
                view, groups = preview
                zone_node, zone_value, building_node, building_value = scoped_building
                return EditorWorldPresentation(
                    world_node=world_node,
                    selected_node=selected,
                    scope_node=building_node,
                    view=view,
                    groups=groups,
                    breadcrumbs=(
                        ("", str(world.get("city", {}).get("name", "City"))),
                        (zone_node.id, _record_label(zone_value, "City zone")),
                        (
                            building_node.id,
                            _building_instance_label(
                                building_value,
                                element_library,
                            ),
                        ),
                    ),
                    decode_issues=decode_issues,
                )
        view, items, placed = _city_presentation(
            draft,
            world,
            entity_nodes,
            entity_values,
            session,
            element_library,
        )
        breadcrumbs = [
            ("", str(world.get("city", {}).get("name", "City")))
        ]
        selected_hierarchy = (
            hierarchy["building_by_node"].get(selected.id)
            if selected is not None
            else None
        )
        if selected_hierarchy is not None:
            zone_node, zone_value, building_node, building_value = (
                selected_hierarchy
            )
            breadcrumbs.extend(
                [
                    (zone_node.id, _record_label(zone_value, "City zone")),
                    (
                        building_node.id,
                        _building_instance_label(
                            building_value,
                            element_library,
                        ),
                    ),
                ]
            )
        elif selected is not None and selected.id in hierarchy["zone_by_node"]:
            zone_value = hierarchy["zone_by_node"][selected.id][1]
            breadcrumbs.append(
                (selected.id, _record_label(zone_value, "City zone"))
            )
        return EditorWorldPresentation(
            world_node=world_node,
            selected_node=selected,
            scope_node=None,
            view=view,
            groups=_groups(*items),
            breadcrumbs=tuple(breadcrumbs),
            decode_issues=decode_issues,
        )

    view, items, placed = _grid_presentation(
        draft,
        world,
        entity_nodes,
        entity_values,
        session,
        scope_prefix=("world",),
        entity_place_id=None,
        title="Grid world",
    )
    return EditorWorldPresentation(
        world_node=world_node,
        selected_node=selected,
        scope_node=None,
        view=view,
        groups=_groups(*items),
        breadcrumbs=(),
        decode_issues=decode_issues,
    )


def spatial_collection_ids(draft: ScenarioEditorDraft) -> frozenset[str]:
    paths = (
        ("world", "blocked"),
        ("world", "zones"),
        ("world", "stations"),
        ("world", "transaction_points"),
        ("world", "city_zones"),
        ("world", "transport", "nodes"),
        ("world", "transport", "edges"),
        ("world", "transport", "vehicles"),
        ("entities",),
    )
    ids = {
        node.id
        for path in paths
        if (node := find_node_by_path(draft.root, path)) is not None
    }
    city_zones = find_node_by_path(draft.root, ("world", "city_zones"))
    if city_zones is not None:
        for index, _zone in enumerate(city_zones.items):
            for name in ("buildings", "outdoor_places"):
                node = find_node_by_path(
                    draft.root,
                    ("world", "city_zones", index, name),
                )
                if node is not None:
                    ids.add(node.id)
    return frozenset(ids)


def _grid_presentation(
    draft: ScenarioEditorDraft,
    world: dict[str, Any],
    entity_nodes: list[ScenarioEditorNode],
    entity_values: list[Any],
    session: OperatorSession,
    *,
    scope_prefix: tuple[str | int, ...],
    entity_place_id: str | None,
    title: str,
) -> tuple[
    dict[str, Any],
    tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...],
    set[str],
]:
    payload = {
        **world,
        "width": _positive_int(world.get("width"), 1),
        "height": _positive_int(world.get("height"), 1),
    }
    groups: list[tuple[str, str, str, tuple[EditorWorldItem, ...]]] = []
    for field, label, kind in (
        ("zones", "Zones", "zone"),
        ("stations", "Stations", "station"),
        (
            "transaction_points",
            "Transaction points",
            "transaction point",
        ),
        ("blocked", "Blocked cells", "blocked"),
    ):
        collection = find_node_by_path(draft.root, (*scope_prefix, field))
        nodes = collection.items if collection is not None else []
        values = payload.get(field)
        values = values if isinstance(values, list) else []
        enriched = []
        items = []
        for index, node in enumerate(nodes):
            value = values[index] if index < len(values) and isinstance(values[index], dict) else {}
            item_label = _record_label(value, f"{label} item {index + 1}")
            drawable = (
                _valid_zone(value)
                if field == "zones"
                else _valid_xy(value.get("position"))
                if field in {"stations", "transaction_points"}
                else _valid_xy(value)
            )
            if drawable:
                enriched.append(
                    {
                        **value,
                        "node_id": node.id,
                        "selected": node.id == draft.view.selected_node_id,
                    }
                )
            items.append(
                EditorWorldItem(
                    node.id,
                    kind,
                    item_label,
                    field,
                    unplaced=not drawable,
                )
            )
        payload[field] = enriched
        groups.append((field, label, collection.id if collection else "", tuple(items)))

    agents = []
    placed: set[str] = set()
    entity_items: list[EditorWorldItem] = []
    for index, node in enumerate(entity_nodes):
        value = (
            entity_values[index]
            if index < len(entity_values)
            and isinstance(entity_values[index], dict)
            else {}
        )
        components = value.get("components")
        components = components if isinstance(components, dict) else {}
        position = components.get("position")
        spatial = components.get("spatial_location")
        belongs = entity_place_id is None or (
            isinstance(spatial, dict) and spatial.get("place_id") == entity_place_id
        )
        label = str(value.get("id") or f"Entity {index + 1}")
        local_position = (
            position
            if entity_place_id is None
            else spatial.get("local_coordinate")
            if isinstance(spatial, dict)
            else None
        )
        unplaced = not belongs or not isinstance(local_position, dict)
        entity_items.append(
            EditorWorldItem(node.id, "entity", label, "entities", unplaced=unplaced)
        )
        if not belongs or unplaced:
            continue
        agents.append(
            {
                "id": label,
                "position": local_position,
            }
        )
        placed.add(node.id)
    view = _grid_view(payload, agents, session, title, {})
    by_entity_id = {
        str(
            entity_values[index].get("id")
            if index < len(entity_values) and isinstance(entity_values[index], dict)
            else ""
        ): node
        for index, node in enumerate(entity_nodes)
    }
    for agent in view["agents"]:
        entity_node = by_entity_id.get(str(agent["id"]))
        if entity_node is not None:
            agent["node_id"] = entity_node.id
            agent["selected"] = entity_node.id == draft.view.selected_node_id
    entities_collection = _required_node(draft, ("entities",))
    groups.append(("entities", "Entities", entities_collection.id, tuple(entity_items)))
    return view, tuple(groups), placed


def _city_presentation(
    draft: ScenarioEditorDraft,
    world: dict[str, Any],
    entity_nodes: list[ScenarioEditorNode],
    entity_values: list[Any],
    session: OperatorSession,
    element_library: ElementLibrary | None,
) -> tuple[
    dict[str, Any],
    tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...],
    set[str],
]:
    city = world.get("city")
    city = city if isinstance(city, dict) else {}
    raw_bounds = city.get("bounds_meters")
    bounds = (
        raw_bounds
        if _valid_city_bounds(raw_bounds)
        else {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1}
    )
    payload: dict[str, Any] = {
        "name": city.get("name", "City"),
        "bounds": bounds,
    }
    groups: list[
        tuple[str, str, str, tuple[EditorWorldItem, ...]]
    ] = []
    districts: list[dict[str, Any]] = []
    buildings: list[dict[str, Any]] = []
    outdoor_places: list[dict[str, Any]] = []
    zone_collection = find_node_by_path(draft.root, ("world", "city_zones"))
    zone_nodes = zone_collection.items if zone_collection is not None else []
    zone_values = world.get("city_zones")
    zone_values = zone_values if isinstance(zone_values, list) else []
    zone_items: list[EditorWorldItem] = []
    for zone_index, zone_node in enumerate(zone_nodes):
        zone = (
            zone_values[zone_index]
            if zone_index < len(zone_values)
            and isinstance(zone_values[zone_index], dict)
            else {}
        )
        zone_label = _record_label(zone, f"City zone {zone_index + 1}")
        drawable = _valid_xy(zone.get("center"))
        zone_items.append(
            EditorWorldItem(
                zone_node.id,
                "city zone",
                zone_label,
                "city_zones",
                unplaced=not drawable,
            )
        )
        if drawable:
            districts.append(
                {
                    "id": zone.get("id", ""),
                    "name": zone_label,
                    "center": zone.get("center"),
                    "node_id": zone_node.id,
                    "selected": zone_node.id == draft.view.selected_node_id,
                }
            )
        building_collection = find_node_by_path(
            draft.root,
            ("world", "city_zones", zone_index, "buildings"),
        )
        building_nodes = (
            building_collection.items if building_collection is not None else []
        )
        building_values = zone.get("buildings")
        building_values = (
            building_values if isinstance(building_values, list) else []
        )
        building_items: list[EditorWorldItem] = []
        for building_index, building_node in enumerate(building_nodes):
            building = (
                building_values[building_index]
                if building_index < len(building_values)
                and isinstance(building_values[building_index], dict)
                else {}
            )
            label = _building_instance_label(building, element_library)
            drawable = _valid_xy(building.get("city_position"))
            building_items.append(
                EditorWorldItem(
                    building_node.id,
                    "building instance",
                    label,
                    "buildings",
                    scope_node_id=building_node.id,
                    unplaced=not drawable,
                )
            )
            if drawable:
                buildings.append(
                    {
                        "id": building.get("id", ""),
                        "name": label,
                        "district_id": zone.get("id", ""),
                        "city_position": building.get("city_position"),
                        "position": building.get("city_position"),
                        "node_id": building_node.id,
                        "interior_node_id": building_node.id,
                        "selected": (
                            building_node.id == draft.view.selected_node_id
                        ),
                    }
                )
        groups.append(
            (
                f"buildings-{zone_index}",
                f"Buildings · {zone_label}",
                building_collection.id if building_collection else "",
                tuple(building_items),
            )
        )
        place_collection = find_node_by_path(
            draft.root,
            ("world", "city_zones", zone_index, "outdoor_places"),
        )
        place_nodes = place_collection.items if place_collection is not None else []
        place_values = zone.get("outdoor_places")
        place_values = place_values if isinstance(place_values, list) else []
        place_items: list[EditorWorldItem] = []
        for place_index, place_node in enumerate(place_nodes):
            place = (
                place_values[place_index]
                if place_index < len(place_values)
                and isinstance(place_values[place_index], dict)
                else {}
            )
            label = _record_label(place, f"Outdoor place {place_index + 1}")
            drawable = _valid_xy(place.get("city_position"))
            place_items.append(
                EditorWorldItem(
                    place_node.id,
                    "outdoor place",
                    label,
                    "outdoor_places",
                    unplaced=not drawable,
                )
            )
            if drawable:
                outdoor_places.append(
                    {
                        **place,
                        "district_id": zone.get("id", ""),
                        "position": place.get("city_position"),
                        "node_id": place_node.id,
                        "selected": place_node.id
                        == draft.view.selected_node_id,
                    }
                )
        groups.append(
            (
                f"outdoor-places-{zone_index}",
                f"Outdoor places · {zone_label}",
                place_collection.id if place_collection else "",
                tuple(place_items),
            )
        )
    payload["districts"] = districts
    payload["buildings"] = buildings
    payload["outdoor_places"] = outdoor_places
    groups.insert(
        0,
        (
            "city_zones",
            "City zones",
            zone_collection.id if zone_collection else "",
            tuple(zone_items),
        ),
    )

    transport = world.get("transport")
    transport = transport if isinstance(transport, dict) else {}
    for field, label, kind in (
        ("nodes", "Transport nodes", "node"),
        ("edges", "Transport edges", "edge"),
        ("vehicles", "Vehicles", "vehicle"),
    ):
        collection = find_node_by_path(draft.root, ("world", "transport", field))
        nodes = collection.items if collection is not None else []
        values = transport.get(field)
        values = values if isinstance(values, list) else []
        projected = []
        items = []
        for index, node in enumerate(nodes):
            value = values[index] if index < len(values) and isinstance(values[index], dict) else {}
            item_label = _record_label(value, f"{label} item {index + 1}")
            drawable = (
                _valid_xy(value.get("position"))
                if field == "nodes"
                else _valid_geometry(value.get("geometry"))
                if field == "edges"
                else True
            )
            if drawable:
                projected.append(
                    {
                        **value,
                        "node_id": node.id,
                        "selected": node.id == draft.view.selected_node_id,
                    }
                )
            items.append(
                EditorWorldItem(
                    node.id,
                    kind,
                    item_label,
                    field,
                    unplaced=not drawable,
                )
            )
        payload[field] = projected
        groups.append((field, label, collection.id if collection else "", tuple(items)))

    agents = []
    entity_items = []
    placed: set[str] = set()
    for index, node in enumerate(entity_nodes):
        value = (
            entity_values[index]
            if index < len(entity_values)
            and isinstance(entity_values[index], dict)
            else {}
        )
        components = value.get("components")
        components = components if isinstance(components, dict) else {}
        location = components.get("spatial_location")
        label = str(value.get("id") or f"Entity {index + 1}")
        unplaced = not isinstance(location, dict)
        entity_items.append(
            EditorWorldItem(node.id, "entity", label, "entities", unplaced=unplaced)
        )
        if unplaced:
            continue
        agents.append({"id": label, "spatial_location": location})
        placed.add(node.id)

    vehicle_states = [
        {
            "id": vehicle.get("id", ""),
            "network_node_id": (
                vehicle.get("location", {}).get("network_node_id")
                if isinstance(vehicle.get("location"), dict)
                else None
            ),
        }
        for vehicle in payload.get("vehicles", [])
    ]
    view = _city_view(payload, agents, session, "City world", {}, vehicle_states)
    for field in ("districts", "buildings", "places"):
        projected_values = payload.get(
            "outdoor_places" if field == "places" else field,
            [],
        )
        projected_values = (
            projected_values if isinstance(projected_values, list) else []
        )
        projected_by_id: dict[str, dict[str, Any]] = {
            str(candidate.get("id", "")): candidate
            for candidate in projected_values
            if isinstance(candidate, dict)
        }
        for item in view.get(field, []):
            projected_item = projected_by_id.get(str(item.get("id", "")))
            if projected_item is not None:
                item["node_id"] = projected_item["node_id"]
                item["selected"] = projected_item["selected"]
    for field in ("edges", "nodes", "vehicles"):
        values = transport.get(field)
        values = values if isinstance(values, list) else []
        nodes = _list_items(draft, ("world", "transport", field))
        by_id = {
            str(value.get("id", "")): nodes[index]
            for index, value in enumerate(values)
            if index < len(nodes) and isinstance(value, dict)
        }
        for item in view.get(field, []):
            item_node = by_id.get(str(item.get("id", "")))
            if item_node is not None:
                item["node_id"] = item_node.id
                item["selected"] = (
                    item_node.id == draft.view.selected_node_id
                )
    entity_map = {
        str(
            entity_values[index].get("id")
            if index < len(entity_values) and isinstance(entity_values[index], dict)
            else ""
        ): node
        for index, node in enumerate(entity_nodes)
    }
    for agent in view["agents"]:
        entity_node = entity_map.get(str(agent["id"]))
        if entity_node is not None:
            agent["node_id"] = entity_node.id
            agent["selected"] = entity_node.id == draft.view.selected_node_id
    entities_collection = _required_node(draft, ("entities",))
    groups.append(("entities", "Entities", entities_collection.id, tuple(entity_items)))
    return view, tuple(groups), placed


def _source_city_hierarchy(
    draft: ScenarioEditorDraft,
    world: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    zone_by_node: dict[str, Any] = {}
    building_by_node: dict[str, Any] = {}
    zones = world.get("city_zones")
    zones = zones if isinstance(zones, list) else []
    zone_nodes = _list_items(draft, ("world", "city_zones"))
    for zone_index, zone_node in enumerate(zone_nodes):
        zone = (
            zones[zone_index]
            if zone_index < len(zones) and isinstance(zones[zone_index], dict)
            else {}
        )
        zone_by_node[zone_node.id] = (zone_node, zone)
        building_nodes = _list_items(
            draft,
            ("world", "city_zones", zone_index, "buildings"),
        )
        buildings = zone.get("buildings")
        buildings = buildings if isinstance(buildings, list) else []
        for building_index, building_node in enumerate(building_nodes):
            building = (
                buildings[building_index]
                if building_index < len(buildings)
                and isinstance(buildings[building_index], dict)
                else {}
            )
            building_by_node[building_node.id] = (
                zone_node,
                zone,
                building_node,
                building,
            )
    return {
        "zone_by_node": zone_by_node,
        "building_by_node": building_by_node,
    }


def _building_instance_label(
    building: dict[str, Any],
    library: ElementLibrary | None,
) -> str:
    overrides = building.get("overrides")
    if isinstance(overrides, dict) and overrides.get("name"):
        return str(overrides["name"])
    reference = building.get("element")
    if isinstance(reference, dict):
        element_id = str(reference.get("id", ""))
        if library is not None and element_id:
            try:
                element = library.get(element_id, ElementKind.BUILDING)
            except ValueError:
                pass
            else:
                if isinstance(element, BuildingElementDefinition):
                    return element.name
        if element_id:
            return element_id
    return str(building.get("id") or "Building instance")


def _resolved_building_preview(
    draft: ScenarioEditorDraft,
    raw: dict[str, Any],
    hierarchy: tuple[
        ScenarioEditorNode,
        dict[str, Any],
        ScenarioEditorNode,
        dict[str, Any],
    ],
    entity_nodes: list[ScenarioEditorNode],
    entity_values: list[Any],
    session: OperatorSession,
    library: ElementLibrary,
) -> tuple[
    dict[str, Any],
    tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...],
] | None:
    _zone_node, _zone, building_node, building_value = hierarchy
    try:
        source = ScenarioSourceDefinition.model_validate(raw)
        resolved = resolve_scenario(source, library)
    except (ScenarioResolutionError, ValidationError):
        return None
    if not isinstance(resolved.source.world, CityWorldSourceDefinition):
        return None
    if not isinstance(resolved.scenario.world, CityWorldDefinition):
        return None
    building_id = str(building_value.get("id", ""))
    runtime_building = next(
        (
            building
            for building in resolved.scenario.world.buildings
            if building.id == building_id
        ),
        None,
    )
    if runtime_building is None:
        return None
    runtime_rooms = [
        room
        for room in resolved.scenario.world.rooms
        if room.building_id == runtime_building.id
    ]
    if not runtime_rooms:
        return None
    selected_room = _camera_room(runtime_rooms, session)
    payload = selected_room.world.model_dump(mode="json")
    for field in ("blocked", "zones", "stations", "transaction_points"):
        values = payload.get(field)
        if not isinstance(values, list):
            continue
        payload[field] = [
            {
                **value,
                "node_id": building_node.id,
                "selected": building_node.id
                == draft.view.selected_node_id,
            }
            for value in values
            if isinstance(value, dict)
        ]
    agents: list[dict[str, Any]] = []
    entity_by_id: dict[str, ScenarioEditorNode] = {}
    entity_items: list[EditorWorldItem] = []
    for index, entity_node in enumerate(entity_nodes):
        entity = (
            entity_values[index]
            if index < len(entity_values)
            and isinstance(entity_values[index], dict)
            else {}
        )
        entity_id = str(entity.get("id") or f"Entity {index + 1}")
        components = entity.get("components")
        components = components if isinstance(components, dict) else {}
        spatial = components.get("spatial_location")
        local_coordinate = (
            spatial.get("local_coordinate")
            if isinstance(spatial, dict)
            and spatial.get("place_id") == selected_room.id
            else None
        )
        unplaced = not isinstance(local_coordinate, dict)
        entity_items.append(
            EditorWorldItem(
                entity_node.id,
                "entity",
                entity_id,
                "entities",
                unplaced=unplaced,
            )
        )
        if unplaced:
            continue
        agents.append({"id": entity_id, "position": local_coordinate})
        entity_by_id[entity_id] = entity_node
    title = (
        "Building interior · "
        f"{_building_instance_label(building_value, library)} · "
        f"{selected_room.name}"
    )
    view = _grid_view(payload, agents, session, title, {})
    for agent in view["agents"]:
        matched_entity = entity_by_id.get(str(agent["id"]))
        if matched_entity is not None:
            agent["node_id"] = matched_entity.id
            agent["selected"] = (
                matched_entity.id == draft.view.selected_node_id
            )
    room_items = tuple(
        EditorWorldItem(
            building_node.id,
            "inherited room",
            room.name,
            "inherited_rooms",
        )
        for room in runtime_rooms
    )
    object_items = tuple(
        EditorWorldItem(
            building_node.id,
            "inherited object",
            str(value.get("name") or value.get("id") or "Object"),
            "inherited_objects",
        )
        for field in ("stations", "transaction_points")
        for value in payload.get(field, [])
        if isinstance(value, dict)
    )
    entities_collection = _required_node(draft, ("entities",))
    return (
        view,
        _groups(
            ("inherited_rooms", "Inherited rooms", "", room_items),
            ("inherited_objects", "Inherited objects", "", object_items),
            (
                "entities",
                "Entities",
                entities_collection.id,
                tuple(entity_items),
            ),
        ),
    )


def _groups(
    *groups: tuple[str, str, str, tuple[EditorWorldItem, ...]],
) -> tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...]:
    return tuple(group for group in groups if group[3] or group[2])


def _entity_items(
    nodes: list[ScenarioEditorNode],
    values: list[Any],
    placed: set[str],
) -> tuple[EditorWorldItem, ...]:
    return tuple(
        EditorWorldItem(
            node.id,
            "entity",
            str(
                values[index].get("id")
                if index < len(values) and isinstance(values[index], dict)
                else f"Entity {index + 1}"
            ),
            "entities",
            unplaced=node.id not in placed,
        )
        for index, node in enumerate(nodes)
    )


def _required_node(
    draft: ScenarioEditorDraft,
    path: tuple[str | int, ...],
) -> ScenarioEditorNode:
    node = find_node_by_path(draft.root, path)
    if node is None:
        raise RuntimeError(f"scenario editor node is missing: {path}")
    return node


def _list_items(
    draft: ScenarioEditorDraft,
    path: tuple[str | int, ...],
) -> list[ScenarioEditorNode]:
    node = find_node_by_path(draft.root, path)
    return node.items if node is not None else []


def _record_label(value: dict[str, Any], fallback: str) -> str:
    return str(value.get("name") or value.get("id") or fallback)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _positive_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if not isinstance(value, (int, float, str)):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _valid_xy(value: object) -> bool:
    return (
        isinstance(value, dict)
        and _number(value.get("x")) is not None
        and _number(value.get("y")) is not None
    )


def _valid_geometry(value: object) -> bool:
    return isinstance(value, list) and len(value) >= 2 and all(
        _valid_xy(point) for point in value
    )


def _valid_zone(value: dict[str, Any]) -> bool:
    tiles = value.get("tiles")
    if isinstance(tiles, list) and tiles:
        return all(_valid_xy(tile) for tile in tiles)
    bounds = value.get("bounds")
    return (
        _valid_xy(bounds)
        and isinstance(bounds, dict)
        and _positive_int(bounds.get("width"), 0) > 0
        and _positive_int(bounds.get("height"), 0) > 0
    )


def _valid_city_bounds(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    min_x = _number(value.get("min_x"))
    min_y = _number(value.get("min_y"))
    max_x = _number(value.get("max_x"))
    max_y = _number(value.get("max_y"))
    return (
        min_x is not None
        and min_y is not None
        and max_x is not None
        and max_y is not None
        and max_x > min_x
        and max_y > min_y
    )
