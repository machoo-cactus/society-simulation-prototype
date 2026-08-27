from dataclasses import dataclass, field

from stage0_sim.domain.perception import PerceivedFact
from stage0_sim.domain.world import Coordinate


@dataclass(frozen=True, slots=True)
class SensesComponent:
    vision_range: int = 8
    recognition_range: int = 5
    hearing_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.vision_range < 0 or self.recognition_range < 0:
            raise ValueError("sense ranges must not be negative")
        if self.hearing_multiplier <= 0:
            raise ValueError("hearing_multiplier must be greater than zero")


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    subject_id: str
    last_seen_coordinate: Coordinate | None
    last_seen_zone_id: str | None
    last_activity: str | None
    observed_tick: int


@dataclass(slots=True)
class PerceptionComponent:
    inbox: list[PerceivedFact] = field(default_factory=list)
    visible_now: set[str] = field(default_factory=set)
    knowledge: dict[str, KnowledgeRecord] = field(default_factory=dict)
    last_processed_tick: int = 0
    last_positions: dict[str, Coordinate] = field(default_factory=dict)
    last_activities: dict[str, str] = field(default_factory=dict)
