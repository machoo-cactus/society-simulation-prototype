import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from stage0_sim.api.app import app
from stage0_sim.application.data_management import selection_fingerprint


def load_scenario_payload(name: str) -> dict[str, Any]:
    path = Path(__file__).parents[1] / "scenarios" / name
    return json.loads(path.read_text(encoding="utf-8"))


def create_run(
    client: TestClient,
    scenario_name: str = "system1-preemption.json",
) -> str:
    scenario_response = client.post(
        "/simulation/scenarios",
        json={
            "scenario": load_scenario_payload(scenario_name),
            "character_assignments": {},
        },
    )
    assert scenario_response.status_code == 201
    scenario_id = scenario_response.json()["scenario_id"]
    run_response = client.post(
        "/simulation/runs",
        json={"scenario_id": scenario_id, "realtime": False},
    )
    assert run_response.status_code == 201
    return str(run_response.json()["run_id"])


def test_simulation_api_rejects_schema_v2_scenario_input() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/simulation/scenarios",
            json={
                "scenario": {
                    "schema_version": 2,
                    "name": "Legacy input",
                },
                "character_assignments": {},
            },
        )

    assert response.status_code == 422
    assert "Input should be 3" in response.text


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


def test_data_management_api_catalog_aggregate_exports_and_safe_delete() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        fingerprint = selection_fingerprint((run_id,))
        selection = {
            "run_ids": [run_id],
            "selection_fingerprint": fingerprint,
        }

        catalog = client.get(
            "/simulation/data/runs",
            params={"effective_status": "running", "limit": 10},
        )
        active_preview = client.post(
            "/simulation/data/deletion-preview",
            json=selection,
        )

        assert catalog.status_code == 200
        catalog_run = next(
            run for run in catalog.json()["runs"] if run["run_id"] == run_id
        )
        assert catalog_run["live"] is True
        assert catalog_run["effective_status"] == "running"
        assert catalog_run["live_cognition_phase"] == "idle"
        assert catalog_run["deletion_ready"] is False
        assert active_preview.status_code == 200
        assert active_preview.json()["eligible"] is False

        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        client.post(f"/simulation/runs/{run_id}/stop")
        aggregate = client.post(
            "/simulation/data/aggregate",
            json={**selection, "include_private_derived": True},
        )
        json_export = client.get(
            "/simulation/data/aggregate.json",
            params={
                "run_id": run_id,
                "selection_fingerprint": fingerprint,
                "include_private_derived": "false",
            },
        )
        csv_export = client.get(
            "/simulation/data/aggregate.csv",
            params={
                "run_id": run_id,
                "selection_fingerprint": fingerprint,
            },
        )
        preview = client.post(
            "/simulation/data/deletion-preview",
            json=selection,
        )
        stale = client.post(
            "/simulation/data/delete",
            json={
                **selection,
                "confirmation_token": "0" * 64,
                "confirmed": True,
                "confirmation_phrase": "DELETE 1 RUNS",
            },
        )
        deleted = client.post(
            "/simulation/data/delete",
            json={
                **selection,
                "confirmation_token": preview.json()["confirmation_token"],
                "confirmed": True,
                "confirmation_phrase": "DELETE 1 RUNS",
            },
        )

        assert aggregate.status_code == 200
        assert aggregate.json()["selection"]["run_ids"] == [run_id]
        assert aggregate.json()["private_derived_warning"] is not None
        assert json_export.status_code == 200
        assert json_export.json()["include_private_derived"] is False
        assert csv_export.status_code == 200
        assert "text/csv" in csv_export.headers["content-type"]
        assert preview.json()["eligible"] is True
        assert stale.status_code == 409
        assert deleted.status_code == 200
        assert client.get(f"/simulation/runs/{run_id}").status_code == 404
        assert client.get(f"/simulation/runs/{run_id}/data").status_code == 404


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
        mutated = next(
            event
            for event in events
            if event["event_type"] == "homeostasis.mutated"
        )
        operator_action_id = mutated["payload"]["action_id"]
        assert mutated["payload"]["action_origin"] == "operator"
        assert mutated["payload"]["operator_intervention_id"].startswith(
            "intervention-"
        )
        assert any(
            event["event_type"] == "action.completed"
            and event["payload"]["action_id"] == operator_action_id
            for event in events
        )
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
        assert after["latest_sequence"] == before["latest_sequence"]
        assert after["snapshot_revision"] > before["snapshot_revision"]


