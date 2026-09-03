from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.events import JsonValue

HOMEOSTASIS_FIELDS = (
    "satiety",
    "energy",
    "stress",
    "hydration",
    "social_connection",
    "happiness",
    "fear",
)


class ActivityType(StrEnum):
    IDLE = "IDLE"
    WALKING = "WALKING"
    WORKING = "WORKING"
    READING = "READING"
    WRITING = "WRITING"
    ENGAGING = "ENGAGING"
    EATING = "EATING"
    DRINKING = "DRINKING"
    SLEEPING = "SLEEPING"
    RELAXING = "RELAXING"


@dataclass(frozen=True, slots=True)
class ActivityRates:
    satiety: float
    energy: float
    stress: float
    hydration: float = 0.0
    social_connection: float = 0.0
    happiness: float = 0.0
    fear: float = 0.0


def default_activity_rates() -> dict[ActivityType, ActivityRates]:
    return {
        ActivityType.IDLE: ActivityRates(satiety=-0.02, energy=-0.01, stress=-0.01),
        ActivityType.WALKING: ActivityRates(satiety=-0.05, energy=-0.04, stress=0.01),
        ActivityType.WORKING: ActivityRates(satiety=-0.03, energy=-0.05, stress=0.04),
        ActivityType.READING: ActivityRates(
            satiety=-0.02,
            energy=-0.02,
            stress=-0.01,
        ),
        ActivityType.WRITING: ActivityRates(
            satiety=-0.025,
            energy=-0.03,
            stress=0.01,
        ),
        ActivityType.ENGAGING: ActivityRates(
            satiety=-0.02,
            energy=-0.01,
            stress=0.0,
        ),
        ActivityType.EATING: ActivityRates(satiety=1.0, energy=-0.005, stress=-0.01),
        ActivityType.DRINKING: ActivityRates(
            satiety=0.1,
            energy=-0.002,
            stress=-0.01,
        ),
        ActivityType.SLEEPING: ActivityRates(satiety=-0.01, energy=0.2, stress=-0.05),
        ActivityType.RELAXING: ActivityRates(satiety=-0.015, energy=0.01, stress=-0.1),
    }


@dataclass(frozen=True, slots=True)
class HomeostasisConfiguration:
    activity_rates: Mapping[ActivityType, ActivityRates]
    drink_hydration_delta: float = 0.0
    read_happiness_delta: float = 0.0
    social_connection_delta: float = 0.0
    social_happiness_delta: float = 0.0
    alarming_fear_delta: float = 0.0
    calming_happiness_delta: float = 0.0
    calming_fear_delta: float = 0.0

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
    hydration: float = 100.0
    social_connection: float = 50.0
    happiness: float = 50.0
    fear: float = 0.0

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
        self.hydration = _clamp(self.hydration + rates.hydration * dt)
        self.social_connection = _clamp(
            self.social_connection + rates.social_connection * dt
        )
        self.happiness = _clamp(self.happiness + rates.happiness * dt)
        self.fear = _clamp(self.fear + rates.fear * dt)

    def apply_deltas(self, **deltas: float) -> dict[str, float]:
        unknown = set(deltas) - set(HOMEOSTASIS_FIELDS)
        if unknown:
            raise ValueError(f"unknown homeostasis fields: {sorted(unknown)}")
        actual: dict[str, float] = {}
        for name in HOMEOSTASIS_FIELDS:
            if name not in deltas:
                continue
            before = float(getattr(self, name))
            after = _clamp(before + deltas[name])
            setattr(self, name, after)
            actual[name] = round(after - before, 12)
        return actual

    def snapshot(self) -> dict[str, JsonValue]:
        return {
            "satiety": self.satiety,
            "energy": self.energy,
            "stress": self.stress,
            "hydration": self.hydration,
            "social_connection": self.social_connection,
            "happiness": self.happiness,
            "fear": self.fear,
        }


@dataclass(slots=True)
class ActivityComponent:
    current: ActivityType = ActivityType.IDLE
    previous: ActivityType | None = None
    movement_override: bool = False


def _clamp(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 12)
