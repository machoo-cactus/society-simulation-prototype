import json
from pathlib import Path
from typing import Any

from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    element_content_hash,
)
from stage0_sim.application.migrations.models import (
    MigrationContext,
    ResourceKind,
)
from stage0_sim.application.migrations.registry import (
    CONTENT_MIGRATION_REGISTRY,
)

FIXTURES = Path("tests/fixtures/migrations/catalog")
CURRENT = FIXTURES / "current"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_registry_has_one_deterministic_path_to_every_current_version() -> None:
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.CHARACTER, 1) == (1, 2)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.CHARACTER, 2) == (2,)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.ELEMENT, 1) == (1, 2, 3, 4, 5)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.ELEMENT, 2) == (2, 3, 4, 5)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.ELEMENT, 3) == (3, 4, 5)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.ELEMENT, 4) == (4, 5)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.ELEMENT, 5) == (5,)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 4) == (
        4,
        5,
        6,
        7,
        8,
        9,
    )
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 5) == (
        5,
        6,
        7,
        8,
        9,
    )
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 6) == (
        6,
        7,
        8,
        9,
    )
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 7) == (7, 8, 9)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 8) == (8, 9)
    assert CONTENT_MIGRATION_REGISTRY.path(ResourceKind.SCENARIO, 9) == (9,)
    CONTENT_MIGRATION_REGISTRY.validate_integrity()


def test_character_v1_to_v2_matches_exact_golden_and_preserves_lossy_age() -> None:
    raw = _read(FIXTURES / "legacy/characters/legacy-person.json")
    expected = _read(CURRENT / "characters/legacy-person.json")

    first = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.CHARACTER,
        "legacy-person",
        raw,
    )
    second = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.CHARACTER,
        "legacy-person",
        raw,
    )

    assert first.succeeded
    assert first.canonical_json == expected
    assert second == first
    assert first.warnings == [
        "preserved identity.age because an age cannot be converted to a "
        "birth date without a reference date"
    ]
    assert expected["body_measurements"]["height_cm"] == 178.0
    migration_section = expected["custom_sections"][-1]
    assert migration_section["id"] == "migration-v1-legacy"
    assert migration_section["fields"][0]["value"] == 34


def test_all_element_v1_kinds_match_exact_golden_outputs() -> None:
    context = MigrationContext()
    order = ("legacy-role", "legacy-object", "legacy-room", "legacy-building")
    for element_id in order:
        raw = _read(FIXTURES / f"legacy/elements/{element_id}.json")
        if element_id in {"legacy-room", "legacy-building"}:
            references = _references(raw)
            for reference in references:
                reference["content_hash"] = context.element_hashes[reference["id"]]
        result = CONTENT_MIGRATION_REGISTRY.migrate(
            ResourceKind.ELEMENT,
            element_id,
            raw,
            context,
        )
        expected = _read(CURRENT / f"elements/{element_id}.json")
        assert result.succeeded, result.errors
        assert result.canonical_json == expected
        element = SCENARIO_ELEMENT_ADAPTER.validate_python(expected)
        context.element_definitions[element_id] = expected
        context.element_hashes[element_id] = element_content_hash(element)

    physical = context.element_definitions["legacy-object"]["physical"]
    assert physical["footprint"]["cells"] == [{"x": 0, "y": 0}]
    assert physical["obstruction"] == {
        "hearing": "PASS",
        "movement": "NONE",
        "smell": "PASS",
        "vision": "TRANSPARENT",
    }
    placement = context.element_definitions["legacy-room"]["objects"][0]
    assert placement["position"] == {"x": 0, "y": 0}
    assert placement["placement"]["anchor"] == {"x": 4, "y": 4}


def test_scenario_v4_to_v8_rewrites_all_element_hashes_exactly() -> None:
    context = _expected_element_context()
    raw = _read(FIXTURES / "legacy/scenarios/legacy-city.json")
    expected = _read(CURRENT / "scenarios/legacy-city.json")

    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.SCENARIO,
        "legacy-city",
        raw,
        context,
    )

    assert result.succeeded, result.errors
    assert result.canonical_json == expected
    assert "$.world.city_zones[0].buildings[0].element.content_hash" in (
        result.changed_paths
    )


def test_scenario_v6_to_v7_adds_engage_only_to_the_previous_default() -> None:
    default = {
        "schema_version": 6,
        "name": "Default tools",
        "cognition": {
            "tool_allowlist": [
                "navigate_to",
                "perform",
                "say",
                "wait",
                "skip",
                "transact",
                "check_environment",
            ]
        },
    }
    migrated_default = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.SCENARIO,
        "default-tools",
        default,
    )
    assert migrated_default.succeeded, migrated_default.errors
    assert migrated_default.canonical_json is not None
    assert "engage" in migrated_default.canonical_json["cognition"]["tool_allowlist"]

    restricted = {
        "schema_version": 6,
        "name": "Restricted tools",
        "cognition": {"tool_allowlist": ["say", "wait"]},
    }
    migrated_restricted = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.SCENARIO,
        "restricted-tools",
        restricted,
    )
    assert migrated_restricted.succeeded, migrated_restricted.errors
    assert migrated_restricted.canonical_json is not None
    assert migrated_restricted.canonical_json["cognition"]["tool_allowlist"] == [
        "say",
        "wait",
    ]


