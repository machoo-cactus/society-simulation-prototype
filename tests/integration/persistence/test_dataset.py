import asyncio
import json
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.app import app
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.data_capture import DatasetQueryFilter
from stage0_sim.application.dataset import (
    DATASET_SCHEMA_VERSION,
    AgentStateProjector,
    DatasetRecord,
    DatasetRecordFilter,
    RecordCategory,
    RecordJoinIds,
    RecordRelation,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.cli import main
from stage0_sim.domain.components import HomeostasisComponent
from stage0_sim.domain.content import (
    TextAccessGrant,
    TextAccessPolicy,
    TextCollection,
    TextCollectionKind,
    TextContentRegistry,
    TextOperation,
    TextPrincipal,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.information import (
    InformationDocument,
    InformationSource,
    VisibilityLevel,
    VisibilityPolicy,
)
from tests.helpers.paths import CATALOG_CHARACTERS, CATALOG_SCENARIOS


def scenario_path(name: str) -> Path:
    return CATALOG_SCENARIOS / name


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
        "needs-and-preemption.json",
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
    assert payload["scenario"]["name"] == "system1-preemption"
    assert record_types.count("state_vector") == 11
    assert record_types.count("trajectory") == 11
    assert record_types.count("environment_state") == 11
    assert "activity_interval" in record_types
    assert "threshold_crossing" in record_types
    assert "plan_transition" in record_types
    assert "affordance" in record_types
    assert "memory_reference" in record_types
    assert summary["status"] == "completed"
    sequences = [int(record["sequence"]) for record in records]
    assert sequences == list(range(len(sequences)))




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
            subject_id="agent",
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
    assert records[1]["schema_version"] == DATASET_SCHEMA_VERSION
    assert records[1]["payload"] == {
        "known": 1,
        "future_nested_field": {"version": 7, "values": [1, 2, 3]},
    }


def test_v5_record_envelope_round_trips_all_dataset_metadata() -> None:
    record = DatasetRecord(
        run_id="round-trip",
        sequence=7,
        record_type="decision",
        simulation_tick=3,
        simulation_time=1.5,
        payload={"choice": "wait"},
        source_event_id="event-7",
        schema_id="stage0.decision.v1",
        schema_version="1",
        category=RecordCategory.DECISION,
        source=RecordSource.APPLICATION,
        phase=RunnerPhase.TICK_POST_COGNITION,
        wall_time="2026-09-01T00:00:00+00:00",
        visibility=RecordVisibility.PRIVATE_RESEARCH,
        subject_id="agent-001",
        related_entity_ids=("agent-002", "place-1"),
        causation_id="cause-1",
        correlation_id="correlation-1",
        joins=RecordJoinIds(
            goal_id="goal-1",
            decision_id="decision-1",
            model_request_id="request-1",
            tool_call_id="tool-1",
            engagement_id="engagement-1",
            engagement_group_id="group-1",
            engagement_invocation_id="invocation-1",
        ),
        source_metadata={"provider": "fake"},
    )

    restored = DatasetRecord.from_dict(record.to_dict())

    assert restored == record
    assert record.record_id == "round-trip:record:00000007"
    assert record.to_dict()["decision_id"] == "decision-1"
    assert record.to_dict()["engagement_id"] == "engagement-1"
    legacy_shape = record.to_dict()
    del legacy_shape["engagement_id"]
    del legacy_shape["engagement_group_id"]
    del legacy_shape["engagement_invocation_id"]
    assert DatasetRecord.from_dict(legacy_shape).joins.engagement_id is None


def test_dataset_record_rejects_positional_and_agent_id_compatibility() -> None:
    with pytest.raises(TypeError):
        DatasetRecord(  # type: ignore[call-arg]
            "old-run",
            1,
            "event",
            2,
            2.5,
            {"event_type": "old.event"},
        )
    with pytest.raises(TypeError, match="unexpected keyword argument 'agent_id'"):
        DatasetRecord(  # type: ignore[call-arg]
            run_id="old-run",
            sequence=1,
            record_type="event",
            simulation_tick=2,
            simulation_time=2.5,
            agent_id="agent-001",
            payload={"event_type": "old.event"},
        )

    content = DatasetRecord(
        run_id="current-run",
        sequence=1,
        record_type="event",
        simulation_tick=2,
        simulation_time=2.5,
        subject_id="agent-001",
        payload={"event_type": "current.event"},
    ).to_dict()
    content["agent_id"] = "agent-001"
    with pytest.raises(
        ValueError,
        match="agent_id is not supported; use subject_id",
    ):
        DatasetRecord.from_dict(content)


def test_noncurrent_database_schema_is_rejected_without_modification(
    tmp_path: Path,
) -> None:
    database = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE sentinel (value TEXT NOT NULL);
        INSERT INTO sentinel VALUES ('preserve-me');
        PRAGMA user_version = 3;
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match=(
            "unsupported SQLite schema version 3; expected 12. "
            "Existing databases are not migrated"
        ),
    ):
        SQLiteDatasetStore(database)

    connection = sqlite3.connect(database)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    sentinel = connection.execute("SELECT value FROM sentinel").fetchone()[0]
    connection.close()
    assert version == 3
    assert sentinel == "preserve-me"


def test_nonempty_schema_8_database_is_rejected_without_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema-8.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY);
        INSERT INTO runs VALUES ('preserve-schema-8');
        PRAGMA user_version = 8;
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match=(
            "unsupported SQLite schema version 8; expected 12. "
            "Existing databases are not migrated"
        ),
    ):
        SQLiteDatasetStore(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
    assert connection.execute("SELECT run_id FROM runs").fetchone()[0] == (
        "preserve-schema-8"
    )
    connection.close()


def test_fresh_database_creates_only_the_current_schema(tmp_path: Path) -> None:
    database = tmp_path / "current.sqlite3"
    store = SQLiteDatasetStore(database)
    store.close()

    connection = sqlite3.connect(database)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    record_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(records)")
    }
    action_columns = {
        row[1]: row[4]
        for row in connection.execute("PRAGMA table_info(action_instances)")
    }
    physical_state_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(physical_object_states)"
        )
    }
    interaction_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(interactions)")
    }
    connection.close()

    assert version == 12
    assert "subject_id" in record_columns
    assert "agent_id" not in record_columns
    assert action_columns["root_correlation_id"] is None
    assert {
        "object_id",
        "room_id",
        "parent_id",
        "relation_kind",
        "is_open",
        "is_locked",
        "spatial_index_revision",
        "topology_revision",
        "state_json",
    } <= physical_state_columns
    assert {
        "interaction_verb",
        "actor_id",
        "target_id",
        "destination_id",
        "slot_id",
        "action_id",
        "decision_id",
        "tool_call_id",
        "correlation_id",
    } <= interaction_columns


