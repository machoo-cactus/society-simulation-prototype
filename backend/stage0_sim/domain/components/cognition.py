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
