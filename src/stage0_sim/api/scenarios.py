from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from stage0_sim.application.scenario import ScenarioDefinition
from stage0_sim.application.scenarios import (
    ScenarioConflictError,
    ScenarioLibrary,
    ScenarioLibraryError,
    ScenarioNotFoundError,
    scenario_content_hash,
)
from stage0_sim.domain.events import JsonValue

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class ScenarioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    scenario: ScenarioDefinition


class ScenarioUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(min_length=1)
    scenario: ScenarioDefinition


class ScenarioRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(min_length=1)
    new_id: str = Field(min_length=1)


def get_library(request: Request) -> ScenarioLibrary:
    return cast(ScenarioLibrary, request.app.state.scenario_library)


def scenario_payload(
    scenario_id: str,
    scenario: ScenarioDefinition,
) -> dict[str, JsonValue]:
    return {
        "id": scenario_id,
        "scenario": scenario.model_dump(mode="json"),
        "content_hash": scenario_content_hash(scenario),
    }


def raise_library_http_error(error: ScenarioLibraryError) -> None:
    if isinstance(error, ScenarioNotFoundError):
        status_code = 404
    elif isinstance(error, ScenarioConflictError):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("")
async def list_scenarios(request: Request) -> dict[str, object]:
    try:
        scenarios = get_library(request).list()
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return {"scenarios": [summary.to_payload() for summary in scenarios]}


@router.post("", status_code=201)
async def create_scenario(
    body: ScenarioCreateRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        scenario = get_library(request).create(body.id, body.scenario)
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return scenario_payload(body.id, scenario)


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        scenario = get_library(request).get(scenario_id)
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return scenario_payload(scenario_id, scenario)


@router.put("/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    body: ScenarioUpdateRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        scenario = get_library(request).update(
            scenario_id,
            body.scenario,
            body.expected_hash,
        )
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return scenario_payload(scenario_id, scenario)


@router.post("/{scenario_id}/rename")
async def rename_scenario(
    scenario_id: str,
    body: ScenarioRenameRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        scenario = get_library(request).rename(
            scenario_id,
            body.new_id,
            body.expected_hash,
        )
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return scenario_payload(body.new_id, scenario)


@router.delete("/{scenario_id}")
async def delete_scenario(
    scenario_id: str,
    request: Request,
    expected_hash: str = Query(min_length=1),
) -> dict[str, JsonValue]:
    try:
        scenario = get_library(request).delete(scenario_id, expected_hash)
    except ScenarioLibraryError as error:
        raise_library_http_error(error)
    return scenario_payload(scenario_id, scenario)
