import json
import re
import shutil
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage0_sim.api.scenario_editor_world import build_editor_world
from stage0_sim.api.scenario_forms import (
    KNOWN_ENTITY_COMPONENT_MODELS,
    ScenarioEditorDraft,
    ScenarioEditorNode,
    find_node_by_path,
    scenario_editor_coverage_errors,
)
from stage0_sim.application.elements import (
    CityWorldSourceDefinition,
    ElementKind,
    ScenarioSourceDefinition,
)
from stage0_sim.application.scenarios import scenario_content_hash
from stage0_sim.config import Settings


def _nodes(node: ScenarioEditorNode) -> Iterator[ScenarioEditorNode]:
    yield node
    for child in node.children:
        yield from _nodes(child)
    for item in node.items:
        yield from _nodes(item)
    for variant in node.variants.values():
        if variant is not None:
            yield from _nodes(variant)


def _node(
    draft: ScenarioEditorDraft,
    path: tuple[str | int, ...],
    *,
    kind: str | None = None,
) -> ScenarioEditorNode:
    return next(
        item
        for item in _nodes(draft.root)
        if item.path == path and (kind is None or item.schema.kind == kind)
    )


@pytest.fixture
def scenario_ui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, FastAPI, Path]]:
    root = Path(__file__).parents[1]
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    for name in ("minimal.json", "navigation.json"):
        payload = json.loads((root / "scenarios" / name).read_text(encoding="utf-8"))
        payload["schema_version"] = 3
        (scenarios / name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    shutil.copy2(
        root / "scenarios" / "reference-city-restaurants.json",
        scenarios / "reference-city-restaurants.json",
    )
    elements = tmp_path / "elements"
    shutil.copytree(root / "elements", elements)
    settings = Settings(
        data_directory=tmp_path / "runs",
        character_directory=root / "characters",
        scenario_directory=scenarios,
        element_directory=elements,
    )
    app_module = import_module("stage0_sim.api.app")
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    with TestClient(app_module.app) as client:
        yield client, cast(FastAPI, app_module.app), scenarios


def _open_draft(
    client: TestClient,
    app: FastAPI,
    path: str,
) -> ScenarioEditorDraft:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    token = response.url.params["draft"]
    session_id = client.cookies["stage0_operator_session"]
    draft = cast(
        ScenarioEditorDraft | None,
        app.state.scenario_editor_drafts.get(session_id, token),
    )
    assert draft is not None
    return draft


def test_scenario_editor_descriptor_covers_every_typed_field() -> None:
    assert scenario_editor_coverage_errors() == ()
    assert set(KNOWN_ENTITY_COMPONENT_MODELS) == {
        "position",
        "spatial_location",
        "movement",
        "homeostasis",
        "activity",
        "possessions",
        "character_slot",
        "plan",
        "planner",
        "information",
        "controller",
        "senses",
        "memory",
        "conversation",
        "metadata",
    }


def test_scenario_library_page_renders_structured_grid_city_and_entity_controls(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui

    page = client.get(
        "/ui/scenarios/?selected=navigation",
        follow_redirects=True,
    )
    token = page.url.params["draft"]
    session_id = client.cookies["stage0_operator_session"]
    draft = app.state.scenario_editor_drafts.get(session_id, token)
    assert draft is not None

    assert page.status_code == 200
    assert "<h1>Scenario Library</h1>" in page.text
    assert 'href="/ui/scenarios/"' in page.text
    assert "Scenario resource ID" in page.text
    assert "World type" in page.text
    assert "Visual world editor" in page.text
    assert "Pannable scenario world map" in page.text
    assert "World objects" in page.text
    assert "Zones" in page.text
    assert "Stations" in page.text
    assert "Activity Coefficients" in page.text
    assert "Tie Break Order" in page.text
    assert 'option value="city"' in page.text
    identifiers = re.findall(r'\sid="([^"]+)"', page.text)
    assert len(identifiers) == len(set(identifiers))

    city_page = client.get(
        "/ui/scenarios/?selected=reference-city-restaurants",
        follow_redirects=True,
    )
    assert "City zones" in city_page.text
    assert "Buildings · Market Zone" in city_page.text
    assert "Transport nodes" in city_page.text
    assert "Walking Speed Mps" in city_page.text
    assert "Metro Lines" in city_page.text

    stations = find_node_by_path(draft.root, ("world", "stations"))
    assert stations is not None and stations.items
    station_page = client.get(
        f"/ui/scenarios/?draft={draft.token}&selected=navigation"
        f"&focus={stations.items[0].id}",
    )
    assert 'option value="EAT"' in station_page.text

    slot_component = _node(
        draft,
        ("entities", 0, "components", "character_slot"),
        kind="optional",
    )
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"choice_{slot_component.id}": "present",
            "intent": "refresh",
        },
        follow_redirects=True,
    )
    entity = find_node_by_path(draft.root, ("entities", 0))
    assert entity is not None
    slot_page = client.get(
        f"/ui/scenarios/?draft={draft.token}&selected=navigation&focus={entity.id}",
    )
    assert "Unknown Passthrough Components" in slot_page.text
    assert "Default Character Id" in slot_page.text
    assert "Allowed Genders" in slot_page.text


