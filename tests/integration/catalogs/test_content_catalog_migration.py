import json
import shutil
from pathlib import Path

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.application.elements import ScenarioSourceDefinition
from stage0_sim.application.migrations.catalog import (
    CatalogMigrationOptions,
    migrate_catalog,
)
from stage0_sim.application.scenario import create_runner
from stage0_sim.application.scenario_resolution import resolve_scenario

FIXTURES = Path("tests/fixtures/migrations/catalog")


def test_catalog_check_is_read_only_and_rewrites_hashes_transitively() -> None:
    legacy = FIXTURES / "legacy"
    before = {
        path: path.read_bytes()
        for path in sorted(legacy.rglob("*.json"))
    }

    report = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=legacy / "characters",
            elements_dir=legacy / "elements",
            scenarios_dir=legacy / "scenarios",
        )
    )

    assert report.succeeded
    assert report.changed_count == 6
    assert [entry.resource_id for entry in report.manifest] == [
        "legacy-person",
        "legacy-building",
        "legacy-object",
        "legacy-role",
        "legacy-room",
        "legacy-city",
    ]
    assert before == {
        path: path.read_bytes()
        for path in sorted(legacy.rglob("*.json"))
    }


def test_catalog_output_is_complete_current_and_runnable(tmp_path: Path) -> None:
    legacy = FIXTURES / "legacy"
    output = tmp_path / "migrated"
    report = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=legacy / "characters",
            elements_dir=legacy / "elements",
            scenarios_dir=legacy / "scenarios",
            mode="output",
            output_dir=output,
        )
    )
    assert report.succeeded
    assert output.is_dir()

    current = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=output / "characters",
            elements_dir=output / "elements",
            scenarios_dir=output / "scenarios",
        )
    )
    assert current.succeeded
    assert current.changed_count == 0
    source = ScenarioSourceDefinition.model_validate_json(
        (output / "scenarios/legacy-city.json").read_text(encoding="utf-8")
    )
    resolved = resolve_scenario(
        source,
        FileSystemElementLibrary(output / "elements"),
    )
    runner = create_runner(resolved.scenario)
    runner.run_for(1)
    assert runner.clock.tick == 1


def test_catalog_write_creates_backup_and_updates_sources(tmp_path: Path) -> None:
    source = tmp_path / "catalog"
    shutil.copytree(FIXTURES / "legacy", source)
    backup = tmp_path / "backup"

    report = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=source / "characters",
            elements_dir=source / "elements",
            scenarios_dir=source / "scenarios",
            mode="write",
            backup_dir=backup,
        )
    )

    assert report.succeeded
    assert backup.is_dir()
    assert json.loads(
        (backup / "elements/legacy-object.json").read_text(encoding="utf-8")
    )["schema_version"] == 1
    assert json.loads(
        (source / "elements/legacy-object.json").read_text(encoding="utf-8")
    )["schema_version"] == 3


def test_invalid_catalog_never_writes_or_creates_backup(tmp_path: Path) -> None:
    characters = tmp_path / "characters"
    characters.mkdir()
    valid = characters / "valid.json"
    valid.write_text(
        '{"schema_version":1,"id":"valid","identity":{"display_name":"Valid"}}',
        encoding="utf-8",
    )
    malformed = characters / "malformed.json"
    malformed.write_text('{"schema_version":1,', encoding="utf-8")
    before = {path: path.read_bytes() for path in characters.glob("*.json")}
    backup = tmp_path / "backup"

    report = migrate_catalog(
        CatalogMigrationOptions(
            characters_dir=characters,
            mode="write",
            backup_dir=backup,
        )
    )

    assert not report.succeeded
    assert "malformed JSON" in report.errors[0]
    assert not backup.exists()
    assert before == {path: path.read_bytes() for path in characters.glob("*.json")}
