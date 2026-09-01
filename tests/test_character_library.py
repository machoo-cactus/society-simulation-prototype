import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.characters import router as character_router
from stage0_sim.application.characters import (
    CharacterConflictError,
    CharacterDefinition,
    CharacterLibraryError,
    CharacterNotFoundError,
    age_on,
    character_age,
    character_content_hash,
    prepare_scenario,
)
from stage0_sim.application.information import InformationStore
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.cli import main
from stage0_sim.domain.components import CharacterProfileComponent
from stage0_sim.domain.information import character_dossier_document_id


def character(character_id: str, display_name: str) -> CharacterDefinition:
    return CharacterDefinition.model_validate(
        {
            "schema_version": 1,
            "id": character_id,
            "identity": {"display_name": display_name},
            "motivations": {"values": ["Finish commitments"]},
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
                        "character_slot": {
                            "label": "Assigned character",
                            "default_character_id": character_id,
                        },
                        "planner": {"daily_goals": ["Finish the work"]},
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


def test_character_definition_rejects_scenario_owned_state() -> None:
    with pytest.raises(ValidationError, match="scenario-owned fields"):
        CharacterDefinition.model_validate(
            {
                "schema_version": 1,
                "id": "alex",
                "identity": {"display_name": "Alex"},
                "motivations": {
                    "values": ["accuracy"],
                    "goals": ["Finish this scenario"],
                },
            }
        )


def test_version_two_character_uses_canonical_hard_facts() -> None:
    character = CharacterDefinition.model_validate(
        {
            "schema_version": 2,
            "id": "alex",
            "identity": {
                "display_name": "Alex",
                "birth_date": "1990-02-14",
            },
            "body_measurements": {
                "measured_on": "2026-08-12",
                "height_cm": 178.0,
                "weight_kg": 69.4,
            },
            "financial_situation": {
                "as_of_date": "2026-08-31",
                "currency": "cad",
                "annual_gross_income": 128000,
            },
            "family": {
                "members": [
                    {
                        "member_id": "mei",
                        "display_name": "Mei",
                        "relationship": "Sister",
                        "living_status": "alive",
                    }
                ]
            },
            "health": {
                "as_of_date": "2026-08-12",
                "allergies": [
                    {
                        "substance": "Pollen",
                        "reaction": "Rhinitis",
                        "severity": "mild",
                    }
                ],
            },
        }
    )

    assert character.identity.birth_date == date(1990, 2, 14)
    assert character.body_measurements.height_cm == 178.0
    assert character.financial_situation.currency == "CAD"
    assert character.family.members[0].member_id == "mei"
    assert character.health.allergies[0].substance == "Pollen"

    with pytest.raises(ValidationError, match="identity.age is legacy"):
        CharacterDefinition.model_validate(
            {
                "schema_version": 2,
                "id": "legacy-age",
                "identity": {"display_name": "Legacy", "age": 34},
            }
        )
    with pytest.raises(ValidationError, match="appearance.height is legacy"):
        CharacterDefinition.model_validate(
            {
                "schema_version": 2,
                "id": "legacy-height",
                "identity": {"display_name": "Legacy"},
                "appearance": {"height": "178 cm"},
            }
        )


def test_age_is_derived_from_scenario_date_with_leap_day_rule() -> None:
    assert age_on(date(1990, 9, 1), date(2026, 8, 31)) == 35
    assert age_on(date(1990, 9, 1), date(2026, 9, 1)) == 36
    assert age_on(date(2000, 2, 29), date(2025, 2, 28)) == 24
    assert age_on(date(2000, 2, 29), date(2025, 3, 1)) == 25


def test_birth_date_age_constraints_use_scenario_calendar(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path / "characters")
    candidate = library.create(
        CharacterDefinition.model_validate(
            {
                "schema_version": 2,
                "id": "dated-character",
                "identity": {
                    "display_name": "Dated Character",
                    "birth_date": "1990-09-01",
                },
            }
        )
    )
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "dated-constraint",
            "calendar": {"start_datetime": "2026-08-31T09:00:00+00:00"},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "character_slot": {
                            "label": "Thirty-five year old",
                            "default_character_id": candidate.id,
                            "constraints": {
                                "minimum_age": 35,
                                "maximum_age": 35,
                            },
                        }
                    },
                }
            ],
        }
    )

    assert prepare_scenario(scenario, library).assignments == {
        "agent-001": candidate.id
    }
    assert character_age(candidate, date(2026, 8, 31)) == 35

    without_calendar = scenario.model_copy(
        update={"calendar": None},
        deep=True,
    )
    with pytest.raises(
        CharacterLibraryError,
        match="scenario calendar is required",
    ):
        prepare_scenario(without_calendar, library)


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
        scenario_id = await manager.add_scenario(external_scenario("alex"))
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
        assert manifest["character_assignments"] == {
            "agent-001": "alex"
        }
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
        summary = client.get("/characters").json()["characters"][0]
        assert summary["id"] == "alex"
        assert summary["birth_date"] is None

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


