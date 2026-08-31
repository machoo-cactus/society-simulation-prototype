from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from stage0_sim.application.characters import (
    CharacterConflictError,
    CharacterDefinition,
    CharacterLibrary,
    CharacterLibraryError,
    CharacterNotFoundError,
    character_content_hash,
)
from stage0_sim.domain.events import JsonValue

router = APIRouter(prefix="/characters", tags=["characters"])


class CharacterUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(min_length=1)
    character: CharacterDefinition


class CharacterRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(min_length=1)
    new_id: str = Field(min_length=1)


def get_library(request: Request) -> CharacterLibrary:
    return cast(CharacterLibrary, request.app.state.character_library)


def character_payload(
    character: CharacterDefinition,
) -> dict[str, JsonValue]:
    return {
        "character": character.model_dump(mode="json"),
        "content_hash": character_content_hash(character),
    }


def raise_library_http_error(error: CharacterLibraryError) -> None:
    if isinstance(error, CharacterNotFoundError):
        status_code = 404
    elif isinstance(error, CharacterConflictError):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("")
async def list_characters(request: Request) -> dict[str, object]:
    try:
        characters = get_library(request).list()
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return {
        "characters": [summary.to_payload() for summary in characters],
    }


@router.post("", status_code=201)
async def create_character(
    character: CharacterDefinition,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        created = get_library(request).create(character)
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return character_payload(created)


@router.get("/{character_id}")
async def get_character(
    character_id: str,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        character = get_library(request).get(character_id)
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return character_payload(character)


@router.put("/{character_id}")
async def update_character(
    character_id: str,
    body: CharacterUpdateRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        character = get_library(request).update(
            character_id,
            body.character,
            body.expected_hash,
        )
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return character_payload(character)


@router.post("/{character_id}/rename")
async def rename_character(
    character_id: str,
    body: CharacterRenameRequest,
    request: Request,
) -> dict[str, JsonValue]:
    try:
        character = get_library(request).rename(
            character_id,
            body.new_id,
            body.expected_hash,
        )
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return character_payload(character)


@router.delete("/{character_id}")
async def delete_character(
    character_id: str,
    request: Request,
    expected_hash: str = Query(min_length=1),
) -> dict[str, str]:
    try:
        get_library(request).delete(character_id, expected_hash)
    except CharacterLibraryError as error:
        raise_library_http_error(error)
    return {"status": "deleted", "character_id": character_id}