def test_websocket_stream_has_ordered_sequences_and_authoritative_snapshot() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        run = client.get(f"/simulation/runs/{run_id}").json()
        latest = run["latest_sequence"]

        with client.websocket_connect(
            f"/simulation/runs/{run_id}/stream?after_sequence={latest}"
            f"&after_snapshot_revision=0"
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

        assert [first["type"], second["type"]] == ["hello", "world_snapshot"]
        assert first["schema_version"] == "stage0.telemetry.v2"
        assert second["sequence"] == first["sequence"] == latest
        assert second["snapshot_revision"] >= 1
        assert second["simulation_tick"] == 1
        snapshot = second["payload"]
        assert snapshot["tick"] == 1
        assert snapshot["agents"][0]["position"] == {"x": 5, "y": 1}
        assert snapshot["agents"][0]["homeostasis"]["satiety"] == 9.95


def test_websocket_subscription_does_not_mutate_shared_sequence() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        before = client.get(f"/simulation/runs/{run_id}").json()

        with client.websocket_connect(
            f"/simulation/runs/{run_id}/stream"
            f"?after_sequence={before['latest_sequence']}"
            f"&after_snapshot_revision={before['snapshot_revision']}"
        ) as websocket:
            hello = websocket.receive_json()

        after = client.get(f"/simulation/runs/{run_id}").json()

    assert hello["type"] == "hello"
    assert after["latest_sequence"] == before["latest_sequence"]


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


def test_synthesis_enabled_scenario_requires_configured_provider() -> None:
    payload = load_scenario_payload("minimal.json")
    payload["character_situation_synthesis"] = {"enabled": True}

    with TestClient(app) as client:
        response = client.post(
            "/simulation/scenarios",
            json={"scenario": payload, "character_assignments": {}},
        )

    assert response.status_code == 422
    assert "no model provider is configured" in response.json()["detail"]


def test_dataset_query_exports_private_opt_in_and_persisted_stopped_events() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        assert client.post(f"/simulation/runs/{run_id}/stop").status_code == 200
        persisted_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]

        default_records = client.get(
            f"/simulation/runs/{run_id}/records",
            params={"limit": 2},
        ).json()
        next_records = client.get(
            f"/simulation/runs/{run_id}/records",
            params={"cursor": default_records["next_cursor"], "limit": 1000},
        ).json()
        private_records = client.get(
            f"/simulation/runs/{run_id}/records",
            params={
                "include_private": True,
                "visibility": "PRIVATE_RESEARCH",
                "limit": 1000,
            },
        ).json()
        rejected_private = client.get(
            f"/simulation/runs/{run_id}/records",
            params={"visibility": "PRIVATE_RESEARCH"},
        )
        schema = client.get(f"/simulation/runs/{run_id}/schema")
        bundle = client.get(f"/simulation/runs/{run_id}/exports/bundle")
        legacy_summary = client.get(f"/simulation/runs/{run_id}/data")
        legacy_export = client.get(f"/simulation/runs/{run_id}/export")

        stopped_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]

    with TestClient(app) as restarted_client:
        restarted_events = restarted_client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]
        missing_records_status = restarted_client.get(
            "/simulation/runs/missing/records"
        ).status_code

    returned = default_records["records"] + next_records["records"]
    assert all(
        record["visibility"] != "PRIVATE_RESEARCH" for record in returned
    )
    assert private_records["records"]
    assert all(
        record["visibility"] == "PRIVATE_RESEARCH"
        for record in private_records["records"]
    )
    assert rejected_private.status_code == 422
    assert schema.status_code == 200
    assert schema.json()["schema_id"] == "stage0.data_dictionary"
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist()[:3] == [
            "manifest.json",
            "schema.json",
            "records.ndjson",
        ]
    assert legacy_summary.status_code == 200
    assert "record_counts" in legacy_summary.json()
    assert legacy_export.status_code == 200
    assert json.loads(legacy_export.text.splitlines()[0])["record_type"] == "run"
    assert stopped_events == persisted_events
    assert restarted_events == persisted_events
    assert missing_records_status == 404
