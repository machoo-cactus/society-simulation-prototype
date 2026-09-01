from __future__ import annotations

import pytest
from pydantic import ValidationError

from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    BuildingElementDefinition,
    ElementKind,
    ElementReference,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    derive_instance_id,
    element_content_hash,
)


def _reference(
    kind: ElementKind,
    element_id: str,
    content_hash: str = "0" * 64,
) -> dict[str, str]:
    return {
        "kind": kind.value,
        "id": element_id,
        "content_hash": content_hash,
    }


def test_element_hash_and_instance_ids_are_deterministic() -> None:
    role = NpcRoleElementDefinition(
        id="cashier",
        name="Cashier",
        briefing="Serve customers in request order.",
    )

    assert element_content_hash(role) == element_content_hash(
        SCENARIO_ELEMENT_ADAPTER.validate_python(
            role.model_dump(mode="json")
        )
    )
    assert derive_instance_id("restaurant-001", "checkout") == (
        "restaurant-001.checkout"
    )
    with pytest.raises(ValueError, match="local key"):
        derive_instance_id("restaurant-001", "Checkout Counter")


def test_element_references_are_typed_and_hash_pinned() -> None:
    reference = ElementReference.model_validate(
        _reference(ElementKind.ROOM, "standard-dining-room")
    )

    assert reference.kind is ElementKind.ROOM
    with pytest.raises(ValidationError, match="64"):
        ElementReference.model_validate(
            {
                "kind": "room",
                "id": "standard-dining-room",
                "content_hash": "unpinned",
            }
        )


def test_room_layout_rejects_collisions_and_wrong_reference_kinds() -> None:
    with pytest.raises(ValidationError, match="must reference an object"):
        RoomElementDefinition.model_validate(
            {
                "id": "bad-room",
                "name": "Bad room",
                "kind": "room",
                "room_type": "DINING",
                "width": 3,
                "height": 3,
                "objects": [
                    {
                        "key": "table",
                        "element": _reference(ElementKind.ROOM, "table"),
                        "position": {"x": 1, "y": 1},
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="blocked or occupied"):
        RoomElementDefinition.model_validate(
            {
                "id": "colliding-room",
                "name": "Colliding room",
                "kind": "room",
                "room_type": "DINING",
                "width": 3,
                "height": 3,
                "blocked": [{"x": 1, "y": 1}],
                "objects": [
                    {
                        "key": "table",
                        "element": _reference(ElementKind.OBJECT, "table"),
                        "position": {"x": 1, "y": 1},
                    }
                ],
            }
        )


def test_building_structure_requires_valid_room_keys() -> None:
    with pytest.raises(ValidationError, match="unknown room missing"):
        BuildingElementDefinition.model_validate(
            {
                "id": "restaurant",
                "name": "Restaurant",
                "kind": "building",
                "rooms": [
                    {
                        "key": "dining",
                        "element": _reference(
                            ElementKind.ROOM,
                            "standard-dining-room",
                        ),
                    }
                ],
                "entrances": [
                    {
                        "key": "front",
                        "room_key": "missing",
                        "local_coordinate": {"x": 0, "y": 1},
                    }
                ],
            }
        )


def test_object_capabilities_are_closed_and_staffed_objects_require_roles() -> None:
    with pytest.raises(ValidationError, match="require an npc_role"):
        ObjectElementDefinition.model_validate(
            {
                "id": "checkout",
                "name": "Checkout",
                "kind": "object",
                "object_type": "transaction",
                "operation": "STAFFED",
                "offers": [
                    {
                        "id": "buy-meal",
                        "name": "Buy meal",
                        "character_receives": [
                            {"item_id": "meal", "quantity": 1}
                        ],
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="cannot define transaction"):
        ObjectElementDefinition.model_validate(
            {
                "id": "sofa",
                "name": "Sofa",
                "kind": "object",
                "object_type": "affordance",
                "actions": [
                    {
                        "action": "RELAX",
                        "duration": 10,
                        "effect": {"stress_delta": -1},
                    }
                ],
                "holdings": {"meal": 1},
            }
        )


def test_scenario_source_is_an_explicit_break_from_schema_v2() -> None:
    source = ScenarioSourceDefinition.model_validate(
        {
            "schema_version": 4,
            "name": "Reference scenario",
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
                        "id": "zone",
                        "name": "Zone",
                        "center": {"x": 5, "y": 5},
                    }
                ],
                "transport": {},
            },
        }
    )

    assert source.schema_version == 4
    with pytest.raises(ValidationError, match="4"):
        ScenarioSourceDefinition.model_validate(
            {"schema_version": 2, "name": "Legacy"}
        )
