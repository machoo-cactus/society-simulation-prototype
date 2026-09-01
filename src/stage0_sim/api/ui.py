import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode, urlsplit
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
    CharacterSummary,
    character_constraint_violations,
    character_content_hash,
    character_summary,
)
from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetRecordFilter,
    RecordCategory,
    RecordVisibility,
)
from stage0_sim.application.data_management import (
    AggregateDatasetSummary,
    PersistedRunFilter,
    RunDeletionPreview,
    RunSelection,
)
from stage0_sim.application.element_library import ElementLibrary
from stage0_sim.application.elements import ScenarioSourceDefinition
from stage0_sim.application.manager import (
    ManagedRun,
    SimulationConflictError,
    SimulationManager,
    SimulationNotFoundError,
)
from stage0_sim.application.runner import RunnerStatus
from stage0_sim.application.scenario import (
    CharacterSlotDefinition,
    CityWorldDefinition,
    RoomDefinition,
    ScenarioDefinition,
    WorldDefinition,
)
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)
from stage0_sim.application.scenarios import ScenarioLibrary, ScenarioLibraryError
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
MIN_MAP_ZOOM = 0.5
MAX_MAP_ZOOM = 3.0
ROOM_ZOOM = 2.5
BUILDING_ZOOM = 1.75
CITY_ZONE_ZOOM = 1.2

EVENT_FILTERS: dict[str, re.Pattern[str]] = {
    "system1": re.compile(r"^(system1\.|threshold\.breached$)"),
    "actions": re.compile(
        r"^(plan|planner|affordance|transaction|activity|agent|path)\."
    ),
    "dialogue": re.compile(r"^(dialogue|speech)\."),
    "cognition": re.compile(r"^(cognition|tool)\."),
    "perception": re.compile(r"^perception\."),
    "travel": re.compile(r"^(travel|building|vehicle|metro)\."),
    "environment": re.compile(
        r"^(time\.|weather\.|surface_condition\.|availability\.)"
    ),
    "errors": re.compile(r"(failed|blocked|cancelled|error|rejected)"),
    "ui": re.compile(r"^simulation\."),
}

DATASET_VIEW_OPTIONS: tuple[tuple[str, str], ...] = (
    ("summary", "Run summary"),
    ("records", "Raw records"),
    ("goals", "Goals timeline"),
    ("decisions", "Decisions timeline"),
    ("actions", "Actions timeline"),
    ("interactions", "Interactions timeline"),
    ("transitions", "State transitions"),
    ("population", "Population aggregates"),
    ("resource_samples", "Resource samples"),
    ("resource_flows", "Resource flows"),
    ("schema", "Schema and data dictionary"),
)
DATASET_TABLE_VIEWS: dict[str, str] = {
    "goals": "goals",
    "decisions": "decisions",
    "actions": "actions",
    "interactions": "interactions",
    "transitions": "transitions",
    "population": "population",
    "resource_samples": "resource_samples",
    "resource_flows": "resource_flows",
}
DATASET_DOMAIN_FILTERS: tuple[tuple[str, str], ...] = (
    ("goal_id", "Goal ID"),
    ("plan_id", "Plan ID"),
    ("action_id", "Action ID"),
    ("decision_id", "Decision ID"),
    ("model_request_id", "Model request ID"),
    ("tool_call_id", "Tool call ID"),
    ("interaction_id", "Interaction ID"),
    ("perception_fact_id", "Perception fact ID"),
    ("memory_id", "Memory ID"),
    ("transaction_request_id", "Transaction request ID"),
    ("operator_intervention_id", "Operator intervention ID"),
)
DATASET_FILTER_KEYS: tuple[str, ...] = (
    "record_type",
    "category",
    "schema_id",
    "schema_version",
    "entity_id",
    "related_entity_id",
    "minimum_tick",
    "maximum_tick",
    "minimum_time",
    "maximum_time",
    "visibility",
    *(name for name, _ in DATASET_DOMAIN_FILTERS),
    "status",
    "outcome",
    "include_private",
    "limit",
)
DATA_RUN_STATUSES = (
    "created",
    "running",
    "paused",
    "completed",
    "stopped",
    "failed",
    "capture_failed",
    "interrupted",
)

