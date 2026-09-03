import json
import re
from pathlib import Path
from typing import Any

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.characters import CharacterDefinition
from stage0_sim.application.elements import (
    ElementKind,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.migrations.catalog import (
    CatalogMigrationOptions,
    migrate_catalog,
)
from stage0_sim.application.scenario import create_runner
from stage0_sim.application.scenario_resolution import resolve_scenario
from stage0_sim.config import Settings
from tests.helpers.paths import (
    EXAMPLE_CHARACTERS,
    EXAMPLE_ELEMENTS,
    EXAMPLE_SCENARIOS,
    EXAMPLES_ROOT,
    PACKAGED_DEMO,
    REPOSITORY_ROOT,
    SCENARIO_FIXTURES,
)

REFERENCE_PATTERN = re.compile(
    r"`((?:scenarios|characters|elements)[\\/][^`]+\.json)`"
)
REPOSITORY_EXAMPLE_PATTERN = re.compile(
    r"(examples[\\/](?:scenarios|characters|elements)[\\/][a-z0-9._-]+\.json)"
)


def _json_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                *EXAMPLES_ROOT.rglob("*.json"),
                *SCENARIO_FIXTURES.rglob("*.json"),
                *PACKAGED_DEMO.parent.glob("*.json"),
            ),
            key=lambda path: path.as_posix(),
        )
    )


def _references(value: object) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"kind", "id", "content_hash"} <= value.keys():
            found.append(value)
        for child in value.values():
            found.extend(_references(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_references(child))
    return tuple(found)


def test_all_retained_json_is_parseable() -> None:
    paths = _json_files()

    assert paths
    for path in paths:
        json.loads(path.read_text(encoding="utf-8"))


def test_catalog_ids_filenames_and_current_schemas_are_coherent() -> None:
    character_library = FileSystemCharacterLibrary(EXAMPLE_CHARACTERS)
    element_library = FileSystemElementLibrary(EXAMPLE_ELEMENTS)

    characters = character_library.list()
    elements = element_library.list()
    assert {item.id for item in characters} == {
        path.stem for path in EXAMPLE_CHARACTERS.glob("*.json")
    }
    assert {item.id for item in elements} == {
        path.stem for path in EXAMPLE_ELEMENTS.glob("*.json")
    }
    assert {item.schema_version for item in characters} == {2}
    assert {item.schema_version for item in elements} == {4}

    character_ids = {item.id for item in characters}
    for path in EXAMPLE_SCENARIOS.glob("*.json"):
        source = ScenarioSourceDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert source.schema_version == 8
        assert source.name == path.stem
        for entity in source.entities:
            slot = entity.components.get("character_slot")
            if isinstance(slot, dict):
                character_id = slot.get("default_character_id")
                if character_id is not None:
                    assert character_id in character_ids

    fixture = ScenarioSourceDefinition.model_validate_json(
        (SCENARIO_FIXTURES / "scripted-tool-cognition.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture.schema_version == 8
    assert fixture.name == "scripted-tool-cognition"

    demo = ScenarioSourceDefinition.model_validate_json(
        PACKAGED_DEMO.read_text(encoding="utf-8")
    )
    assert demo.schema_version == 8
    demo_character = CharacterDefinition.model_validate_json(
        PACKAGED_DEMO.with_name("demo-character.json").read_text(
            encoding="utf-8"
        )
    )
    assert demo_character.schema_version == 2
    assert {
        entity.components["character_slot"]["default_character_id"]
        for entity in demo.entities
    } == {demo_character.id}


def test_element_references_and_semantic_hashes_resolve() -> None:
    library = FileSystemElementLibrary(EXAMPLE_ELEMENTS)

    for summary in library.list():
        element = library.get(summary.id)
        raw = element.model_dump(mode="json")
        for reference in _references(raw):
            referenced = library.get(
                str(reference["id"]),
                ElementKind(str(reference["kind"])),
            )
            assert element_content_hash(referenced) == reference["content_hash"]


def test_every_user_scenario_resolves_and_runs_one_tick() -> None:
    library = FileSystemElementLibrary(EXAMPLE_ELEMENTS)
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

    for path in sorted(EXAMPLE_SCENARIOS.glob("*.json")):
        source = ScenarioSourceDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        resolved = resolve_scenario(source, library)
        runner = create_runner(
            resolved.scenario,
            run_id=f"source-smoke-{path.stem}",
            model_client=ScriptedModelClient(turns),
        )
        runner.run_for(1)
        assert runner.clock.tick == 1


def test_documented_examples_are_reachable_and_complete() -> None:
    documentation = (EXAMPLES_ROOT / "README.md").read_text(encoding="utf-8")
    documented = {
        EXAMPLES_ROOT / relative.replace("\\", "/")
        for relative in REFERENCE_PATTERN.findall(documentation)
    }

    assert documented
    assert all(path.is_file() for path in documented)
    assert {
        path for path in EXAMPLE_SCENARIOS.glob("*.json")
    } <= documented
    assert {
        path for path in EXAMPLE_CHARACTERS.glob("*.json")
    } <= documented

    active_documents = (
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / ".github" / "copilot-instructions.md",
        *sorted((REPOSITORY_ROOT / "docs").glob("*.md")),
    )
    repository_references = {
        REPOSITORY_ROOT / relative.replace("\\", "/")
        for document in active_documents
        for relative in REPOSITORY_EXAMPLE_PATTERN.findall(
            document.read_text(encoding="utf-8")
        )
    }
    assert repository_references
    assert all(path.is_file() for path in repository_references)


def test_runtime_catalog_defaults_are_ignored_data_paths() -> None:
    fields = Settings.model_fields

    assert fields["character_directory"].default == Path("data/characters")
    assert fields["scenario_directory"].default == Path("data/scenarios")
    assert fields["element_directory"].default == Path("data/elements")
    assert "data/" in (
        REPOSITORY_ROOT / ".gitignore"
    ).read_text(encoding="utf-8").splitlines()
    environment_example = (
        REPOSITORY_ROOT / ".env.example"
    ).read_text(encoding="utf-8")
    assert "STAGE0_CHARACTER_DIRECTORY=data/characters" in environment_example
    assert "STAGE0_SCENARIO_DIRECTORY=data/scenarios" in environment_example
    assert "STAGE0_ELEMENT_DIRECTORY=data/elements" in environment_example
    assert not (REPOSITORY_ROOT / "data").resolve().is_relative_to(
        EXAMPLES_ROOT.resolve()
    )


def test_character_samples_validate_as_current_profiles() -> None:
    for path in EXAMPLE_CHARACTERS.glob("*.json"):
        character = CharacterDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert character.schema_version == 2
        assert character.id == path.stem


def test_repository_content_requires_no_migration() -> None:
    examples = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=EXAMPLE_CHARACTERS,
            elements_dir=EXAMPLE_ELEMENTS,
            scenarios_dir=EXAMPLE_SCENARIOS,
        )
    )
    packaged = migrate_catalog(
        CatalogMigrationOptions(
            scenarios_dir=PACKAGED_DEMO.parent,
        )
    )
    current_fixture = migrate_catalog(
        CatalogMigrationOptions(
            scenarios_dir=SCENARIO_FIXTURES,
        )
    )

    for report in (examples, packaged, current_fixture):
        assert report.succeeded, report.errors
        assert report.changed_count == 0
