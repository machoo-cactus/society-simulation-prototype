from dataclasses import dataclass

from stage0_sim.application.data_capture import (
    DATASET_SCHEMA_VERSION,
    RecordCategory,
    RecordJoinIds,
    RecordRelation,
    RecordSource,
    RecordVisibility,
    RunnerPhase,
)
from stage0_sim.application.dataset import DatasetRecord
from stage0_sim.application.ports import DatasetCaptureRepository
from stage0_sim.domain.events import JsonValue


@dataclass(slots=True)
class DatasetRecordProjector:
    """Projects collector facts into canonical records and relation rows."""

    store: DatasetCaptureRepository
    run_id: str
    sequence: int = 0

    def append(
        self,
        record_type: str,
        tick: int,
        simulation_time: float,
        subject_id: str | None,
        payload: dict[str, JsonValue],
        source_event_id: str | None,
        *,
        category: RecordCategory = RecordCategory.OTHER,
        source: RecordSource = RecordSource.DATASET_COLLECTOR,
        phase: RunnerPhase = RunnerPhase.UNSPECIFIED,
        visibility: RecordVisibility = RecordVisibility.OPERATOR,
        related_entity_ids: tuple[str, ...] = (),
        joins: RecordJoinIds | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        schema_id: str = "",
        schema_version: str = DATASET_SCHEMA_VERSION,
    ) -> DatasetRecord:
        self.sequence += 1
        resolved_joins = joins or RecordJoinIds()
        record = DatasetRecord(
            run_id=self.run_id,
            sequence=self.sequence,
            record_type=record_type,
            simulation_tick=tick,
            simulation_time=simulation_time,
            subject_id=subject_id,
            payload=payload,
            source_event_id=source_event_id,
            schema_id=schema_id,
            schema_version=schema_version,
            category=category,
            source=source,
            phase=phase,
            visibility=visibility,
            related_entity_ids=related_entity_ids,
            joins=resolved_joins,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        self.store.append(record)
        if subject_id is not None:
            self._relation(record, "subject", "entity", subject_id)
        if source_event_id is not None:
            self._relation(record, "source", "event", source_event_id)
        for ordinal, related_id in enumerate(related_entity_ids):
            self._relation(
                record,
                "related",
                "entity",
                related_id,
                ordinal=ordinal,
            )
        for ordinal, (target_type, target_id) in enumerate(
            resolved_joins.to_dict().items()
        ):
            self._relation(
                record,
                "join",
                target_type.removesuffix("_id"),
                str(target_id),
                ordinal=ordinal,
            )
        return record

    def _relation(
        self,
        record: DatasetRecord,
        relation_type: str,
        target_type: str,
        target_id: str,
        *,
        ordinal: int = 0,
    ) -> None:
        self.store.add_record_relation(
            RecordRelation(
                run_id=self.run_id,
                record_id=record.record_id,
                relation_type=relation_type,
                target_type=target_type,
                target_id=target_id,
                ordinal=ordinal,
            )
        )
