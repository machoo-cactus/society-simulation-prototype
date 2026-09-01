from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol, TextIO

from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetQueryPage,
    DatasetRecordFilter,
    DatasetRecordPage,
)
from stage0_sim.domain.events import JsonValue

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "stopped", "failed", "capture_failed", "interrupted"}
)


@dataclass(frozen=True, slots=True)
class LiveRunOverlay:
    status: str
    cognition_phase: str
    deletion_ready: bool


@dataclass(frozen=True, slots=True)
class PersistedRunFilter:
    search_text: str | None = None
    persisted_statuses: tuple[str, ...] = ()
    effective_statuses: tuple[str, ...] = ()
    scenario_name: str | None = None
    dataset_schema_version: str | None = None
    capture_complete: bool | None = None
    started_at_or_after: datetime | None = None
    started_before: datetime | None = None
    completed_at_or_after: datetime | None = None
    completed_before: datetime | None = None
    cursor: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 500:
            raise ValueError("run catalog limit must be between 1 and 500")
        if (
            self.started_at_or_after is not None
            and self.started_before is not None
            and self.started_at_or_after >= self.started_before
        ):
            raise ValueError("started_at_or_after must precede started_before")
        if (
            self.completed_at_or_after is not None
            and self.completed_before is not None
            and self.completed_at_or_after >= self.completed_before
        ):
            raise ValueError("completed_at_or_after must precede completed_before")

    def without_page(self) -> PersistedRunFilter:
        return replace(self, cursor=None, limit=500)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "search_text": self.search_text,
            "persisted_statuses": list(self.persisted_statuses),
            "effective_statuses": list(self.effective_statuses),
            "scenario_name": self.scenario_name,
            "dataset_schema_version": self.dataset_schema_version,
            "capture_complete": self.capture_complete,
            "started_at_or_after": _datetime_text(self.started_at_or_after),
            "started_before": _datetime_text(self.started_before),
            "completed_at_or_after": _datetime_text(self.completed_at_or_after),
            "completed_before": _datetime_text(self.completed_before),
        }


@dataclass(frozen=True, slots=True)
class PersistedRunSummary:
    run_id: str
    persisted_status: str
    effective_status: str
    live: bool
    live_cognition_phase: str | None
    deletion_ready: bool
    scenario_identity: str
    scenario_name: str
    dataset_schema_version: str
    world_schema_version: str
    capture_configuration: Mapping[str, JsonValue]
    capture_complete: bool
    record_count: int
    final_tick: int | None
    final_simulation_time: float | None
    seed: int
    dt: float
    initial_speed: float
    started_at: datetime
    completed_at: datetime | None
    interruption_reason: str | None
    cognition_execution_mode: str | None
    requested_npc_control_mode: str | None
    effective_npc_control_mode: str | None
    feature_schema_versions: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "persisted_status": self.persisted_status,
            "effective_status": self.effective_status,
            "live": self.live,
            "live_cognition_phase": self.live_cognition_phase,
            "deletion_ready": self.deletion_ready,
            "scenario_identity": self.scenario_identity,
            "scenario_name": self.scenario_name,
            "dataset_schema_version": self.dataset_schema_version,
            "world_schema_version": self.world_schema_version,
            "capture_configuration": dict(
                sorted(self.capture_configuration.items())
            ),
            "capture_complete": self.capture_complete,
            "record_count": self.record_count,
            "final_tick": self.final_tick,
            "final_simulation_time": self.final_simulation_time,
            "seed": self.seed,
            "dt": self.dt,
            "initial_speed": self.initial_speed,
            "started_at": self.started_at.isoformat(),
            "completed_at": _datetime_text(self.completed_at),
            "interruption_reason": self.interruption_reason,
            "cognition_execution_mode": self.cognition_execution_mode,
            "requested_npc_control_mode": self.requested_npc_control_mode,
            "effective_npc_control_mode": self.effective_npc_control_mode,
            "feature_schema_versions": dict(
                sorted(self.feature_schema_versions.items())
            ),
        }


