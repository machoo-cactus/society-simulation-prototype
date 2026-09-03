import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stage0_sim.adapters.persistence import RUN_SCOPED_TABLES, SQLiteDatasetStore
from stage0_sim.application.data_capture import (
    DatasetRecord,
    RecordCategory,
    RecordJoinIds,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.application.data_management import (
    DatasetManagementService,
    PersistedRunFilter,
    RunSelection,
)


def _scenario(
    name: str,
    *,
    schema_version: int = 2,
    world_type: str = "city",
    cognition_mode: str = "global_barrier",
    npc_mode: str = "deterministic",
    capture_profile: str = "full",
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "name": name,
        "world": {"type": world_type},
        "cognition": {
            "execution_mode": cognition_mode,
            "npc_control_mode": npc_mode,
        },
        "runtime_configuration": {
            "cognition_execution_mode": cognition_mode,
            "npc_control_mode": npc_mode,
            "effective_npc_control_mode": npc_mode,
        },
        "capture_configuration": {"profile": capture_profile},
    }


def _record(
    run_id: str,
    sequence: int,
    *,
    record_type: str = "event",
    tick: int | None = None,
    visibility: RecordVisibility = RecordVisibility.OPERATOR,
    category: RecordCategory = RecordCategory.EVENT,
    phase: RunnerPhase = RunnerPhase.TICK_POST_SYSTEMS,
    schema_id: str | None = None,
    schema_version: str = "1",
    payload: dict[str, object] | None = None,
    joins: RecordJoinIds | None = None,
) -> DatasetRecord:
    resolved_tick = sequence if tick is None else tick
    return DatasetRecord(
        run_id=run_id,
        sequence=sequence,
        record_type=record_type,
        simulation_tick=resolved_tick,
        simulation_time=float(resolved_tick),
        subject_id="alice",
        payload=payload or {},
        category=category,
        source=(
            RecordSource.DERIVED
            if schema_id is not None and schema_id.startswith("stage0.feature.")
            else RecordSource.DATASET_COLLECTOR
        ),
        phase=phase,
        visibility=visibility,
        schema_id=schema_id or f"stage0.record.{record_type}",
        schema_version=schema_version,
        joins=joins or RecordJoinIds(),
    )


def _add_action(
    store: SQLiteDatasetStore,
    run_id: str,
    sequence: int,
    action_id: str,
    duration: float,
    *,
    visibility: RecordVisibility = RecordVisibility.OPERATOR,
    action_type: str = "wait",
    status: str = "completed",
) -> None:
    record = _record(
        run_id,
        sequence,
        record_type="action_episode",
        tick=sequence,
        visibility=visibility,
        category=RecordCategory.ACTION,
        schema_id="stage0.feature.action_episode",
        payload={
            "terminal_status": status,
            "elapsed_simulation_time": duration,
        },
    )
    store.append(record)
    store.append_action_instance(
        run_id=run_id,
        action_id=action_id,
        record_id=record.record_id,
        plan_id=None,
        goal_id=None,
        decision_id=None,
        tool_call_id=None,
        subject_id="alice",
        action_type=action_type,
        status=status,
        origin="controller",
        plan_revision=None,
        created_tick=sequence - 1,
        created_at=float(sequence) - duration,
        root_correlation_id=action_id,
        action={"action_type": action_type},
    )
    store.append_action_episode(
        run_id=run_id,
        action_id=action_id,
        record_id=record.record_id,
        subject_id="alice",
        terminal_status=status,
        created_tick=sequence - 1,
        terminal_tick=sequence,
        created_at=float(sequence) - duration,
        terminal_at=float(sequence),
        elapsed_simulation_time=duration,
        source_event_ids=(),
        episode=record.payload,
    )


def _finish_run(
    store: SQLiteDatasetStore,
    run_id: str,
    sequence: int,
    *,
    status: str = "completed",
) -> None:
    store.append(
        _record(
            run_id,
            sequence,
            record_type="run_state",
            phase=RunnerPhase.RUN_FINAL,
            category=RecordCategory.RUN,
        )
    )
    store.complete_run(
        run_id,
        status=status,
        final_tick=sequence,
        final_simulation_time=float(sequence),
    )


def _set_started_at(path: Path, run_id: str, value: datetime) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE runs SET started_at = ? WHERE run_id = ?",
        (value.isoformat(), run_id),
    )
    connection.commit()
    connection.close()


