import json
import re
import shutil
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage0_sim.api.scenario_editor_world import build_editor_world
from stage0_sim.api.scenario_forms import (
    KNOWN_ENTITY_COMPONENT_MODELS,
    ElementEditorDraft,
    ScenarioEditorDraft,
    ScenarioEditorNode,
    element_editor_coverage_errors,
    find_node_by_path,
    scenario_editor_coverage_errors,
)
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    CityWorldSourceDefinition,
    ElementKind,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.scenarios import scenario_content_hash
from stage0_sim.config import Settings
from tests.helpers.paths import EXAMPLE_ELEMENTS, EXAMPLE_SCENARIOS


def _nodes(node: ScenarioEditorNode) -> Iterator[ScenarioEditorNode]:
    yield node
    for child in node.children:
        yield from _nodes(child)
    for item in node.items:
        yield from _nodes(item)
    for variant in node.variants.values():
        if variant is not None:
            yield from _nodes(variant)


def _node(
    draft: ScenarioEditorDraft | ElementEditorDraft,
    path: tuple[str | int, ...],
    *,
    kind: str | None = None,
) -> ScenarioEditorNode:
    return next(
        item
        for item in _nodes(draft.root)
        if item.path == path and (kind is None or item.schema.kind == kind)
    )


@pytest.fixture
def scenario_ui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, FastAPI, Path]]:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    for name in ("minimal.json", "navigation.json"):
        payload = json.loads((EXAMPLE_SCENARIOS / name).read_text(encoding="utf-8"))
        payload["schema_version"] = 6
        (scenarios / name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    shutil.copy2(
        EXAMPLE_SCENARIOS / "reference-city-restaurants.json",
        scenarios / "reference-city-restaurants.json",
    )
    elements = tmp_path / "elements"
    shutil.copytree(EXAMPLE_ELEMENTS, elements)
    settings = Settings(
        data_directory=tmp_path / "runs",
        character_directory=tmp_path / "_runtime" / "characters",
        scenario_directory=scenarios,
        element_directory=elements,
    )
    app_module = import_module("stage0_sim.api.app")
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    with TestClient(app_module.app) as client:
        yield client, cast(FastAPI, app_module.app), scenarios


def _open_draft(
    client: TestClient,
    app: FastAPI,
    path: str,
) -> ScenarioEditorDraft:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    token = response.url.params["draft"]
    session_id = client.cookies["stage0_operator_session"]
    draft = cast(
        ScenarioEditorDraft | None,
        app.state.scenario_editor_drafts.get(session_id, token),
    )
    assert draft is not None
    return draft


def _open_element_draft(
    client: TestClient,
    app: FastAPI,
    path: str,
) -> ElementEditorDraft:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    token = response.url.params["draft"]
    session_id = client.cookies["stage0_operator_session"]
    draft = cast(
        ElementEditorDraft | None,
        app.state.element_editor_drafts.get(session_id, token),
    )
    assert draft is not None
    return draft


def test_scenario_editor_descriptor_covers_every_typed_field() -> None:
    assert scenario_editor_coverage_errors() == ()
    assert element_editor_coverage_errors() == ()
    assert set(KNOWN_ENTITY_COMPONENT_MODELS) == {
        "position",
        "spatial_location",
        "movement",
        "homeostasis",
        "activity",
        "possessions",
        "character_slot",
        "plan",
        "goals",
        "information",
        "controller",
        "senses",
        "embodiment",
        "memory",
        "conversation",
        "metadata",
    }


def test_structured_element_editor_round_trips_v2_physical_fields(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui

    role = _open_element_draft(client, app, "/ui/elements/?kind=npc_role")
    role_name = _node(role, ("name",), kind="scalar")
    role_briefing = _node(role, ("briefing",), kind="scalar")
    role_response = client.post(
        "/ui/elements/save",
        data={
            "draft_token": role.token,
            "resource_id": "structured-role",
            f"value_{role_name.id}": "Structured Role",
            f"value_{role_briefing.id}": "Serve structured requests.",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert role_response.status_code == 200
    assert isinstance(
        app.state.element_library.get("structured-role"),
        NpcRoleElementDefinition,
    )

    object_draft = _open_element_draft(
        client,
        app,
        "/ui/elements/?kind=object",
    )
    footprint_cells = _node(
        object_draft,
        ("physical", "footprint", "cells"),
        kind="list",
    )
    slots = _node(
        object_draft,
        ("physical", "capabilities", "slots"),
        kind="list",
    )
    client.post(
        "/ui/elements/save",
        data={
            "draft_token": object_draft.token,
            "resource_id": "structured-cabinet",
            "collection_action": f"add:{footprint_cells.id}",
        },
    )
    client.post(
        "/ui/elements/save",
        data={
            "draft_token": object_draft.token,
            "resource_id": "structured-cabinet",
            "collection_action": f"add:{slots.id}",
        },
    )
    accepted_relations = _node(
        object_draft,
        (
            "physical",
            "capabilities",
            "slots",
            0,
            "accepted_relations",
        ),
        kind="list",
    )
    for _index in range(2):
        client.post(
            "/ui/elements/save",
            data={
                "draft_token": object_draft.token,
                "resource_id": "structured-cabinet",
                "collection_action": f"add:{accepted_relations.id}",
            },
        )
    support_slot_ids = _node(
        object_draft,
        ("physical", "capabilities", "support", "slot_ids"),
        kind="list",
    )
    container_slot_ids = _node(
        object_draft,
        ("physical", "capabilities", "container", "slot_ids"),
        kind="list",
    )
    for collection in (support_slot_ids, container_slot_ids):
        client.post(
            "/ui/elements/save",
            data={
                "draft_token": object_draft.token,
                "resource_id": "structured-cabinet",
                "collection_action": f"add:{collection.id}",
            },
        )

    values = {
        ("name",): "Structured Cabinet",
        ("description",): "Exercises every physical capability.",
        ("physical", "footprint", "cells", 0, "x"): "0",
        ("physical", "footprint", "cells", 0, "y"): "0",
        ("physical", "footprint", "cells", 1, "x"): "1",
        ("physical", "footprint", "cells", 1, "y"): "0",
        ("physical", "obstruction", "movement"): "HARD",
        ("physical", "obstruction", "vision"): "OPAQUE",
        ("physical", "capabilities", "slots", 0, "id"): "storage",
        ("physical", "capabilities", "slots", 0, "capacity"): "3",
        (
            "physical",
            "capabilities",
            "slots",
            0,
            "accepted_relations",
            0,
        ): "ON_SUPPORT",
        (
            "physical",
            "capabilities",
            "slots",
            0,
            "accepted_relations",
            1,
        ): "IN_CONTAINER",
        ("physical", "capabilities", "support", "slot_ids", 0): "storage",
        ("physical", "capabilities", "container", "slot_ids", 0): "storage",
        ("physical", "capabilities", "portable", "two_handed"): "true",
        ("physical", "capabilities", "readable", "document_id"): "label",
        ("physical", "capabilities", "consumable", "item_id"): "water",
        ("physical", "capabilities", "consumable", "servings"): "2",
        ("physical", "capabilities", "usable", "use_kind"): "inspect",
        (
            "physical",
            "capabilities",
            "openable",
            "initially_locked",
        ): "false",
        ("physical", "initial_open"): "true",
        ("physical", "owner_id"): "owner-source",
        ("physical", "custodian_id"): "custodian-source",
    }
    choices = {
        ("physical", "capabilities", capability): "present"
        for capability in (
            "support",
            "container",
            "portable",
            "readable",
            "consumable",
            "usable",
            "openable",
        )
    }
    choices.update(
        {
            ("physical", "initial_open"): "present",
            ("physical", "owner_id"): "present",
            ("physical", "custodian_id"): "present",
        }
    )
    object_form = {
        "draft_token": object_draft.token,
        "resource_id": "structured-cabinet",
        "intent": "save",
    }
    object_form.update(
        {
            f"value_{_node(object_draft, path, kind='scalar').id}": value
            for path, value in values.items()
        }
    )
    object_form.update(
        {
            f"choice_{_node(object_draft, path, kind='optional').id}": value
            for path, value in choices.items()
        }
    )
    object_response = client.post(
        "/ui/elements/save",
        data=object_form,
        follow_redirects=True,
    )
    assert object_response.status_code == 200
    saved_object = app.state.element_library.get("structured-cabinet")
    assert isinstance(saved_object, ObjectElementDefinition)
    assert saved_object.object_type is None
    assert saved_object.physical is not None
    assert saved_object.physical.owner_id == "owner-source"
    assert saved_object.physical.custodian_id == "custodian-source"
    assert saved_object.physical.obstruction.movement.value == "HARD"
    assert saved_object.physical.obstruction.vision.value == "OPAQUE"
    assert saved_object.physical.capabilities.slots[0].capacity == 3
    assert saved_object.physical.capabilities.portable is not None
    assert saved_object.physical.capabilities.portable.two_handed is True
    assert saved_object.physical.initial_open is True

    invalid = _open_element_draft(
        client,
        app,
        "/ui/elements/?selected=structured-cabinet",
    )
    servings = _node(
        invalid,
        ("physical", "capabilities", "consumable", "servings"),
        kind="scalar",
    )
    movement = _node(
        invalid,
        ("physical", "obstruction", "movement"),
        kind="scalar",
    )
    invalid_response = client.post(
        "/ui/elements/save",
        data={
            "draft_token": invalid.token,
            "resource_id": "structured-cabinet",
            f"value_{servings.id}": "not-a-number",
            f"value_{movement.id}": "NOT_A_MOVEMENT_MODE",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Element could not be saved" in invalid_response.text
    assert "Input should be a valid integer" in invalid_response.text
    assert 'value="not-a-number"' in invalid_response.text
    assert "Invalid submitted value: NOT_A_MOVEMENT_MODE" in (
        invalid_response.text
    )
    assert servings.value == "not-a-number"
    assert movement.value == "NOT_A_MOVEMENT_MODE"

    fixed_response = client.post(
        "/ui/elements/save",
        data={
            "draft_token": invalid.token,
            "resource_id": "structured-cabinet",
            f"value_{servings.id}": "4",
            f"value_{movement.id}": "HARD",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Saved Structured Cabinet." in fixed_response.text
    assert (
        app.state.element_library.get("structured-cabinet")
        .physical.capabilities.consumable.servings
        == 4
    )

    object_hash = element_content_hash(
        app.state.element_library.get("structured-cabinet")
    )
    room = _open_element_draft(client, app, "/ui/elements/?kind=room")
    room_objects = _node(room, ("objects",), kind="list")
    client.post(
        "/ui/elements/save",
        data={
            "draft_token": room.token,
            "resource_id": "structured-room",
            "collection_action": f"add:{room_objects.id}",
        },
    )
    room_values = {
        ("name",): "Structured Room",
        ("room_type",): "STUDY",
        ("width",): "4",
        ("height",): "4",
        ("objects", 0, "key"): "cabinet",
        ("objects", 0, "element", "kind"): "object",
        ("objects", 0, "element", "id"): "structured-cabinet",
        ("objects", 0, "element", "content_hash"): object_hash,
        ("objects", 0, "position", "x"): "1",
        ("objects", 0, "position", "y"): "1",
        ("objects", 0, "placement", "anchor", "x"): "10",
        ("objects", 0, "placement", "anchor", "y"): "10",
        ("objects", 0, "placement", "orientation"): "EAST",
        (
            "objects",
            0,
            "placement",
            "parent_relation",
            "kind",
        ): "ON_FLOOR",
    }
    room_form = {
        "draft_token": room.token,
        "resource_id": "structured-room",
        "intent": "save",
        f"choice_{_node(room, ('objects', 0, 'position'), kind='optional').id}": "present",
        f"choice_{_node(room, ('objects', 0, 'placement'), kind='optional').id}": "present",
    }
    room_form.update(
        {
            f"value_{_node(room, path, kind='scalar').id}": value
            for path, value in room_values.items()
        }
    )
    room_response = client.post(
        "/ui/elements/save",
        data=room_form,
        follow_redirects=True,
    )
    assert "Saved Structured Room." in room_response.text
    saved_room = app.state.element_library.get("structured-room")
    assert isinstance(saved_room, RoomElementDefinition)
    assert saved_room.spatial_metric.microcells_per_legacy_cell == 9
    assert saved_room.objects[0].placement is not None
    assert saved_room.objects[0].placement.orientation.value == "EAST"

    room_hash = element_content_hash(saved_room)
    building = _open_element_draft(
        client,
        app,
        "/ui/elements/?kind=building",
    )
    for path in (("rooms",), ("entrances",), ("portals",)):
        collection = _node(building, path, kind="list")
        client.post(
            "/ui/elements/save",
            data={
                "draft_token": building.token,
                "resource_id": "structured-building",
                "collection_action": f"add:{collection.id}",
            },
        )
    building_values = {
        ("name",): "Structured Building",
        ("rooms", 0, "key"): "main",
        ("rooms", 0, "element", "kind"): "room",
        ("rooms", 0, "element", "id"): "structured-room",
        ("rooms", 0, "element", "content_hash"): room_hash,
        ("entrances", 0, "key"): "front",
        ("entrances", 0, "room_key"): "main",
        ("entrances", 0, "local_coordinate", "x"): "0",
        ("entrances", 0, "local_coordinate", "y"): "1",
        ("entrances", 0, "door_object_id"): "structured-cabinet",
        ("portals", 0, "key"): "internal",
        ("portals", 0, "from_room_key"): "main",
        ("portals", 0, "from_coordinate", "x"): "1",
        ("portals", 0, "from_coordinate", "y"): "1",
        ("portals", 0, "to_room_key"): "main",
        ("portals", 0, "to_coordinate", "x"): "2",
        ("portals", 0, "to_coordinate", "y"): "1",
        ("portals", 0, "door_object_id"): "structured-cabinet",
    }
    entrance_door = _node(
        building,
        ("entrances", 0, "door_object_id"),
        kind="optional",
    )
    portal_door = _node(
        building,
        ("portals", 0, "door_object_id"),
        kind="optional",
    )
    building_form = {
        "draft_token": building.token,
        "resource_id": "structured-building",
        "intent": "save",
        f"choice_{entrance_door.id}": "present",
        f"choice_{portal_door.id}": "present",
    }
    building_form.update(
        {
            f"value_{_node(building, path, kind='scalar').id}": value
            for path, value in building_values.items()
        }
    )
    building_response = client.post(
        "/ui/elements/save",
        data=building_form,
        follow_redirects=True,
    )
    assert "Saved Structured Building." in building_response.text
    saved_building = app.state.element_library.get("structured-building")
    assert isinstance(saved_building, BuildingElementDefinition)
    assert saved_building.entrances[0].door_object_id == (
        "structured-cabinet"
    )
    assert saved_building.portals[0].door_object_id == (
        "structured-cabinet"
    )


def test_physical_only_inherited_objects_are_stable_selectable_and_unplaced(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    object_element = app.state.element_library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "physical-only-door",
                "name": "Physical Only Door",
                "kind": "object",
                "physical": {
                    "footprint": {
                        "cells": [
                            {"x": 0, "y": 0},
                            {"x": 1, "y": 0},
                        ]
                    },
                    "obstruction": {
                        "movement": "HARD",
                        "vision": "OPAQUE",
                    },
                    "capabilities": {
                        "openable": {"initially_locked": True}
                    },
                    "owner_id": "author-owner",
                    "custodian_id": "author-custodian",
                },
            }
        )
    )
    room_element = app.state.element_library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "physical-only-room",
                "name": "Physical Only Room",
                "kind": "room",
                "room_type": "ENTRY",
                "width": 3,
                "height": 3,
                "objects": [
                    {
                        "key": "door",
                        "element": {
                            "kind": "object",
                            "id": object_element.id,
                            "content_hash": element_content_hash(
                                object_element
                            ),
                        },
                        "position": {"x": 1, "y": 1},
                    }
                ],
            }
        )
    )
    building_element = app.state.element_library.create(
        BuildingElementDefinition.model_validate(
            {
                "id": "physical-only-building",
                "name": "Physical Only Building",
                "kind": "building",
                "rooms": [
                    {
                        "key": "entry",
                        "element": {
                            "kind": "room",
                            "id": room_element.id,
                            "content_hash": element_content_hash(
                                room_element
                            ),
                        },
                    }
                ],
                "entrances": [
                    {
                        "key": "front",
                        "room_key": "entry",
                        "local_coordinate": {"x": 0, "y": 1},
                        "door_object_id": object_element.id,
                    }
                ],
            }
        )
    )
    source = ScenarioSourceDefinition.model_validate(
        {
            "name": "Physical-only preview",
            "world": {
                "type": "city",
                "city": {
                    "id": "physical-city",
                    "name": "Physical City",
                    "bounds_meters": {
                        "min_x": 0,
                        "min_y": 0,
                        "max_x": 10,
                        "max_y": 10,
                    },
                },
                "city_zones": [
                    {
                        "id": "center",
                        "name": "Center",
                        "center": {"x": 5, "y": 5},
                        "buildings": [
                            {
                                "id": "physical-instance",
                                "element": {
                                    "kind": "building",
                                    "id": building_element.id,
                                    "content_hash": element_content_hash(
                                        building_element
                                    ),
                                },
                                "city_position": {"x": 5, "y": 5},
                                "entrance_node_ids": {
                                    "front": "physical-node"
                                },
                            }
                        ],
                    }
                ],
                "transport": {
                    "nodes": [
                        {
                            "id": "physical-node",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 5, "y": 5},
                            "place_id": "physical-instance",
                        }
                    ]
                },
            },
        }
    )
    app.state.scenario_library.create("physical-only-preview", source)
    draft = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=physical-only-preview",
    )
    building_node = _node(
        draft,
        ("world", "city_zones", 0, "buildings", 0),
        kind="model",
    )
    draft.view.scope_node_id = building_node.id
    first = build_editor_world(draft, app.state.element_library)
    second = build_editor_world(draft, app.state.element_library)
    physical_items = [
        item
        for _key, label, _collection, items in first.groups
        if label.startswith("Physical objects")
        for item in items
    ]
    assert len(physical_items) == 1
    assert physical_items[0].node_id == next(
        item.node_id
        for _key, label, _collection, items in second.groups
        if label.startswith("Physical objects")
        for item in items
    )
    assert physical_items[0].unplaced is True
    assert physical_items[0].node_id.startswith(
        "inherited:physical-object:"
    )
    assert all(
        item.label != "Physical Only Door"
        for _key, label, _collection, items in first.groups
        if label.startswith("Legacy")
        for item in items
    )

    selected = client.get(
        f"/ui/scenarios/?draft={draft.token}"
        "&selected=physical-only-preview"
        f"&scope={building_node.id}"
        f"&focus={physical_items[0].node_id}",
    )
    assert selected.status_code == 200
    assert "Preview limitations" in selected.text
    assert "Physical Only Door" in selected.text
    assert "not shown on map" in selected.text
    assert "Movement obstruction" in selected.text
    assert "HARD" in selected.text
    assert "Author-owner" not in selected.text
    assert "author-owner" in selected.text
    assert "Entrance front" in selected.text
    assert "This inherited element detail is read-only" in selected.text


def test_scenario_library_page_renders_structured_grid_city_and_entity_controls(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui

    page = client.get(
        "/ui/scenarios/?selected=navigation",
        follow_redirects=True,
    )
    token = page.url.params["draft"]
    session_id = client.cookies["stage0_operator_session"]
    draft = app.state.scenario_editor_drafts.get(session_id, token)
    assert draft is not None

    assert page.status_code == 200
    assert "<h1>Scenario Library</h1>" in page.text
    assert 'href="/ui/scenarios/"' in page.text
    assert "Scenario resource ID" in page.text
    assert "World type" in page.text
    assert "Visual world editor" in page.text
    assert "Pannable scenario world map" in page.text
    assert "World objects" in page.text
    assert "Zones" in page.text
    assert "Stations" in page.text
    assert "Activity Coefficients" in page.text
    assert "Tie Break Order" in page.text
    assert 'option value="city"' in page.text
    identifiers = re.findall(r'\sid="([^"]+)"', page.text)
    assert len(identifiers) == len(set(identifiers))

    city_page = client.get(
        "/ui/scenarios/?selected=reference-city-restaurants",
        follow_redirects=True,
    )
    assert "City zones" in city_page.text
    assert "Buildings · Market Zone" in city_page.text
    assert "Transport nodes" in city_page.text
    assert "Walking Speed Mps" in city_page.text
    assert "Metro Lines" in city_page.text

    stations = find_node_by_path(draft.root, ("world", "stations"))
    assert stations is not None and stations.items
    station_page = client.get(
        f"/ui/scenarios/?draft={draft.token}&selected=navigation"
        f"&focus={stations.items[0].id}",
    )
    assert 'option value="EAT"' in station_page.text

    slot_component = _node(
        draft,
        ("entities", 0, "components", "character_slot"),
        kind="optional",
    )
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"choice_{slot_component.id}": "present",
            "intent": "refresh",
        },
        follow_redirects=True,
    )
    entity = find_node_by_path(draft.root, ("entities", 0))
    assert entity is not None
    slot_page = client.get(
        f"/ui/scenarios/?draft={draft.token}&selected=navigation&focus={entity.id}",
    )
    assert "Unknown Passthrough Components" in slot_page.text
    assert "Default Character Id" in slot_page.text
    assert "Allowed Genders" in slot_page.text