@dataclass(frozen=True, slots=True)
class PersistedRunPage:
    runs: tuple[PersistedRunSummary, ...]
    next_cursor: str | None
    total_count: int
    filters: PersistedRunFilter

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "runs": [run.to_dict() for run in self.runs],
            "next_cursor": self.next_cursor,
            "total_count": self.total_count,
            "filters": self.filters.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RunSelection:
    run_ids: tuple[str, ...]
    fingerprint: str
    filters: PersistedRunFilter | None = None

    @classmethod
    def create(
        cls,
        run_ids: Sequence[str],
        filters: PersistedRunFilter | None = None,
    ) -> RunSelection:
        normalized = _normalize_run_ids(run_ids)
        if not normalized:
            raise ValueError("at least one run must be selected")
        return cls(
            run_ids=normalized,
            fingerprint=selection_fingerprint(normalized, filters),
            filters=filters,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_ids": list(self.run_ids),
            "fingerprint": self.fingerprint,
            "filters": self.filters.to_dict() if self.filters is not None else None,
        }


@dataclass(frozen=True, slots=True)
class RunCompatibilityGroup:
    group_id: str
    run_ids: tuple[str, ...]
    dataset_schema_version: str
    scenario_identity: str
    scenario_name: str
    world_schema_version: str
    capture_configuration: Mapping[str, JsonValue]
    capture_complete: bool
    cognition_execution_mode: str | None
    requested_npc_control_mode: str | None
    effective_npc_control_mode: str | None
    feature_schema_versions: Mapping[str, str]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "group_id": self.group_id,
            "run_ids": list(self.run_ids),
            "dataset_schema_version": self.dataset_schema_version,
            "scenario_identity": self.scenario_identity,
            "scenario_name": self.scenario_name,
            "world_schema_version": self.world_schema_version,
            "capture_configuration": dict(
                sorted(self.capture_configuration.items())
            ),
            "capture_complete": self.capture_complete,
            "cognition_execution_mode": self.cognition_execution_mode,
            "requested_npc_control_mode": self.requested_npc_control_mode,
            "effective_npc_control_mode": self.effective_npc_control_mode,
            "feature_schema_versions": dict(
                sorted(self.feature_schema_versions.items())
            ),
        }


@dataclass(frozen=True, slots=True)
class AggregateStatistics:
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "median": self.median,
        }


@dataclass(frozen=True, slots=True)
class RunMetricValue:
    run_id: str
    statistics: AggregateStatistics

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "statistics": self.statistics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    name: str
    unit: str | None
    pooled: AggregateStatistics
    macro_per_run: AggregateStatistics
    per_run: tuple[RunMetricValue, ...]
    pooled_weighting: str = "each observation has equal weight"
    macro_weighting: str = "each run mean has equal weight"

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "unit": self.unit,
            "pooled": self.pooled.to_dict(),
            "macro_per_run": self.macro_per_run.to_dict(),
            "per_run": [value.to_dict() for value in self.per_run],
            "pooled_weighting": self.pooled_weighting,
            "macro_weighting": self.macro_weighting,
        }


@dataclass(frozen=True, slots=True)
class AggregateDatasetSummary:
    selection: RunSelection
    include_private_derived: bool
    private_derived_warning: str | None
    compatibility_groups: tuple[RunCompatibilityGroup, ...]
    compatibility_warnings: tuple[str, ...]
    weighting_definitions: Mapping[str, str]
    per_run: tuple[PersistedRunSummary, ...]
    distributions: Mapping[str, Mapping[str, int]]
    metrics: tuple[AggregateMetric, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "selection": self.selection.to_dict(),
            "include_private_derived": self.include_private_derived,
            "private_derived_warning": self.private_derived_warning,
            "compatibility_groups": [
                group.to_dict() for group in self.compatibility_groups
            ],
            "compatibility_warnings": list(self.compatibility_warnings),
            "weighting_definitions": dict(
                sorted(self.weighting_definitions.items())
            ),
            "per_run": [run.to_dict() for run in self.per_run],
            "distributions": {
                name: dict(sorted(values.items()))
                for name, values in sorted(self.distributions.items())
            },
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


@dataclass(frozen=True, slots=True)
class RunDeletionPreview:
    selection: RunSelection
    runs: tuple[PersistedRunSummary, ...]
    table_counts: Mapping[str, int]
    total_records: int
    eligible: bool
    ineligible_run_ids: tuple[str, ...]
    confirmation_token: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "selection": self.selection.to_dict(),
            "runs": [run.to_dict() for run in self.runs],
            "table_counts": dict(sorted(self.table_counts.items())),
            "total_records": self.total_records,
            "eligible": self.eligible,
            "ineligible_run_ids": list(self.ineligible_run_ids),
            "confirmation_token": self.confirmation_token,
        }


