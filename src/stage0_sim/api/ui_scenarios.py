from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from stage0_sim.api.scenario_forms import (
    ScenarioEditorDraft,
    ScenarioEditorDraftStore,
    ScenarioEditorError,
    apply_collection_action,
    clear_draft_errors,
    minimal_scenario,
    node_from_value,
    update_draft_from_form,
    validate_draft,
)
from stage0_sim.api.ui import (
    MAX_UPLOAD_BYTES,
    _manager,
    _redirect,
    _session,
    _stage_scenario,
    _validation_message,
    _with_session,
    templates,
)
from stage0_sim.application.scenario import ScenarioDefinition
from stage0_sim.application.scenarios import (
    ScenarioConflictError,
    ScenarioLibrary,
    ScenarioLibraryError,
    scenario_content_hash,
    validate_scenario_id,
)

router = APIRouter(prefix="/ui", tags=["scenario-library-ui"], include_in_schema=False)
MAX_EDITOR_FORM_FIELDS = 20_000


def _library(request: Request) -> ScenarioLibrary:
    return cast(ScenarioLibrary, request.app.state.scenario_library)


def _drafts(request: Request) -> ScenarioEditorDraftStore:
    return cast(ScenarioEditorDraftStore, request.app.state.scenario_editor_drafts)


def _draft_url(draft: ScenarioEditorDraft) -> str:
    query: dict[str, str] = {"draft": draft.token}
    if draft.original_id is not None:
        query["selected"] = draft.original_id
    else:
        query["new"] = "1"
    return f"/ui/scenarios/?{urlencode(query)}"


def _replace_draft_scenario(
    draft: ScenarioEditorDraft,
    scenario: ScenarioDefinition,
    *,
    resource_id: str,
    original_id: str | None,
    original_hash: str,
) -> None:
    draft.resource_id = resource_id
    draft.original_id = original_id
    draft.original_hash = original_hash
    draft.root = node_from_value(
        draft.root.schema,
        scenario.model_dump(mode="json"),
    )
    draft.errors.clear()


def _draft_or_redirect(
    request: Request,
    session_id: str,
    token: str,
) -> tuple[ScenarioEditorDraft | None, Response | None]:
    draft = _drafts(request).get(session_id, token)
    if draft is not None:
        return draft, None
    _session(request)[1].notify(
        "That scenario editor draft expired. Open the scenario again.",
        error=True,
    )
    return None, _with_session(_redirect("/ui/scenarios/"), session_id)


@router.get("/scenarios/")
async def scenario_library_page(request: Request) -> Response:
    session_id, session = _session(request)
    library = _library(request)
    search_text = request.query_params.get("search", "")
    search = search_text.strip().casefold()
    try:
        summaries = list(library.list())
    except ScenarioLibraryError as error:
        session.notify(f"Could not list scenarios: {error}", error=True)
        summaries = []
    if search:
        summaries = [
            item
            for item in summaries
            if search
            in (f"{item.id} {item.name} {item.world_kind} {item.schema_version}").casefold()
        ]
    token = request.query_params.get("draft", "")
    draft = _drafts(request).get(session_id, token) if token else None
    if token and draft is None:
        session.notify(
            "That scenario editor draft expired. Open the scenario again.",
            error=True,
        )
        return _with_session(_redirect("/ui/scenarios/"), session_id)
    if draft is None:
        selected_id = request.query_params.get("selected")
        creating = request.query_params.get("new") == "1"
        try:
            if creating or (selected_id is None and not summaries):
                draft = _drafts(request).create(
                    session_id,
                    minimal_scenario(),
                )
            else:
                selected_id = selected_id or summaries[0].id
                scenario = library.get(selected_id)
                draft = _drafts(request).create(
                    session_id,
                    scenario,
                    resource_id=selected_id,
                    original_id=selected_id,
                    original_hash=scenario_content_hash(scenario),
                )
        except ScenarioLibraryError as error:
            session.notify(f"Could not open scenario: {error}", error=True)
            return _with_session(_redirect("/ui/scenarios/?new=1"), session_id)
        return _with_session(_redirect(_draft_url(draft)), session_id)
    message, notice_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "scenarios.html",
        {
            "scenarios": summaries,
            "draft": draft,
            "search": search_text,
            "message": message,
            "error": notice_error,
        },
    )
    return _with_session(response, session_id)


