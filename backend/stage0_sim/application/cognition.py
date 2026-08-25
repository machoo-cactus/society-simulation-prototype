from dataclasses import dataclass
from typing import Protocol

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
class PlannerContext:
    agent_id: str
    simulation_time: float
    vitals: VitalContext
    location: LocationContext
    zones: tuple[ZoneContext, ...]
    stations: tuple[StationContext, ...]
    daily_goals: tuple[str, ...]
    memories: tuple[str, ...] = ()


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


@dataclass(frozen=True, slots=True)
class DialogueResult:
    text: str


class PlannerError(RuntimeError):
    pass


class PlanValidationError(ValueError):
    pass


class DialogueError(RuntimeError):
    pass


class EmbeddingError(RuntimeError):
    pass


class Planner(Protocol):
    def plan(self, context: PlannerContext) -> PlanResult: ...


class DialogueGenerator(Protocol):
    def generate(self, context: DialogueContext) -> DialogueResult: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
