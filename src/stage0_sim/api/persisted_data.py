import io
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from stage0_sim.application.data_management import PersistedRunFilter, RunSelection
from stage0_sim.application.manager import (
    SimulationConflictError,
    SimulationManager,
)
from stage0_sim.domain.events import JsonValue

router = APIRouter()


class PersistedRunCatalogParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_text: str | None = None
    persisted_status: list[str] = Field(default_factory=list)
    effective_status: list[str] = Field(default_factory=list)
    scenario_name: str | None = None
    dataset_schema_version: str | None = None
    capture_complete: bool | None = None
    started_at_or_after: datetime | None = None
    started_before: datetime | None = None
    completed_at_or_after: datetime | None = None
    completed_before: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=500)

    def to_filter(self) -> PersistedRunFilter:
        return PersistedRunFilter(
            search_text=self.search_text,
            persisted_statuses=tuple(self.persisted_status),
            effective_statuses=tuple(self.effective_status),
            scenario_name=self.scenario_name,
            dataset_schema_version=self.dataset_schema_version,
            capture_complete=self.capture_complete,
            started_at_or_after=self.started_at_or_after,
            started_before=self.started_before,
            completed_at_or_after=self.completed_at_or_after,
            completed_before=self.completed_before,
            cursor=self.cursor,
            limit=self.limit,
        )


class RunSelectionFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_text: str | None = None
    persisted_statuses: list[str] = Field(default_factory=list)
    effective_statuses: list[str] = Field(default_factory=list)
    scenario_name: str | None = None
    dataset_schema_version: str | None = None
    capture_complete: bool | None = None
    started_at_or_after: datetime | None = None
    started_before: datetime | None = None
    completed_at_or_after: datetime | None = None
    completed_before: datetime | None = None

    def to_filter(self) -> PersistedRunFilter:
        return PersistedRunFilter(
            search_text=self.search_text,
            persisted_statuses=tuple(self.persisted_statuses),
            effective_statuses=tuple(self.effective_statuses),
            scenario_name=self.scenario_name,
            dataset_schema_version=self.dataset_schema_version,
            capture_complete=self.capture_complete,
            started_at_or_after=self.started_at_or_after,
            started_before=self.started_before,
            completed_at_or_after=self.completed_at_or_after,
            completed_before=self.completed_before,
        )


class AggregateRunsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str] = Field(min_length=1, max_length=500)
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    selection_filters: RunSelectionFilters | None = None
    include_private_derived: bool = False

    def selection(self) -> RunSelection:
        selection = RunSelection.create(
            self.run_ids,
            (
                self.selection_filters.to_filter()
                if self.selection_filters is not None
                else None
            ),
        )
        if selection.fingerprint != self.selection_fingerprint:
            raise ValueError("stale or invalid run selection fingerprint")
        return selection


class DeleteRunsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str] = Field(min_length=1, max_length=500)
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    selection_filters: RunSelectionFilters | None = None

    def selection(self) -> RunSelection:
        selection = RunSelection.create(
            self.run_ids,
            (
                self.selection_filters.to_filter()
                if self.selection_filters is not None
                else None
            ),
        )
        if selection.fingerprint != self.selection_fingerprint:
            raise ValueError("stale or invalid run selection fingerprint")
        return selection


class ConfirmDeleteRunsRequest(DeleteRunsRequest):
    confirmation_token: str = Field(min_length=64, max_length=64)
    confirmed: bool
    confirmation_phrase: str


class AggregateExportParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: list[str] = Field(min_length=1, max_length=500)
    selection_fingerprint: str = Field(min_length=64, max_length=64)
    selection_filters: str | None = None
    include_private_derived: bool = False

    def request_body(self) -> AggregateRunsRequest:
        parsed_filters = (
            RunSelectionFilters.model_validate_json(self.selection_filters)
            if self.selection_filters is not None
            else None
        )
        return AggregateRunsRequest(
            run_ids=self.run_id,
            selection_fingerprint=self.selection_fingerprint,
            selection_filters=parsed_filters,
            include_private_derived=self.include_private_derived,
        )


def get_manager(request: Request) -> SimulationManager:
    return cast(SimulationManager, request.app.state.simulation_manager)