def test_visual_editor_selects_objects_adds_records_and_drills_into_rooms(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    grid = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    blocked = find_node_by_path(grid.root, ("world", "blocked"))
    assert blocked is not None

    added = client.post(
        f"/ui/scenarios/drafts/{grid.token}",
        data={"collection_action": f"add:{blocked.id}"},
        follow_redirects=True,
    )

    assert added.status_code == 200
    assert grid.view.selected_node_id == blocked.items[-1].id
    assert "Selected object" in added.text
    assert "Blocked cells" in added.text

    city = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    buildings = find_node_by_path(
        city.root,
        ("world", "city_zones", 0, "buildings"),
    )
    assert buildings is not None and buildings.items
    building = buildings.items[0]

    selected = client.get(
        f"/ui/scenarios/?draft={city.token}&selected=reference-city-restaurants"
        f"&focus={building.id}",
    )
    assert selected.status_code == 200
    assert "Open Standard Restaurant interior" in selected.text
    assert "Inherited building definition" in selected.text

    drilled = client.get(
        f"/ui/scenarios/?draft={city.token}&selected=reference-city-restaurants"
        f"&scope={building.id}&focus={building.id}",
    )
    assert drilled.status_code == 200
    assert "Building interior · Standard Restaurant" in drilled.text
    assert "Reference City" in drilled.text
    assert "Market Zone" in drilled.text


def test_visual_editor_reports_unprojectable_spatial_records(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    blocked = find_node_by_path(draft.root, ("world", "blocked"))
    assert blocked is not None
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"add:{blocked.id}"},
    )
    x = find_node_by_path(
        draft.root,
        ("world", "blocked", len(blocked.items) - 1, "x"),
    )
    assert x is not None
    x.value = "not-a-number"

    presentation = build_editor_world(draft)
    blocked_group = next(group for group in presentation.groups if group[0] == "blocked")

    assert blocked_group[3][-1].unplaced is True


