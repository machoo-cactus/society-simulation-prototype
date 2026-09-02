from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.events import JsonValue


class DisclosureClass(StrEnum):
    SELF = "SELF"
    DIRECT_PARTICIPANTS = "DIRECT_PARTICIPANTS"
    LOCAL_VISUAL = "LOCAL_VISUAL"
    LOCAL_AUDITORY = "LOCAL_AUDITORY"
    LOCAL_OLFACTORY = "LOCAL_OLFACTORY"
    PUBLIC_WORLD = "PUBLIC_WORLD"
    ADMIN_ONLY = "ADMIN_ONLY"


class Modality(StrEnum):
    SELF = "self"
    VISUAL = "visual"
    AUDITORY = "auditory"
    OLFACTORY = "olfactory"
    ENVIRONMENTAL = "environmental"


@dataclass(frozen=True, slots=True)
class PerceptibleFact:
    fact_id: str
    tick: int
    fact_type: str
    modality: Modality
    disclosure: DisclosureClass
    subject_id: str | None = None
    object_id: str | None = None
    location_id: str | None = None
    event_id: str | None = None
    properties: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PerceivedFact:
    fact: PerceptibleFact
    observer_id: str
    perceived_tick: int
    certainty: str = "direct"
    salience: float = 0.5


@dataclass(frozen=True, slots=True)
class PerceptionPacket:
    observer_id: str
    start_tick: int
    end_tick: int
    facts: tuple[PerceivedFact, ...]
    deterministic_text: tuple[str, ...]
