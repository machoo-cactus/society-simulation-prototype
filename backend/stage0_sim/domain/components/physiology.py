from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.events import JsonValue


class ActivityType(StrEnum):
    IDLE = "IDLE"
    WALKING = "WALKING"
    WORKING = "WORKING"
    EATING = "EATING"
    SLEEPING = "SLEEPING"
    RELAXING = "RELAXING"


@dataclass(frozen=True, slots=True)
class ActivityRates:
    satiety: float
    energy: float
    stress: float


def default_activity_rates() -> dict[ActivityType, ActivityRates]:
    return {
        ActivityType.IDLE: ActivityRates(satiety=-0.02, energy=-0.01, stress=-0.01),
        ActivityType.WALKING: ActivityRates(satiety=-0.05, energy=-0.04, stress=0.01),
        ActivityType.WORKING: ActivityRates(satiety=-0.03, energy=-0.05, stress=0.04),
        ActivityType.EATING: ActivityRates(satiety=1.0, energy=-0.005, stress=-0.01),
        ActivityType.SLEEPING: ActivityRates(satiety=-0.01, energy=0.2, stress=-0.05),
        ActivityType.RELAXING: ActivityRates(satiety=-0.015, energy=0.01, stress=-0.1),
    }


@dataclass(frozen=True, slots=True)
class HomeostasisConfiguration:
    activity_rates: Mapping[ActivityType, ActivityRates]

    def __post_init__(self) -> None:
        missing = set(ActivityType) - set(self.activity_rates)
        if missing:
            names = ", ".join(sorted(activity.value for activity in missing))
            raise ValueError(f"missing homeostasis rates for activities: {names}")


@dataclass(slots=True)
class HomeostasisComponent:
    satiety: float = 100.0
    energy: float = 100.0
    stress: float = 0.0

    def __post_init__(self) -> None:
        for name, value in self.snapshot().items():
            if not isinstance(value, int | float) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")

    def integrate(self, rates: ActivityRates, dt: float) -> None:
        if dt <= 0:
            raise ValueError("dt must be greater than zero")
        self.satiety = _clamp(self.satiety + rates.satiety * dt)
        self.energy = _clamp(self.energy + rates.energy * dt)
        self.stress = _clamp(self.stress + rates.stress * dt)

    def snapshot(self) -> dict[str, JsonValue]:
        return {
            "satiety": self.satiety,
            "energy": self.energy,
            "stress": self.stress,
        }


@dataclass(slots=True)
class ActivityComponent:
    current: ActivityType = ActivityType.IDLE
    previous: ActivityType | None = None
    movement_override: bool = False


def _clamp(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 12)