def test_unversioned_existing_database_is_rejected_without_recreation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unversioned.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE sentinel (value TEXT NOT NULL);
        INSERT INTO sentinel VALUES ('preserve-me');
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match="unsupported SQLite schema version 0; expected 12",
    ):
        SQLiteDatasetStore(database)

    connection = sqlite3.connect(database)
    sentinel = connection.execute("SELECT value FROM sentinel").fetchone()[0]
    connection.close()
    assert sentinel == "preserve-me"


def test_relational_foundation_tables_and_explicit_projection_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "relations.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="relations",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "relations"},
    )
    record = DatasetRecord(
        run_id="relations",
        sequence=1,
        record_type="state_sample",
        simulation_tick=1,
        simulation_time=1,
        subject_id="agent-001",
        payload={"energy": 80},
        category=RecordCategory.STATE,
        phase=RunnerPhase.TICK_POST_SYSTEMS,
    )
    store.append(record)
    store.add_record_relation(
        RecordRelation(
            run_id="relations",
            record_id=record.record_id,
            relation_type="subject",
            target_type="entity",
            target_id="agent-001",
        )
    )
    store.append_state_sample(
        run_id="relations",
        state_sample_id="sample-1",
        record_id=record.record_id,
        subject_id="agent-001",
        phase=RunnerPhase.TICK_POST_SYSTEMS,
        simulation_tick=1,
        simulation_time=1,
        state={"energy": 80},
    )
    store.flush()
    store.close()

    expected = {
        "record_relations",
        "state_samples",
        "state_deltas",
        "physical_object_states",
        "physical_relation_samples",
        "goals",
        "goal_transitions",
        "decisions",
        "decision_options",
        "model_requests",
        "model_turns",
        "tool_executions",
        "action_instances",
        "action_transitions",
        "interactions",
        "interaction_participants",
        "interaction_events",
        "interaction_episodes",
        "perception_facts",
        "perception_deliveries",
        "opportunity_samples",
        "transition_samples",
        "population_samples",
        "goal_episodes",
        "resource_samples",
        "resource_flows",
        "memory_relations",
    }
    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    relation_count = connection.execute(
        "SELECT COUNT(*) FROM record_relations"
    ).fetchone()[0]
    sample = connection.execute(
        "SELECT state_json FROM state_samples"
    ).fetchone()[0]
    connection.close()

    assert expected <= tables
    assert relation_count == 1
    assert json.loads(sample) == {"energy": 80}


