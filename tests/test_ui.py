import json
import re
from html import unescape
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from stage0_sim.api import ui as operator_ui
from stage0_sim.api.app import app


def _create_persisted_dataset_run(
    client: TestClient,
    *,
    scenario_name: str | None = None,
) -> str:
    scenario = json.loads(
        (
            Path(__file__).parents[1]
            / "scenarios"
            / "system1-preemption.json"
        ).read_text(encoding="utf-8")
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


def test_ui_assets_are_installed_as_package_data() -> None:
    web = files("stage0_sim").joinpath("web")

    assert web.joinpath("base.html").is_file()
    assert web.joinpath("simulation.html").is_file()
    assert web.joinpath("characters.html").is_file()
    assert web.joinpath("scenarios.html").is_file()
    assert web.joinpath("elements.html").is_file()
    assert web.joinpath("scenario_fields.html").is_file()
    assert web.joinpath("dataset.html").is_file()
    assert web.joinpath("data_management.html").is_file()
    assert web.joinpath("styles.css").is_file()
    assert web.joinpath("enhancements.js").is_file()
    assert web.joinpath("demo.json").is_file()
    assert not web.joinpath("app.js").exists()


def test_root_redirects_to_accessible_server_rendered_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        legacy = client.get("/ui/characters.html", follow_redirects=False)
        page = client.get("/ui/")
        styles = client.get("/ui/assets/styles.css")
        enhancements = client.get("/ui/assets/enhancements.js")

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"
    assert legacy.status_code == 303
    assert legacy.headers["location"] == "/ui/characters/"
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


def test_reference_scenario_upload_resolves_shared_elements() -> None:
    root = Path(__file__).parents[1]
    scenario_path = root / "scenarios" / "reference-city-restaurants.json"

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
    assert "Download run dataset" in summary.text

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
        assert "PRIVATE_RESEARCH-derived rows" in selected_second.text
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
                "exclude_private_derived": "true",
            },
        )
        assert "0 runs selected across catalog pages." in cleared.text
        assert "2 runs selected across catalog pages." in selected_all.text
        assert "PRIVATE_RESEARCH-derived rows" not in excluded_private.text

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
    assert characters_response.status_code == 200
    assert {"alex-chen", "jordan-lee"}.issubset(
        {
            character["id"]
            for character in characters_response.json()["characters"]
        }
    )
    assert json.loads(demo_response.text)["name"] == "browser-survival-demo"
    assert scenario_response.status_code == 201
    assert scenario_response.json()["characters"][0]["character_id"] == (
        "alex-chen"
    )
    assert run_response.status_code == 201
    assert step_response.json()["tick"] == 1
    assert snapshot["world"]["zones"]
    assert snapshot["agents"][0]["system1"]["active_drive"] == "SATIETY"