def test_visual_editor_selects_objects_adds_records_and_drills_into_rooms(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    grid = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    blocked = find_node_by_path(grid.root, ("world", "blocked"))
    assert blocked is not None

    added = client.post(
        f"/ui/scenarios/drafts/{grid.token}",
        data={"collection_action": f"add:{blocked.id}"},
        follow_redirects=True,
    )

    assert added.status_code == 200
    assert grid.view.selected_node_id == blocked.items[-1].id
    assert "Selected object" in added.text
    assert "Blocked cells" in added.text

    city = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    buildings = find_node_by_path(
        city.root,
        ("world", "city_zones", 0, "buildings"),
    )
    assert buildings is not None and buildings.items
    building = buildings.items[0]

    selected = client.get(
        f"/ui/scenarios/?draft={city.token}&selected=reference-city-restaurants"
        f"&focus={building.id}",
    )
    assert selected.status_code == 200
    assert "Open Standard Restaurant interior" in selected.text
    assert "Inherited building definition" in selected.text

    drilled = client.get(
        f"/ui/scenarios/?draft={city.token}&selected=reference-city-restaurants"
        f"&scope={building.id}&focus={building.id}",
    )
    assert drilled.status_code == 200
    assert "Building interior · Standard Restaurant" in drilled.text
    assert "Reference City" in drilled.text
    assert "Market Zone" in drilled.text


def test_visual_editor_reports_unprojectable_spatial_records(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    blocked = find_node_by_path(draft.root, ("world", "blocked"))
    assert blocked is not None
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"add:{blocked.id}"},
    )
    x = find_node_by_path(
        draft.root,
        ("world", "blocked", len(blocked.items) - 1, "x"),
    )
    assert x is not None
    x.value = "not-a-number"

    presentation = build_editor_world(draft)
    blocked_group = next(group for group in presentation.groups if group[0] == "blocked")

    assert blocked_group[3][-1].unplaced is True


def test_separate_tabs_keep_separate_drafts_and_invalid_values(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    first = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    second = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    assert first.token != second.token
    first_name = _node(first, ("name",), kind="scalar")
    second_name = _node(second, ("name",), kind="scalar")

    invalid = client.post(
        f"/ui/scenarios/drafts/{first.token}",
        data={
            "resource_id": "minimal",
            f"value_{first_name.id}": "",
            "intent": "save",
        },
        follow_redirects=True,
    )

    assert invalid.status_code == 200
    assert "Scenario validation failed" in invalid.text
    assert "String should have at least 1 character" in invalid.text
    assert first_name.value == ""
    assert second_name.value == "phase-1-minimal"


def test_create_collections_save_and_unsaved_stage_are_separate(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?new=1")
    name = _node(draft, ("name",), kind="scalar")

    saved = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{name.id}": "Created scenario",
            "intent": "save",
        },
        follow_redirects=True,
    )

    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]
    assert saved.status_code == 200
    assert "staged scenario and active run were unchanged" in saved.text
    assert (directory / "created.json").is_file()
    assert operator_session.scenario is None
    assert operator_session.run_id is None

    entities = _node(draft, ("entities",), kind="list")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            "collection_action": f"add:{entities.id}",
        },
    )
    entity_id = _node(draft, ("entities", 0, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{entity_id.id}": "person-001",
            "intent": "save",
        },
    )
    assert app.state.scenario_library.get("created").entities[0].id == "person-001"

    staged_name = _node(draft, ("name",), kind="scalar")
    staged = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "created",
            f"value_{staged_name.id}": "Unsaved staged scenario",
            "intent": "stage",
        },
        follow_redirects=True,
    )

    assert staged.status_code == 200
    assert "Unsaved staged scenario" in staged.text
    assert operator_session.scenario is not None
    assert operator_session.scenario.name == "Unsaved staged scenario"
    assert operator_session.run_id is None
    assert app.state.scenario_library.get("created").name == "Created scenario"


