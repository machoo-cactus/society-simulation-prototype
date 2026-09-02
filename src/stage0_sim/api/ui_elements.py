from __future__ import annotations

import json
from typing import cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError

from stage0_sim.api.scenario_forms import (
    ELEMENT_EDITOR_MODELS,
    ElementEditorDraft,
    ElementEditorDraftStore,
    ScenarioEditorError,
    apply_collection_action,
    clear_draft_errors,
    encode_element_draft_value,
    node_from_value,
    update_draft_from_form,
    validate_element_draft,
)
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
    ElementKind,
    ScenarioElementDefinition,
    element_content_hash,
)

router = APIRouter(prefix="/ui", tags=["element-library-ui"], include_in_schema=False)


def _library(request: Request) -> ElementLibrary:
    return cast(ElementLibrary, request.app.state.element_library)


def _drafts(request: Request) -> ElementEditorDraftStore:
    store = getattr(request.app.state, "element_editor_drafts", None)
    if not isinstance(store, ElementEditorDraftStore):
        store = ElementEditorDraftStore()
        request.app.state.element_editor_drafts = store
    return store


def _starter(kind: ElementKind) -> dict[str, object]:
    common: dict[str, object] = {
        "schema_version": 3,
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
            "hearing_range": 10,
            "smell_range": 0,
        }
    if kind is ElementKind.OBJECT:
        return {
            **common,
            "object_type": None,
            "physical": {
                "footprint": {"cells": [{"x": 0, "y": 0}]},
            },
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


def _draft_url(draft: ElementEditorDraft) -> str:
    query: dict[str, str] = {"draft": draft.token}
    if draft.original_id is not None:
        query["selected"] = draft.original_id
    else:
        query["kind"] = draft.kind.value
    return f"/ui/elements/?{urlencode(query)}"


def _replace_draft_element(
    draft: ElementEditorDraft,
    element: ScenarioElementDefinition,
    *,
    resource_id: str,
    original_id: str | None,
    original_hash: str,
) -> None:
    draft.resource_id = resource_id
    draft.original_id = original_id
    draft.original_hash = original_hash
    draft.kind = ElementKind(element.kind)
    draft.root = node_from_value(
        draft.root.schema,
        element.model_dump(mode="json"),
    )
    draft.raw_json = json.dumps(
        element.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    draft.errors.clear()


def _add_draft_error(
    draft: ElementEditorDraft,
    message: str,
    *,
    control_id: str = "",
) -> None:
    draft.errors.append(
        ScenarioEditorError(
            message=message,
            control_id=control_id or draft.root.control_id,
        )
    )


def _render(
    request: Request,
    *,
    draft: ElementEditorDraft,
    form_error: str = "",
) -> Response:
    session_id, session = _session(request)
    try:
        summaries = _library(request).list()
    except ElementLibraryError as error:
        summaries = ()
        form_error = form_error or str(error)
    raw, _issues = encode_element_draft_value(draft)
    generated_json = json.dumps(
        raw,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    advanced_json = (
        draft.raw_json
        if any(error.control_id == "element-json" for error in draft.errors)
        else generated_json
    )
    message, notice_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "elements.html",
        {
            "elements": summaries,
            "draft": draft,
            "raw_json": advanced_json,
            "form_error": form_error,
            "message": message,
            "error": notice_error,
        },
    )
    return _with_session(response, session_id)


@router.get("/elements/")
async def element_library_page(request: Request) -> Response:
    session_id, session = _session(request)
    token = request.query_params.get("draft", "")
    if token:
        draft = _drafts(request).get(session_id, token)
        if draft is None:
            session.notify(
                "That element editor draft expired. Open the element again.",
                error=True,
            )
            return _with_session(_redirect("/ui/elements/"), session_id)
        return _render(request, draft=draft)
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
            draft = _drafts(request).create(
                session_id,
                selected_kind,
                _starter(selected_kind),
            )
            session.notify(str(error), error=True)
            return _with_session(_redirect(_draft_url(draft)), session_id)
        draft = _drafts(request).create(
            session_id,
            ElementKind(selected.kind),
            selected.model_dump(mode="json"),
            resource_id=selected.id,
            original_id=selected.id,
            original_hash=element_content_hash(selected),
        )
        return _with_session(_redirect(_draft_url(draft)), session_id)
    starter = _starter(selected_kind)
    draft = _drafts(request).create(
        session_id,
        selected_kind,
        starter,
    )
    return _with_session(_redirect(_draft_url(draft)), session_id)


@router.post("/elements/save")
async def save_element(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    token = str(form.get("draft_token", ""))
    if not token and "element_json" in form:
        selected_kind_value = str(
            form.get("selected_kind", ElementKind.BUILDING.value)
        )
        try:
            selected_kind = ElementKind(selected_kind_value)
        except ValueError:
            selected_kind = ElementKind.BUILDING
        resource_id = str(form.get("resource_id", "")).strip()
        original_id = str(form.get("original_id", "")).strip()
        expected_hash = str(form.get("expected_hash", "")).strip()
        try:
            raw = json.loads(str(form.get("element_json", "")))
            if not isinstance(raw, dict):
                raise ValueError("element JSON must be an object")
            raw["id"] = resource_id
            element = cast(
                ScenarioElementDefinition,
                ELEMENT_EDITOR_MODELS[selected_kind].model_validate(raw),
            )
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
            session.notify(f"Element JSON is invalid: {error}", error=True)
            return _with_session(_redirect("/ui/elements/"), session_id)
        except ValidationError as error:
            session.notify(
                f"Element validation failed: {_validation_message(error)}",
                error=True,
            )
            return _with_session(_redirect("/ui/elements/"), session_id)
        except (ElementLibraryError, ValueError) as error:
            session.notify(f"Could not save element: {error}", error=True)
            return _with_session(_redirect("/ui/elements/"), session_id)
        session.notify(f"Saved {saved.name}.")
        return _with_session(
            _redirect(f"/ui/elements/?selected={quote(saved.id)}"),
            session_id,
        )
    draft = _drafts(request).get(session_id, token)
    if draft is None:
        session.notify(
            "That element editor draft expired. Open the element again.",
            error=True,
        )
        return _with_session(_redirect("/ui/elements/"), session_id)
    update_draft_from_form(draft, form)
    collection_action = str(form.get("collection_action", ""))
    if collection_action:
        if not apply_collection_action(draft, collection_action):
            session.notify("The requested collection change is invalid.", error=True)
        return _with_session(_redirect(_draft_url(draft)), session_id)
    intent = str(form.get("intent", "save"))
    if intent == "refresh":
        clear_draft_errors(draft)
        return _with_session(_redirect(_draft_url(draft)), session_id)
    if intent == "import_json":
        draft.raw_json = str(form.get("element_json", ""))
        clear_draft_errors(draft)
        try:
            raw = json.loads(draft.raw_json)
            if not isinstance(raw, dict):
                raise ValueError("element JSON must be an object")
            model = ELEMENT_EDITOR_MODELS[draft.kind]
            imported = cast(
                ScenarioElementDefinition,
                model.model_validate(raw),
            )
        except json.JSONDecodeError as error:
            _add_draft_error(
                draft,
                f"Element JSON is invalid: {error}",
                control_id="element-json",
            )
        except ValidationError as error:
            _add_draft_error(
                draft,
                f"Element validation failed: {_validation_message(error)}",
                control_id="element-json",
            )
        except ValueError as error:
            _add_draft_error(
                draft,
                str(error),
                control_id="element-json",
            )
        else:
            _replace_draft_element(
                draft,
                imported,
                resource_id=imported.id,
                original_id=draft.original_id,
                original_hash=draft.original_hash,
            )
            session.notify("Imported JSON into the structured element editor.")
        return _with_session(_redirect(_draft_url(draft)), session_id)

    validated_element = validate_element_draft(draft)
    if validated_element is None:
        return _with_session(_redirect(_draft_url(draft)), session_id)
    expected_hash = draft.original_hash
    try:
        if draft.original_id:
            if draft.resource_id != draft.original_id:
                renamed = _library(request).rename(
                    draft.original_id,
                    draft.resource_id,
                    expected_hash,
                )
                expected_hash = element_content_hash(renamed)
            saved = _library(request).update(
                draft.resource_id,
                validated_element,
                expected_hash,
            )
        else:
            saved = _library(request).create(validated_element)
    except (ElementLibraryError, ValueError) as error:
        _add_draft_error(draft, f"Could not save element: {error}")
    else:
        _drafts(request).delete(session_id, draft.token)
        session.notify(f"Saved {saved.name}.")
        return _with_session(
            _redirect(f"/ui/elements/?selected={quote(saved.id)}"),
            session_id,
        )
    return _with_session(_redirect(_draft_url(draft)), session_id)


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