CHARACTER_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "identity",
        "title": "Identity",
        "fields": (
            ("display_name", "Display name", "text", True),
            ("birth_date", "Birth date", "date", False),
            ("gender", "Gender", "text", False),
            ("pronouns", "Pronouns", "text", False),
            ("occupation", "Occupation", "text", False),
        ),
    },
    {
        "id": "body_measurements",
        "title": "Body measurements",
        "fields": (
            ("measured_on", "Measured on", "date", False),
            ("height_cm", "Height (cm)", "decimal", False),
            ("weight_kg", "Weight (kg)", "decimal", False),
            ("chest_cm", "Chest circumference (cm)", "decimal", False),
            ("waist_cm", "Waist circumference (cm)", "decimal", False),
            ("hips_cm", "Hip circumference (cm)", "decimal", False),
            ("inseam_cm", "Inseam (cm)", "decimal", False),
            ("shoe_size_system", "Shoe size system", "text", False),
            ("shoe_size_value", "Shoe size", "decimal", False),
        ),
    },
    {
        "id": "appearance",
        "title": "Appearance",
        "fields": (
            ("summary", "Summary", "textarea", False),
            ("build", "Build", "text", False),
            ("hair", "Hair", "text", False),
            ("eyes", "Eyes", "text", False),
            (
                "clothing",
                "Legacy exact clothing (prefer Presentation style)",
                "textarea",
                False,
            ),
            (
                "distinguishing_features",
                "Distinguishing features",
                "list",
                False,
            ),
        ),
    },
    {
        "id": "health",
        "title": "Stable health facts",
        "fields": (
            ("as_of_date", "Health facts as of", "date", False),
            ("blood_type", "Blood type", "text", False),
            ("conditions", "Conditions (JSON records)", "json_array", False),
            ("allergies", "Allergies (JSON records)", "json_array", False),
            ("medications", "Medications (JSON records)", "json_array", False),
            ("disabilities", "Disabilities", "list", False),
            ("vision", "Vision", "text", False),
            ("hearing", "Hearing", "text", False),
            ("mobility", "Mobility", "text", False),
            ("past_procedures", "Past procedures", "list", False),
            ("dietary_restrictions", "Dietary restrictions", "list", False),
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
        "id": "financial_situation",
        "title": "Financial situation",
        "fields": (
            ("as_of_date", "Financial facts as of", "date", False),
            ("currency", "Currency code", "text", False),
            ("annual_gross_income", "Annual gross income", "money", False),
            ("income_band", "Income band", "text", False),
            ("liquid_assets", "Liquid assets", "money", False),
            ("total_assets", "Total assets", "money", False),
            ("total_debt", "Total debt", "money", False),
            ("monthly_fixed_expenses", "Monthly fixed expenses", "money", False),
            ("housing_tenure", "Housing tenure", "text", False),
            ("financial_dependents", "Financial dependents", "number", False),
        ),
    },
    {
        "id": "motivations",
        "title": "Motivations",
        "fields": (
            ("values", "Values", "list", False),
            ("fears", "Fears", "list", False),
            ("needs", "Needs", "list", False),
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
    {
        "id": "presentation",
        "title": "Stable presentation style",
        "fields": (
            ("aesthetic_identity", "Aesthetic identity", "textarea", False),
            ("wardrobe_palette", "Wardrobe palette", "list", False),
            ("preferred_silhouettes", "Preferred silhouettes", "list", False),
            ("preferred_fabrics", "Preferred fabrics", "list", False),
            ("formality_range", "Formality range", "textarea", False),
            ("comfort_priorities", "Comfort priorities", "list", False),
            ("grooming_norms", "Grooming norms", "list", False),
            ("usual_accessories", "Usual accessories", "list", False),
            ("practical_constraints", "Practical constraints", "list", False),
            ("purchase_habits", "Purchase habits", "list", False),
            ("context_variations", "Context variations", "list", False),
        ),
    },
    {
        "id": "dispositions",
        "title": "Usual dispositions",
        "fields": (
            ("summary", "Summary", "textarea", False),
            ("emotional_baseline", "Emotional baseline", "textarea", False),
            ("sociability", "Sociability", "textarea", False),
            ("assertiveness", "Assertiveness", "textarea", False),
            ("patience", "Patience", "textarea", False),
            ("conscientiousness", "Conscientiousness", "textarea", False),
            ("openness", "Openness", "textarea", False),
            ("adaptability", "Adaptability", "textarea", False),
            ("risk_tolerance", "Risk tolerance", "textarea", False),
            ("ambiguity_tolerance", "Ambiguity tolerance", "textarea", False),
            ("impulse_control", "Impulse control", "textarea", False),
            ("conflict_style", "Conflict style", "textarea", False),
            ("cooperation_style", "Cooperation style", "textarea", False),
            ("trust_formation", "Trust formation", "textarea", False),
            ("boundary_setting", "Boundary setting", "textarea", False),
            ("help_seeking", "Help seeking", "textarea", False),
            ("pressure_response", "Pressure response", "textarea", False),
            ("fatigue_response", "Fatigue response", "textarea", False),
            ("novelty_response", "Novelty response", "textarea", False),
            ("authority_response", "Authority response", "textarea", False),
            ("crowd_response", "Crowd response", "textarea", False),
        ),
    },
    {
        "id": "communication",
        "title": "Communication and manner",
        "fields": (
            ("cadence", "Cadence", "textarea", False),
            ("vocabulary", "Vocabulary", "textarea", False),
            ("directness", "Directness", "textarea", False),
            ("politeness", "Politeness", "textarea", False),
            ("humor", "Humor", "textarea", False),
            ("gesture", "Gesture", "textarea", False),
            ("posture", "Posture", "textarea", False),
            ("facial_expressiveness", "Facial expressiveness", "textarea", False),
            ("listening_style", "Listening style", "textarea", False),
            ("disagreement_style", "Disagreement style", "textarea", False),
            ("apology_style", "Apology style", "textarea", False),
            ("with_intimates", "With intimates", "textarea", False),
            ("with_colleagues", "With colleagues", "textarea", False),
            ("with_strangers", "With strangers", "textarea", False),
            ("with_authority", "With authority", "textarea", False),
        ),
    },
    {
        "id": "decision_coping",
        "title": "Decision and coping patterns",
        "fields": (
            ("information_seeking", "Information seeking", "textarea", False),
            ("planning_horizon", "Planning horizon", "textarea", False),
            ("default_heuristics", "Default heuristics", "list", False),
            ("error_sensitivity", "Error sensitivity", "textarea", False),
            ("persistence", "Persistence", "textarea", False),
            ("recovery_habits", "Recovery habits", "list", False),
            ("self_soothing", "Self-soothing", "list", False),
            ("stress_signals", "Stress signals", "list", False),
            ("disposition_shifts", "Disposition shifts", "list", False),
        ),
    },
    {
        "id": "life_structure",
        "title": "Life structure",
        "fields": (
            ("household", "Household", "textarea", False),
            ("recurring_obligations", "Recurring obligations", "list", False),
            ("material_habits", "Material habits", "list", False),
            ("typical_possessions", "Typical possessions", "list", False),
            ("cultural_practices", "Cultural practices", "list", False),
            ("interests", "Interests", "list", False),
            ("social_patterns", "Social patterns", "list", False),
        ),
    },
    {
        "id": "family",
        "title": "Family facts",
        "fields": (
            ("members", "Family members (JSON records)", "json_array", False),
        ),
    },
)


@dataclass(slots=True)
class PendingRunDeletion:
    run_ids: tuple[str, ...]
    selection_fingerprint: str
    filters: PersistedRunFilter | None
    confirmation_token: str
    total_records: int
    phrase: str


@dataclass(slots=True)
class OperatorSession:
    scenario: ScenarioDefinition | None = None
    scenario_id: str | None = None
    scenario_source: str = ""
    scenario_warnings: tuple[str, ...] = ()
    character_assignments: dict[str, str] = field(default_factory=dict)
    run_id: str | None = None
    selected_agent_id: str | None = None
    view_level: str = "auto"
    follow_selected: bool = False
    zoom: float = 1.0
    camera_x: float = 0.5
    camera_y: float = 0.5
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
    selected_data_run_ids: tuple[str, ...] = ()
    selected_data_filters: PersistedRunFilter | None = None
    include_private_derived: bool = True
    pending_run_deletion: PendingRunDeletion | None = None
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

    def remove_deleted_run_ids(self, run_ids: tuple[str, ...]) -> None:
        deleted = frozenset(run_ids)
        for session in self._sessions.values():
            session.selected_data_run_ids = tuple(
                run_id
                for run_id in session.selected_data_run_ids
                if run_id not in deleted
            )
            if not session.selected_data_run_ids:
                session.selected_data_filters = None
            if session.run_id in deleted:
                session.run_id = None
                session.selected_agent_id = None
                session.follow_selected = False
            pending = session.pending_run_deletion
            if pending is not None and deleted.intersection(pending.run_ids):
                session.pending_run_deletion = None


def _manager(request: Request) -> SimulationManager:
    return cast(SimulationManager, request.app.state.simulation_manager)


def _library(request: Request) -> CharacterLibrary:
    return cast(CharacterLibrary, request.app.state.character_library)


def _scenario_library(request: Request) -> ScenarioLibrary:
    return cast(ScenarioLibrary, request.app.state.scenario_library)


def _element_library(request: Request) -> ElementLibrary:
    return cast(ElementLibrary, request.app.state.element_library)


def _parse_operator_scenario(
    request: Request,
    content: str | bytes,
) -> tuple[
    ScenarioDefinition,
    dict[str, JsonValue] | None,
    dict[str, JsonValue],
]:
    raw = json.loads(content)
    source = ScenarioSourceDefinition.model_validate(raw)
    resolved = resolve_scenario(source, _element_library(request))
    return (
        resolved.scenario,
        source.model_dump(mode="json"),
        resolved.provenance_payload(),
    )


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


def _managed_run(manager: SimulationManager, session: OperatorSession) -> ManagedRun | None:
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


async def _stage_scenario(
    manager: SimulationManager,
    session: OperatorSession,
    scenario: ScenarioDefinition,
    source: str,
    character_assignments: dict[str, str] | None = None,
    *,
    scenario_source: dict[str, JsonValue] | None = None,
    resolved_elements: dict[str, JsonValue] | None = None,
) -> None:
    try:
        scenario_id = await manager.add_scenario(
            scenario,
            character_assignments,
            scenario_source=scenario_source,
            resolved_elements=resolved_elements,
        )
    except ValueError:
        if session.scenario is not scenario:
            session.scenario = scenario
            session.scenario_id = None
            session.scenario_source = source
            session.character_assignments = {}
        raise
    prepared = manager.get_scenario(scenario_id)
    session.scenario = scenario
    session.scenario_id = scenario_id
    session.scenario_source = source
    session.scenario_warnings = ()
    session.character_assignments = dict(prepared.assignments)
    session.selected_agent_id = None
    session.follow_selected = False
    session.view_level = "auto"
    session.zoom = 1.0
    session.camera_x = 0.5
    session.camera_y = 0.5
    session.notify(f"{scenario.name} is validated and staged. Starting remains a separate action.")


@router.get("/", response_class=HTMLResponse, name="operator")
async def operator_page(request: Request) -> Response:
    session_id, session = _session(request)
    if "selected" in request.query_params:
        session.selected_agent_id = request.query_params.get("selected", "").strip() or None
        if session.selected_agent_id is None:
            session.follow_selected = False
    manager = _manager(request)
    managed = _managed_run(manager, session)
    prepared = (
        manager.get_scenario(session.scenario_id)
        if session.scenario_id is not None
        else None
    )
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
    if session.selected_agent_id not in {str(agent["id"]) for agent in agents}:
        session.selected_agent_id = None
        session.follow_selected = False
    selected_agent = next(
        (agent for agent in agents if agent["id"] == session.selected_agent_id),
        None,
    )
    event_filter = request.query_params.get("filter", "all")
    event_search = request.query_params.get("search", "").strip().casefold()
    event_order = request.query_params.get("order", "newest")
    try:
        event_limit = max(100, min(5000, int(request.query_params.get("limit", "500"))))
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
    try:
        saved_scenarios = _scenario_library(request).list()
    except ScenarioLibraryError as library_error:
        session.notify(
            f"Could not list saved scenarios: {library_error}",
            error=True,
        )
        saved_scenarios = ()
    message, page_error = session.consume_notice()
    dataset_summary: dict[str, JsonValue] | None = None
    if session.run_id is not None:
        try:
            dataset_summary = manager.data_query.summary(session.run_id)
        except KeyError:
            dataset_summary = None
    response = templates.TemplateResponse(
        request,
        "simulation.html",
        {
            "session": session,
            "scenario": session.scenario,
            "prepared": prepared,
            "saved_scenarios": saved_scenarios,
            "characters": _library(request).list(),
            "slot_characters": _slot_character_options(
                session.scenario,
                _library(request),
            ),
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
            "environment": (
                snapshot.get("environment")
                if snapshot is not None
                and isinstance(snapshot.get("environment"), dict)
                else None
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
            "recent_perceptions": _recent_perceptions(events, session.selected_agent_id, agents),
            "dataset_summary": dataset_summary,
            "message": message,
            "error": page_error,
            "refresh_url": str(request.url),
            "auto_refresh": (
                managed is not None
                and managed.runner.status is RunnerStatus.RUNNING
                and session.live_refresh
            ),
        },
    )
    return _with_session(response, session_id)


@router.get("/datasets/{run_id}/", response_class=HTMLResponse)
async def dataset_explorer(request: Request, run_id: str) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    message, session_error = session.consume_notice()
    view = request.query_params.get("view", "summary").strip() or "summary"
    summary: dict[str, JsonValue] | None = None
    schema: dict[str, JsonValue] | None = None
    entries: list[dict[str, Any]] = []
    next_cursor: str | None = None
    explorer_error = session_error
    filter_values = _dataset_filter_values(request)
    include_private = _dataset_include_private(request)
    query_filter: DatasetQueryFilter | None = None
    record_filter: DatasetRecordFilter | None = None
    try:
        if view not in {value for value, _ in DATASET_VIEW_OPTIONS}:
            raise ValueError(f"unknown dataset view: {view}")
        summary = manager.data_query.summary(run_id)
        query_filter, record_filter = _dataset_filters(
            request,
            raw_records=view == "records",
        )
        if view == "records":
            record_page = manager.data_query.records(run_id, record_filter)
            entries = [
                _dataset_entry(view, record.to_dict(), index)
                for index, record in enumerate(record_page.records, start=1)
            ]
            next_cursor = (
                str(record_page.next_cursor)
                if record_page.next_cursor is not None
                else None
            )
        elif view in DATASET_TABLE_VIEWS:
            table_page = manager.data_query.table(
                run_id,
                DATASET_TABLE_VIEWS[view],
                query_filter,
            )
            entries = [
                _dataset_entry(view, row, index)
                for index, row in enumerate(table_page.rows, start=1)
            ]
            next_cursor = table_page.next_cursor
        elif view == "schema":
            schema = manager.data_query.schema(run_id)
    except (KeyError, ValueError) as error:
        explorer_error = str(error).strip("'")

    route = f"/ui/datasets/{quote(run_id, safe='')}/"
    current_params = _dataset_preserved_params(request, view=view)
    first_page_url = _dataset_url(route, current_params, remove=("cursor",))
    next_page_url = (
        _dataset_url(route, current_params, updates={"cursor": next_cursor})
        if next_cursor is not None
        else None
    )
    view_links = [
        {
            "value": value,
            "label": label,
            "url": _dataset_url(
                route,
                current_params,
                updates={"view": value},
                remove=("cursor",),
            ),
        }
        for value, label in DATASET_VIEW_OPTIONS
    ]
    export_params = {
        key: value
        for key, value in current_params.items()
        if key not in {"view", "cursor", "limit"} and value != ""
    }
    export_query = urlencode(export_params)
    export_suffix = f"?{export_query}" if export_query else ""
    response = templates.TemplateResponse(
        request,
        "dataset.html",
        {
            "run_id": run_id,
            "summary": summary,
            "schema": schema,
            "entries": entries,
            "view": view,
            "view_label": dict(DATASET_VIEW_OPTIONS).get(view, view),
            "view_options": DATASET_VIEW_OPTIONS,
            "view_links": view_links,
            "filter_values": filter_values,
            "record_categories": tuple(category.value for category in RecordCategory),
            "record_visibilities": tuple(
                visibility.value for visibility in RecordVisibility
            ),
            "domain_filters": DATASET_DOMAIN_FILTERS,
            "include_private": include_private,
            "next_page_url": next_page_url,
            "first_page_url": first_page_url,
            "filtered_records_url": (
                f"/simulation/runs/{quote(run_id, safe='')}"
                f"/exports/records{export_suffix}"
            ),
            "analysis_bundle_url": (
                f"/simulation/runs/{quote(run_id, safe='')}"
                f"/exports/bundle{export_suffix}"
            ),
            "message": message,
            "error": "",
            "explorer_error": explorer_error,
            "refresh_url": str(request.url),
            "auto_refresh": False,
        },
    )
    return _with_session(response, session_id)


@router.get("/data/", response_class=HTMLResponse)
async def data_management_page(request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    if "privacy_setting" in request.query_params:
        session.include_private_derived = (
            request.query_params.get("exclude_private_derived") != "true"
        )
    try:
        filters = _data_run_filters(request.query_params)
        catalog = manager.persisted_runs(filters)
    except ValueError as error:
        filters = PersistedRunFilter()
        catalog = manager.persisted_runs(filters)
        session.notify(str(error), error=True)

    selection = _current_data_selection(manager, session)
    aggregate: AggregateDatasetSummary | None = None
    if selection is not None:
        try:
            aggregate = manager.aggregate_persisted_runs(
                selection,
                include_private_derived=session.include_private_derived,
            )
        except (KeyError, ValueError) as error:
            session.selected_data_run_ids = ()
            session.pending_run_deletion = None
            session.notify(str(error).strip("'"), error=True)
            selection = None

    deletion_preview: RunDeletionPreview | None = None
    pending = session.pending_run_deletion
    if pending is not None:
        try:
            pending_selection = RunSelection(
                run_ids=pending.run_ids,
                fingerprint=pending.selection_fingerprint,
                filters=pending.filters,
            )
            deletion_preview = manager.preview_persisted_run_deletion(
                pending_selection
            )
            if deletion_preview.confirmation_token != pending.confirmation_token:
                raise ValueError("deletion preview is stale")
        except (KeyError, ValueError) as error:
            session.pending_run_deletion = None
            session.notify(str(error).strip("'"), error=True)

    filter_values = _data_filter_values(request)
    preserved = {
        key: value
        for key, value in filter_values.items()
        if value != ""
    }
    first_page_url = _data_management_url(preserved, remove=("cursor",))
    next_page_url = (
        _data_management_url(
            preserved,
            updates={"cursor": catalog.next_cursor},
        )
        if catalog.next_cursor is not None
        else None
    )
    export_urls = _aggregate_export_urls(
        selection,
        session.include_private_derived,
    )
    message, page_error = session.consume_notice()
    response = templates.TemplateResponse(
        request,
        "data_management.html",
        {
            "catalog": catalog,
            "filters": filters,
            "filter_values": filter_values,
            "run_statuses": DATA_RUN_STATUSES,
            "selection": selection,
            "selected_run_ids": frozenset(session.selected_data_run_ids),
            "row_selection_fingerprints": {
                run.run_id: manager.data_management.selection(
                    (run.run_id,)
                ).fingerprint
                for run in catalog.runs
            },
            "aggregate": aggregate,
            "aggregate_sections": (
                _aggregate_sections(aggregate) if aggregate is not None else ()
            ),
            "include_private_derived": session.include_private_derived,
            "export_json_url": export_urls[0],
            "export_csv_url": export_urls[1],
            "first_page_url": first_page_url,
            "next_page_url": next_page_url,
            "return_to": str(request.url),
            "deletion_preview": deletion_preview,
            "pending_deletion": session.pending_run_deletion,
            "message": message,
            "error": page_error,
            "refresh_url": str(request.url),
            "auto_refresh": False,
        },
    )
    return _with_session(response, session_id)


@router.post("/data/selection")
async def update_data_selection(request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    form = await request.form()
    action = str(form.get("action", ""))
    current = list(session.selected_data_run_ids)
    checked = [str(value) for value in form.getlist("run_id") if str(value)]
    page_run_ids = [
        str(value) for value in form.getlist("page_run_id") if str(value)
    ]
    try:
        if action == "add":
            current.extend(checked)
        elif action == "remove":
            remove = frozenset(checked)
            current = [run_id for run_id in current if run_id not in remove]
        elif action == "add_page":
            current.extend(page_run_ids)
        elif action == "remove_page":
            remove = frozenset(page_run_ids)
            current = [run_id for run_id in current if run_id not in remove]
        elif action == "select_all":
            selected = manager.data_management.select_all(_data_run_filters(form))
            current = list(selected.run_ids)
        elif action == "clear":
            current = []
        else:
            raise ValueError("unknown data selection action")
        if current:
            selected_filters = (
                selected.filters if action == "select_all" else None
            )
            resolved = manager.data_management.selection(
                current,
                selected_filters,
            )
            session.selected_data_run_ids = resolved.run_ids
            session.selected_data_filters = resolved.filters
        else:
            session.selected_data_run_ids = ()
            session.selected_data_filters = None
        session.pending_run_deletion = None
        session.notify(
            f"{len(session.selected_data_run_ids)} persisted run"
            f"{'' if len(session.selected_data_run_ids) == 1 else 's'} selected."
        )
    except (KeyError, ValueError) as error:
        session.notify(str(error).strip("'"), error=True)
    response = _redirect(_safe_data_return_to(form.get("return_to")))
    return _with_session(response, session_id)


@router.post("/data/deletion-preview")
async def preview_data_deletion(request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    form = await request.form()
    requested_ids = tuple(
        str(value) for value in form.getlist("run_id") if str(value)
    )
    run_ids = requested_ids or session.selected_data_run_ids
    try:
        submitted_fingerprint = str(form.get("selection_fingerprint", ""))
        candidates = [
            manager.data_management.selection(run_ids),
        ]
        if (
            tuple(run_ids) == session.selected_data_run_ids
            and session.selected_data_filters is not None
        ):
            candidates.append(
                manager.data_management.selection(
                    run_ids,
                    session.selected_data_filters,
                )
            )
        selection = next(
            (
                candidate
                for candidate in candidates
                if candidate.fingerprint == submitted_fingerprint
            ),
            None,
        )
        if selection is None:
            raise ValueError("stale or invalid run selection fingerprint")
        preview = manager.preview_persisted_run_deletion(selection)
        phrase = f"DELETE {len(selection.run_ids)} RUNS"
        session.selected_data_run_ids = selection.run_ids
        session.pending_run_deletion = PendingRunDeletion(
            run_ids=selection.run_ids,
            selection_fingerprint=selection.fingerprint,
            filters=selection.filters,
            confirmation_token=preview.confirmation_token,
            total_records=preview.total_records,
            phrase=phrase,
        )
        if not preview.eligible:
            session.notify(
                "Deletion is blocked because these runs are active or not fully "
                "finalized: "
                + ", ".join(preview.ineligible_run_ids),
                error=True,
            )
        else:
            session.notify("Review the exact permanent deletion impact.")
    except (KeyError, ValueError) as error:
        session.pending_run_deletion = None
        session.notify(str(error).strip("'"), error=True)
    response = _redirect(_safe_data_return_to(form.get("return_to")))
    return _with_session(response, session_id)


@router.post("/data/delete")
async def confirm_data_deletion(request: Request) -> Response:
    session_id, session = _session(request)
    manager = _manager(request)
    form = await request.form()
    pending = session.pending_run_deletion
    try:
        if pending is None:
            raise ValueError("deletion confirmation expired; preview it again")
        submitted_ids = tuple(
            str(value) for value in form.getlist("run_id") if str(value)
        )
        if submitted_ids != pending.run_ids:
            raise ValueError("selected runs changed after deletion preview")
        if tuple(session.selected_data_run_ids) != pending.run_ids:
            raise ValueError("session selection changed after deletion preview")
        if str(form.get("selection_fingerprint", "")) != pending.selection_fingerprint:
            raise ValueError("stale or invalid run selection fingerprint")
        if str(form.get("confirmation_token", "")) != pending.confirmation_token:
            raise ValueError("stale deletion preview or confirmation token")
        if form.get("confirmed") != "yes":
            raise ValueError("confirm permanent deletion must be checked")
        if str(form.get("confirmation_phrase", "")) != pending.phrase:
            raise ValueError(f"confirmation phrase must exactly match {pending.phrase}")
        selection = RunSelection(
            run_ids=pending.run_ids,
            fingerprint=pending.selection_fingerprint,
            filters=pending.filters,
        )
        result = manager.delete_persisted_runs(
            selection,
            pending.confirmation_token,
        )
        store = cast(OperatorSessionStore, request.app.state.operator_sessions)
        store.remove_deleted_run_ids(result.run_ids)
        session.notify(
            f"Permanently deleted {len(result.run_ids)} run"
            f"{'' if len(result.run_ids) == 1 else 's'} and "
            f"{result.deleted_record_count} records."
        )
    except (KeyError, ValueError, SimulationConflictError) as error:
        session.notify(str(error).strip("'"), error=True)
    response = _redirect(_safe_data_return_to(form.get("return_to")))
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
        scenario, source_payload, resolved_elements = _parse_operator_scenario(
            request,
            (WEB_DIRECTORY / "demo.json").read_text(encoding="utf-8"),
        )
        await _stage_scenario(
            _manager(request),
            session,
            scenario,
            "bundled example",
            scenario_source=source_payload,
            resolved_elements=resolved_elements,
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        ScenarioResolutionError,
        ValueError,
    ) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Could not load example: {message}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/scenario/situations/regenerate")
async def regenerate_character_situations(request: Request) -> Response:
    session_id, session = _session(request)
    if session.scenario is None or session.scenario_id is None:
        session.notify(
            "Stage a complete scenario before regenerating character situations.",
            error=True,
        )
        return _with_session(_redirect(), session_id)
    if session.run_id is not None:
        session.notify(
            "Character situations cannot be regenerated after a run starts.",
            error=True,
        )
        return _with_session(_redirect(), session_id)
    try:
        manager = _manager(request)
        scenario_id = await manager.add_scenario(
            session.scenario,
            session.character_assignments,
        )
        session.scenario_id = scenario_id
        session.notify("Character situations were regenerated and staged.")
    except ValueError as error:
        session.notify(
            f"Could not regenerate character situations: {error}",
            error=True,
        )
    return _with_session(_redirect(), session_id)


@router.post("/scenario/upload")
async def upload_scenario(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form(max_part_size=MAX_UPLOAD_BYTES + 1)
    upload = form.get("scenario")
    if not isinstance(upload, UploadFile) or not upload.filename:
        session.notify("Choose a scenario JSON file.", error=True)
        return _with_session(_redirect(), session_id)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        session.notify("Scenario files must be 5 MB or smaller.", error=True)
        return _with_session(_redirect(), session_id)
    try:
        scenario, source_payload, resolved_elements = _parse_operator_scenario(
            request,
            content,
        )
        await _stage_scenario(
            _manager(request),
            session,
            scenario,
            upload.filename,
            scenario_source=source_payload,
            resolved_elements=resolved_elements,
        )
    except ValidationError as error:
        session.notify(f"Scenario is invalid: {_validation_message(error)}", error=True)
    except json.JSONDecodeError as error:
        session.notify(f"Scenario is invalid JSON: {error}", error=True)
    except (ScenarioResolutionError, ValueError) as error:
        session.notify(f"Scenario is invalid: {error}", error=True)
    return _with_session(_redirect(), session_id)


@router.post("/scenario/assign")
async def assign_characters(request: Request) -> Response:
    session_id, session = _session(request)
    if session.scenario is None:
        session.notify("Load a scenario before assigning characters.", error=True)
        return _with_session(_redirect(), session_id)
    form = await request.form()
    try:
        assignments: dict[str, str] = {}
        for entity in session.scenario.entities:
            if "character_slot" not in entity.components:
                continue
            character_id = str(form.get(f"character.{entity.id}", "")).strip()
            if not character_id:
                raise ValueError(f"Select a character for {entity.id}")
            assignments[entity.id] = character_id
        await _stage_scenario(
            _manager(request),
            session,
            session.scenario,
            f"{session.scenario_source} with assigned characters",
            assignments,
        )
        session.notify("Character assignments were validated and staged.")
    except (ValidationError, ValueError) as error:
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Assignment invalid: {message}", error=True)
    return _with_session(_redirect(), session_id)


def _slot_character_options(
    scenario: ScenarioDefinition | None,
    library: CharacterLibrary,
) -> dict[str, tuple[CharacterSummary, ...]]:
    if scenario is None:
        return {}
    summaries = library.list()
    reference_date = (
        scenario.calendar.start_datetime.date()
        if scenario.calendar is not None
        else None
    )
    options: dict[str, tuple[CharacterSummary, ...]] = {}
    for entity in scenario.entities:
        raw_slot = entity.components.get("character_slot")
        if raw_slot is None:
            continue
        slot = CharacterSlotDefinition.model_validate(raw_slot)
        eligible: list[CharacterSummary] = []
        for summary in summaries:
            character = library.get(summary.id)
            if character_constraint_violations(
                character,
                slot,
                reference_date=reference_date,
            ):
                continue
            eligible.append(character_summary(character, reference_date))
        options[entity.id] = tuple(eligible)
    return options


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
        npc_control_mode = str(form.get("npc_control_mode", "")).strip() or None
        session.run_id = await manager.start_run(
            session.scenario_id,
            realtime=True,
            speed=speed,
            npc_control_mode=npc_control_mode,
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
        _manager(request).mutate_vitals(session.run_id, session.selected_agent_id, values)
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
    if form.get("selected_agent_present") == "yes":
        selected_agent = str(form.get("selected_agent", "")).strip()
        session.selected_agent_id = selected_agent or None
        if session.selected_agent_id is None:
            session.follow_selected = False
    level = str(form.get("view_level", session.view_level)).lower()
    if level in {"auto", "room", "building", "city_zone", "city"}:
        session.view_level = level
    if form.get("follow_present") == "yes":
        session.follow_selected = (
            form.get("follow_selected") == "on"
            and session.selected_agent_id is not None
        )
    zoom_action = str(form.get("zoom_action", ""))
    if zoom_action == "in":
        session.zoom = min(MAX_MAP_ZOOM, session.zoom * 1.25)
    elif zoom_action == "out":
        session.zoom = max(MIN_MAP_ZOOM, session.zoom / 1.25)
    elif zoom_action == "fit":
        session.zoom = 1.0
    session.live_refresh = form.get("live_refresh") == "on"
    overlays = form.getlist("overlays")
    if form.get("overlays_present") == "yes":
        session.overlays = {str(value) for value in overlays}
    return _with_session(_redirect(), session_id)


@router.post("/view/zoom")
async def update_zoom(request: Request) -> Response:
    session_id, session = _session(request)
    form = await request.form()
    try:
        zoom = float(str(form.get("zoom", "")))
        if not math.isfinite(zoom) or not MIN_MAP_ZOOM <= zoom <= MAX_MAP_ZOOM:
            raise ValueError(
                f"zoom must be between {MIN_MAP_ZOOM} and {MAX_MAP_ZOOM}"
            )
        camera_x = _bounded_fraction(form.get("camera_x"), session.camera_x)
        camera_y = _bounded_fraction(form.get("camera_y"), session.camera_y)
    except ValueError as error:
        return _with_session(
            Response(str(error), status_code=400, media_type="text/plain"),
            session_id,
        )
    session.zoom = zoom
    session.camera_x = camera_x
    session.camera_y = camera_y
    response = Response(status_code=204)
    response.headers["X-Stage0-Semantic-Level"] = _effective_view_level(session)
    return _with_session(response, session_id)


@router.post("/events/clear")
async def clear_events(request: Request) -> Response:
    session_id, session = _session(request)
    managed = _managed_run(_manager(request), session)
    session.event_start_index = len(managed.runner.events.events) if managed is not None else 0
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
            item for item in summaries if search in f"{item.id} {item.display_name}".casefold()
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
            "selected_hash": (character_content_hash(selected) if selected is not None else ""),
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
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
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
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
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
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
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
        _library(request).delete(character_id, str(form.get("expected_hash", "")))
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
        message = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        session.notify(f"Could not import character: {message}", error=True)
    return _with_session(_redirect(target), session_id)


@router.get("/characters/{character_id}/download")
async def download_character(character_id: str, request: Request) -> Response:
    try:
        character = _library(request).get(character_id)
    except CharacterLibraryError as error:
        return Response(str(error), status_code=404, media_type="text/plain")
    payload = json.dumps(
        character.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    return Response(
        f"{payload}\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{character_id}.json"'},
    )


def _character_from_form(
    form: Any,
    current: CharacterDefinition | None,
) -> CharacterDefinition:
    raw: dict[str, Any]
    if current is None:
        raw = {
            "schema_version": 2,
            "id": str(form.get("id", "")).strip(),
            "template_id": str(form.get("template_id", "human-v1")).strip(),
            "identity": {},
        }
    else:
        raw = current.model_dump(mode="python")
        raw["id"] = str(form.get("id", current.id)).strip()
        raw["template_id"] = str(form.get("template_id", current.template_id)).strip()
    for section in CHARACTER_SECTIONS:
        section_id = cast(str, section["id"])
        values = cast(dict[str, Any], raw.setdefault(section_id, {}))
        for field_name, _label, field_type, _required in cast(
            tuple[tuple[str, str, str, bool], ...], section["fields"]
        ):
            text = str(form.get(f"{section_id}.{field_name}", "")).strip()
            if field_type == "list":
                values[field_name] = [line.strip() for line in text.splitlines() if line.strip()]
            elif field_type == "number":
                values[field_name] = int(text) if text else None
            elif field_type == "decimal":
                values[field_name] = float(text) if text else None
            elif field_type == "money":
                values[field_name] = int(text) if text else None
            elif field_type == "json_array":
                values[field_name] = _parse_json_array(text, field_name.replace("_", " ").title())
            elif field_type == "date":
                values[field_name] = text or None
            else:
                values[field_name] = text
    raw["relationships"] = _parse_json_array(str(form.get("relationships", "[]")), "Relationships")
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
            elif field_type == "json_array":
                values[f"{section_id}.{field_name}"] = json.dumps(
                    value or [],
                    ensure_ascii=False,
                    indent=2,
                )
            elif value is not None:
                values[f"{section_id}.{field_name}"] = str(value)
    values["relationships"] = json.dumps(raw.get("relationships", []), ensure_ascii=False, indent=2)
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
    return character.identity.display_name if character.identity is not None else character.id


def _control_availability(managed: ManagedRun | None, scenario_id: str | None) -> dict[str, bool]:
    status = managed.runner.status if managed is not None else None
    return {
        "start": scenario_id is not None and (status is None or status is RunnerStatus.STOPPED),
        "pause": status is RunnerStatus.RUNNING,
        "resume": status is RunnerStatus.PAUSED,
        "step": status is RunnerStatus.PAUSED,
        "stop": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
        "speed": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
        "vitals": status in {RunnerStatus.RUNNING, RunnerStatus.PAUSED},
    }


def _filter_events(events: list[DomainEvent], event_filter: str, search: str) -> list[DomainEvent]:
    pattern = EVENT_FILTERS.get(event_filter)
    filtered = []
    for event in events:
        if pattern is not None and pattern.search(event.event_type) is None:
            continue
        if search and search not in json.dumps(event.to_dict(), ensure_ascii=False).casefold():
            continue
        filtered.append(event)
    return filtered


def _dataset_include_private(request: Request) -> bool:
    return request.query_params.get("include_private", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _data_run_filters(values: Any) -> PersistedRunFilter:
    def optional(name: str) -> str | None:
        value = values.get(name)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    capture_value = optional("capture_complete")
    if capture_value not in {None, "true", "false"}:
        raise ValueError("capture completeness must be true or false")
    try:
        limit = int(optional("limit") or "25")
    except ValueError as error:
        raise ValueError("runs per page must be an integer") from error

    def parsed_datetime(name: str) -> datetime | None:
        value = optional(name)
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            label = name.replace("_", " ")
            raise ValueError(f"{label} is not a valid date") from error

    persisted_status = optional("persisted_status")
    effective_status = optional("status")
    return PersistedRunFilter(
        search_text=optional("search"),
        persisted_statuses=(persisted_status,) if persisted_status else (),
        effective_statuses=(effective_status,) if effective_status else (),
        scenario_name=optional("scenario"),
        dataset_schema_version=optional("schema"),
        capture_complete=(
            capture_value == "true" if capture_value is not None else None
        ),
        started_at_or_after=parsed_datetime("started_after"),
        started_before=parsed_datetime("started_before"),
        completed_at_or_after=parsed_datetime("completed_after"),
        completed_before=parsed_datetime("completed_before"),
        cursor=optional("cursor"),
        limit=limit,
    )


def _data_filter_values(request: Request) -> dict[str, str]:
    values = {
        name: request.query_params.get(name, "")
        for name in (
            "search",
            "persisted_status",
            "status",
            "scenario",
            "schema",
            "capture_complete",
            "started_after",
            "started_before",
            "completed_after",
            "completed_before",
            "cursor",
            "limit",
        )
    }
    values["limit"] = values["limit"] or "25"
    return values


def _current_data_selection(
    manager: SimulationManager,
    session: OperatorSession,
) -> RunSelection | None:
    if not session.selected_data_run_ids:
        return None
    try:
        selection = manager.data_management.selection(
            session.selected_data_run_ids,
            session.selected_data_filters,
        )
    except KeyError:
        available: list[str] = []
        for run_id in session.selected_data_run_ids:
            try:
                manager.data_management.selection((run_id,))
            except KeyError:
                continue
            available.append(run_id)
        session.selected_data_run_ids = tuple(available)
        session.pending_run_deletion = None
        session.selected_data_filters = None
        if not session.selected_data_run_ids:
            return None
        selection = manager.data_management.selection(
            session.selected_data_run_ids,
            session.selected_data_filters,
        )
    session.selected_data_run_ids = selection.run_ids
    return selection


def _data_management_url(
    params: dict[str, str],
    *,
    updates: dict[str, str | None] | None = None,
    remove: tuple[str, ...] = (),
) -> str:
    resolved = dict(params)
    for key in remove:
        resolved.pop(key, None)
    if updates is not None:
        for key, value in updates.items():
            if value is None:
                resolved.pop(key, None)
            else:
                resolved[key] = value
    query = urlencode([(key, value) for key, value in resolved.items() if value])
    return f"/ui/data/{'?' + query if query else ''}"


def _aggregate_export_urls(
    selection: RunSelection | None,
    include_private_derived: bool,
) -> tuple[str | None, str | None]:
    if selection is None:
        return None, None
    parameters = [
        ("run_id", run_id) for run_id in selection.run_ids
    ] + [
        ("selection_fingerprint", selection.fingerprint),
        (
            "include_private_derived",
            str(include_private_derived).lower(),
        ),
    ]
    if selection.filters is not None:
        parameters.append(
            (
                "selection_filters",
                json.dumps(
                    selection.filters.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    query = urlencode(parameters)
    return (
        f"/simulation/data/aggregate.json?{query}",
        f"/simulation/data/aggregate.csv?{query}",
    )


def _aggregate_sections(
    aggregate: AggregateDatasetSummary,
) -> tuple[dict[str, Any], ...]:
    specs = (
        ("Outcomes and records", ("run.", "record."), ("records.", "run.")),
        ("Models and tools", ("model_", "tool_"), ("models.", "tools.")),
        ("Goals", ("goals.",), ("goals.",)),
        ("Actions", ("actions.",), ("actions.",)),
        ("Interactions", ("interactions.",), ("interactions.",)),
        ("Population", ("population.",), ("population.",)),
        (
            "Transitions and opportunities",
            ("transitions.", "opportunities."),
            ("transitions.", "opportunities."),
        ),
        ("Resources", ("resource_",), ("resources.",)),
    )
    sections: list[dict[str, Any]] = []
    for title, distribution_prefixes, metric_prefixes in specs:
        distributions = tuple(
            (name, values)
            for name, values in aggregate.distributions.items()
            if name.startswith(distribution_prefixes)
        )
        metrics = tuple(
            metric
            for metric in aggregate.metrics
            if metric.name.startswith(metric_prefixes)
        )
        sections.append(
            {
                "id": re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-"),
                "title": title,
                "distributions": distributions,
                "metrics": metrics,
            }
        )
    return tuple(sections)


def _safe_data_return_to(value: object) -> str:
    parsed = urlsplit(str(value or "/ui/data/"))
    if parsed.path != "/ui/data/":
        return "/ui/data/"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _dataset_filter_values(request: Request) -> dict[str, str]:
    values = {
        key: request.query_params.get(key, "")
        for key in DATASET_FILTER_KEYS
    }
    values["limit"] = values["limit"] or "25"
    return values


def _dataset_optional_value(request: Request, name: str) -> str | None:
    value = request.query_params.get(name, "").strip()
    return value or None


def _dataset_optional_int(request: Request, name: str) -> int | None:
    value = _dataset_optional_value(request, name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < 0:
        raise ValueError(f"{name} must not be negative")
    return parsed


def _dataset_optional_float(request: Request, name: str) -> float | None:
    value = _dataset_optional_value(request, name)
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed


def _dataset_filters(
    request: Request,
    *,
    raw_records: bool,
) -> tuple[DatasetQueryFilter, DatasetRecordFilter]:
    category_value = _dataset_optional_value(request, "category")
    visibility_value = _dataset_optional_value(request, "visibility")
    try:
        category = (
            RecordCategory(category_value)
            if category_value is not None
            else None
        )
    except ValueError as error:
        raise ValueError(f"unknown record category: {category_value}") from error
    try:
        visibility = (
            RecordVisibility(visibility_value)
            if visibility_value is not None
            else None
        )
    except ValueError as error:
        raise ValueError(
            f"unknown record visibility: {visibility_value}"
        ) from error
    include_private = _dataset_include_private(request)
    if (
        visibility is RecordVisibility.PRIVATE_RESEARCH
        and not include_private
    ):
        raise ValueError(
            "Select “Include private research data” before filtering for "
            "PRIVATE_RESEARCH."
        )
    raw_limit = _dataset_optional_value(request, "limit") or "25"
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise ValueError("limit must be an integer") from error
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    minimum_tick = _dataset_optional_int(request, "minimum_tick")
    maximum_tick = _dataset_optional_int(request, "maximum_tick")
    minimum_time = _dataset_optional_float(request, "minimum_time")
    maximum_time = _dataset_optional_float(request, "maximum_time")
    domain_ids = {
        name: _dataset_optional_value(request, name)
        for name, _ in DATASET_DOMAIN_FILTERS
    }
    common: dict[str, Any] = {
        "record_type": _dataset_optional_value(request, "record_type"),
        "category": category,
        "schema_id": _dataset_optional_value(request, "schema_id"),
        "schema_version": _dataset_optional_value(request, "schema_version"),
        "related_entity_id": _dataset_optional_value(
            request,
            "related_entity_id",
        ),
        "minimum_tick": minimum_tick,
        "maximum_tick": maximum_tick,
        "minimum_time": minimum_time,
        "maximum_time": maximum_time,
        "visibility": visibility,
        **domain_ids,
        "status": _dataset_optional_value(request, "status"),
        "outcome": _dataset_optional_value(request, "outcome"),
        "include_private": include_private,
        "limit": limit,
    }
    query_filter = DatasetQueryFilter(
        **common,
        primary_entity_id=_dataset_optional_value(request, "entity_id"),
        cursor=_dataset_optional_value(request, "cursor"),
    )
    after_sequence = (
        _dataset_optional_int(request, "cursor") if raw_records else None
    )
    record_filter = DatasetRecordFilter(
        **common,
        subject_id=_dataset_optional_value(request, "entity_id"),
        after_sequence=after_sequence,
    )
    return query_filter, record_filter


def _dataset_preserved_params(
    request: Request,
    *,
    view: str,
) -> dict[str, str]:
    values = {
        key: request.query_params.get(key, "")
        for key in (*DATASET_FILTER_KEYS, "cursor")
        if request.query_params.get(key, "") != ""
    }
    values["view"] = view
    if "limit" not in values:
        values["limit"] = "25"
    if _dataset_include_private(request):
        values["include_private"] = "true"
    else:
        values.pop("include_private", None)
    return values


def _dataset_url(
    route: str,
    values: dict[str, str],
    *,
    updates: dict[str, str | None] | None = None,
    remove: tuple[str, ...] = (),
) -> str:
    parameters = dict(values)
    for key in remove:
        parameters.pop(key, None)
    for key, value in (updates or {}).items():
        if value is None or value == "":
            parameters.pop(key, None)
        else:
            parameters[key] = value
    query = urlencode(parameters)
    return f"{route}?{query}" if query else route


def _dataset_entry(
    view: str,
    data: dict[str, JsonValue],
    index: int,
) -> dict[str, Any]:
    if view == "records":
        sequence = data.get("sequence", index)
        record_type = data.get("record_type", "record")
        title = f"Sequence {sequence} · {record_type}"
        visibility = data.get("visibility", "")
        tick = data.get("simulation_tick")
    else:
        sequence = data.get("record_sequence", index)
        identifier = next(
            (
                str(data[key])
                for key in (
                    "goal_id",
                    "decision_id",
                    "action_id",
                    "interaction_id",
                    "transition_id",
                    "population_sample_id",
                    "resource_sample_id",
                    "resource_flow_id",
                )
                if data.get(key) is not None
            ),
            f"row {index}",
        )
        status = next(
            (
                str(data[key])
                for key in ("status", "terminal_status", "to_status")
                if data.get(key) is not None
            ),
            "",
        )
        title = f"Sequence {sequence} · {identifier}"
        if status:
            title = f"{title} · {status}"
        visibility = data.get("record_visibility", "")
        tick = data.get("record_simulation_tick")
    return {
        "title": title,
        "visibility": str(visibility),
        "tick": tick,
        "data": data,
    }


def event_summary(event: DomainEvent) -> str:
    preferred = (
        "text",
        "tool_name",
        "action",
        "target_id",
        "recipient_ids",
        "drive",
        "station_id",
        "point_id",
        "offer_id",
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
                    f"tick {event.simulation_tick}: heard {speaker}: "
                    f'"{event.payload.get("text", "")}"'
                )
        if len(rows) >= 20:
            break
    return rows


def _bounded_fraction(value: object, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    parsed = float(str(value))
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError("camera coordinates must be between 0 and 1")
    return parsed


def _effective_view_level(session: OperatorSession) -> str:
    if session.view_level != "auto":
        return session.view_level
    if session.zoom >= ROOM_ZOOM:
        return "room"
    if session.zoom >= BUILDING_ZOOM:
        return "building"
    if session.zoom >= CITY_ZONE_ZOOM:
        return "city_zone"
    return "city"


def _camera_building_id(
    city_world: CityWorldDefinition, session: OperatorSession
) -> str | None:
    payload = city_world.model_dump(mode="json")
    bounds = cast(dict[str, float], payload["city"]["bounds_meters"])
    x = float(bounds["min_x"]) + session.camera_x * (
        float(bounds["max_x"]) - float(bounds["min_x"])
    )
    y = float(bounds["min_y"]) + session.camera_y * (
        float(bounds["max_y"]) - float(bounds["min_y"])
    )
    buildings = cast(list[dict[str, Any]], payload.get("buildings", []))
    if not buildings:
        return None
    closest = min(
        buildings,
        key=lambda item: (
            (float(item["city_position"]["x"]) - x) ** 2
            + (float(item["city_position"]["y"]) - y) ** 2,
            str(item["id"]),
        ),
    )
    return str(closest["id"])


def _camera_room(
    rooms: list[RoomDefinition],
    session: OperatorSession,
) -> RoomDefinition:
    width = max(room.offset.x + room.world.width for room in rooms)
    height = max(room.offset.y + room.world.height for room in rooms)
    target_x = session.camera_x * max(1, width)
    target_y = session.camera_y * max(1, height)
    return min(
        rooms,
        key=lambda room: (
            (
                room.offset.x + room.world.width / 2 - target_x
            )
            ** 2
            + (
                room.offset.y + room.world.height / 2 - target_y
            )
            ** 2,
            room.id,
        ),
    )


def _location_city_point(
    city: dict[str, Any], location: dict[str, JsonValue]
) -> tuple[float, float] | None:
    edge_id = location.get("edge_id")
    edge_progress = location.get("edge_progress")
    if isinstance(edge_id, str) and isinstance(edge_progress, (int, float)):
        edge = next(
            (item for item in city.get("edges", []) if item.get("id") == edge_id),
            None,
        )
        if edge is not None:
            points = [
                (float(point["x"]), float(point["y"]))
                for point in edge.get("geometry", [])
            ]
            interpolated = _interpolate_points(points, float(edge_progress))
            if interpolated is not None:
                return interpolated
    node_id = location.get("network_node_id")
    node = next(
        (item for item in city.get("nodes", []) if item.get("id") == node_id),
        None,
    )
    if node is not None:
        point = node["position"]
        return float(point["x"]), float(point["y"])
    place_id = location.get("building_id") or location.get("place_id")
    place = next(
        (
            item
            for item in [*city.get("buildings", []), *city.get("outdoor_places", [])]
            if item.get("id") == place_id
        ),
        None,
    )
    if place is None:
        return None
    point = place["position"]
    return float(point["x"]), float(point["y"])


def _normalize_city_point(
    city: dict[str, Any], point: tuple[float, float]
) -> tuple[float, float]:
    bounds = cast(dict[str, float], city["bounds"])
    span_x = max(1.0, float(bounds["max_x"]) - float(bounds["min_x"]))
    span_y = max(1.0, float(bounds["max_y"]) - float(bounds["min_y"]))
    return (
        min(1.0, max(0.0, (point[0] - float(bounds["min_x"])) / span_x)),
        min(1.0, max(0.0, (point[1] - float(bounds["min_y"])) / span_y)),
    )


def _project_city_point(
    point: dict[str, Any],
    min_x: float,
    min_y: float,
    span_x: float,
    span_y: float,
) -> tuple[float, float]:
    return (
        (float(point["x"]) - min_x) / span_x * 1000,
        (float(point["y"]) - min_y) / span_y * 650,
    )


def _marker_offset(index: int) -> tuple[float, float]:
    if index == 0:
        return 0.0, 0.0
    angle = (index - 1) * (math.pi / 3)
    radius = 13.0 * (1 + (index - 1) // 6)
    return math.cos(angle) * radius, math.sin(angle) * radius


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
    level = _effective_view_level(session)
    if not isinstance(city, dict):
        level = "building"
    selected_location = (
        cast(dict[str, JsonValue], selected_agent.get("spatial_location", {}))
        if selected_agent
        else {}
    )
    overlays = _world_overlays(agents, events, session.selected_agent_id)
    city_world_value = scenario.world if scenario is not None else None
    if level in {"room", "building"} and isinstance(
        city_world_value,
        CityWorldDefinition,
    ):
        city_world = city_world_value
        building_id = _camera_building_id(city_world, session)
        selected_room = None
        if session.follow_selected and selected_location.get("place_id"):
            selected_place_id = str(selected_location["place_id"])
            selected_room = next(
                (
                    item
                    for item in city_world.rooms
                    if item.id == selected_place_id
                ),
                None,
            )
            if selected_room is not None:
                building_id = selected_room.building_id
            elif any(
                item.id == selected_place_id
                for item in city_world.buildings
            ):
                building_id = selected_place_id
        building = next(
            (item for item in city_world.buildings if item.id == building_id),
            None,
        )
        if building is not None:
            building_rooms = [
                item
                for item in city_world.rooms
                if item.building_id == building.id
            ]
            if not building_rooms:
                return None
            room = (
                selected_room
                if selected_room is not None
                and selected_room.building_id == building.id
                else _camera_room(building_rooms, session)
            )
            local_world = _apply_runtime_environment(
                room.world.model_dump(mode="json"),
                snapshot,
            )
            local_agents = [
                agent
                for agent in agents
                if isinstance(agent.get("spatial_location"), dict)
                and cast(dict[str, JsonValue], agent["spatial_location"]).get("place_id")
                == room.id
            ]
            title = f"Building view · {building.name} · {room.name}"
            if level == "room":
                title = f"Room view · {room.name} · {building.name}"
            return _grid_view(
                local_world,
                local_agents,
                session,
                title,
                overlays,
                semantic_level=level,
            )
    if level == "building" and isinstance(static_world, dict):
        return _grid_view(
            _apply_runtime_environment(static_world, snapshot),
            agents,
            session,
            "Building view",
            overlays,
        )
    if isinstance(city, dict):
        if session.follow_selected and selected_location:
            followed_point = _location_city_point(city, selected_location)
            if followed_point is not None:
                session.camera_x, session.camera_y = _normalize_city_point(
                    city, followed_point
                )
        city_payload = _apply_runtime_environment(
            _city_zone_payload(city, session) if level == "city_zone" else city
            ,
            snapshot,
        )
        vehicle_states = cast(dict[str, JsonValue], snapshot.get("world", {})).get(
            "vehicle_states", []
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
        return _grid_view(
            _apply_runtime_environment(static_world, snapshot),
            agents,
            session,
            "World view",
            overlays,
        )
    return None


def _apply_runtime_environment(
    world: dict[str, Any],
    snapshot: dict[str, JsonValue],
) -> dict[str, Any]:
    environment = snapshot.get("environment")
    if not isinstance(environment, dict):
        return world
    raw_availability = environment.get("availability")
    availability = {
        str(item["resource_id"]): item
        for item in raw_availability
        if isinstance(item, dict) and "resource_id" in item
    } if isinstance(raw_availability, list) else {}
    raw_surfaces = environment.get("surface_conditions")
    surfaces = {
        str(item["surface_id"]): item
        for item in raw_surfaces
        if isinstance(item, dict) and "surface_id" in item
    } if isinstance(raw_surfaces, list) else {}
    runtime_world = snapshot.get("world")
    raw_point_states = (
        runtime_world.get("transaction_point_states")
        if isinstance(runtime_world, dict)
        else None
    )
    point_states = {
        str(item["id"]): item
        for item in raw_point_states
        if isinstance(item, dict) and "id" in item
    } if isinstance(raw_point_states, list) else {}

    def annotate(
        items: list[dict[str, Any]],
        *,
        surface_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                **item,
                "environment_availability": availability.get(str(item.get("id"))),
                "surface_condition": (
                    surfaces.get(f"{surface_prefix}:{item.get('id')}")
                    if surface_prefix is not None
                    else None
                ),
            }
            for item in items
        ]

    return {
        **world,
        "stations": annotate(list(world.get("stations", []))),
        "transaction_points": [
            {
                **item,
                "environment_availability": availability.get(
                    str(item.get("id"))
                ),
                "runtime": point_states.get(str(item.get("id"))),
            }
            for item in world.get("transaction_points", [])
        ],
        "buildings": annotate(list(world.get("buildings", []))),
        "outdoor_places": annotate(
            list(world.get("outdoor_places", [])),
            surface_prefix="place",
        ),
        "edges": annotate(
            list(world.get("edges", [])),
            surface_prefix="edge",
        ),
        "vehicles": annotate(list(world.get("vehicles", []))),
    }


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
        return _grid_view(payload, agents, session, "Staged scenario preview", {})
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
    *,
    semantic_level: str = "building",
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
            destination_point = movement.get("destination") if isinstance(movement, dict) else None
            rendered_agents.append(
                {
                    "id": agent["id"],
                    "name": _agent_name(agent),
                    "actor_kind": agent.get("actor_kind", "character"),
                    "x": position.get("x", 0),
                    "y": position.get("y", 0),
                    "selected": agent["id"] == session.selected_agent_id,
                    "system1": cast(dict[str, JsonValue], agent.get("system1", {})).get("state"),
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
                    f"{_number(point.get('x')) + 0.5},{_number(point.get('y')) + 0.5}"
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
        "base_display_width": width * 72,
        "display_width": width * 72 * session.zoom,
        "semantic_level": semantic_level,
        "follow_selected": session.follow_selected,
        "camera_x": session.camera_x,
        "camera_y": session.camera_y,
        "zones": zones,
        "blocked": world.get("blocked", []),
        "stations": world.get("stations", []),
        "transaction_points": world.get("transaction_points", []),
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
        return _project_city_point(point, min_x, min_y, span_x, span_y)

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
    projected_nodes = [
        {
            **node,
            "px": nodes[str(node["id"])][0],
            "py": nodes[str(node["id"])][1],
        }
        for node in city.get("nodes", [])
    ]
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
    point_counts: dict[tuple[int, int], int] = {}
    unplaced_agent_ids: list[str] = []
    for agent in sorted(agents, key=lambda item: str(item["id"])):
        location = agent.get("spatial_location")
        if not isinstance(location, dict):
            unplaced_agent_ids.append(str(agent["id"]))
            continue
        point = None
        edge_id = location.get("edge_id")
        edge_progress = location.get("edge_progress")
        if isinstance(edge_id, str) and isinstance(edge_progress, (int, float)):
            point = _interpolate_points(edge_points.get(edge_id, []), float(edge_progress))
        if point is None:
            point = nodes.get(str(location.get("network_node_id")))
        if point is None:
            point = building_points.get(
                str(location.get("building_id") or location.get("place_id"))
            ) or place_points.get(str(location.get("place_id")))
        if point is None:
            unplaced_agent_ids.append(str(agent["id"]))
            continue
        key = (round(point[0]), round(point[1]))
        offset_index = point_counts.get(key, 0)
        point_counts[key] = offset_index + 1
        offset_x, offset_y = _marker_offset(offset_index)
        rendered_agents.append(
            {
                "id": agent["id"],
                "name": _agent_name(agent),
                "actor_kind": agent.get("actor_kind", "character"),
                "px": point[0] + offset_x,
                "py": point[1] + offset_y,
                "selected": agent["id"] == session.selected_agent_id,
                "visible": bool(overlays.get(str(agent["id"]), {}).get("visible")),
                "speech": overlays.get(str(agent["id"]), {}).get("speech"),
            }
        )
    vehicle_definitions = {str(vehicle["id"]): vehicle for vehicle in city.get("vehicles", [])}
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
    districts = [
        {
            **district,
            "px": project(cast(dict[str, Any], district["center"]))[0],
            "py": project(cast(dict[str, Any], district["center"]))[1],
        }
        for district in city.get("districts", [])
    ]
    return {
        "kind": "city",
        "title": f"{title} · {city.get('name', 'City')}",
        "view_box": "0 0 1000 650",
        "base_display_width": 1000,
        "display_width": 1000 * session.zoom,
        "semantic_level": _effective_view_level(session),
        "follow_selected": session.follow_selected,
        "camera_x": session.camera_x,
        "camera_y": session.camera_y,
        "districts": districts,
        "buildings": buildings,
        "places": places,
        "nodes": projected_nodes,
        "edges": edges,
        "agents": rendered_agents,
        "vehicles": rendered_vehicles,
        "unplaced_agent_ids": unplaced_agent_ids,
        "labels": _city_labels(
            districts,
            buildings,
            places,
            rendered_agents,
            rendered_vehicles,
            session.zoom,
        ),
    }


def _city_labels(
    districts: list[dict[str, Any]],
    buildings: list[dict[str, Any]],
    places: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    zoom: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(
        *,
        kind: str,
        item_id: object,
        text: object,
        x: float,
        y: float,
        priority: int,
        minimum_zoom: float,
    ) -> None:
        if zoom < minimum_zoom:
            return
        candidates.append(
            {
                "kind": kind,
                "id": str(item_id),
                "text": str(text),
                "x": x,
                "y": y,
                "priority": priority,
                "width": max(36.0, len(str(text)) * 7.0),
                "height": 16.0,
            }
        )

    for district in districts:
        add(
            kind="district",
            item_id=district["id"],
            text=district["name"],
            x=float(district["px"]),
            y=float(district["py"]) - 22,
            priority=20,
            minimum_zoom=0.5,
        )
    for building in buildings:
        add(
            kind="building",
            item_id=building["id"],
            text=building["name"],
            x=float(building["px"]) + 13,
            y=float(building["py"]) + 4,
            priority=40,
            minimum_zoom=1.25,
        )
    for place in places:
        add(
            kind="place",
            item_id=place["id"],
            text=place["name"],
            x=float(place["px"]) + 11,
            y=float(place["py"]) + 4,
            priority=50,
            minimum_zoom=1.25,
        )
    for vehicle in vehicles:
        add(
            kind="vehicle",
            item_id=vehicle["id"],
            text=vehicle["name"],
            x=float(vehicle["px"]) + 10,
            y=float(vehicle["py"]) - 8,
            priority=70,
            minimum_zoom=2.0,
        )
    for agent in agents:
        add(
            kind="character",
            item_id=agent["id"],
            text=agent["name"],
            x=float(agent["px"]) + 12,
            y=float(agent["py"]) - 10,
            priority=100 if agent["selected"] else 90,
            minimum_zoom=1.0,
        )

    accepted: list[dict[str, Any]] = []
    occupied: list[tuple[float, float, float, float]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-int(item["priority"]), str(item["kind"]), str(item["id"])),
    ):
        left = float(candidate["x"])
        top = float(candidate["y"]) - float(candidate["height"])
        box = (
            left,
            top,
            left + float(candidate["width"]),
            top + float(candidate["height"]),
        )
        if any(_boxes_overlap(box, prior) for prior in occupied):
            continue
        occupied.append(box)
        accepted.append(candidate)
    return sorted(accepted, key=lambda item: (str(item["kind"]), str(item["id"])))


def _boxes_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[2] + 4 <= right[0]
        or right[2] + 4 <= left[0]
        or left[3] + 4 <= right[1]
        or right[3] + 4 <= left[1]
    )


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


def _city_zone_payload(
    city: dict[str, Any], session: OperatorSession
) -> dict[str, Any]:
    bounds = cast(dict[str, float], city["bounds"])
    target_x = float(bounds["min_x"]) + session.camera_x * (
        float(bounds["max_x"]) - float(bounds["min_x"])
    )
    target_y = float(bounds["min_y"]) + session.camera_y * (
        float(bounds["max_y"]) - float(bounds["min_y"])
    )
    districts = city.get("districts", [])
    if not districts:
        return city
    selected_district = min(
        districts,
        key=lambda item: (
            (float(item["center"]["x"]) - target_x) ** 2
            + (float(item["center"]["y"]) - target_y) ** 2,
            str(item["id"]),
        ),
    )
    district_id = selected_district.get("id")
    buildings = [
        item for item in city.get("buildings", []) if item.get("district_id") == district_id
    ]
    places = [
        item for item in city.get("outdoor_places", []) if item.get("district_id") == district_id
    ]
    place_ids = {str(item["id"]) for item in [*buildings, *places] if "id" in item}
    nodes = [item for item in city.get("nodes", []) if item.get("place_id") in place_ids]
    node_ids = {str(item["id"]) for item in nodes}
    edges = [
        item
        for item in city.get("edges", [])
        if item.get("from_node_id") in node_ids or item.get("to_node_id") in node_ids
    ]
    return {
        **city,
        "name": f"{city.get('name', 'City')} · {district_id}",
        "buildings": buildings,
        "outdoor_places": places,
        "nodes": nodes,
        "edges": edges,
    }


def _room_payload(
    world: dict[str, Any],
    session: OperatorSession,
) -> tuple[dict[str, Any], str]:
    zones = list(world.get("zones", []))
    if not zones:
        return world, "Unpartitioned interior"
    width = max(1, int(world.get("width", 1)))
    height = max(1, int(world.get("height", 1)))
    target_x = session.camera_x * width
    target_y = session.camera_y * height
    selected = min(
        zones,
        key=lambda zone: (
            min(
                (
                    (float(point["x"]) - target_x) ** 2
                    + (float(point["y"]) - target_y) ** 2
                    for point in _zone_tiles(zone)
                ),
                default=float("inf"),
            ),
            str(zone.get("id", "")),
        ),
    )
    tiles = {
        (int(point["x"]), int(point["y"]))
        for point in _zone_tiles(selected)
    }
    blocked = {
        (int(point["x"]), int(point["y"]))
        for point in world.get("blocked", [])
    }
    blocked.update(
        (x, y)
        for y in range(height)
        for x in range(width)
        if (x, y) not in tiles
    )
    return (
        {
            **world,
            "blocked": [
                {"x": x, "y": y}
                for x, y in sorted(
                    blocked,
                    key=lambda item: (item[1], item[0]),
                )
            ],
            "zones": [selected],
            "stations": [
                item
                for item in world.get("stations", [])
                if (
                    int(item["position"]["x"]),
                    int(item["position"]["y"]),
                )
                in tiles
            ],
            "transaction_points": [
                item
                for item in world.get("transaction_points", [])
                if (
                    int(item["position"]["x"]),
                    int(item["position"]["y"]),
                )
                in tiles
            ],
        },
        str(selected.get("name", selected.get("id", "Room"))),
    )


def _agent_is_in_room(
    agent: dict[str, JsonValue],
    world: dict[str, Any],
) -> bool:
    position = agent.get("position")
    if not isinstance(position, dict):
        return False
    return any(
        point.get("x") == position.get("x")
        and point.get("y") == position.get("y")
        for zone in world.get("zones", [])
        for point in _zone_tiles(zone)
    )


def _zone_tiles(zone: dict[str, Any]) -> list[dict[str, int]]:
    raw_tiles = zone.get("tiles")
    if isinstance(raw_tiles, list):
        return [
            cast(dict[str, int], point)
            for point in raw_tiles
            if isinstance(point, dict)
        ]
    bounds = zone.get("bounds")
    if not isinstance(bounds, dict):
        return []
    return [
        {"x": x, "y": y}
        for y in range(
            int(bounds["y"]),
            int(bounds["y"]) + int(bounds["height"]),
        )
        for x in range(
            int(bounds["x"]),
            int(bounds["x"]) + int(bounds["width"]),
        )
    ]


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
