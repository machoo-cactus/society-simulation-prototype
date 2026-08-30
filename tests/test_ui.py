import json
from importlib.resources import files

from fastapi.testclient import TestClient

from stage0_sim.api.app import app


def test_ui_assets_are_installed_as_package_data() -> None:
    web = files("stage0_sim").joinpath("web")

    assert web.joinpath("index.html").is_file()
    assert web.joinpath("styles.css").is_file()
    assert web.joinpath("app.js").is_file()
    assert web.joinpath("api-client.js").is_file()
    assert web.joinpath("protocol.js").is_file()
    assert web.joinpath("transcript-view.js").is_file()
    assert web.joinpath("character-editor.js").is_file()
    assert web.joinpath("characters-page.js").is_file()
    assert web.joinpath("characters.html").is_file()
    assert web.joinpath("ui-state.js").is_file()
    assert web.joinpath("demo.json").is_file()


def test_root_redirects_to_python_served_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        page = client.get("/ui/")

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"
    assert page.status_code == 200
    assert 'id="world-canvas"' in page.text
    assert 'id="zoom-in"' in page.text
    assert 'id="zoom-out"' in page.text
    assert 'id="focus-world"' in page.text
    assert "Load a scenario, assign characters" not in page.text
    assert 'id="agent-select"' in page.text
    assert 'id="event-log"' in page.text
    assert 'id="start-button"' in page.text
    assert 'id="character-assignments"' in page.text
    assert 'href="/ui/characters.html"' in page.text
    assert 'id="refresh-characters-button"' in page.text
    assert 'id="character-studio-panel"' not in page.text
    assert 'id="event-detail-dialog"' in page.text
    assert 'src="/ui/app.js"' in page.text


def test_ui_assets_and_protocol_adapter_are_served() -> None:
    with TestClient(app) as client:
        script = client.get("/ui/app.js")
        styles = client.get("/ui/styles.css")
        ui_state = client.get("/ui/ui-state.js")
        telemetry_hz = app.state.simulation_manager.telemetry_hz

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
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
    assert "async function loadScenario" in script.text
    assert "async function startLoadedScenario" in script.text
    assert "function renderCharacterAssignments" in script.text
    assert "character_id: profileId" in script.text
    assert "function showEventDetail" in script.text
    assert 'filter === "dialogue"' in script.text
    assert "/^(dialogue|speech)\\./" in script.text
    assert 'from "./ui-state.js"' in script.text
    assert "/ING$/" not in script.text
    assert "recoverTelemetry" in script.text
    assert "lastDomainEventOffset" in script.text
    assert "drawSpeechBubble" in script.text
    assert "auditoryUntil" in script.text
    assert "function drawCityWorld" in script.text
    assert "function ensureFocusedBuildingMap" in script.text
    assert "after_snapshot_revision" in script.text
    assert "function renderTranscript()" in script.text
    assert "runUiRuntimeSelfCheck" in script.text
    assert "cognitionPhase" in script.text
    assert "cognitionSettling" in ui_state.text
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert "#world-canvas" in styles.text

    with TestClient(app) as client:
        protocol = client.get("/ui/protocol.js")
        api_client = client.get("/ui/api-client.js")
        transcript = client.get("/ui/transcript-view.js")
        character_editor = client.get("/ui/character-editor.js")
        characters_page = client.get("/ui/characters-page.js")
        characters_html = client.get("/ui/characters.html")
    assert "function normalizeEnvelope" in protocol.text
    assert "stage0.telemetry.v2" in protocol.text
    assert "async function api" in api_client.text
    assert "function renderTranscriptView" in transcript.text
    assert "function createCharacterEditor" in character_editor.text
    assert "custom_sections" in character_editor.text
    assert "relationships" in character_editor.text
    assert "deepMergePreservingUnknown" in character_editor.text
    assert "syncScenario(nextScenario)" in character_editor.text
    assert "persistScenario" in characters_page.text
    assert "schemaVersions" in characters_page.text
    assert 'id="character-studio-panel"' in characters_html.text
    assert 'href="/ui/"' in characters_html.text


def test_ui_state_module_defines_explicit_pending_states() -> None:
    with TestClient(app) as client:
        module = client.get("/ui/ui-state.js")

    assert module.status_code == 200
    assert "PENDING_UI_STATES" in module.text
    assert "UI_STATES.RUN_STARTING" in module.text
    assert "UI_STATES.RUNNING" not in module.text.split(
        "PENDING_UI_STATES", 1
    )[1].split("]);", 1)[0]
    assert "pause: hasRun && running && !busy" in module.text


def test_bundled_demo_is_a_valid_runnable_scenario() -> None:
    with TestClient(app) as client:
        demo_response = client.get("/ui/demo.json")
        characters_response = client.get("/characters")
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
