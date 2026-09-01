from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.scenario import create_runner
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    load_and_resolve_scenario,
    resolve_scenario,
)

ROOT = Path(__file__).parents[1]
CITY_SEMANTIC_HASHES = {
    "greyford-office-evening.json": (
        "b20a83bcba28969669d0bef695fd19d3cf9f3b840053fffbd47eb07c54545f47"
    ),
    "greyford-rivermarket-exchange.json": (
        "d11af3c989a79e61585afe014b55cc98fac0b2910e7dbc190c975208b27b9395"
    ),
    "sparse-city-car-demo.json": (
        "60d7fe1bb7ab3b50133a678dd4e14408c4eefcbffc331a7b915c0eceefab06b8"
    ),
}


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


def _library(tmp_path: Path) -> tuple[
    FileSystemElementLibrary,
    BuildingElementDefinition,
]:
    library = FileSystemElementLibrary(tmp_path)
    role = library.create(
        NpcRoleElementDefinition(
            id="restaurant-server",
            name="Restaurant Server",
        )
    )
    table = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "dining-table",
                "name": "Dining Table",
                "kind": "object",
                "object_type": "affordance",
                "actions": [
                    {
                        "action": "EAT",
                        "duration": 60,
                        "effect": {"satiety_delta": 20},
                    }
                ],
            }
        )
    )
    checkout = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "restaurant-checkout",
                "name": "Restaurant Checkout",
                "kind": "object",
                "object_type": "transaction",
                "offers": [
                    {
                        "id": "buy-meal",
                        "name": "Buy meal",
                        "character_gives": [
                            {"item_id": "credit", "quantity": 5}
                        ],
                        "character_receives": [
                            {"item_id": "meal", "quantity": 1}
                        ],
                    }
                ],
                "holdings": {"meal": 10},
                "operation": "STAFFED",
                "npc_role": _reference(
                    ElementKind.NPC_ROLE,
                    role.id,
                    element_content_hash(role),
                ),
            }
        )
    )
    room = library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "standard-dining-room",
                "name": "Dining Room",
                "kind": "room",
                "room_type": "DINING",
                "width": 5,
                "height": 4,
                "objects": [
                    {
                        "key": "table",
                        "element": _reference(
                            ElementKind.OBJECT,
                            table.id,
                            element_content_hash(table),
                        ),
                        "position": {"x": 2, "y": 2},
                    },
                    {
                        "key": "checkout",
                        "element": _reference(
                            ElementKind.OBJECT,
                            checkout.id,
                            element_content_hash(checkout),
                        ),
                        "position": {"x": 4, "y": 2},
                        "staff_position": {"x": 4, "y": 1},
                    },
                ],
            }
        )
    )
    building = library.create(
        BuildingElementDefinition.model_validate(
            {
                "id": "standard-restaurant",
                "name": "Standard Restaurant",
                "kind": "building",
                "rooms": [
                    {
                        "key": "dining",
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
                        "room_key": "dining",
                        "local_coordinate": {"x": 0, "y": 2},
                    }
                ],
            }
        )
    )
    return library, building


def _source(
    building: BuildingElementDefinition,
) -> ScenarioSourceDefinition:
    building_reference = _reference(
        ElementKind.BUILDING,
        building.id,
        element_content_hash(building),
    )
    return ScenarioSourceDefinition.model_validate(
        {
            "schema_version": 3,
            "name": "Two restaurants",
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
                        "max_x": 100,
                        "max_y": 100,
                    },
                },
                "city_zones": [
                    {
                        "id": "zone-central",
                        "name": "Central",
                        "center": {"x": 50, "y": 50},
                        "buildings": [
                            {
                                "id": "restaurant-one",
                                "element": building_reference,
                                "city_position": {"x": 20, "y": 50},
                                "entrance_node_ids": {
                                    "front": "node-restaurant-one"
                                },
                            },
                            {
                                "id": "restaurant-two",
                                "element": building_reference,
                                "city_position": {"x": 80, "y": 50},
                                "entrance_node_ids": {
                                    "front": "node-restaurant-two"
                                },
                                "overrides": {
                                    "name": "Late Restaurant",
                                    "room_overrides": {
                                        "dining": {
                                            "object_overrides": {
                                                "checkout": {
                                                    "holdings": {"meal": 3}
                                                }
                                            }
                                        }
                                    },
                                },
                            },
                        ],
                    }
                ],
                "transport": {
                    "nodes": [
                        {
                            "id": "node-restaurant-one",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 20, "y": 50},
                            "place_id": "restaurant-one",
                        },
                        {
                            "id": "node-restaurant-two",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 80, "y": 50},
                            "place_id": "restaurant-two",
                        },
                    ],
                    "edges": [
                        {
                            "id": "street",
                            "from_node_id": "node-restaurant-one",
                            "to_node_id": "node-restaurant-two",
                            "allowed_modes": ["WALK"],
                            "distance_meters": 60,
                            "geometry": [
                                {"x": 20, "y": 50},
                                {"x": 80, "y": 50},
                            ],
                            "bidirectional": True,
                        }
                    ],
                },
            },
        }
    )


