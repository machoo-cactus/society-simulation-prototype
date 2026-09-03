from pathlib import Path

import pytest

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.scenario import CoordinateDefinition, create_runner
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)
from stage0_sim.domain.components import (
    DriveComponent,
    NavigationComponent,
    NavigationStatus,
    PerceptionComponent,
    PossessionsComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.world import (
    ContainerTopology,
    GridTopology,
    SpaceRegistry,
)


def _reference(
    kind: ElementKind,
    element: object,
) -> dict[str, str]:
    return {
        "kind": kind.value,
        "id": element.id,
        "content_hash": element_content_hash(element),
    }


def _source(
    tmp_path: Path,
    *,
    entities: list[dict[str, object]],
    portal_available: bool = True,
) -> tuple[ScenarioSourceDefinition, FileSystemElementLibrary]:
    library = FileSystemElementLibrary(tmp_path)
    cashier = library.create(
        NpcRoleElementDefinition(
            id="cashier",
            name="Cashier",
        )
    )
    foyer_chair = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "foyer-chair",
                "name": "Foyer chair",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "affordance",
                "supported_actions": ["RELAX"],
            }
        )
    )
    kitchen_table = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "kitchen-table",
                "name": "Kitchen table",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "affordance",
                "actions": [
                    {
                        "action": "EAT",
                        "duration": 2,
                        "effect": {"satiety_target": 100},
                    }
                ],
            }
        )
    )
    checkout = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "checkout",
                "name": "Checkout",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "transaction",
                "operation": "STAFFED",
                "npc_role": _reference(ElementKind.NPC_ROLE, cashier),
                "offers": [
                    {
                        "id": "buy-meal",
                        "name": "Buy meal",
                        "character_gives": [
                            {"item_id": "credit", "quantity": 1}
                        ],
                        "character_receives": [
                            {"item_id": "meal", "quantity": 1}
                        ],
                        "duration": 1,
                    }
                ],
                "holdings": {"meal": 10},
            }
        )
    )
    office_desk = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "office-desk",
                "name": "Office desk",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "affordance",
                "supported_actions": ["RELAX"],
            }
        )
    )
    foyer = library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "foyer",
                "name": "Foyer",
                "kind": "room",
                "room_type": "ENTRY",
                "width": 3,
                "height": 3,
                "objects": [
                    {
                        "key": "chair",
                        "element": _reference(
                            ElementKind.OBJECT, foyer_chair
                        ),
                        "position": {"x": 1, "y": 2},
                        "placement": {
                            "anchor": {"x": 13, "y": 22},
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                    }
                ],
            }
        )
    )
    kitchen = library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "kitchen",
                "name": "Kitchen",
                "kind": "room",
                "room_type": "KITCHEN",
                "width": 3,
                "height": 3,
                "objects": [
                    {
                        "key": "table",
                        "element": _reference(
                            ElementKind.OBJECT, kitchen_table
                        ),
                        "position": {"x": 2, "y": 1},
                        "placement": {
                            "anchor": {"x": 22, "y": 13},
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                    },
                    {
                        "key": "checkout",
                        "element": _reference(
                            ElementKind.OBJECT, checkout
                        ),
                        "position": {"x": 1, "y": 2},
                        "placement": {
                            "anchor": {"x": 13, "y": 22},
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                        "staff_position": {"x": 2, "y": 2},
                    },
                ],
            }
        )
    )
    office = library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "office",
                "name": "Office",
                "kind": "room",
                "room_type": "OFFICE",
                "width": 3,
                "height": 3,
                "objects": [
                    {
                        "key": "desk",
                        "element": _reference(
                            ElementKind.OBJECT, office_desk
                        ),
                        "position": {"x": 2, "y": 1},
                        "placement": {
                            "anchor": {"x": 22, "y": 13},
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                    }
                ],
            }
        )
    )
    home = library.create(
        BuildingElementDefinition.model_validate(
            {
                "id": "home",
                "name": "Home",
                "kind": "building",
                "rooms": [
                    {
                        "key": "foyer",
                        "element": _reference(ElementKind.ROOM, foyer),
                        "offset": {"x": 0, "y": 0},
                    },
                    {
                        "key": "kitchen",
                        "element": _reference(ElementKind.ROOM, kitchen),
                        "offset": {"x": 3, "y": 0},
                    },
                ],
                "portals": [
                    {
                        "key": "foyer-kitchen",
                        "from_room_key": "foyer",
                        "from_coordinate": {"x": 2, "y": 1},
                        "to_room_key": "kitchen",
                        "to_coordinate": {"x": 0, "y": 1},
                        "bidirectional": True,
                        "available": portal_available,
                    }
                ],
                "entrances": [
                    {
                        "key": "front",
                        "room_key": "foyer",
                        "local_coordinate": {"x": 0, "y": 1},
                    }
                ],
            }
        )
    )
    workplace = library.create(
        BuildingElementDefinition.model_validate(
            {
                "id": "workplace",
                "name": "Workplace",
                "kind": "building",
                "rooms": [
                    {
                        "key": "office",
                        "element": _reference(ElementKind.ROOM, office),
                    }
                ],
                "entrances": [
                    {
                        "key": "front",
                        "room_key": "office",
                        "local_coordinate": {"x": 0, "y": 1},
                    }
                ],
            }
        )
    )
    source = ScenarioSourceDefinition.model_validate(
        {
            "schema_version": 8,
            "name": "Authoritative hierarchy",
            "items": [
                {"id": "credit", "name": "Credit", "unit": "credit"},
                {"id": "meal", "name": "Meal", "unit": "meal"},
            ],
            "world": {
                "type": "city",
                "city": {
                    "id": "city",
                    "name": "City",
                    "bounds_meters": {
                        "min_x": 0,
                        "min_y": 0,
                        "max_x": 20,
                        "max_y": 10,
                    },
                },
                "city_zones": [
                    {
                        "id": "zone",
                        "name": "Zone",
                        "center": {"x": 10, "y": 5},
                        "buildings": [
                            {
                                "id": "home-instance",
                                "element": _reference(
                                    ElementKind.BUILDING, home
                                ),
                                "city_position": {"x": 2, "y": 5},
                                "entrance_node_ids": {
                                    "front": "home-node"
                                },
                            },
                            {
                                "id": "work-instance",
                                "element": _reference(
                                    ElementKind.BUILDING, workplace
                                ),
                                "city_position": {"x": 18, "y": 5},
                                "entrance_node_ids": {
                                    "front": "work-node"
                                },
                            },
                        ],
                    }
                ],
                "transport": {
                    "nodes": [
                        {
                            "id": "home-node",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 2, "y": 5},
                            "place_id": "home-instance",
                        },
                        {
                            "id": "work-node",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 18, "y": 5},
                            "place_id": "work-instance",
                        },
                    ],
                    "edges": [
                        {
                            "id": "street",
                            "from_node_id": "home-node",
                            "to_node_id": "work-node",
                            "allowed_modes": ["WALK"],
                            "distance_meters": 2,
                            "geometry": [
                                {"x": 2, "y": 5},
                                {"x": 18, "y": 5},
                            ],
                            "bidirectional": True,
                        }
                    ],
                },
            },
            "entities": entities,
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0},
                    "WALKING": {"satiety": 0, "energy": 0, "stress": 0},
                }
            },
        }
    )
    return source, library