def test_restart_reconciles_only_prior_instance_and_preserves_latest_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile.sqlite3"
    first = SQLiteDatasetStore(database)
    first.begin_run(
        run_id="abandoned",
        seed=7,
        dt=1,
        initial_speed=1,
        scenario=_scenario("reconcile"),
    )
    first.append(_record("abandoned", 1, tick=9))
    first.flush()

    assert first.reconcile_incomplete_runs() == ()
    first.close()

    reopened = SQLiteDatasetStore(database)
    assert reopened.reconcile_incomplete_runs() == ("abandoned",)
    summary = DatasetManagementService(reopened).catalog().runs[0]
    raw_records = reopened.query_records("abandoned").records
    reopened.close()

    assert summary.persisted_status == "interrupted"
    assert summary.final_tick == 9
    assert summary.final_simulation_time == 9.0
    assert summary.completed_at is not None
    assert summary.capture_complete is False
    assert "owning dataset-store lease" in (summary.interruption_reason or "")
    assert all(record.phase is not RunnerPhase.RUN_FINAL for record in raw_records)


def test_live_foreign_store_lease_blocks_reconciliation_and_deletion(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-owner.sqlite3"
    owner = SQLiteDatasetStore(database)
    owner.begin_run(
        run_id="foreign-live",
        seed=7,
        dt=1,
        initial_speed=1,
        scenario=_scenario("concurrent"),
    )
    owner.append(_record("foreign-live", 1, tick=3))
    owner.flush()

    observer = SQLiteDatasetStore(database)
    assert observer.reconcile_incomplete_runs() == ()
    running = DatasetManagementService(observer).catalog().runs[0]
    assert running.persisted_status == "running"

    owner.complete_run(
        "foreign-live",
        status="completed",
        final_tick=3,
        final_simulation_time=3,
    )
    selection = DatasetManagementService(observer).selection(("foreign-live",))
    active_owner_preview = observer.deletion_preview(selection)
    assert active_owner_preview.eligible is False
    assert active_owner_preview.ineligible_run_ids == ("foreign-live",)

    owner.close()
    closed_owner_preview = observer.deletion_preview(selection)
    assert closed_owner_preview.eligible is True
    observer.close()


def test_owner_commit_refreshes_lease_after_a_write_transaction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "write-lease.sqlite3"
    owner = SQLiteDatasetStore(database)
    owner.begin_run(
        run_id="long-write",
        seed=8,
        dt=1,
        initial_speed=1,
        scenario=_scenario("long-write"),
    )
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    connection = sqlite3.connect(database)
    connection.execute(
        """
        UPDATE dataset_store_instances
        SET heartbeat_at = ?
        WHERE instance_id = ?
        """,
        (stale, owner.instance_id),
    )
    connection.commit()
    connection.close()

    owner.append(_record("long-write", 1, tick=4))
    owner.flush()

    observer = SQLiteDatasetStore(database)
    assert observer.reconcile_incomplete_runs() == ()
    owner.close()
    observer.close()


def test_reconciliation_fences_the_expired_owner_from_future_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fenced-owner.sqlite3"
    owner = SQLiteDatasetStore(database)
    observer = SQLiteDatasetStore(database)
    owner.begin_run(
        run_id="fenced-owner",
        seed=14,
        dt=1,
        initial_speed=1,
        scenario=_scenario("fenced-owner"),
    )
    owner.append(_record("fenced-owner", 1))
    owner.flush()
    owner._heartbeat_stop.set()
    owner._heartbeat_thread.join(timeout=3)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        UPDATE dataset_store_instances
        SET heartbeat_at = ?
        WHERE instance_id = ?
        """,
        (
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            owner.instance_id,
        ),
    )
    connection.commit()
    connection.close()

    assert observer.reconcile_incomplete_runs() == ("fenced-owner",)
    with pytest.raises(RuntimeError, match="no longer writable"):
        owner.append(_record("fenced-owner", 2))
    with pytest.raises(RuntimeError, match="no longer writable"):
        owner.complete_run(
            "fenced-owner",
            status="completed",
            final_tick=2,
            final_simulation_time=2,
        )

    owner.close()
    observer.close()


def test_current_schema_open_does_not_repeat_run_backfill(tmp_path: Path) -> None:
    database = tmp_path / "current-schema.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="preserve-capture-flag",
        seed=9,
        dt=1,
        initial_speed=1,
        scenario=_scenario("current-schema"),
    )
    _finish_run(store, "preserve-capture-flag", 1)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute(
        """
        UPDATE runs SET capture_complete = 0
        WHERE run_id = 'preserve-capture-flag'
        """
    )
    connection.commit()
    connection.close()

    reopened = SQLiteDatasetStore(database)
    summary = DatasetManagementService(reopened).catalog().runs[0]
    reopened.close()

    assert summary.capture_complete is False


def test_catalog_does_not_nest_reconciliation_in_capture_transaction(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "active-capture.sqlite3")
    store.begin_run(
        run_id="active-capture",
        seed=10,
        dt=1,
        initial_speed=1,
        scenario=_scenario("active-capture"),
    )
    store.append(_record("active-capture", 1))

    page = DatasetManagementService(store).catalog()

    assert [run.run_id for run in page.runs] == ["active-capture"]
    store.flush()
    store.close()


def test_catalog_defers_reconciliation_while_foreign_writer_is_active(
    tmp_path: Path,
) -> None:
    database = tmp_path / "foreign-write.sqlite3"
    owner = SQLiteDatasetStore(database)
    observer = SQLiteDatasetStore(database)
    owner.begin_run(
        run_id="foreign-write",
        seed=13,
        dt=1,
        initial_speed=1,
        scenario=_scenario("foreign-write"),
    )
    owner.append(_record("foreign-write", 1))

    page = DatasetManagementService(observer).catalog()

    assert [run.run_id for run in page.runs] == ["foreign-write"]
    assert page.runs[0].persisted_status == "running"
    owner.flush()
    owner.close()
    observer.close()


def test_deletion_defers_while_capture_transaction_is_active(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "delete-boundary.sqlite3")
    store.begin_run(
        run_id="delete-me",
        seed=11,
        dt=1,
        initial_speed=1,
        scenario=_scenario("delete-me"),
    )
    _finish_run(store, "delete-me", 1)
    store.begin_run(
        run_id="live-write",
        seed=12,
        dt=1,
        initial_speed=1,
        scenario=_scenario("live-write"),
    )
    store.append(_record("live-write", 1))
    service = DatasetManagementService(store)
    selection = service.selection(("delete-me",))
    preview = service.preview_deletion(selection)

    with pytest.raises(ValueError, match="capture transaction is active"):
        service.delete(selection, preview.confirmation_token)

    store.flush()
    refreshed = service.preview_deletion(selection)
    result = service.delete(selection, refreshed.confirmation_token)
    assert result.run_ids == ("delete-me",)
    store.close()


def test_catalog_filters_newest_first_cursor_and_metadata(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    store = SQLiteDatasetStore(database)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index, (run_id, name, status) in enumerate(
        (
            ("run-old", "Alpha", "completed"),
            ("run-middle", "Beta", "failed"),
            ("run-new", "Alpha", "completed"),
        )
    ):
        store.begin_run(
            run_id=run_id,
            seed=index,
            dt=1,
            initial_speed=2,
            scenario=_scenario(name, world_type="city" if index else "grid"),
        )
        if status == "completed":
            _finish_run(store, run_id, 1)
        else:
            store.complete_run(
                run_id,
                status=status,
                final_tick=0,
                final_simulation_time=0,
            )
        _set_started_at(database, run_id, base + timedelta(days=index))

    service = DatasetManagementService(store)
    first = service.catalog(PersistedRunFilter(limit=1))
    second = service.catalog(
        PersistedRunFilter(limit=1, cursor=first.next_cursor)
    )
    filtered = service.catalog(
        PersistedRunFilter(
            search_text="alpha",
            persisted_statuses=("completed",),
            effective_statuses=("completed",),
            scenario_name="Alpha",
            dataset_schema_version="stage0.dataset.v6",
            capture_complete=True,
            started_at_or_after=base,
            started_before=base + timedelta(days=3),
            completed_at_or_after=base,
        )
    )
    store.close()

    assert [run.run_id for run in first.runs] == ["run-new"]
    assert [run.run_id for run in second.runs] == ["run-middle"]
    assert first.total_count == 3
    assert [run.run_id for run in filtered.runs] == ["run-new", "run-old"]
    assert filtered.runs[0].scenario_name == "Alpha"
    assert filtered.runs[0].world_schema_version == "city-v2"
    assert filtered.runs[0].record_count == 1


def test_effective_status_filter_uses_live_overlay(tmp_path: Path) -> None:
    store = SQLiteDatasetStore(tmp_path / "effective.sqlite3")
    store.begin_run(
        run_id="live",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario=_scenario("live"),
    )
    service = DatasetManagementService(store, lambda: {"live": "paused"})

    page = service.catalog(
        PersistedRunFilter(effective_statuses=("paused",))
    )
    store.close()

    assert page.runs[0].persisted_status == "running"
    assert page.runs[0].effective_status == "paused"
    assert page.runs[0].live is True


def test_mixed_groups_pooled_macro_private_filter_and_exports(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "aggregate.sqlite3")
    store.begin_run(
        run_id="run-a",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario=_scenario("scenario-a", cognition_mode="global_barrier"),
    )
    _add_action(store, "run-a", 1, "a-public", 1)
    _add_action(
        store,
        "run-a",
        2,
        "a-private",
        3,
        visibility=RecordVisibility.PRIVATE_RESEARCH,
    )
    _finish_run(store, "run-a", 3)
    store.begin_run(
        run_id="run-b",
        seed=2,
        dt=1,
        initial_speed=1,
        scenario=_scenario(
            "scenario-b",
            schema_version=5,
            world_type="grid",
            cognition_mode="global_barrier",
            capture_profile="reduced",
        ),
    )
    _add_action(store, "run-b", 1, "b-public", 10)
    _finish_run(store, "run-b", 2)
    service = DatasetManagementService(store)
    selection = service.selection(
        ["run-a", "run-b", "run-a"],
        PersistedRunFilter(scenario_name=None),
    )

    included = service.aggregate(selection, include_private_derived=True)
    default = service.aggregate(selection)
    excluded = service.aggregate(selection, include_private_derived=False)
    first_json = io.StringIO()
    second_json = io.StringIO()
    csv_output = io.StringIO()
    service.write_aggregate_json(included, first_json)
    service.write_aggregate_json(included, second_json)
    service.write_aggregate_csv(included, csv_output)
    store.close()

    included_duration = next(
        metric for metric in included.metrics if metric.name == "actions.duration"
    )
    excluded_duration = next(
        metric for metric in excluded.metrics if metric.name == "actions.duration"
    )
    assert selection.run_ids == ("run-a", "run-b")
    assert selection.fingerprint
    assert len(included.compatibility_groups) == 2
    assert any("mixed scenarios" in warning for warning in included.compatibility_warnings)
    assert any(
        "mixed capture configurations" in warning
        for warning in included.compatibility_warnings
    )
    assert included_duration.pooled.count == 3
    assert included_duration.pooled.mean == pytest.approx(14 / 3)
    assert included_duration.macro_per_run.mean == pytest.approx(6)
    assert [value.run_id for value in included_duration.per_run] == [
        "run-a",
        "run-b",
    ]
    assert excluded_duration.pooled.count == 2
    assert default.include_private_derived is False
    assert excluded_duration.pooled.mean == pytest.approx(5.5)
    assert excluded.distributions["record.visibility"] == {"OPERATOR": 4}
    assert included.private_derived_warning is not None
    assert excluded.private_derived_warning is None
    assert first_json.getvalue() == second_json.getvalue()
    exported = json.loads(first_json.getvalue())
    assert exported["selection"]["run_ids"] == ["run-a", "run-b"]
    assert "macro_per_run" in exported["weighting_definitions"]
    assert "run-a" in csv_output.getvalue()
    assert "compatibility_groups" in csv_output.getvalue()
    assert "weighting_definitions" in csv_output.getvalue()


def test_run_scoped_table_registry_covers_schema(tmp_path: Path) -> None:
    database = tmp_path / "coverage.sqlite3"
    store = SQLiteDatasetStore(database)
    store.close()
    connection = sqlite3.connect(database)
    tables = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    run_scoped = {
        table
        for table in tables
        if any(
            str(column[1]) == "run_id"
            for column in connection.execute(f"PRAGMA table_info({table})")
        )
    }
    connection.close()

    assert run_scoped == set(RUN_SCOPED_TABLES)
    assert RUN_SCOPED_TABLES[-1] == "runs"


def test_individual_and_bulk_deletion_leave_no_orphans(tmp_path: Path) -> None:
    database = tmp_path / "delete.sqlite3"
    store = SQLiteDatasetStore(database)
    for run_id in ("one", "two", "three"):
        store.begin_run(
            run_id=run_id,
            seed=1,
            dt=1,
            initial_speed=1,
            scenario=_scenario(run_id),
        )
        _add_action(store, run_id, 1, f"{run_id}-action", 1)
        _finish_run(store, run_id, 2)
    store.flush()
    connection = sqlite3.connect(database)
    for run_id in ("one", "two", "three"):
        connection.execute(
            """
            INSERT INTO episodic_memories (
                run_id, memory_id, agent_id, simulation_time, importance,
                text, embedding_json, metadata_json
            ) VALUES (?, ?, 'alice', 1, 1, 'memory', '[]', '{}')
            """,
            (run_id, f"{run_id}-memory"),
        )
        connection.execute(
            """
            INSERT INTO information_documents (
                run_id, document_id, revision, namespace_id, kind,
                content_hash, document_json
            ) VALUES (?, ?, 1, 'world', 'fact', 'hash', '{}')
            """,
            (run_id, f"{run_id}-document"),
        )
    connection.commit()
    connection.close()
    service = DatasetManagementService(store)

    one = service.selection(["one"])
    one_preview = service.preview_deletion(one)
    one_result = service.delete(one, one_preview.confirmation_token)
    remaining = service.selection(["two", "three"])
    remaining_preview = service.preview_deletion(remaining)
    remaining_result = service.delete(
        remaining,
        remaining_preview.confirmation_token,
    )

    assert one_result.deleted_record_count == 2
    assert remaining_result.deleted_record_count == 4
    assert service.catalog().runs == ()
    connection = sqlite3.connect(database)
    assert all(
        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        for table in RUN_SCOPED_TABLES
    )
    connection.close()
    store.close()


def test_delete_transaction_rolls_back_after_mid_delete_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "transaction-rollback.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="terminal",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario=_scenario("terminal"),
    )
    _add_action(store, "terminal", 1, "action", 1)
    _finish_run(store, "terminal", 2)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TRIGGER reject_record_delete
        BEFORE DELETE ON records
        BEGIN
            SELECT RAISE(ABORT, 'injected deletion failure');
        END
        """
    )
    connection.commit()
    connection.close()
    service = DatasetManagementService(store)
    selection = service.selection(["terminal"])
    preview = service.preview_deletion(selection)

    with pytest.raises(sqlite3.IntegrityError, match="injected deletion failure"):
        service.delete(selection, preview.confirmation_token)

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT COUNT(*) FROM runs WHERE run_id = 'terminal'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM action_instances WHERE run_id = 'terminal'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM action_episodes WHERE run_id = 'terminal'"
    ).fetchone()[0] == 1
    connection.close()
    store.close()


