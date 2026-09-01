from __future__ import annotations

import json
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError

from stage0_sim.api.ui import (
    _redirect,
    _session,
    _validation_message,
    _with_session,
    templates,
)
from stage0_sim.application.element_library import (
    ElementLibrary,
    ElementLibraryError,
)
from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    ElementKind,
    ScenarioElementDefinition,
    element_content_hash,
)

router = APIRouter(prefix="/ui", tags=["element-library-ui"], include_in_schema=False)


def _library(request: Request) -> ElementLibrary:
    return cast(ElementLibrary, request.app.state.element_library)


def _starter(kind: ElementKind) -> dict[str, object]:
    common: dict[str, object] = {
        "schema_version": 1,
        "id": "",
        "name": "Untitled element",
        "description": "",
        "kind": kind.value,
    }
    if kind is ElementKind.NPC_ROLE:
        return {
            **common,
            "briefing": "",
            "tool_allowlist": ["serve_transaction", "say", "wait", "skip"],
            "vision_range": 6,
            "recognition_range": 4,
            "hearing_multiplier": 1.0,
        }
    if kind is ElementKind.OBJECT:
        return {
            **common,
            "object_type": "affordance",
            "actions": [
                {
                    "action": "RELAX",
                    "duration": 60,
                    "effect": {"stress_delta": -1},
                }
            ],
            "capacity": 1,
        }
    if kind is ElementKind.ROOM:
        return {
            **common,
            "room_type": "ROOM",
            "width": 5,
            "height": 5,
            "blocked": [],
            "objects": [],
        }
    return {
        **common,
        "rooms": [],
        "portals": [],
        "entrances": [],
    }


def _render(
    request: Request,
    *,
    selected: ScenarioElementDefinition | None,
    raw_json: str,
    resource_id: str,
    original_id: str,
    expected_hash: str,
    selected_kind: ElementKind,
    form_error: str = "",
) -> Response:
    session_id, session = _session(request)
    try:
        summaries = _library(request).list()
    except ElementLibraryError as error:
        summaries = ()
        form_error = form_error or str(error)
    message, notice_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "elements.html",
        {
            "elements": summaries,
            "selected": selected,
            "raw_json": raw_json,
            "resource_id": resource_id,
            "original_id": original_id,
            "expected_hash": expected_hash,
            "selected_kind": selected_kind,
            "form_error": form_error,
            "message": message,
            "error": notice_error,
        },
    )
    return _with_session(response, session_id)


@router.get("/elements/")
async def element_library_page(request: Request) -> Response:
    selected_id = request.query_params.get("selected", "")
    kind_value = request.query_params.get("kind", ElementKind.BUILDING.value)
    try:
        selected_kind = ElementKind(kind_value)
    except ValueError:
        selected_kind = ElementKind.BUILDING
    if selected_id:
        try:
            selected = _library(request).get(selected_id)
        except ElementLibraryError as error:
            return _render(
                request,
                selected=None,
                raw_json=json.dumps(
                    _starter(selected_kind),
                    indent=2,
                    sort_keys=True,
                ),
                resource_id="",
                original_id="",
                expected_hash="",
                selected_kind=selected_kind,
                form_error=str(error),
            )
        return _render(
            request,
            selected=selected,
            raw_json=json.dumps(
                selected.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            resource_id=selected.id,
            original_id=selected.id,
            expected_hash=element_content_hash(selected),
            selected_kind=ElementKind(selected.kind),
        )
    starter = _starter(selected_kind)
    return _render(
        request,
        selected=None,
        raw_json=json.dumps(starter, indent=2, sort_keys=True),
        resource_id="",
        original_id="",
        expected_hash="",
        selected_kind=selected_kind,
    )


@router.post("/elements/save")
async def save_element(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    resource_id = str(form.get("resource_id", "")).strip()
    original_id = str(form.get("original_id", "")).strip()
    expected_hash = str(form.get("expected_hash", "")).strip()
    raw_json = str(form.get("element_json", ""))
    selected_kind_value = str(
        form.get("selected_kind", ElementKind.BUILDING.value)
    )
    try:
        selected_kind = ElementKind(selected_kind_value)
    except ValueError:
        selected_kind = ElementKind.BUILDING
    try:
        raw = json.loads(raw_json)
        if not isinstance(raw, dict):
            raise ValueError("element JSON must be an object")
        raw["id"] = resource_id
        element = SCENARIO_ELEMENT_ADAPTER.validate_python(raw)
        if original_id:
            if resource_id != original_id:
                renamed = _library(request).rename(
                    original_id,
                    resource_id,
                    expected_hash,
                )
                expected_hash = element_content_hash(renamed)
            saved = _library(request).update(
                resource_id,
                element,
                expected_hash,
            )
        else:
            saved = _library(request).create(element)
    except json.JSONDecodeError as error:
        form_error = f"Element JSON is invalid: {error}"
    except ValidationError as error:
        form_error = f"Element validation failed: {_validation_message(error)}"
    except (ElementLibraryError, ValueError) as error:
        form_error = f"Could not save element: {error}"
    else:
        session.notify(f"Saved {saved.name}.")
        return _with_session(
            _redirect(f"/ui/elements/?selected={quote(saved.id)}"),
            session_id,
        )
    return _render(
        request,
        selected=None,
        raw_json=raw_json,
        resource_id=resource_id,
        original_id=original_id,
        expected_hash=expected_hash,
        selected_kind=selected_kind,
        form_error=form_error,
    )


@router.post("/elements/{element_id}/duplicate")
async def duplicate_element(element_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    new_id = str(form.get("new_id", "")).strip()
    try:
        source = _library(request).get(element_id)
        duplicate = source.model_copy(
            update={
                "id": new_id,
                "name": f"{source.name} Copy",
            }
        )
        saved = _library(request).create(duplicate)
    except ElementLibraryError as error:
        session.notify(f"Could not duplicate element: {error}", error=True)
        target = f"/ui/elements/?selected={quote(element_id)}"
    else:
        session.notify(f"Duplicated {element_id} as {saved.id}.")
        target = f"/ui/elements/?selected={quote(saved.id)}"
    return _with_session(_redirect(target), session_id)


@router.post("/elements/{element_id}/delete")
async def delete_element(element_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        if form.get("confirm") != "yes":
            raise ValueError("confirm deletion before continuing")
        _library(request).delete(
            element_id,
            str(form.get("expected_hash", "")),
        )
    except (ElementLibraryError, ValueError) as error:
        session.notify(f"Could not delete element: {error}", error=True)
        target = f"/ui/elements/?selected={quote(element_id)}"
    else:
        session.notify(f"Deleted {element_id}.")
        target = "/ui/elements/"
    return _with_session(_redirect(target), session_id)