def test_separate_tabs_keep_separate_drafts_and_invalid_values(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    first = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    second = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    assert first.token != second.token
    first_name = _node(first, ("name",), kind="scalar")
    second_name = _node(second, ("name",), kind="scalar")

    invalid = client.post(
        f"/ui/scenarios/drafts/{first.token}",
        data={
            "resource_id": "minimal",
            f"value_{first_name.id}": "",
            "intent": "save",
        },
        follow_redirects=True,
    )

    assert invalid.status_code == 200
    assert "Scenario validation failed" in invalid.text
    assert "String should have at least 1 character" in invalid.text
    assert first_name.value == ""
    assert second_name.value == "minimal"


def test_create_collections_save_and_unsaved_stage_are_separate(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?new=1")
    name = _node(draft, ("name",), kind="scalar")

    saved = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{name.id}": "Created scenario",
            "intent": "save",
        },
        follow_redirects=True,
    )

    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]
    assert saved.status_code == 200
    assert "staged scenario and active run were unchanged" in saved.text
    assert (directory / "created.json").is_file()
    assert operator_session.scenario is None
    assert operator_session.run_id is None

    entities = _node(draft, ("entities",), kind="list")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            "collection_action": f"add:{entities.id}",
        },
    )
    entity_id = _node(draft, ("entities", 0, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{entity_id.id}": "person-001",
            "intent": "save",
        },
    )
    assert app.state.scenario_library.get("created").entities[0].id == "person-001"

    staged_name = _node(draft, ("name",), kind="scalar")
    staged = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{staged_name.id}": "Unsaved staged scenario",
            "intent": "stage",
        },
        follow_redirects=True,
    )

    assert staged.status_code == 200
    assert "Unsaved staged scenario" in staged.text
    assert operator_session.scenario is not None
    assert operator_session.scenario.name == "Unsaved staged scenario"
    assert operator_session.run_id is None
    assert app.state.scenario_library.get("created").name == "Created scenario"


