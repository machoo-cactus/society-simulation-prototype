import csv
import io
import json
import zipfile
from pathlib import Path

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetRecord,
    DatasetRecordFilter,
    RecordCategory,
    RecordJoinIds,
    RecordVisibility,
)


def _query_store(path: Path) -> SQLiteDatasetStore:
    store = SQLiteDatasetStore(path)
    store.begin_run(
        run_id="query-run",
        seed=7,
        dt=1,
        initial_speed=1,
        scenario={"name": "query-run"},
    )
    for sequence, goal_id, visibility, description in (
        (1, "goal-1", RecordVisibility.OPERATOR, 'Ask, "clearly"\nthen listen'),
        (2, "goal-2", RecordVisibility.PUBLIC, "Leave safely"),
        (3, "goal-private", RecordVisibility.PRIVATE_RESEARCH, "Private motive"),
    ):
        record = DatasetRecord(
            run_id="query-run",
            sequence=sequence,
            record_type="goal",
            simulation_tick=sequence,
            simulation_time=float(sequence) + 0.5,
            agent_id="alice",
            subject_id="alice",
            related_entity_ids=("bob",),
            payload={"status": "active", "description": description},
            schema_id="stage0.goal.runtime",
            schema_version="1",
            category=RecordCategory.GOAL,
            visibility=visibility,
            joins=RecordJoinIds(goal_id=goal_id),
        )
        store.append(record)
        store.append_goal(
            run_id="query-run",
            goal_id=goal_id,
            record_id=record.record_id,
            subject_id="alice",
            description=description,
            status="active",
            goal=record.payload,
        )
    action = DatasetRecord(
        run_id="query-run",
        sequence=4,
        record_type="action_episode",
        simulation_tick=4,
        simulation_time=4.5,
        agent_id="alice",
        payload={"status": "completed", "terminal_status": "completed"},
        category=RecordCategory.ACTION,
        joins=RecordJoinIds(action_id="action-1"),
    )
    store.append(action)
    store.append_action_instance(
        run_id="query-run",
        action_id="action-1",
        record_id=action.record_id,
        plan_id=None,
        goal_id="goal-1",
        decision_id=None,
        tool_call_id=None,
        subject_id="alice",
        action_type="say",
        status="completed",
        origin="controller",
        plan_revision=None,
        created_tick=1,
        created_at=1.5,
        root_correlation_id="root-1",
        action={"text": "Hello, Bob"},
    )
    store.append_action_episode(
        run_id="query-run",
        action_id="action-1",
        record_id=action.record_id,
        subject_id="alice",
        terminal_status="completed",
        created_tick=1,
        terminal_tick=4,
        created_at=1.5,
        terminal_at=4.5,
        elapsed_simulation_time=3,
        source_event_ids=("event-1",),
        episode=action.payload,
    )
    store.flush()
    return store


def test_extended_filters_private_defaults_and_stable_table_cursors(
    tmp_path: Path,
) -> None:
    store = _query_store(tmp_path / "query.sqlite3")

    raw = store.query_records(
        "query-run",
        DatasetRecordFilter(
            related_entity_id="bob",
            goal_id="goal-2",
            minimum_time=2,
            maximum_time=3,
            status="active",
            include_private=False,
        ),
    )
    first = store.query_table(
        "query-run",
        "goals",
        DatasetQueryFilter(primary_entity_id="alice", limit=1),
    )
    second = store.query_table(
        "query-run",
        "goals",
        DatasetQueryFilter(
            primary_entity_id="alice",
            cursor=first.next_cursor,
            limit=1,
        ),
    )
    private = store.query_table(
        "query-run",
        "goals",
        DatasetQueryFilter(include_private=True),
    )
    store.close()

    assert [record.sequence for record in raw.records] == [2]
    assert [row["goal_id"] for row in first.rows] == ["goal-1"]
    assert first.next_cursor is not None
    assert [row["goal_id"] for row in second.rows] == ["goal-2"]
    assert second.next_cursor is None
    assert [row["record_visibility"] for row in private.rows] == [
        "OPERATOR",
        "PUBLIC",
        "PRIVATE_RESEARCH",
    ]


def test_data_dictionary_summary_and_analysis_bundle_are_exact_and_stable(
    tmp_path: Path,
) -> None:
    store = _query_store(tmp_path / "bundle.sqlite3")
    schema = store.data_dictionary("query-run")
    summary = store.summary("query-run")
    output = io.BytesIO()
    filters = DatasetQueryFilter(
        primary_entity_id="alice",
        include_private=False,
        limit=17,
    )
    store.write_analysis_bundle("query-run", output, filters)
    store.close()

    assert schema["feature_schema_versions"]["stage0.feature.action_episode"] == "1"
    assert any(
        field["name"] == "wall_time"
        and field["nondeterministic"] is True
        and field["canonical"] is False
        for field in schema["record_envelope"]
    )
    assert summary["entity_counts"]["alice"] == 4
    assert summary["status_counts"]["actions"] == {"completed": 1}
    assert summary["feature_family_counts"]["actions"] == 1

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        records = [
            json.loads(line)
            for line in archive.read("records.ndjson").splitlines()
        ]
        goal_rows = list(
            csv.DictReader(
                io.StringIO(archive.read("tables/goals.csv").decode("utf-8"))
            )
        )

    assert names[:3] == ["manifest.json", "schema.json", "records.ndjson"]
    assert names == manifest["files"]
    assert manifest["filters"]["primary_entity_id"] == "alice"
    assert manifest["filters"]["include_private"] is False
    assert [record["sequence"] for record in records] == [1, 2, 4]
    assert all(
        record["visibility"] != "PRIVATE_RESEARCH" for record in records
    )
    assert goal_rows[0]["description"] == 'Ask, "clearly"\nthen listen'