def test_collection_reorder_and_remove_actions_preserve_loose_values(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?new=1")
    entities = _node(draft, ("entities",), kind="list")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"add:{entities.id}"},
    )
    first_id = _node(draft, ("entities", 0, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"value_{first_id.id}": "first",
            "collection_action": f"add:{entities.id}",
        },
    )
    second_item = entities.items[1]
    second_id = _node(draft, ("entities", 1, "id"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            f"value_{second_id.id}": "second",
            "collection_action": f"up:{entities.id}:{second_item.id}",
        },
    )

    assert _node(draft, ("entities", 0, "id"), kind="scalar").value == "second"
    assert _node(draft, ("entities", 1, "id"), kind="scalar").value == "first"

    removed_id = entities.items[1].id
    client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"collection_action": f"remove:{entities.id}:{removed_id}"},
    )
    assert len(entities.items) == 1
    assert _node(draft, ("entities", 0, "id"), kind="scalar").value == "second"


def test_stale_editor_save_is_rejected_without_losing_the_draft(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(client, app, "/ui/scenarios/?selected=minimal")
    library = app.state.scenario_library
    current = library.get("minimal")
    external = current.model_copy(update={"name": "External update"})
    library.update("minimal", external, scenario_content_hash(current))
    name = _node(draft, ("name",), kind="scalar")

    response = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "minimal",
            f"value_{name.id}": "Stale draft value",
            "intent": "save",
        },
        follow_redirects=True,
    )

    assert "scenario changed since it was loaded" in response.text
    assert name.value == "Stale draft value"
    assert library.get("minimal").name == "External update"


def test_nested_grid_and_city_fields_save_through_structured_controls(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    grid = _open_draft(client, app, "/ui/scenarios/?selected=navigation")
    grid_width = _node(grid, ("world", "width"), kind="scalar")
    client.post(
        f"/ui/scenarios/drafts/{grid.token}",
        data={
            "resource_id": "navigation",
            f"value_{grid_width.id}": "13",
            "intent": "save",
        },
    )
    assert app.state.scenario_library.get("navigation").world.width == 13

    city = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    city_name = _node(city, ("world", "city", "name"), kind="scalar")
    walking_speed = _node(
        city,
        ("world", "transport", "walking_speed_mps"),
        kind="scalar",
    )
    response = client.post(
        f"/ui/scenarios/drafts/{city.token}",
        data={
            "resource_id": "reference-city-restaurants",
            f"value_{city_name.id}": "Edited City",
            f"value_{walking_speed.id}": "1.75",
            "intent": "save",
        },
        follow_redirects=True,
    )
    loaded = app.state.scenario_library.get("reference-city-restaurants")

    assert response.status_code == 200
    assert "Saved" in response.text
    assert isinstance(loaded.world, CityWorldSourceDefinition)
    assert loaded.world.city.name == "Edited City"
    assert loaded.world.transport.walking_speed_mps == 1.75


def test_building_library_add_populates_reference_and_reset_is_isolated(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    draft = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=reference-city-restaurants",
    )
    zone = _node(draft, ("world", "city_zones", 0), kind="model")
    buildings = _node(
        draft,
        ("world", "city_zones", 0, "buildings"),
        kind="list",
    )
    before = len(buildings.items)

    added = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "building_action": f"add:{zone.id}:standard-restaurant",
        },
        follow_redirects=True,
    )

    assert added.status_code == 200
    assert len(buildings.items) == before + 1
    added_index = len(buildings.items) - 1
    reference_id = _node(
        draft,
        (
            "world",
            "city_zones",
            0,
            "buildings",
            added_index,
            "element",
            "id",
        ),
        kind="scalar",
    )
    reference_hash = _node(
        draft,
        (
            "world",
            "city_zones",
            0,
            "buildings",
            added_index,
            "element",
            "content_hash",
        ),
        kind="scalar",
    )
    assert reference_id.value == "standard-restaurant"
    assert len(reference_hash.value) == 64
    assert "Inherited building definition" in added.text

    first = buildings.items[0]
    first_name = _node(
        draft,
        ("world", "city_zones", 0, "buildings", 0, "overrides", "name"),
        kind="optional",
    )
    changed = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            f"choice_{first_name.id}": "present",
            f"value_{first_name.items[0].id}": "West Custom Restaurant",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Saved" in changed.text
    saved = app.state.scenario_library.get("reference-city-restaurants")
    assert isinstance(saved.world, CityWorldSourceDefinition)
    assert saved.world.city_zones[0].buildings[0].overrides.name == (
        "West Custom Restaurant"
    )
    assert saved.world.city_zones[0].buildings[1].overrides.name == (
        "East Market Restaurant"
    )

    first = _node(
        draft,
        ("world", "city_zones", 0, "buildings", 0),
        kind="model",
    )
    reset = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "building_action": f"reset:{first.id}",
        },
        follow_redirects=True,
    )
    assert reset.status_code == 200
    saved_again = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={
            "resource_id": "reference-city-restaurants",
            "intent": "save",
        },
        follow_redirects=True,
    )
    assert "Saved" in saved_again.text
    reset_source = app.state.scenario_library.get(
        "reference-city-restaurants"
    )
    assert isinstance(reset_source.world, CityWorldSourceDefinition)
    assert reset_source.world.city_zones[0].buildings[0].overrides.name is None
    assert reset_source.world.city_zones[0].buildings[1].overrides.name == (
        "East Market Restaurant"
    )
    on_disk = json.loads(
        (_directory / "reference-city-restaurants.json").read_text(
            encoding="utf-8"
        )
    )
    assert "city_zones" in on_disk["world"]
    assert "local_maps" not in on_disk["world"]