def test_collection_reorder_and_remove_actions_preserve_loose_values(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?new=1")
    entities = _node(draft, ("entities",), kind="list")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"add:{entities.id}"},
    )
    first_id = _node(draft, ("entities", 0, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"value_{first_id.id}": "first",
            "collection_action": f"add:{entities.id}",
        },
    )
    second_item = entities.items[1]
    second_id = _node(draft, ("entities", 1, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"value_{second_id.id}": "second",
            "collection_action": f"up:{entities.id}:{second_item.id}",
        },
    )

    assert _node(draft, ("entities", 0, "id"), kind="scalar").value == "second"
    assert _node(draft, ("entities", 1, "id"), kind="scalar").value == "first"

    removed_id = entities.items[1].id
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"remove:{entities.id}:{removed_id}"},
    )
    assert len(entities.items) == 1
    assert _node(draft, ("entities", 0, "id"), kind="scalar").value == "second"


def test_stale_editor_save_is_rejected_without_losing_the_draft(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    library = app.state.scenario_library
    current = library.get("minimal")
    external = current.model_copy(update={"name": "External update"})
    library.update("minimal", external, scenario_content_hash(current))
    name = _node(draft, ("name",), kind="scalar")

    response = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "minimal",
            f"value_{name.id}": "Stale draft value",
            "intent": "save",
        },
        follow_redirects=True,
    )

    assert "scenario changed since it was loaded" in response.text
    assert name.value == "Stale draft value"
    assert library.get("minimal").name == "External update"


