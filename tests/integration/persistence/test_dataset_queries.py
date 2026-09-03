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
    RunnerPhase,
)
from stage0_sim.application.data_management import DatasetManagementService


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
        subject_id="alice",
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


def _physical_query_store(path: Path) -> SQLiteDatasetStore:
    store = SQLiteDatasetStore(path)
    store.begin_run(
        run_id="physical-query",
        seed=9,
        dt=1,
        initial_speed=1,
        scenario={"name": "physical-query", "schema_version": 9},
    )
    sequence = 0
    for tick, phase, is_open, relation_kind in (
        (0, RunnerPhase.RUN_INITIAL, False, "IN_CONTAINER"),
        (1, RunnerPhase.TICK_POST_SYSTEMS, True, "ON_SUPPORT"),
    ):
        sequence += 1
        payload = {
            "feature_schema": "stage0.feature.physical_object_state.v2",
            "object_id": "secret-object",
            "definition_id": "definition-secret",
            "name": "PRIVATE OBJECT MARKER",
            "pose": {
                "room_id": "room-a",
                "anchor": {"x": tick + 4, "y": 5},
                "orientation": "NORTH",
            },
            "footprint": {
                "coordinate_system": "local_microcell_offset",
                "cells": [{"x": 0, "y": 0}],
            },
            "occupied_cells": [{"x": tick + 4, "y": 5}],
            "obstruction": {
                "movement": "HARD",
                "vision": "OPAQUE",
                "hearing": "PASS",
                "smell": "PASS",
                "blocks_movement": True,
                "blocks_vision": True,
                "blocks_hearing": False,
                "blocks_smell": False,
            },
            "intrinsics": {
                "mass_kg": 1.5,
                "dimensions_cm": None,
                "size_class": "SMALL",
            },
            "openable": {
                "is_open": is_open,
                "is_locked": False,
                "closed_movement_obstruction": "HARD",
                "closed_vision_obstruction": "OPAQUE",
            },
            "capabilities": {},
            "slots": [],
            "parent_relation": {
                "parent_id": "cabinet-a",
                "relation_kind": relation_kind,
                "slot_id": "slot-a",
            },
            "custody": {
                "custodian_id": "alice",
                "held": False,
                "held_by_id": None,
            },
            "ownership": {"owner_id": "alice"},
            "spatial_index": {
                "indexed": True,
                "dynamic": False,
                "revision": tick + 3,
                "topology_revision": tick + 2,
            },
        }
        record = DatasetRecord(
            run_id="physical-query",
            sequence=sequence,
            record_type="physical_object_state",
            simulation_tick=tick,
            simulation_time=float(tick),
            subject_id="secret-object",
            related_entity_ids=("cabinet-a", "alice"),
            payload=payload,
            schema_id="stage0.feature.physical_object_state",
            schema_version="2",
            category=RecordCategory.STATE,
            phase=phase,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
        store.append(record)
        store.append_physical_object_state(
            run_id="physical-query",
            physical_state_id=f"{record.record_id}:physical-state",
            record_id=record.record_id,
            object_id="secret-object",
            definition_id="definition-secret",
            name="PRIVATE OBJECT MARKER",
            room_id="room-a",
            anchor_x=tick + 4,
            anchor_y=5,
            orientation="NORTH",
            phase=phase,
            simulation_tick=tick,
            simulation_time=float(tick),
            movement_obstruction="HARD",
            vision_obstruction="OPAQUE",
            hearing_transmission="PASS",
            smell_transmission="PASS",
            blocks_movement=True,
            blocks_vision=True,
            blocks_hearing=False,
            blocks_smell=False,
            mass_kg=1.5,
            size_class="SMALL",
            is_open=is_open,
            is_locked=False,
            parent_id="cabinet-a",
            relation_kind=relation_kind,
            slot_id="slot-a",
            custodian_id="alice",
            held_by_id=None,
            spatial_index_revision=tick + 3,
            topology_revision=tick + 2,
            state=payload,
        )
        sequence += 1
        relation_payload = {
            "feature_schema": "stage0.feature.physical_relation_sample.v1",
            "object_id": "secret-object",
            "entity_kind": "physical_object",
            "room_id": "room-a",
            "parent_id": "cabinet-a",
            "parent_kind": "physical_object",
            "relation_kind": relation_kind,
            "slot_id": "slot-a",
            "custodian_id": "alice",
            "held": False,
            "held_by_id": None,
            "spatial_index": {
                "revision": tick + 3,
                "topology_revision": tick + 2,
            },
        }
        relation_record = DatasetRecord(
            run_id="physical-query",
            sequence=sequence,
            record_type="physical_relation_sample",
            simulation_tick=tick,
            simulation_time=float(tick),
            subject_id="secret-object",
            related_entity_ids=("cabinet-a", "alice"),
            payload=relation_payload,
            schema_id="stage0.feature.physical_relation_sample",
            schema_version="1",
            category=RecordCategory.STATE,
            phase=phase,
            visibility=RecordVisibility.PRIVATE_RESEARCH,
        )
        store.append(relation_record)
        store.append_physical_relation_sample(
            run_id="physical-query",
            relation_sample_id=(
                f"{relation_record.record_id}:physical-relation"
            ),
            record_id=relation_record.record_id,
            object_id="secret-object",
            entity_kind="physical_object",
            room_id="room-a",
            parent_id="cabinet-a",
            parent_kind="physical_object",
            relation_kind=relation_kind,
            slot_id="slot-a",
            custodian_id="alice",
            held_by_id=None,
            phase=phase,
            simulation_tick=tick,
            simulation_time=float(tick),
            spatial_index_revision=tick + 3,
            topology_revision=tick + 2,
            relation=relation_payload,
        )
    store.flush()
    store.complete_run(
        "physical-query",
        status="completed",
        final_tick=1,
        final_simulation_time=1,
    )
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
    private_schema = store.data_dictionary(
        "query-run",
        include_private=True,
    )
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
    assert summary["entity_counts"]["alice"] == 3
    assert summary["status_counts"]["actions"] == {"completed": 1}
    assert summary["feature_family_counts"]["actions"] == 1

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        bundle_schema = json.loads(archive.read("schema.json"))
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
    assert all(
        row["visibility"] != "PRIVATE_RESEARCH"
        for row in schema["observed_record_schemas"]
    )
    assert any(
        row["visibility"] == "PRIVATE_RESEARCH"
        for row in private_schema["observed_record_schemas"]
    )
    assert all(
        row["visibility"] != "PRIVATE_RESEARCH"
        for row in bundle_schema["observed_record_schemas"]
    )
    assert [record["sequence"] for record in records] == [1, 2, 4]
    assert all(
        record["visibility"] != "PRIVATE_RESEARCH" for record in records
    )
    assert goal_rows[0]["description"] == 'Ask, "clearly"\nthen listen'


def test_physical_filters_exports_dictionary_summary_and_aggregation(
    tmp_path: Path,
) -> None:
    store = _physical_query_store(tmp_path / "physical-query.sqlite3")

    public = store.query_table(
        "physical-query",
        "physical_object_states",
    )
    first = store.query_table(
        "physical-query",
        "physical_object_states",
        DatasetQueryFilter(
            object_id="secret-object",
            room_id="room-a",
            parent_id="cabinet-a",
            relation_kind="ON_SUPPORT",
            phase=RunnerPhase.TICK_POST_SYSTEMS,
            is_open=True,
            is_locked=False,
            include_private=True,
            limit=1,
        ),
    )
    page_one = store.query_table(
        "physical-query",
        "physical_object_states",
        DatasetQueryFilter(
            object_id="secret-object",
            include_private=True,
            limit=1,
        ),
    )
    page_two = store.query_table(
        "physical-query",
        "physical_object_states",
        DatasetQueryFilter(
            object_id="secret-object",
            include_private=True,
            cursor=page_one.next_cursor,
            limit=1,
        ),
    )
    relation = store.query_table(
        "physical-query",
        "physical_relation_samples",
        DatasetQueryFilter(
            object_id="secret-object",
            room_id="room-a",
            parent_id="cabinet-a",
            relation_kind="IN_CONTAINER",
            phase=RunnerPhase.RUN_INITIAL,
            include_private=True,
        ),
    )
    raw = store.query_records(
        "physical-query",
        DatasetRecordFilter(
            object_id="secret-object",
            room_id="room-a",
            parent_id="cabinet-a",
            relation_kind="ON_SUPPORT",
            phase=RunnerPhase.TICK_POST_SYSTEMS,
            is_open=True,
            is_locked=False,
            include_private=True,
        ),
    )
    public_summary = store.summary("physical-query")
    private_summary = store.summary(
        "physical-query",
        include_private=True,
    )
    schema = store.data_dictionary("physical-query")
    private_schema = store.data_dictionary(
        "physical-query",
        include_private=True,
    )
    public_bundle = io.BytesIO()
    private_bundle = io.BytesIO()
    store.write_analysis_bundle(
        "physical-query",
        public_bundle,
        DatasetQueryFilter(),
    )
    store.write_analysis_bundle(
        "physical-query",
        private_bundle,
        DatasetQueryFilter(include_private=True),
    )
    complete = [json.loads(line) for line in store.iter_jsonl("physical-query")]
    aggregate_service = DatasetManagementService(store)
    selection = aggregate_service.selection(["physical-query"])
    public_aggregate = aggregate_service.aggregate(selection)
    private_aggregate = aggregate_service.aggregate(
        selection,
        include_private_derived=True,
    )
    store.close()

    assert public.rows == ()
    assert len(first.rows) == 1
    assert first.rows[0]["simulation_tick"] == 1
    assert first.next_cursor is None
    assert [row["simulation_tick"] for row in page_one.rows] == [0]
    assert page_one.next_cursor is not None
    assert [row["simulation_tick"] for row in page_two.rows] == [1]
    assert page_two.next_cursor is None
    assert len(relation.rows) == 1
    assert [record.record_type for record in raw.records] == [
        "physical_object_state"
    ]
    assert public_summary["physical"] == {
        "private_records_included": False,
        "distinct_object_count": 0,
        "state_sample_count": 0,
        "relation_sample_count": 0,
        "distributions": {
            "relation_kind": {},
            "room": {},
            "open": {},
            "locked": {},
            "movement_obstruction": {},
            "vision_obstruction": {},
            "custody": {},
            "held": {},
        },
    }
    assert private_summary["physical"]["distinct_object_count"] == 1
    assert private_summary["physical"]["state_sample_count"] == 2
    assert private_summary["physical"]["relation_sample_count"] == 2
    assert schema["dataset_schema_version"] == "stage0.dataset.v6"
    assert schema["sqlite_schema_version"] == 12
    assert schema["feature_schema_versions"][
        "stage0.feature.physical_object_state"
    ] == "2"
    table_meanings = {
        table["name"]: table["meaning"]
        for table in schema["normalized_and_derived_tables"]
    }
    assert "checkpoint authority" in table_meanings["physical_object_states"]
    assert "parent" in table_meanings["physical_relation_samples"]

    with zipfile.ZipFile(public_bundle) as archive:
        public_manifest = json.loads(archive.read("manifest.json"))
        public_schema = json.loads(archive.read("schema.json"))
        public_states = list(
            csv.DictReader(
                io.StringIO(
                    archive.read(
                        "tables/physical_object_states.csv"
                    ).decode("utf-8")
                )
            )
        )
        public_records = archive.read("records.ndjson").decode("utf-8")
    with zipfile.ZipFile(private_bundle) as archive:
        private_manifest = json.loads(archive.read("manifest.json"))
        private_bundle_schema = json.loads(archive.read("schema.json"))
        private_states = list(
            csv.DictReader(
                io.StringIO(
                    archive.read(
                        "tables/physical_object_states.csv"
                    ).decode("utf-8")
                )
            )
        )
        private_relations = list(
            csv.DictReader(
                io.StringIO(
                    archive.read(
                        "tables/physical_relation_samples.csv"
                    ).decode("utf-8")
                )
            )
        )
        private_records = archive.read("records.ndjson").decode("utf-8")
    assert public_manifest["private_records_included"] is False
    assert public_manifest["private_data_warning"] is None
    assert all(
        row["visibility"] != "PRIVATE_RESEARCH"
        for row in schema["observed_record_schemas"]
    )
    assert any(
        row["visibility"] == "PRIVATE_RESEARCH"
        for row in private_schema["observed_record_schemas"]
    )
    assert all(
        row["visibility"] != "PRIVATE_RESEARCH"
        for row in public_schema["observed_record_schemas"]
    )
    assert public_states == []
    assert "PRIVATE OBJECT MARKER" not in public_records
    assert private_manifest["private_records_included"] is True
    assert any(
        row["visibility"] == "PRIVATE_RESEARCH"
        for row in private_bundle_schema["observed_record_schemas"]
    )
    assert "hidden physical contents" in private_manifest[
        "private_data_warning"
    ]
    assert len(private_states) == 2
    assert len(private_relations) == 2
    assert "PRIVATE OBJECT MARKER" in private_records
    assert complete[0]["payload"]["private_records_included"] is True
    assert "Keep it restricted" in complete[0]["payload"][
        "private_data_warning"
    ]
    public_dist = public_aggregate.distributions
    private_dist = private_aggregate.distributions
    assert "PRIVATE OBJECT MARKER" not in json.dumps(public_aggregate.to_dict())
    assert public_aggregate.include_private_derived is False
    assert private_aggregate.compatibility_groups[0].feature_schema_versions[
        "stage0.feature.physical_object_state"
    ] == "2"
    assert private_dist["physical.state.room_id"] == {"room-a": 2}
    assert private_dist["physical.state.is_open"] == {
        "false": 1,
        "true": 1,
    }
    assert private_dist["physical.state.is_locked"] == {"false": 2}
    assert private_dist["physical.state.movement_obstruction"] == {
        "HARD": 2
    }
    assert private_dist["physical.state.vision_obstruction"] == {
        "OPAQUE": 2
    }
    assert private_dist["physical.state.custodian_id"] == {"alice": 2}
    assert private_dist["physical.state.held_by_id"] == {"none": 2}
    assert private_dist["physical.relation.relation_kind"] == {
        "IN_CONTAINER": 1,
        "ON_SUPPORT": 1,
    }
    assert public_dist.get("physical.state.room_id") is None
    physical_metric = next(
        metric
        for metric in private_aggregate.metrics
        if metric.name == "physical.distinct_objects"
    )
    assert physical_metric.pooled.mean == 1
