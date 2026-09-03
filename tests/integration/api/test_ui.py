import json
import re
from dataclasses import replace
from html import unescape
from importlib.resources import files
from uuid import uuid4

from fastapi.testclient import TestClient

from stage0_sim.api import ui as operator_ui
from stage0_sim.api.app import app
from stage0_sim.api.operator_sessions import SESSION_COOKIE
from stage0_sim.application.manager import SimulationManager
from stage0_sim.domain.components import (
    CharacterHandStateComponent,
    CharacterPosture,
    CharacterPostureComponent,
    CustodyComponent,
    OpenableComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    SpatialParentRelationComponent,
)
from tests.helpers.paths import EXAMPLE_SCENARIOS
from tests.integration.api.test_simulation_api import (
    _create_physical_api_run,
    _physical_api_source,
)


def _create_persisted_dataset_run(
    client: TestClient,
    *,
    scenario_name: str | None = None,
) -> str:
    scenario = json.loads(
        (EXAMPLE_SCENARIOS / "system1-preemption.json").read_text(
            encoding="utf-8"
        )
    )
    if scenario_name is not None:
        scenario["name"] = scenario_name
    scenario_response = client.post(
        "/simulation/scenarios",
        json={"scenario": scenario, "character_assignments": {}},
    )
    run_response = client.post(
        "/simulation/runs",
        json={
            "scenario_id": scenario_response.json()["scenario_id"],
            "realtime": False,
        },
    )
    run_id = str(run_response.json()["run_id"])
    client.post(f"/simulation/runs/{run_id}/pause")
    client.post(f"/simulation/runs/{run_id}/step")
    client.post(f"/simulation/runs/{run_id}/stop")
    return run_id


def _create_persisted_physical_dataset_run(client: TestClient) -> str:
    run_id = _create_physical_api_run(client)
    client.post(f"/simulation/runs/{run_id}/pause")
    client.post(f"/simulation/runs/{run_id}/step")
    client.post(f"/simulation/runs/{run_id}/stop")
    return run_id


def _attach_physical_operator_run(
    client: TestClient,
) -> tuple[str, operator_ui.OperatorSession]:
    library, source = _physical_api_source()
    original_library = app.state.element_library
    app.state.element_library = library
    try:
        scenario_response = client.post(
            "/simulation/scenarios",
            json={"scenario": source, "character_assignments": {}},
        )
    finally:
        app.state.element_library = original_library
    assert scenario_response.status_code == 201
    scenario_id = str(scenario_response.json()["scenario_id"])
    run_response = client.post(
        "/simulation/runs",
        json={"scenario_id": scenario_id, "realtime": False},
    )
    assert run_response.status_code == 201
    run_id = str(run_response.json()["run_id"])
    client.get("/ui/")
    session_id = client.cookies.get(SESSION_COOKIE)
    assert session_id is not None
    _, session = app.state.operator_sessions.get(session_id)
    manager = app.state.simulation_manager
    assert isinstance(manager, SimulationManager)
    prepared = manager.get_scenario(scenario_id)
    session.scenario = prepared.scenario
    session.scenario_id = scenario_id
    session.scenario_source = "integration physical fixture"
    session.run_id = run_id
    session.view_level = "room"
    session.zoom = operator_ui.ROOM_ZOOM
    return run_id, session


def test_ui_assets_are_installed_as_package_data() -> None:
    web = files("stage0_sim").joinpath("web")
    templates = web.joinpath("templates")
    static = web.joinpath("static")
    resources = files("stage0_sim").joinpath("resources")

    assert files("stage0_sim").joinpath("py.typed").is_file()
    assert templates.joinpath("base.html").is_file()
    assert templates.joinpath("simulation.html").is_file()
    assert templates.joinpath("characters.html").is_file()
    assert templates.joinpath("scenarios.html").is_file()
    assert templates.joinpath("elements.html").is_file()
    assert templates.joinpath("scenario_fields.html").is_file()
    assert templates.joinpath("dataset.html").is_file()
    assert templates.joinpath("data_management.html").is_file()
    assert static.joinpath("styles.css").is_file()
    assert static.joinpath("enhancements.js").is_file()
    assert resources.joinpath("demo.json").is_file()
    assert resources.joinpath("demo-character.json").is_file()
    assert not static.joinpath("app.js").exists()


def test_root_redirects_to_accessible_server_rendered_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        old_index = client.get("/ui/index.html", follow_redirects=False)
        old_characters = client.get(
            "/ui/characters.html",
            follow_redirects=False,
        )
        page = client.get("/ui/")
        styles = client.get("/ui/assets/styles.css")
        enhancements = client.get("/ui/assets/enhancements.js")
        template = client.get("/ui/assets/base.html")
        demo = client.get("/ui/assets/demo.json")

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"
    assert old_index.status_code == 404
    assert old_characters.status_code == 404
    assert page.status_code == 200
    assert "<h1>Operator Console</h1>" in page.text
    assert 'aria-label="Run lifecycle"' in page.text
    assert 'aria-labelledby="world-heading"' in page.text
    assert 'aria-labelledby="inspector-heading"' in page.text
    assert 'aria-labelledby="events-heading"' in page.text
    assert 'href="/ui/characters/"' in page.text
    assert 'href="/ui/scenarios/"' in page.text
    assert 'href="/ui/elements/"' in page.text
    assert 'href="/ui/data/"' in page.text
    assert 'action="/ui/scenario/example"' in page.text
    assert 'action="/ui/run/start"' in page.text
    assert 'src="/ui/assets/enhancements.js"' in page.text
    assert "<canvas" not in page.text
    assert "WebSocket" not in page.text
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert enhancements.status_code == 200
    assert "navigator.clipboard.writeText" in enhancements.text
    assert template.status_code == 404
    assert demo.status_code == 404