def test_missing_and_hash_drift_dependencies_block_saved_staging(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    library = app.state.scenario_library
    source = library.get("reference-city-restaurants")
    raw = source.model_dump(mode="json")
    raw["world"]["city_zones"][0]["buildings"][0]["element"]["id"] = (
        "missing-building"
    )
    missing = ScenarioSourceDefinition.model_validate(raw)
    library.create("missing-dependency", missing)

    missing_response = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "missing-dependency"},
        follow_redirects=True,
    )
    assert "Could not stage saved scenario" in missing_response.text
    assert "unknown element: missing-building" in missing_response.text

    building = app.state.element_library.get(
        "standard-restaurant",
        ElementKind.BUILDING,
    )
    building_hash = next(
        summary.content_hash
        for summary in app.state.element_library.list(ElementKind.BUILDING)
        if summary.id == "standard-restaurant"
    )
    app.state.element_library.update(
        "standard-restaurant",
        building.model_copy(
            update={"description": "Changed after scenario save"}
        ),
        building_hash,
    )
    drift_response = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "reference-city-restaurants"},
        follow_redirects=True,
    )
    assert "Could not stage saved scenario" in drift_response.text
    assert "content hash changed" in drift_response.text


def test_import_search_duplicate_rename_download_and_delete(
    scenario_ui: tuple[TestClient, FastAPI, Path],
    tmp_path: Path,
) -> None:
    client, app, _directory = scenario_ui
    imported_path = tmp_path / "imported-scenario.json"
    legacy_path = tmp_path / "legacy-scenario.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "Legacy scenario",
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    legacy = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("legacy-scenario.json", legacy_path.read_bytes())},
        follow_redirects=True,
    )
    assert "schema-version-2 imports are not supported" in legacy.text
    imported_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "Imported scenario",
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    imported = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("imported-scenario.json", imported_path.read_bytes())},
        follow_redirects=True,
    )
    assert "Imported Imported scenario as imported-scenario" in imported.text
    oversized = client.post(
        "/ui/scenarios/import",
        files={"scenario": ("too-large.json", b"x" * (5 * 1024 * 1024 + 1))},
        follow_redirects=True,
    )
    assert "scenario files must be 5 MB or smaller" in oversized.text

    draft = _open_draft(
        client,
        app,
        "/ui/scenarios/?selected=imported-scenario",
    )
    renamed = client.post(
        f"/ui/scenarios/drafts/{draft.token}",
        data={"resource_id": "renamed-scenario", "intent": "save"},
        follow_redirects=True,
    )
    assert renamed.status_code == 200
    assert app.state.scenario_library.get("renamed-scenario").name == (
        "Imported scenario"
    )

    expected_hash = app.state.scenario_library.list()[
        [item.id for item in app.state.scenario_library.list()].index(
            "renamed-scenario"
        )
    ].content_hash
    duplicated = client.post(
        "/ui/scenarios/renamed-scenario/duplicate",
        data={"expected_hash": expected_hash},
        follow_redirects=True,
    )
    assert "Duplicated renamed-scenario as renamed-scenario-copy" in duplicated.text

    search = client.get("/ui/scenarios/?search=renamed", follow_redirects=True)
    assert "renamed-scenario-copy" in search.text
    download = client.get("/ui/scenarios/renamed-scenario/download")
    assert download.status_code == 200
    assert download.json()["name"] == "Imported scenario"

    deleted = client.post(
        "/ui/scenarios/renamed-scenario/delete",
        data={"expected_hash": expected_hash, "confirm": "yes"},
        follow_redirects=True,
    )
    assert "Deleted renamed-scenario" in deleted.text


