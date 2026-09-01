from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, ValidationError

from stage0_sim.application.element_library import (
    ElementConflictError,
    ElementDependencyError,
    ElementLibrary,
    ElementLibraryError,
    ElementNotFoundError,
)
from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    ElementKind,
    ScenarioElementDefinition,
    element_content_hash,
)

router = APIRouter(prefix="/elements", tags=["elements"])


class ElementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element: dict[str, Any]


class ElementUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str
    element: dict[str, Any]


class ElementRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str
    new_id: str


def get_library(request: Request) -> ElementLibrary:
    return cast(ElementLibrary, request.app.state.element_library)


def element_response(
    element: ScenarioElementDefinition,
) -> dict[str, object]:
    return {
        "id": element.id,
        "kind": element.kind,
        "content_hash": element_content_hash(element),
        "element": element.model_dump(mode="json"),
    }


def parse_element(raw: dict[str, Any]) -> ScenarioElementDefinition:
    try:
        return SCENARIO_ELEMENT_ADAPTER.validate_python(raw)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error


def raise_library_http_error(error: ElementLibraryError) -> None:
    if isinstance(error, ElementNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, (ElementConflictError, ElementDependencyError)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("")
async def list_elements(
    request: Request,
    kind: Annotated[ElementKind | None, Query()] = None,
) -> dict[str, object]:
    try:
        summaries = get_library(request).list(kind)
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return {"elements": [item.to_payload() for item in summaries]}


@router.get("/{element_id}")
async def get_element(
    element_id: str,
    request: Request,
) -> dict[str, object]:
    try:
        element = get_library(request).get(element_id)
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return element_response(element)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_element(
    body: ElementCreateRequest,
    request: Request,
) -> dict[str, object]:
    element = parse_element(body.element)
    try:
        created = get_library(request).create(element)
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return element_response(created)


@router.put("/{element_id}")
async def update_element(
    element_id: str,
    body: ElementUpdateRequest,
    request: Request,
) -> dict[str, object]:
    element = parse_element(body.element)
    try:
        updated = get_library(request).update(
            element_id,
            element,
            body.expected_hash,
        )
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return element_response(updated)


@router.post("/{element_id}/rename")
async def rename_element(
    element_id: str,
    body: ElementRenameRequest,
    request: Request,
) -> dict[str, object]:
    try:
        renamed = get_library(request).rename(
            element_id,
            body.new_id,
            body.expected_hash,
        )
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return element_response(renamed)


@router.delete("/{element_id}")
async def delete_element(
    element_id: str,
    request: Request,
    expected_hash: Annotated[str, Query(min_length=1)],
) -> dict[str, object]:
    try:
        deleted = get_library(request).delete(element_id, expected_hash)
    except ElementLibraryError as error:
        raise_library_http_error(error)
    return element_response(deleted)