def test_scenario_v7_to_v8_adds_text_tools_only_to_previous_defaults() -> None:
    default_tools = [
        "navigate_to",
        "perform",
        "say",
        "engage",
        "wait",
        "skip",
        "transact",
        "check_environment",
    ]
    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.SCENARIO,
        "text-tools",
        {
            "schema_version": 7,
            "name": "Text tools",
            "cognition": {"tool_allowlist": default_tools},
            "entities": [
                {
                    "id": "actor",
                    "components": {
                        "controller": {"tool_allowlist": default_tools}
                    },
                }
            ],
        },
    )

    assert result.succeeded, result.errors
    assert result.canonical_json is not None
    assert result.canonical_json["schema_version"] == 9
    assert result.canonical_json["text_content"] == {
        "artifacts": [],
        "collections": [],
        "addresses": [],
        "groups": [],
    }
    assert result.canonical_json["cognition"]["tool_allowlist"][-2:] == [
        "read_text",
        "write_text",
    ]
    assert result.canonical_json["entities"][0]["components"]["controller"][
        "tool_allowlist"
    ][-2:] == ["read_text", "write_text"]


def test_scenario_v8_to_v9_adds_neutral_homeostasis_factors() -> None:
    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.SCENARIO,
        "homeostasis-v9",
        {
            "schema_version": 8,
            "name": "Homeostasis migration",
            "entities": [
                {
                    "id": "actor",
                    "components": {
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 70,
                            "stress": 20,
                        }
                    },
                }
            ],
        },
    )

    assert result.succeeded, result.errors
    assert result.canonical_json is not None
    assert result.canonical_json["schema_version"] == 9
    state = result.canonical_json["entities"][0]["components"]["homeostasis"]
    assert state["hydration"] == 100
    assert state["social_connection"] == 50
    assert state["happiness"] == 50
    assert state["fear"] == 0
    assert result.canonical_json["system1"]["enabled_drives"] == [
        "SATIETY",
        "ENERGY",
        "STRESS",
    ]


def test_current_version_check_is_a_canonical_noop() -> None:
    expected = _read(CURRENT / "characters/legacy-person.json")
    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.CHARACTER,
        "legacy-person",
        expected,
    )
    assert result.succeeded
    assert result.canonical_json == expected
    assert result.changed_paths == []
    assert result.warnings == []


def test_unsupported_and_malformed_versions_fail_explicitly() -> None:
    unsupported = _read(
        Path("tests/fixtures/migrations/invalid/unsupported-character.json")
    )
    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.CHARACTER,
        "unsupported-character",
        unsupported,
    )
    assert not result.succeeded
    assert "unsupported character schema version 0" in result.errors[0]

    result = CONTENT_MIGRATION_REGISTRY.migrate(
        ResourceKind.ELEMENT,
        "missing-version",
        {"id": "missing-version"},
    )
    assert not result.succeeded
    assert result.errors == ["schema_version must be an integer"]


def _expected_element_context() -> MigrationContext:
    context = MigrationContext()
    for path in sorted((CURRENT / "elements").glob("*.json")):
        raw = _read(path)
        element = SCENARIO_ELEMENT_ADAPTER.validate_python(raw)
        context.element_definitions[element.id] = raw
        context.element_hashes[element.id] = element_content_hash(element)
    return context


def test_element_v2_to_v3_matches_exact_current_golden() -> None:
    context = MigrationContext()
    order = ("legacy-role", "legacy-object", "legacy-room", "legacy-building")
    for element_id in order:
        raw = _read(FIXTURES / f"expected/elements/{element_id}.json")
        references = _references(raw)
        for reference in references:
            reference["content_hash"] = context.element_hashes[reference["id"]]
        result = CONTENT_MIGRATION_REGISTRY.migrate(
            ResourceKind.ELEMENT,
            element_id,
            raw,
            context,
        )
        expected = _read(CURRENT / f"elements/{element_id}.json")
        assert result.succeeded, result.errors
        assert result.canonical_json == expected
        element = SCENARIO_ELEMENT_ADAPTER.validate_python(expected)
        context.element_definitions[element_id] = expected
        context.element_hashes[element_id] = element_content_hash(element)


def _references(value: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"kind", "id", "content_hash"} <= value.keys():
            found.append(value)
        for child in value.values():
            found.extend(_references(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_references(child))
    return found
