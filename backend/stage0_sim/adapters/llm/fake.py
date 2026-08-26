import hashlib
import math
from dataclasses import dataclass

from stage0_sim.application.cognition import (
    DialogueContext,
    DialogueResult,
    PlannerContext,
    PlanResult,
)
from stage0_sim.domain.components import ActionType, PlanAction


class FakePlanner:
    """Deterministic local stand-in for schema-constrained LLM planning."""

    def __init__(self) -> None:
        self.call_count = 0
        self.provider_name = "fake"

    def plan(self, context: PlannerContext) -> PlanResult:
        self.call_count += 1
        work_station = next(
            (
                station
                for station in sorted(context.stations, key=lambda item: item.id)
                if station.available and ActionType.WORK.value in station.actions
            ),
            None,
        )
        if work_station is not None:
            return PlanResult(
                actions=(
                    PlanAction(ActionType.MOVE_TO, target=work_station.id),
                    PlanAction(ActionType.WORK, duration=60.0),
                ),
                rationale="Move to the first available work station and complete a work block.",
                provider="fake",
            )

        office = next(
            (
                zone
                for zone in sorted(context.zones, key=lambda item: item.id)
                if zone.zone_type.upper() == "OFFICE"
            ),
            None,
        )
        if office is not None:
            return PlanResult(
                actions=(
                    PlanAction(ActionType.MOVE_TO, target=office.id),
                    PlanAction(ActionType.WORK, duration=60.0),
                ),
                rationale="Use the available office zone for the next work block.",
                provider="fake",
            )

        return PlanResult(
            actions=(PlanAction(ActionType.IDLE, duration=60.0),),
            rationale="No suitable work location is available, so remain idle.",
            provider="fake",
        )


@dataclass(slots=True)
class ScriptedPlanner:
    actions: tuple[PlanAction, ...]
    rationale: str = "Deterministic scripted plan."
    call_count: int = 0
    provider_name: str = "scripted"

    def plan(self, context: PlannerContext) -> PlanResult:
        del context
        self.call_count += 1
        return PlanResult(
            actions=self.actions,
            rationale=self.rationale,
            provider="scripted",
        )


class FakeDialogueGenerator:
    def __init__(self) -> None:
        self.call_count = 0
        self.provider_name = "fake"

    def generate(self, context: DialogueContext) -> DialogueResult:
        self.call_count += 1
        prompt = " ".join(context.prompt.split())
        return DialogueResult(
            text=f"{context.agent_id} responds at t={context.simulation_time:g}: {prompt}",
            provider="fake",
        )


class FakeEmbeddingProvider:
    def __init__(self, dimensions: int = 8) -> None:
        if dimensions <= 0:
            raise ValueError("embedding dimensions must be greater than zero")
        self.dimensions = dimensions
        self.call_count = 0
        self.provider_name = "fake"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.call_count += 1
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = tuple(
            digest[index] / 127.5 - 1.0 for index in range(self.dimensions)
        )
        magnitude = math.sqrt(sum(value * value for value in raw))
        if magnitude == 0:
            return tuple(0.0 for _ in raw)
        return tuple(round(value / magnitude, 12) for value in raw)