def test_extensible_character_content_round_trips_and_freezes(
    tmp_path: Path,
) -> None:
    raw = {
        "schema_version": 1,
        "id": "alex",
        "template_id": "human-v1",
        "identity": {
            "display_name": "Alex",
            "birth_event": {
                "date": "1992-04-03",
                "place": {"id": "place-shanghai", "coordinates": [1, 2]},
            },
        },
        "capabilities": {
            "skills": ["analysis"],
            "driving": {
                "experience": "moderate",
                "licences": ["car"],
                "assessment": {"wet_weather": "cautious"},
            },
        },
        "experimental": {
            "spatial_reasoning": {
                "style": "landmark-oriented",
                "weights": [1, 2, {"future": True}],
            }
        },
    }
    library = FileSystemCharacterLibrary(tmp_path / "characters")
    created = library.create(CharacterDefinition.model_validate(raw))
    created_hash = character_content_hash(created)
    loaded_payload = library.get("alex").model_dump(mode="json")

    assert loaded_payload["identity"]["birth_event"] == (
        raw["identity"]["birth_event"]
    )
    assert loaded_payload["capabilities"]["driving"] == (
        raw["capabilities"]["driving"]
    )
    assert loaded_payload["experimental"] == raw["experimental"]

    api = FastAPI()
    api.state.character_library = library
    api.include_router(character_router)
    with TestClient(api) as client:
        response = client.get("/characters/alex")
        assert response.status_code == 200
        assert response.json()["character"]["experimental"] == raw["experimental"]
        updated_payload = response.json()["character"]
        updated_payload["identity"]["pronouns"] = "they/them"
        updated = client.put(
            "/characters/alex",
            json={
                "expected_hash": created_hash,
                "character": updated_payload,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["character"]["capabilities"]["driving"] == (
            raw["capabilities"]["driving"]
        )

    scenario = external_scenario("alex")
    prepared = prepare_scenario(scenario, library)
    frozen_payload = prepared.dataset_payload()
    resolved = frozen_payload["resolved_characters"]
    assert isinstance(resolved, dict)
    resolved_alex = resolved["alex"]
    assert isinstance(resolved_alex, dict)
    frozen_data = resolved_alex["data"]
    assert isinstance(frozen_data, dict)
    assert frozen_data["experimental"] == raw["experimental"]

    latest = library.get("alex")
    changed = latest.model_copy(deep=True)
    changed.experimental["spatial_reasoning"]["style"] = "route-oriented"
    library.update("alex", changed, character_content_hash(latest))
    runner = create_runner(
        prepared.scenario,
        resolved_characters=prepared.runtime_characters(),
    )
    dossier = runner.registry.get_resource(InformationStore).get(
        character_dossier_document_id("agent-001")
    )

    assert dossier.content["experimental"] == raw["experimental"]
    profile = runner.registry.get_component(
        "agent-001",
        CharacterProfileComponent,
    )
    assert "Spatial Reasoning" in profile.description
    assert len(profile.content_hash) == 64


def test_character_slot_assignments_apply_defaults_overrides_and_constraints(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path / "characters")
    library.create(
        CharacterDefinition.model_validate(
            {
                "schema_version": 1,
                "id": "older-woman",
                "identity": {
                    "display_name": "Older Woman",
                    "age": 42,
                    "gender": "Woman",
                },
            }
        )
    )
    library.create(
        CharacterDefinition.model_validate(
            {
                "schema_version": 1,
                "id": "younger-man",
                "identity": {
                    "display_name": "Younger Man",
                    "age": 25,
                    "gender": "Man",
                },
            }
        )
    )
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "constrained-slot",
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "character_slot": {
                            "label": "Experienced operator",
                            "default_character_id": "older-woman",
                            "constraints": {
                                "minimum_age": 30,
                                "maximum_age": 42,
                                "allowed_genders": ["woman"],
                                "allowed_template_ids": ["human-v1"],
                            },
                        }
                    },
                }
            ],
        }
    )

    prepared = prepare_scenario(scenario, library)
    assert prepared.assignments == {"agent-001": "older-woman"}

    with pytest.raises(
        CharacterLibraryError,
        match="ineligible.*age 25 is below minimum 30.*gender 'Man' is not allowed",
    ):
        prepare_scenario(
            scenario,
            library,
            {"agent-001": "younger-man"},
        )


def test_character_slot_rejects_missing_and_unknown_assignments(
    tmp_path: Path,
) -> None:
    library = FileSystemCharacterLibrary(tmp_path / "characters")
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "unassigned-slot",
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "character_slot": {"label": "Open role"}
                    },
                }
            ],
        }
    )

    with pytest.raises(CharacterLibraryError, match="has no assignment"):
        prepare_scenario(scenario, library)
    with pytest.raises(CharacterLibraryError, match="unknown character slots"):
        prepare_scenario(scenario, library, {"not-a-slot": "alex"})


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
    assert migrated["entities"][0]["components"]["character_slot"] == {
        "label": "Alex",
        "briefing": "",
        "default_character_id": "alex",
        "constraints": {},
    }
    assert (character_directory / "alex.json").is_file()
