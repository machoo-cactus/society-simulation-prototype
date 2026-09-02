from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum

from stage0_sim.domain.components.planning import ActionType
from stage0_sim.domain.events import JsonValue


class GoalStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    RETIRED = "retired"
    UNKNOWN = "unknown"


class GoalCompletionPolicy(StrEnum):
    ALL = "all"
    ANY = "any"


class GoalCriterionEffect(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


class GoalComparator(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class GoalStateComponent(StrEnum):
    HOMEOSTASIS = "homeostasis"
    ACTIVITY = "activity"
    CONTROLLER = "controller"


class GoalLocationKind(StrEnum):
    ANY = "any"
    ZONE = "zone"
    PLACE = "place"


class ActionOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class InteractionType(StrEnum):
    SPEECH = "speech"
    TRANSACTION = "transaction"
    PHYSICAL = "physical"


@dataclass(frozen=True, slots=True)
class EventMatchCriterion:
    event_type: str
    payload_subset: dict[str, JsonValue] = dc_field(default_factory=dict)
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="event_match", init=False)


@dataclass(frozen=True, slots=True)
class StateComparisonCriterion:
    component: GoalStateComponent
    field: str
    comparator: GoalComparator
    value: bool | int | float | str
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="state_comparison", init=False)


@dataclass(frozen=True, slots=True)
class LocationMatchCriterion:
    location_id: str
    location_kind: GoalLocationKind = GoalLocationKind.ANY
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="location_match", init=False)


@dataclass(frozen=True, slots=True)
class PossessionThresholdCriterion:
    item_id: str
    comparator: GoalComparator
    quantity: int
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="possession_threshold", init=False)


@dataclass(frozen=True, slots=True)
class ActionOutcomeCriterion:
    action: ActionType
    outcome: ActionOutcome
    target: str | None = None
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="action_outcome", init=False)


@dataclass(frozen=True, slots=True)
class InteractionCountCriterion:
    interaction_type: InteractionType
    minimum_count: int
    target_id: str | None = None
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="interaction_count", init=False)


@dataclass(frozen=True, slots=True)
class SimulationTimeCriterion:
    comparator: GoalComparator
    simulation_time: float
    effect: GoalCriterionEffect = GoalCriterionEffect.SUCCESS
    criterion_type: str = dc_field(default="simulation_time", init=False)


GoalCriterion = (
    EventMatchCriterion
    | StateComparisonCriterion
    | LocationMatchCriterion
    | PossessionThresholdCriterion
    | ActionOutcomeCriterion
    | InteractionCountCriterion
    | SimulationTimeCriterion
)


@dataclass(frozen=True, slots=True)
class GoalDefinition:
    id: str
    description: str
    priority: int = 0
    tags: tuple[str, ...] = ()
    activation_time: float | None = None
    deadline_time: float | None = None
    completion_policy: GoalCompletionPolicy = GoalCompletionPolicy.ALL
    criteria: tuple[GoalCriterion, ...] = ()
    def __post_init__(self) -> None:
        if not self.id or not self.description:
            raise ValueError("goal identity and description must not be empty")
        if self.activation_time is not None and self.activation_time < 0:
            raise ValueError("goal activation_time must not be negative")
        if self.deadline_time is not None and self.deadline_time < 0:
            raise ValueError("goal deadline_time must not be negative")
        if (
            self.activation_time is not None
            and self.deadline_time is not None
            and self.deadline_time < self.activation_time
        ):
            raise ValueError("goal deadline_time must not precede activation_time")


@dataclass(frozen=True, slots=True)
class GoalEvidence:
    criterion_id: str
    criterion_type: str
    simulation_tick: int
    simulation_time: float
    context: dict[str, JsonValue]


@dataclass(slots=True)
class GoalRuntime:
    definition: GoalDefinition
    status: GoalStatus = GoalStatus.PENDING
    progress: float = 0.0
    evidence: list[GoalEvidence] = dc_field(default_factory=list)
    criterion_progress: dict[str, float] = dc_field(default_factory=dict)
    interaction_counts: dict[str, int] = dc_field(default_factory=dict)
    matched_criteria: set[str] = dc_field(default_factory=set)


@dataclass(slots=True)
class GoalComponent:
    goals: list[GoalRuntime] = dc_field(default_factory=list)

    def get(self, goal_id: str) -> GoalRuntime:
        for goal in self.goals:
            if goal.definition.id == goal_id:
                return goal
        raise KeyError(f"unknown goal: {goal_id}")