def test_raw_record_filtering_and_stable_sequence_pagination(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "queries.sqlite3")
    store.begin_run(
        run_id="queries",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "queries"},
    )
    for sequence, category, visibility, agent_id in (
        (1, RecordCategory.STATE, RecordVisibility.OPERATOR, "agent-001"),
        (2, RecordCategory.STATE, RecordVisibility.PRIVATE_RESEARCH, "agent-001"),
        (3, RecordCategory.DECISION, RecordVisibility.OPERATOR, "agent-002"),
        (4, RecordCategory.STATE, RecordVisibility.OPERATOR, "agent-001"),
    ):
        store.append(
            DatasetRecord(
                run_id="queries",
                sequence=sequence,
                record_type="sample",
                simulation_tick=sequence,
                simulation_time=float(sequence),
                subject_id=agent_id,
                payload={"sequence": sequence},
                schema_id="stage0.sample.v1",
                category=category,
                visibility=visibility,
            )
        )
    store.flush()

    first_page = store.query_records(
        "queries",
        DatasetRecordFilter(
            record_type="sample",
            category=RecordCategory.STATE,
            schema_id="stage0.sample.v1",
            subject_id="agent-001",
            minimum_tick=1,
            maximum_tick=4,
            visibility=RecordVisibility.OPERATOR,
            limit=1,
        ),
    )
    second_page = store.query_records(
        "queries",
        DatasetRecordFilter(
            category=RecordCategory.STATE,
            visibility=RecordVisibility.OPERATOR,
            after_sequence=first_page.next_cursor,
            limit=1,
        ),
    )
    store.close()

    assert [record.sequence for record in first_page.records] == [1]
    assert first_page.next_cursor == 1
    assert [record.sequence for record in second_page.records] == [4]
    assert second_page.next_cursor is None