@dataclass(frozen=True, slots=True)
class RunDeletionResult:
    run_ids: tuple[str, ...]
    deleted_table_counts: Mapping[str, int]
    deleted_record_count: int
    confirmation_token: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_ids": list(self.run_ids),
            "deleted_table_counts": dict(sorted(self.deleted_table_counts.items())),
            "deleted_record_count": self.deleted_record_count,
            "confirmation_token": self.confirmation_token,
        }


class DatasetManagementRepository(Protocol):
    def list_persisted_runs(
        self,
        filters: PersistedRunFilter,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> PersistedRunPage: ...

    def get_persisted_runs(
        self,
        run_ids: Sequence[str],
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> tuple[PersistedRunSummary, ...]: ...

    def reconcile_incomplete_runs(self) -> tuple[str, ...]: ...

    def query_records(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> DatasetRecordPage: ...

    def query_table(
        self,
        run_id: str,
        table_or_alias: str,
        filters: DatasetQueryFilter | None = None,
    ) -> DatasetQueryPage: ...

    def deletion_preview(
        self,
        selection: RunSelection,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> RunDeletionPreview: ...

    def delete_runs(
        self,
        selection: RunSelection,
        confirmation_token: str,
        effective_statuses: Mapping[str, LiveRunOverlay | str] | None = None,
    ) -> RunDeletionResult: ...


class DatasetManagementService:
    """Provider-neutral persisted-run catalog and aggregate management boundary."""

    def __init__(
        self,
        repository: DatasetManagementRepository,
        live_status_provider: (
            Callable[[], Mapping[str, LiveRunOverlay | str]] | None
        ) = None,
    ) -> None:
        self._repository = repository
        self._live_status_provider = live_status_provider or (lambda: {})

    def reconcile_prior_runs(self) -> tuple[str, ...]:
        return self._repository.reconcile_incomplete_runs()

    def catalog(
        self,
        filters: PersistedRunFilter | None = None,
    ) -> PersistedRunPage:
        self._repository.reconcile_incomplete_runs()
        return self._repository.list_persisted_runs(
            filters or PersistedRunFilter(),
            self._live_status_provider(),
        )

    def selection(
        self,
        run_ids: Sequence[str],
        filters: PersistedRunFilter | None = None,
    ) -> RunSelection:
        selection = RunSelection.create(run_ids, filters)
        self._require_exact_runs(selection.run_ids)
        return selection

    def select_all(self, filters: PersistedRunFilter) -> RunSelection:
        run_ids: list[str] = []
        cursor: str | None = None
        while True:
            page = self.catalog(replace(filters, cursor=cursor, limit=500))
            run_ids.extend(run.run_id for run in page.runs)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return RunSelection.create(run_ids, filters)

    def aggregate(
        self,
        selection: RunSelection,
        *,
        include_private_derived: bool = True,
    ) -> AggregateDatasetSummary:
        self._repository.reconcile_incomplete_runs()
        _validate_selection(selection)
        runs = self._require_exact_runs(selection.run_ids)
        groups, warnings = _compatibility(runs)
        distributions: dict[str, Counter[str]] = defaultdict(Counter)
        metric_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        metric_units: dict[str, str | None] = {}

        for run in runs:
            included_record_count = self._collect_record_distributions(
                run.run_id,
                include_private_derived,
                distributions,
            )
            self._collect_feature_rows(
                run.run_id,
                include_private_derived,
                distributions,
                metric_values,
                metric_units,
            )
            _add_metric(
                metric_values,
                metric_units,
                "records.count",
                run.run_id,
                float(included_record_count),
                "records",
            )
            if run.final_tick is not None:
                _add_metric(
                    metric_values,
                    metric_units,
                    "run.final_tick",
                    run.run_id,
                    float(run.final_tick),
                    "ticks",
                )
            if run.final_simulation_time is not None:
                _add_metric(
                    metric_values,
                    metric_units,
                    "run.final_simulation_time",
                    run.run_id,
                    run.final_simulation_time,
                    "simulation seconds",
                )

        distributions["run.status"].update(run.effective_status for run in runs)
        distributions["run.scenario"].update(run.scenario_name for run in runs)
        distributions["run.dataset_schema"].update(
            run.dataset_schema_version for run in runs
        )
        distributions["run.capture_complete"].update(
            str(run.capture_complete).lower() for run in runs
        )
        distributions["run.seed"].update(str(run.seed) for run in runs)

        metrics = tuple(
            _metric(
                name,
                metric_units.get(name),
                metric_values[name],
                selection.run_ids,
            )
            for name in sorted(metric_values)
        )
        return AggregateDatasetSummary(
            selection=selection,
            include_private_derived=include_private_derived,
            private_derived_warning=(
                "Aggregate statistics include PRIVATE_RESEARCH-derived rows; "
                "raw private payloads are never returned."
                if include_private_derived
                else None
            ),
            compatibility_groups=groups,
            compatibility_warnings=warnings,
            weighting_definitions={
                "pooled": "Each numeric observation across selected runs has equal weight.",
                "macro_per_run": (
                    "Each run contributes one equally weighted mean calculated "
                    "from that run's observations."
                ),
            },
            per_run=runs,
            distributions={
                name: dict(sorted(counter.items()))
                for name, counter in sorted(distributions.items())
            },
            metrics=metrics,
        )

    def preview_deletion(self, selection: RunSelection) -> RunDeletionPreview:
        self._repository.reconcile_incomplete_runs()
        return self._repository.deletion_preview(
            selection,
            self._live_status_provider(),
        )

    def delete(
        self,
        selection: RunSelection,
        confirmation_token: str,
    ) -> RunDeletionResult:
        return self._repository.delete_runs(
            selection,
            confirmation_token,
            self._live_status_provider(),
        )

    def write_aggregate_json(
        self,
        aggregate: AggregateDatasetSummary,
        destination: TextIO,
    ) -> None:
        json.dump(
            aggregate.to_dict(),
            destination,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        destination.write("\n")

    def write_aggregate_csv(
        self,
        aggregate: AggregateDatasetSummary,
        destination: TextIO,
    ) -> None:
        writer = csv.writer(destination, lineterminator="\n")
        writer.writerow(("section", "path", "run_id", "value"))
        for path, run_id, value in _flatten_export(aggregate.to_dict()):
            section = path.split(".", 1)[0].split("[", 1)[0]
            writer.writerow((section, path, run_id, _csv_value(value)))

    def _require_exact_runs(
        self,
        run_ids: Sequence[str],
    ) -> tuple[PersistedRunSummary, ...]:
        runs = self._repository.get_persisted_runs(
            run_ids,
            self._live_status_provider(),
        )
        found = {run.run_id for run in runs}
        missing = [run_id for run_id in run_ids if run_id not in found]
        if missing:
            raise KeyError(f"unknown persisted runs: {', '.join(missing)}")
        by_id = {run.run_id: run for run in runs}
        return tuple(by_id[run_id] for run_id in run_ids)

    def _collect_record_distributions(
        self,
        run_id: str,
        include_private: bool,
        distributions: dict[str, Counter[str]],
    ) -> int:
        count = 0
        cursor: int | None = None
        while True:
            page = self._repository.query_records(
                run_id,
                DatasetRecordFilter(
                    include_private=include_private,
                    after_sequence=cursor,
                    limit=1000,
                ),
            )
            for record in page.records:
                count += 1
                distributions["record.type"][record.record_type] += 1
                distributions["record.category"][record.category.value] += 1
                distributions["record.visibility"][record.visibility.value] += 1
                distributions["record.schema"][record.schema_id] += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return count

    def _collect_feature_rows(
        self,
        run_id: str,
        include_private: bool,
        distributions: dict[str, Counter[str]],
        metric_values: dict[str, dict[str, list[float]]],
        metric_units: dict[str, str | None],
    ) -> None:
        table_specs: tuple[
            tuple[str, tuple[str, ...], tuple[tuple[str, str, str | None], ...]],
            ...,
        ] = (
            (
                "actions",
                ("action_type", "status", "origin"),
                (("created_tick", "actions.created_tick", "ticks"),),
            ),
            (
                "action_episodes",
                ("terminal_status",),
                (
                    (
                        "elapsed_simulation_time",
                        "actions.duration",
                        "simulation seconds",
                    ),
                ),
            ),
            (
                "decisions",
                ("status",),
                (("simulation_tick", "decisions.tick", "ticks"),),
            ),
            (
                "decision_episodes",
                ("status", "terminal_reason"),
                (
                    ("requested_at", "decisions.requested_at", "simulation seconds"),
                    ("terminal_at", "decisions.terminal_at", "simulation seconds"),
                ),
            ),
            (
                "goals",
                ("status",),
                (),
            ),
            (
                "goal_episodes",
                ("terminal_status",),
                (("duration", "goals.duration", "simulation seconds"),),
            ),
            (
                "interactions",
                ("interaction_type", "status"),
                (),
            ),
            (
                "interaction_episodes",
                ("interaction_type", "status", "content_visibility"),
                (("duration", "interactions.duration", "simulation seconds"),),
            ),
            (
                "model_requests",
                ("operation", "provider", "model", "status"),
                (),
            ),
            (
                "tool_executions",
                ("tool_name", "status"),
                (),
            ),
            (
                "transitions",
                ("outcome",),
                (
                    (
                        "elapsed_simulation_time",
                        "transitions.duration",
                        "simulation seconds",
                    ),
                ),
            ),
            (
                "resource_samples",
                ("resource_type", "phase"),
                (
                    ("capacity", "resources.capacity", "slots"),
                    ("occupancy", "resources.occupancy", "entities"),
                    ("queue_length", "resources.queue_length", "entities"),
                    ("utilization", "resources.utilization", "ratio"),
                ),
            ),
            (
                "resource_flows",
                ("flow_type",),
                (("amount", "resources.flow_amount", "units"),),
            ),
        )
        for table, dimensions, numeric in table_specs:
            for row in self._iter_table(run_id, table, include_private):
                distributions["feature.family"][table] += 1
                for dimension in dimensions:
                    value = row.get(dimension)
                    if value is not None:
                        distributions[f"{table}.{dimension}"][str(value)] += 1
                for column, name, unit in numeric:
                    value = _number(row.get(column))
                    if value is not None:
                        _add_metric(
                            metric_values,
                            metric_units,
                            name,
                            run_id,
                            value,
                            unit,
                        )
                if table == "decision_episodes":
                    requested_at = _number(row.get("requested_at"))
                    terminal_at = _number(row.get("terminal_at"))
                    if requested_at is not None and terminal_at is not None:
                        _add_metric(
                            metric_values,
                            metric_units,
                            "decisions.duration",
                            run_id,
                            max(0.0, terminal_at - requested_at),
                            "simulation seconds",
                        )
                if table == "goals":
                    goal = row.get("goal")
                    if isinstance(goal, dict):
                        progress = _number(goal.get("progress"))
                        if progress is not None:
                            _add_metric(
                                metric_values,
                                metric_units,
                                "goals.progress",
                                run_id,
                                progress,
                                "ratio",
                            )

        participant_counts: Counter[str] = Counter()
        for row in self._iter_table(
            run_id,
            "interaction_participants",
            include_private,
        ):
            interaction_id = row.get("interaction_id")
            if interaction_id is not None:
                participant_counts[str(interaction_id)] += 1
        for count in participant_counts.values():
            _add_metric(
                metric_values,
                metric_units,
                "interactions.participant_count",
                run_id,
                float(count),
                "participants",
            )

        for row in self._iter_table(run_id, "model_turns", include_private):
            usage = row.get("usage")
            if not isinstance(usage, dict):
                continue
            for key, name, unit in (
                ("latency_ms", "models.latency", "milliseconds"),
                ("input_tokens", "models.input_tokens", "tokens"),
                ("output_tokens", "models.output_tokens", "tokens"),
            ):
                value = _number(usage.get(key))
                if value is not None:
                    _add_metric(
                        metric_values,
                        metric_units,
                        name,
                        run_id,
                        value,
                        unit,
                    )

        for row in self._iter_table(run_id, "population", include_private):
            distributions["feature.family"]["population"] += 1
            population = row.get("population")
            if not isinstance(population, dict):
                continue
            entity_count = _number(population.get("entity_count"))
            if entity_count is not None:
                _add_metric(
                    metric_values,
                    metric_units,
                    "population.entity_count",
                    run_id,
                    entity_count,
                    "entities",
                )
            for key, distribution_name in (
                ("actor_counts", "population.actor"),
                ("activity_counts", "population.activity"),
                ("system1_state_counts", "population.system1_state"),
                ("place_counts", "population.place"),
            ):
                values = population.get(key)
                if isinstance(values, dict):
                    for name, item_count in values.items():
                        numeric_count = _number(item_count)
                        if numeric_count is not None:
                            distributions[distribution_name][name] += int(
                                numeric_count
                            )

        for row in self._iter_table(run_id, "opportunities", include_private):
            distributions["feature.family"]["opportunities"] += 1
            options = row.get("options")
            if isinstance(options, list):
                _add_metric(
                    metric_values,
                    metric_units,
                    "opportunities.option_count",
                    run_id,
                    float(len(options)),
                    "options",
                )

        for table, metric_name in (
            ("actions", "actions.count"),
            ("decisions", "decisions.count"),
            ("goals", "goals.count"),
            ("interactions", "interactions.count"),
        ):
            count = sum(1 for _ in self._iter_table(run_id, table, include_private))
            _add_metric(
                metric_values,
                metric_units,
                metric_name,
                run_id,
                float(count),
                table,
            )

    def _iter_table(
        self,
        run_id: str,
        table: str,
        include_private: bool,
    ) -> Iterator[dict[str, JsonValue]]:
        cursor: str | None = None
        while True:
            page = self._repository.query_table(
                run_id,
                table,
                DatasetQueryFilter(
                    include_private=include_private,
                    cursor=cursor,
                    limit=1000,
                ),
            )
            yield from page.rows
            if page.next_cursor is None:
                break
            cursor = page.next_cursor


def selection_fingerprint(
    run_ids: Sequence[str],
    filters: PersistedRunFilter | None = None,
) -> str:
    return _fingerprint(
        {
            "run_ids": list(_normalize_run_ids(run_ids)),
            "filters": filters.to_dict() if filters is not None else None,
        }
    )


def deletion_confirmation_token(
    selection: RunSelection,
    runs: Sequence[PersistedRunSummary],
    table_counts: Mapping[str, int],
) -> str:
    return _fingerprint(
        {
            "selection": selection.to_dict(),
            "runs": [run.to_dict() for run in runs],
            "table_counts": dict(sorted(table_counts.items())),
        }
    )


def _validate_selection(selection: RunSelection) -> None:
    if selection.fingerprint != selection_fingerprint(
        selection.run_ids,
        selection.filters,
    ):
        raise ValueError("stale or invalid run selection fingerprint")


def _compatibility(
    runs: Sequence[PersistedRunSummary],
) -> tuple[tuple[RunCompatibilityGroup, ...], tuple[str, ...]]:
    type CompatibilityKey = tuple[
        str,
        str,
        str,
        str,
        str,
        bool,
        str | None,
        str | None,
        str | None,
        tuple[tuple[str, str], ...],
    ]
    grouped: dict[CompatibilityKey, list[PersistedRunSummary]] = defaultdict(list)
    for run in runs:
        feature_versions = tuple(sorted(run.feature_schema_versions.items()))
        grouped[
            (
                run.dataset_schema_version,
                run.scenario_identity,
                run.scenario_name,
                run.world_schema_version,
                _canonical_json(dict(run.capture_configuration)),
                run.capture_complete,
                run.cognition_execution_mode,
                run.requested_npc_control_mode,
                run.effective_npc_control_mode,
                feature_versions,
            )
        ].append(run)
    groups: list[RunCompatibilityGroup] = []
    for index, (key, members) in enumerate(
        sorted(grouped.items(), key=lambda item: str(item[0])),
        start=1,
    ):
        (
            dataset_schema,
            scenario_identity,
            scenario,
            world_schema,
            capture_configuration,
            capture_complete,
            cognition_mode,
            requested_npc,
            effective_npc,
            feature_versions,
        ) = key
        groups.append(
            RunCompatibilityGroup(
                group_id=f"compatibility-{index:03d}",
                run_ids=tuple(run.run_id for run in members),
                dataset_schema_version=str(dataset_schema),
                scenario_identity=scenario_identity,
                scenario_name=str(scenario),
                world_schema_version=str(world_schema),
                capture_configuration=json.loads(capture_configuration),
                capture_complete=bool(capture_complete),
                cognition_execution_mode=_optional_string(cognition_mode),
                requested_npc_control_mode=_optional_string(requested_npc),
                effective_npc_control_mode=_optional_string(effective_npc),
                feature_schema_versions=dict(feature_versions),
            )
        )
    warnings: list[str] = []
    dimensions: tuple[tuple[str, Callable[[PersistedRunSummary], object]], ...] = (
        ("dataset schemas", lambda run: run.dataset_schema_version),
        ("scenario identities", lambda run: run.scenario_identity),
        ("scenarios", lambda run: run.scenario_name),
        ("world schemas", lambda run: run.world_schema_version),
        (
            "capture configurations",
            lambda run: _canonical_json(dict(run.capture_configuration)),
        ),
        ("capture completeness states", lambda run: run.capture_complete),
        ("cognition execution modes", lambda run: run.cognition_execution_mode),
        (
            "requested NPC control modes",
            lambda run: run.requested_npc_control_mode,
        ),
        (
            "effective NPC control modes",
            lambda run: run.effective_npc_control_mode,
        ),
        (
            "feature schema version sets",
            lambda run: tuple(sorted(run.feature_schema_versions.items())),
        ),
    )
    for label, getter in dimensions:
        if len({getter(run) for run in runs}) > 1:
            warnings.append(
                f"Selected runs use mixed {label}; pooled results may not be "
                "directly comparable."
            )
    if any(not run.capture_complete for run in runs):
        warnings.append(
            "The selection contains incomplete or interrupted capture; totals "
            "may be censored."
        )
    return tuple(groups), tuple(warnings)


def _metric(
    name: str,
    unit: str | None,
    values_by_run: Mapping[str, list[float]],
    run_order: Sequence[str],
) -> AggregateMetric:
    pooled_values = [
        value
        for run_id in run_order
        for value in values_by_run.get(run_id, ())
    ]
    per_run = tuple(
        RunMetricValue(run_id, _statistics(values_by_run[run_id]))
        for run_id in run_order
        if values_by_run.get(run_id)
    )
    run_means = [
        value.statistics.mean
        for value in per_run
        if value.statistics.mean is not None
    ]
    return AggregateMetric(
        name=name,
        unit=unit,
        pooled=_statistics(pooled_values),
        macro_per_run=_statistics(run_means),
        per_run=per_run,
    )


def _statistics(values: Sequence[float]) -> AggregateStatistics:
    if not values:
        return AggregateStatistics(0, None, None, None, None)
    return AggregateStatistics(
        count=len(values),
        minimum=min(values),
        maximum=max(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
    )


def _add_metric(
    values: dict[str, dict[str, list[float]]],
    units: dict[str, str | None],
    name: str,
    run_id: str,
    value: float,
    unit: str | None,
) -> None:
    values[name][run_id].append(value)
    units.setdefault(name, unit)


def _normalize_run_ids(run_ids: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for run_id in run_ids:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run IDs must not be empty")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _fingerprint(value: JsonValue) -> str:
    payload = _canonical_json(value).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _flatten_export(
    value: JsonValue,
    path: str = "",
    run_id: str = "",
) -> Iterator[tuple[str, str, JsonValue]]:
    if isinstance(value, dict):
        local_run_id = value.get("run_id")
        inherited_run_id = (
            local_run_id if isinstance(local_run_id, str) else run_id
        )
        for key in sorted(value):
            child_path = f"{path}.{key}" if path else key
            yield from _flatten_export(value[key], child_path, inherited_run_id)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_export(item, f"{path}[{index}]", run_id)
        if not value:
            yield path, run_id, []
        return
    yield path, run_id, value


def _csv_value(value: JsonValue) -> str:
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
    return str(value)
