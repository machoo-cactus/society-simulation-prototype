import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from stage0_sim.application.characters import (
    CharacterDefinition,
    CharacterLibrary,
    CharacterLibraryError,
    character_content_hash,
)
from stage0_sim.application.manager import (
    ManagedRun,
    SimulationConflictError,
    SimulationManager,
    SimulationNotFoundError,
)
from stage0_sim.application.runner import RunnerStatus
from stage0_sim.application.scenario import (
    CityWorldDefinition,
    ScenarioDefinition,
    WorldDefinition,
)
from stage0_sim.application.telemetry import (
    build_agent_snapshot,
    build_ui_bootstrap,
    build_world_snapshot,
)
from stage0_sim.domain.events import DomainEvent, JsonValue

router = APIRouter(prefix="/ui", tags=["operator-ui"], include_in_schema=False)
WEB_DIRECTORY = Path(__file__).parents[1] / "web"
templates = Jinja2Templates(directory=WEB_DIRECTORY)
SESSION_COOKIE = "stage0_operator_session"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

EVENT_FILTERS: dict[str, re.Pattern[str]] = {
    "system1": re.compile(r"^(system1\.|threshold\.breached$)"),
    "actions": re.compile(r"^(plan|planner|affordance|activity|agent|path)\."),
    "dialogue": re.compile(r"^(dialogue|speech)\."),
    "cognition": re.compile(r"^(cognition|tool)\."),
    "perception": re.compile(r"^perception\."),
    "travel": re.compile(r"^(travel|building|vehicle|metro)\."),
    "errors": re.compile(r"(failed|blocked|cancelled|error|rejected)"),
    "ui": re.compile(r"^simulation\."),
}

CHARACTER_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "identity",
        "title": "Identity",
        "fields": (
            ("display_name", "Display name", "text", True),
            ("age", "Age", "number", False),
            ("gender", "Gender", "text", False),
            ("pronouns", "Pronouns", "text", False),
            ("occupation", "Occupation", "text", False),
        ),
    },
    {
        "id": "appearance",
        "title": "Appearance",
        "fields": (
            ("summary", "Summary", "textarea", False),
            ("height", "Height", "text", False),
            ("build", "Build", "text", False),
            ("hair", "Hair", "text", False),
            ("eyes", "Eyes", "text", False),
            ("clothing", "Clothing", "textarea", False),
            (
                "distinguishing_features",
                "Distinguishing features",
                "list",
                False,
            ),
        ),
    },
    {
        "id": "personality",
        "title": "Personality",
        "fields": (
            ("summary", "Summary", "textarea", False),
            ("traits", "Traits", "list", False),
            ("temperament", "Temperament", "text", False),
            ("social_style", "Social style", "text", False),
            ("speech_style", "Speech style", "textarea", False),
            ("strengths", "Strengths", "list", False),
            ("flaws", "Flaws", "list", False),
        ),
    },
    {
        "id": "background",
        "title": "Background",
        "fields": (
            ("birthplace", "Birthplace", "text", False),
            ("residence", "Residence", "text", False),
            ("education", "Education", "textarea", False),
            ("history", "History", "textarea", False),
        ),
    },
    {
        "id": "motivations",
        "title": "Motivations",
        "fields": (
            ("values", "Values", "list", False),
            ("goals", "Goals", "list", False),
            ("fears", "Fears", "list", False),
            ("needs", "Needs", "list", False),
            ("current_priorities", "Current priorities", "list", False),
        ),
    },
    {
        "id": "capabilities",
        "title": "Capabilities",
        "fields": (
            ("skills", "Skills", "list", False),
            ("knowledge_areas", "Knowledge areas", "list", False),
            ("limitations", "Limitations", "list", False),
        ),
    },
    {
        "id": "preferences",
        "title": "Preferences",
        "fields": (
            ("likes", "Likes", "list", False),
            ("dislikes", "Dislikes", "list", False),
            ("habits", "Habits", "list", False),
            ("routines", "Routines", "list", False),
        ),
    },
)


@dataclass(slots=True)
class OperatorSession:
    scenario: ScenarioDefinition | None = None
    scenario_id: str | None = None
    scenario_source: str = ""
    scenario_warnings: tuple[str, ...] = ()
    run_id: str | None = None
    selected_agent_id: str | None = None
    view_level: str = "auto"
    zoom: float = 1.0
    overlays: set[str] = field(
        default_factory=lambda: {
            "names",
            "paths",
            "speech",
            "vision",
            "hearing",
        }
    )
    live_refresh: bool = True
    event_start_index: int = 0
    message: str = ""
    error: str = ""

    def notify(self, message: str, *, error: bool = False) -> None:
        self.message = "" if error else message
        self.error = message if error else ""

    def consume_notice(self) -> tuple[str, str]:
        notice = (self.message, self.error)
        self.message = ""
        self.error = ""
        return notice


class OperatorSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, OperatorSession] = {}

    def get(self, session_id: str | None) -> tuple[str, OperatorSession]:
        if session_id is not None and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        next_id = uuid4().hex
        session = OperatorSession()
        self._sessions[next_id] = session
        return next_id, session


def _manager(request: Request) -> SimulationManager:
    return cast(SimulationManager, request.app.state.simulation_manager)


def _library(request: Request) -> CharacterLibrary:
    return cast(CharacterLibrary, request.app.state.character_library)


