from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.components.planning import ActionType


class DriveType(StrEnum):
    SATIETY = "SATIETY"
    ENERGY = "ENERGY"
    STRESS = "STRESS"


class System1State(StrEnum):
    NORMAL = "NORMAL"
    CRITICAL_DETECTED = "CRITICAL_DETECTED"
    PREEMPTING = "PREEMPTING"
    NAVIGATING_TO_CORRECTION = "NAVIGATING_TO_CORRECTION"
    EXECUTING_CORRECTION = "EXECUTING_CORRECTION"
    RECOVERED = "RECOVERED"
    BLOCKED_SURVIVAL = "BLOCKED_SURVIVAL"


@dataclass(frozen=True, slots=True)
class DriveThreshold:
    critical: float
    recovery: float
    critical_when_high: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.critical <= 100 or not 0 <= self.recovery <= 100:
            raise ValueError("drive thresholds must be between 0 and 100")
        if self.critical_when_high and self.recovery >= self.critical:
            raise ValueError("high-critical recovery must be below the critical threshold")
        if not self.critical_when_high and self.recovery <= self.critical:
            raise ValueError("low-critical recovery must be above the critical threshold")

    def is_critical(self, value: float) -> bool:
        return value >= self.critical if self.critical_when_high else value <= self.critical

    def is_recovered(self, value: float) -> bool:
        return value <= self.recovery if self.critical_when_high else value >= self.recovery

    def severity(self, value: float) -> float:
        if self.critical_when_high:
            available_range = 100.0 - self.critical
            severity = (
                (value - self.critical) / available_range if available_range else 1.0
            )
        else:
            severity = (
                (self.critical - value) / self.critical if self.critical else 1.0
            )
        return round(severity, 12)


def default_drive_thresholds() -> dict[DriveType, DriveThreshold]:
    return {
        DriveType.SATIETY: DriveThreshold(critical=15.0, recovery=30.0),
        DriveType.ENERGY: DriveThreshold(critical=15.0, recovery=30.0),
        DriveType.STRESS: DriveThreshold(
            critical=85.0,
            recovery=70.0,
            critical_when_high=True,
        ),
    }


@dataclass(frozen=True, slots=True)
class System1Configuration:
    thresholds: Mapping[DriveType, DriveThreshold]
    corrective_actions: Mapping[DriveType, ActionType] = field(
        default_factory=lambda: {
            DriveType.SATIETY: ActionType.EAT,
            DriveType.ENERGY: ActionType.SLEEP,
            DriveType.STRESS: ActionType.RELAX,
        }
    )
    tie_break_order: tuple[DriveType, ...] = (
        DriveType.SATIETY,
        DriveType.ENERGY,
        DriveType.STRESS,
    )

    def __post_init__(self) -> None:
        drives = set(DriveType)
        if set(self.thresholds) != drives:
            raise ValueError("System 1 thresholds must cover every drive")
        if set(self.corrective_actions) != drives:
            raise ValueError("System 1 corrective actions must cover every drive")
        if set(self.tie_break_order) != drives or len(self.tie_break_order) != len(drives):
            raise ValueError("System 1 tie-break order must contain every drive once")


@dataclass(slots=True)
class DriveComponent:
    state: System1State = System1State.NORMAL
    active_drive: DriveType | None = None
    target_station_id: str | None = None
    critical_drives: frozenset[DriveType] = frozenset()
