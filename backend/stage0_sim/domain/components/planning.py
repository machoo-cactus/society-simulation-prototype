from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.components.physiology import ActivityType


class ActionType(StrEnum):
    MOVE_TO = "MOVE_TO"
    WORK = "WORK"
    SOCIALIZE = "SOCIALIZE"
    READ = "READ"
    EAT = "EAT"
    SLEEP = "SLEEP"
    RELAX = "RELAX"
    IDLE = "IDLE"


@dataclass(frozen=True, slots=True)
class PlanAction:
    action: ActionType
    target: str | None = None
    duration: float | None = None

    def __post_init__(self) -> None:
        if self.duration is not None and self.duration <= 0:
            raise ValueError("action duration must be greater than zero")


@dataclass(slots=True)
class PlanComponent:
    queue: list[PlanAction] = field(default_factory=list)
    current: PlanAction | None = None
    remaining_duration: float | None = None
    previous_activity: ActivityType | None = None
    waiting_for_affordance: bool = False
    current_started: bool = False

    def clear(self) -> int:
        cleared_count = len(self.queue) + (self.current is not None)
        self.queue.clear()
        self.current = None
        self.remaining_duration = None
        self.previous_activity = None
        self.waiting_for_affordance = False
        self.current_started = False
        return cleared_count
