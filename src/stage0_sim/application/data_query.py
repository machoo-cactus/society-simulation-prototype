from collections.abc import Iterator
from typing import BinaryIO, Protocol

from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetQueryPage,
    DatasetRecordFilter,
    DatasetRecordPage,
)
from stage0_sim.domain.events import JsonValue


class DatasetQueryRepository(Protocol):
    def query_records(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> DatasetRecordPage: ...

    def query_table(
        self,
        run_id: str,
        table_name: str,
        filters: DatasetQueryFilter | None = None,
    ) -> DatasetQueryPage: ...

    def summary(self, run_id: str) -> dict[str, JsonValue]: ...

    def data_dictionary(
        self,
        run_id: str | None = None,
    ) -> dict[str, JsonValue]: ...

    def iter_records_ndjson(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> Iterator[str]: ...

    def write_analysis_bundle(
        self,
        run_id: str,
        destination: BinaryIO,
        filters: DatasetQueryFilter | None = None,
    ) -> None: ...


class DatasetQueryService:
    """Provider-neutral application boundary for persisted research data."""

    def __init__(self, repository: DatasetQueryRepository) -> None:
        self._repository = repository

    def records(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> DatasetRecordPage:
        return self._repository.query_records(run_id, filters)

    def table(
        self,
        run_id: str,
        table_name: str,
        filters: DatasetQueryFilter | None = None,
    ) -> DatasetQueryPage:
        return self._repository.query_table(run_id, table_name, filters)

    def summary(self, run_id: str) -> dict[str, JsonValue]:
        return self._repository.summary(run_id)

    def schema(self, run_id: str | None = None) -> dict[str, JsonValue]:
        return self._repository.data_dictionary(run_id)

    def raw_ndjson(
        self,
        run_id: str,
        filters: DatasetRecordFilter | None = None,
    ) -> Iterator[str]:
        return self._repository.iter_records_ndjson(run_id, filters)

    def analysis_bundle(
        self,
        run_id: str,
        destination: BinaryIO,
        filters: DatasetQueryFilter | None = None,
    ) -> None:
        self._repository.write_analysis_bundle(run_id, destination, filters)
