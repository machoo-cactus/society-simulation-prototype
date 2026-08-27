import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.app import app
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.dataset import (
    DATASET_SCHEMA_VERSION,
    AgentStateProjector,
    DatasetRecord,
)
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import create_runner, load_scenario
from stage0_sim.cli import main
from stage0_sim.domain.components import HomeostasisComponent
from stage0_sim.domain.ecs import Registry


def scenario_path(name: str) -> Path:
    return Path(__file__).parents[2] / "scenarios" / name


def collect_run(
    database: Path,
    scenario_name: str,
    ticks: int,
) -> tuple[SQLiteDatasetStore, str]:
    scenario = load_scenario(scenario_path(scenario_name))
    runner = create_runner(scenario, run_id=f"dataset-{scenario_name}")
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )
    runner.run_for(ticks)
    collector.finalize()
    return store, runner.events.run_id


def parse_export(store: SQLiteDatasetStore, run_id: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in store.iter_jsonl(run_id)]


def test_completed_run_contains_offline_analysis_records(tmp_path: Path) -> None:
    store, run_id = collect_run(
        tmp_path / "dataset.sqlite3",
        "system1-preemption.json",
        10,
    )

    records = parse_export(store, run_id)
    manifest = records[0]
    record_types = [record["record_type"] for record in records[1:]]
    summary = store.summary(run_id)
    store.close()

    assert manifest["schema_version"] == DATASET_SCHEMA_VERSION
    assert manifest["record_type"] == "run"
    payload = manifest["payload"]
    assert isinstance(payload, dict)
    assert payload["seed"] == 20260824
    assert payload["final_tick"] == 10
    assert payload["scenario"]["name"] == "phase-4-system1-preemption"
    assert record_types.count("state_vector") == 11
    assert record_types.count("trajectory") == 11
    assert "activity_interval" in record_types
    assert "threshold_crossing" in record_types
    assert "plan_transition" in record_types
    assert "affordance" in record_types
    assert "memory_reference" in record_types
    assert summary["status"] == "completed"
    sequences = [int(record["sequence"]) for record in records]
    assert sequences == list(range(len(sequences)))


def test_planner_provider_metadata_is_exported(tmp_path: Path) -> None:
    store, run_id = collect_run(
        tmp_path / "planner.sqlite3",
        "fake-llm-planning.json",
        2,
    )

    records = parse_export(store, run_id)
    llm_record = next(
        record for record in records if record["record_type"] == "llm_request"
    )
    store.close()

    payload = llm_record["payload"]
    assert isinstance(payload, dict)
    assert payload["operation"] == "plan"
    assert payload["status"] == "completed"
    assert payload["provider"] == "fake"
    assert payload["latency_ms"] == 0.0
    assert payload["input_tokens"] == 0
    assert payload["output_tokens"] == 0


def test_unknown_payload_fields_survive_sqlite_restart(tmp_path: Path) -> None:
    database = tmp_path / "evolution.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="evolution-run",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "evolution"},
    )
    store.append(
        DatasetRecord(
            run_id="evolution-run",
            sequence=1,
            record_type="future_component",
            simulation_tick=1,
            simulation_time=1,
            agent_id="agent",
            payload={
                "known": 1,
                "future_nested_field": {"version": 7, "values": [1, 2, 3]},
            },
        )
    )
    store.flush()
    store.close()

    reopened = SQLiteDatasetStore(database)
    records = parse_export(reopened, "evolution-run")
    reopened.close()

    assert records[1]["record_type"] == "future_component"
    assert records[1]["payload"] == {
        "known": 1,
        "future_nested_field": {"version": 7, "values": [1, 2, 3]},
    }


@dataclass
class FutureComponent:
    value: str


def test_projector_ignores_new_components_without_breaking_stable_state() -> None:
    registry = Registry()
    registry.create_entity("agent")
    registry.add_component(
        "agent",
        HomeostasisComponent(satiety=70, energy=80, stress=20),
    )
    registry.add_component("agent", FutureComponent("new internal field"))

    projected = AgentStateProjector().project(registry, "agent")

    assert projected == {
        "homeostasis": {"satiety": 70, "energy": 80, "stress": 20}
    }


def test_telemetry_sampling_does_not_add_canonical_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = SQLiteDatasetStore(tmp_path / "telemetry.sqlite3")
        manager = SimulationManager(dataset_store=store, telemetry_hz=50)
        scenario = load_scenario(scenario_path("system1-preemption.json"))
        scenario_id = manager.add_scenario(scenario)
        run_id = await manager.start_run(scenario_id, realtime=False)
        manager.pause(run_id)
        before = store.summary(run_id)["record_counts"]
        await asyncio.sleep(0.08)
        after = store.summary(run_id)["record_counts"]
        assert before == after
        assert manager.get_run(run_id).broker.latest_sequence > 0
        await manager.close()

    asyncio.run(exercise())


def test_api_exposes_summary_and_versioned_jsonl_export() -> None:
    demo = json.loads(
        (
            Path(__file__).parents[1]
            / "stage0_sim"
            / "web"
            / "demo.json"
        ).read_text(encoding="utf-8")
    )
    with TestClient(app) as client:
        scenario_id = client.post(
            "/simulation/scenarios", json=demo
        ).json()["scenario_id"]
        run_id = client.post(
            "/simulation/runs",
            json={"scenario_id": scenario_id, "realtime": False},
        ).json()["run_id"]
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        client.post(f"/simulation/runs/{run_id}/stop")
        summary = client.get(f"/simulation/runs/{run_id}/data")
        export = client.get(f"/simulation/runs/{run_id}/export")

    assert summary.status_code == 200
    assert summary.json()["schema_version"] == DATASET_SCHEMA_VERSION
    assert summary.json()["record_counts"]["state_vector"] == 2
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in export.text.splitlines()]
    assert lines[0]["record_type"] == "run"
    assert any(line["record_type"] == "state_vector" for line in lines)


def test_cli_persists_and_exports_without_ui(tmp_path: Path) -> None:
    database = tmp_path / "cli.sqlite3"
    export = tmp_path / "cli.jsonl"
    events = tmp_path / "events.jsonl"

    exit_code = main(
        [
            "run",
            str(scenario_path("minimal.json")),
            "--ticks",
            "2",
            "--database",
            str(database),
            "--export",
            str(export),
            "--output",
            str(events),
        ]
    )

    assert exit_code == 0
    assert database.exists()
    records = [
        json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["record_type"] == "run"
    assert records[0]["payload"]["final_tick"] == 2


def test_newer_database_schema_is_rejected_explicitly(tmp_path: Path) -> None:
    database = tmp_path / "newer.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(RuntimeError, match="newer than supported"):
        SQLiteDatasetStore(database)
