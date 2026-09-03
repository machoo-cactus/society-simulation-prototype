from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.components.physiology import ActivityType


class EngagementStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EngagementGroupStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


type EngagementScalar = None | bool | int | float | str


@dataclass(frozen=True, slots=True)
class EngagementArgument:
    name: str
    value: EngagementScalar

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("engagement argument name must not be empty")


@dataclass(frozen=True, slots=True)
class EngagementCapabilityInvocation:
    invocation_id: str
    capability: str
    consequence_tier: int
    arguments: tuple[EngagementArgument, ...]

    def __post_init__(self) -> None:
        if not self.invocation_id or not self.capability:
            raise ValueError("engagement invocation identity must not be empty")
        if self.consequence_tier < 0:
            raise ValueError("engagement consequence tier must not be negative")
        names = [argument.name for argument in self.arguments]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError(
                "engagement invocation arguments must be unique and ordered"
            )


@dataclass(frozen=True, slots=True)
class EngagementInvocationGroup:
    group_id: str
    ordinal: int
    required_atomic: bool
    public_text: str | None
    invocations: tuple[EngagementCapabilityInvocation, ...]

    def __post_init__(self) -> None:
        if not self.group_id or not self.invocations:
            raise ValueError("engagement groups require identity and invocations")
        if self.ordinal < 0:
            raise ValueError("engagement group ordinal must not be negative")
        invocation_ids = [
            invocation.invocation_id for invocation in self.invocations
        ]
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ValueError("engagement invocation IDs must be unique per group")


@dataclass(frozen=True, slots=True)
class EngagementValidationIssue:
    code: str
    message: str
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("engagement validation issues require code and message")


@dataclass(frozen=True, slots=True)
class RejectedEngagementGroup:
    group_id: str
    ordinal: int
    issues: tuple[EngagementValidationIssue, ...]

    def __post_init__(self) -> None:
        if not self.group_id or not self.issues:
            raise ValueError("rejected engagement groups require issues")
        if self.ordinal < 0:
            raise ValueError("rejected engagement group ordinal must not be negative")


@dataclass(frozen=True, slots=True)
class EngagementProgram:
    engagement_id: str
    actor_id: str
    action_id: str
    plan_id: str | None
    plan_revision: int | None
    decision_id: str
    tool_call_id: str
    root_correlation_id: str
    requested_tick: int
    scene_hash: str
    groups: tuple[EngagementInvocationGroup, ...]
    rejected_groups: tuple[RejectedEngagementGroup, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.engagement_id,
            self.actor_id,
            self.action_id,
            self.decision_id,
            self.tool_call_id,
            self.root_correlation_id,
            self.scene_hash,
        )
        if any(not value for value in required):
            raise ValueError("engagement program identity must not be empty")
        if self.requested_tick < 0:
            raise ValueError("engagement requested tick must not be negative")
        if not self.groups:
            raise ValueError("engagement programs require at least one valid group")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("engagement program group IDs must be unique")


@dataclass(frozen=True, slots=True)
class PendingEngagementComponent:
    engagement_id: str
    action_id: str
    plan_id: str | None
    plan_revision: int | None
    decision_id: str
    tool_call_id: str
    root_correlation_id: str
    requested_tick: int
    expected_state_revision: int

    def __post_init__(self) -> None:
        required = (
            self.engagement_id,
            self.action_id,
            self.decision_id,
            self.tool_call_id,
            self.root_correlation_id,
        )
        if any(not value for value in required):
            raise ValueError("pending engagement identity must not be empty")
        if self.requested_tick < 0 or self.expected_state_revision < 0:
            raise ValueError("pending engagement tick/revision must not be negative")


@dataclass(frozen=True, slots=True)
class EngagementProgramComponent:
    program: EngagementProgram
    status: EngagementStatus = EngagementStatus.READY

    def __post_init__(self) -> None:
        if self.status is not EngagementStatus.READY:
            raise ValueError("installed engagement programs must be ready")


@dataclass(slots=True)
class EngagementGroupExecution:
    group_id: str
    status: EngagementGroupStatus = EngagementGroupStatus.PENDING
    failure_reason: str | None = None


@dataclass(slots=True)
class EngagementExecutionComponent:
    program: EngagementProgram
    status: EngagementStatus = EngagementStatus.PENDING
    groups: list[EngagementGroupExecution] = field(default_factory=list)
    next_group_index: int = 0
    active_group_id: str | None = None
    active_until: float | None = None
    previous_activity: ActivityType | None = None
    started_tick: int | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.groups:
            self.groups = [
                EngagementGroupExecution(group.group_id)
                for group in self.program.groups
            ]
        if [group.group_id for group in self.groups] != [
            group.group_id for group in self.program.groups
        ]:
            raise ValueError("engagement execution groups must match the program")