def _session(request: Request) -> tuple[str, OperatorSession]:
    store = cast(OperatorSessionStore, request.app.state.operator_sessions)
    return store.get(request.cookies.get(SESSION_COOKIE))


def _redirect(path: str = "/ui/") -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _with_session(response: Response, session_id: str) -> Response:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
    )
    return response


def _managed_run(
    manager: SimulationManager, session: OperatorSession
) -> ManagedRun | None:
    if session.run_id is None:
        return None
    try:
        return manager.get_run(session.run_id)
    except SimulationNotFoundError:
        session.run_id = None
        return None


def _validation_message(error: ValidationError) -> str:
    problems = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        problems.append(f"{location}: {item['msg']}")
    return "; ".join(problems)


def _stage_scenario(
    manager: SimulationManager,
    session: OperatorSession,
    scenario: ScenarioDefinition,
    source: str,
) -> None:
    scenario_id = manager.add_scenario(scenario)
    prepared = manager.get_scenario(scenario_id)
    session.scenario = scenario
    session.scenario_id = scenario_id
    session.scenario_source = source
    session.scenario_warnings = prepared.warnings
    session.selected_agent_id = scenario.entities[0].id if scenario.entities else None
    session.notify(
        f"{scenario.name} is validated and staged. Starting remains a separate action."
    )


@router.get("/", response_class=HTMLResponse, name="operator")
async def operator_page(request: Request) -> Response:
    session_id, session = _session(request)
    requested_agent = request.query_params.get("selected")
    if requested_agent:
        session.selected_agent_id = requested_agent
    manager = _manager(request)
    managed = _managed_run(manager, session)
    snapshot: dict[str, JsonValue] | None = None
    bootstrap: dict[str, JsonValue] | None = None
    agents: list[dict[str, JsonValue]] = []
    all_events: list[DomainEvent] = []
    status = "not started"
    if managed is not None:
        snapshot = build_world_snapshot(managed.runner)
        bootstrap = build_ui_bootstrap(managed.runner)
        agents = [
            build_agent_snapshot(managed.runner, entity_id)
            for entity_id in managed.runner.registry.entities()
        ]
        all_events = list(managed.runner.events.events)
        status = managed.runner.status.value
    session.event_start_index = min(session.event_start_index, len(all_events))
    events = all_events[session.event_start_index :]
    if agents and session.selected_agent_id not in {
        str(agent["id"]) for agent in agents
    }:
        session.selected_agent_id = str(agents[0]["id"])
    selected_agent = next(
        (
            agent
            for agent in agents
            if agent["id"] == session.selected_agent_id
        ),
        None,
    )
    event_filter = request.query_params.get("filter", "all")
    event_search = request.query_params.get("search", "").strip().casefold()
    event_order = request.query_params.get("order", "newest")
    try:
        event_limit = max(
            100, min(5000, int(request.query_params.get("limit", "500")))
        )
    except ValueError:
        event_limit = 500
    selected_event_id = request.query_params.get("event")
    filtered_events = _filter_events(events, event_filter, event_search)
    if event_order != "oldest":
        filtered_events.reverse()
        event_order = "newest"
    selected_event = next(
        (event for event in events if event.event_id == selected_event_id),
        None,
    )
    message, error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "simulation.html",
        {
            "session": session,
            "scenario": session.scenario,
            "characters": _library(request).list(),
            "managed": managed,
            "snapshot": snapshot,
            "bootstrap": bootstrap,
            "agents": agents,
            "selected_agent": selected_agent,
            "status": status,
            "controls": _control_availability(managed, session.scenario_id),
            "world_view": _world_view(
                session,
                session.scenario,
                snapshot,
                bootstrap,
                selected_agent,
                events,
            ),
            "events": filtered_events[:event_limit],
            "event_total": len(events),
            "event_shown": min(len(filtered_events), event_limit),
            "event_limit": event_limit,
            "has_older_events": len(filtered_events) > event_limit,
            "event_filter": event_filter,
            "event_search": request.query_params.get("search", ""),
            "event_order": event_order,
            "selected_event": selected_event,
            "transcript": _transcript(events, agents),
            "recent_perceptions": _recent_perceptions(
                events, session.selected_agent_id, agents
            ),
            "message": message,
            "error": error,
            "refresh_url": str(request.url),
            "auto_refresh": (
                managed is not None
                and managed.runner.status is RunnerStatus.RUNNING
                and session.live_refresh
            ),
        },
    )
    return _with_session(response, session_id)


@router.get("/index.html")
async def legacy_operator_path() -> RedirectResponse:
    return _redirect("/ui/")


@router.get("/characters.html")
async def legacy_character_path() -> RedirectResponse:
    return _redirect("/ui/characters/")


@router.get("/demo.json")
async def bundled_demo() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "demo.json", media_type="application/json")


