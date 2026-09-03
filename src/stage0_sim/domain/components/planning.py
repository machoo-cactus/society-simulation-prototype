from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.components.physiology import ActivityType
from stage0_sim.domain.engagements import EngagementSpecification
from stage0_sim.domain.interactions import InteractionSpecification
from stage0_sim.domain.text_actions import (
    TextReadSpecification,
    TextWriteSpecification,
)
from stage0_sim.domain.world import TravelMode


class ActionType(StrEnum):
    WORK = "WORK"
    SOCIALIZE = "SOCIALIZE"
    READ = "READ"
    READ_TEXT = "READ_TEXT"
    WRITE_TEXT = "WRITE_TEXT"
    EAT = "EAT"
    SLEEP = "SLEEP"
    RELAX = "RELAX"
    IDLE = "IDLE"
    NAVIGATE = "NAVIGATE"
    TRANSACT = "TRANSACT"
    SERVE_TRANSACTION = "SERVE_TRANSACTION"
    INTERACT = "INTERACT"
    DRINK = "DRINK"
    ENGAGE = "ENGAGE"


class ActionOrigin(StrEnum):
    SCENARIO = "scenario"
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
    interaction: InteractionSpecification | None = None
    engagement: EngagementSpecification | None = None
    text_read: TextReadSpecification | None = None
    text_write: TextWriteSpecification | None = None

    def __post_init__(self) -> None:
        if self.duration is not None and self.duration <= 0:
            raise ValueError("action duration must be greater than zero")
        text_specifications = (
            self.text_read is not None,
            self.text_write is not None,
        )
        if self.action is ActionType.READ_TEXT:
            if self.text_read is None or self.text_write is not None:
                raise ValueError("READ_TEXT requires only a read specification")
            if self.target is None:
                object.__setattr__(self, "target", self.text_read.target_id)
            elif self.target != self.text_read.target_id:
                raise ValueError("READ_TEXT target must match its specification")
            if any(
                value is not None
                for value in (
                    self.mode,
                    self.offer_id,
                    self.interaction,
                    self.engagement,
                )
            ):
                raise ValueError("READ_TEXT does not accept unrelated specifications")
        elif self.action is ActionType.WRITE_TEXT:
            if self.text_write is None or self.text_read is not None:
                raise ValueError("WRITE_TEXT requires only a write specification")
            if self.target is None:
                object.__setattr__(self, "target", self.text_write.target_id)
            elif self.target != self.text_write.target_id:
                raise ValueError("WRITE_TEXT target must match its specification")
            if any(
                value is not None
                for value in (
                    self.mode,
                    self.offer_id,
                    self.interaction,
                    self.engagement,
                )
            ):
                raise ValueError("WRITE_TEXT does not accept unrelated specifications")
        elif any(text_specifications):
            raise ValueError("text specifications require a text action")
        elif self.action is ActionType.ENGAGE:
            if self.engagement is None:
                raise ValueError("ENGAGE requires an engagement specification")
            if self.target is None and self.engagement.reference_ids:
                object.__setattr__(self, "target", self.engagement.reference_ids[0])
            if self.mode is not None or self.offer_id is not None:
                raise ValueError("ENGAGE does not accept mode or offer_id")
            if self.interaction is not None:
                raise ValueError("ENGAGE does not accept an interaction specification")
        elif self.engagement is not None:
            raise ValueError("engagement is only valid for ENGAGE")
        elif self.action is ActionType.INTERACT:
            if self.interaction is None:
                raise ValueError("INTERACT requires an interaction specification")
            if self.target is None:
                object.__setattr__(self, "target", self.interaction.target_id)
            elif self.target != self.interaction.target_id:
                raise ValueError(
                    "INTERACT target must match its interaction specification"
                )
            if self.mode is not None or self.offer_id is not None:
                raise ValueError(
                    "INTERACT does not accept mode or offer_id"
                )
        elif self.interaction is not None:
            raise ValueError("interaction is only valid for INTERACT")
        elif self.action is ActionType.TRANSACT:
            if self.target is None or self.offer_id is None:
                raise ValueError("TRANSACT requires target and offer_id")
            if self.mode is not None:
                raise ValueError("mode is only valid for NAVIGATE")
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
        elif self.mode is not None and self.action is not ActionType.NAVIGATE:
            raise ValueError("mode is only valid for NAVIGATE")


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
    def interaction(self) -> InteractionSpecification | None:
        return (
            self.specification.interaction
            if self.specification is not None
            else None
        )

    @property
    def engagement(self) -> EngagementSpecification | None:
        return (
            self.specification.engagement
            if self.specification is not None
            else None
        )

    @property
    def text_read(self) -> TextReadSpecification | None:
        return (
            self.specification.text_read
            if self.specification is not None
            else None
        )

    @property
    def text_write(self) -> TextWriteSpecification | None:
        return (
            self.specification.text_write
            if self.specification is not None
            else None
        )

    @property
    def goal_ids(self) -> tuple[str, ...]:
        return tuple(link.goal_id for link in self.goal_links)


@dataclass(slots=True)
class LineageIdGenerator:
    next_plan_number: int = 1
    next_action_number: int = 1
    next_intervention_number: int = 1
    next_engagement_number: int = 1

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

    def new_engagement_id(self) -> str:
        value = f"engagement-{self.next_engagement_number:08d}"
        self.next_engagement_number += 1
        return value


@dataclass(slots=True)
class PlanComponent:
    queue: list[ActionInstance] = field(default_factory=list)
    current: ActionInstance | None = None
    remaining_duration: float | None = None
    previous_activity: ActivityType | None = None
    waiting_for_affordance: bool = False
    waiting_for_transaction: bool = False
    waiting_for_interaction: bool = False
    waiting_for_engagement: bool = False
    waiting_for_text: bool = False
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
        self.waiting_for_interaction = False
        self.waiting_for_engagement = False
        self.waiting_for_text = False
        self.current_started = False
        self.plan_id = None
        self.plan_revision = 0
        self.origin = None
        self.root_correlation_id = None
        return cleared_count