def _character(
    character_id: str,
    room_id: str,
    coordinate: tuple[int, int],
    *,
    plan: list[dict[str, object]] | None = None,
    satiety: float = 100,
) -> dict[str, object]:
    components: dict[str, object] = {
        "spatial_location": {
            "scale": "BUILDING",
            "place_id": room_id,
            "local_coordinate": {"x": coordinate[0], "y": coordinate[1]},
        },
        "homeostasis": {
            "satiety": satiety,
            "energy": 100,
            "stress": 0,
        },
        "possessions": {"holdings": {"credit": 5}},
    }
    if plan is not None:
        components["plan"] = {"queue": plan}
    return {"id": character_id, "components": components}


def _runner(
    tmp_path: Path,
    *,
    entities: list[dict[str, object]],
    portal_available: bool = True,
    run_id: str | None = None,
):
    source, library = _source(
        tmp_path,
        entities=entities,
        portal_available=portal_available,
    )
    return create_runner(
        resolve_scenario(source, library).scenario,
        run_id=run_id,
    )


def test_resolved_rooms_are_the_only_interior_grid_spaces(
    tmp_path: Path,
) -> None:
    source, library = _source(tmp_path, entities=[])
    resolved = resolve_scenario(source, library)
    world = resolved.scenario.world
    assert world is not None and hasattr(world, "rooms")
    assert [room.id for room in world.rooms] == [
        "home-instance.foyer",
        "home-instance.kitchen",
        "work-instance.office",
    ]
    assert world.rooms[1].offset.model_dump() == {"x": 3, "y": 0}
    assert world.portals[0].from_room_id == "home-instance.foyer"
    runner = create_runner(resolved.scenario)
    topology = runner.registry.get_resource(SpaceRegistry)
    assert isinstance(
        topology.space("home-instance").topology, ContainerTopology
    )
    assert isinstance(
        topology.space("home-instance.foyer").topology, GridTopology
    )
    portal = topology.transition("home-instance.foyer-kitchen")
    assert any(
        transition.id == "home-instance.foyer-kitchen:reverse"
        for transition in topology.transitions_from(portal.to_locator)
    )


