import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from stage0_sim.api.app import app


def load_scenario_payload(name: str) -> dict[str, Any]:
    path = Path(__file__).parents[2] / "scenarios" / name
    return json.loads(path.read_text(encoding="utf-8"))


def create_run(
    client: TestClient,
    scenario_name: str = "system1-preemption.json",
) -> str:
    scenario_response = client.post(
        "/simulation/scenarios",
        json=load_scenario_payload(scenario_name),
    )
    assert scenario_response.status_code == 201
    scenario_id = scenario_response.json()["scenario_id"]
    run_response = client.post(
        "/simulation/runs",
        json={"scenario_id": scenario_id, "realtime": False},
    )
    assert run_response.status_code == 201
    return str(run_response.json()["run_id"])


def test_rest_lifecycle_step_speed_agent_and_event_history() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)

        initial = client.get(f"/simulation/runs/{run_id}")
        assert initial.json()["status"] == "running"
        assert initial.json()["tick"] == 0
        assert client.post(f"/simulation/runs/{run_id}/step").status_code == 409

        assert client.post(f"/simulation/runs/{run_id}/pause").status_code == 200
        speed = client.post(
            f"/simulation/runs/{run_id}/speed",
            json={"speed": 4},
        )
        assert speed.json() == {"speed": 4.0}
        step = client.post(f"/simulation/runs/{run_id}/step")
        assert step.status_code == 200
        assert step.json()["tick"] == 1

        agent = client.get(
            f"/simulation/runs/{run_id}/agents/agent-001"
        ).json()["agent"]
        assert agent["position"] == {"x": 5, "y": 1}
        assert agent["homeostasis"]["satiety"] == 9.95
        assert agent["system1"]["active_drive"] == "SATIETY"
        assert agent["movement"]["destination"] == {"x": 1, "y": 1}

        first_page = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"offset": 0, "limit": 2},
        ).json()
        second_page = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"offset": first_page["next_offset"], "limit": 100},
        ).json()
        assert len(first_page["events"]) == 2
        assert first_page["next_offset"] == 2
        assert first_page["total"] == len(first_page["events"]) + len(
            second_page["events"]
        )

        assert client.post(f"/simulation/runs/{run_id}/resume").status_code == 200
        assert client.post(f"/simulation/runs/{run_id}/stop").json() == {
            "status": "stopped"
        }


def test_controlled_vital_mutation_triggers_survival_on_next_step() -> None:
    with TestClient(app) as client:
        run_id = create_run(client, "fake-llm-planning.json")
        client.post(f"/simulation/runs/{run_id}/pause")

        mutation = client.patch(
            f"/simulation/runs/{run_id}/agents/agent-001/vitals",
            json={"satiety": 10},
        )
        assert mutation.status_code == 200
        assert mutation.json()["agent"]["homeostasis"]["satiety"] == 10

        client.post(f"/simulation/runs/{run_id}/step")
        agent = client.get(
            f"/simulation/runs/{run_id}/agents/agent-001"
        ).json()["agent"]
        assert agent["system1"]["active_drive"] == "SATIETY"
        events = client.get(f"/simulation/runs/{run_id}/events").json()["events"]
        assert any(event["event_type"] == "homeostasis.mutated" for event in events)
        assert any(event["event_type"] == "system1.activated" for event in events)


def test_telemetry_clock_does_not_advance_paused_simulation() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        before = client.get(f"/simulation/runs/{run_id}").json()

        time.sleep(0.25)

        after = client.get(f"/simulation/runs/{run_id}").json()
        assert after["tick"] == before["tick"] == 0
        assert after["simulation_time"] == before["simulation_time"] == 0.0
        assert after["latest_sequence"] > before["latest_sequence"]


def test_websocket_stream_has_ordered_sequences_and_authoritative_snapshot() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        latest = client.get(f"/simulation/runs/{run_id}").json()["latest_sequence"]

        with client.websocket_connect(
            f"/simulation/runs/{run_id}/stream?after_sequence={latest}"
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

        assert [first["type"], second["type"]] == [
            "simulation_status",
            "world_snapshot",
        ]
        assert second["sequence"] > first["sequence"] > latest
        assert second["simulation_tick"] == 1
        snapshot = second["payload"]
        assert snapshot["tick"] == 1
        assert snapshot["agents"][0]["position"] == {"x": 5, "y": 1}
        assert snapshot["agents"][0]["homeostasis"]["satiety"] == 9.95


def test_unknown_resources_and_invalid_mutation_are_rejected() -> None:
    with TestClient(app) as client:
        assert client.get("/simulation/runs/missing").status_code == 404
        run_id = create_run(client)
        invalid = client.patch(
            f"/simulation/runs/{run_id}/agents/agent-001/vitals",
            json={"stress": 101},
        )
        assert invalid.status_code == 422
        missing_agent = client.get(
            f"/simulation/runs/{run_id}/agents/missing"
        )
        assert missing_agent.status_code == 404
