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
    ObjectElementDefinition,
    ObjectPlacementDefinition,
    RoomElementDefinition,
    RoomPlacementDefinition,
    ScenarioSourceDefinition,
    derive_instance_id,
    element_content_hash,
)
from stage0_sim.application.scenario import (
    CityWorldDefinition,
    WorldObjectDefinition,
)
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
    selected_item: EditorWorldItem | None
    scope_node: ScenarioEditorNode | None
    view: dict[str, Any] | None
    groups: tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...]
    breadcrumbs: tuple[tuple[str, str], ...]
    decode_issues: tuple[tuple[str, str], ...]
    preview_issues: tuple[str, ...] = ()
    read_only_inspector: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _BuildingPreview:
    view: dict[str, Any] | None
    groups: tuple[tuple[str, str, str, tuple[EditorWorldItem, ...]], ...]
    inspectors: dict[str, dict[str, Any]]
    issues: tuple[str, ...] = ()


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
            selected_item=None,
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
                zone_node, zone_value, building_node, building_value = scoped_building
                return EditorWorldPresentation(
                    world_node=world_node,
                    selected_node=selected,
                    selected_item=_selected_item(
                        preview.groups,
                        draft.view.selected_node_id,
                    ),
                    scope_node=building_node,
                    view=preview.view,
                    groups=preview.groups,
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
                    preview_issues=preview.issues,
                    read_only_inspector=preview.inspectors.get(
                        draft.view.selected_node_id
                    ),
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
            selected_item=_selected_item(
                _groups(*items),
                draft.view.selected_node_id,
            ),
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
        selected_item=_selected_item(
            _groups(*items),
            draft.view.selected_node_id,
        ),
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
) -> _BuildingPreview | None:
    _zone_node, _zone, building_node, building_value = hierarchy
    try:
        source = ScenarioSourceDefinition.model_validate(raw)
        resolved = resolve_scenario(source, library)
    except (ScenarioResolutionError, ValidationError) as error:
        issue = (
            "The scenario draft could not be fully resolved. "
            "The interior preview uses referenced element data: "
            f"{error}"
        )
        fallback = _library_building_preview(
            draft,
            building_node,
            building_value,
            entity_nodes,
            entity_values,
            session,
            library,
            issue=issue,
        )
        return fallback or _unavailable_building_preview(issue)
    if not isinstance(resolved.source.world, CityWorldSourceDefinition):
        return _unavailable_building_preview(
            "The resolved scenario source is not a city world."
        )
    if not isinstance(resolved.scenario.world, CityWorldDefinition):
        return _unavailable_building_preview(
            "The resolved scenario does not contain a city world."
        )
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
        return _unavailable_building_preview(
            f"Resolved building {building_id or '(missing ID)'} is unavailable."
        )
    runtime_rooms = [
        room
        for room in resolved.scenario.world.rooms
        if room.building_id == runtime_building.id
    ]
    if not runtime_rooms:
        return _unavailable_building_preview(
            f"Resolved building {runtime_building.id} has no rooms to preview."
        )
    selected_room = _camera_room(runtime_rooms, session)
    payload = selected_room.world.model_dump(mode="json")
    inspectors: dict[str, dict[str, Any]] = {}
    legacy_groups: list[
        tuple[str, str, str, tuple[EditorWorldItem, ...]]
    ] = []
    for field, label, item_kind in (
        ("stations", "Legacy stations", "legacy station"),
        (
            "transaction_points",
            "Legacy transaction points",
            "legacy transaction point",
        ),
    ):
        values = payload.get(field)
        values = values if isinstance(values, list) else []
        projected: list[dict[str, Any]] = []
        items: list[EditorWorldItem] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            object_id = str(value.get("id") or f"{field}-{index + 1}")
            selection_key = _synthetic_key(item_kind, object_id)
            item_label = _record_label(value, f"{label} item {index + 1}")
            projected.append(
                {
                    **value,
                    "node_id": selection_key,
                    "selected": selection_key
                    == draft.view.selected_node_id,
                }
            )
            items.append(
                EditorWorldItem(
                    selection_key,
                    item_kind,
                    item_label,
                    field,
                )
            )
            inspectors[selection_key] = {
                "title": item_label,
                "kind": item_kind,
                "definition_id": next(
                    (
                        item.definition_id
                        for item in resolved.scenario.world.objects
                        if item.id == object_id
                    ),
                    "",
                ),
                "legacy_position": value.get("position"),
            }
        payload[field] = projected
        legacy_groups.append(
            (
                f"{field}-{selected_room.id}",
                f"{label} · {selected_room.name}",
                "",
                tuple(items),
            )
        )
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
            _synthetic_key("room", room.id),
            "inherited room",
            room.name,
            "inherited_rooms",
        )
        for room in runtime_rooms
    )
    for room in runtime_rooms:
        inspectors[_synthetic_key("room", room.id)] = {
            "title": room.name,
            "kind": "inherited room",
            "room_id": room.id,
            "room_key": room.key,
            "room_type": room.type,
            "offset": room.offset.model_dump(mode="json"),
            "width": room.world.width,
            "height": room.world.height,
            "spatial_metric": room.world.spatial_metric.model_dump(mode="json"),
        }
    physical_groups: list[
        tuple[str, str, str, tuple[EditorWorldItem, ...]]
    ] = []
    for room in runtime_rooms:
        relation_groups: dict[str, list[EditorWorldItem]] = {}
        for world_object in resolved.scenario.world.objects:
            if world_object.room_id != room.id:
                continue
            relation = (
                world_object.placement.parent_relation.kind.value
                if world_object.placement is not None
                else "UNPLACED"
            )
            selection_key = _synthetic_key(
                "physical object",
                world_object.id,
            )
            relation_groups.setdefault(relation, []).append(
                EditorWorldItem(
                    selection_key,
                    "physical object",
                    world_object.name,
                    f"physical-{room.id}-{relation}",
                    unplaced=not _valid_physical_object_projection(
                        world_object
                    ),
                )
            )
            inspectors[selection_key] = _physical_object_inspector(
                world_object,
                runtime_building.entrances,
                resolved.scenario.world.portals,
            )
        for relation, items in sorted(relation_groups.items()):
            physical_groups.append(
                (
                    f"physical-{room.id}-{relation}",
                    "Physical objects · "
                    f"{room.name} · {_relation_label(relation)}",
                    "",
                    tuple(items),
                )
            )
    entities_collection = _required_node(draft, ("entities",))
    return _BuildingPreview(
        view=view,
        groups=_groups(
            ("inherited_rooms", "Inherited rooms", "", room_items),
            *physical_groups,
            *legacy_groups,
            (
                "entities",
                "Entities",
                entities_collection.id,
                tuple(entity_items),
            ),
        ),
        inspectors=inspectors,
    )


