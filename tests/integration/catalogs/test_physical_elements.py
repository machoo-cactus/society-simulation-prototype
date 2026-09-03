from pathlib import Path

import pytest
from pydantic import ValidationError

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.scenario import create_runner
from stage0_sim.application.scenario_resolution import resolve_scenario
from stage0_sim.domain.components import (
    MovementObstruction,
    OpenableComponent,
    PhysicalObjectIdentityComponent,
    PhysicalStateComponent,
    SpatialIndex,
    SpatialParentRelationComponent,
    VisionObstruction,
)
from stage0_sim.domain.world import Coordinate


def _reference(
    kind: ElementKind,
    element_id: str,
    content_hash: str,
) -> dict[str, str]:
    return {
        "kind": kind.value,
        "id": element_id,
        "content_hash": content_hash,
    }


def test_element_v3_physical_schema_is_strict() -> None:
    with pytest.raises(ValidationError, match="require physical data"):
        ObjectElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "chair",
                "name": "Chair",
                "kind": "object",
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ObjectElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "chair",
                "name": "Chair",
                "kind": "object",
                "physical": {
                    "footprint": {"cells": [{"x": 0, "y": 0}]},
                    "unknown": True,
                },
            }
        )
    with pytest.raises(ValidationError, match="initial_open requires"):
        ObjectElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "cabinet",
                "name": "Cabinet",
                "kind": "object",
                "physical": {
                    "footprint": {"cells": [{"x": 0, "y": 0}]},
                    "initial_open": True,
                },
            }
        )


def test_v5_scenario_resolves_and_materializes_physical_object(
    tmp_path: Path,
) -> None:
    library = FileSystemElementLibrary(tmp_path)
    cabinet = library.create(
        ObjectElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "reading-cabinet",
                "name": "Reading Cabinet",
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
                        "openable": {"initially_locked": False},
                        "readable": {"document_id": "cabinet-label"},
                    },
                    "initial_open": True,
                    "owner_id": "library",
                    "custodian_id": "librarian",
                },
            }
        )
    )
    room = library.create(
        RoomElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "reading-room",
                "name": "Reading Room",
                "kind": "room",
                "room_type": "READING",
                "width": 3,
                "height": 2,
                "spatial_metric": {"microcells_per_legacy_cell": 9},
                "objects": [
                    {
                        "key": "cabinet",
                        "element": _reference(
                            ElementKind.OBJECT,
                            cabinet.id,
                            element_content_hash(cabinet),
                        ),
                        "placement": {
                            "anchor": {"x": 10, "y": 4},
                            "orientation": "EAST",
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                    }
                ],
            }
        )
    )
    building = library.create(
        BuildingElementDefinition.model_validate(
            {
                "schema_version": 4,
                "id": "library",
                "name": "Library",
                "kind": "building",
                "rooms": [
                    {
                        "key": "reading",
                        "element": _reference(
                            ElementKind.ROOM,
                            room.id,
                            element_content_hash(room),
                        ),
                    }
                ],
                "entrances": [
                    {
                        "key": "front",
                        "room_key": "reading",
                        "local_coordinate": {"x": 0, "y": 0},
                        "door_object_id": "reading-cabinet",
                    }
                ],
            }
        )
    )
    source = ScenarioSourceDefinition.model_validate(
        {
            "schema_version": 8,
            "name": "Physical library",
            "world": {
                "type": "city",
                "city": {
                    "id": "city",
                    "name": "City",
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
                                "id": "main-library",
                                "element": _reference(
                                    ElementKind.BUILDING,
                                    building.id,
                                    element_content_hash(building),
                                ),
                                "city_position": {"x": 5, "y": 5},
                                "entrance_node_ids": {
                                    "front": "library-node"
                                },
                            }
                        ],
                    }
                ],
                "transport": {
                    "nodes": [
                        {
                            "id": "library-node",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 5, "y": 5},
                            "place_id": "main-library",
                        }
                    ]
                },
            },
        }
    )

    resolved = resolve_scenario(source, library)
    assert resolved.scenario.schema_version == 8
    assert resolved.scenario.world is not None
    physical_object = resolved.scenario.world.objects[0]
    assert physical_object.id == "main-library.reading.cabinet"
    assert physical_object.definition_id == "reading-cabinet"
    assert physical_object.placement is not None
    assert physical_object.placement.parent_relation.parent_id == (
        "main-library.reading"
    )
    assert resolved.scenario.world.buildings[0].entrances[0].door_object_id == (
        physical_object.id
    )

    runner = create_runner(resolved.scenario)
    object_id = physical_object.id
    identity = runner.registry.get_component(
        object_id,
        PhysicalObjectIdentityComponent,
    )
    state = runner.registry.get_component(object_id, PhysicalStateComponent)
    relation = runner.registry.get_component(
        object_id,
        SpatialParentRelationComponent,
    )
    assert identity.definition_id == "reading-cabinet"
    assert state.pose.anchor == Coordinate(10, 4)
    assert state.movement_obstruction is MovementObstruction.NONE
    assert state.vision_obstruction is VisionObstruction.TRANSPARENT
    assert relation.parent_id == "main-library.reading"
    assert runner.registry.get_component(object_id, OpenableComponent).is_open
    index = runner.registry.get_resource(SpatialIndex)
    assert index.entries("main-library.reading")[0].entity_id == object_id
    assert index.hard_occupants(
        "main-library.reading",
        Coordinate(10, 4),
    ) == ()
