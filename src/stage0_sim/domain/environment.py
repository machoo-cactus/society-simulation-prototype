import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from stage0_sim.domain.events import JsonValue


class WeatherCondition(StrEnum):
    CLEAR = "CLEAR"
    CLOUDY = "CLOUDY"
    RAIN = "RAIN"
    HEAVY_RAIN = "HEAVY_RAIN"
    FOG = "FOG"
    STORM = "STORM"


@dataclass(frozen=True, slots=True)
class WeatherState:
    condition: WeatherCondition
    temperature_c: float
    precipitation_mm_per_hour: float = 0.0
    wind_speed_mps: float = 0.0
    wind_direction_degrees: float = 0.0
    visibility_meters: float = 10_000.0

    def __post_init__(self) -> None:
        values = (
            self.temperature_c,
            self.precipitation_mm_per_hour,
            self.wind_speed_mps,
            self.wind_direction_degrees,
            self.visibility_meters,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("weather values must be finite")
        if self.precipitation_mm_per_hour < 0:
            raise ValueError("precipitation must not be negative")
        if self.wind_speed_mps < 0:
            raise ValueError("wind speed must not be negative")
        if not 0 <= self.wind_direction_degrees < 360:
            raise ValueError("wind direction must be between 0 and 360 degrees")
        if self.visibility_meters <= 0:
            raise ValueError("visibility must be greater than zero")
        if (
            self.condition
            in {
                WeatherCondition.CLEAR,
                WeatherCondition.CLOUDY,
                WeatherCondition.FOG,
            }
            and self.precipitation_mm_per_hour > 0
        ):
            raise ValueError(f"{self.condition.value} weather cannot have precipitation")

    @property
    def raining(self) -> bool:
        return self.precipitation_mm_per_hour > 0

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "condition": self.condition.value,
            "temperature_c": self.temperature_c,
            "precipitation_mm_per_hour": self.precipitation_mm_per_hour,
            "wind_speed_mps": self.wind_speed_mps,
            "wind_direction_degrees": self.wind_direction_degrees,
            "visibility_meters": self.visibility_meters,
        }