def _library_building_preview(
    draft: ScenarioEditorDraft,
    building_node: ScenarioEditorNode,
    building_value: dict[str, Any],
    entity_nodes: list[ScenarioEditorNode],
    entity_values: list[Any],
    session: OperatorSession,
    library: ElementLibrary,
    *,
    issue: str,
) -> _BuildingPreview | None:
    reference = building_value.get("element")
    if not isinstance(reference, dict):
        return None
    element_id = str(reference.get("id", ""))
    try:
        building = library.get(element_id, ElementKind.BUILDING)
    except ValueError:
        return None
    if not isinstance(building, BuildingElementDefinition):
        return None
    issues = [issue]
    expected_hash = str(reference.get("content_hash", ""))
    actual_hash = element_content_hash(building)
    if expected_hash and expected_hash != actual_hash:
        issues.append(
            f"Referenced building hash {expected_hash} differs from "
            f"the library hash {actual_hash}."
        )
    instance_id = str(building_value.get("id") or element_id)
    room_entries: list[
        tuple[RoomPlacementDefinition, RoomElementDefinition]
    ] = []
    for placement in building.rooms:
        try:
            room = library.get(placement.element.id, ElementKind.ROOM)
        except ValueError as error:
            issues.append(str(error))
            continue
        if isinstance(room, RoomElementDefinition):
            room_entries.append((placement, room))
    if not room_entries:
        return None
    selected_placement, selected_room = _source_camera_room(
        room_entries,
        session,
    )
    selected_room_id = derive_instance_id(
        instance_id,
        selected_placement.key,
    )
    payload = selected_room.model_dump(mode="json")
    payload["zones"] = payload.get("zones") or []
    payload["stations"] = []
    payload["transaction_points"] = []
    inspectors: dict[str, dict[str, Any]] = {}
    physical_groups: list[
        tuple[str, str, str, tuple[EditorWorldItem, ...]]
    ] = []
    legacy_items: dict[str, list[EditorWorldItem]] = {
        "stations": [],
        "transaction_points": [],
    }
    for room_placement, room in room_entries:
        room_id = derive_instance_id(instance_id, room_placement.key)
        room_key = _synthetic_key("room", room_id)
        inspectors[room_key] = {
            "title": room.name,
            "kind": "inherited room",
            "room_id": room_id,
            "room_key": room_placement.key,
            "room_type": room.room_type,
            "offset": room_placement.offset.model_dump(mode="json"),
            "width": room.width,
            "height": room.height,
            "spatial_metric": room.spatial_metric.model_dump(mode="json"),
        }
        relation_groups: dict[str, list[EditorWorldItem]] = {}
        for object_placement in room.objects:
            try:
                object_element = library.get(
                    object_placement.element.id,
                    ElementKind.OBJECT,
                )
            except ValueError as error:
                issues.append(str(error))
                continue
            if not isinstance(object_element, ObjectElementDefinition):
                continue
            object_id = derive_instance_id(room_id, object_placement.key)
            relation = (
                object_placement.placement.parent_relation.kind.value
                if object_placement.placement is not None
                else "UNPLACED"
            )
            selection_key = _synthetic_key("physical object", object_id)
            relation_groups.setdefault(relation, []).append(
                EditorWorldItem(
                    selection_key,
                    "physical object",
                    object_element.name,
                    f"physical-{room_id}-{relation}",
                    unplaced=not _valid_source_physical_projection(
                        object_element,
                        object_placement,
                    ),
                )
            )
            inspectors[selection_key] = _source_physical_object_inspector(
                object_id,
                object_element,
                object_placement,
                building,
            )
            if room_id != selected_room_id:
                continue
            legacy_field = (
                "stations"
                if object_element.object_type == "affordance"
                else "transaction_points"
                if object_element.object_type == "transaction"
                else ""
            )
            if not legacy_field or object_placement.position is None:
                continue
            legacy_kind = (
                "legacy station"
                if legacy_field == "stations"
                else "legacy transaction point"
            )
            legacy_key = _synthetic_key(legacy_kind, object_id)
            legacy_value: dict[str, Any] = {
                **object_element.model_dump(mode="json"),
                "id": object_id,
                "name": object_element.name,
                "position": object_placement.position.model_dump(mode="json"),
                "node_id": legacy_key,
                "selected": legacy_key == draft.view.selected_node_id,
            }
            if (
                legacy_field == "transaction_points"
                and object_placement.staff_position is not None
            ):
                legacy_value["staffing"] = {
                    "staff_position": object_placement.staff_position.model_dump(
                        mode="json"
                    )
                }
            payload[legacy_field].append(legacy_value)
            legacy_items[legacy_field].append(
                EditorWorldItem(
                    legacy_key,
                    legacy_kind,
                    object_element.name,
                    legacy_field,
                )
            )
            inspectors[legacy_key] = {
                "title": object_element.name,
                "kind": legacy_kind,
                "definition_id": object_element.id,
                "legacy_position": legacy_value["position"],
            }
        for relation, items in sorted(relation_groups.items()):
            physical_groups.append(
                (
                    f"physical-{room_id}-{relation}",
                    "Physical objects · "
                    f"{room.name} · {_relation_label(relation)}",
                    "",
                    tuple(items),
                )
            )
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
            and spatial.get("place_id") == selected_room_id
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
        if not unplaced:
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
            _synthetic_key(
                "room",
                derive_instance_id(instance_id, placement.key),
            ),
            "inherited room",
            room.name,
            "inherited_rooms",
        )
        for placement, room in room_entries
    )
    entities_collection = _required_node(draft, ("entities",))
    return _BuildingPreview(
        view=view,
        groups=_groups(
            ("inherited_rooms", "Inherited rooms", "", room_items),
            *physical_groups,
            (
                f"stations-{selected_room_id}",
                f"Legacy stations · {selected_room.name}",
                "",
                tuple(legacy_items["stations"]),
            ),
            (
                f"transaction-points-{selected_room_id}",
                f"Legacy transaction points · {selected_room.name}",
                "",
                tuple(legacy_items["transaction_points"]),
            ),
            (
                "entities",
                "Entities",
                entities_collection.id,
                tuple(entity_items),
            ),
        ),
        inspectors=inspectors,
        issues=tuple(issues),
    )


