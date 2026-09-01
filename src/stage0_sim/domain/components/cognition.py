from dataclasses import dataclass, field

from stage0_sim.domain.events import JsonValue


@dataclass(slots=True)
class ConversationComponent:
    turns: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CharacterProfileComponent:
    profile_id: str
    template_id: str
    template_version: int
    content_hash: str
    display_name: str
    description: str
    ui_data: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.profile_id or not self.template_id or not self.display_name:
            raise ValueError("character profile identity must not be empty")
        if self.template_version <= 0:
            raise ValueError("character template_version must be greater than zero")


@dataclass(frozen=True, slots=True)
class CharacterSituationComponent:
    slot_id: str
    label: str
    briefing: str = ""
    description: str = ""
    content_hash: str = ""
    input_hash: str = ""
    data: dict[str, JsonValue] = field(default_factory=dict)
    generation: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.slot_id or not self.label:
            raise ValueError("character situation identity must not be empty")


@dataclass(slots=True)
class ControllerComponent:
    enabled: bool = False
    tool_allowlist: tuple[str, ...] = (
        "navigate_to",
        "perform",
        "say",
        "wait",
        "skip",
        "transact",
        "check_environment",
    )
    state_revision: int = 0
    decision_sequence: int = 0
    request_pending: bool = False
    current_decision_id: str | None = None
    last_outcome: str | None = None
    next_decision_time: float = 0.0