@router.post("/scenario/example")
async def load_example(request: Request) -> Response:
    session_id, session = _session(request)
    try:
        scenario = ScenarioDefinition.model_validate_json(
            (WEB_DIRECTORY / "demo.json").read_text(encoding="utf-8")
        )
        _stage_scenario(_manager(request), session, scenario, "bundled example")
    except (OSError, ValidationError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Could not load example: {message}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/scenario/upload")
async def upload_scenario(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    upload = form.get("scenario")
    if not isinstance(upload, UploadFile) or not upload.filename:
        session.notify("Choose a scenario JSON file.", error=True)
        return _with_session(_redirect(), session_id)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        session.notify("Scenario files must be 5 MB or smaller.", error=True)
        return _with_session(_redirect(), session_id)
    try:
        scenario = ScenarioDefinition.model_validate_json(content)
        _stage_scenario(
            _manager(request),
            session,
            scenario,
            upload.filename,
        )
    except ValidationError as error:
        session.notify(
            f"Scenario is invalid: {_validation_message(error)}", error=True
        )
    except ValueError as error:
        session.notify(f"Scenario is invalid: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/scenario/assign")
async def assign_characters(request: Request) -> Response:
    session_id, session = _session(request)
    if session.scenario is None:
        session.notify("Load a scenario before assigning characters.", error=True)
        return _with_session(_redirect(), session_id)
    form = await request.form()
    raw = session.scenario.model_dump(mode="python")
    try:
        entities = cast(list[dict[str, Any]], raw["entities"])
        for entity in entities:
            components = cast(dict[str, Any], entity["components"])
            if "character_profile" not in components:
                continue
            character_id = str(form.get(f"character.{entity['id']}", "")).strip()
            if not character_id:
                raise ValueError(f"Select a character for {entity['id']}")
            components["character_profile"] = {"character_id": character_id}
        scenario = ScenarioDefinition.model_validate(raw)
        _stage_scenario(
            _manager(request),
            session,
            scenario,
            f"{session.scenario_source} with assigned characters",
        )
        session.notify("Character assignments were validated and staged.")
    except (ValidationError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Assignment invalid: {message}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/run/start")
async def start_run(request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    active = _managed_run(manager, session)
    if active is not None and active.runner.status is not RunnerStatus.STOPPED:
        session.notify("Stop the active run before starting another.", error=True)
        return _with_session(_redirect(), session_id)
    if session.scenario_id is None:
        session.notify("Load and validate a scenario first.", error=True)
        return _with_session(_redirect(), session_id)
    form = await request.form()
    try:
        speed = float(str(form.get("speed", session.scenario.speed if session.scenario else 1)))
        session.run_id = await manager.start_run(
            session.scenario_id,
            realtime=True,
            speed=speed,
        )
        session.event_start_index = 0
        session.notify("Simulation started.")
    except (ValueError, SimulationNotFoundError) as error:
        session.notify(f"Could not start simulation: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/run/control/{action}")
async def control_run(action: str, request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    if session.run_id is None:
        session.notify("There is no active run.", error=True)
        return _with_session(_redirect(), session_id)
    try:
        if action == "pause":
            manager.pause(session.run_id)
            message = "Simulation paused."
        elif action == "resume":
            manager.resume(session.run_id)
            message = "Simulation resumed."
        elif action == "step":
            await manager.step(session.run_id)
            message = "Advanced one deterministic tick."
        elif action == "stop":
            await manager.stop_run(session.run_id)
            message = "Simulation stopped. The staged scenario is ready to restart."
        else:
            session.notify(f"Unknown control action: {action}", error=True)
            return _with_session(_redirect(), session_id)
        session.notify(message)
    except (
        RuntimeError,
        SimulationConflictError,
        SimulationNotFoundError,
    ) as error:
        session.notify(f"{action.title()} failed: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/run/speed")
async def set_run_speed(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        if session.run_id is None:
            raise SimulationNotFoundError("there is no active run")
        speed = float(str(form.get("speed", "")))
        if speed <= 0:
            raise ValueError("speed must be greater than zero")
        _manager(request).set_speed(session.run_id, speed)
        session.notify(f"Simulation speed set to {speed:g}x.")
    except (ValueError, SimulationNotFoundError) as error:
        session.notify(f"Could not set speed: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/run/vitals")
async def mutate_vitals(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        if session.run_id is None or session.selected_agent_id is None:
            raise SimulationNotFoundError("select a character in an active run")
        values: dict[str, float] = {}
        for name in ("satiety", "energy", "stress"):
            raw = str(form.get(name, "")).strip()
            if not raw:
                continue
            value = float(raw)
            if not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
            values[name] = value
        if not values:
            raise ValueError("supply at least one vital")
        _manager(request).mutate_vitals(
            session.run_id, session.selected_agent_id, values
        )
        session.notify(f"Updated vitals for {session.selected_agent_id}.")
    except (
        ValueError,
        SimulationConflictError,
        SimulationNotFoundError,
    ) as error:
        session.notify(f"Could not mutate vitals: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/view")
async def update_view(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    selected_agent = str(form.get("selected_agent", "")).strip()
    if selected_agent:
        session.selected_agent_id = selected_agent
    level = str(form.get("view_level", session.view_level)).lower()
    if level in {"auto", "building", "neighborhood", "city"}:
        session.view_level = level
    zoom_action = str(form.get("zoom_action", ""))
    if zoom_action == "in":
        session.zoom = min(3.0, session.zoom * 1.25)
    elif zoom_action == "out":
        session.zoom = max(0.5, session.zoom / 1.25)
    elif zoom_action == "fit":
        session.zoom = 1.0
    session.live_refresh = form.get("live_refresh") == "on"
    overlays = form.getlist("overlays")
    if form.get("overlays_present") == "yes":
        session.overlays = {str(value) for value in overlays}
    return _with_session(_redirect(), session_id)


@router.post("/events/clear")
async def clear_events(request: Request) -> Response:
    session_id, session = _session(request)
    managed = _managed_run(_manager(request), session)
    session.event_start_index = (
        len(managed.runner.events.events) if managed is not None else 0
    )
    session.notify("Cleared the browser event and transcript view.")
    return _with_session(_redirect(), session_id)


@router.get("/characters/", response_class=HTMLResponse, name="character-library")
async def character_library_page(request: Request) -> Response:
    session_id, session = _session(request)
    library = _library(request)
    search = request.query_params.get("search", "").strip().casefold()
    selected_id = request.query_params.get("selected")
    creating = request.query_params.get("new") == "1"
    summaries = list(library.list())
    if search:
        summaries = [
            item
            for item in summaries
            if search in f"{item.id} {item.display_name}".casefold()
        ]
    selected: CharacterDefinition | None = None
    if creating:
        selected = None
    elif selected_id:
        try:
            selected = library.get(selected_id)
        except CharacterLibraryError as library_error:
            session.notify(str(library_error), error=True)
    elif summaries:
        selected = library.get(summaries[0].id)
    message, notice_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "characters.html",
        {
            "characters": summaries,
            "selected": selected,
            "selected_hash": (
                character_content_hash(selected) if selected is not None else ""
            ),
            "sections": CHARACTER_SECTIONS,
            "field_values": _character_field_values(selected),
            "search": request.query_params.get("search", ""),
            "message": message,
            "error": notice_error,
        },
    )
    return _with_session(response, session_id)


@router.post("/characters/create")
async def create_character(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        character = _character_from_form(form, None)
        _library(request).create(character)
        session.notify(f"Created {_character_display_name(character)}.")
        target = f"/ui/characters/?selected={quote(character.id)}"
    except (CharacterLibraryError, ValidationError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Could not create character: {message}", error=True)
        target = "/ui/characters/"
    return _with_session(_redirect(target), session_id)


@router.post("/characters/{character_id}/save")
async def save_character(character_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    library = _library(request)
    form = await request.form()
    expected_hash = str(form.get("expected_hash", ""))
    target_id = str(form.get("id", character_id)).strip()
    try:
        current = library.get(character_id)
        updated = _character_from_form(form, current)
        if target_id != character_id:
            renamed = library.rename(character_id, target_id, expected_hash)
            expected_hash = character_content_hash(renamed)
            updated = updated.model_copy(update={"id": target_id})
            saved = library.update(target_id, updated, expected_hash)
        else:
            saved = library.update(character_id, updated, expected_hash)
        session.notify(f"Saved {_character_display_name(saved)}.")
        target = f"/ui/characters/?selected={quote(saved.id)}"
    except (CharacterLibraryError, ValidationError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Could not save character: {message}", error=True)
        target = f"/ui/characters/?selected={quote(character_id)}"
    return _with_session(_redirect(target), session_id)


@router.post("/characters/{character_id}/duplicate")
async def duplicate_character(character_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    library = _library(request)
    try:
        current = library.get(character_id)
        next_id = _unique_character_id(library, f"{character_id}-copy")
        raw = current.model_dump(mode="python")
        raw["id"] = next_id
        identity = cast(dict[str, Any], raw["identity"])
        identity["display_name"] = f"{identity['display_name']} Copy"
        duplicate = CharacterDefinition.model_validate(raw)
        library.create(duplicate)
        session.notify(f"Duplicated {character_id} as {next_id}.")
        target = f"/ui/characters/?selected={quote(next_id)}"
    except (CharacterLibraryError, ValidationError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Could not duplicate character: {message}", error=True)
        target = f"/ui/characters/?selected={quote(character_id)}"
    return _with_session(_redirect(target), session_id)


@router.post("/characters/{character_id}/delete")
async def delete_character(character_id: str, request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        if form.get("confirm") != "yes":
            raise ValueError("confirm deletion before continuing")
        _library(request).delete(
            character_id, str(form.get("expected_hash", ""))
        )
        session.notify(f"Deleted {character_id}.")
        target = "/ui/characters/"
    except (CharacterLibraryError, ValueError) as error:
        session.notify(f"Could not delete character: {error}", error=True)
        target = f"/ui/characters/?selected={quote(character_id)}"
    return _with_session(_redirect(target), session_id)


@router.post("/characters/import")
async def import_character(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    upload = form.get("character")
    target = "/ui/characters/"
    if not isinstance(upload, UploadFile) or not upload.filename:
        session.notify("Choose a character JSON file.", error=True)
        return _with_session(_redirect(target), session_id)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    try:
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("character files must be 5 MB or smaller")
        character = CharacterDefinition.model_validate_json(content)
        _library(request).create(character)
        session.notify(f"Imported {_character_display_name(character)}.")
        target = f"/ui/characters/?selected={quote(character.id)}"
    except (CharacterLibraryError, ValidationError, ValueError) as error:
        message = (
            _validation_message(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        session.notify(f"Could not import character: {message}", error=True)
    return _with_session(_redirect(target), session_id)


@router.get("/characters/{character_id}/download")
async def download_character(character_id: str, request: Request) -> Response:
    try:
        character = _library(request).get(character_id)
    except CharacterLibraryError as error:
        return Response(str(error), status_code=404, media_type="text/plain")
    payload = json.dumps(
        character.model_dump(mode="json", exclude={"profile_ref"}),
        ensure_ascii=False,
        indent=2,
    )
    return Response(
        f"{payload}\n",
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{character_id}.json"'
        },
    )


def _character_from_form(
    form: Any,
    current: CharacterDefinition | None,
) -> CharacterDefinition:
    raw: dict[str, Any]
    if current is None:
        raw = {
            "schema_version": 1,
            "id": str(form.get("id", "")).strip(),
            "template_id": str(form.get("template_id", "human-v1")).strip(),
            "identity": {},
        }
    else:
        raw = current.model_dump(mode="python")
        raw["id"] = str(form.get("id", current.id)).strip()
        raw["template_id"] = str(
            form.get("template_id", current.template_id)
        ).strip()
    for section in CHARACTER_SECTIONS:
        section_id = cast(str, section["id"])
        values = cast(dict[str, Any], raw.setdefault(section_id, {}))
        for field_name, _label, field_type, _required in cast(
            tuple[tuple[str, str, str, bool], ...], section["fields"]
        ):
            text = str(form.get(f"{section_id}.{field_name}", "")).strip()
            if field_type == "list":
                values[field_name] = [
                    line.strip() for line in text.splitlines() if line.strip()
                ]
            elif field_type == "number":
                values[field_name] = int(text) if text else None
            else:
                values[field_name] = text
    raw["relationships"] = _parse_json_array(
        str(form.get("relationships", "[]")), "Relationships"
    )
    raw["custom_sections"] = _parse_json_array(
        str(form.get("custom_sections", "[]")), "Custom sections"
    )
    return CharacterDefinition.model_validate(raw)


def _parse_json_array(value: str, label: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must be valid JSON: {error}") from error
    if not isinstance(parsed, list):
        raise ValueError(f"{label} must be a JSON array")
    return parsed


def _character_field_values(
    character: CharacterDefinition | None,
) -> dict[str, str]:
    if character is None:
        return {}
    raw = character.model_dump(mode="json")
    values: dict[str, str] = {}
    for section in CHARACTER_SECTIONS:
        section_id = cast(str, section["id"])
        section_values = cast(dict[str, Any], raw.get(section_id, {}))
        for field_name, _label, field_type, _required in cast(
            tuple[tuple[str, str, str, bool], ...], section["fields"]
        ):
            value = section_values.get(field_name)
            if field_type == "list":
                values[f"{section_id}.{field_name}"] = "\n".join(value or [])
            elif value is not None:
                values[f"{section_id}.{field_name}"] = str(value)
    values["relationships"] = json.dumps(
        raw.get("relationships", []), ensure_ascii=False, indent=2
    )
    values["custom_sections"] = json.dumps(
        raw.get("custom_sections", []), ensure_ascii=False, indent=2
    )
    return values


def _unique_character_id(library: CharacterLibrary, base: str) -> str:
    existing = {item.id for item in library.list()}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _character_display_name(character: CharacterDefinition) -> str:
    return (
        character.identity.display_name
        if character.identity is not None
        else character.id
    )


def _control_availability(
    managed: ManagedRun | None, scenario_id: str | None
) -> dict[str, bool]:
    status = managed.runner.status if managed is not None else None
    return {
        "start": scenario_id is not None
        and (status is None or status is RunnerStatus.STOPPED),
        "pause": status is RunnerStatus.RUNNING,
        "resume": status is RunnerStatus.PAUSED,
        "step": status is RunnerStatus.PAUSED,
        "stop": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
        "speed": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
        "vitals": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
    }


def _filter_events(
    events: list[DomainEvent], event_filter: str, search: str
) -> list[DomainEvent]:
    pattern = EVENT_FILTERS.get(event_filter)
    filtered = []
    for event in events:
        if pattern is not None and pattern.search(event.event_type) is None:
            continue
        if search and search not in json.dumps(
            event.to_dict(), ensure_ascii=False
        ).casefold():
            continue
        filtered.append(event)
    return filtered


def event_summary(event: DomainEvent) -> str:
    preferred = (
        "text",
        "tool_name",
        "action",
        "target_id",
        "recipient_ids",
        "drive",
        "station_id",
        "reason",
        "current",
        "message",
        "provider",
        "latency_ms",
    )
    details = [
        f"{key}={event.payload[key]}"
        for key in preferred
        if key in event.payload and event.payload[key] is not None
    ]
    if details:
        return ", ".join(details)
    serialized = json.dumps(event.payload, ensure_ascii=False)
    return serialized if len(serialized) <= 240 else f"{serialized[:237]}..."


def _transcript(
    events: list[DomainEvent], agents: list[dict[str, JsonValue]]
) -> list[dict[str, str]]:
    names = {
        str(agent["id"]): str(
            cast(dict[str, JsonValue], agent.get("character_profile", {})).get(
                "display_name", agent["id"]
            )
        )
        for agent in agents
    }
    rows = []
    for event in events:
        if not event.event_type.startswith(("speech.", "dialogue.")):
            continue
        text = event.payload.get("text")
        if not isinstance(text, str) or not text:
            continue
        speaker = names.get(event.agent_id or "", event.agent_id or "Unknown")
        rows.append(
            {
                "speaker": speaker,
                "text": text,
                "meta": f"tick {event.simulation_tick} · {event.event_type}",
                "event_id": event.event_id,
            }
        )
    return rows[-100:]


def _recent_perceptions(
    events: list[DomainEvent],
    agent_id: str | None,
    agents: list[dict[str, JsonValue]],
) -> list[str]:
    if agent_id is None:
        return []
    names = {
        str(agent["id"]): str(
            cast(dict[str, JsonValue], agent.get("character_profile", {})).get(
                "display_name", agent["id"]
            )
        )
        for agent in agents
    }
    rows: list[str] = []
    for event in reversed(events):
        if event.event_type == "perception.delivered" and event.agent_id == agent_id:
            modality = event.payload.get("modality", "unknown")
            subject = event.payload.get("subject_id")
            subject_name = names.get(str(subject), str(subject)) if subject else "event"
            rows.append(f"tick {event.simulation_tick}: {modality} · {subject_name}")
        elif event.event_type == "speech.delivered":
            recipients = event.payload.get("recipient_ids", [])
            if isinstance(recipients, list) and agent_id in recipients:
                speaker = names.get(event.agent_id or "", event.agent_id or "Unknown")
                rows.append(
                    f'tick {event.simulation_tick}: heard {speaker}: '
                    f'"{event.payload.get("text", "")}"'
                )
        if len(rows) >= 20:
            break
    return rows


def _world_view(
    session: OperatorSession,
    scenario: ScenarioDefinition | None,
    snapshot: dict[str, JsonValue] | None,
    bootstrap: dict[str, JsonValue] | None,
    selected_agent: dict[str, JsonValue] | None,
    events: list[DomainEvent],
) -> dict[str, Any] | None:
    if snapshot is None or bootstrap is None:
        return _scenario_world_view(session, scenario)
    static_world = bootstrap.get("world")
    city = bootstrap.get("city")
    agents = cast(list[dict[str, JsonValue]], snapshot.get("agents", []))
    level = session.view_level
    selected_location = (
        cast(dict[str, JsonValue], selected_agent.get("spatial_location", {}))
        if selected_agent
        else {}
    )
    if level == "auto":
        level = (
            "building"
            if selected_location.get("scale") == "BUILDING"
            else "city" if city else "building"
        )
    overlays = _world_overlays(agents, events, session.selected_agent_id)
    city_world_value = scenario.world if scenario is not None else None
    if level == "building" and isinstance(
        city_world_value, CityWorldDefinition
    ):
        city_world = city_world_value
        place_id = selected_location.get("place_id")
        building = next(
            (
                item
                for item in city_world.buildings
                if item.id == place_id
            ),
            None,
        )
        if building is not None:
            local_world = city_world.local_maps[building.local_map_id].model_dump(
                mode="json"
            )
            local_agents = [
                agent
                for agent in agents
                if isinstance(agent.get("spatial_location"), dict)
                and cast(
                    dict[str, JsonValue], agent["spatial_location"]
                ).get("place_id")
                == building.id
            ]
            return _grid_view(
                local_world,
                local_agents,
                session,
                f"Building view · {building.name}",
                overlays,
            )
    if level == "building" and isinstance(static_world, dict):
        return _grid_view(
            static_world, agents, session, "Building view", overlays
        )
    if isinstance(city, dict):
        city_payload = _neighborhood_payload(
            city, selected_location
        ) if level == "neighborhood" else city
        vehicle_states = (
            cast(dict[str, JsonValue], snapshot.get("world", {})).get(
                "vehicle_states", []
            )
        )
        return _city_view(
            city_payload,
            agents,
            session,
            level.title(),
            overlays,
            cast(list[dict[str, JsonValue]], vehicle_states)
            if isinstance(vehicle_states, list)
            else [],
        )
    if isinstance(static_world, dict):
        return _grid_view(static_world, agents, session, "World view", overlays)
    return None


def _scenario_world_view(
    session: OperatorSession, scenario: ScenarioDefinition | None
) -> dict[str, Any] | None:
    if scenario is None or scenario.world is None:
        return None
    if isinstance(scenario.world, WorldDefinition):
        payload = scenario.world.model_dump(mode="json")
        agents: list[dict[str, JsonValue]] = []
        for entity in scenario.entities:
            position = entity.components.get("position")
            if position:
                agents.append(
                    {
                        "id": entity.id,
                        "position": cast(dict[str, JsonValue], position),
                    }
                )
        return _grid_view(
            payload, agents, session, "Staged scenario preview", {}
        )
    if isinstance(scenario.world, CityWorldDefinition):
        payload = scenario.world.model_dump(mode="json")
        city = {
            "name": payload["city"]["name"],
            "bounds": payload["city"]["bounds_meters"],
            "districts": payload["districts"],
            "buildings": [
                {
                    **building,
                    "position": building["city_position"],
                }
                for building in payload["buildings"]
            ],
            "outdoor_places": [
                {
                    **place,
                    "position": place["city_position"],
                }
                for place in payload["outdoor_places"]
            ],
            "nodes": payload["transport"]["nodes"],
            "edges": payload["transport"]["edges"],
            "vehicles": payload["transport"]["vehicles"],
        }
        return _city_view(city, [], session, "Staged city preview", {}, [])
    return None


def _grid_view(
    world: dict[str, Any],
    agents: list[dict[str, JsonValue]],
    session: OperatorSession,
    title: str,
    overlays: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    width = max(1, int(world.get("width", 1)))
    height = max(1, int(world.get("height", 1)))
    zones = []
    for zone in world.get("zones", []):
        tiles = zone.get("tiles")
        if tiles is None and zone.get("bounds"):
            bounds = zone["bounds"]
            tiles = [
                {"x": x, "y": y}
                for y in range(bounds["y"], bounds["y"] + bounds["height"])
                for x in range(bounds["x"], bounds["x"] + bounds["width"])
            ]
        zones.append({**zone, "tiles": tiles or []})
    rendered_agents = []
    paths = []
    for agent in agents:
        position = agent.get("position")
        if isinstance(position, dict):
            agent_id = str(agent["id"])
            overlay = overlays.get(agent_id, {})
            movement = agent.get("movement")
            destination_point = (
                movement.get("destination")
                if isinstance(movement, dict)
                else None
            )
            rendered_agents.append(
                {
                    "id": agent["id"],
                    "name": _agent_name(agent),
                    "x": position.get("x", 0),
                    "y": position.get("y", 0),
                    "selected": agent["id"] == session.selected_agent_id,
                    "system1": cast(dict[str, JsonValue], agent.get("system1", {})).get(
                        "state"
                    ),
                    "visible": bool(overlay.get("visible")),
                    "speech": overlay.get("speech"),
                    "vision_count": overlay.get("vision_count", 0),
                    "heard": bool(overlay.get("heard")),
                    "destination": (
                        destination_point
                        if isinstance(destination_point, dict)
                        and "destinations" in session.overlays
                        else None
                    ),
                }
            )
        movement = agent.get("movement")
        if (
            "paths" in session.overlays
            and isinstance(movement, dict)
            and isinstance(movement.get("path"), list)
        ):
            raw_path = movement.get("path")
            points = (
                [
                    f"{_number(point.get('x')) + 0.5},"
                    f"{_number(point.get('y')) + 0.5}"
                    for point in raw_path
                    if isinstance(point, dict)
                ]
                if isinstance(raw_path, list)
                else []
            )
            if points:
                paths.append(" ".join(points))
    return {
        "kind": "grid",
        "title": title,
        "width": width,
        "height": height,
        "view_box": f"0 0 {width} {height}",
        "display_width": width * 72 * session.zoom,
        "zones": zones,
        "blocked": world.get("blocked", []),
        "stations": world.get("stations", []),
        "agents": rendered_agents,
        "paths": paths,
    }


def _city_view(
    city: dict[str, Any],
    agents: list[dict[str, JsonValue]],
    session: OperatorSession,
    title: str,
    overlays: dict[str, dict[str, Any]],
    vehicle_states: list[dict[str, JsonValue]],
) -> dict[str, Any]:
    bounds = cast(dict[str, float], city["bounds"])
    min_x = float(bounds["min_x"])
    min_y = float(bounds["min_y"])
    span_x = max(1.0, float(bounds["max_x"]) - min_x)
    span_y = max(1.0, float(bounds["max_y"]) - min_y)

    def project(point: dict[str, Any]) -> tuple[float, float]:
        return (
            (float(point["x"]) - min_x) / span_x * 1000,
            (float(point["y"]) - min_y) / span_y * 650,
        )

    buildings = [
        {
            **building,
            "px": project(cast(dict[str, Any], building["position"]))[0],
            "py": project(cast(dict[str, Any], building["position"]))[1],
        }
        for building in city.get("buildings", [])
    ]
    places = [
        {
            **place,
            "px": project(cast(dict[str, Any], place["position"]))[0],
            "py": project(cast(dict[str, Any], place["position"]))[1],
        }
        for place in city.get("outdoor_places", [])
    ]
    nodes = {
        str(node["id"]): project(cast(dict[str, Any], node["position"]))
        for node in city.get("nodes", [])
    }
    edges = []
    edge_points: dict[str, list[tuple[float, float]]] = {}
    for edge in city.get("edges", []):
        geometry = edge.get("geometry", [])
        points = [project(cast(dict[str, Any], point)) for point in geometry]
        edge_points[str(edge["id"])] = points
        edges.append({**edge, "points": " ".join(f"{x},{y}" for x, y in points)})
    building_points = {str(item["id"]): (item["px"], item["py"]) for item in buildings}
    place_points = {str(item["id"]): (item["px"], item["py"]) for item in places}
    rendered_agents = []
    for agent in agents:
        location = agent.get("spatial_location")
        if not isinstance(location, dict):
            continue
        point = (
            building_points.get(str(location.get("place_id")))
            or place_points.get(str(location.get("place_id")))
            or nodes.get(str(location.get("network_node_id")))
        )
        if point is None:
            continue
        rendered_agents.append(
            {
                "id": agent["id"],
                "name": _agent_name(agent),
                "px": point[0],
                "py": point[1],
                "selected": agent["id"] == session.selected_agent_id,
                "visible": bool(
                    overlays.get(str(agent["id"]), {}).get("visible")
                ),
                "speech": overlays.get(str(agent["id"]), {}).get("speech"),
            }
        )
    vehicle_definitions = {
        str(vehicle["id"]): vehicle for vehicle in city.get("vehicles", [])
    }
    rendered_vehicles = []
    for state in vehicle_states:
        vehicle_id = str(state.get("id", ""))
        definition = vehicle_definitions.get(vehicle_id, {})
        point = nodes.get(str(state.get("network_node_id")))
        edge_id = state.get("edge_id")
        progress = state.get("edge_progress")
        if isinstance(edge_id, str) and isinstance(progress, (int, float)):
            point = _interpolate_points(edge_points.get(edge_id, []), float(progress))
        if point is None:
            continue
        rendered_vehicles.append(
            {
                "id": vehicle_id,
                "name": definition.get("name", vehicle_id),
                "px": point[0],
                "py": point[1],
            }
        )
    return {
        "kind": "city",
        "title": f"{title} · {city.get('name', 'City')}",
        "view_box": "0 0 1000 650",
        "display_width": 1000 * session.zoom,
        "districts": city.get("districts", []),
        "buildings": buildings,
        "places": places,
        "edges": edges,
        "agents": rendered_agents,
        "vehicles": rendered_vehicles,
    }


def _world_overlays(
    agents: list[dict[str, JsonValue]],
    events: list[DomainEvent],
    selected_agent_id: str | None,
) -> dict[str, dict[str, Any]]:
    overlays: dict[str, dict[str, Any]] = {}
    selected = next(
        (agent for agent in agents if agent["id"] == selected_agent_id),
        None,
    )
    visible: set[str] = set()
    if selected is not None:
        perception = selected.get("perception")
        if isinstance(perception, dict):
            raw_visible = perception.get("visible_now")
            if isinstance(raw_visible, list):
                visible = {str(value) for value in raw_visible}
    for agent in agents:
        agent_id = str(agent["id"])
        perception = agent.get("perception")
        vision_count = 0
        if isinstance(perception, dict):
            raw_visible = perception.get("visible_now")
            if isinstance(raw_visible, list):
                vision_count = len(raw_visible)
        overlays[agent_id] = {
            "visible": agent_id in visible,
            "vision_count": vision_count,
            "heard": False,
            "speech": None,
        }
    for event in reversed(events[-200:]):
        if event.event_type.startswith(("speech.", "dialogue.")) and event.agent_id:
            text = event.payload.get("text")
            current = overlays.setdefault(event.agent_id, {})
            if isinstance(text, str) and not current.get("speech"):
                current["speech"] = text
        if (
            event.event_type == "perception.delivered"
            and event.agent_id
            and event.payload.get("modality") == "auditory"
        ):
            overlays.setdefault(event.agent_id, {})["heard"] = True
        if event.event_type == "speech.delivered":
            recipients = event.payload.get("recipient_ids")
            if isinstance(recipients, list):
                for recipient in recipients:
                    overlays.setdefault(str(recipient), {})["heard"] = True
    return overlays


def _neighborhood_payload(
    city: dict[str, Any],
    selected_location: dict[str, JsonValue],
) -> dict[str, Any]:
    place_id = selected_location.get("place_id")
    selected_building = next(
        (
            item
            for item in city.get("buildings", [])
            if item.get("id") == place_id
        ),
        None,
    )
    selected_place = next(
        (
            item
            for item in city.get("outdoor_places", [])
            if item.get("id") == place_id
        ),
        None,
    )
    selected_item = selected_building or selected_place
    if selected_item is None:
        return city
    district_id = selected_item.get("district_id")
    buildings = [
        item
        for item in city.get("buildings", [])
        if item.get("district_id") == district_id
    ]
    places = [
        item
        for item in city.get("outdoor_places", [])
        if item.get("district_id") == district_id
    ]
    place_ids = {
        str(item["id"]) for item in [*buildings, *places] if "id" in item
    }
    nodes = [
        item
        for item in city.get("nodes", [])
        if item.get("place_id") in place_ids
    ]
    node_ids = {str(item["id"]) for item in nodes}
    edges = [
        item
        for item in city.get("edges", [])
        if item.get("from_node_id") in node_ids
        or item.get("to_node_id") in node_ids
    ]
    return {
        **city,
        "name": f"{city.get('name', 'City')} · {district_id}",
        "buildings": buildings,
        "outdoor_places": places,
        "nodes": nodes,
        "edges": edges,
    }


def _interpolate_points(
    points: list[tuple[float, float]], progress: float
) -> tuple[float, float] | None:
    if not points:
        return None
    if len(points) == 1:
        return points[0]
    bounded = max(0.0, min(1.0, progress))
    segment_position = bounded * (len(points) - 1)
    index = min(len(points) - 2, int(segment_position))
    fraction = segment_position - index
    start = points[index]
    end = points[index + 1]
    return (
        start[0] + (end[0] - start[0]) * fraction,
        start[1] + (end[1] - start[1]) * fraction,
    )


def _agent_name(agent: dict[str, JsonValue]) -> str:
    profile = agent.get("character_profile")
    if isinstance(profile, dict):
        display_name = profile.get("display_name")
        if isinstance(display_name, str):
            return display_name
    return str(agent["id"])


def _number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def percent(value: Any) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def plan_rows(agent: dict[str, JsonValue] | None) -> list[str]:
    if agent is None:
        return []
    plan = agent.get("plan")
    if not isinstance(plan, dict):
        return []
    actions = [plan.get("current"), *cast(list[Any], plan.get("queue", []))]
    rows = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        detail = str(action.get("action", "unknown"))
        if action.get("target"):
            detail += f" → {action['target']}"
        if action.get("duration") is not None:
            detail += f" · {action['duration']}s"
        rows.append(detail)
    return rows


def destination(agent: dict[str, JsonValue] | None) -> str:
    if agent is None:
        return "—"
    movement = agent.get("movement")
    if isinstance(movement, dict):
        point = movement.get("destination")
        if isinstance(point, dict):
            return f"({point.get('x')}, {point.get('y')})"
    travel = agent.get("travel")
    if isinstance(travel, dict) and travel.get("destination_id"):
        return str(travel["destination_id"])
    navigation = agent.get("navigation")
    if isinstance(navigation, dict) and navigation.get("target_id"):
        return str(navigation["target_id"])
    return "none"


templates.env.globals.update(
    destination=destination,
    event_summary=event_summary,
    json_pretty=json_pretty,
    math=math,
    percent=percent,
    plan_rows=plan_rows,
)
