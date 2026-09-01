from dataclasses import dataclass
from typing import Protocol

from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.domain.components import PlanAction


@dataclass(frozen=True, slots=True)
class VitalContext:
    satiety: float
    energy: float
    stress: float


@dataclass(frozen=True, slots=True)
class LocationContext:
    x: int
    y: int
    zone_id: str | None


@dataclass(frozen=True, slots=True)
class ZoneContext:
    id: str
    name: str
    zone_type: str


@dataclass(frozen=True, slots=True)
class StationContext:
    id: str
    name: str
    x: int
    y: int
    actions: tuple[str, ...]
    available: bool


@dataclass(frozen=True, slots=True)
class PlannerGoalContext:
    id: str
    description: str
    status: str
    priority: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerContext:
    agent_id: str
    simulation_time: float
    vitals: VitalContext
    location: LocationContext
    zones: tuple[ZoneContext, ...]
    stations: tuple[StationContext, ...]
    daily_goals: tuple[str, ...]
    memories: tuple[str, ...] = ()
    retrieved_information: tuple[InformationContextCapsule, ...] = ()
    structured_goals: tuple[PlannerGoalContext, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanResult:
    actions: tuple[PlanAction, ...]
    rationale: str
    provider: str = "unknown"
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class DialogueContext:
    agent_id: str
    simulation_time: float
    prompt: str
    memories: tuple[str, ...] = ()
    retrieved_information: tuple[InformationContextCapsule, ...] = ()


@dataclass(frozen=True, slots=True)
class DialogueResult:
    text: str
    provider: str = "unknown"
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class PlannerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        latency_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class PlanValidationError(ValueError):
    pass


class DialogueError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        latency_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class EmbeddingError(RuntimeError):
    pass


class Planner(Protocol):
    def plan(self, context: PlannerContext) -> PlanResult: ...


class DialogueGenerator(Protocol):
    def generate(self, context: DialogueContext) -> DialogueResult: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
