import base64
import csv
import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, cast
from uuid import uuid4

from stage0_sim.application.data_capture import (
    DATASET_SCHEMA_VERSION,
    DatasetQueryFilter,
    DatasetQueryPage,
    DatasetRecord,
    DatasetRecordFilter,
    DatasetRecordPage,
    RecordCategory,
    RecordJoinIds,
    RecordRelation,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.application.data_management import (
    TERMINAL_RUN_STATUSES,
    LiveRunOverlay,
    PersistedRunFilter,
    PersistedRunPage,
    PersistedRunSummary,
    RunDeletionPreview,
    RunDeletionResult,
    RunSelection,
    deletion_confirmation_token,
    selection_fingerprint,
)
from stage0_sim.application.memory import MemoryRecord
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.information import (
    InformationDocument,
    information_document_from_dict,
)

_DATABASE_SCHEMA_VERSION = 7
_MAX_DATABASE_SCHEMA_VERSION = 7
_INSTANCE_HEARTBEAT_INTERVAL_SECONDS = 10.0
_INSTANCE_LEASE_TIMEOUT_SECONDS = 120.0


@contextmanager
def _schema_migration_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.migration.lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, io.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import importlib

            fcntl = importlib.import_module("fcntl")
            flock = cast(
                Callable[[int, int], object],
                fcntl.__dict__["flock"],
            )
            lock_ex = cast(int, fcntl.__dict__["LOCK_EX"])
            lock_un = cast(int, fcntl.__dict__["LOCK_UN"])
            flock(handle.fileno(), lock_ex)
            try:
                yield
            finally:
                flock(handle.fileno(), lock_un)

_ANALYSIS_TABLES = (
    "record_relations",
    "state_samples",
    "state_deltas",
    "goals",
    "goal_transitions",
    "plans",
    "goal_action_links",
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
    "perception_facts",
    "perception_deliveries",
    "memory_operations",
    "memory_relations",
    "information_retrievals",
    "opportunity_samples",
    "transition_samples",
    "action_episodes",
    "decision_episodes",
    "goal_episodes",
    "interaction_episodes",
    "population_samples",
    "resource_samples",
    "resource_flows",
)
_QUERY_TABLE_ALIASES = {
    "actions": "action_instances",
    "action_episodes": "action_episodes",
    "action_transitions": "action_transitions",
    "decisions": "decisions",
    "decision_episodes": "decision_episodes",
    "goals": "goals",
    "goal_episodes": "goal_episodes",
    "goal_transitions": "goal_transitions",
    "information_retrievals": "information_retrievals",
    "interactions": "interactions",
    "interaction_episodes": "interaction_episodes",
    "memory_operations": "memory_operations",
    "model_requests": "model_requests",
    "model_turns": "model_turns",
    "opportunities": "opportunity_samples",
    "perception_deliveries": "perception_deliveries",
    "perception_facts": "perception_facts",
    "population": "population_samples",
    "resource_flows": "resource_flows",
    "resource_samples": "resource_samples",
    "state": "state_samples",
    "state_deltas": "state_deltas",
    "tool_executions": "tool_executions",
    "transitions": "transition_samples",
}
_DOMAIN_ID_COLUMNS = (
    "goal_id",
    "plan_id",
    "action_id",
    "decision_id",
    "model_request_id",
    "tool_call_id",
    "interaction_id",
    "perception_fact_id",
    "memory_id",
    "transaction_request_id",
    "operator_intervention_id",
)
_FEATURE_SCHEMA_VERSIONS = {
    "stage0.feature.action_episode": "1",
    "stage0.feature.decision_episode": "1",
    "stage0.feature.goal_episode": "1",
    "stage0.feature.interaction_episode": "1",
    "stage0.feature.opportunity_sample": "1",
    "stage0.feature.population_sample": "1",
    "stage0.feature.resource_flow": "1",
    "stage0.feature.resource_sample": "1",
    "stage0.feature.transition_sample": "1",
}

# Child tables precede their parents. This is the complete deletion and schema
# coverage registry for every SQLite table containing a run_id column.
RUN_SCOPED_TABLES = (
    "perception_deliveries",
    "interaction_participants",
    "interaction_events",
    "goal_action_links",
    "action_episodes",
    "action_transitions",
    "decision_options",
    "model_turns",
    "goal_transitions",
    "memory_relations",
    "decision_episodes",
    "goal_episodes",
    "interaction_episodes",
    "memory_operations",
    "information_retrievals",
    "state_samples",
    "state_deltas",
    "plans",
    "opportunity_samples",
    "transition_samples",
    "population_samples",
    "resource_samples",
    "resource_flows",
    "tool_executions",
    "perception_facts",
    "action_instances",
    "decisions",
    "model_requests",
    "goals",
    "interactions",
    "record_relations",
    "records",
    "episodic_memories",
    "information_documents",
    "runs",
)


class SQLiteDatasetStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.instance_id = str(uuid4())
        self._heartbeat_stop = threading.Event()
        self._heartbeat_error: str | None = None
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        with _schema_migration_lock(path):
            self._migrate()
        self._register_instance()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"stage0-dataset-heartbeat-{self.instance_id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _register_instance(self) -> None:
        timestamp = datetime.now(UTC).isoformat()
        self._connection.execute(
            """
            INSERT INTO dataset_store_instances (
                instance_id, started_at, heartbeat_at, closed_at
            ) VALUES (?, ?, ?, NULL)
            """,
            (self.instance_id, timestamp, timestamp),
        )
        self._connection.commit()

    def heartbeat(self) -> None:
        timestamp = datetime.now(UTC).isoformat()
        self._connection.execute(
            """
            UPDATE dataset_store_instances
            SET heartbeat_at = ?, closed_at = NULL
            WHERE instance_id = ?
            """,
            (timestamp, self.instance_id),
        )
        self._connection.commit()
        self._heartbeat_error = None

    def _commit(self) -> None:
        self._connection.execute(
            """
            UPDATE dataset_store_instances
            SET heartbeat_at = ?, closed_at = NULL
            WHERE instance_id = ?
            """,
            (datetime.now(UTC).isoformat(), self.instance_id),
        )
        self._connection.commit()
        self._heartbeat_error = None

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(_INSTANCE_HEARTBEAT_INTERVAL_SECONDS):
            try:
                connection = sqlite3.connect(self.path, timeout=2.0)
                try:
                    connection.execute(
                        """
                        UPDATE dataset_store_instances
                        SET heartbeat_at = ?, closed_at = NULL
                        WHERE instance_id = ?
                        """,
                        (datetime.now(UTC).isoformat(), self.instance_id),
                    )
                    connection.commit()
                finally:
                    connection.close()
            except sqlite3.OperationalError as error:
                self._heartbeat_error = str(error)
            else:
                self._heartbeat_error = None

    def begin_run(
        self,
        *,
        run_id: str,
        seed: int,
        dt: float,
        initial_speed: float,
        scenario: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO runs (
                run_id, schema_version, seed, dt, initial_speed,
                scenario_json, started_at, owner_instance_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                DATASET_SCHEMA_VERSION,
                seed,
                dt,
                initial_speed,
                _json(scenario),
                datetime.now(UTC).isoformat(),
                self.instance_id,
            ),
        )
        self._commit()

    def append(self, record: DatasetRecord) -> None:
        self._require_run_write_ownership(record.run_id)
        self._connection.execute(
            """
            INSERT INTO records (
                run_id, sequence, record_id, schema_id, schema_version,
                record_type, category, source, phase, simulation_tick,
                simulation_time, wall_time, visibility, agent_id, subject_id,
                related_entity_ids_json, source_event_id, causation_id,
                correlation_id, goal_id, plan_id, action_id, decision_id,
                model_request_id, tool_call_id, interaction_id,
                perception_fact_id, memory_id, transaction_request_id,
                operator_intervention_id, source_metadata_json, payload_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.run_id,
                record.sequence,
                record.record_id,
                record.schema_id,
                record.schema_version,
                record.record_type,
                record.category.value,
                record.source.value,
                record.phase.value,
                record.simulation_tick,
                record.simulation_time,
                record.wall_time,
                record.visibility.value,
                record.agent_id,
                record.subject_id,
                _json(list(record.related_entity_ids)),
                record.source_event_id,
                record.causation_id,
                record.correlation_id,
                record.joins.goal_id,
                record.joins.plan_id,
                record.joins.action_id,
                record.joins.decision_id,
                record.joins.model_request_id,
                record.joins.tool_call_id,
                record.joins.interaction_id,
                record.joins.perception_fact_id,
                record.joins.memory_id,
                record.joins.transaction_request_id,
                record.joins.operator_intervention_id,
                _json(record.source_metadata),
                _json(record.payload),
            ),
        )

    def add_record_relation(self, relation: RecordRelation) -> None:
        self._connection.execute(
            """
            INSERT INTO record_relations (
                run_id, record_id, relation_type, target_type, target_id,
                ordinal, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation.run_id,
                relation.record_id,
                relation.relation_type,
                relation.target_type,
                relation.target_id,
                relation.ordinal,
                _json(relation.metadata),
            ),
        )

    def append_state_sample(
        self,
        *,
        run_id: str,
        state_sample_id: str,
        record_id: str,
        subject_id: str | None,
        phase: RunnerPhase,
        simulation_tick: int,
        simulation_time: float,
        state: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "state_samples",
            (
                "run_id",
                "state_sample_id",
                "record_id",
                "subject_id",
                "phase",
                "simulation_tick",
                "simulation_time",
                "state_json",
            ),
            (
                run_id,
                state_sample_id,
                record_id,
                subject_id,
                phase.value,
                simulation_tick,
                simulation_time,
                _json(state),
            ),
        )

    def append_state_delta(
        self,
        *,
        run_id: str,
        state_delta_id: str,
        record_id: str,
        subject_id: str | None,
        from_sample_id: str | None,
        to_sample_id: str | None,
        simulation_tick: int,
        delta: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "state_deltas",
            (
                "run_id",
                "state_delta_id",
                "record_id",
                "subject_id",
                "from_sample_id",
                "to_sample_id",
                "simulation_tick",
                "delta_json",
            ),
            (
                run_id,
                state_delta_id,
                record_id,
                subject_id,
                from_sample_id,
                to_sample_id,
                simulation_tick,
                _json(delta),
            ),
        )

    def append_goal(
        self,
        *,
        run_id: str,
        goal_id: str,
        record_id: str,
        subject_id: str | None,
        description: str,
        status: str,
        goal: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO goals (
                run_id, goal_id, record_id, subject_id, description, status,
                goal_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, goal_id) DO UPDATE SET
                subject_id = excluded.subject_id,
                description = excluded.description,
                status = excluded.status,
                goal_json = excluded.goal_json
            """,
            (
                run_id,
                goal_id,
                record_id,
                subject_id,
                description,
                status,
                _json(goal),
            ),
        )

    def append_goal_transition(
        self,
        *,
        run_id: str,
        goal_transition_id: str,
        record_id: str,
        goal_id: str,
        simulation_tick: int,
        from_status: str | None,
        to_status: str,
        transition: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "goal_transitions",
            (
                "run_id",
                "goal_transition_id",
                "record_id",
                "goal_id",
                "simulation_tick",
                "from_status",
                "to_status",
                "transition_json",
            ),
            (
                run_id,
                goal_transition_id,
                record_id,
                goal_id,
                simulation_tick,
                from_status,
                to_status,
                _json(transition),
            ),
        )

    def append_decision(
        self,
        *,
        run_id: str,
        decision_id: str,
        record_id: str,
        subject_id: str | None,
        simulation_tick: int,
        status: str,
        selected_option_id: str | None,
        context: dict[str, JsonValue],
        outcome: dict[str, JsonValue] | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO decisions (
                run_id, decision_id, record_id, subject_id, simulation_tick,
                status, selected_option_id, context_json, outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, decision_id) DO UPDATE SET
                record_id = CASE
                    WHEN excluded.context_json = '{}' THEN decisions.record_id
                    ELSE excluded.record_id
                END,
                status = excluded.status,
                selected_option_id = COALESCE(
                    excluded.selected_option_id,
                    decisions.selected_option_id
                ),
                context_json = CASE
                    WHEN excluded.context_json = '{}' THEN decisions.context_json
                    ELSE excluded.context_json
                END,
                outcome_json = CASE
                    WHEN excluded.outcome_json = '{}' THEN decisions.outcome_json
                    ELSE excluded.outcome_json
                END
            """,
            (
                run_id,
                decision_id,
                record_id,
                subject_id,
                simulation_tick,
                status,
                selected_option_id,
                _json(context),
                _json(outcome or {}),
            ),
        )

    def append_decision_option(
        self,
        *,
        run_id: str,
        decision_id: str,
        option_id: str,
        record_id: str,
        option_index: int,
        option_type: str,
        selected: bool,
        option: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO decision_options (
                run_id, decision_id, option_id, record_id, option_index,
                option_type, selected, option_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, decision_id, option_id) DO UPDATE SET
                selected = excluded.selected,
                option_json = excluded.option_json
            """,
            (
                run_id,
                decision_id,
                option_id,
                record_id,
                option_index,
                option_type,
                int(selected),
                _json(option),
            ),
        )

    def append_model_request(
        self,
        *,
        run_id: str,
        model_request_id: str,
        record_id: str,
        decision_id: str | None,
        subject_id: str | None,
        operation: str,
        provider: str | None,
        model: str | None,
        status: str,
        request: dict[str, JsonValue],
        response: dict[str, JsonValue] | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO model_requests (
                run_id, model_request_id, record_id, decision_id, subject_id,
                operation, provider, model, status, request_json, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, model_request_id) DO UPDATE SET
                provider = COALESCE(excluded.provider, model_requests.provider),
                model = COALESCE(excluded.model, model_requests.model),
                status = excluded.status,
                response_json = CASE
                    WHEN excluded.response_json = '{}'
                    THEN model_requests.response_json
                    ELSE excluded.response_json
                END
            """,
            (
                run_id,
                model_request_id,
                record_id,
                decision_id,
                subject_id,
                operation,
                provider,
                model,
                status,
                _json(request),
                _json(response or {}),
            ),
        )

    def append_model_turn(
        self,
        *,
        run_id: str,
        model_request_id: str,
        turn_index: int,
        record_id: str,
        role: str,
        content: dict[str, JsonValue],
        usage: dict[str, JsonValue] | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO model_turns (
                run_id, model_request_id, turn_index, record_id, role,
                content_json, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, model_request_id, turn_index) DO UPDATE SET
                role = excluded.role,
                content_json = excluded.content_json,
                usage_json = excluded.usage_json
            """,
            (
                run_id,
                model_request_id,
                turn_index,
                record_id,
                role,
                _json(content),
                _json(usage or {}),
            ),
        )

    def append_tool_execution(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        record_id: str,
        decision_id: str | None,
        action_id: str | None,
        subject_id: str | None,
        tool_name: str,
        status: str,
        input_data: dict[str, JsonValue],
        output_data: dict[str, JsonValue] | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tool_executions (
                run_id, tool_call_id, record_id, decision_id, action_id,
                subject_id, tool_name, status, input_json, output_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, tool_call_id) DO UPDATE SET
                action_id = COALESCE(
                    excluded.action_id,
                    tool_executions.action_id
                ),
                status = excluded.status,
                output_json = CASE
                    WHEN excluded.output_json = '{}'
                    THEN tool_executions.output_json
                    ELSE excluded.output_json
                END
            """,
            (
                run_id,
                tool_call_id,
                record_id,
                decision_id,
                action_id,
                subject_id,
                tool_name,
                status,
                _json(input_data),
                _json(output_data or {}),
            ),
        )

    def append_decision_episode(
        self,
        *,
        run_id: str,
        decision_id: str,
        record_id: str,
        subject_id: str | None,
        action_id: str | None,
        goal_id: str | None,
        tool_call_id: str | None,
        status: str,
        selected_option_id: str | None,
        requested_tick: int,
        terminal_tick: int,
        requested_at: float,
        terminal_at: float,
        terminal_reason: str | None,
        delays: dict[str, JsonValue],
        episode: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO decision_episodes (
                run_id, decision_id, record_id, subject_id, action_id, goal_id,
                tool_call_id, status, selected_option_id, requested_tick,
                terminal_tick, requested_at, terminal_at, terminal_reason,
                delays_json, episode_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, decision_id) DO UPDATE SET
                record_id = excluded.record_id,
                action_id = excluded.action_id,
                goal_id = excluded.goal_id,
                tool_call_id = excluded.tool_call_id,
                status = excluded.status,
                selected_option_id = excluded.selected_option_id,
                terminal_tick = excluded.terminal_tick,
                terminal_at = excluded.terminal_at,
                terminal_reason = excluded.terminal_reason,
                delays_json = excluded.delays_json,
                episode_json = excluded.episode_json
            """,
            (
                run_id,
                decision_id,
                record_id,
                subject_id,
                action_id,
                goal_id,
                tool_call_id,
                status,
                selected_option_id,
                requested_tick,
                terminal_tick,
                requested_at,
                terminal_at,
                terminal_reason,
                _json(delays),
                _json(episode),
            ),
        )

    def append_memory_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
        record_id: str,
        subject_id: str | None,
        operation_type: str,
        status: str,
        memory_id: str | None,
        request: dict[str, JsonValue],
        result: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO memory_operations (
                run_id, operation_id, record_id, subject_id, operation_type,
                status, memory_id, request_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, operation_id) DO UPDATE SET
                record_id = excluded.record_id,
                status = excluded.status,
                memory_id = COALESCE(
                    excluded.memory_id,
                    memory_operations.memory_id
                ),
                result_json = excluded.result_json
            """,
            (
                run_id,
                operation_id,
                record_id,
                subject_id,
                operation_type,
                status,
                memory_id,
                _json(request),
                _json(result),
            ),
        )

    def append_information_retrieval(
        self,
        *,
        run_id: str,
        retrieval_id: str,
        record_id: str,
        subject_id: str | None,
        status: str,
        query: dict[str, JsonValue],
        result: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO information_retrievals (
                run_id, retrieval_id, record_id, subject_id, status,
                query_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, retrieval_id) DO UPDATE SET
                record_id = excluded.record_id,
                status = excluded.status,
                result_json = excluded.result_json
            """,
            (
                run_id,
                retrieval_id,
                record_id,
                subject_id,
                status,
                _json(query),
                _json(result),
            ),
        )

    def append_action_instance(
        self,
        *,
        run_id: str,
        action_id: str,
        record_id: str,
        plan_id: str | None,
        goal_id: str | None,
        decision_id: str | None,
        tool_call_id: str | None,
        subject_id: str | None,
        action_type: str,
        status: str,
        origin: str,
        plan_revision: int | None,
        created_tick: int,
        created_at: float,
        root_correlation_id: str,
        action: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO action_instances (
                run_id, action_id, record_id, plan_id, goal_id, decision_id,
                tool_call_id, subject_id, action_type, status, origin,
                plan_revision, created_tick, created_at, root_correlation_id,
                action_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, action_id) DO UPDATE SET
                status = excluded.status,
                action_json = excluded.action_json
            """,
            (
                run_id,
                action_id,
                record_id,
                plan_id,
                goal_id,
                decision_id,
                tool_call_id,
                subject_id,
                action_type,
                status,
                origin,
                plan_revision,
                created_tick,
                created_at,
                root_correlation_id,
                _json(action),
            ),
        )

    def update_action_status(
        self,
        *,
        run_id: str,
        action_id: str,
        status: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE action_instances SET status = ?
            WHERE run_id = ? AND action_id = ?
            """,
            (status, run_id, action_id),
        )

    def append_action_transition(
        self,
        *,
        run_id: str,
        action_transition_id: str,
        record_id: str,
        action_id: str,
        simulation_tick: int,
        from_status: str | None,
        to_status: str,
        transition: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "action_transitions",
            (
                "run_id",
                "action_transition_id",
                "record_id",
                "action_id",
                "simulation_tick",
                "from_status",
                "to_status",
                "transition_json",
            ),
            (
                run_id,
                action_transition_id,
                record_id,
                action_id,
                simulation_tick,
                from_status,
                to_status,
                _json(transition),
            ),
        )

    def append_plan(
        self,
        *,
        run_id: str,
        plan_id: str,
        record_id: str,
        subject_id: str | None,
        revision: int,
        origin: str,
        status: str,
        root_correlation_id: str | None,
        plan: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO plans (
                run_id, plan_id, record_id, subject_id, revision, origin,
                status, root_correlation_id, plan_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, plan_id) DO UPDATE SET
                record_id = excluded.record_id,
                revision = excluded.revision,
                status = excluded.status,
                plan_json = excluded.plan_json
            """,
            (
                run_id,
                plan_id,
                record_id,
                subject_id,
                revision,
                origin,
                status,
                root_correlation_id,
                _json(plan),
            ),
        )

    def append_goal_action_link(
        self,
        *,
        run_id: str,
        goal_id: str,
        action_id: str,
        record_id: str,
        link_kind: str,
        ordinal: int,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO goal_action_links (
                run_id, goal_id, action_id, record_id, link_kind, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, goal_id, action_id, record_id, link_kind, ordinal),
        )

    def append_action_episode(
        self,
        *,
        run_id: str,
        action_id: str,
        record_id: str,
        subject_id: str | None,
        terminal_status: str,
        created_tick: int,
        terminal_tick: int,
        created_at: float,
        terminal_at: float,
        elapsed_simulation_time: float,
        source_event_ids: tuple[str, ...],
        episode: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO action_episodes (
                run_id, action_id, record_id, subject_id, terminal_status,
                created_tick, terminal_tick, created_at, terminal_at,
                elapsed_simulation_time, source_event_ids_json, episode_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                action_id,
                record_id,
                subject_id,
                terminal_status,
                created_tick,
                terminal_tick,
                created_at,
                terminal_at,
                elapsed_simulation_time,
                _json(list(source_event_ids)),
                _json(episode),
            ),
        )

    def append_interaction(
        self,
        *,
        run_id: str,
        interaction_id: str,
        record_id: str,
        interaction_type: str,
        start_tick: int,
        end_tick: int | None,
        status: str,
        context: dict[str, JsonValue],
        outcome: dict[str, JsonValue] | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO interactions (
                run_id, interaction_id, record_id, interaction_type,
                start_tick, end_tick, status, context_json, outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                interaction_id,
                record_id,
                interaction_type,
                start_tick,
                end_tick,
                status,
                _json(context),
                _json(outcome or {}),
            ),
        )

    def append_interaction_participant(
        self,
        *,
        run_id: str,
        interaction_id: str,
        participant_id: str,
        role: str,
        participant: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO interaction_participants (
                run_id, interaction_id, participant_id, role, participant_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, interaction_id, participant_id, role, _json(participant)),
        )

    def append_interaction_event(
        self,
        *,
        run_id: str,
        interaction_id: str,
        event_id: str,
        record_id: str,
        event_index: int,
        event_type: str,
        simulation_tick: int,
        event: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO interaction_events (
                run_id, interaction_id, event_id, record_id, event_index,
                event_type, simulation_tick, event_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                interaction_id,
                event_id,
                record_id,
                event_index,
                event_type,
                simulation_tick,
                _json(event),
            ),
        )

    def append_interaction_episode(
        self,
        *,
        run_id: str,
        interaction_id: str,
        record_id: str,
        interaction_type: str,
        status: str,
        start_tick: int,
        terminal_tick: int,
        started_at: float,
        terminal_at: float,
        duration: float,
        initiating_goal_id: str | None,
        initiating_decision_id: str | None,
        initiating_action_id: str | None,
        initiating_tool_call_id: str | None,
        content_visibility: str,
        episode: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO interaction_episodes (
                run_id, interaction_id, record_id, interaction_type, status,
                start_tick, terminal_tick, started_at, terminal_at, duration,
                initiating_goal_id, initiating_decision_id,
                initiating_action_id, initiating_tool_call_id,
                content_visibility, episode_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                interaction_id,
                record_id,
                interaction_type,
                status,
                start_tick,
                terminal_tick,
                started_at,
                terminal_at,
                duration,
                initiating_goal_id,
                initiating_decision_id,
                initiating_action_id,
                initiating_tool_call_id,
                content_visibility,
                _json(episode),
            ),
        )

    def append_perception_fact(
        self,
        *,
        run_id: str,
        fact_id: str,
        record_id: str,
        source_event_id: str | None,
        fact_type: str,
        subject_id: str | None,
        object_id: str | None,
        location_id: str | None,
        modality: str,
        disclosure: str,
        created_tick: int,
        fact: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO perception_facts (
                run_id, fact_id, record_id, source_event_id, fact_type,
                subject_id, object_id, location_id, modality, disclosure,
                created_tick, fact_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                fact_id,
                record_id,
                source_event_id,
                fact_type,
                subject_id,
                object_id,
                location_id,
                modality,
                disclosure,
                created_tick,
                _json(fact),
            ),
        )

    def append_perception_delivery(
        self,
        *,
        run_id: str,
        delivery_id: str,
        fact_id: str,
        record_id: str,
        observer_id: str,
        status: str,
        reason: str | None,
        perceived_tick: int,
        fact_age: float,
        salience: float | None,
        delivery: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO perception_deliveries (
                run_id, delivery_id, fact_id, record_id, observer_id, status,
                reason, perceived_tick, fact_age, salience, delivery_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                delivery_id,
                fact_id,
                record_id,
                observer_id,
                status,
                reason,
                perceived_tick,
                fact_age,
                salience,
                _json(delivery),
            ),
        )

    def append_goal_episode(
        self,
        *,
        run_id: str,
        goal_id: str,
        record_id: str,
        subject_id: str | None,
        terminal_status: str,
        activated_tick: int,
        terminal_tick: int,
        activated_at: float,
        terminal_at: float,
        duration: float,
        episode: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO goal_episodes (
                run_id, goal_id, record_id, subject_id, terminal_status,
                activated_tick, terminal_tick, activated_at, terminal_at,
                duration, episode_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                goal_id,
                record_id,
                subject_id,
                terminal_status,
                activated_tick,
                terminal_tick,
                activated_at,
                terminal_at,
                duration,
                _json(episode),
            ),
        )

    def append_resource_sample(
        self,
        *,
        run_id: str,
        resource_sample_id: str,
        record_id: str,
        resource_id: str,
        resource_type: str,
        simulation_tick: int,
        phase: RunnerPhase,
        capacity: int | None,
        occupancy: int,
        queue_length: int,
        utilization: float | None,
        sample: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO resource_samples (
                run_id, resource_sample_id, record_id, resource_id,
                resource_type, simulation_tick, phase, capacity, occupancy,
                queue_length, utilization, sample_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                resource_sample_id,
                record_id,
                resource_id,
                resource_type,
                simulation_tick,
                phase.value,
                capacity,
                occupancy,
                queue_length,
                utilization,
                _json(sample),
            ),
        )

    def append_resource_flow(
        self,
        *,
        run_id: str,
        resource_flow_id: str,
        record_id: str,
        resource_id: str,
        subject_id: str | None,
        simulation_tick: int,
        flow_type: str,
        amount: float | None,
        flow: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO resource_flows (
                run_id, resource_flow_id, record_id, resource_id, subject_id,
                simulation_tick, flow_type, amount, flow_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                resource_flow_id,
                record_id,
                resource_id,
                subject_id,
                simulation_tick,
                flow_type,
                amount,
                _json(flow),
            ),
        )

    def append_memory_relation(
        self,
        *,
        run_id: str,
        relation_id: str,
        record_id: str,
        memory_id: str,
        subject_id: str | None,
        relation_type: str,
        source_type: str,
        source_id: str,
        relation: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO memory_relations (
                run_id, relation_id, record_id, memory_id, subject_id,
                relation_type, source_type, source_id, relation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                relation_id,
                record_id,
                memory_id,
                subject_id,
                relation_type,
                source_type,
                source_id,
                _json(relation),
            ),
        )

    def append_opportunity_sample(
        self,
        *,
        run_id: str,
        opportunity_sample_id: str,
        record_id: str,
        subject_id: str | None,
        simulation_tick: int,
        selected_option_id: str | None,
        context: dict[str, JsonValue],
        options: list[JsonValue],
    ) -> None:
        self._insert_projection(
            "opportunity_samples",
            (
                "run_id",
                "opportunity_sample_id",
                "record_id",
                "subject_id",
                "simulation_tick",
                "selected_option_id",
                "context_json",
                "options_json",
            ),
            (
                run_id,
                opportunity_sample_id,
                record_id,
                subject_id,
                simulation_tick,
                selected_option_id,
                _json(context),
                _json(options),
            ),
        )

    def append_transition_sample(
        self,
        *,
        run_id: str,
        transition_sample_id: str,
        record_id: str,
        subject_id: str | None,
        action_id: str | None,
        start_tick: int,
        end_tick: int,
        elapsed_simulation_time: float,
        outcome: str,
        state_before: dict[str, JsonValue],
        action: dict[str, JsonValue],
        exogenous_context: dict[str, JsonValue],
        state_after: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "transition_samples",
            (
                "run_id",
                "transition_sample_id",
                "record_id",
                "subject_id",
                "action_id",
                "start_tick",
                "end_tick",
                "elapsed_simulation_time",
                "outcome",
                "state_before_json",
                "action_json",
                "exogenous_context_json",
                "state_after_json",
            ),
            (
                run_id,
                transition_sample_id,
                record_id,
                subject_id,
                action_id,
                start_tick,
                end_tick,
                elapsed_simulation_time,
                outcome,
                _json(state_before),
                _json(action),
                _json(exogenous_context),
                _json(state_after),
            ),
        )

    def append_population_sample(
        self,
        *,
        run_id: str,
        population_sample_id: str,
        record_id: str,
        simulation_tick: int,
        phase: RunnerPhase,
        population: dict[str, JsonValue],
    ) -> None:
        self._insert_projection(
            "population_samples",
            (
                "run_id",
                "population_sample_id",
                "record_id",
                "simulation_tick",
                "phase",
                "population_json",
            ),
            (
                run_id,
                population_sample_id,
                record_id,
                simulation_tick,
                phase.value,
                _json(population),
            ),
        )

    def _insert_projection(
        self,
        table: str,
        columns: tuple[str, ...],
        values: tuple[object, ...],
    ) -> None:
        placeholders = ", ".join("?" for _ in values)
        self._connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def flush(self) -> None:
        self._commit()

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        final_tick: int,
        final_simulation_time: float,
    ) -> None:
        cursor = self._connection.execute(
            """
            UPDATE runs
            SET status = ?, final_tick = ?, final_simulation_time = ?,
                completed_at = ?, capture_complete = CASE
                    WHEN ? IN ('completed', 'stopped')
                     AND EXISTS (
                         SELECT 1 FROM records
                         WHERE records.run_id = runs.run_id
                           AND records.phase = ?
                     )
                    THEN 1 ELSE 0 END,
                interruption_reason = NULL
            WHERE run_id = ? AND owner_instance_id = ?
            """,
            (
                status,
                final_tick,
                final_simulation_time,
                datetime.now(UTC).isoformat(),
                status,
                RunnerPhase.RUN_FINAL.value,
                run_id,
                self.instance_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"run is no longer writable by this dataset-store instance: {run_id}"
            )
        self._commit()

    def reconcile_incomplete_runs(self) -> tuple[str, ...]:
        """Mark nonterminal runs as interrupted only after their owner lease ends."""
        if self._connection.in_transaction:
            return ()
        now = datetime.now(UTC)
        completed_at = now.isoformat()
        lease_cutoff = (
            now - timedelta(seconds=_INSTANCE_LEASE_TIMEOUT_SECONDS)
        ).isoformat()
        reason = (
            "reconciled as interrupted because the owning dataset-store lease "
            "is closed, missing, or expired"
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """
                SELECT runs.run_id
                FROM runs
                LEFT JOIN dataset_store_instances AS owner
                  ON owner.instance_id = runs.owner_instance_id
                WHERE runs.status IN ('created', 'running', 'paused')
                  AND (
                      runs.owner_instance_id IS NULL
                      OR (
                          runs.owner_instance_id != ?
                          AND (
                              owner.instance_id IS NULL
                              OR owner.closed_at IS NOT NULL
                              OR owner.heartbeat_at < ?
                          )
                      )
                  )
                ORDER BY runs.run_id
                """,
                (self.instance_id, lease_cutoff),
            ).fetchall()
            run_ids = tuple(str(row["run_id"]) for row in rows)
            if not run_ids:
                self._commit()
                return ()
            for run_id in run_ids:
                latest = self._connection.execute(
                    """
                    SELECT MAX(simulation_tick) AS final_tick,
                           MAX(simulation_time) AS final_simulation_time
                    FROM records WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                self._connection.execute(
                    """
                    UPDATE runs
                    SET status = 'interrupted',
                        final_tick = COALESCE(?, final_tick, 0),
                        final_simulation_time = COALESCE(
                            ?, final_simulation_time, 0.0
                        ),
                        completed_at = ?,
                        capture_complete = 0,
                        interruption_reason = ?,
                        owner_instance_id = ?
                    WHERE run_id = ?
                    """,
                    (
                        latest["final_tick"],
                        latest["final_simulation_time"],
                        completed_at,
                        reason,
                        self.instance_id,
                        run_id,
                    ),
                )
            self._commit()
        except sqlite3.OperationalError as error:
            self._connection.rollback()
            error_code = getattr(error, "sqlite_errorcode", None)
            if (
                isinstance(error_code, int)
                and error_code & 0xFF
                in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            ):
                return ()
            raise
        except Exception:
            self._connection.rollback()
            raise
        return run_ids

    def list_persisted_runs(
        self,
        filters: PersistedRunFilter,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> PersistedRunPage:
        clauses: list[str] = []
        parameters: list[object] = []
        if filters.search_text:
            pattern = _like_pattern(filters.search_text)
            clauses.append(
                """
                (
                    run_id LIKE ? ESCAPE '\\'
                    OR COALESCE(json_extract(scenario_json, '$.name'), '')
                       LIKE ? ESCAPE '\\'
                )
                """
            )
            parameters.extend((pattern, pattern))
        if filters.persisted_statuses:
            placeholders = ", ".join("?" for _ in filters.persisted_statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(filters.persisted_statuses)
        if filters.scenario_name is not None:
            clauses.append("json_extract(scenario_json, '$.name') = ?")
            parameters.append(filters.scenario_name)
        if filters.dataset_schema_version is not None:
            clauses.append("schema_version = ?")
            parameters.append(filters.dataset_schema_version)
        if filters.capture_complete is not None:
            clauses.append("capture_complete = ?")
            parameters.append(int(filters.capture_complete))
        for column, operator, value in (
            ("started_at", ">=", filters.started_at_or_after),
            ("started_at", "<", filters.started_before),
            ("completed_at", ">=", filters.completed_at_or_after),
            ("completed_at", "<", filters.completed_before),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                parameters.append(_database_datetime(value))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT runs.*,
                   (
                       SELECT COUNT(*) FROM records
                       WHERE records.run_id = runs.run_id
                   ) AS record_count
            FROM runs
            {where}
            ORDER BY started_at DESC, run_id DESC
            """,
            parameters,
        ).fetchall()
        live_statuses = effective_statuses or {}
        summaries = [
            self._persisted_run_summary(
                row,
                live_statuses.get(str(row["run_id"])),
            )
            for row in rows
        ]
        if filters.effective_statuses:
            allowed = frozenset(filters.effective_statuses)
            summaries = [
                summary
                for summary in summaries
                if summary.effective_status in allowed
            ]
        total_count = len(summaries)
        if filters.cursor is not None:
            cursor = _decode_cursor(filters.cursor, 2)
            started_at, run_id = cursor
            if not isinstance(started_at, str) or not isinstance(run_id, str):
                raise ValueError("invalid run catalog cursor")
            summaries = [
                summary
                for summary in summaries
                if (
                    summary.started_at.isoformat() < started_at
                    or (
                        summary.started_at.isoformat() == started_at
                        and summary.run_id < run_id
                    )
                )
            ]
        page_runs = summaries[: filters.limit]
        next_cursor = None
        if len(summaries) > filters.limit and page_runs:
            last = page_runs[-1]
            next_cursor = _encode_cursor(
                [last.started_at.isoformat(), last.run_id]
            )
        return PersistedRunPage(
            runs=tuple(page_runs),
            next_cursor=next_cursor,
            total_count=total_count,
            filters=filters,
        )

    def get_persisted_runs(
        self,
        run_ids: Sequence[str],
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> tuple[PersistedRunSummary, ...]:
        if not run_ids:
            return ()
        placeholders = ", ".join("?" for _ in run_ids)
        rows = self._connection.execute(
            f"""
            SELECT runs.*,
                   (
                       SELECT COUNT(*) FROM records
                       WHERE records.run_id = runs.run_id
                   ) AS record_count
            FROM runs
            WHERE run_id IN ({placeholders})
            """,
            tuple(run_ids),
        ).fetchall()
        by_id = {str(row["run_id"]): row for row in rows}
        live_statuses = effective_statuses or {}
        return tuple(
            self._persisted_run_summary(
                by_id[run_id],
                live_statuses.get(run_id),
            )
            for run_id in run_ids
            if run_id in by_id
        )

    def deletion_preview(
        self,
        selection: RunSelection,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> RunDeletionPreview:
        if selection.fingerprint != selection_fingerprint(
            selection.run_ids,
            selection.filters,
        ):
            raise ValueError("stale or invalid run selection fingerprint")
        runs = self.get_persisted_runs(selection.run_ids, effective_statuses)
        if len(runs) != len(selection.run_ids):
            found = {run.run_id for run in runs}
            missing = [
                run_id for run_id in selection.run_ids if run_id not in found
            ]
            raise KeyError(f"unknown persisted runs: {', '.join(missing)}")
        foreign_owned = self._foreign_owned_runs_with_active_leases(
            selection.run_ids
        )
        ineligible = tuple(
            run.run_id
            for run in runs
            if (
                run.persisted_status not in TERMINAL_RUN_STATUSES
                or run.effective_status not in TERMINAL_RUN_STATUSES
                or (run.live and not run.deletion_ready)
                or run.run_id in foreign_owned
            )
        )
        table_counts = self._selection_table_counts(selection.run_ids)
        token = deletion_confirmation_token(selection, runs, table_counts)
        return RunDeletionPreview(
            selection=selection,
            runs=runs,
            table_counts=table_counts,
            total_records=table_counts["records"],
            eligible=not ineligible,
            ineligible_run_ids=ineligible,
            confirmation_token=token,
        )

    def delete_runs(
        self,
        selection: RunSelection,
        confirmation_token: str,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> RunDeletionResult:
        if self._connection.in_transaction:
            raise ValueError(
                "dataset capture transaction is active; retry deletion after "
                "the current tick boundary"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            preview = self.deletion_preview(selection, effective_statuses)
            if preview.confirmation_token != confirmation_token:
                raise ValueError("stale deletion preview or confirmation token")
            if not preview.eligible:
                raise ValueError(
                    "all selected runs must be terminal and fully finalized: "
                    + ", ".join(preview.ineligible_run_ids)
                )
            placeholders = ", ".join("?" for _ in selection.run_ids)
            deleted_counts: dict[str, int] = {}
            for table in RUN_SCOPED_TABLES:
                cursor = self._connection.execute(
                    f"DELETE FROM {table} WHERE run_id IN ({placeholders})",
                    selection.run_ids,
                )
                deleted_counts[table] = cursor.rowcount
            for table in RUN_SCOPED_TABLES:
                remaining = int(
                    self._connection.execute(
                        f"""
                        SELECT COUNT(*) FROM {table}
                        WHERE run_id IN ({placeholders})
                        """,
                        selection.run_ids,
                    ).fetchone()[0]
                )
                if remaining:
                    raise RuntimeError(
                        f"run deletion left {remaining} rows in {table}"
                    )
            self._commit()
        except Exception:
            self._connection.rollback()
            raise
        return RunDeletionResult(
            run_ids=selection.run_ids,
            deleted_table_counts=deleted_counts,
            deleted_record_count=deleted_counts["records"],
            confirmation_token=confirmation_token,
        )

    def _foreign_owned_runs_with_active_leases(
        self,
        run_ids: tuple[str, ...],
    ) -> frozenset[str]:
        if not run_ids:
            return frozenset()
        lease_cutoff = (
            datetime.now(UTC)
            - timedelta(seconds=_INSTANCE_LEASE_TIMEOUT_SECONDS)
        ).isoformat()
        placeholders = ", ".join("?" for _ in run_ids)
        rows = self._connection.execute(
            f"""
            SELECT runs.run_id
            FROM runs
            JOIN dataset_store_instances AS owner
              ON owner.instance_id = runs.owner_instance_id
            WHERE runs.run_id IN ({placeholders})
              AND runs.owner_instance_id != ?
              AND owner.closed_at IS NULL
              AND owner.heartbeat_at >= ?
            """,
            (*run_ids, self.instance_id, lease_cutoff),
        )
        return frozenset(str(row["run_id"]) for row in rows)

    def _selection_table_counts(
        self,
        run_ids: tuple[str, ...],
    ) -> dict[str, int]:
        placeholders = ", ".join("?" for _ in run_ids)
        return {
            table: int(
                self._connection.execute(
                    f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE run_id IN ({placeholders})
                    """,
                    run_ids,
                ).fetchone()[0]
            )
            for table in RUN_SCOPED_TABLES
        }

    def _persisted_run_summary(
        self,
        row: sqlite3.Row,
        live_status: LiveRunOverlay | str | None,
    ) -> PersistedRunSummary:
        run_id = str(row["run_id"])
        scenario = json.loads(str(row["scenario_json"]))
        if not isinstance(scenario, dict):
            raise ValueError(f"stored scenario for {run_id} is not an object")
        runtime = scenario.get("runtime_configuration")
        runtime_configuration = runtime if isinstance(runtime, dict) else {}
        cognition = scenario.get("cognition")
        cognition_configuration = cognition if isinstance(cognition, dict) else {}
        scenario_name = scenario.get("name")
        scenario_identity = scenario.get("id")
        scenario_schema = scenario.get("schema_version")
        world = scenario.get("world")
        world_type = world.get("type") if isinstance(world, dict) else None
        raw_capture_configuration = scenario.get(
            "capture_configuration",
            runtime_configuration.get("capture_configuration"),
        )
        capture_configuration = (
            raw_capture_configuration
            if isinstance(raw_capture_configuration, dict)
            else {}
        )
        feature_rows = self._connection.execute(
            """
            SELECT schema_id, schema_version FROM records
            WHERE run_id = ? AND schema_id LIKE 'stage0.feature.%'
            GROUP BY schema_id, schema_version
            ORDER BY schema_id, schema_version
            """,
            (run_id,),
        )
        feature_versions = {
            str(feature["schema_id"]): str(feature["schema_version"])
            for feature in feature_rows
        }
        persisted_status = str(row["status"])
        overlay = (
            live_status
            if isinstance(live_status, LiveRunOverlay)
            else (
                LiveRunOverlay(
                    status=live_status,
                    cognition_phase="unknown",
                    deletion_ready=False,
                )
                if live_status is not None
                else None
            )
        )
        return PersistedRunSummary(
            run_id=run_id,
            persisted_status=persisted_status,
            effective_status=overlay.status if overlay is not None else persisted_status,
            live=overlay is not None,
            live_cognition_phase=(
                overlay.cognition_phase if overlay is not None else None
            ),
            deletion_ready=overlay.deletion_ready if overlay is not None else True,
            scenario_identity=(
                scenario_identity
                if isinstance(scenario_identity, str)
                else (
                    scenario_name if isinstance(scenario_name, str) else run_id
                )
            ),
            scenario_name=(
                scenario_name if isinstance(scenario_name, str) else run_id
            ),
            dataset_schema_version=str(row["schema_version"]),
            world_schema_version=_world_schema_version(
                scenario_schema,
                world_type,
                world is not None,
            ),
            capture_configuration=capture_configuration,
            capture_complete=bool(row["capture_complete"]),
            record_count=int(row["record_count"]),
            final_tick=(
                int(row["final_tick"])
                if row["final_tick"] is not None
                else None
            ),
            final_simulation_time=(
                float(row["final_simulation_time"])
                if row["final_simulation_time"] is not None
                else None
            ),
            seed=int(row["seed"]),
            dt=float(row["dt"]),
            initial_speed=float(row["initial_speed"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
            interruption_reason=(
                str(row["interruption_reason"])
                if row["interruption_reason"] is not None
                else None
            ),
            cognition_execution_mode=_first_text(
                runtime_configuration.get("cognition_execution_mode"),
                cognition_configuration.get("execution_mode"),
            ),
            requested_npc_control_mode=_first_text(
                runtime_configuration.get("npc_control_mode"),
                cognition_configuration.get("npc_control_mode"),
            ),
            effective_npc_control_mode=_first_text(
                runtime_configuration.get("effective_npc_control_mode"),
            ),
            feature_schema_versions=feature_versions,
        )

    def query_records(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> DatasetRecordPage:
        self._require_run(run_id)
        query = filters or DatasetRecordFilter()
        clauses = ["run_id = ?"]
        parameters: list[object] = [run_id]
        for column, value in (
            ("record_type", query.record_type),
            ("category", query.category.value if query.category is not None else None),
            ("schema_id", query.schema_id),
            ("schema_version", query.schema_version),
            ("agent_id", query.agent_id),
            ("subject_id", query.subject_id),
            (
                "visibility",
                query.visibility.value if query.visibility is not None else None,
            ),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if query.minimum_tick is not None:
            clauses.append("simulation_tick >= ?")
            parameters.append(query.minimum_tick)
        if query.maximum_tick is not None:
            clauses.append("simulation_tick <= ?")
            parameters.append(query.maximum_tick)
        if query.minimum_time is not None:
            clauses.append("simulation_time >= ?")
            parameters.append(query.minimum_time)
        if query.maximum_time is not None:
            clauses.append("simulation_time <= ?")
            parameters.append(query.maximum_time)
        if query.related_entity_id is not None:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM json_each(records.related_entity_ids_json)
                    WHERE json_each.value = ?
                )
                """
            )
            parameters.append(query.related_entity_id)
        for column in _DOMAIN_ID_COLUMNS:
            value = getattr(query, column)
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if query.status is not None:
            clauses.append(
                """
                COALESCE(
                    json_extract(payload_json, '$.status'),
                    json_extract(payload_json, '$.terminal_status'),
                    json_extract(payload_json, '$.to_status')
                ) = ?
                """
            )
            parameters.append(query.status)
        if query.outcome is not None:
            clauses.append(
                """
                COALESCE(
                    json_extract(payload_json, '$.outcome'),
                    json_extract(payload_json, '$.terminal_outcome'),
                    json_extract(payload_json, '$.terminal_status'),
                    json_extract(payload_json, '$.status')
                ) = ?
                """
            )
            parameters.append(query.outcome)
        if not query.include_private:
            clauses.append("visibility != ?")
            parameters.append(RecordVisibility.PRIVATE_RESEARCH.value)
        if query.after_sequence is not None:
            clauses.append("sequence > ?")
            parameters.append(query.after_sequence)
        parameters.append(query.limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT * FROM records
            WHERE {' AND '.join(clauses)}
            ORDER BY sequence
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        records = tuple(_record_from_row(row) for row in page_rows)
        next_cursor = records[-1].sequence if has_more and records else None
        return DatasetRecordPage(records=records, next_cursor=next_cursor)

    def query_table(
        self,
        run_id: str,
        table_or_alias: str,
        filters: DatasetQueryFilter | None = None,
    ) -> DatasetQueryPage:
        self._require_run(run_id)
        query = filters or DatasetQueryFilter()
        table = _QUERY_TABLE_ALIASES.get(table_or_alias, table_or_alias)
        if table not in _ANALYSIS_TABLES:
            raise ValueError(f"unknown dataset table: {table_or_alias}")
        columns = self._table_columns(table)
        primary_key = self._table_primary_key(table)
        if not primary_key:
            raise RuntimeError(f"dataset table has no stable primary key: {table}")
        from_sql = self._query_table_source(table)
        clauses = ["p.run_id = ?"]
        parameters: list[object] = [run_id]
        for column, value in (
            ("record_type", query.record_type),
            (
                "category",
                query.category.value if query.category is not None else None,
            ),
            ("schema_id", query.schema_id),
            ("schema_version", query.schema_version),
        ):
            if value is not None:
                clauses.append(f"r.{column} = ?")
                parameters.append(value)
        if query.primary_entity_id is not None:
            entity_clauses = [
                f"p.{column} = ?"
                for column in (
                    "subject_id",
                    "observer_id",
                    "participant_id",
                )
                if column in columns
            ]
            entity_parameters = [query.primary_entity_id] * len(entity_clauses)
            entity_clauses.extend(("r.subject_id = ?", "r.agent_id = ?"))
            entity_parameters.extend(
                (query.primary_entity_id, query.primary_entity_id)
            )
            if table in {"interactions", "interaction_episodes"}:
                entity_clauses.append(
                    """
                    EXISTS (
                        SELECT 1 FROM interaction_participants ip
                        WHERE ip.run_id = p.run_id
                          AND ip.interaction_id = p.interaction_id
                          AND ip.participant_id = ?
                    )
                    """
                )
                entity_parameters.append(query.primary_entity_id)
            clauses.append(f"({' OR '.join(entity_clauses)})")
            parameters.extend(entity_parameters)
        if query.related_entity_id is not None:
            related_clauses = [
                """
                EXISTS (
                    SELECT 1 FROM json_each(r.related_entity_ids_json)
                    WHERE json_each.value = ?
                )
                """
            ]
            related_parameters: list[object] = [query.related_entity_id]
            if table in {"interactions", "interaction_episodes"}:
                related_clauses.append(
                    """
                    EXISTS (
                        SELECT 1 FROM interaction_participants ip
                        WHERE ip.run_id = p.run_id
                          AND ip.interaction_id = p.interaction_id
                          AND ip.participant_id = ?
                    )
                    """
                )
                related_parameters.append(query.related_entity_id)
            clauses.append(f"({' OR '.join(related_clauses)})")
            parameters.extend(related_parameters)
        for operator, tick_value in (
            (">=", query.minimum_tick),
            ("<=", query.maximum_tick),
        ):
            if tick_value is not None:
                clauses.append(f"r.simulation_tick {operator} ?")
                parameters.append(tick_value)
        for operator, time_value in (
            (">=", query.minimum_time),
            ("<=", query.maximum_time),
        ):
            if time_value is not None:
                clauses.append(f"r.simulation_time {operator} ?")
                parameters.append(time_value)
        if query.visibility is not None:
            clauses.append("r.visibility = ?")
            parameters.append(query.visibility.value)
        if not query.include_private:
            clauses.append("r.visibility != ?")
            parameters.append(RecordVisibility.PRIVATE_RESEARCH.value)
        for column in _DOMAIN_ID_COLUMNS:
            value = getattr(query, column)
            if value is None:
                continue
            source = "p" if column in columns else "r"
            clauses.append(f"{source}.{column} = ?")
            parameters.append(value)
        if query.status is not None:
            status_columns = [
                column
                for column in ("status", "terminal_status", "to_status")
                if column in columns
            ]
            if not status_columns:
                clauses.append(
                    """
                    COALESCE(
                        json_extract(r.payload_json, '$.status'),
                        json_extract(r.payload_json, '$.terminal_status'),
                        json_extract(r.payload_json, '$.to_status')
                    ) = ?
                    """
                )
                parameters.append(query.status)
            else:
                clauses.append(
                    "("
                    + " OR ".join(f"p.{column} = ?" for column in status_columns)
                    + ")"
                )
                parameters.extend([query.status] * len(status_columns))
        if query.outcome is not None:
            outcome_columns = [
                column
                for column in ("outcome", "terminal_status", "status")
                if column in columns
            ]
            if outcome_columns:
                clauses.append(
                    "("
                    + " OR ".join(f"p.{column} = ?" for column in outcome_columns)
                    + ")"
                )
                parameters.extend([query.outcome] * len(outcome_columns))
            else:
                clauses.append(
                    """
                    COALESCE(
                        json_extract(r.payload_json, '$.outcome'),
                        json_extract(r.payload_json, '$.terminal_status'),
                        json_extract(r.payload_json, '$.status')
                    ) = ?
                    """
                )
                parameters.append(query.outcome)
        order_columns = ("r.sequence",) + tuple(
            f"p.{column}" for column in primary_key if column != "run_id"
        )
        if query.cursor is not None:
            cursor = _decode_cursor(query.cursor, len(order_columns))
            clauses.append(
                f"({', '.join(order_columns)}) > "
                f"({', '.join('?' for _ in order_columns)})"
            )
            parameters.extend(cursor)
        parameters.append(query.limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT p.*, r.sequence AS record_sequence,
                   r.visibility AS record_visibility,
                   r.simulation_tick AS record_simulation_tick,
                   r.simulation_time AS record_simulation_time
            {from_sql}
            WHERE {' AND '.join(clauses)}
            ORDER BY {', '.join(order_columns)}
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        result = tuple(_json_safe_row(row) for row in page_rows)
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            cursor_values: list[JsonValue] = [int(last["record_sequence"])]
            cursor_values.extend(last[column] for column in primary_key if column != "run_id")
            next_cursor = _encode_cursor(cursor_values)
        return DatasetQueryPage(rows=result, next_cursor=next_cursor)

    def iter_records_ndjson(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> Iterator[str]:
        query = filters or DatasetRecordFilter()
        cursor = query.after_sequence
        while True:
            page = self.query_records(
                run_id,
                replace(query, after_sequence=cursor, limit=1000),
            )
            for record in page.records:
                yield _json(record.to_dict())
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def persisted_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[tuple[dict[str, JsonValue], ...], int]:
        self._require_run(run_id)
        total = int(
            self._connection.execute(
                """
                SELECT COUNT(*) FROM records
                WHERE run_id = ? AND record_type = 'event'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        rows = self._connection.execute(
            """
            SELECT * FROM records
            WHERE run_id = ? AND record_type = 'event'
            ORDER BY sequence
            LIMIT ? OFFSET ?
            """,
            (run_id, limit, offset),
        )
        events: list[dict[str, JsonValue]] = []
        for row in rows:
            record = _record_from_row(row)
            event_payload = record.payload
            payload = event_payload.get("payload", {})
            events.append(
                {
                    "run_id": run_id,
                    "event_id": record.source_event_id,
                    "simulation_tick": record.simulation_tick,
                    "simulation_time": record.simulation_time,
                    "wall_time": event_payload.get("wall_time"),
                    "agent_id": record.agent_id,
                    "event_type": event_payload.get("event_type"),
                    "payload": payload if isinstance(payload, dict) else {},
                    "causation_id": record.causation_id,
                    "correlation_id": record.correlation_id,
                }
            )
        return tuple(events), total

    def data_dictionary(self, run_id: str | None = None) -> dict[str, JsonValue]:
        observed_schemas: list[JsonValue] = []
        if run_id is not None:
            self._require_run(run_id)
            observed_schemas = [
                {
                    "schema_id": str(row["schema_id"]),
                    "schema_version": str(row["schema_version"]),
                    "record_type": str(row["record_type"]),
                    "category": str(row["category"]),
                    "visibility": str(row["visibility"]),
                    "record_count": int(row["record_count"]),
                }
                for row in self._connection.execute(
                    """
                    SELECT schema_id, schema_version, record_type, category,
                           visibility, COUNT(*) AS record_count
                    FROM records WHERE run_id = ?
                    GROUP BY schema_id, schema_version, record_type, category,
                             visibility
                    ORDER BY schema_id, schema_version, record_type, visibility
                    """,
                    (run_id,),
                )
            ]
        envelope_fields: list[JsonValue] = []
        for contract_field in fields(DatasetRecord):
            name = contract_field.name
            envelope_fields.append(
                {
                    "name": name,
                    "meaning": _field_meaning(name),
                    "visibility": (
                        "classification"
                        if name == "visibility"
                        else "applies to every visibility class"
                    ),
                    "phase": (
                        "classification"
                        if name == "phase"
                        else "available in every runner phase"
                    ),
                    "canonical": name not in {"wall_time", "source_metadata"},
                    "nondeterministic": name in {"wall_time", "source_metadata"},
                }
            )
        tables: list[JsonValue] = []
        for table in _ANALYSIS_TABLES:
            table_fields: list[JsonValue] = []
            for row in self._connection.execute(f"PRAGMA table_info({table})"):
                name = str(row["name"])
                table_fields.append(
                    {
                        "name": name,
                        "storage_type": str(row["type"]),
                        "nullable": not bool(row["notnull"]),
                        "primary_key_ordinal": int(row["pk"]),
                        "meaning": _field_meaning(name),
                        "json_encoded": name.endswith("_json"),
                        "canonical": not _nondeterministic_field(name),
                        "nondeterministic": _nondeterministic_field(name),
                    }
                )
            tables.append(
                {
                    "name": table,
                    "meaning": _table_meaning(table),
                    "visibility": "inherited from the linked raw record",
                    "phase": "inherited from the linked raw record",
                    "fields": table_fields,
                }
            )
        return {
            "schema_id": "stage0.data_dictionary",
            "schema_version": "1",
            "dataset_schema_id": "stage0.dataset",
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "record_visibility_values": [
                visibility.value for visibility in RecordVisibility
            ],
            "runner_phase_values": [phase.value for phase in RunnerPhase],
            "record_category_values": [
                category.value for category in RecordCategory
            ],
            "canonical_rule": (
                "simulation-owned IDs, sequence, ticks, simulation time, and "
                "serialized authoritative state are canonical"
            ),
            "nondeterministic_rule": (
                "wall-clock values and provider-assigned metadata are explicit "
                "and excluded from canonical comparisons"
            ),
            "record_envelope": envelope_fields,
            "feature_schema_versions": dict(sorted(_FEATURE_SCHEMA_VERSIONS.items())),
            "normalized_and_derived_tables": tables,
            "observed_record_schemas": observed_schemas,
        }

    def write_analysis_bundle(
        self,
        run_id: str,
        destination: BinaryIO,
        filters: DatasetQueryFilter | None = None,
    ) -> None:
        self._require_run(run_id)
        query = filters or DatasetQueryFilter()
        applied_filters = _query_filter_payload(query)
        table_files = [
            f"tables/{_safe_filename(table)}.csv" for table in _ANALYSIS_TABLES
        ]
        manifest: dict[str, JsonValue] = {
            "schema_id": "stage0.analysis_bundle.manifest",
            "schema_version": "1",
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "run_id": run_id,
            "filters": applied_filters,
            "private_records_included": query.include_private,
            "ordering": {
                "records.ndjson": ["sequence"],
                "csv": ["record_sequence", "table primary key"],
                "zip_entries": [
                    "manifest.json",
                    "schema.json",
                    "records.ndjson",
                    *table_files,
                ],
            },
            "files": [
                "manifest.json",
                "schema.json",
                "records.ndjson",
                *table_files,
            ],
            "summary": self.summary(run_id),
        }
        record_filter = _record_filter_from_query(query)
        with zipfile.ZipFile(
            destination,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.writestr(
                _zip_info("manifest.json"),
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            archive.writestr(
                _zip_info("schema.json"),
                json.dumps(
                    self.data_dictionary(run_id),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            with archive.open(_zip_info("records.ndjson"), mode="w") as raw:
                for line in self.iter_records_ndjson(run_id, record_filter):
                    raw.write(line.encode("utf-8"))
                    raw.write(b"\n")
            for table, filename in zip(_ANALYSIS_TABLES, table_files, strict=True):
                self._write_table_csv(archive, filename, run_id, table, query)

    def iter_jsonl(self, run_id: str) -> Iterator[str]:
        export_connection = sqlite3.connect(self.path)
        export_connection.row_factory = sqlite3.Row
        try:
            run = export_connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown persisted run: {run_id}")
            manifest: dict[str, JsonValue] = {
                "schema_id": "stage0.run.manifest",
                "schema_version": str(run["schema_version"]),
                "record_id": f"{run_id}:record:00000000",
                "record_type": "run",
                "category": RecordCategory.RUN.value,
                "source": RecordSource.APPLICATION.value,
                "phase": RunnerPhase.RUN_INITIAL.value,
                "visibility": RecordVisibility.OPERATOR.value,
                "run_id": str(run["run_id"]),
                "sequence": 0,
                "simulation_tick": 0,
                "simulation_time": 0.0,
                "payload": {
                    "seed": int(run["seed"]),
                    "dt": float(run["dt"]),
                    "initial_speed": float(run["initial_speed"]),
                    "scenario": json.loads(str(run["scenario_json"])),
                    "status": str(run["status"]),
                    "final_tick": (
                        int(run["final_tick"])
                        if run["final_tick"] is not None
                        else None
                    ),
                    "final_simulation_time": (
                        float(run["final_simulation_time"])
                        if run["final_simulation_time"] is not None
                        else None
                    ),
                },
            }
            yield _json(manifest)
            rows = export_connection.execute(
                """
                SELECT * FROM records
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            )
            next_sequence = 1
            for row in rows:
                record = _record_from_row(row)
                yield _json(record.to_dict())
                next_sequence = max(next_sequence, record.sequence + 1)
            document_rows = export_connection.execute(
                """
                SELECT document_json FROM information_documents
                WHERE run_id = ?
                ORDER BY namespace_id, kind, document_id, revision
                """,
                (run_id,),
            )
            for row in document_rows:
                document = information_document_from_dict(
                    json.loads(str(row["document_json"]))
                )
                record = DatasetRecord(
                    run_id=run_id,
                    sequence=next_sequence,
                    record_type="information_document",
                    simulation_tick=0,
                    simulation_time=(
                        document.recorded_at
                        if document.recorded_at is not None
                        else 0.0
                    ),
                    agent_id=None,
                    source_event_id=None,
                    payload=document.to_dict(),
                    schema_id=document.schema_id,
                    category=RecordCategory.INFORMATION,
                    source=RecordSource.APPLICATION,
                    subject_id=(
                        document.subject_ids[0] if document.subject_ids else None
                    ),
                    related_entity_ids=document.subject_ids[1:],
                )
                yield _json(record.to_dict())
                next_sequence += 1
        finally:
            export_connection.close()

    def summary(self, run_id: str) -> dict[str, JsonValue]:
        run = self._connection.execute(
            """
            SELECT status, final_tick, final_simulation_time
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(f"unknown persisted run: {run_id}")
        status = str(run["status"])
        run_final_count = int(
            self._connection.execute(
                """
                SELECT COUNT(*) FROM records
                WHERE run_id = ? AND phase = ?
                """,
                (run_id, RunnerPhase.RUN_FINAL.value),
            ).fetchone()[0]
        )
        record_count = self._table_count(run_id, "records")
        maximum_sequence = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM records WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        sequence_gap_count = maximum_sequence - record_count
        coverage_count = int(
            self._connection.execute(
                """
                SELECT COUNT(*) FROM records
                WHERE run_id = ? AND record_type = 'capture_coverage'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        return {
            "schema_version": DATASET_SCHEMA_VERSION,
            "run_id": run_id,
            "status": status,
            "final_tick": (
                int(run["final_tick"]) if run["final_tick"] is not None else None
            ),
            "final_simulation_time": (
                float(run["final_simulation_time"])
                if run["final_simulation_time"] is not None
                else None
            ),
            "record_counts": self._counts(run_id, "record_type"),
            "category_counts": self._counts(run_id, "category"),
            "visibility_counts": self._counts(run_id, "visibility"),
            "schema_counts": self._counts(run_id, "schema_id"),
            "schema_version_counts": self._counts(run_id, "schema_version"),
            "capture_complete": (
                status in {"completed", "stopped"} and run_final_count > 0
            ),
            "capture_completeness": {
                "run_final_recorded": run_final_count > 0,
                "capture_failed": status == "capture_failed",
                "coverage_manifest_recorded": coverage_count > 0,
                "record_sequence_contiguous": sequence_gap_count == 0,
                "sequence_gap_count": sequence_gap_count,
            },
            "entity_counts": self._entity_counts(run_id),
            "status_counts": {
                "goals": self._table_counts(run_id, "goals", "status"),
                "decisions": self._table_counts(
                    run_id, "decisions", "status"
                ),
                "actions": self._table_counts(
                    run_id, "action_instances", "status"
                ),
                "interactions": self._table_counts(
                    run_id, "interactions", "status"
                ),
                "model_requests": self._table_counts(
                    run_id, "model_requests", "status"
                ),
                "tool_executions": self._table_counts(
                    run_id, "tool_executions", "status"
                ),
            },
            "terminal_outcome_counts": {
                "actions": self._table_counts(
                    run_id, "action_episodes", "terminal_status"
                ),
                "decisions": self._table_counts(
                    run_id, "decision_episodes", "status"
                ),
                "goals": self._table_counts(
                    run_id, "goal_episodes", "terminal_status"
                ),
                "interactions": self._table_counts(
                    run_id, "interaction_episodes", "status"
                ),
            },
            "derived_feature_counts": {
                table: self._table_count(run_id, table)
                for table in (
                    "transition_samples",
                    "action_episodes",
                    "decision_episodes",
                    "goal_episodes",
                    "interaction_episodes",
                    "opportunity_samples",
                    "population_samples",
                    "resource_samples",
                    "resource_flows",
                )
            },
            "feature_family_counts": {
                "actions": self._table_count(run_id, "action_episodes"),
                "decisions": self._table_count(run_id, "decision_episodes"),
                "goals": self._table_count(run_id, "goal_episodes"),
                "interactions": self._table_count(
                    run_id, "interaction_episodes"
                ),
                "transitions": self._table_count(
                    run_id, "transition_samples"
                ),
                "opportunities": self._table_count(
                    run_id, "opportunity_samples"
                ),
                "population": self._table_count(
                    run_id, "population_samples"
                ),
                "resources": (
                    self._table_count(run_id, "resource_samples")
                    + self._table_count(run_id, "resource_flows")
                ),
            },
        }

    def _counts(self, run_id: str, column: str) -> dict[str, JsonValue]:
        rows = self._connection.execute(
            f"""
            SELECT {column} AS value, COUNT(*) AS count
            FROM records WHERE run_id = ?
            GROUP BY {column} ORDER BY {column}
            """,
            (run_id,),
        )
        return {str(row["value"]): int(row["count"]) for row in rows}

    def _table_count(self, run_id: str, table: str) -> int:
        return int(
            self._connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )

    def _table_counts(
        self,
        run_id: str,
        table: str,
        column: str,
    ) -> dict[str, JsonValue]:
        rows = self._connection.execute(
            f"""
            SELECT {column} AS value, COUNT(*) AS count
            FROM {table} WHERE run_id = ?
            GROUP BY {column} ORDER BY {column}
            """,
            (run_id,),
        )
        return {str(row["value"]): int(row["count"]) for row in rows}

    def _entity_counts(self, run_id: str) -> dict[str, JsonValue]:
        counts: dict[str, int] = {}
        for row in self._connection.execute(
            """
            SELECT COALESCE(subject_id, agent_id) AS entity_id,
                   COUNT(*) AS count
            FROM records
            WHERE run_id = ? AND COALESCE(subject_id, agent_id) IS NOT NULL
            GROUP BY COALESCE(subject_id, agent_id)
            ORDER BY entity_id
            """,
            (run_id,),
        ):
            counts[str(row["entity_id"])] = int(row["count"])
        for row in self._connection.execute(
            """
            SELECT json_each.value AS entity_id, COUNT(*) AS count
            FROM records, json_each(records.related_entity_ids_json)
            WHERE records.run_id = ?
            GROUP BY json_each.value
            ORDER BY json_each.value
            """,
            (run_id,),
        ):
            entity_id = str(row["entity_id"])
            counts[entity_id] = counts.get(entity_id, 0) + int(row["count"])
        return dict(sorted(counts.items()))

    def _require_run(self, run_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown persisted run: {run_id}")
        return cast(sqlite3.Row, row)

    def _table_columns(self, table: str) -> frozenset[str]:
        return frozenset(
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        )

    def _table_primary_key(self, table: str) -> tuple[str, ...]:
        rows = self._connection.execute(f"PRAGMA table_info({table})")
        return tuple(
            str(row["name"])
            for row in sorted(
                (row for row in rows if int(row["pk"]) > 0),
                key=lambda row: int(row["pk"]),
            )
        )

    @staticmethod
    def _query_table_source(table: str) -> str:
        if table == "interaction_participants":
            return """
                FROM interaction_participants p
                JOIN interactions owner
                  ON owner.run_id = p.run_id
                 AND owner.interaction_id = p.interaction_id
                JOIN records r
                  ON r.run_id = owner.run_id
                 AND r.record_id = owner.record_id
            """
        return f"""
            FROM {table} p
            JOIN records r
              ON r.run_id = p.run_id
             AND r.record_id = p.record_id
        """

    def _write_table_csv(
        self,
        archive: zipfile.ZipFile,
        filename: str,
        run_id: str,
        table: str,
        filters: DatasetQueryFilter,
    ) -> None:
        columns = [
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        ]
        export_columns = [
            *columns,
            "record_sequence",
            "record_visibility",
            "record_simulation_tick",
            "record_simulation_time",
        ]
        with archive.open(_zip_info(filename), mode="w") as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            writer = csv.writer(text, lineterminator="\n")
            writer.writerow(export_columns)
            cursor: str | None = None
            while True:
                page = self.query_table(
                    run_id,
                    table,
                    replace(filters, cursor=cursor, limit=1000),
                )
                for row in page.rows:
                    writer.writerow(
                        [_csv_value(row.get(column)) for column in export_columns]
                    )
                text.flush()
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            text.detach()

    def rebuild_run_projections(self, run_id: str) -> dict[str, JsonValue]:
        """Transactionally rebuild analysis projections from immutable records."""
        if self._connection.in_transaction:
            raise ValueError(
                "dataset capture transaction is active; retry projection rebuild "
                "after the current tick boundary"
            )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self._connection.execute(
                "SELECT status FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown persisted run: {run_id}")
            if str(run["status"]) not in TERMINAL_RUN_STATUSES:
                raise ValueError(
                    f"projection rebuild requires a terminal run: {run_id}"
                )
            if self._foreign_owned_runs_with_active_leases((run_id,)):
                raise ValueError(
                    f"projection rebuild is blocked by an active foreign owner: {run_id}"
                )
        except Exception:
            self._connection.rollback()
            raise
        try:
            records = tuple(
                _record_from_row(row)
                for row in self._connection.execute(
                    "SELECT * FROM records WHERE run_id = ? ORDER BY sequence",
                    (run_id,),
                )
            )
            for table in (
                "interaction_events",
                "interaction_participants",
                "interaction_episodes",
                "interactions",
                "perception_deliveries",
                "perception_facts",
                "resource_flows",
                "resource_samples",
                "transition_samples",
                "population_samples",
                "opportunity_samples",
                "goal_episodes",
                "action_episodes",
                "decision_episodes",
                "memory_relations",
                "memory_operations",
                "information_retrievals",
            ):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE run_id = ?",
                    (run_id,),
                )
            for record in records:
                self._rebuild_projection(record)
        except Exception as error:
            self._connection.rollback()
            raise RuntimeError(
                f"projection rebuild failed for run {run_id}: {error}"
            ) from error
        self._commit()
        return {
            "run_id": run_id,
            "record_count": len(records),
            "derived_feature_counts": {
                table: self._table_count(run_id, table)
                for table in (
                    "transition_samples",
                    "action_episodes",
                    "decision_episodes",
                    "goal_episodes",
                    "interaction_episodes",
                    "opportunity_samples",
                    "population_samples",
                    "resource_samples",
                    "resource_flows",
                )
            },
        }

    def _rebuild_projection(self, record: DatasetRecord) -> None:
        payload = record.payload
        if record.record_type == "interaction_started":
            interaction_id = _text(payload, "interaction_id")
            self.append_interaction(
                run_id=record.run_id,
                interaction_id=interaction_id,
                record_id=record.record_id,
                interaction_type=_text(payload, "interaction_type"),
                start_tick=record.simulation_tick,
                end_tick=None,
                status=_text(payload, "status"),
                context=_object(payload, "context"),
            )
            for participant in _array(payload, "participants"):
                if not isinstance(participant, dict):
                    continue
                participant_id = participant.get("participant_id")
                role = participant.get("role")
                if isinstance(participant_id, str) and isinstance(role, str):
                    self.append_interaction_participant(
                        run_id=record.run_id,
                        interaction_id=interaction_id,
                        participant_id=participant_id,
                        role=role,
                        participant=participant,
                    )
        elif record.record_type == "interaction_event":
            event = _object(payload, "event")
            self.append_interaction_event(
                run_id=record.run_id,
                interaction_id=_text(payload, "interaction_id"),
                event_id=_text(event, "event_id"),
                record_id=record.record_id,
                event_index=_integer(payload, "event_index"),
                event_type=_text(event, "event_type"),
                simulation_tick=_integer(event, "simulation_tick"),
                event=event,
            )
        elif record.record_type == "interaction_episode":
            _rebuild_interaction_episode(self, record, payload)
        elif record.record_type == "perception_fact":
            _rebuild_perception_fact(self, record, payload)
        elif record.record_type == "perception_delivery":
            _rebuild_perception_delivery(self, record, payload)
        elif record.record_type == "transition_sample":
            _rebuild_transition_sample(self, record, payload)
        elif record.record_type == "opportunity_sample":
            context = _object(payload, "context")
            options = _array(payload, "options")
            self.append_opportunity_sample(
                run_id=record.run_id,
                opportunity_sample_id=f"{record.record_id}:opportunity",
                record_id=record.record_id,
                subject_id=record.subject_id,
                simulation_tick=record.simulation_tick,
                selected_option_id=_optional_text(payload, "selected_option_id"),
                context=context,
                options=options,
            )
        elif record.record_type == "population_sample":
            self.append_population_sample(
                run_id=record.run_id,
                population_sample_id=f"{record.record_id}:population",
                record_id=record.record_id,
                simulation_tick=record.simulation_tick,
                phase=record.phase,
                population=payload,
            )
        elif record.record_type == "resource_sample":
            _rebuild_resource_sample(self, record, payload)
        elif record.record_type == "resource_flow":
            _rebuild_resource_flow(self, record, payload)
        elif record.record_type == "action_episode":
            self.append_action_episode(
                run_id=record.run_id,
                action_id=_text(payload, "action_id"),
                record_id=record.record_id,
                subject_id=record.subject_id,
                terminal_status=_text(payload, "terminal_status"),
                created_tick=_integer(payload, "created_tick"),
                terminal_tick=_integer(payload, "terminal_tick"),
                created_at=_number(payload, "created_at"),
                terminal_at=_number(payload, "terminal_at"),
                elapsed_simulation_time=_number(
                    payload, "elapsed_simulation_time"
                ),
                source_event_ids=tuple(_string_array(payload, "source_event_ids")),
                episode=payload,
            )
        elif record.record_type == "goal_episode":
            self.append_goal_episode(
                run_id=record.run_id,
                goal_id=_text(payload, "goal_id"),
                record_id=record.record_id,
                subject_id=record.subject_id,
                terminal_status=_text(payload, "terminal_status"),
                activated_tick=_integer(payload, "activated_tick"),
                terminal_tick=_integer(payload, "terminal_tick"),
                activated_at=_number(payload, "activated_at"),
                terminal_at=_number(payload, "terminal_at"),
                duration=_number(payload, "duration"),
                episode=payload,
            )
        elif record.record_type == "decision_episode":
            self.append_decision_episode(
                run_id=record.run_id,
                decision_id=_text(payload, "decision_id"),
                record_id=record.record_id,
                subject_id=record.subject_id,
                action_id=_optional_text(payload, "action_id"),
                goal_id=_optional_text(payload, "goal_id"),
                tool_call_id=_optional_text(payload, "tool_call_id"),
                status=_text(payload, "status"),
                selected_option_id=_optional_text(
                    payload, "selected_option_id"
                ),
                requested_tick=_integer(payload, "requested_tick"),
                terminal_tick=_integer(payload, "terminal_tick"),
                requested_at=_number(payload, "requested_at"),
                terminal_at=_number(payload, "terminal_at"),
                terminal_reason=_optional_text(payload, "terminal_reason"),
                delays=_object(payload, "delays"),
                episode=payload,
            )
        elif record.record_type == "memory_relation":
            self.append_memory_relation(
                run_id=record.run_id,
                relation_id=_text(payload, "relation_id"),
                record_id=record.record_id,
                memory_id=_text(payload, "memory_id"),
                subject_id=record.subject_id,
                relation_type=_text(payload, "relation_type"),
                source_type=_text(payload, "source_type"),
                source_id=_text(payload, "source_id"),
                relation=payload,
            )
        elif (
            record.record_type.startswith("memory_")
            or record.record_type.startswith("embedding_")
            and record.category is RecordCategory.MEMORY
        ):
            operation_id = _optional_text(payload, "operation_id")
            if operation_id is not None:
                self.append_memory_operation(
                    run_id=record.run_id,
                    operation_id=operation_id,
                    record_id=record.record_id,
                    subject_id=record.subject_id,
                    operation_type=record.record_type,
                    status=_projection_trace_status(record),
                    memory_id=(
                        str(record.joins.memory_id)
                        if record.joins.memory_id is not None
                        else None
                    ),
                    request=(
                        payload
                        if record.record_type.endswith("_request")
                        else {}
                    ),
                    result=(
                        {}
                        if record.record_type.endswith("_request")
                        else payload
                    ),
                )
        elif (
            record.record_type.startswith("information_retrieval_")
            or record.record_type.startswith("embedding_")
            and record.category is RecordCategory.INFORMATION
        ):
            operation_id = _optional_text(payload, "operation_id")
            if operation_id is not None:
                self.append_information_retrieval(
                    run_id=record.run_id,
                    retrieval_id=operation_id,
                    record_id=record.record_id,
                    subject_id=record.subject_id,
                    status=_projection_trace_status(record),
                    query=(
                        payload
                        if record.record_type.endswith("_request")
                        else {}
                    ),
                    result=(
                        {}
                        if record.record_type.endswith("_request")
                        else payload
                    ),
                )

    def save_memory(self, run_id: str, record: MemoryRecord) -> None:
        self._insert_memory(run_id, record)
        self._commit()

    def _insert_memory(self, run_id: str, record: MemoryRecord) -> None:
        self._require_run_write_ownership(run_id)
        self._connection.execute(
            """
            INSERT OR REPLACE INTO episodic_memories (
                run_id, memory_id, agent_id, simulation_time, importance,
                text, embedding_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.id,
                record.agent_id,
                record.simulation_time,
                record.importance,
                record.text,
                _json(list(record.embedding)),
                _json(record.metadata),
            ),
        )

    def load_memories(self, run_id: str) -> tuple[MemoryRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM episodic_memories
            WHERE run_id = ?
            ORDER BY memory_id
            """,
            (run_id,),
        )
        return tuple(
            MemoryRecord(
                id=str(row["memory_id"]),
                agent_id=str(row["agent_id"]),
                text=str(row["text"]),
                simulation_time=float(row["simulation_time"]),
                importance=float(row["importance"]),
                embedding=tuple(
                    float(value)
                    for value in json.loads(str(row["embedding_json"]))
                ),
                metadata=json.loads(str(row["metadata_json"])),
            )
            for row in rows
        )

    def save_information_document(
        self,
        run_id: str,
        document: InformationDocument,
    ) -> None:
        self._insert_information_document(run_id, document)
        self._commit()

    def _insert_information_document(
        self,
        run_id: str,
        document: InformationDocument,
    ) -> None:
        self._require_run_write_ownership(run_id)
        self._connection.execute(
            """
            INSERT OR REPLACE INTO information_documents (
                run_id, document_id, revision, namespace_id, kind,
                content_hash, document_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                document.id,
                document.revision,
                document.namespace_id,
                document.kind,
                document.content_hash,
                _json(document.to_dict()),
            ),
        )

    def _require_run_write_ownership(self, run_id: str) -> None:
        started_transaction = not self._connection.in_transaction
        if started_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        row = self._connection.execute(
            """
            SELECT owner_instance_id, status
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            if started_transaction:
                self._connection.rollback()
            raise KeyError(f"unknown persisted run: {run_id}")
        status = str(row["status"])
        owner_instance_id = (
            str(row["owner_instance_id"])
            if row["owner_instance_id"] is not None
            else None
        )
        if status in TERMINAL_RUN_STATUSES:
            if started_transaction:
                self._connection.rollback()
            raise RuntimeError(
                f"run is no longer writable by this dataset-store instance: {run_id}"
            )
        if owner_instance_id != self.instance_id:
            lease_cutoff = (
                datetime.now(UTC)
                - timedelta(seconds=_INSTANCE_LEASE_TIMEOUT_SECONDS)
            ).isoformat()
            active_owner = (
                self._connection.execute(
                    """
                    SELECT 1 FROM dataset_store_instances
                    WHERE instance_id = ?
                      AND closed_at IS NULL
                      AND heartbeat_at >= ?
                    """,
                    (owner_instance_id, lease_cutoff),
                ).fetchone()
                if owner_instance_id is not None
                else None
            )
            if active_owner is not None:
                if started_transaction:
                    self._connection.rollback()
                raise RuntimeError(
                    f"run is owned by an active dataset-store instance: {run_id}"
                )
            cursor = self._connection.execute(
                """
                UPDATE runs SET owner_instance_id = ?
                WHERE run_id = ?
                  AND status = ?
                """,
                (self.instance_id, run_id, status),
            )
            if cursor.rowcount != 1:
                if started_transaction:
                    self._connection.rollback()
                raise RuntimeError(
                    f"run ownership could not be claimed safely: {run_id}"
                )

    def save_memory_episode(
        self,
        run_id: str,
        record: MemoryRecord,
        document: InformationDocument,
    ) -> None:
        savepoint = "memory_episode_write"
        self._connection.execute(f"SAVEPOINT {savepoint}")
        try:
            self._insert_memory(run_id, record)
            self._insert_information_document(run_id, document)
        except Exception:
            self._connection.execute(f"ROLLBACK TO {savepoint}")
            self._connection.execute(f"RELEASE {savepoint}")
            raise
        self._connection.execute(f"RELEASE {savepoint}")
        self._commit()

    def save_memory_binding(
        self,
        run_id: str,
        documents: tuple[InformationDocument, ...],
        episodes: tuple[tuple[MemoryRecord, InformationDocument], ...],
    ) -> None:
        savepoint = "memory_binding_write"
        self._connection.execute(f"SAVEPOINT {savepoint}")
        try:
            for document in documents:
                self._insert_information_document(run_id, document)
            for record, document in episodes:
                self._insert_memory(run_id, record)
                self._insert_information_document(run_id, document)
        except Exception:
            self._connection.execute(f"ROLLBACK TO {savepoint}")
            self._connection.execute(f"RELEASE {savepoint}")
            raise
        self._connection.execute(f"RELEASE {savepoint}")
        self._commit()

    def load_information_documents(
        self,
        run_id: str,
    ) -> tuple[InformationDocument, ...]:
        rows = self._connection.execute(
            """
            SELECT document_json FROM information_documents
            WHERE run_id = ?
            ORDER BY namespace_id, kind, document_id, revision
            """,
            (run_id,),
        )
        return tuple(
            information_document_from_dict(
                json.loads(str(row["document_json"]))
            )
            for row in rows
        )

    def close(self) -> None:
        self._heartbeat_stop.set()
        self._heartbeat_thread.join(timeout=3.0)
        self._connection.execute(
            """
            UPDATE dataset_store_instances
            SET heartbeat_at = ?, closed_at = ?
            WHERE instance_id = ?
            """,
            (
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                self.instance_id,
            ),
        )
        self._connection.commit()
        self._connection.close()

    def _migrate(self) -> None:
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current > _MAX_DATABASE_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than supported "
                f"schema {_MAX_DATABASE_SCHEMA_VERSION}"
            )
        if current < 1:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    dt REAL NOT NULL,
                    initial_speed REAL NOT NULL,
                    scenario_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    final_tick INTEGER,
                    final_simulation_time REAL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    simulation_tick INTEGER NOT NULL,
                    simulation_time REAL NOT NULL,
                    agent_id TEXT,
                    source_event_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS records_run_type_tick
                ON records(run_id, record_type, simulation_tick);
                CREATE INDEX IF NOT EXISTS records_run_agent_tick
                ON records(run_id, agent_id, simulation_tick);
                PRAGMA user_version = 1;
                """
            )
            self._connection.commit()
            current = 1
        if current < 2:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodic_memories (
                    run_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    simulation_time REAL NOT NULL,
                    importance REAL NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, memory_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS memories_run_agent_time
                ON episodic_memories(run_id, agent_id, simulation_time);
                PRAGMA user_version = 2;
                """
            )
            self._connection.commit()
            current = 2
        if current < 3:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS information_documents (
                    run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    namespace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, document_id, revision),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS information_run_namespace_kind
                ON information_documents(run_id, namespace_id, kind);
                PRAGMA user_version = 3;
                """
            )
            self._connection.commit()
            current = 3
        if current < 4:
            self._migrate_v4()
            current = 4
        if current < 5:
            self._ensure_v4_lineage_schema()
            self._connection.executescript(_V5_SCHEMA)
            self._connection.execute("PRAGMA user_version = 5")
            self._connection.commit()
            current = 5
        if current < 6:
            self._migrate_v6()
            self._connection.execute("PRAGMA user_version = 6")
            self._connection.commit()
            current = 6
        if current < 7:
            self._migrate_v7()
            self._connection.execute("PRAGMA user_version = 7")
            self._connection.commit()

    def _migrate_v6(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(runs)")
        }
        columns = {
            "owner_instance_id": "TEXT",
            "capture_complete": "INTEGER NOT NULL DEFAULT 0",
            "interruption_reason": "TEXT",
        }
        for name, declaration in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {name} {declaration}"
                )
        self._connection.execute(
            """
            UPDATE runs
            SET capture_complete = CASE
                WHEN status IN ('completed', 'stopped')
                 AND EXISTS (
                     SELECT 1 FROM records
                     WHERE records.run_id = runs.run_id
                       AND records.phase = ?
                 )
                THEN 1 ELSE 0 END
            """,
            (RunnerPhase.RUN_FINAL.value,),
        )
        self._connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS runs_status_started
            ON runs(status, started_at DESC, run_id DESC);
            CREATE INDEX IF NOT EXISTS runs_completed
            ON runs(completed_at DESC, run_id DESC);
            CREATE INDEX IF NOT EXISTS runs_schema_started
            ON runs(schema_version, started_at DESC, run_id DESC);
            CREATE INDEX IF NOT EXISTS runs_capture_started
            ON runs(capture_complete, started_at DESC, run_id DESC);
            """
        )

    def _migrate_v7(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dataset_store_instances (
                instance_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS dataset_store_instances_lease
            ON dataset_store_instances(closed_at, heartbeat_at);
            """
        )

    def _ensure_v4_lineage_schema(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(action_instances)"
            )
        }
        columns = {
            "origin": "TEXT NOT NULL DEFAULT 'scenario'",
            "plan_revision": "INTEGER",
            "created_tick": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "REAL NOT NULL DEFAULT 0",
            "root_correlation_id": "TEXT NOT NULL DEFAULT 'legacy'",
        }
        for name, declaration in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE action_instances ADD COLUMN {name} {declaration}"
                )
        self._connection.executescript(_V4_LINEAGE_SCHEMA)
        self._connection.commit()

    def _migrate_v4(self) -> None:
        columns = {
            "record_id": "TEXT",
            "schema_id": "TEXT NOT NULL DEFAULT 'stage0.record.legacy'",
            "category": "TEXT NOT NULL DEFAULT 'OTHER'",
            "source": "TEXT NOT NULL DEFAULT 'DATASET_COLLECTOR'",
            "phase": "TEXT NOT NULL DEFAULT 'unspecified'",
            "wall_time": "TEXT",
            "visibility": "TEXT NOT NULL DEFAULT 'OPERATOR'",
            "subject_id": "TEXT",
            "related_entity_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "causation_id": "TEXT",
            "correlation_id": "TEXT",
            "goal_id": "TEXT",
            "plan_id": "TEXT",
            "action_id": "TEXT",
            "decision_id": "TEXT",
            "model_request_id": "TEXT",
            "tool_call_id": "TEXT",
            "interaction_id": "TEXT",
            "perception_fact_id": "TEXT",
            "memory_id": "TEXT",
            "transaction_request_id": "TEXT",
            "operator_intervention_id": "TEXT",
            "source_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(records)")
        }
        for name, declaration in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE records ADD COLUMN {name} {declaration}"
                )
        self._connection.execute(
            """
            UPDATE records
            SET record_id = run_id || ':record:' || printf('%08d', sequence)
            WHERE record_id IS NULL OR record_id = ''
            """
        )
        self._connection.execute(
            """
            UPDATE records
            SET schema_id = 'stage0.record.' || record_type
            WHERE schema_id = 'stage0.record.legacy'
            """
        )
        self._connection.execute(
            """
            UPDATE records SET subject_id = agent_id
            WHERE subject_id IS NULL AND agent_id IS NOT NULL
            """
        )
        self._connection.executescript(_V4_SCHEMA)
        self._connection.execute("PRAGMA user_version = 4")
        self._connection.commit()


def _rebuild_interaction_episode(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    interaction_id = _text(payload, "interaction_id")
    interaction_type = _text(payload, "interaction_type")
    status = _text(payload, "terminal_status")
    context = _object(payload, "context")
    outcome = _object(payload, "outcome")
    store.append_interaction(
        run_id=record.run_id,
        interaction_id=interaction_id,
        record_id=record.record_id,
        interaction_type=interaction_type,
        start_tick=_integer(payload, "start_tick"),
        end_tick=_integer(payload, "terminal_tick"),
        status=status,
        context=context,
        outcome=outcome,
    )
    participants = _array(payload, "participants")
    for participant in participants:
        if not isinstance(participant, dict):
            raise ValueError("interaction participant must be an object")
        store.append_interaction_participant(
            run_id=record.run_id,
            interaction_id=interaction_id,
            participant_id=_text(participant, "participant_id"),
            role=_text(participant, "role"),
            participant=participant,
        )
    events = _array(payload, "constituent_events")
    for index, item in enumerate(events):
        if not isinstance(item, dict):
            raise ValueError("interaction event must be an object")
        store.append_interaction_event(
            run_id=record.run_id,
            interaction_id=interaction_id,
            event_id=_text(item, "event_id"),
            record_id=record.record_id,
            event_index=index,
            event_type=_text(item, "event_type"),
            simulation_tick=_integer(item, "simulation_tick"),
            event=item,
        )
    store.append_interaction_episode(
        run_id=record.run_id,
        interaction_id=interaction_id,
        record_id=record.record_id,
        interaction_type=interaction_type,
        status=status,
        start_tick=_integer(payload, "start_tick"),
        terminal_tick=_integer(payload, "terminal_tick"),
        started_at=_number(payload, "started_at"),
        terminal_at=_number(payload, "terminal_at"),
        duration=_number(payload, "duration"),
        initiating_goal_id=_optional_text(payload, "initiating_goal_id"),
        initiating_decision_id=_optional_text(payload, "initiating_decision_id"),
        initiating_action_id=_optional_text(payload, "initiating_action_id"),
        initiating_tool_call_id=_optional_text(
            payload, "initiating_tool_call_id"
        ),
        content_visibility=_text(payload, "content_visibility"),
        episode=payload,
    )


def _rebuild_perception_fact(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    fact = _object(payload, "fact")
    store.append_perception_fact(
        run_id=record.run_id,
        fact_id=_text(fact, "fact_id"),
        record_id=record.record_id,
        source_event_id=_optional_text(fact, "event_id"),
        fact_type=_text(fact, "fact_type"),
        subject_id=_optional_text(fact, "subject_id"),
        object_id=_optional_text(fact, "object_id"),
        location_id=_optional_text(fact, "location_id"),
        modality=_text(fact, "modality"),
        disclosure=_text(fact, "disclosure"),
        created_tick=_integer(fact, "tick"),
        fact=fact,
    )


def _rebuild_perception_delivery(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    salience = payload.get("salience")
    if salience is not None and (
        not isinstance(salience, int | float) or isinstance(salience, bool)
    ):
        raise ValueError("perception delivery salience must be numeric")
    store.append_perception_delivery(
        run_id=record.run_id,
        delivery_id=_text(payload, "delivery_id"),
        fact_id=_text(payload, "fact_id"),
        record_id=record.record_id,
        observer_id=_text(payload, "observer_id"),
        status=_text(payload, "status"),
        reason=_optional_text(payload, "reason"),
        perceived_tick=_integer(payload, "perceived_tick"),
        fact_age=_number(payload, "fact_age"),
        salience=float(salience) if salience is not None else None,
        delivery=payload,
    )


def _rebuild_transition_sample(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    store.append_transition_sample(
        run_id=record.run_id,
        transition_sample_id=f"{record.record_id}:transition",
        record_id=record.record_id,
        subject_id=record.subject_id,
        action_id=_optional_text(payload, "action_id"),
        start_tick=_integer(payload, "start_tick"),
        end_tick=_integer(payload, "end_tick"),
        elapsed_simulation_time=_number(payload, "dt"),
        outcome=_text(payload, "outcome"),
        state_before=_object(payload, "state_before"),
        action=_object(payload, "action_context"),
        exogenous_context=_object(payload, "exogenous_context"),
        state_after=_object(payload, "state_after"),
    )


def _rebuild_resource_sample(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    capacity = payload.get("capacity")
    if capacity is not None and (
        not isinstance(capacity, int) or isinstance(capacity, bool)
    ):
        raise ValueError("resource capacity must be an integer")
    utilization = payload.get("utilization")
    if utilization is not None and (
        not isinstance(utilization, int | float)
        or isinstance(utilization, bool)
    ):
        raise ValueError("resource utilization must be numeric")
    store.append_resource_sample(
        run_id=record.run_id,
        resource_sample_id=f"{record.record_id}:resource",
        record_id=record.record_id,
        resource_id=_text(payload, "resource_id"),
        resource_type=_text(payload, "resource_type"),
        simulation_tick=record.simulation_tick,
        phase=record.phase,
        capacity=capacity,
        occupancy=_integer(payload, "occupancy"),
        queue_length=_integer(payload, "queue_length"),
        utilization=(
            float(utilization) if utilization is not None else None
        ),
        sample=payload,
    )


def _rebuild_resource_flow(
    store: SQLiteDatasetStore,
    record: DatasetRecord,
    payload: dict[str, JsonValue],
) -> None:
    amount = payload.get("amount")
    if amount is not None and (
        not isinstance(amount, int | float) or isinstance(amount, bool)
    ):
        raise ValueError("resource flow amount must be numeric")
    store.append_resource_flow(
        run_id=record.run_id,
        resource_flow_id=f"{record.record_id}:flow",
        record_id=record.record_id,
        resource_id=_text(payload, "resource_id"),
        subject_id=record.subject_id,
        simulation_tick=record.simulation_tick,
        flow_type=_text(payload, "flow_type"),
        amount=float(amount) if amount is not None else None,
        flow=payload,
    )


def _projection_trace_status(record: DatasetRecord) -> str:
    status = record.payload.get("status")
    if isinstance(status, str):
        return status
    if record.record_type.endswith("_request"):
        return "requested"
    if record.record_type.endswith(("_result", "_completed")):
        return "completed"
    if record.record_type.endswith(("_error", "_failed")):
        return "failed"
    return "recorded"


def _object(
    payload: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(payload: dict[str, JsonValue], name: str) -> list[JsonValue]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _text(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_text(
    payload: dict[str, JsonValue],
    name: str,
) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _integer(payload: dict[str, JsonValue], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _number(payload: dict[str, JsonValue], name: str) -> float:
    value = payload.get(name)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    return float(value)


def _string_array(
    payload: dict[str, JsonValue],
    name: str,
) -> list[str]:
    value = payload.get(name)
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a string array")
    return [str(item) for item in value]


def _encode_cursor(values: list[JsonValue]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, expected_length: int) -> list[object]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("ascii")
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid dataset cursor") from error
    if not isinstance(value, list) or len(value) != expected_length:
        raise ValueError("invalid dataset cursor")
    if any(not isinstance(item, str | int | float) for item in value):
        raise ValueError("invalid dataset cursor")
    return value


def _json_safe_row(row: sqlite3.Row) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for name in row.keys():  # noqa: SIM118
        value = row[name]
        if name.endswith("_json"):
            result[name.removesuffix("_json")] = json.loads(str(value))
        elif value is None or isinstance(value, bool | int | float | str):
            result[name] = value
        else:
            result[name] = str(value)
    return result


def _record_filter_from_query(query: DatasetQueryFilter) -> DatasetRecordFilter:
    return DatasetRecordFilter(
        record_type=query.record_type,
        category=query.category,
        schema_id=query.schema_id,
        schema_version=query.schema_version,
        subject_id=query.primary_entity_id,
        related_entity_id=query.related_entity_id,
        minimum_tick=query.minimum_tick,
        maximum_tick=query.maximum_tick,
        minimum_time=query.minimum_time,
        maximum_time=query.maximum_time,
        visibility=query.visibility,
        goal_id=query.goal_id,
        plan_id=query.plan_id,
        action_id=query.action_id,
        decision_id=query.decision_id,
        model_request_id=query.model_request_id,
        tool_call_id=query.tool_call_id,
        interaction_id=query.interaction_id,
        perception_fact_id=query.perception_fact_id,
        memory_id=query.memory_id,
        transaction_request_id=query.transaction_request_id,
        operator_intervention_id=query.operator_intervention_id,
        status=query.status,
        outcome=query.outcome,
        include_private=query.include_private,
        limit=query.limit,
    )


def _query_filter_payload(query: DatasetQueryFilter) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for contract_field in fields(query):
        value = getattr(query, contract_field.name)
        if isinstance(value, RecordCategory | RecordVisibility):
            result[contract_field.name] = value.value
        else:
            result[contract_field.name] = value
    return result


def _zip_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _safe_filename(value: str) -> str:
    safe = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "._-")
        else "_"
        for character in value
    )
    return safe or "data"


def _csv_value(value: JsonValue | None) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def _field_meaning(name: str) -> str:
    meanings = {
        "record_id": "stable immutable record identifier within a run",
        "run_id": "simulation run identifier",
        "sequence": "monotonic stable raw-record order within the run",
        "schema_id": "record payload contract identifier",
        "schema_version": "version of the named record contract",
        "record_type": "capture record kind",
        "category": "high-level capture taxonomy category",
        "source": "subsystem that created the record",
        "phase": "runner boundary at which the record was captured",
        "simulation_tick": "deterministic fixed-clock tick",
        "simulation_time": "deterministic fixed-clock time",
        "wall_time": "nondeterministic diagnostic wall-clock timestamp",
        "visibility": "disclosure class enforced by query and export defaults",
        "subject_id": "primary entity described by the row",
        "agent_id": "legacy alias for the primary character entity",
        "related_entity_ids": "ordered secondary entities related to the record",
        "payload": "complete forward-compatible record content",
        "record_sequence": "sequence of the linked immutable raw record",
        "record_visibility": "visibility of the linked immutable raw record",
    }
    if name in meanings:
        return meanings[name]
    if name.endswith("_id"):
        return f"stable identifier for {name.removesuffix('_id').replace('_', ' ')}"
    if name.endswith("_json"):
        return f"canonical JSON encoding of {name.removesuffix('_json').replace('_', ' ')}"
    if name.endswith("_tick"):
        return f"deterministic tick for {name.removesuffix('_tick').replace('_', ' ')}"
    if name.endswith("_at"):
        return f"simulation time for {name.removesuffix('_at').replace('_', ' ')}"
    return name.replace("_", " ")


def _table_meaning(table: str) -> str:
    if table.endswith("_episodes"):
        return f"derived terminal {table.removesuffix('_episodes').replace('_', ' ')} episodes"
    if table.endswith("_samples"):
        return f"analysis-ready {table.removesuffix('_samples').replace('_', ' ')} samples"
    if table.endswith("_transitions"):
        subject = table.removesuffix("_transitions").replace("_", " ")
        return f"ordered {subject} lifecycle transitions"
    return table.replace("_", " ")


def _nondeterministic_field(name: str) -> bool:
    return name in {"wall_time", "provider_request_id", "latency_ms"}


def _record_from_row(row: sqlite3.Row) -> DatasetRecord:
    related = json.loads(str(row["related_entity_ids_json"]))
    source_metadata = json.loads(str(row["source_metadata_json"]))
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(related, list) or not all(
        isinstance(value, str) for value in related
    ):
        raise ValueError("stored related_entity_ids_json is invalid")
    if not isinstance(source_metadata, dict):
        raise ValueError("stored source_metadata_json is invalid")
    if not isinstance(payload, dict):
        raise ValueError("stored payload_json is invalid")
    return DatasetRecord(
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        record_type=str(row["record_type"]),
        simulation_tick=int(row["simulation_tick"]),
        simulation_time=float(row["simulation_time"]),
        agent_id=_row_str(row, "agent_id"),
        payload=payload,
        source_event_id=_row_str(row, "source_event_id"),
        schema_version=str(row["schema_version"]),
        record_id=str(row["record_id"]),
        schema_id=str(row["schema_id"]),
        category=RecordCategory(str(row["category"])),
        source=RecordSource(str(row["source"])),
        phase=RunnerPhase(str(row["phase"])),
        wall_time=_row_str(row, "wall_time"),
        visibility=RecordVisibility(str(row["visibility"])),
        subject_id=_row_str(row, "subject_id"),
        related_entity_ids=tuple(related),
        causation_id=_row_str(row, "causation_id"),
        correlation_id=_row_str(row, "correlation_id"),
        joins=RecordJoinIds.from_dict(
            {
                "goal_id": _row_str(row, "goal_id"),
                "plan_id": _row_str(row, "plan_id"),
                "action_id": _row_str(row, "action_id"),
                "decision_id": _row_str(row, "decision_id"),
                "model_request_id": _row_str(row, "model_request_id"),
                "tool_call_id": _row_str(row, "tool_call_id"),
                "interaction_id": _row_str(row, "interaction_id"),
                "perception_fact_id": _row_str(row, "perception_fact_id"),
                "memory_id": _row_str(row, "memory_id"),
                "transaction_request_id": _row_str(
                    row,
                    "transaction_request_id",
                ),
                "operator_intervention_id": _row_str(
                    row,
                    "operator_intervention_id",
                ),
            }
        ),
        source_metadata=source_metadata,
    )


def _row_str(row: sqlite3.Row, name: str) -> str | None:
    value = row[name]
    return str(value) if value is not None else None


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _like_pattern(value: str) -> str:
    escaped = value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _database_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _world_schema_version(
    scenario_schema: object,
    world_type: object,
    has_world: bool,
) -> str:
    version = (
        str(scenario_schema)
        if isinstance(scenario_schema, int | str)
        and not isinstance(scenario_schema, bool)
        else "unknown"
    )
    if not has_world:
        return f"none-v{version}"
    if world_type == "city":
        return f"city-v{version}"
    return f"grid-v{version}"


def _first_text(*values: object) -> str | None:
    return next((value for value in values if isinstance(value, str)), None)


_V4_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS records_run_record_id
ON records(run_id, record_id);
CREATE INDEX IF NOT EXISTS records_run_category_tick
ON records(run_id, category, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_schema_tick
ON records(run_id, schema_id, schema_version, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_subject_tick
ON records(run_id, subject_id, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_visibility_sequence
ON records(run_id, visibility, sequence);

CREATE TABLE IF NOT EXISTS record_relations (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (
        run_id, record_id, relation_type, target_type, target_id, ordinal
    ),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS record_relations_target
ON record_relations(run_id, target_type, target_id, relation_type);

CREATE TABLE IF NOT EXISTS state_samples (
    run_id TEXT NOT NULL,
    state_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    phase TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    simulation_time REAL NOT NULL,
    state_json TEXT NOT NULL,
    PRIMARY KEY (run_id, state_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS state_samples_subject_tick
ON state_samples(run_id, subject_id, simulation_tick, phase);

CREATE TABLE IF NOT EXISTS state_deltas (
    run_id TEXT NOT NULL,
    state_delta_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    from_sample_id TEXT,
    to_sample_id TEXT,
    simulation_tick INTEGER NOT NULL,
    delta_json TEXT NOT NULL,
    PRIMARY KEY (run_id, state_delta_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS state_deltas_subject_tick
ON state_deltas(run_id, subject_id, simulation_tick);

CREATE TABLE IF NOT EXISTS goals (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    goal_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goals_subject_status
ON goals(run_id, subject_id, status);

CREATE TABLE IF NOT EXISTS goal_transitions (
    run_id TEXT NOT NULL,
    goal_transition_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    transition_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_transition_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, goal_id) REFERENCES goals(run_id, goal_id)
);
CREATE INDEX IF NOT EXISTS goal_transitions_goal_tick
ON goal_transitions(run_id, goal_id, simulation_tick);

CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    status TEXT NOT NULL,
    selected_option_id TEXT,
    context_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS decisions_subject_tick
ON decisions(run_id, subject_id, simulation_tick, status);

CREATE TABLE IF NOT EXISTS decision_options (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    option_index INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    selected INTEGER NOT NULL,
    option_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id, option_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, decision_id) REFERENCES decisions(run_id, decision_id)
);
CREATE INDEX IF NOT EXISTS decision_options_selected
ON decision_options(run_id, decision_id, selected, option_index);

CREATE TABLE IF NOT EXISTS model_requests (
    run_id TEXT NOT NULL,
    model_request_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    decision_id TEXT,
    subject_id TEXT,
    operation TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (run_id, model_request_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS model_requests_subject_status
ON model_requests(run_id, subject_id, operation, status);

CREATE TABLE IF NOT EXISTS model_turns (
    run_id TEXT NOT NULL,
    model_request_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    record_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    PRIMARY KEY (run_id, model_request_id, turn_index),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, model_request_id)
        REFERENCES model_requests(run_id, model_request_id)
);

CREATE TABLE IF NOT EXISTS tool_executions (
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    decision_id TEXT,
    action_id TEXT,
    subject_id TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    PRIMARY KEY (run_id, tool_call_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS tool_executions_subject_status
ON tool_executions(run_id, subject_id, tool_name, status);

CREATE TABLE IF NOT EXISTS action_instances (
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    plan_id TEXT,
    goal_id TEXT,
    decision_id TEXT,
    tool_call_id TEXT,
    subject_id TEXT,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'scenario',
    plan_revision INTEGER,
    created_tick INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    root_correlation_id TEXT NOT NULL DEFAULT 'legacy',
    action_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS action_instances_subject_status
ON action_instances(run_id, subject_id, action_type, status);

CREATE TABLE IF NOT EXISTS action_transitions (
    run_id TEXT NOT NULL,
    action_transition_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    transition_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_transition_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id)
);
CREATE INDEX IF NOT EXISTS action_transitions_action_tick
ON action_transitions(run_id, action_id, simulation_tick);

CREATE TABLE IF NOT EXISTS interactions (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER,
    status TEXT NOT NULL,
    context_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS interactions_type_status
ON interactions(run_id, interaction_type, status, start_tick);

CREATE TABLE IF NOT EXISTS interaction_participants (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    role TEXT NOT NULL,
    participant_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id, participant_id, role),
    FOREIGN KEY (run_id, interaction_id)
        REFERENCES interactions(run_id, interaction_id)
);
CREATE INDEX IF NOT EXISTS interaction_participants_participant
ON interaction_participants(run_id, participant_id, interaction_id);

CREATE TABLE IF NOT EXISTS interaction_events (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id, event_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, interaction_id)
        REFERENCES interactions(run_id, interaction_id)
);
CREATE INDEX IF NOT EXISTS interaction_events_tick
ON interaction_events(run_id, interaction_id, simulation_tick, event_index);

CREATE TABLE IF NOT EXISTS opportunity_samples (
    run_id TEXT NOT NULL,
    opportunity_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    selected_option_id TEXT,
    context_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    PRIMARY KEY (run_id, opportunity_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS opportunity_samples_subject_tick
ON opportunity_samples(run_id, subject_id, simulation_tick);

CREATE TABLE IF NOT EXISTS transition_samples (
    run_id TEXT NOT NULL,
    transition_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    action_id TEXT,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    elapsed_simulation_time REAL NOT NULL,
    outcome TEXT NOT NULL,
    state_before_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    exogenous_context_json TEXT NOT NULL,
    state_after_json TEXT NOT NULL,
    PRIMARY KEY (run_id, transition_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS transition_samples_subject_outcome
ON transition_samples(run_id, subject_id, outcome, start_tick);

CREATE TABLE IF NOT EXISTS population_samples (
    run_id TEXT NOT NULL,
    population_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    population_json TEXT NOT NULL,
    PRIMARY KEY (run_id, population_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS population_samples_tick_phase
ON population_samples(run_id, simulation_tick, phase);
"""


_V4_LINEAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    run_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    revision INTEGER NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    root_correlation_id TEXT,
    plan_json TEXT NOT NULL,
    PRIMARY KEY (run_id, plan_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS plans_subject_status
ON plans(run_id, subject_id, status, revision);

CREATE TABLE IF NOT EXISTS goal_action_links (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    link_kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (run_id, goal_id, action_id),
    FOREIGN KEY (run_id, goal_id) REFERENCES goals(run_id, goal_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goal_action_links_action
ON goal_action_links(run_id, action_id, link_kind, ordinal);

CREATE TABLE IF NOT EXISTS action_episodes (
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    terminal_status TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    created_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    elapsed_simulation_time REAL NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS action_episodes_subject_status
ON action_episodes(run_id, subject_id, terminal_status, terminal_tick);
"""


_V5_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_episodes (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    action_id TEXT,
    goal_id TEXT,
    tool_call_id TEXT,
    status TEXT NOT NULL,
    selected_option_id TEXT,
    requested_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    requested_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    terminal_reason TEXT,
    delays_json TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS decision_episodes_subject_status
ON decision_episodes(run_id, subject_id, status, requested_tick);

CREATE TABLE IF NOT EXISTS memory_operations (
    run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    memory_id TEXT,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, operation_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS memory_operations_subject_status
ON memory_operations(run_id, subject_id, operation_type, status);

CREATE TABLE IF NOT EXISTS information_retrievals (
    run_id TEXT NOT NULL,
    retrieval_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    status TEXT NOT NULL,
    query_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, retrieval_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS information_retrievals_subject_status
ON information_retrievals(run_id, subject_id, status);

CREATE TABLE IF NOT EXISTS interaction_episodes (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    status TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    started_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    duration REAL NOT NULL,
    initiating_goal_id TEXT,
    initiating_decision_id TEXT,
    initiating_action_id TEXT,
    initiating_tool_call_id TEXT,
    content_visibility TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS interaction_episodes_type_status
ON interaction_episodes(run_id, interaction_type, status, start_tick);

CREATE TABLE IF NOT EXISTS perception_facts (
    run_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    source_event_id TEXT,
    fact_type TEXT NOT NULL,
    subject_id TEXT,
    object_id TEXT,
    location_id TEXT,
    modality TEXT NOT NULL,
    disclosure TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    fact_json TEXT NOT NULL,
    PRIMARY KEY (run_id, fact_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS perception_facts_subject_tick
ON perception_facts(run_id, subject_id, created_tick, fact_type);

CREATE TABLE IF NOT EXISTS perception_deliveries (
    run_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    observer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    perceived_tick INTEGER NOT NULL,
    fact_age REAL NOT NULL,
    salience REAL,
    delivery_json TEXT NOT NULL,
    PRIMARY KEY (run_id, delivery_id),
    FOREIGN KEY (run_id, fact_id) REFERENCES perception_facts(run_id, fact_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS perception_deliveries_observer_tick
ON perception_deliveries(run_id, observer_id, perceived_tick, status);

CREATE TABLE IF NOT EXISTS goal_episodes (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    terminal_status TEXT NOT NULL,
    activated_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    activated_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    duration REAL NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goal_episodes_subject_status
ON goal_episodes(run_id, subject_id, terminal_status, activated_tick);

CREATE TABLE IF NOT EXISTS resource_samples (
    run_id TEXT NOT NULL,
    resource_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    capacity INTEGER,
    occupancy INTEGER NOT NULL,
    queue_length INTEGER NOT NULL,
    utilization REAL,
    sample_json TEXT NOT NULL,
    PRIMARY KEY (run_id, resource_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS resource_samples_resource_tick
ON resource_samples(run_id, resource_id, simulation_tick, phase);

CREATE TABLE IF NOT EXISTS resource_flows (
    run_id TEXT NOT NULL,
    resource_flow_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    flow_type TEXT NOT NULL,
    amount REAL,
    flow_json TEXT NOT NULL,
    PRIMARY KEY (run_id, resource_flow_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS resource_flows_resource_tick
ON resource_flows(run_id, resource_id, simulation_tick, flow_type);

CREATE TABLE IF NOT EXISTS memory_relations (
    run_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    subject_id TEXT,
    relation_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relation_json TEXT NOT NULL,
    PRIMARY KEY (run_id, relation_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS memory_relations_memory_type
ON memory_relations(run_id, memory_id, relation_type);
"""