def test_nested_grid_and_city_fields_save_through_structured_controls(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    grid = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    grid_width = _node(grid, ("world", "width"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{grid.token}",
        data={
            "resource_id": "navigation",
            f"value_{grid_width.id}": "13",
            "intent": "save",
        },
    )
    assert app.state.scenario_library.get("navigation").world.width == 13

    city = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    city_name = _node(city, ("world", "city", "name"), kind="scalar")
    walking_speed = _node(
        city,
        ("world", "transport", "walking_speed_mps"),
        kind="scalar",
    )
    response = client.post(
        f"/ui/scenarios/drafts/{city.token}",
        data={
            "resource_id": "reference-city-restaurants",
            f"value_{city_name.id}": "Edited City",
            f"value_{walking_speed.id}": "1.75",
            "intent": "save",
        },
        follow_redirects=True,
    )
    loaded = app.state.scenario_library.get("reference-city-restaurants")

    assert response.status_code == 200
    assert "Saved" in response.text
    assert isinstance(loaded.world, CityWorldSourceDefinition)
    assert loaded.world.city.name == "Edited City"
    assert loaded.world.transport.walking_speed_mps == 1.75


def test_building_library_add_populates_reference_and_reset_is_isolated(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    zone = _node(draft, ("world", "city_zones", 0), kind="model")
    buildings = _node(
        draft,
        ("world", "city_zones", 0, "buildings"),
        kind="list",
    )
    before = len(buildings.items)

    added = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "building_action": f"add:{zone.id}:standard-restaurant",
        },
        follow_redirects=True,
    )

    assert added.status_code == 200
    assert len(buildings.items) == before + 1
    added_index = len(buildings.items) - 1
    reference_id = _node(
        draft,
        (
            "world",
            "city_zones",
            0,
            "buildings",
            added_index,
            "element",
            "id",
        ),
        kind="scalar",
    )
    reference_hash = _node(
        draft,
        (
            "world",
            "city_zones",
            0,
            "buildings",
            added_index,
            "element",
            "content_hash",
        ),
        kind="scalar",
    )
    assert reference_id.value == "standard-restaurant"
    assert len(reference_hash.value) == 64
    assert "Inherited building definition" in added.text

    first = buildings.items[0]
    first_name = _node(
        draft,
        ("world", "city_zones", 0, "buildings", 0, "overrides", "name"),
        kind="optional",
    )
    changed = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            f"choice_{first_name.id}": "present",
            f"value_{first_name.items[0].id}": "West Custom Restaurant",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Saved" in changed.text
    saved = app.state.scenario_library.get("reference-city-restaurants")
    assert isinstance(saved.world, CityWorldSourceDefinition)
    assert saved.world.city_zones[0].buildings[0].overrides.name == (
        "West Custom Restaurant"
    )
    assert saved.world.city_zones[0].buildings[1].overrides.name == (
        "East Market Restaurant"
    )

    first = _node(
        draft,
        ("world", "city_zones", 0, "buildings", 0),
        kind="model",
    )
    reset = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "building_action": f"reset:{first.id}",
        },
        follow_redirects=True,
    )
    assert reset.status_code == 200
    saved_again = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Saved" in saved_again.text
    reset_source = app.state.scenario_library.get(
        "reference-city-restaurants"
    )
    assert isinstance(reset_source.world, CityWorldSourceDefinition)
    assert reset_source.world.city_zones[0].buildings[0].overrides.name is None
    assert reset_source.world.city_zones[0].buildings[1].overrides.name == (
        "East Market Restaurant"
    )
    on_disk = json.loads(
        (_directory / "reference-city-restaurants.json").read_text(
            encoding="utf-8"
        )
    )
    assert "city_zones" in on_disk["world"]
    assert "local_maps" not in on_disk["world"]