@dataclass(frozen=True, slots=True)
class WeatherTransition:
    at_seconds: float
    state: WeatherState

    def __post_init__(self) -> None:
        if not math.isfinite(self.at_seconds) or self.at_seconds <= 0:
            raise ValueError("weather transition time must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class WeatherTimeline:
    initial: WeatherState
    transitions: tuple[WeatherTransition, ...] = ()

    def __post_init__(self) -> None:
        times = [transition.at_seconds for transition in self.transitions]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("weather transition times must be unique and increasing")

    def state_at(self, simulation_time: float) -> WeatherState:
        state = self.initial
        for transition in self.transitions:
            if transition.at_seconds > simulation_time:
                break
            state = transition.state
        return state


@dataclass(frozen=True, slots=True)
class WeatherEffects:
    walking_speed_multiplier: float = 1.0
    cycling_speed_multiplier: float = 1.0
    visibility_multiplier: float = 1.0
    wetness_gain_per_mm_hour_second: float = 0.00002
    base_drying_per_second: float = 0.00005
    wind_drying_per_mps_second: float = 0.000005
    temperature_drying_per_degree_second: float = 0.000001

    def __post_init__(self) -> None:
        multipliers = (
            self.walking_speed_multiplier,
            self.cycling_speed_multiplier,
            self.visibility_multiplier,
        )
        rates = (
            self.wetness_gain_per_mm_hour_second,
            self.base_drying_per_second,
            self.wind_drying_per_mps_second,
            self.temperature_drying_per_degree_second,
        )
        if not all(math.isfinite(value) and value > 0 for value in multipliers):
            raise ValueError("weather multipliers must be finite and greater than zero")
        if not all(math.isfinite(value) and value >= 0 for value in rates):
            raise ValueError("weather wetness and drying rates must be finite and non-negative")


@dataclass(slots=True)
class WeatherRuntime:
    timeline: WeatherTimeline
    effects_by_condition: dict[WeatherCondition, WeatherEffects]
    current: WeatherState = field(init=False)
    transition_index: int = 0

    def __post_init__(self) -> None:
        self.current = self.timeline.initial

    @property
    def effects(self) -> WeatherEffects:
        return self.effects_by_condition.get(self.current.condition, WeatherEffects())


class WetnessBand(StrEnum):
    DRY = "DRY"
    DAMP = "DAMP"
    WET = "WET"
    SOAKED = "SOAKED"


def wetness_band(value: float) -> WetnessBand:
    if value < 0.1:
        return WetnessBand.DRY
    if value < 0.35:
        return WetnessBand.DAMP
    if value < 0.7:
        return WetnessBand.WET
    return WetnessBand.SOAKED


@dataclass(slots=True)
class SurfaceConditionRegistry:
    wetness: dict[str, float] = field(default_factory=dict)

    def value(self, surface_id: str) -> float:
        return self.wetness.get(surface_id, 0.0)

    def set_value(self, surface_id: str, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError("surface wetness must be finite")
        self.wetness[surface_id] = min(1.0, max(0.0, value))

    def payload(self, surface_id: str) -> dict[str, JsonValue]:
        value = self.value(surface_id)
        return {"surface_id": surface_id, "wetness": value, "band": wetness_band(value).value}


class AvailabilityReason(StrEnum):
    OPEN = "open"
    BASE_UNAVAILABLE = "base_unavailable"
    CLOSED_BY_SCHEDULE = "closed_by_schedule"
    CLOSED_BY_WEATHER = "closed_by_weather"


@dataclass(frozen=True, slots=True)
class AvailabilityState:
    available: bool
    reason: AvailabilityReason
    next_transition_datetime: str | None = None

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "available": self.available,
            "reason": self.reason.value,
            "next_transition_datetime": self.next_transition_datetime,
        }


@dataclass(slots=True)
class EnvironmentAvailabilityRegistry:
    states: dict[str, AvailabilityState] = field(default_factory=dict)

    def state(self, resource_id: str, *, base_available: bool = True) -> AvailabilityState:
        if resource_id in self.states:
            return self.states[resource_id]
        return AvailabilityState(
            available=base_available,
            reason=(
                AvailabilityReason.OPEN
                if base_available
                else AvailabilityReason.BASE_UNAVAILABLE
            ),
        )


@dataclass(frozen=True, slots=True)
class WeeklyOpeningWindow:
    weekdays: frozenset[int]
    opens_minute: int
    closes_minute: int

    def __post_init__(self) -> None:
        if not self.weekdays or any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("opening-window weekdays must be between 0 and 6")
        if not 0 <= self.opens_minute < 1440 or not 0 <= self.closes_minute < 1440:
            raise ValueError("opening-window times must be minutes within a day")
        if self.opens_minute == self.closes_minute:
            raise ValueError("opening and closing times must differ")

    def contains(self, current: datetime) -> bool:
        minute = current.hour * 60 + current.minute
        if self.opens_minute < self.closes_minute:
            return (
                current.weekday() in self.weekdays
                and self.opens_minute <= minute < self.closes_minute
            )
        if current.weekday() in self.weekdays and minute >= self.opens_minute:
            return True
        previous_day = (current.weekday() - 1) % 7
        return previous_day in self.weekdays and minute < self.closes_minute


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    windows: tuple[WeeklyOpeningWindow, ...]

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("weekly schedule must contain at least one window")

    def is_open(self, current: datetime) -> bool:
        return any(window.contains(current) for window in self.windows)

    def next_transition(self, current: datetime) -> datetime:
        candidates: list[datetime] = []
        start = current.replace(second=0, microsecond=0)
        for day_offset in range(8):
            day = start + timedelta(days=day_offset)
            for window in self.windows:
                if day.weekday() not in window.weekdays:
                    continue
                opening = day.replace(
                    hour=window.opens_minute // 60,
                    minute=window.opens_minute % 60,
                )
                closing_day = day + (
                    timedelta(days=1)
                    if window.opens_minute > window.closes_minute
                    else timedelta()
                )
                closing = closing_day.replace(
                    hour=window.closes_minute // 60,
                    minute=window.closes_minute % 60,
                )
                candidates.extend(
                    candidate
                    for candidate in (opening, closing)
                    if candidate > current
                )
        if not candidates:
            raise RuntimeError("weekly schedule has no future transition")
        return min(candidates)


@dataclass(frozen=True, slots=True)
class AvailabilityRule:
    resource_id: str
    resource_kind: str
    base_available: bool = True
    schedule: WeeklySchedule | None = None
    closed_weather: frozenset[WeatherCondition] = frozenset()


@dataclass(frozen=True, slots=True)
class EnvironmentAvailabilityRules:
    rules: tuple[AvailabilityRule, ...] = ()
