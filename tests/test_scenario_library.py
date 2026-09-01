import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage0_sim.adapters.scenarios import FileSystemScenarioLibrary
from stage0_sim.api.app import app
from stage0_sim.api.scenarios import router as scenario_router
from stage0_sim.application.elements import ScenarioSourceDefinition
from stage0_sim.application.scenarios import (
    ScenarioConflictError,
    ScenarioLibraryError,
    ScenarioNotFoundError,
    scenario_content_hash,
)
from stage0_sim.config import Settings


def scenario(
    name: str = "Library scenario",
    *,
    world: dict[str, object] | None = None,
) -> ScenarioSourceDefinition:
    payload: dict[str, object] = {
        "schema_version": 3,
        "name": name,
        "entities": [
            {
                "id": "agent-001",
                "components": {"metadata": {"display_name": "Alex"}},
            }
        ],
    }
    if world is not None:
        payload["world"] = world
    return ScenarioSourceDefinition.model_validate(payload)


def test_filesystem_scenario_library_crud_and_stale_conflicts(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path / "scenarios")
    created = library.create("example", scenario())
    created_hash = scenario_content_hash(created)

    assert (tmp_path / "scenarios").is_dir()
    assert library.get("example") == created
    assert [summary.id for summary in library.list()] == ["example"]
    with pytest.raises(ScenarioConflictError):
        library.create("example", created)

    updated = library.update(
        "example",
        scenario("Updated scenario"),
        created_hash,
    )
    with pytest.raises(ScenarioConflictError, match="changed since"):
        library.update("example", updated, created_hash)
    with pytest.raises(ScenarioConflictError, match="changed since"):
        library.rename("example", "stale-rename", created_hash)
    with pytest.raises(ScenarioConflictError, match="changed since"):
        library.delete("example", created_hash)

    updated_hash = scenario_content_hash(updated)
    original_bytes = (tmp_path / "scenarios" / "example.json").read_bytes()
    renamed = library.rename("example", "renamed", updated_hash)
    assert renamed == updated
    assert scenario_content_hash(renamed) == updated_hash
    assert (tmp_path / "scenarios" / "renamed.json").read_bytes() == original_bytes
    with pytest.raises(ScenarioNotFoundError):
        library.get("example")

    deleted = library.delete("renamed", updated_hash)
    assert deleted == updated
    assert library.list() == ()


def test_scenario_library_uses_safe_resource_ids_and_ignores_hidden_files(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)

    for unsafe_id in ("../outside", "Uppercase", "space name", "con", "con.demo"):
        with pytest.raises(ScenarioLibraryError):
            library.get(unsafe_id)

    (tmp_path / ".hidden.json").write_text("not JSON", encoding="utf-8")
    library.create("safe.name-1_test", scenario())

    assert [summary.id for summary in library.list()] == [
        "safe.name-1_test"
    ]
    assert not (tmp_path.parent / "outside.json").exists()


