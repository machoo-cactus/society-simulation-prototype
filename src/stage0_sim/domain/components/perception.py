from dataclasses import dataclass, field

from stage0_sim.domain.perception import PerceivedFact
from stage0_sim.domain.world import Coordinate


@dataclass(frozen=True, slots=True)
class SensesComponent:
    vision_range: int = 8
    recognition_range: int = 5
    hearing_range: int = 10
    smell_range: int = 0

    def __post_init__(self) -> None:
        if min(
            self.vision_range,
            self.recognition_range,
            self.hearing_range,
            self.smell_range,
        ) < 0:
            raise ValueError("sense ranges must not be negative")
        if self.recognition_range > self.vision_range:
            raise ValueError("recognition range must not exceed vision range")


@dataclass(frozen=True, slots=True)
class EffectiveSensesComponent:
    vision_range: int = 8
    recognition_range: int = 5
    hearing_range: int = 10
    smell_range: int = 0

    def __post_init__(self) -> None:
        SensesComponent(
            vision_range=self.vision_range,
            recognition_range=self.recognition_range,
            hearing_range=self.hearing_range,
            smell_range=self.smell_range,
        )


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
    visible_objects_now: set[str] = field(default_factory=set)
    object_knowledge: dict[str, int] = field(default_factory=dict)
    last_object_states: dict[str, str] = field(default_factory=dict)
    recognized_objects_now: set[str] = field(default_factory=set)
    smelled_objects_now: set[str] = field(default_factory=set)
    last_scent_states: dict[str, str] = field(default_factory=dict)
