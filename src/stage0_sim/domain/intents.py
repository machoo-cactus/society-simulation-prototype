from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.components import ActionType
from stage0_sim.domain.world import TravelMode


class IntentKind(StrEnum):
    MOVE = "move"
    ACTIVITY = "activity"
    SPEECH = "speech"
    WAIT = "wait"
    SKIP = "skip"
    TRAVEL = "travel"
    NAVIGATE = "navigate"


@dataclass(frozen=True, slots=True)
class CharacterIntent:
    decision_id: str
    tool_call_id: str
    agent_id: str
    kind: IntentKind
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MoveIntent(CharacterIntent):
    target_id: str = ""


@dataclass(frozen=True, slots=True)
class ActivityIntent(CharacterIntent):
    action: ActionType = ActionType.IDLE
    target_id: str | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SpeechIntent(CharacterIntent):
    target_id: str = ""
    text: str = ""
    channel: str = "voice"


@dataclass(frozen=True, slots=True)
class WaitIntent(CharacterIntent):
    duration_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class SkipIntent(CharacterIntent):
    reconsider_after_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class TravelIntent(CharacterIntent):
    target_id: str = ""
    mode: TravelMode = TravelMode.WALK


@dataclass(frozen=True, slots=True)
class NavigationIntent(CharacterIntent):
    target_id: str = ""
    preferred_mode: TravelMode | None = None