def test_summary_includes_category_visibility_and_schema_counts(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "summary-v2.sqlite3")
    store.begin_run(
        run_id="summary-v2",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "summary-v2"},
    )
    store.append(
        DatasetRecord(
            run_id="summary-v2",
            sequence=1,
            record_type="decision",
            simulation_tick=1,
            simulation_time=1,
            subject_id="agent-001",
            payload={},
            schema_id="stage0.decision.v1",
            schema_version="1",
            category=RecordCategory.DECISION,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
    )
    store.flush()

    public_summary = store.summary("summary-v2")
    summary = store.summary("summary-v2", include_private=True)
    store.close()

    assert summary["record_counts"] == {"decision": 1}
    assert summary["category_counts"] == {"DECISION": 1}
    assert summary["visibility_counts"] == {"PRIVATE_RESEARCH": 1}
    assert summary["schema_counts"] == {"stage0.decision.v1": 1}
    assert summary["schema_version_counts"] == {"1": 1}
    assert public_summary["record_counts"] == {}
    assert public_summary["entity_counts"] == {}


def test_private_decision_context_uses_private_projection_visibility(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "private-decision.sqlite3")
    store.begin_run(
        run_id="private-decision",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "private-decision"},
    )
    operator_record = DatasetRecord(
        run_id="private-decision",
        sequence=1,
        record_type="cognition.requested",
        simulation_tick=1,
        simulation_time=1,
        subject_id="agent-001",
        payload={},
        visibility=RecordVisibility.OPERATOR,
    )
    private_record = DatasetRecord(
        run_id="private-decision",
        sequence=2,
        record_type="decision_request",
        simulation_tick=1,
        simulation_time=1,
        subject_id="agent-001",
        payload={"secret": "private context"},
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    store.append(operator_record)
    store.append(private_record)
    store.append_decision(
        run_id="private-decision",
        decision_id="decision-1",
        record_id=operator_record.record_id,
        subject_id="agent-001",
        simulation_tick=1,
        status="requested",
        selected_option_id=None,
        context={},
    )
    store.append_decision(
        run_id="private-decision",
        decision_id="decision-1",
        record_id=private_record.record_id,
        subject_id="agent-001",
        simulation_tick=1,
        status="requested",
        selected_option_id=None,
        context={"secret": "private context"},
    )
    store.flush()

    public_page = store.query_table(
        "private-decision",
        "decisions",
        DatasetQueryFilter(include_private=False),
    )
    private_page = store.query_table(
        "private-decision",
        "decisions",
        DatasetQueryFilter(include_private=True),
    )
    store.close()

    assert public_page.rows == ()
    assert private_page.rows[0]["context"] == {
        "secret": "private context"
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
        "homeostasis": {
            "satiety": 70,
            "energy": 80,
            "stress": 20,
            "hydration": 100.0,
            "social_connection": 50.0,
            "happiness": 50.0,
            "fear": 0.0,
        }
    }


def test_telemetry_sampling_does_not_add_canonical_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = SQLiteDatasetStore(tmp_path / "telemetry.sqlite3")
        manager = SimulationManager(
            dataset_store=store,
            character_library=FileSystemCharacterLibrary(
                CATALOG_CHARACTERS
            ),
            telemetry_hz=50,
        )
        scenario = load_scenario(scenario_path("needs-and-preemption.json"))
        scenario_id = await manager.add_scenario(scenario)
        run_id = await manager.start_run(scenario_id, realtime=False)
        manager.pause(run_id)
        before = store.summary(run_id)["record_counts"]
        await asyncio.sleep(0.08)
        after = store.summary(run_id)["record_counts"]
        assert before == after
        assert manager.get_run(run_id).broker.latest_sequence > 0
        await manager.close()

    asyncio.run(exercise())


def test_manager_close_preserves_failed_realtime_task_status(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        database = tmp_path / "failed-realtime.sqlite3"
        manager = SimulationManager(
            dataset_store=SQLiteDatasetStore(database),
            character_library=FileSystemCharacterLibrary(
                CATALOG_CHARACTERS
            ),
        )
        scenario = load_scenario(scenario_path("needs-and-preemption.json"))
        scenario_id = await manager.add_scenario(scenario)
        run_id = await manager.start_run(scenario_id, realtime=False)
        managed = manager.get_run(run_id)

        async def fail_realtime() -> None:
            raise RuntimeError("realtime loop failed")

        managed.realtime_task = asyncio.create_task(fail_realtime())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="realtime loop failed"):
            await manager.close()

        reopened = SQLiteDatasetStore(database)
        summary = reopened.summary(run_id)
        reopened.close()
        assert summary["status"] == "failed"
        assert summary["capture_complete"] is False

    asyncio.run(exercise())


def test_api_exposes_summary_and_versioned_jsonl_export() -> None:
    resources = files("stage0_sim").joinpath("resources")
    demo = json.loads(resources.joinpath("demo.json").read_text(encoding="utf-8"))
    demo_character = json.loads(
        resources.joinpath("demo-character.json").read_text(encoding="utf-8")
    )
    with TestClient(app) as client:
        assert client.post("/characters", json=demo_character).status_code == 201
        scenario_id = client.post(
            "/simulation/scenarios",
            json={"scenario": demo, "character_assignments": {}},
        ).json()["scenario_id"]
        run_id = client.post(
            "/simulation/runs",
            json={"scenario_id": scenario_id, "realtime": False},
        ).json()["run_id"]
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        client.post(f"/simulation/runs/{run_id}/stop")
        public_summary = client.get(f"/simulation/runs/{run_id}/data")
        summary = client.get(
            f"/simulation/runs/{run_id}/data",
            params={"include_private": True},
        )
        export = client.get(
            f"/simulation/runs/{run_id}/exports/complete"
        )

    assert summary.status_code == 200
    assert summary.json()["schema_version"] == DATASET_SCHEMA_VERSION
    assert summary.json()["record_counts"]["state_vector"] == 2
    assert "state_vector" not in public_summary.json()["record_counts"]
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in export.text.splitlines()]
    assert lines[0]["record_type"] == "run"
    assert any(line["record_type"] == "state_vector" for line in lines)
    assert any(line["record_type"] == "environment_state" for line in lines)