def test_scenario_library_reports_malformed_and_invalid_files(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    (tmp_path / "malformed.json").write_text("{", encoding="utf-8")
    (tmp_path / "invalid.json").write_text(
        json.dumps({"schema_version": 3, "name": ""}),
        encoding="utf-8",
    )

    with pytest.raises(ScenarioLibraryError, match="not valid JSON"):
        library.get("malformed")
    with pytest.raises(ScenarioLibraryError, match="validation failed"):
        library.get("invalid")
    with pytest.raises(ScenarioLibraryError, match="validation failed|not valid JSON"):
        library.list()


def test_scenario_summaries_are_sorted_and_report_world_kind(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    library.create("z-none", scenario("No world"))
    library.create(
        "a-grid",
        scenario("Grid world", world={"width": 2, "height": 3}),
    )
    city_source = Path("scenarios/reference-city-restaurants.json")
    city = ScenarioSourceDefinition.model_validate_json(
        city_source.read_text(encoding="utf-8")
    )
    library.create("m-city", city)

    summaries = library.list()

    assert [item.id for item in summaries] == ["a-grid", "m-city", "z-none"]
    assert [item.world_kind for item in summaries] == ["grid", "city", "none"]
    assert summaries[0].name == "Grid world"
    assert summaries[0].schema_version == 3
    assert summaries[0].entity_count == 1
    assert summaries[0].content_hash == scenario_content_hash(
        library.get("a-grid")
    )


def test_scenario_files_are_deterministic_indented_and_newline_terminated(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    value = scenario("Stable output", world={"height": 2, "width": 3})

    library.create("stable", value)
    first = (tmp_path / "stable.json").read_bytes()
    library.update("stable", value, scenario_content_hash(value))
    second = (tmp_path / "stable.json").read_bytes()

    assert first == second
    assert first.endswith(b"\n")
    assert b'\n  "entities":' in first
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_scenario_api_crud_statuses_and_payloads(tmp_path: Path) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    api = FastAPI()
    api.state.scenario_library = library
    api.include_router(scenario_router)

    with TestClient(api) as client:
        missing = client.get("/scenarios/missing")
        assert missing.status_code == 404

        invalid_request = client.post(
            "/scenarios",
            json={"id": "bad", "scenario": {"name": ""}},
        )
        assert invalid_request.status_code == 422
        legacy_request = client.post(
            "/scenarios",
            json={
                "id": "legacy",
                "scenario": {
                    "schema_version": 2,
                    "name": "Legacy saved scenario",
                },
            },
        )
        assert legacy_request.status_code == 422
        assert "Input should be 3" in legacy_request.text

        created = client.post(
            "/scenarios",
            json={
                "id": "example",
                "scenario": scenario().model_dump(mode="json"),
            },
        )
        assert created.status_code == 201
        created_payload = created.json()
        content_hash = created_payload["content_hash"]
        assert created_payload["id"] == "example"
        assert created_payload["scenario"]["name"] == "Library scenario"

        duplicate = client.post(
            "/scenarios",
            json={
                "id": "example",
                "scenario": scenario().model_dump(mode="json"),
            },
        )
        assert duplicate.status_code == 409
        assert client.get("/scenarios").json()["scenarios"][0]["id"] == "example"

        updated = client.put(
            "/scenarios/example",
            json={
                "expected_hash": content_hash,
                "scenario": scenario("Updated").model_dump(mode="json"),
            },
        )
        assert updated.status_code == 200
        assert updated.json()["scenario"]["name"] == "Updated"

        stale = client.put(
            "/scenarios/example",
            json={
                "expected_hash": content_hash,
                "scenario": scenario("Stale").model_dump(mode="json"),
            },
        )
        assert stale.status_code == 409

        renamed = client.post(
            "/scenarios/example/rename",
            json={
                "expected_hash": updated.json()["content_hash"],
                "new_id": "renamed",
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["id"] == "renamed"
        assert renamed.json()["scenario"]["name"] == "Updated"
        assert client.get("/scenarios/example").status_code == 404

        unsafe = client.get("/scenarios/Uppercase")
        assert unsafe.status_code == 400

        deleted = client.delete(
            "/scenarios/renamed",
            params={"expected_hash": renamed.json()["content_hash"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["scenario"]["name"] == "Updated"
        assert deleted.json()["content_hash"] == renamed.json()["content_hash"]

        (tmp_path / "malformed.json").write_text("{", encoding="utf-8")
        malformed = client.get("/scenarios/malformed")
        assert malformed.status_code == 400


def test_schema_v3_reference_scenario_round_trips_without_flattening(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    path = Path("scenarios/reference-city-restaurants.json")
    original = ScenarioSourceDefinition.model_validate_json(
        path.read_text(encoding="utf-8")
    )

    library.create(path.stem, original)
    loaded = library.get(path.stem)

    assert loaded.model_dump(mode="json") == original.model_dump(mode="json")
    payload = loaded.model_dump(mode="json")
    assert "city_zones" in payload["world"]
    assert "local_maps" not in payload["world"]


def test_schema_v2_saved_scenario_has_clear_validation_error(
    tmp_path: Path,
) -> None:
    library = FileSystemScenarioLibrary(tmp_path)
    (tmp_path / "legacy.json").write_text(
        json.dumps({"schema_version": 2, "name": "Legacy"}),
        encoding="utf-8",
    )

    with pytest.raises(
        ScenarioLibraryError,
        match="schema version 3.*schema-version-2",
    ):
        library.get("legacy")
    with pytest.raises(
        ScenarioLibraryError,
        match="schema version 3.*schema-version-2",
    ):
        library.list()


def test_settings_reads_scenario_directory_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "library"
    monkeypatch.setenv("STAGE0_SCENARIO_DIRECTORY", str(configured))

    assert Settings().scenario_directory == configured


def test_app_lifespan_initializes_and_mounts_scenario_library(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_directory=tmp_path / "runs",
        character_directory=tmp_path / "characters",
        scenario_directory=tmp_path / "scenarios",
    )
    monkeypatch.setattr("stage0_sim.api.app.get_settings", lambda: settings)

    with TestClient(app) as client:
        response = client.get("/scenarios")

        assert response.status_code == 200
        assert response.json() == {"scenarios": []}
        assert isinstance(app.state.scenario_library, FileSystemScenarioLibrary)
        assert app.state.scenario_library.root == (
            tmp_path / "scenarios"
        ).resolve()