@router.post("/scenarios/drafts/{token}")
async def update_scenario_draft(token: str, request: Request) -> Response:
    session_id, session = _session(request)
    draft, expired = _draft_or_redirect(request, session_id, token)
    if expired is not None or draft is None:
        assert expired is not None
        return expired
    form = await request.form(max_fields=MAX_EDITOR_FORM_FIELDS)
    update_draft_from_form(draft, form)
    collection_action = str(form.get("collection_action", ""))
    if collection_action:
        if not apply_collection_action(draft, collection_action):
            session.notify("The requested collection change is invalid.", error=True)
        return _with_session(_redirect(_draft_url(draft)), session_id)
    intent = str(form.get("intent", ""))
    if intent == "refresh":
        clear_draft_errors(draft)
        return _with_session(_redirect(_draft_url(draft)), session_id)
    if intent == "discard":
        if draft.original_id is None:
            _replace_draft_scenario(
                draft,
                minimal_scenario(),
                resource_id="",
                original_id=None,
                original_hash="",
            )
        else:
            try:
                discarded_scenario = _library(request).get(draft.original_id)
            except ScenarioLibraryError as error:
                session.notify(
                    f"Could not discard changes: {error}",
                    error=True,
                )
                return _with_session(_redirect(_draft_url(draft)), session_id)
            _replace_draft_scenario(
                draft,
                discarded_scenario,
                resource_id=draft.original_id,
                original_id=draft.original_id,
                original_hash=scenario_content_hash(discarded_scenario),
            )
        session.notify("Discarded unsaved scenario changes.")
        return _with_session(_redirect(_draft_url(draft)), session_id)
    scenario = validate_draft(draft)
    if scenario is None:
        return _with_session(_redirect(_draft_url(draft)), session_id)
    if intent == "save":
        try:
            validate_scenario_id(draft.resource_id)
            saved = _save_draft(_library(request), draft, scenario)
        except ScenarioLibraryError as error:
            session.notify(f"Could not save scenario: {error}", error=True)
            return _with_session(_redirect(_draft_url(draft)), session_id)
        _replace_draft_scenario(
            draft,
            saved,
            resource_id=draft.resource_id,
            original_id=draft.resource_id,
            original_hash=scenario_content_hash(saved),
        )
        session.notify(f"Saved {saved.name}. The staged scenario and active run were unchanged.")
        return _with_session(_redirect(_draft_url(draft)), session_id)
    if intent == "stage":
        try:
            _stage_scenario(
                _manager(request),
                session,
                scenario,
                f"scenario editor draft {draft.resource_id or '(unsaved)'}",
            )
        except (ValidationError, ValueError) as error:
            message = (
                _validation_message(error) if isinstance(error, ValidationError) else str(error)
            )
            if session.scenario is scenario and session.scenario_id is None:
                session.notify(
                    f"Scenario loaded but not staged: {message}. "
                    "Choose eligible character assignments.",
                    error=True,
                )
                return _with_session(_redirect("/ui/"), session_id)
            draft.root.errors.append(message)
            draft.errors.append(
                ScenarioEditorError(
                    message=message,
                    control_id=draft.root.control_id,
                )
            )
            return _with_session(_redirect(_draft_url(draft)), session_id)
        return _with_session(_redirect("/ui/"), session_id)
    session.notify("Choose Save scenario or Validate and stage.", error=True)
    return _with_session(_redirect(_draft_url(draft)), session_id)


def _save_draft(
    library: ScenarioLibrary,
    draft: ScenarioEditorDraft,
    scenario: ScenarioDefinition,
) -> ScenarioDefinition:
    if draft.original_id is None:
        return library.create(draft.resource_id, scenario)
    current_id = draft.original_id
    expected_hash = draft.original_hash
    if draft.resource_id != current_id:
        renamed = library.rename(
            current_id,
            draft.resource_id,
            expected_hash,
        )
        expected_hash = scenario_content_hash(renamed)
    return library.update(
        draft.resource_id,
        scenario,
        expected_hash,
    )