def test_resolution_rejects_invalid_portals_and_disabled_dependencies(
    tmp_path: Path,
) -> None:
    source, library = _source(tmp_path, entities=[])
    raw = source.model_dump(mode="json")
    raw["world"]["city_zones"][0]["buildings"][0]["overrides"] = {
        "disabled_room_keys": ["foyer"]
    }
    with pytest.raises(
        ScenarioResolutionError,
        match="cannot disable rooms used by entrances or portals",
    ):
        resolve_scenario(
            ScenarioSourceDefinition.model_validate(raw),
            library,
        )

    home = library.get("home", ElementKind.BUILDING)
    bad_portal = home.portals[0].model_copy(
        update={"from_coordinate": CoordinateDefinition(x=9, y=9)}
    )
    invalid_home = home.model_copy(update={"portals": [bad_portal]})
    library.update(
        home.id,
        invalid_home,
        element_content_hash(home),
    )
    raw = source.model_dump(mode="json")
    raw["world"]["city_zones"][0]["buildings"][0]["element"][
        "content_hash"
    ] = element_content_hash(invalid_home)
    with pytest.raises(
        ScenarioResolutionError,
        match="from-coordinate is outside room",
    ):
        resolve_scenario(
            ScenarioSourceDefinition.model_validate(raw),
            library,
        )


def test_cross_room_and_cross_building_navigation_use_transitions(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        entities=[
            _character(
                "character",
                "home-instance.foyer",
                (1, 1),
                plan=[
                    {
                        "action": "NAVIGATE",
                        "target": "home-instance.kitchen.table",
                    },
                    {
                        "action": "NAVIGATE",
                        "target": "work-instance.office.desk",
                    },
                ],
            )
        ],
    )
    runner.run_for(30)
    location = runner.registry.get_component(
        "character", SpatialLocationComponent
    ).location
    assert location.place_id == "work-instance.office"
    assert location.local_coordinate is not None
    assert location.local_coordinate.to_payload() == {"x": 19, "y": 13}
    traversed = [
        event
        for event in runner.events.events
        if event.event_type == "portal.traversed"
    ]
    assert traversed[0].payload["from_room_id"] == "home-instance.foyer"
    assert any(
        event.event_type == "building.entered"
        and event.payload["room_id"] == "work-instance.office"
        for event in runner.events.events
    )


