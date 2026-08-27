import json
from importlib.resources import files

from fastapi.testclient import TestClient

from stage0_sim.api.app import app


def test_ui_assets_are_installed_as_package_data() -> None:
    web = files("stage0_sim").joinpath("web")

    assert web.joinpath("index.html").is_file()
    assert web.joinpath("styles.css").is_file()
    assert web.joinpath("app.js").is_file()
    assert web.joinpath("demo.json").is_file()


def test_root_redirects_to_python_served_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        page = client.get("/ui/")

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"
    assert page.status_code == 200
    assert 'id="world-canvas"' in page.text
    assert 'id="agent-select"' in page.text
    assert 'id="event-log"' in page.text
    assert 'src="/ui/app.js"' in page.text


def test_ui_assets_and_protocol_adapter_are_served() -> None:
    with TestClient(app) as client:
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")
        telemetry_hz = app.state.simulation_manager.telemetry_hz

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "function normalizeEnvelope" in script.text
    assert "function normalizeSnapshot" in script.text
    assert 'if (message.type === "world_snapshot")' in script.text
    assert "state.snapshot = normalizeSnapshot" in script.text
    assert "renderInspector();" in script.text
    assert "drawWorld();" in script.text
    assert 'setGauge("satiety", agent?.homeostasis.satiety);' in script.text
    assert 'elements[`${name}-gauge`].value = value;' in script.text
    assert telemetry_hz == 10.0
    assert "Telemetry gap" in script.text
    assert "scheduleReconnect" in script.text
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert "#world-canvas" in styles.text


def test_bundled_demo_is_a_valid_runnable_scenario() -> None:
    with TestClient(app) as client:
        demo_response = client.get("/ui/demo.json")
        demo = demo_response.json()
        scenario_response = client.post("/simulation/scenarios", json=demo)
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
    assert json.loads(demo_response.text)["name"] == "browser-survival-demo"
    assert scenario_response.status_code == 201
    assert run_response.status_code == 201
    assert step_response.json()["tick"] == 1
    assert snapshot["world"]["zones"]
    assert snapshot["agents"][0]["system1"]["active_drive"] == "SATIETY"
