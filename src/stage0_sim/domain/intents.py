from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.components import ActionType
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.world import TravelMode


class IntentKind(StrEnum):
    ACTIVITY = "activity"
    SPEECH = "speech"
    WAIT = "wait"
    SKIP = "skip"
    NAVIGATE = "navigate"
    TRANSACT = "transact"
    SERVE_TRANSACTION = "serve_transaction"
    INTERACT = "interact"


@dataclass(frozen=True, slots=True)
class CharacterIntent:
    decision_id: str
    tool_call_id: str
    agent_id: str
    kind: IntentKind
    reason: str | None = None


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
class NavigationIntent(CharacterIntent):
    target_id: str = ""
    preferred_mode: TravelMode | None = None


@dataclass(frozen=True, slots=True)
class TransactionIntent(CharacterIntent):
    point_id: str = ""
    offer_id: str = ""


@dataclass(frozen=True, slots=True)
class ServeTransactionIntent(CharacterIntent):
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class InteractionIntent(CharacterIntent):
    specification: InteractionSpecification = InteractionSpecification(
        verb=InteractionVerb.USE,
        target_id="target",
    )