@router.post("/scenarios/{scenario_id}/duplicate")
async def duplicate_scenario(scenario_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    expected_hash = str(form.get("expected_hash", ""))
    library = _library(request)
    target = f"/ui/scenarios/?selected={quote(scenario_id)}"
    try:
        source = library.get(scenario_id)
        if scenario_content_hash(source) != expected_hash:
            raise ScenarioConflictError(f"scenario changed since it was loaded: {scenario_id}")
        next_id = _unique_scenario_id(library, f"{scenario_id}-copy")
        raw = source.model_dump(mode="python")
        raw["name"] = f"{source.name} Copy"
        duplicate = ScenarioDefinition.model_validate(raw)
        library.create(next_id, duplicate)
        session.notify(f"Duplicated {scenario_id} as {next_id}.")
        target = f"/ui/scenarios/?selected={quote(next_id)}"
    except (ScenarioLibraryError, ValidationError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Could not duplicate scenario: {message}", error=True)
    return _with_session(_redirect(target), session_id)


@router.post("/scenarios/{scenario_id}/delete")
async def delete_scenario(scenario_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        if form.get("confirm") != "yes":
            raise ValueError("confirm deletion before continuing")
        _library(request).delete(
            scenario_id,
            str(form.get("expected_hash", "")),
        )
        session.notify(f"Deleted {scenario_id}.")
        target = "/ui/scenarios/"
    except (ScenarioLibraryError, ValueError) as error:
        session.notify(f"Could not delete scenario: {error}", error=True)
        target = f"/ui/scenarios/?selected={quote(scenario_id)}"
    return _with_session(_redirect(target), session_id)


@router.post("/scenarios/import")
async def import_scenario(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form(max_part_size=MAX_UPLOAD_BYTES + 1)
    upload = form.get("scenario")
    target = "/ui/scenarios/"
    if not isinstance(upload, UploadFile) or not upload.filename:
        session.notify("Choose a scenario JSON file.", error=True)
        return _with_session(_redirect(target), session_id)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    try:
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("scenario files must be 5 MB or smaller")
        scenario_id = Path(upload.filename).stem
        validate_scenario_id(scenario_id)
        scenario = ScenarioDefinition.model_validate_json(content)
        _library(request).create(scenario_id, scenario)
        session.notify(f"Imported {scenario.name} as {scenario_id}.")
        target = f"/ui/scenarios/?selected={quote(scenario_id)}"
    except (ScenarioLibraryError, ValidationError, ValueError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Could not import scenario: {message}", error=True)
    return _with_session(_redirect(target), session_id)


@router.get("/scenarios/{scenario_id}/download")
async def download_scenario(scenario_id: str, request: Request) -> Response:
    try:
        scenario = _library(request).get(scenario_id)
    except ScenarioLibraryError as error:
        return Response(str(error), status_code=404, media_type="text/plain")
    payload = json.dumps(
        scenario.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return Response(
        f"{payload}\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{scenario_id}.json"'},
    )


@router.post("/scenario/library/stage")
async def stage_saved_scenario(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    scenario_id = str(form.get("scenario_id", "")).strip()
    try:
        if not scenario_id:
            raise ValueError("select a saved scenario")
        scenario = _library(request).get(scenario_id)
        _stage_scenario(
            _manager(request),
            session,
            scenario,
            f"saved scenario {scenario_id}",
        )
    except (ScenarioLibraryError, ValidationError, ValueError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Could not stage saved scenario: {message}", error=True)
    return _with_session(_redirect("/ui/"), session_id)


@router.get("/scenario/library/edit")
async def edit_saved_scenario(request: Request) -> Response:
    session_id, session = _session(request)
    scenario_id = request.query_params.get("scenario_id", "").strip()
    if not scenario_id:
        session.notify("Select a saved scenario to edit.", error=True)
        return _with_session(_redirect("/ui/"), session_id)
    target = f"/ui/scenarios/?selected={quote(scenario_id)}"
    return _with_session(_redirect(target), session_id)


def _unique_scenario_id(library: ScenarioLibrary, base: str) -> str:
    existing = {item.id for item in library.list()}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