def _unavailable_building_preview(issue: str) -> _BuildingPreview:
    return _BuildingPreview(
        view=None,
        groups=(),
        inspectors={},
        issues=(issue,),
    )


def _source_camera_room(
    rooms: list[tuple[RoomPlacementDefinition, RoomElementDefinition]],
    session: OperatorSession,
) -> tuple[RoomPlacementDefinition, RoomElementDefinition]:
    width = max(placement.offset.x + room.width for placement, room in rooms)
    height = max(placement.offset.y + room.height for placement, room in rooms)
    target_x = session.camera_x * max(1, width)
    target_y = session.camera_y * max(1, height)
    return min(
        rooms,
        key=lambda item: (
            (
                item[0].offset.x + item[1].width / 2 - target_x
            )
            ** 2
            + (
                item[0].offset.y + item[1].height / 2 - target_y
            )
            ** 2,
            item[0].key,
        ),
    )


def _synthetic_key(kind: str, identifier: str) -> str:
    return f"inherited:{kind.replace(' ', '-')}:{identifier}"


def _relation_label(relation: str) -> str:
    return relation.replace("_", " ").strip().title()


def _valid_physical_object_projection(
    world_object: WorldObjectDefinition,
) -> bool:
    if world_object.physical is None or world_object.placement is None:
        return False
    return _valid_xy(
        world_object.placement.anchor.model_dump(mode="json")
    ) and all(
        _valid_xy(cell.model_dump(mode="json"))
        for cell in world_object.physical.footprint.cells
    )


