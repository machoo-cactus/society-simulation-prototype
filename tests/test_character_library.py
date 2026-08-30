import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.characters import router as character_router
from stage0_sim.application.characters import (
    CharacterConflictError,
    CharacterDefinition,
    CharacterLibraryError,
    CharacterNotFoundError,
    character_content_hash,
    prepare_scenario,
)
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import ScenarioDefinition
from stage0_sim.cli import main
from stage0_sim.domain.components import CharacterProfileComponent


def character(character_id: str, display_name: str) -> CharacterDefinition:
    return CharacterDefinition.model_validate(
        {
            "schema_version": 1,
            "id": character_id,
            "identity": {"display_name": display_name},
            "motivations": {"goals": ["Finish the work"]},
        }
    )


def external_scenario(character_id: str) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "external-character",
            "world": {"width": 1, "height": 1},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "character_profile": {
                            "character_id": character_id
                        },
                    },
                }
            ],
        }
    )


def test_filesystem_character_library_crud_and_conflicts(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path / "characters")
    alex = library.create(character("alex", "Alex"))
    alex_hash = character_content_hash(alex)

    assert [summary.id for summary in library.list()] == ["alex"]
    assert library.get("alex").identity.display_name == "Alex"
    with pytest.raises(CharacterConflictError):
        library.create(alex)

    updated = library.update(
        "alex",
        character("alex", "Alex Chen"),
        alex_hash,
    )
    with pytest.raises(CharacterConflictError):
        library.update("alex", updated, alex_hash)

    renamed = library.rename(
        "alex",
        "alex-chen",
        character_content_hash(updated),
    )
    assert renamed.id == "alex-chen"
    with pytest.raises(CharacterNotFoundError):
        library.get("alex")

    library.delete("alex-chen", character_content_hash(renamed))
    assert library.list() == ()


def test_character_library_rejects_unsafe_or_mismatched_files(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path)
    with pytest.raises(CharacterLibraryError):
        library.get("../outside")

    (tmp_path / "wrong.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "different",
                "identity": {"display_name": "Wrong"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CharacterLibraryError, match="mismatched ID"):
        library.get("wrong")


def test_prepared_scenario_freezes_character_for_later_run(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        library = FileSystemCharacterLibrary(tmp_path / "characters")
        original = library.create(character("alex", "Original Alex"))
        manager = SimulationManager(
            dataset_store=SQLiteDatasetStore(tmp_path / "runs.sqlite3"),
            character_library=library,
        )
        scenario_id = manager.add_scenario(external_scenario("alex"))
        library.update(
            "alex",
            character("alex", "Changed Alex"),
            character_content_hash(original),
        )

        run_id = await manager.start_run(scenario_id, realtime=False)
        profile = manager.get_run(run_id).runner.registry.get_component(
            "agent-001",
            CharacterProfileComponent,
        )
        manifest = manager.get_scenario(scenario_id).dataset_payload()
        await manager.close()

        assert profile.display_name == "Original Alex"
        resolved = manifest["resolved_characters"]
        assert isinstance(resolved, dict)
        data = resolved["alex"]
        assert isinstance(data, dict)
        character_data = data["data"]
        assert isinstance(character_data, dict)
        identity = character_data["identity"]
        assert isinstance(identity, dict)
        assert identity["display_name"] == "Original Alex"

    asyncio.run(exercise())


def test_scenario_preparation_rejects_missing_character(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path)

    with pytest.raises(CharacterNotFoundError):
        prepare_scenario(external_scenario("missing"), library)


def test_character_api_crud_uses_content_hashes(tmp_path: Path) -> None:
    library = FileSystemCharacterLibrary(tmp_path)
    api = FastAPI()
    api.state.character_library = library
    api.include_router(character_router)

    with TestClient(api) as client:
        created = client.post(
            "/characters",
            json=character("alex", "Alex").model_dump(mode="json"),
        )
        content_hash = created.json()["content_hash"]
        assert created.status_code == 201
        assert client.get("/characters").json()["characters"][0]["id"] == "alex"

        updated_character = character("alex", "Alex Chen")
        updated = client.put(
            "/characters/alex",
            json={
                "expected_hash": content_hash,
                "character": updated_character.model_dump(mode="json"),
            },
        )
        assert updated.status_code == 200
        assert updated.json()["character"]["identity"]["display_name"] == (
            "Alex Chen"
        )
        stale = client.put(
            "/characters/alex",
            json={
                "expected_hash": content_hash,
                "character": updated_character.model_dump(mode="json"),
            },
        )
        assert stale.status_code == 409

        renamed = client.post(
            "/characters/alex/rename",
            json={
                "expected_hash": updated.json()["content_hash"],
                "new_id": "alex-chen",
            },
        )
        assert renamed.status_code == 200
        deleted = client.delete(
            "/characters/alex-chen",
            params={"expected_hash": renamed.json()["content_hash"]},
        )
        assert deleted.json()["status"] == "deleted"


def test_character_extract_cli_is_dry_run_first(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario_path = tmp_path / "legacy.json"
    scenario_path.write_text(
        json.dumps(
            {
                "name": "legacy",
                "character_profiles": {
                    "alex": {"identity": {"display_name": "Alex"}}
                },
                "entities": [
                    {
                        "id": "agent-001",
                        "components": {
                            "character_profile": {"profile_ref": "alex"}
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    character_directory = tmp_path / "characters"
    output = tmp_path / "migrated.json"

    assert main(
        [
            "characters",
            "extract",
            str(scenario_path),
            "--directory",
            str(character_directory),
            "--output",
            str(output),
        ]
    ) == 0
    assert not output.exists()
    assert not (character_directory / "alex.json").exists()
    assert "would write" in capsys.readouterr().out

    assert main(
        [
            "characters",
            "extract",
            str(scenario_path),
            "--directory",
            str(character_directory),
            "--output",
            str(output),
            "--write",
        ]
    ) == 0
    migrated = json.loads(output.read_text(encoding="utf-8"))
    assert "character_profiles" not in migrated
    assert migrated["entities"][0]["components"]["character_profile"] == {
        "character_id": "alex"
    }
    assert (character_directory / "alex.json").is_file()
