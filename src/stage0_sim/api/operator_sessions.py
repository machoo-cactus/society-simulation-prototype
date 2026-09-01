from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from fastapi import Request, Response

from stage0_sim.application.data_management import PersistedRunFilter
from stage0_sim.application.scenario import ScenarioDefinition

SESSION_COOKIE = "stage0_operator_session"


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


def operator_session(request: Request) -> tuple[str, OperatorSession]:
    store = cast(OperatorSessionStore, request.app.state.operator_sessions)
    return store.get(request.cookies.get(SESSION_COOKIE))


def attach_operator_session(response: Response, session_id: str) -> Response:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
    )
    return response