def _valid_source_physical_projection(
    object_element: ObjectElementDefinition,
    placement: ObjectPlacementDefinition,
) -> bool:
    if object_element.physical is None or placement.placement is None:
        return False
    return _valid_xy(
        placement.placement.anchor.model_dump(mode="json")
    ) and all(
        _valid_xy(cell.model_dump(mode="json"))
        for cell in object_element.physical.footprint.cells
    )


def _physical_object_inspector(
    world_object: WorldObjectDefinition,
    entrances: list[Any],
    portals: list[Any],
) -> dict[str, Any]:
    physical = (
        world_object.physical.model_dump(mode="json")
        if world_object.physical is not None
        else None
    )
    placement = (
        world_object.placement.model_dump(mode="json")
        if world_object.placement is not None
        else None
    )
    door_links = [
        f"Entrance {item.id}"
        for item in entrances
        if item.door_object_id == world_object.id
    ]
    door_links.extend(
        f"Portal {item.id}"
        for item in portals
        if item.door_object_id == world_object.id
    )
    return {
        "title": world_object.name,
        "kind": "physical object",
        "object_kind": world_object.object_kind,
        "object_id": world_object.id,
        "definition_id": world_object.definition_id,
        "room_id": world_object.room_id,
        "legacy_position": world_object.position.model_dump(mode="json"),
        "physical": physical,
        "placement": placement,
        "door_links": door_links,
        "unplaced": not _valid_physical_object_projection(world_object),
    }


def _source_physical_object_inspector(
    object_id: str,
    object_element: ObjectElementDefinition,
    placement: ObjectPlacementDefinition,
    building: BuildingElementDefinition,
) -> dict[str, Any]:
    aliases = {
        object_id,
        object_element.id,
        placement.id or "",
    }
    door_links = [
        f"Entrance {item.key}"
        for item in building.entrances
        if item.door_object_id in aliases
    ]
    door_links.extend(
        f"Portal {item.key}"
        for item in building.portals
        if item.door_object_id in aliases
    )
    return {
        "title": object_element.name,
        "kind": "physical object",
        "object_kind": object_element.object_type or "physical",
        "object_id": object_id,
        "definition_id": object_element.id,
        "legacy_position": (
            placement.position.model_dump(mode="json")
            if placement.position is not None
            else None
        ),
        "physical": (
            object_element.physical.model_dump(mode="json")
            if object_element.physical is not None
            else None
        ),
        "placement": (
            placement.placement.model_dump(mode="json")
            if placement.placement is not None
            else None
        ),
        "door_links": door_links,
        "unplaced": not _valid_source_physical_projection(
            object_element,
            placement,
        ),
    }


def _selected_item(
    groups: tuple[
        tuple[str, str, str, tuple[EditorWorldItem, ...]],
        ...,
    ],
    selection_key: str,
) -> EditorWorldItem | None:
    return next(
        (
            item
            for _key, _label, _collection_id, items in groups
            for item in items
            if item.node_id == selection_key
        ),
        None,
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
