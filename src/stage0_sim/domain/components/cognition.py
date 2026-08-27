from dataclasses import dataclass, field


@dataclass(slots=True)
class PlannerComponent:
    daily_goals: tuple[str, ...] = ()
    needs_plan: bool = True
    request_count: int = 0
    failure_count: int = 0
    last_planned_at: float | None = None
    request_pending: bool = False


@dataclass(slots=True)
class ConversationComponent:
    turns: list[str] = field(default_factory=list)
    request_pending: bool = False


@dataclass(frozen=True, slots=True)
class CharacterProfileComponent:
    display_name: str
    role: str = ""
    traits: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    relationships: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display_name:
            raise ValueError("character display_name must not be empty")


@dataclass(slots=True)
class ControllerComponent:
    enabled: bool = False
    tool_allowlist: tuple[str, ...] = ("go_to", "perform", "say", "wait")
    state_revision: int = 0
    decision_sequence: int = 0
    request_pending: bool = False
    current_decision_id: str | None = None
    last_outcome: str | None = None