def test_simulation_page_stages_only_the_selected_saved_scenario(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    page = client.get("/ui/")
    staged = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "minimal"},
        follow_redirects=True,
    )
    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]

    assert "Stage selected saved scenario" in page.text
    assert "Open selected scenario in editor" in page.text
    assert "minimal is validated and staged" in staged.text
    assert operator_session.scenario is not None
    assert operator_session.scenario.name == "phase-1-minimal"
    assert operator_session.run_id is None
    assert operator_session.scenario_id is not None
    prepared = app.state.simulation_manager.get_scenario(
        operator_session.scenario_id
    )
    assert prepared.scenario_source is not None
    assert prepared.scenario_source["schema_version"] == 3


def test_saved_reference_scenario_preserves_resolved_provenance(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui

    staged = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "reference-city-restaurants"},
        follow_redirects=True,
    )
    session_id = client.cookies["stage0_operator_session"]
    operator_session = app.state.operator_sessions.get(session_id)[1]

    assert "reference-city-restaurants is validated and staged" in staged.text
    assert operator_session.scenario_id is not None
    prepared = app.state.simulation_manager.get_scenario(
        operator_session.scenario_id
    )
    assert prepared.scenario_source is not None
    assert "city_zones" in prepared.scenario_source["world"]
    assert prepared.resolved_elements["standard-restaurant"]["kind"] == (
        "building"
    )


def test_character_slot_filters_and_revalidates_assignments(
    scenario_ui: tuple[TestClient, FastAPI, Path],
) -> None:
    client, app, _directory = scenario_ui
    constrained = ScenarioSourceDefinition.model_validate(
        {
            "name": "Constrained composition",
            "calendar": {
                "start_datetime": "2026-08-31T09:00:00+00:00"
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "character_slot": {
                            "label": "Experienced male analyst",
                            "briefing": "Review the evidence.",
                            "constraints": {
                                "minimum_age": 30,
                                "allowed_genders": ["man"],
                                "allowed_template_ids": ["human-v1"],
                            },
                        }
                    },
                }
            ],
        }
    )
    app.state.scenario_library.create("constrained", constrained)

    loaded = client.post(
        "/ui/scenario/library/stage",
        data={"scenario_id": "constrained"},
        follow_redirects=True,
    )

    assert "Could not stage saved scenario" in loaded.text
    assert "Experienced male analyst" in loaded.text
    assert "Alex Chen" in loaded.text
    assert "Jordan Lee" not in loaded.text
    assert 'disabled>Start run</button>' in loaded.text

    rejected = client.post(
        "/ui/scenario/assign",
        data={"character.agent-001": "jordan-lee"},
        follow_redirects=True,
    )
    assert "Assignment invalid" in rejected.text
    assert "is ineligible for slot agent-001" in rejected.text
    assert 'disabled>Start run</button>' in rejected.text

    staged = client.post(
        "/ui/scenario/assign",
        data={"character.agent-001": "alex-chen"},
        follow_redirects=True,
    )
    assert "assignments were validated and staged" in staged.text
    assert ">Start run</button>" in staged.text