def test_missing_and_hash_drift_dependencies_block_saved_staging(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    library = app.state.scenario_library
    source = library.get("reference-city-restaurants")
    raw = source.model_dump(mode="json")
    raw["world"]["city_zones"][0]["buildings"][0]["element"]["id"] = (
        "missing-building"
    )
    missing = ScenarioSourceDefinition.model_validate(raw)
    library.create("missing-dependency", missing)

    missing_response = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "missing-dependency"},
        follow_redirects=True,
    )
    assert "Could not stage saved scenario" in missing_response.text
    assert "unknown element: missing-building" in missing_response.text

    building = app.state.element_library.get(
        "standard-restaurant",
        ElementKind.BUILDING,
    )
    building_hash = next(
        summary.content_hash
        for summary in app.state.element_library.list(ElementKind.BUILDING)
        if summary.id == "standard-restaurant"
    )
    app.state.element_library.update(
        "standard-restaurant",
        building.model_copy(
            update={"description": "Changed after scenario save"}
        ),
        building_hash,
    )
    drift_response = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "reference-city-restaurants"},
        follow_redirects=True,
    )
    assert "Could not stage saved scenario" in drift_response.text
    assert "content hash changed" in drift_response.text


def test_import_search_duplicate_rename_download_and_delete(
    scenario_ui: tuple[TestClient, FastAPI, Path],
    tmp_path: Path,
) -> None:
    client, app, _directory = scenario_ui
    imported_path = tmp_path / "imported-scenario.json"
    legacy_path = tmp_path / "legacy-scenario.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "Legacy scenario",
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    legacy = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("legacy-scenario.json", legacy_path.read_bytes())},
        follow_redirects=True,
    )
    assert "saved scenarios require schema version 6" in legacy.text
    imported_path.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "name": "Imported scenario",
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    imported = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("imported-scenario.json", imported_path.read_bytes())},
        follow_redirects=True,
    )
    assert "Imported Imported scenario as imported-scenario" in imported.text
    oversized = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("too-large.json", b"x" * (5 * 1024 * 1024 + 1))},
        follow_redirects=True,
    )
    assert "scenario files must be 5 MB or smaller" in oversized.text

    draft = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=imported-scenario",
    )
    renamed = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"resource_id": "renamed-scenario", "intent": "save"},
        follow_redirects=True,
    )
    assert renamed.status_code == 200
    assert app.state.scenario_library.get("renamed-scenario").name == (
        "Imported scenario"
    )

    expected_hash = app.state.scenario_library.list()[
        [item.id for item in app.state.scenario_library.list()].index(
            "renamed-scenario"
        )
    ].content_hash
    duplicated = client.post(
        "/ui/scenarios/renamed-scenario/duplicate",
        data={"expected_hash": expected_hash},
        follow_redirects=True,
    )
    assert "Duplicated renamed-scenario as renamed-scenario-copy" in duplicated.text

    search = client.get("/ui/scenarios/?search=renamed", follow_redirects=True)
    assert "renamed-scenario-copy" in search.text
    download = client.get("/ui/scenarios/renamed-scenario/download")
    assert download.status_code == 200
    assert download.json()["name"] == "Imported scenario"

    deleted = client.post(
        "/ui/scenarios/renamed-scenario/delete",
        data={"expected_hash": expected_hash, "confirm": "yes"},
        follow_redirects=True,
    )
    assert "Deleted renamed-scenario" in deleted.text