def test_cli_persists_and_exports_without_ui(tmp_path: Path) -> None:
    database = tmp_path / "cli.sqlite3"
    export = tmp_path / "cli.jsonl"
    events = tmp_path / "events.jsonl"

    exit_code = main(
        [
            "run",
            str(scenario_path("baseline.json")),
            "--ticks",
            "2",
            "--characters-dir",
            str(CATALOG_CHARACTERS),
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

    with pytest.raises(
        RuntimeError,
        match="unsupported SQLite schema version 999; expected 12",
    ):
        SQLiteDatasetStore(database)


def test_jsonl_export_includes_every_information_document_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "documents.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="document-export",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"name": "document-export"},
    )
    original = InformationDocument.create(
        id="known-place",
        namespace_id="character:agent-001",
        kind="knowledge.place",
        schema_id="knowledge.place.v1",
        subject_ids=("agent-001", "place-a"),
        content={"name": "First name", "nested": {"values": [1, 2]}},
        source=InformationSource(
            type="SCENARIO",
            observer_id="agent-001",
            reference_ids=("source-1",),
            metadata={"confidence": 0.8},
        ),
        recorded_at=1,
        visibility=VisibilityPolicy(
            level=VisibilityLevel.SHARED,
            owner_ids=("agent-001",),
            reader_ids=("agent-002",),
        ),
    )
    revised = InformationDocument.create(
        id=original.id,
        namespace_id=original.namespace_id,
        kind=original.kind,
        schema_id=original.schema_id,
        subject_ids=original.subject_ids,
        content={"name": "Revised name", "nested": {"values": [1, 2, 3]}},
        source=original.source,
        recorded_at=2,
        visibility=original.visibility,
        revision=2,
    )
    store.save_information_document("document-export", revised)
    store.save_information_document("document-export", original)

    exported = parse_export(store, "document-export")
    store.close()

    documents = [
        record
        for record in exported
        if record["record_type"] == "information_document"
    ]
    assert [record["sequence"] for record in exported] == [0, 1, 2]
    assert [record["payload"] for record in documents] == [
        original.to_dict(),
        revised.to_dict(),
    ]


def test_text_content_snapshot_round_trips_and_exports_privately(
    tmp_path: Path,
) -> None:
    database = tmp_path / "text-content.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="text-content",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario={"schema_version": 9, "name": "text-content"},
    )
    registry = TextContentRegistry(
        collections=(
            TextCollection(
                "documents",
                TextCollectionKind.DOCUMENT_SET,
                1,
                (),
                10,
                TextAccessPolicy(
                    (
                        TextAccessGrant(
                            TextOperation.CREATE,
                            (TextPrincipal.public(),),
                        ),
                    )
                ),
            ),
        )
    )

    store.save_text_content_snapshot("text-content", registry.to_dict())

    assert store.load_text_content_snapshot("text-content") == registry.to_dict()
    exported = parse_export(store, "text-content")
    snapshot = next(
        record
        for record in exported
        if record["record_type"] == "text_content_snapshot"
    )
    assert snapshot["visibility"] == "PRIVATE_RESEARCH"
    assert snapshot["payload"] == registry.to_dict()
    store.close()


def test_completed_navigation_knowledge_is_persisted_and_exported(
    tmp_path: Path,
) -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "persist-learned-route",
            "world": {
                "width": 2,
                "height": 1,
                "zones": [
                    {
                        "id": "lounge",
                        "name": "Lounge",
                        "type": "LOUNGE",
                        "tiles": [{"x": 1, "y": 0}],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "plan": {
                            "queue": [
                                {"action": "NAVIGATE", "target": "lounge"}
                            ]
                        },
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario, run_id="persist-learned-route")
    store = SQLiteDatasetStore(tmp_path / "learned-route.sqlite3")
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(5)
    collector.finalize()

    persisted = tuple(
        document
        for document in store.load_information_documents(
            "persist-learned-route"
        )
        if document.kind == "knowledge.route"
        and document.source.type == "DIRECT_EXPERIENCE"
    )
    exported = [
        record
        for record in parse_export(store, "persist-learned-route")
        if record["record_type"] == "information_document"
        and isinstance(record["payload"], dict)
        and record["payload"]["kind"] == "knowledge.route"
    ]
    store.close()

    assert len(persisted) == 1
    assert persisted[0].content["destination_id"] == "lounge"
    assert [record["payload"] for record in exported] == [
        persisted[0].to_dict()
    ]