def test_same_room_navigation_uses_only_grid_movement(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        entities=[
            _character(
                "character",
                "home-instance.foyer",
                (1, 1),
                plan=[
                    {
                        "action": "NAVIGATE",
                        "target": "home-instance.foyer.chair",
                    }
                ],
            )
        ],
    )
    runner.run_for(6)
    location = runner.registry.get_component(
        "character", SpatialLocationComponent
    ).location
    assert location.place_id == "home-instance.foyer"
    assert location.local_coordinate is not None
    assert location.local_coordinate.to_payload() == {"x": 13, "y": 19}
    assert not any(
        event.event_type == "portal.traversed"
        for event in runner.events.events
    )


def test_disabled_portal_blocks_navigation_without_teleporting(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        portal_available=False,
        entities=[
            _character(
                "character",
                "home-instance.foyer",
                (1, 1),
                plan=[
                    {
                        "action": "NAVIGATE",
                        "target": "home-instance.kitchen.table",
                    }
                ],
            )
        ],
    )
    runner.run_for(5)
    location = runner.registry.get_component(
        "character", SpatialLocationComponent
    ).location
    navigation = runner.registry.get_component(
        "character", NavigationComponent
    )
    assert location.place_id == "home-instance.foyer"
    assert navigation.status in {
        NavigationStatus.FAILED,
        NavigationStatus.IDLE,
    }
    assert any(
        event.event_type == "navigation.failed"
        and event.payload["reason"] == "route_not_found"
        for event in runner.events.events
    )


def test_occupied_portal_destination_blocks_cross_room_route(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        entities=[
            _character(
                "character",
                "home-instance.foyer",
                (1, 1),
                plan=[
                    {
                        "action": "NAVIGATE",
                        "target": "home-instance.kitchen.table",
                    }
                ],
            ),
            _character(
                "blocker",
                "home-instance.kitchen",
                (0, 1),
            ),
        ],
    )
    runner.run_for(5)
    location = runner.registry.get_component(
        "character", SpatialLocationComponent
    ).location
    assert location.place_id == "home-instance.foyer"
    assert not any(
        event.event_type == "portal.traversed"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "navigation.failed"
        and event.payload["reason"] == "route_not_found"
        for event in runner.events.events
    )


def test_room_occupancy_and_perception_are_isolated(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        entities=[
            _character("first", "home-instance.foyer", (1, 1)),
            _character("second", "home-instance.kitchen", (1, 1)),
        ],
    )
    runner.run_for(1)
    first = runner.registry.get_component("first", PerceptionComponent)
    second = runner.registry.get_component("second", PerceptionComponent)
    assert "second" not in first.visible_now
    assert "first" not in second.visible_now


def test_room_transaction_spawns_staff_in_the_authoritative_room(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        entities=[
            _character(
                "customer",
                "home-instance.kitchen",
                (1, 2),
                plan=[
                    {
                        "action": "TRANSACT",
                        "target": "home-instance.kitchen.checkout",
                        "offer_id": "buy-meal",
                    }
                ],
            )
        ],
    )
    runner.run_for(6)
    possessions = runner.registry.get_component(
        "customer", PossessionsComponent
    )
    assert possessions.holdings["meal"] == 1
    npc_id = next(
        entity_id
        for entity_id in runner.registry.entities()
        if entity_id.startswith("npc-")
    )
    npc_location = runner.registry.get_component(
        npc_id, SpatialLocationComponent
    ).location
    assert npc_location.place_id == "home-instance.kitchen"


def test_system1_uses_room_portals_and_runs_deterministically(
    tmp_path: Path,
) -> None:
    events: list[list[tuple[str, object]]] = []
    for index in range(2):
        runner = _runner(
            tmp_path / str(index),
            run_id="deterministic-hierarchy",
            entities=[
                _character(
                    "character",
                    "home-instance.foyer",
                    (1, 1),
                    satiety=0,
                )
            ],
        )
        runner.run_for(20)
        drive = runner.registry.get_component("character", DriveComponent)
        location = runner.registry.get_component(
            "character", SpatialLocationComponent
        ).location
        assert drive.active_drive is None
        assert location.place_id == "home-instance.kitchen"
        assert any(
            event.event_type == "portal.traversed"
            for event in runner.events.events
        )
        events.append(
            [
                (event.event_type, event.payload)
                for event in runner.events.events
            ]
        )
    assert events[0] == events[1]