def test_simulation_page_stages_only_the_selected_saved_scenario(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    page = client.get("/ui/")
    staged = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "minimal"},
        follow_redirects=True,
    )
    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]

    assert "Stage selected saved scenario" in page.text
    assert "Open selected scenario in editor" in page.text
    assert "minimal is validated and staged" in staged.text
    assert operator_session.scenario is not None
    assert operator_session.scenario.name == "minimal"
    assert operator_session.run_id is None
    assert operator_session.scenario_id is not None
    prepared = app.state.simulation_manager.get_scenario(
        operator_session.scenario_id
    )
    assert prepared.scenario_source is not None
    assert prepared.scenario_source["schema_version"] == 6


def test_saved_reference_scenario_preserves_resolved_provenance(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui

    staged = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "reference-city-restaurants"},
        follow_redirects=True,
    )
    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]

    assert "reference-city-restaurants is validated and staged" in staged.text
    assert operator_session.scenario_id is not None
    prepared = app.state.simulation_manager.get_scenario(
        operator_session.scenario_id
    )
    assert prepared.scenario_source is not None
    assert "city_zones" in prepared.scenario_source["world"]
    assert prepared.resolved_elements["standard-restaurant"]["kind"] == (
        "building"
    )


def test_character_slot_filters_and_revalidates_assignments(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    constrained = ScenarioSourceDefinition.model_validate(
        {
            "name": "Constrained composition",
            "calendar": {
                "start_datetime": "2026-08-31T09:00:00+00:00"
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "character_slot": {
                            "label": "Experienced male analyst",
                            "briefing": "Review the evidence.",
                            "constraints": {
                                "minimum_age": 30,
                                "allowed_genders": ["man"],
                                "allowed_template_ids": ["human-v1"],
                            },
                        }
                    },
                }
            ],
        }
    )
    app.state.scenario_library.create("constrained", constrained)

    loaded = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "constrained"},
        follow_redirects=True,
    )

    assert "Could not stage saved scenario" in loaded.text
    assert "Experienced male analyst" in loaded.text
    assert "Alex Chen" in loaded.text
    assert "Jordan Lee" not in loaded.text
    assert 'disabled>Start run</button>' in loaded.text

    rejected = client.post(
        "/ui/scenario/assign",
        data={"character.agent-001": "jordan-lee"},
        follow_redirects=True,
    )
    assert "Assignment invalid" in rejected.text
    assert "is ineligible for slot agent-001" in rejected.text
    assert 'disabled>Start run</button>' in rejected.text

    staged = client.post(
        "/ui/scenario/assign",
        data={"character.agent-001": "alex-chen"},
        follow_redirects=True,
    )
    assert "assignments were validated and staged" in staged.text
    assert ">Start run</button>" in staged.text
