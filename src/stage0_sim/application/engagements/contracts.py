from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

from stage0_sim.application.agents.contracts import ModelTurn
from stage0_sim.domain.events import JsonValue


class CompilationDisposition(StrEnum):
    COMPILED = "compiled"
    SPECIALIZED_TOOL_REQUIRED = "specialized_tool_required"
    UNSUPPORTED = "unsupported"


class ExpressionBand(StrEnum):
    SUBTLE = "subtle"
    MODERATE = "moderate"
    EMPHATIC = "emphatic"


class SoundBand(StrEnum):
    QUIET = "quiet"
    NORMAL = "normal"
    LOUD = "loud"


class AuditoryMode(StrEnum):
    SPEECH = "speech"
    NONVERBAL = "nonverbal"


class DurationBand(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class EffortBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StressEffectBand(StrEnum):
    CALMING = "calming"
    NEUTRAL = "neutral"
    ACTIVATING = "activating"


class ListenerEffectBand(StrEnum):
    NONE = "none"
    ALARMING = "alarming"


class ExpressiveBehaviorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: StrictStr = Field(min_length=1)
    target_id: StrictStr | None = Field(default=None, min_length=1)
    public_text: StrictStr = Field(min_length=1, max_length=2000)
    expression_band: ExpressionBand


class AuditoryExpressionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: StrictStr = Field(min_length=1)
    target_id: StrictStr | None = Field(default=None, min_length=1)
    public_text: StrictStr = Field(min_length=1, max_length=2000)
    mode: AuditoryMode
    sound_band: SoundBand
    effort_band: EffortBand
    listener_effect: ListenerEffectBand = ListenerEffectBand.NONE


class BoundedActivityArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: StrictStr = Field(min_length=1)
    target_id: StrictStr | None = Field(default=None, min_length=1)
    activity: StrictStr = Field(min_length=1, max_length=2000)
    duration_band: DurationBand
    effort_band: EffortBand
    stress_effect: StressEffectBand = StressEffectBand.NEUTRAL


class CapabilityInvocationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: StrictStr = Field(min_length=1, max_length=128)
    capability: StrictStr = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue]


class InvocationGroupProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: StrictStr = Field(min_length=1, max_length=128)
    required_atomic: StrictBool = True
    public_text: StrictStr | None = Field(default=None, min_length=1, max_length=2000)
    invocations: list[CapabilityInvocationProposal] = Field(
        min_length=1,
        max_length=16,
    )


class EngagementCompilationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: CompilationDisposition
    summary: StrictStr = Field(min_length=1, max_length=2000)
    groups: list[InvocationGroupProposal] = Field(default_factory=list, max_length=16)
    specialized_tool: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    reason: StrictStr | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def disposition_shape_is_supported(self) -> "EngagementCompilationProposal":
        if self.disposition is CompilationDisposition.COMPILED:
            if not self.groups:
                raise ValueError("compiled proposals require at least one group")
            if self.specialized_tool is not None:
                raise ValueError("compiled proposals cannot select a specialized tool")
        elif self.disposition is CompilationDisposition.SPECIALIZED_TOOL_REQUIRED:
            if self.groups:
                raise ValueError("specialized-tool proposals cannot include groups")
            if self.specialized_tool is None:
                raise ValueError("specialized-tool proposals require a tool name")
        else:
            if self.groups:
                raise ValueError("unsupported proposals cannot include groups")
            if self.specialized_tool is not None:
                raise ValueError("unsupported proposals cannot select a specialized tool")
            if self.reason is None:
                raise ValueError("unsupported proposals require a reason")
        return self


type NormalizedScalar = None | bool | int | float | str


@dataclass(frozen=True, slots=True)
class NormalizedArgument:
    name: str
    value: NormalizedScalar


@dataclass(frozen=True, slots=True)
class NormalizedCapabilityInvocation:
    invocation_id: str
    capability: str
    consequence_tier: int
    arguments: tuple[NormalizedArgument, ...]


@dataclass(frozen=True, slots=True)
class NormalizedInvocationGroup:
    group_id: str
    ordinal: int
    required_atomic: bool
    public_text: str | None
    invocations: tuple[NormalizedCapabilityInvocation, ...]


@dataclass(frozen=True, slots=True)
class GroupValidationIssue:
    code: str
    message: str
    invocation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RejectedInvocationGroup:
    group_id: str
    ordinal: int
    issues: tuple[GroupValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class EngagementCompilationResult:
    disposition: CompilationDisposition
    summary: str
    scene_hash: str
    model_turn: ModelTurn = field(compare=False, hash=False, repr=False)
    valid_groups: tuple[NormalizedInvocationGroup, ...] = ()
    rejected_groups: tuple[RejectedInvocationGroup, ...] = ()
    specialized_tool: str | None = None
    reason: str | None = None