def _data_management_error(error: Exception) -> HTTPException:
    detail = str(error).strip("'")
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=detail)
    return HTTPException(status_code=409, detail=detail)


@router.get("/data/runs")
async def list_persisted_runs(
    request: Request,
    query: Annotated[PersistedRunCatalogParameters, Query()],
) -> dict[str, JsonValue]:
    try:
        return get_manager(request).persisted_runs(query.to_filter()).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/data/aggregate")
async def aggregate_persisted_runs(
    body: AggregateRunsRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        aggregate = get_manager(request).aggregate_persisted_runs(
            body.selection(),
            include_private_derived=body.include_private_derived,
        )
    except (KeyError, ValueError) as error:
        raise _data_management_error(error) from error
    return aggregate.to_dict()


def _aggregate_export(
    body: AggregateRunsRequest,
    request: Request,
    export_format: str,
) -> StreamingResponse:
    manager = get_manager(request)
    try:
        aggregate = manager.aggregate_persisted_runs(
            body.selection(),
            include_private_derived=body.include_private_derived,
        )
    except (KeyError, ValueError) as error:
        raise _data_management_error(error) from error
    output = io.StringIO()
    if export_format == "json":
        manager.data_management.write_aggregate_json(aggregate, output)
        media_type = "application/json"
    else:
        manager.data_management.write_aggregate_csv(aggregate, output)
        media_type = "text/csv"
    filename = f"stage0-aggregate-{body.selection_fingerprint[:12]}.{export_format}"
    return StreamingResponse(
        iter((output.getvalue(),)),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Stage0-Private-Included": str(
                body.include_private_derived
            ).lower(),
            "X-Stage0-Privacy-Warning": (
                "Aggregate includes PRIVATE_RESEARCH-derived statistics; "
                "raw private payloads are not included."
                if body.include_private_derived
                else "Private research data excluded."
            ),
        },
    )


@router.post("/data/aggregate.json")
async def export_persisted_run_aggregate_json(
    body: AggregateRunsRequest,
    request: Request,
) -> StreamingResponse:
    return _aggregate_export(body, request, "json")


@router.get("/data/aggregate.json")
async def download_persisted_run_aggregate_json(
    request: Request,
    query: Annotated[AggregateExportParameters, Query()],
) -> StreamingResponse:
    return _aggregate_export(query.request_body(), request, "json")


@router.post("/data/aggregate.csv")
async def export_persisted_run_aggregate_csv(
    body: AggregateRunsRequest,
    request: Request,
) -> StreamingResponse:
    return _aggregate_export(body, request, "csv")


@router.get("/data/aggregate.csv")
async def download_persisted_run_aggregate_csv(
    request: Request,
    query: Annotated[AggregateExportParameters, Query()],
) -> StreamingResponse:
    return _aggregate_export(query.request_body(), request, "csv")


@router.post("/data/deletion-preview")
async def preview_persisted_run_deletion(
    body: DeleteRunsRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        preview = get_manager(request).preview_persisted_run_deletion(
            body.selection()
        )
    except (KeyError, ValueError) as error:
        raise _data_management_error(error) from error
    return preview.to_dict()


@router.post("/data/delete")
async def delete_persisted_runs(
    body: ConfirmDeleteRunsRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        selection = body.selection()
    except ValueError as error:
        raise _data_management_error(error) from error
    expected_phrase = f"DELETE {len(selection.run_ids)} RUNS"
    if not body.confirmed or body.confirmation_phrase != expected_phrase:
        raise HTTPException(
            status_code=422,
            detail=(
                "confirmed must be true and confirmation_phrase must exactly "
                f"match {expected_phrase!r}"
            ),
        )
    try:
        result = get_manager(request).delete_persisted_runs(
            selection,
            body.confirmation_token,
        )
    except (KeyError, ValueError, SimulationConflictError) as error:
        raise _data_management_error(error) from error
    session_store = getattr(request.app.state, "operator_sessions", None)
    cleanup = getattr(session_store, "remove_deleted_run_ids", None)
    if callable(cleanup):
        cleanup(result.run_ids)
    return result.to_dict()

