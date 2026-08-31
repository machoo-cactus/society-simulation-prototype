import json
from importlib.resources import files

from fastapi.testclient import TestClient

from stage0_sim.api import ui as operator_ui
from stage0_sim.api.app import app


def test_ui_assets_are_installed_as_package_data() -> None:
    web = files("stage0_sim").joinpath("web")

    assert web.joinpath("base.html").is_file()
    assert web.joinpath("simulation.html").is_file()
    assert web.joinpath("characters.html").is_file()
    assert web.joinpath("scenarios.html").is_file()
    assert web.joinpath("scenario_fields.html").is_file()
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
    assert 'action="/ui/scenario/example"' in page.text
    assert 'action="/ui/run/start"' in page.text
    assert 'src="/ui/assets/enhancements.js"' in page.text
    assert "<canvas" not in page.text
    assert "WebSocket" not in page.text
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert enhancements.status_code == 200
    assert "navigator.clipboard.writeText" in enhancements.text


def test_server_rendered_ui_stages_then_controls_a_run() -> None:
    with TestClient(app) as client:
        initial = client.get("/ui/")
        staged = client.post(
            "/ui/scenario/example",
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
    assert "Staged scenario preview" in staged.text
    assert "not started" in staged.text
    assert "Simulation started." in started.text
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
    session.zoom = operator_ui.NEIGHBORHOOD_ZOOM
    assert operator_ui._effective_view_level(session) == "neighborhood"
    session.zoom = operator_ui.BUILDING_ZOOM
    assert operator_ui._effective_view_level(session) == "building"
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
    assert valid.headers["X-Stage0-Semantic-Level"] == "neighborhood"
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