def test_reference_scenario_upload_resolves_shared_elements() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "reference-city-restaurants.json"

    with TestClient(app) as client:
        response = client.post(
            "/ui/scenario/upload",
            files={
                "scenario": (
                    scenario_path.name,
                    scenario_path.read_bytes(),
                    "application/json",
                )
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "reference-city-restaurants is validated and staged" in response.text
    assert "Standard Restaurant" in response.text
    assert "East Market Restaurant" in response.text


def test_dataset_explorer_is_server_rendered_filtered_and_private_by_opt_in() -> None:
    with TestClient(app) as client:
        run_id = _create_persisted_dataset_run(client)
        summary = client.get(f"/ui/datasets/{run_id}/")
        records = client.get(
            f"/ui/datasets/{run_id}/",
            params={
                "view": "records",
                "limit": 1,
                "entity_id": "agent-001",
            },
        )
        rejected_private = client.get(
            f"/ui/datasets/{run_id}/",
            params={
                "view": "records",
                "visibility": "PRIVATE_RESEARCH",
            },
        )
        private = client.get(
            f"/ui/datasets/{run_id}/",
            params={
                "view": "records",
                "limit": 1,
                "visibility": "PRIVATE_RESEARCH",
                "include_private": "true",
            },
        )
        schema = client.get(
            f"/ui/datasets/{run_id}/",
            params={"view": "schema"},
        )
        focused = {
            view: client.get(
                f"/ui/datasets/{run_id}/",
                params={"view": view, "limit": 2},
            )
            for view in (
                "goals",
                "decisions",
                "actions",
                "interactions",
                "transitions",
                "population",
                "resource_samples",
                "resource_flows",
            )
        }

    assert summary.status_code == 200
    assert "<h1>Research Dataset Explorer</h1>" in summary.text
    assert "Run summary and capture completeness" in summary.text
    assert "<dt>Capture complete</dt><dd>Yes</dd>" in summary.text
    assert 'aria-label="Dataset views"' in summary.text
    assert 'name="include_private"' in summary.text
    assert "Warning: enabling this control can display prompts" in summary.text
    assert 'data-record-visibility="PRIVATE_RESEARCH"' not in summary.text
    assert "Download filtered NDJSON" in summary.text
    assert "Download filtered analysis bundle" in summary.text
    assert "Download complete run dataset" in summary.text

    assert records.status_code == 200
    assert "Raw records" in records.text
    assert "1 ordered result on this page." in records.text
    assert "<summary>Sequence " in records.text
    assert "&#34;record_id&#34;" in records.text
    assert 'data-record-visibility="PRIVATE_RESEARCH"' not in records.text
    next_match = re.search(
        r'href="([^"]+)"[^>]*>Next page</a>',
        records.text,
    )
    assert next_match is not None
    next_url = unescape(next_match.group(1))
    assert "entity_id=agent-001" in next_url
    assert "limit=1" in next_url

    assert rejected_private.status_code == 200
    assert "Select “Include private research data”" in rejected_private.text
    assert 'data-record-visibility="PRIVATE_RESEARCH"' not in rejected_private.text
    assert private.status_code == 200
    assert "Private research data is displayed" in private.text
    assert 'name="include_private"' in private.text
    assert "checked" in private.text
    assert 'data-record-visibility="PRIVATE_RESEARCH"' in private.text
    assert "&#34;visibility&#34;: &#34;PRIVATE_RESEARCH&#34;" in private.text
    assert "include_private=true" in private.text

    assert schema.status_code == 200
    assert "Schema and data dictionary" in schema.text
    assert "Complete data dictionary JSON" in schema.text
    assert "stage0.data_dictionary" in schema.text
    assert all(response.status_code == 200 for response in focused.values())
    assert "Goals timeline" in focused["goals"].text
    assert "Population aggregates" in focused["population"].text

    ids = re.findall(r'\sid="([^"]+)"', private.text)
    assert len(ids) == len(set(ids))


def test_dataset_explorer_physical_views_require_private_opt_in() -> None:
    with TestClient(app) as client:
        run_id = _create_persisted_physical_dataset_run(client)
        public = client.get(
            f"/ui/datasets/{run_id}/",
            params={"view": "physical_object_states"},
        )
        private = client.get(
            f"/ui/datasets/{run_id}/",
            params={
                "view": "physical_object_states",
                "object_id": "object-z-cabinet",
                "room_id": "physical-api-building.room",
                "phase": "run_initial",
                "is_open": "false",
                "is_locked": "false",
                "include_private": "true",
            },
        )
        relations = client.get(
            f"/ui/datasets/{run_id}/",
            params={
                "view": "physical_relations",
                "object_id": "object-m-secret",
                "parent_id": "object-z-cabinet",
                "relation_kind": "IN_CONTAINER",
                "include_private": "true",
            },
        )
        private_summary = client.get(
            f"/ui/datasets/{run_id}/",
            params={"include_private": "true"},
        )

    assert public.status_code == 200
    assert "Physical object states" in public.text
    assert "No records match the current filters." in public.text
    assert "Private Letter" not in public.text
    assert "object-m-secret" not in public.text
    assert "private-secret-owner" not in public.text
    assert 'name="object_id"' in public.text
    assert 'name="room_id"' in public.text
    assert 'name="parent_id"' in public.text
    assert 'name="relation_kind"' in public.text
    assert 'name="interaction_verb"' in public.text
    assert 'name="phase"' in public.text
    assert 'name="is_open"' in public.text
    assert 'name="is_locked"' in public.text
    assert private.status_code == 200
    assert "Private research data is displayed" in private.text
    assert "object-z-cabinet" in private.text
    assert "physical-api-building.room" in private.text
    assert "Physical relations" in relations.text
    assert "object-m-secret" in relations.text
    assert "IN_CONTAINER" in relations.text
    assert "Physical observations" in private_summary.text
    assert "private_records_included" in private_summary.text


def test_data_management_catalog_selection_aggregate_and_deletion() -> None:
    scenario_name = f"backend data management workflow {uuid4()}"
    with TestClient(app) as client:
        run_ids = [
            _create_persisted_dataset_run(
                client,
                scenario_name=scenario_name,
            )
            for _ in range(2)
        ]
        first = client.get(
            "/ui/data/",
            params={"scenario": scenario_name, "limit": 1},
        )
        first_run_id = re.search(
            r'name="page_run_id" value="([^"]+)"',
            first.text,
        )
        assert first_run_id is not None
        selected_first = client.post(
            "/ui/data/selection",
            data={
                "action": "add_page",
                "return_to": str(first.url),
                "scenario": scenario_name,
                "limit": "1",
                "page_run_id": first_run_id.group(1),
            },
            follow_redirects=True,
        )
        next_match = re.search(
            r'href="([^"]+)"[^>]*>Next page</a>',
            selected_first.text,
        )
        assert next_match is not None
        second = client.get(unescape(next_match.group(1)))
        second_run_id = re.search(
            r'name="page_run_id" value="([^"]+)"',
            second.text,
        )
        assert second_run_id is not None
        selected_second = client.post(
            "/ui/data/selection",
            data={
                "action": "add_page",
                "return_to": str(second.url),
                "scenario": scenario_name,
                "limit": "1",
                "page_run_id": second_run_id.group(1),
            },
            follow_redirects=True,
        )

        assert first.status_code == 200
        assert "<h1>Data Management</h1>" in first.text
        assert "Persisted run catalog" in first.text
        assert "2 persisted runs selected" in selected_second.text
        assert "Pooled:" in selected_second.text
        assert "Macro per run:" in selected_second.text
        assert "PRIVATE_RESEARCH-derived rows" not in selected_second.text
        assert "Download aggregate JSON" in selected_second.text
        assert "Download aggregate CSV" in selected_second.text
        assert all(run_id in selected_second.text for run_id in run_ids)
        assert 'id="data-catalog-region"' in selected_second.text
        assert 'id="aggregate-data-region"' in selected_second.text
        ids = re.findall(r'\sid="([^"]+)"', selected_second.text)
        assert len(ids) == len(set(ids))

        cleared = client.post(
            "/ui/data/selection",
            data={
                "action": "clear",
                "return_to": str(second.url),
            },
            follow_redirects=True,
        )
        selected_all = client.post(
            "/ui/data/selection",
            data={
                "action": "select_all",
                "return_to": str(second.url),
                "scenario": scenario_name,
                "limit": "1",
            },
            follow_redirects=True,
        )
        excluded_private = client.get(
            str(second.url),
            params={
                "scenario": scenario_name,
                "limit": 1,
                "privacy_setting": "1",
            },
        )
        included_private = client.get(
            str(second.url),
            params={
                "scenario": scenario_name,
                "limit": 1,
                "privacy_setting": "1",
                "include_private_derived": "true",
            },
        )
        assert "0 runs selected across catalog pages." in cleared.text
        assert "2 runs selected across catalog pages." in selected_all.text
        assert "PRIVATE_RESEARCH-derived rows" not in excluded_private.text
        assert "PRIVATE_RESEARCH-derived rows" in included_private.text

        selection_form = next(
            (
                form
                for form in re.findall(
            r'<form method="post" action="/ui/data/deletion-preview".*?</form>',
            selected_all.text,
            re.DOTALL,
                )
                if "Delete selected runs" in form
            ),
            None,
        )
        assert selection_form is not None
        selected_ids = re.findall(
            r'name="run_id" value="([^"]+)"',
            selection_form,
        )
        fingerprint = re.search(
            r'name="selection_fingerprint" value="([0-9a-f]+)"',
            selection_form,
        )
        assert fingerprint is not None
        preview = client.post(
            "/ui/data/deletion-preview",
            data={
                "run_id": selected_ids,
                "selection_fingerprint": fingerprint.group(1),
            },
            follow_redirects=True,
        )
        token = re.search(
            r'name="confirmation_token" value="([0-9a-f]+)"',
            preview.text,
        )
        assert token is not None
        stale = client.post(
            "/ui/data/delete",
            data={
                "run_id": selected_ids,
                "selection_fingerprint": fingerprint.group(1),
                "confirmation_token": "0" * 64,
                "confirmed": "yes",
                "confirmation_phrase": "DELETE 2 RUNS",
            },
            follow_redirects=True,
        )
        deleted = client.post(
            "/ui/data/delete",
            data={
                "run_id": selected_ids,
                "selection_fingerprint": fingerprint.group(1),
                "confirmation_token": token.group(1),
                "confirmed": "yes",
                "confirmation_phrase": "DELETE 2 RUNS",
            },
            follow_redirects=True,
        )

        assert "stale deletion preview or confirmation token" in stale.text
        assert "Permanently deleted 2 runs" in deleted.text
        assert all(
            client.get(f"/ui/datasets/{run_id}/").status_code == 200
            and "Could not query this dataset" in client.get(
                f"/ui/datasets/{run_id}/"
            ).text
            for run_id in run_ids
        )


def test_operator_session_store_cleans_deleted_run_references() -> None:
    store = operator_ui.OperatorSessionStore()
    _, first = store.get(None)
    _, second = store.get(None)
    first.run_id = "deleted"
    first.selected_agent_id = "agent-001"
    first.selected_object_id = "object-001"
    first.follow_selected = True
    first.selected_data_run_ids = ("kept", "deleted")
    first.pending_run_deletion = operator_ui.PendingRunDeletion(
        run_ids=("deleted",),
        selection_fingerprint="fingerprint",
        filters=None,
        confirmation_token="token",
        total_records=3,
        phrase="DELETE 1 RUNS",
    )
    second.selected_data_run_ids = ("deleted",)

    store.remove_deleted_run_ids(("deleted",))

    assert first.run_id is None
    assert first.selected_agent_id is None
    assert first.selected_object_id is None
    assert first.follow_selected is False
    assert first.selected_data_run_ids == ("kept",)
    assert first.pending_run_deletion is None
    assert second.selected_data_run_ids == ()


def test_server_rendered_ui_stages_then_controls_a_run() -> None:
    with TestClient(app) as client:
        initial = client.get("/ui/")
        staged = client.post(
            "/ui/scenario/example",
            follow_redirects=True,
        )
        regenerated = client.post(
            "/ui/scenario/situations/regenerate",
            follow_redirects=True,
        )
        started = client.post(
            "/ui/run/start",
            data={"speed": "1"},
            follow_redirects=True,
        )
        paused = client.post("/ui/run/control/pause", follow_redirects=True)
        selected = client.post(
            "/ui/view",
            data={
                "selected_agent_present": "yes",
                "selected_agent": "agent-001",
            },
            follow_redirects=True,
        )
        managed = next(iter(app.state.simulation_manager._runs.values()))
        tick_before = managed.runner.clock.tick
        stepped = client.post("/ui/run/control/step", follow_redirects=True)
        tick_after = managed.runner.clock.tick
        mutated = client.post(
            "/ui/run/vitals",
            data={"satiety": "7", "energy": "", "stress": ""},
            follow_redirects=True,
        )
        zoomed = client.post("/ui/view/zoom", data={"zoom": "1.75"})
        invalid_zoom = client.post("/ui/view/zoom", data={"zoom": "4"})
        zoomed_page = client.get("/ui/")
        stopped = client.post("/ui/run/control/stop", follow_redirects=True)

    assert 'disabled>Start run</button>' in initial.text
    assert "validated and staged" in staged.text
    assert "Frozen character situations" in staged.text
    assert "Synthesis is" in staged.text
    assert "Staged scenario preview" in staged.text
    assert "not started" in staged.text
    assert "Character situations were regenerated and staged." in regenerated.text
    assert "Simulation started." in started.text
    assert ">Regenerate character situations</button>" not in started.text
    assert "disabled" in started.text
    assert 'aria-labelledby="environment-heading"' in started.text
    assert "Environment" in started.text
    assert '<option value="environment"' in started.text
    assert 'aria-label="Building view"' in started.text
    assert "Simulation paused." in paused.text
    assert 'value="agent-001" selected' in selected.text
    assert tick_after == tick_before + 1
    assert "Advanced one deterministic tick." in stepped.text
    assert "Updated vitals for agent-001." in mutated.text
    assert "homeostasis.mutated" in mutated.text
    assert zoomed.status_code == 204
    assert invalid_zoom.status_code == 400
    assert 'data-map-zoom="1.75"' in zoomed_page.text
    assert "Simulation stopped." in stopped.text
    assert ">Start run</button>" in stopped.text


def test_operator_view_allows_no_inspected_character_and_optional_follow() -> None:
    with TestClient(app) as client:
        staged = client.post("/ui/scenario/example", follow_redirects=True)
        started = client.post("/ui/run/start", data={"speed": "1"}, follow_redirects=True)
        selected = client.post(
            "/ui/view",
            data={
                "selected_agent_present": "yes",
                "selected_agent": "agent-001",
                "follow_present": "yes",
                "follow_selected": "on",
            },
            follow_redirects=True,
        )
        cleared = client.post(
            "/ui/view",
            data={
                "selected_agent_present": "yes",
                "selected_agent": "",
                "follow_present": "yes",
            },
            follow_redirects=True,
        )

    assert 'value="" selected>Not inspecting a character' in staged.text
    assert "Not inspecting a character. The world map remains free" in started.text
    assert 'name="follow_selected" checked' in selected.text
    assert 'value="" selected>Not inspecting a character' in cleared.text
    assert 'name="follow_selected" checked' not in cleared.text


def test_zoom_state_selects_semantic_detail_and_validates_camera() -> None:
    session = operator_ui.OperatorSession()

    assert operator_ui._effective_view_level(session) == "city"
    session.zoom = operator_ui.CITY_ZONE_ZOOM
    assert operator_ui._effective_view_level(session) == "city_zone"
    session.zoom = operator_ui.BUILDING_ZOOM
    assert operator_ui._effective_view_level(session) == "building"
    session.zoom = operator_ui.ROOM_ZOOM
    assert operator_ui._effective_view_level(session) == "room"
    session.view_level = "city"
    assert operator_ui._effective_view_level(session) == "city"

    with TestClient(app) as client:
        valid = client.post(
            "/ui/view/zoom",
            data={"zoom": "1.75", "camera_x": "0.2", "camera_y": "0.8"},
        )
        invalid = client.post(
            "/ui/view/zoom",
            data={"zoom": "1.75", "camera_x": "1.2", "camera_y": "0.8"},
        )

    assert valid.status_code == 204
    assert valid.headers["X-Stage0-Semantic-Level"] == "building"
    assert invalid.status_code == 400
    assert "camera coordinates must be between 0 and 1" in invalid.text


def test_physical_grid_view_uses_microcells_and_compact_footprints() -> None:
    world = {
        "width": 4,
        "height": 3,
        "zones": [
            {
                "id": "room",
                "name": "Room",
                "type": "TEST",
                "bounds": {"x": 0, "y": 0, "width": 4, "height": 3},
            }
        ],
        "blocked": [{"x": 3, "y": 2}],
        "stations": [],
        "transaction_points": [],
    }
    physical_room = {
        "spatial": {
            "coordinate_system": "microcell",
            "microcells_per_legacy_cell": 9,
            "width_microcells": 36,
            "height_microcells": 27,
            "width_legacy_cells": 4,
            "height_legacy_cells": 3,
        },
        "objects": [
            {
                "id": "front-door",
                "definition_id": "exterior-door",
                "name": "Front Door",
                "kind": "physical",
                "physical": {
                    "pose": {
                        "room_id": "room",
                        "anchor": {"x": 15, "y": 8},
                        "orientation": "EAST",
                    },
                    "footprint": {
                        "cells": [
                            {"x": 0, "y": 0},
                            {"x": 1, "y": 0},
                            {"x": 0, "y": 1},
                        ]
                    },
                    "occupied_cells": [
                        {"x": 14, "y": 8},
                        {"x": 15, "y": 8},
                        {"x": 15, "y": 9},
                    ],
                    "obstruction": {
                        "movement": "HARD",
                        "vision": "OPAQUE",
                        "blocks_movement": True,
                        "blocks_vision": True,
                    },
                    "openable": {"is_open": False, "is_locked": True},
                    "capabilities": {
                        "portable": None,
                        "support_slot_ids": [],
                        "container_slot_ids": ["inside"],
                        "openable": True,
                        "readable": False,
                        "consumable": None,
                        "usable": None,
                    },
                    "parent_relation": None,
                    "custodian_id": None,
                    "held_by": None,
                    "slots": [
                        {
                            "id": "inside",
                            "accepted_relations": ["IN_CONTAINER"],
                            "capacity": 1,
                            "occupancy": {
                                "entity_ids": ["parcel"],
                                "count": 1,
                                "remaining_capacity": 0,
                            },
                        }
                    ],
                    "spatial_indexed": True,
                    "interaction_target": {
                        "approach_anchors": [{"x": 13, "y": 8}],
                        "occupancy_anchors": {
                            "inside": [{"x": 15, "y": 8}]
                        },
                    },
                },
            },
            {
                "id": "room-window",
                "definition_id": "window",
                "name": "Open Window",
                "kind": "physical",
                "physical": {
                    "pose": {
                        "room_id": "room",
                        "anchor": {"x": 2, "y": 2},
                        "orientation": "NORTH",
                    },
                    "footprint": {"cells": [{"x": 0, "y": 0}]},
                    "occupied_cells": [{"x": 2, "y": 2}],
                    "obstruction": {},
                    "openable": {"is_open": True, "is_locked": False},
                    "capabilities": {"openable": True},
                    "held_by": None,
                    "slots": [],
                },
            },
            {
                "id": "parcel",
                "definition_id": "parcel",
                "name": "Portable Parcel",
                "kind": "physical",
                "physical": {
                    "pose": {
                        "room_id": "room",
                        "anchor": {"x": 10, "y": 10},
                        "orientation": "NORTH",
                    },
                    "footprint": {"cells": [{"x": 0, "y": 0}]},
                    "occupied_cells": [{"x": 10, "y": 10}],
                    "obstruction": {},
                    "openable": None,
                    "capabilities": {
                        "portable": {"two_handed": False}
                    },
                    "held_by": "agent",
                    "slots": [],
                },
            },
        ],
    }
    agent = {
        "id": "agent",
        "position": {"x": 1, "y": 1},
        "physical": {
            "occupied_cells": [
                {"x": x, "y": y}
                for y in range(8, 13)
                for x in range(8, 13)
            ],
            "posture": {"value": "SITTING", "support_id": "chair"},
            "hands": {
                "left_object_id": "parcel",
                "right_object_id": None,
                "held_object_ids": ["parcel"],
            },
        },
        "physical_movement": {
            "coordinate_system": "microcell",
            "destination": {"x": 20, "y": 10},
            "path": [{"x": 10, "y": 10}, {"x": 11, "y": 10}],
        },
        "interaction": {
            "request": {
                "verb": "OPEN",
                "target_id": "front-door",
                "destination_id": None,
                "slot_id": None,
                "status": "queued",
                "action": {
                    "action_id": "action-open-door",
                    "action_name": "OPEN",
                },
            },
            "execution": None,
        },
    }
    session = operator_ui.OperatorSession(
        selected_agent_id="agent",
        selected_object_id="front-door",
        zoom=operator_ui.MICROCELL_GRID_ZOOM,
    )

    view = operator_ui._grid_view(
        world,
        [agent],
        session,
        "Physical room",
        {},
        semantic_level="room",
        physical_room=physical_room,
        door_links={
            "front-door": [
                {
                    "kind": "entrance",
                    "id": "front",
                    "label": "Entrance front",
                    "coordinate": {"x": 15, "y": 8},
                }
            ]
        },
    )

    assert view["view_box"] == "0 0 36 27"
    assert view["base_display_width"] == 288
    assert view["zones"][0]["rectangles"] == [
        {"x": 0, "y": 0, "width": 36, "height": 27}
    ]
    assert view["blocked"] == [
        {"x": 27, "y": 18, "width": 9, "height": 9}
    ]
    assert view["show_microcell_grid"] is True
    assert view["paths"] == [
        {
            "agent_id": "agent",
            "name": "agent",
            "points": "10.5,10.5 11.5,10.5",
        }
    ]
    assert view["agents"][0]["body_rectangles"] == [
        {"x": 8, "y": 8, "width": 5, "height": 5}
    ]
    assert view["agents"][0]["posture"] == "SITTING"
    assert view["agents"][0]["held_object_ids"] == ["parcel"]
    rendered_object = next(
        item for item in view["objects"] if item["id"] == "front-door"
    )
    assert rendered_object["rectangles"] == [
        {"x": 14, "y": 8, "width": 2, "height": 1},
        {"x": 15, "y": 9, "width": 1, "height": 1},
    ]
    assert {
        "physical-object--door",
        "state-closed",
        "state-locked",
        "state-occupied",
        "door-linked",
        "selected",
    }.issubset(set(rendered_object["class_names"].split()))
    assert rendered_object["approach_anchors"] == [{"x": 13, "y": 8}]
    assert rendered_object["occupancy_anchors"] == [
        {"x": 15, "y": 8, "slot_id": "inside"}
    ]
    assert rendered_object["interaction_states"] == [
        {
            "agent_id": "agent",
            "phase": "request",
            "verb": "OPEN",
            "status": "queued",
            "slot_id": None,
            "action_id": "action-open-door",
            "action_name": "OPEN",
        }
    ]
    window = next(
        item for item in view["objects"] if item["id"] == "room-window"
    )
    assert {
        "physical-object--window",
        "state-open",
        "state-unlocked",
    }.issubset(set(window["class_names"].split()))
    parcel = next(
        item for item in view["objects"] if item["id"] == "parcel"
    )
    assert {
        "physical-object--portable",
        "state-held",
    }.issubset(set(parcel["class_names"].split()))

    session.zoom = operator_ui.MICROCELL_GRID_ZOOM - 0.01
    overview = operator_ui._grid_view(
        world,
        [],
        session,
        "Physical room",
        {},
        semantic_level="room",
        physical_room={
            **physical_room,
            "spatial": {
                **physical_room["spatial"],
                "width_microcells": 9000,
                "height_microcells": 7200,
            },
            "objects": [],
        },
    )
    assert overview["view_box"] == "0 0 9000 7200"
    assert overview["show_microcell_grid"] is False
    assert len(overview["zones"]) == 1
    assert overview["objects"] == []


def test_operator_renders_live_physical_state_and_object_inspector() -> None:
    with TestClient(app) as client:
        run_id, session = _attach_physical_operator_run(client)
        manager = app.state.simulation_manager
        assert isinstance(manager, SimulationManager)
        registry = manager.get_run(run_id).runner.registry
        cabinet = registry.get_component(
            "object-z-cabinet",
            OpenableComponent,
        )
        cabinet.is_locked = True
        posture = registry.get_component(
            "physical-agent",
            CharacterPostureComponent,
        )
        posture.posture = CharacterPosture.SITTING
        posture.support_id = "object-a-display"

        overview = client.get("/ui/")
        session.zoom = operator_ui.MICROCELL_GRID_ZOOM
        selected = client.post(
            "/ui/view",
            data={
                "selected_object_present": "yes",
                "selected_object": "object-z-cabinet",
            },
            follow_redirects=True,
        )
        hands = registry.get_component(
            "physical-agent",
            CharacterHandStateComponent,
        )
        hands.left_hand_object_id = "object-m-secret"
        agent_state = registry.get_component(
            "physical-agent",
            PhysicalStateComponent,
        )
        secret_state = registry.get_component(
            "object-m-secret",
            PhysicalStateComponent,
        )
        registry.set_component(
            "object-m-secret",
            replace(secret_state, pose=agent_state.pose),
        )
        registry.set_component(
            "object-m-secret",
            SpatialParentRelationComponent(
                "physical-agent",
                PhysicalRelationKind.HELD_BY,
                "left",
            ),
        )
        registry.set_component(
            "object-m-secret",
            CustodyComponent("physical-agent"),
        )
        held = client.get(
            "/ui/",
            params={"object": "object-m-secret"},
        )
        cleared = client.post(
            "/ui/view",
            data={
                "selected_object_present": "yes",
                "selected_object": "",
            },
            follow_redirects=True,
        )

    assert 'viewBox="0 0 36 27"' in overview.text
    assert 'data-coordinate-system="microcell"' in overview.text
    assert 'id="microcell-grid"' not in overview.text
    assert 'class="grid-line"' not in overview.text
    assert overview.text.count('class="grid-guide ') == 1
    assert 'data-object-id="object-m-secret"' in overview.text
    assert 'data-posture="SITTING"' in overview.text
    assert (
        'class="physical-object physical-object--furniture '
        'state-closed state-locked state-occupied'
    ) in selected.text
    assert 'id="microcell-grid"' in selected.text
    assert selected.text.count('class="grid-guide ') == 2
    assert 'value="object-z-cabinet" selected' in selected.text
    assert "Opaque Cabinet" in selected.text
    assert "anchor (15, 8) microcells · EAST" in selected.text
    assert "3 cells: (14, 8), (15, 8), (15, 9)" in selected.text
    assert "movement HARD · vision OPAQUE" in selected.text
    assert "occupied 1 · remaining 1" in selected.text
    assert 'data-held-object-ids="object-m-secret"' in held.text
    assert "Held by" in held.text
    assert "physical-agent" in held.text
    assert 'value="" selected>Not inspecting an object' in cleared.text


def test_operator_view_route_changes_and_clears_object_selection() -> None:
    with TestClient(app) as client:
        client.get("/ui/")
        session_id = client.cookies.get(SESSION_COOKIE)
        assert session_id is not None
        selected = client.post(
            "/ui/view",
            data={
                "selected_object_present": "yes",
                "selected_object": "object-1",
            },
            follow_redirects=False,
        )
        _, session = app.state.operator_sessions.get(session_id)
        assert session.selected_object_id == "object-1"
        cleared = client.post(
            "/ui/view",
            data={
                "selected_object_present": "yes",
                "selected_object": "",
            },
            follow_redirects=False,
        )

    assert selected.status_code == 303
    assert cleared.status_code == 303
    assert session.selected_object_id is None


def test_city_view_projects_edge_travel_and_declutters_labels() -> None:
    city = {
        "name": "Test City",
        "bounds": {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100},
        "districts": [
            {"id": "district", "name": "Central District", "center": {"x": 50, "y": 50}}
        ],
        "buildings": [
            {
                "id": "building",
                "name": "Central Building",
                "position": {"x": 50, "y": 50},
                "district_id": "district",
            }
        ],
        "outdoor_places": [
            {
                "id": "place",
                "name": "Central Plaza",
                "position": {"x": 50, "y": 50},
                "district_id": "district",
            }
        ],
        "nodes": [],
        "edges": [
            {
                "id": "edge",
                "geometry": [{"x": 0, "y": 50}, {"x": 100, "y": 50}],
            }
        ],
        "vehicles": [],
    }
    agents = [
        {
            "id": "a",
            "spatial_location": {"edge_id": "edge", "edge_progress": 0.5},
        },
        {
            "id": "b",
            "spatial_location": {"place_id": "building"},
        },
    ]
    session = operator_ui.OperatorSession(selected_agent_id="a", zoom=3.0)

    view = operator_ui._city_view(city, agents, session, "City", {}, [])

    assert len(view["agents"]) == 2
    assert view["agents"][0]["px"] == 500
    assert view["agents"][0]["py"] == 325
    assert (view["agents"][1]["px"], view["agents"][1]["py"]) != (500, 325)
    boxes = [
        (
            label["x"],
            label["y"] - label["height"],
            label["x"] + label["width"],
            label["y"],
        )
        for label in view["labels"]
    ]
    for index, box in enumerate(boxes):
        assert not any(
            operator_ui._boxes_overlap(box, other)
            for other in boxes[index + 1 :]
        )


def test_character_library_is_rendered_as_labeled_forms() -> None:
    with TestClient(app) as client:
        page = client.get("/ui/characters/?selected=alex-chen")
        download = client.get("/ui/characters/alex-chen/download")

    assert page.status_code == 200
    assert "<h1>Character Library</h1>" in page.text
    assert "Alex Chen" in page.text
    assert 'name="identity.display_name"' in page.text
    assert 'name="identity.birth_date"' in page.text
    assert 'name="body_measurements.height_cm"' in page.text
    assert 'name="financial_situation.total_debt"' in page.text
    assert 'name="family.members"' in page.text
    assert 'name="health.allergies"' in page.text
    assert 'name="relationships"' in page.text
    assert 'name="custom_sections"' in page.text
    assert 'action="/ui/characters/alex-chen/save"' in page.text
    assert 'action="/ui/characters/alex-chen/duplicate"' in page.text
    assert 'action="/ui/characters/alex-chen/delete"' in page.text
    assert download.status_code == 200
    assert download.json()["id"] == "alex-chen"


def test_bundled_demo_is_a_valid_runnable_scenario() -> None:
    with TestClient(app) as client:
        demo_response = client.get("/ui/demo.json")
        demo_character = json.loads(
            files("stage0_sim")
            .joinpath("resources", "demo-character.json")
            .read_text(encoding="utf-8")
        )
        character_response = client.post(
            "/characters",
            json=demo_character,
        )
        characters_response = client.get("/characters")
        demo = demo_response.json()
        scenario_response = client.post(
            "/simulation/scenarios",
            json={"scenario": demo, "character_assignments": {}},
        )
        run_response = client.post(
            "/simulation/runs",
            json={
                "scenario_id": scenario_response.json()["scenario_id"],
                "realtime": False,
            },
        )
        run_id = run_response.json()["run_id"]
        client.post(f"/simulation/runs/{run_id}/pause")
        step_response = client.post(f"/simulation/runs/{run_id}/step")
        snapshot = client.get(
            f"/simulation/runs/{run_id}/snapshot"
        ).json()["snapshot"]

    assert demo_response.status_code == 200
    assert character_response.status_code == 201
    assert characters_response.status_code == 200
    assert {"bundled-demo-character"}.issubset(
        {
            character["id"]
            for character in characters_response.json()["characters"]
        }
    )
    assert json.loads(demo_response.text)["name"] == "browser-survival-demo"
    assert scenario_response.status_code == 201
    assert scenario_response.json()["characters"][0]["character_id"] == (
        "bundled-demo-character"
    )
    assert run_response.status_code == 201
    assert step_response.json()["tick"] == 1
    assert snapshot["world"]["zones"]
    assert snapshot["agents"][0]["system1"]["active_drive"] == "SATIETY"


def test_selected_character_engagement_panel_and_filter_are_server_rendered() -> None:
    with TestClient(app) as client:
        run_id, session = _attach_physical_operator_run(client)
        managed = app.state.simulation_manager.get_run(run_id)
        lineage = {
            "engagement_id": "engagement-ui-1",
            "action_id": "action-ui-1",
            "plan_id": "plan-ui-1",
            "plan_revision": 3,
            "decision_id": "decision-ui-1",
            "tool_call_id": "tool-ui-1",
            "root_correlation_id": "decision-ui-1",
        }
        for event_type, payload in (
            (
                "engagement.requested",
                {
                    **lineage,
                    "reference_ids": ["physical-api-display"],
                    "intent": "PRIVATE UI INTENT",
                    "reason": "PRIVATE UI REASON",
                    "visibility": "private",
                },
            ),
            (
                "engagement.compilation_requested",
                {
                    **lineage,
                    "compiler_prompt": "PRIVATE COMPILER PROMPT",
                    "visibility": "private",
                },
            ),
            (
                "engagement.compilation_completed",
                {
                    **lineage,
                    "summary": "PRIVATE UI COMPILER SUMMARY",
                    "scene": {"private": "PRIVATE RAW SCENE"},
                    "visibility": "private",
                },
            ),
            (
                "engagement.capability_committed",
                {
                    **lineage,
                    "group_id": "gesture",
                    "group_ordinal": 0,
                    "invocation_id": "gesture-1",
                    "capability": "expressive_behavior",
                    "modality": "visual",
                    "public_text": "Alex gives a visible wave.",
                    "expression_band": "moderate",
                    "target_id": "physical-api-display",
                    "visibility": "private",
                },
            ),
            (
                "engagement.capability_committed",
                {
                    **lineage,
                    "group_id": "call",
                    "group_ordinal": 1,
                    "invocation_id": "call-1",
                    "capability": "auditory_expression",
                    "modality": "auditory",
                    "public_text": "Alex calls out clearly.",
                    "sound_band": "normal",
                    "recipient_ids": ["physical-agent"],
                    "listener_stress_delta": 50,
                    "visibility": "private",
                },
            ),
            (
                "engagement.partial",
                {
                    **lineage,
                    "completed_group_count": 2,
                    "failed_group_count": 1,
                    "reason": "PRIVATE UI TERMINAL REASON",
                    "group_statuses": [
                        {
                            "group_id": "gesture",
                            "group_ordinal": 0,
                            "status": "completed",
                        },
                        {
                            "group_id": "call",
                            "group_ordinal": 1,
                            "status": "completed",
                        },
                        {
                            "group_id": "blocked",
                            "group_ordinal": 2,
                            "status": "failed",
                            "failure_reason": "PRIVATE UI GROUP REASON",
                        },
                    ],
                },
            ),
        ):
            client.portal.call(
                lambda event_type=event_type, payload=payload: (
                    managed.runner.events.emit(
                        event_type,
                        simulation_tick=2,
                        simulation_time=2.0,
                        agent_id="physical-agent",
                        payload=payload,
                    )
                )
            )
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.compilation_failed",
                simulation_tick=3,
                simulation_time=3.0,
                agent_id="physical-agent",
                payload={
                    **lineage,
                    "engagement_id": "engagement-ui-2",
                    "action_id": "action-ui-2",
                    "reason": "PRIVATE UI COMPILATION FAILURE",
                    "summary": "PRIVATE UI FAILED SUMMARY",
                    "visibility": "private",
                },
            )
        )
        session.selected_agent_id = "physical-agent"

        page = client.get("/ui/?filter=engagement&order=oldest")

    assert page.status_code == 200
    assert 'action="/ui/"' in page.text
    assert '<option value="engagement" selected>Engagement</option>' in page.text
    assert "Current and recent engagement" in page.text
    assert "engagement-ui-1" in page.text
    assert "partial" in page.text
    assert "action-ui-1" in page.text
    assert "plan-ui-1" in page.text
    assert "physical-api-display" in page.text
    assert "gesture" in page.text
    assert "completed" in page.text
    assert "blocked" in page.text
    assert "failed" in page.text
    assert "visual" in page.text
    assert "Alex gives a visible wave." in page.text
    assert "auditory" in page.text
    assert "Alex calls out clearly." in page.text
    assert "Compilation pending" in page.text
    assert "Compilation succeeded" in page.text
    assert "Compilation failed" in page.text
    assert "Execution partially completed" in page.text
    event_log = re.search(
        r'<ol id="event-log".*?</ol>',
        page.text,
        flags=re.DOTALL,
    )
    assert event_log is not None
    assert "engagement.requested" in event_log.group()
    assert "simulation.started" not in event_log.group()
    for private_value in (
        "PRIVATE UI INTENT",
        "PRIVATE UI REASON",
        "PRIVATE COMPILER PROMPT",
        "PRIVATE UI COMPILER SUMMARY",
        "PRIVATE RAW SCENE",
        "PRIVATE UI TERMINAL REASON",
        "PRIVATE UI GROUP REASON",
        "PRIVATE UI COMPILATION FAILURE",
        "PRIVATE UI FAILED SUMMARY",
        "listener_stress_delta",
    ):
        assert private_value not in page.text