def test_bulk_deletion_rejects_ineligible_and_rolls_back(tmp_path: Path) -> None:
    store = SQLiteDatasetStore(tmp_path / "rollback.sqlite3")
    store.begin_run(
        run_id="terminal",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario=_scenario("terminal"),
    )
    _finish_run(store, "terminal", 1)
    store.begin_run(
        run_id="running",
        seed=2,
        dt=1,
        initial_speed=1,
        scenario=_scenario("running"),
    )
    service = DatasetManagementService(store)
    selection = service.selection(["terminal", "running"])
    preview = service.preview_deletion(selection)

    assert preview.eligible is False
    assert preview.ineligible_run_ids == ("running",)
    with pytest.raises(ValueError, match="must be terminal"):
        service.delete(selection, preview.confirmation_token)
    assert {run.run_id for run in service.catalog().runs} == {
        "terminal",
        "running",
    }
    store.close()


def test_stale_preview_and_selection_fingerprint_are_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stale.sqlite3"
    store = SQLiteDatasetStore(database)
    store.begin_run(
        run_id="terminal",
        seed=1,
        dt=1,
        initial_speed=1,
        scenario=_scenario("terminal"),
    )
    _finish_run(store, "terminal", 1)
    service = DatasetManagementService(store)
    selection = service.selection(["terminal"])
    preview = service.preview_deletion(selection)
    _set_started_at(
        database,
        "terminal",
        datetime(2027, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="stale deletion preview"):
        service.delete(selection, preview.confirmation_token)
    invalid = RunSelection(
        run_ids=selection.run_ids,
        fingerprint="not-the-selection-fingerprint",
    )
    with pytest.raises(ValueError, match="selection fingerprint"):
        service.preview_deletion(invalid)
    assert service.catalog().runs[0].run_id == "terminal"
    store.close()
