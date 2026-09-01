from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from stage0_sim.api.scenario_editor_world import (
    build_editor_world,
)
from stage0_sim.api.scenario_forms import (
    ScenarioEditorDraft,
    ScenarioEditorDraftStore,
    ScenarioEditorError,
    ScenarioEditorNode,
    apply_collection_action,
    clear_draft_errors,
    encode_draft_value,
    find_collection_membership,
    find_node,
    find_node_by_path,
    minimal_scenario,
    node_from_value,
    refresh_node_paths,
    update_draft_from_form,
    validate_draft,
)
from stage0_sim.api.ui import (
    MAX_MAP_ZOOM,
    MAX_UPLOAD_BYTES,
    MIN_MAP_ZOOM,
    _manager,
    _redirect,
    _session,
    _stage_scenario,
    _validation_message,
    _with_session,
    templates,
)
from stage0_sim.application.element_library import (
    ElementLibrary,
    ElementLibraryError,
)
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)
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


def _elements(request: Request) -> ElementLibrary:
    return cast(ElementLibrary, request.app.state.element_library)


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
    scenario: ScenarioSourceDefinition,
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
    draft.view.selected_node_id = ""
    draft.view.scope_node_id = ""
    draft.view.zoom = 1.0
    draft.view.camera_x = 0.5
    draft.view.camera_y = 0.5


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
    _apply_editor_query(draft, request)
    element_library = _elements(request)
    try:
        building_elements = element_library.list(ElementKind.BUILDING)
    except ElementLibraryError as error:
        session.notify(f"Could not list building elements: {error}", error=True)
        building_elements = ()
    presentation = build_editor_world(draft, element_library)
    membership = (
        find_collection_membership(draft.root, presentation.selected_node.id)
        if presentation.selected_node is not None
        else None
    )
    message, notice_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "scenarios.html",
        {
            "scenarios": summaries,
            "draft": draft,
            "editor_world": presentation,
            "selected_membership": membership,
            "search": search_text,
            "message": message,
            "error": notice_error,
            "building_elements": building_elements,
            "inherited_building": _inherited_building_summary(
                draft,
                presentation.selected_node,
                element_library,
            ),
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
    building_action = str(form.get("building_action", ""))
    if building_action:
        try:
            _apply_building_action(
                draft,
                building_action,
                _elements(request),
            )
        except (ElementLibraryError, ValueError) as error:
            session.notify(
                f"Could not update building instance: {error}",
                error=True,
            )
        return _with_session(_redirect(_draft_url(draft)), session_id)
    collection_action = str(form.get("collection_action", ""))
    if collection_action:
        collection_id = collection_action.split(":", 2)[1] if ":" in collection_action else ""
        collection = find_node(draft.root, collection_id)
        selected_before = draft.view.selected_node_id
        if not apply_collection_action(draft, collection_action):
            session.notify("The requested collection change is invalid.", error=True)
        elif collection_action.startswith("add:") and collection is not None:
            draft.view.selected_node_id = collection.items[-1].id
        elif selected_before and find_node(draft.root, selected_before) is None:
            draft.view.selected_node_id = collection.id if collection is not None else ""
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
    source = validate_draft(draft)
    if source is None:
        return _with_session(_redirect(_draft_url(draft)), session_id)
    if intent == "save":
        try:
            validate_scenario_id(draft.resource_id)
            saved = _save_draft(_library(request), draft, source)
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
        resolved = None
        try:
            resolved = resolve_scenario(source, _elements(request))
            await _stage_scenario(
                _manager(request),
                session,
                resolved.scenario,
                f"scenario editor draft {draft.resource_id or '(unsaved)'}",
                scenario_source=source.model_dump(mode="json"),
                resolved_elements=resolved.provenance_payload(),
            )
        except (ScenarioResolutionError, ValidationError, ValueError) as error:
            message = (
                _validation_message(error) if isinstance(error, ValidationError) else str(error)
            )
            if (
                resolved is not None
                and session.scenario is resolved.scenario
                and session.scenario_id is None
            ):
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


@router.post("/scenarios/drafts/{token}/view")
async def update_scenario_editor_view(token: str, request: Request) -> Response:
    session_id, _session_state = _session(request)
    draft, expired = _draft_or_redirect(request, session_id, token)
    if expired is not None or draft is None:
        assert expired is not None
        return expired
    form = await request.form()
    try:
        zoom_action = str(form.get("zoom_action", ""))
        if zoom_action == "in":
            draft.view.zoom = min(MAX_MAP_ZOOM, draft.view.zoom * 1.25)
        elif zoom_action == "out":
            draft.view.zoom = max(MIN_MAP_ZOOM, draft.view.zoom / 1.25)
        elif zoom_action == "fit":
            draft.view.zoom = 1.0
            draft.view.camera_x = 0.5
            draft.view.camera_y = 0.5
        elif "zoom" in form:
            zoom = float(str(form.get("zoom", "")))
            if not MIN_MAP_ZOOM <= zoom <= MAX_MAP_ZOOM:
                raise ValueError(
                    f"zoom must be between {MIN_MAP_ZOOM} and {MAX_MAP_ZOOM}"
                )
            draft.view.zoom = zoom
            draft.view.camera_x = _fraction(form.get("camera_x"), draft.view.camera_x)
            draft.view.camera_y = _fraction(form.get("camera_y"), draft.view.camera_y)
    except ValueError as error:
        return _with_session(
            Response(str(error), status_code=400, media_type="text/plain"),
            session_id,
        )
    if "zoom" in form:
        return _with_session(Response(status_code=204), session_id)
    return _with_session(_redirect(_draft_url(draft)), session_id)


def _apply_editor_query(draft: ScenarioEditorDraft, request: Request) -> None:
    if "focus" in request.query_params:
        focus = request.query_params.get("focus", "")
        draft.view.selected_node_id = focus if find_node(draft.root, focus) else ""
    if "scope" in request.query_params:
        scope = request.query_params.get("scope", "")
        draft.view.scope_node_id = scope if find_node(draft.root, scope) else ""
        if scope and not draft.view.selected_node_id:
            draft.view.selected_node_id = draft.view.scope_node_id


def _fraction(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    parsed = float(str(value))
    if not 0 <= parsed <= 1:
        raise ValueError("camera coordinates must be between 0 and 1")
    return parsed


def _save_draft(
    library: ScenarioLibrary,
    draft: ScenarioEditorDraft,
    scenario: ScenarioSourceDefinition,
) -> ScenarioSourceDefinition:
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
        duplicate = ScenarioSourceDefinition.model_validate(raw)
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
        raw = json.loads(content)
        if not isinstance(raw, dict) or raw.get("schema_version") != 3:
            raise ValueError(
                "saved scenarios require schema version 3; "
                "schema-version-2 imports are not supported"
            )
        scenario = ScenarioSourceDefinition.model_validate(raw)
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
        source = _library(request).get(scenario_id)
        resolved = resolve_scenario(source, _elements(request))
        await _stage_scenario(
            _manager(request),
            session,
            resolved.scenario,
            f"saved scenario {scenario_id}",
            scenario_source=source.model_dump(mode="json"),
            resolved_elements=resolved.provenance_payload(),
        )
    except (
        ScenarioLibraryError,
        ScenarioResolutionError,
        ValidationError,
        ValueError,
    ) as error:
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


def _apply_building_action(
    draft: ScenarioEditorDraft,
    action: str,
    library: ElementLibrary,
) -> None:
    parts = action.split(":")
    if len(parts) == 2 and parts[0] == "reset":
        building_node = find_node(draft.root, parts[1])
        if building_node is None or "buildings" not in building_node.path:
            raise ValueError("select a valid building instance")
        overrides_index = next(
            (
                index
                for index, child in enumerate(building_node.children)
                if child.schema.field_name == "overrides"
            ),
            None,
        )
        if overrides_index is None:
            raise ValueError("building overrides are unavailable")
        overrides_schema = building_node.children[overrides_index].schema
        building_node.children[overrides_index] = node_from_value(
            overrides_schema,
            {},
        )
        refresh_node_paths(draft.root)
        draft.view.selected_node_id = building_node.id
        return
    if len(parts) != 3 or parts[0] != "add":
        raise ValueError("invalid building action")
    zone_node = find_node(draft.root, parts[1])
    if zone_node is None or zone_node.path[-2:-1] != ("city_zones",):
        raise ValueError("select a valid city zone")
    building_element = library.get(parts[2], ElementKind.BUILDING)
    if not isinstance(building_element, BuildingElementDefinition):
        raise ValueError("selected element is not a building")
    buildings = next(
        (
            child
            for child in zone_node.children
            if child.schema.field_name == "buildings"
        ),
        None,
    )
    if buildings is None or buildings.schema.item is None:
        raise ValueError("city zone building collection is unavailable")
    raw, issues = encode_draft_value(draft)
    if issues:
        raise ValueError("apply valid city-zone values before adding a building")
    zone_value = _value_at_path(raw, zone_node.path)
    center = (
        zone_value.get("center")
        if isinstance(zone_value, dict)
        and isinstance(zone_value.get("center"), dict)
        else {"x": 0.0, "y": 0.0}
    )
    existing_ids = {
        str(item.get("id"))
        for zone in (
            raw.get("world", {}).get("city_zones", [])
            if isinstance(raw.get("world"), dict)
            else []
        )
        if isinstance(zone, dict)
        for item in zone.get("buildings", [])
        if isinstance(item, dict)
    }
    instance_id = building_element.id
    suffix = 2
    while instance_id in existing_ids:
        instance_id = f"{building_element.id}-{suffix}"
        suffix += 1
    entrance_node_ids = {
        entrance.key: f"node-{instance_id}-{entrance.key}"
        for entrance in building_element.entrances
    }
    instance = node_from_value(
        buildings.schema.item,
        {
            "id": instance_id,
            "element": {
                "kind": ElementKind.BUILDING.value,
                "id": building_element.id,
                "content_hash": element_content_hash(building_element),
            },
            "city_position": center,
            "entrance_node_ids": entrance_node_ids,
            "overrides": {},
        },
    )
    buildings.items.append(instance)
    transport_nodes = find_node_by_path(
        draft.root,
        ("world", "transport", "nodes"),
    )
    if transport_nodes is not None and transport_nodes.schema.item is not None:
        for node_id in entrance_node_ids.values():
            transport_nodes.items.append(
                node_from_value(
                    transport_nodes.schema.item,
                    {
                        "id": node_id,
                        "kind": "BUILDING_ENTRANCE",
                        "position": center,
                        "place_id": instance_id,
                    },
                )
            )
    refresh_node_paths(draft.root)
    draft.view.selected_node_id = instance.id


def _value_at_path(value: object, path: tuple[str | int, ...]) -> object:
    current = value
    for part in path:
        if isinstance(part, int) and isinstance(current, list):
            current = current[part]
        elif isinstance(part, str) and isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _inherited_building_summary(
    draft: ScenarioEditorDraft,
    selected: ScenarioEditorNode | None,
    library: ElementLibrary,
) -> dict[str, object] | None:
    if selected is None:
        return None
    path = selected.path
    if "buildings" not in path:
        return None
    building_index = path.index("buildings") + 1
    building_path = path[: building_index + 1]
    building_node = find_node_by_path(draft.root, building_path)
    if building_node is None:
        return None
    raw, _issues = encode_draft_value(draft)
    instance = _value_at_path(raw, building_path)
    if not isinstance(instance, dict) or not isinstance(instance.get("element"), dict):
        return None
    reference = instance["element"]
    element_id = str(reference.get("id", ""))
    expected_hash = str(reference.get("content_hash", ""))
    result: dict[str, object] = {
        "node_id": building_node.id,
        "instance_id": str(instance.get("id", "")),
        "element_id": element_id,
        "expected_hash": expected_hash,
        "rooms": [],
    }
    try:
        element = library.get(element_id, ElementKind.BUILDING)
        if not isinstance(element, BuildingElementDefinition):
            raise ElementLibraryError(f"element {element_id} is not a building")
        actual_hash = element_content_hash(element)
        result.update(
            {
                "name": element.name,
                "description": element.description,
                "actual_hash": actual_hash,
                "hash_matches": actual_hash == expected_hash,
            }
        )
        rooms: list[dict[str, object]] = []
        for placement in element.rooms:
            room_value = library.get(placement.element.id, ElementKind.ROOM)
            if not isinstance(room_value, RoomElementDefinition):
                continue
            objects: list[dict[str, object]] = []
            for object_placement in room_value.objects:
                object_value = library.get(
                    object_placement.element.id,
                    ElementKind.OBJECT,
                )
                if not isinstance(object_value, ObjectElementDefinition):
                    continue
                npc_name = ""
                if object_value.npc_role is not None:
                    npc_value = library.get(
                        object_value.npc_role.id,
                        ElementKind.NPC_ROLE,
                    )
                    if isinstance(npc_value, NpcRoleElementDefinition):
                        npc_name = npc_value.name
                objects.append(
                    {
                        "key": object_placement.key,
                        "id": object_value.id,
                        "name": object_value.name,
                        "kind": object_value.object_type,
                        "npc_name": npc_name,
                    }
                )
            rooms.append(
                {
                    "key": placement.key,
                    "id": room_value.id,
                    "name": room_value.name,
                    "room_type": room_value.room_type,
                    "objects": objects,
                }
            )
        result["rooms"] = rooms
    except ElementLibraryError as error:
        result["error"] = str(error)
    return result


def _unique_scenario_id(library: ScenarioLibrary, base: str) -> str:
    existing = {item.id for item in library.list()}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
