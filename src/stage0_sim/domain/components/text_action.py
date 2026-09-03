from collections.abc import Callable
from dataclasses import dataclass

from stage0_sim.domain.components.physiology import ActivityType
from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.content import TextReadReceipt
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.text_actions import (
    TextReadSpecification,
    TextWriteSpecification,
)


@dataclass(slots=True)
class TextActionRequestComponent:
    read: TextReadSpecification | None = None
    write: TextWriteSpecification | None = None
    source: str = "plan"
    status: str = "requested"
    failure_reason: str | None = None
    action_instance: ActionInstance | None = None

    def __post_init__(self) -> None:
        if (self.read is None) == (self.write is None):
            raise ValueError("text action request requires exactly one specification")


@dataclass(slots=True)
class TextActionExecutionComponent:
    read: TextReadSpecification | None = None
    write: TextWriteSpecification | None = None
    elapsed: float = 0.0
    duration: float = 1.0
    operation_id: str = ""
    pinned_revision: int | None = None
    pinned_receipt: TextReadReceipt | None = None
    previous_activity: ActivityType | None = None
    action_instance: ActionInstance | None = None

    def __post_init__(self) -> None:
        if (self.read is None) == (self.write is None):
            raise ValueError("text action execution requires exactly one specification")
        if self.elapsed < 0 or self.duration <= 0 or not self.operation_id:
            raise ValueError("text action execution timing and identity are invalid")


@dataclass(slots=True)
class PendingTextReceiptsComponent:
    receipts: list[TextReadReceipt]

    def __post_init__(self) -> None:
        if not self.receipts:
            raise ValueError("pending text receipts must not be empty")


@dataclass(frozen=True, slots=True)
class TextContentPersistenceBinding:
    save_snapshot: Callable[[str, dict[str, JsonValue]], None]