def test_resolver_materializes_repeated_buildings_with_distinct_ids(
    tmp_path: Path,
) -> None:
    library, building = _library(tmp_path)
    resolved = resolve_scenario(_source(building), library)
    world = resolved.scenario.world
    assert world is not None and hasattr(world, "buildings")
    city = world

    assert [item.name for item in city.buildings] == [
        "Standard Restaurant",
        "Late Restaurant",
    ]
    assert [room.id for room in city.rooms] == [
        "restaurant-one.dining",
        "restaurant-two.dining",
    ]
    first = city.rooms[0].world
    second = city.rooms[1].world
    assert [station.id for station in first.stations] == [
        "restaurant-one.dining.table"
    ]
    assert [point.id for point in second.transaction_points] == [
        "restaurant-two.dining.checkout"
    ]
    assert first.transaction_points[0].holdings == {"meal": 10}
    assert second.transaction_points[0].holdings == {"meal": 3}
    assert [role.id for role in resolved.scenario.npc_roles] == [
        "restaurant-server"
    ]
    assert set(resolved.elements) == {
        "dining-table",
        "restaurant-checkout",
        "restaurant-server",
        "standard-dining-room",
        "standard-restaurant",
    }


def test_resolver_rejects_hash_drift_and_unknown_override_keys(
    tmp_path: Path,
) -> None:
    library, building = _library(tmp_path)
    source = _source(building)
    library.update(
        building.id,
        building.model_copy(update={"name": "Changed"}),
        element_content_hash(building),
    )
    with pytest.raises(ScenarioResolutionError, match="content hash changed"):
        resolve_scenario(source, library)

    library, building = _library(tmp_path / "other")
    raw = _source(building).model_dump(mode="json")
    raw["world"]["city_zones"][0]["buildings"][0]["overrides"] = {
        "room_overrides": {"missing": {}}
    }
    with pytest.raises(
        ScenarioResolutionError,
        match="unknown room keys",
    ):
        resolve_scenario(ScenarioSourceDefinition.model_validate(raw), library)


def test_loader_rejects_schema_v2_with_explicit_message(
    tmp_path: Path,
) -> None:
    scenario_path = tmp_path / "legacy.json"
    scenario_path.write_text(
        '{"schema_version": 2, "name": "Legacy"}',
        encoding="utf-8",
    )
    library = FileSystemElementLibrary(tmp_path / "elements")

    with pytest.raises(
        ScenarioResolutionError,
        match="schema-version-2 scenarios are not supported",
    ):
        load_and_resolve_scenario(scenario_path, library)


def test_all_repository_sources_resolve_and_run() -> None:
    library = FileSystemElementLibrary(ROOT / "elements")
    paths = [
        *sorted((ROOT / "scenarios").glob("*.json")),
        ROOT / "src" / "stage0_sim" / "web" / "demo.json",
    ]
    turns = tuple(
        ModelTurn(
            text=None,
            tool_calls=(
                ModelToolCall(
                    call_id=f"call-{index}",
                    name="wait",
                    arguments={"duration_seconds": 1},
                ),
            ),
            finish_reason="tool_calls",
            provider="scripted",
            model="repository-source-smoke",
            latency_ms=0,
        )
        for index in range(20)
    )

    assert paths
    library.list()
    for path in paths:
        source = ScenarioSourceDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert source.schema_version == 3
        resolved = resolve_scenario(source, library)
        runner = create_runner(
            resolved.scenario,
            run_id=f"source-smoke-{path.stem}",
            model_client=ScriptedModelClient(turns),
        )
        runner.run_for(1)
        assert runner.clock.tick == 1


@pytest.mark.parametrize(
    ("filename", "expected_hash"),
    CITY_SEMANTIC_HASHES.items(),
)
def test_migrated_city_source_preserves_materialized_semantics(
    filename: str,
    expected_hash: str,
) -> None:
    resolved = load_and_resolve_scenario(
        ROOT / "scenarios" / filename,
        FileSystemElementLibrary(ROOT / "elements"),
    )
    payload = resolved.scenario.model_dump(mode="json")
    payload.pop("schema_version")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected_hash
