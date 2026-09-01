from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.components.physiology import ActivityType
from stage0_sim.domain.world import TravelMode


class ActionType(StrEnum):
    MOVE_TO = "MOVE_TO"
    WORK = "WORK"
    SOCIALIZE = "SOCIALIZE"
    READ = "READ"
    EAT = "EAT"
    SLEEP = "SLEEP"
    RELAX = "RELAX"
    IDLE = "IDLE"
    TRAVEL_TO = "TRAVEL_TO"
    NAVIGATE = "NAVIGATE"
    TRANSACT = "TRANSACT"
    SERVE_TRANSACTION = "SERVE_TRANSACTION"


class ActionOrigin(StrEnum):
    SCENARIO = "scenario"
    PLANNER = "planner"
    CONTROLLER = "controller"
    SYSTEM1 = "system1"
    OPERATOR = "operator"


class GoalLinkKind(StrEnum):
    DECLARED = "declared"
    CONTEXTUAL = "contextual"


@dataclass(frozen=True, slots=True)
class PlanAction:
    action: ActionType
    target: str | None = None
    duration: float | None = None
    mode: TravelMode | None = None
    offer_id: str | None = None

    def __post_init__(self) -> None:
        if self.duration is not None and self.duration <= 0:
            raise ValueError("action duration must be greater than zero")
        if self.action is ActionType.TRANSACT:
            if self.target is None or self.offer_id is None:
                raise ValueError("TRANSACT requires target and offer_id")
            if self.mode is not None:
                raise ValueError(
                    "mode is only valid for TRAVEL_TO or NAVIGATE"
                )
        elif self.action is ActionType.SERVE_TRANSACTION:
            if self.target is None:
                raise ValueError(
                    "SERVE_TRANSACTION requires a transaction request target"
                )
            if self.mode is not None or self.offer_id is not None:
                raise ValueError(
                    "SERVE_TRANSACTION does not accept mode or offer_id"
                )
        elif self.offer_id is not None:
            raise ValueError("offer_id is only valid for TRANSACT")


@dataclass(frozen=True, slots=True)
class ActionGoalLink:
    goal_id: str
    kind: GoalLinkKind

    def __post_init__(self) -> None:
        if not self.goal_id:
            raise ValueError("action goal link ID must not be empty")


@dataclass(frozen=True, slots=True)
class ActionInstance:
    action_id: str
    origin: ActionOrigin
    created_tick: int
    created_at: float
    root_correlation_id: str
    specification: PlanAction | None = None
    action_name: str = ""
    target_id: str | None = None
    plan_id: str | None = None
    plan_revision: int | None = None
    goal_links: tuple[ActionGoalLink, ...] = ()
    decision_id: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.root_correlation_id:
            raise ValueError("action identity and root correlation must not be empty")
        if self.created_tick < 0 or self.created_at < 0:
            raise ValueError("action creation tick/time must not be negative")
        if self.plan_revision is not None and self.plan_revision < 1:
            raise ValueError("action plan revision must be positive")
        if self.specification is None and not self.action_name:
            raise ValueError("standalone action instances require action_name")
        if self.specification is not None and not self.action_name:
            object.__setattr__(self, "action_name", self.specification.action.value)
        goal_ids = [link.goal_id for link in self.goal_links]
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("action goal links must be unique")

    @property
    def action(self) -> ActionType:
        if self.specification is None:
            raise AttributeError("standalone action has no PlanAction action")
        return self.specification.action

    @property
    def target(self) -> str | None:
        if self.specification is not None:
            return self.specification.target
        return self.target_id

    @property
    def duration(self) -> float | None:
        return self.specification.duration if self.specification is not None else None

    @property
    def mode(self) -> TravelMode | None:
        return self.specification.mode if self.specification is not None else None

    @property
    def offer_id(self) -> str | None:
        return self.specification.offer_id if self.specification is not None else None

    @property
    def goal_ids(self) -> tuple[str, ...]:
        return tuple(link.goal_id for link in self.goal_links)


@dataclass(slots=True)
class LineageIdGenerator:
    next_plan_number: int = 1
    next_action_number: int = 1
    next_intervention_number: int = 1

    def new_plan_id(self) -> str:
        value = f"plan-{self.next_plan_number:08d}"
        self.next_plan_number += 1
        return value

    def new_action_id(self) -> str:
        value = f"action-{self.next_action_number:08d}"
        self.next_action_number += 1
        return value

    def new_intervention_id(self) -> str:
        value = f"intervention-{self.next_intervention_number:08d}"
        self.next_intervention_number += 1
        return value


type PlanQueueItem = PlanAction | ActionInstance


@dataclass(slots=True)
class PlanComponent:
    queue: list[PlanQueueItem] = field(default_factory=list)
    current: PlanQueueItem | None = None
    remaining_duration: float | None = None
    previous_activity: ActivityType | None = None
    waiting_for_affordance: bool = False
    waiting_for_transaction: bool = False
    current_started: bool = False
    plan_id: str | None = None
    plan_revision: int = 0
    origin: ActionOrigin | None = None
    root_correlation_id: str | None = None

    def clear(self) -> int:
        cleared_count = len(self.queue) + (self.current is not None)
        self.queue.clear()
        self.current = None
        self.remaining_duration = None
        self.previous_activity = None
        self.waiting_for_affordance = False
        self.waiting_for_transaction = False
        self.current_started = False
        self.plan_id = None
        self.plan_revision = 0
        self.origin = None
        self.root_correlation_id = None
        return cleared_count
