from dataclasses import dataclass
from datetime import datetime, timedelta

from stage0_sim.domain.events import JsonValue


@dataclass(frozen=True, slots=True)
class SimulationCalendar:
    start_datetime: datetime
    update_interval_seconds: float = 1800.0

    def __post_init__(self) -> None:
        if self.start_datetime.utcoffset() is None:
            raise ValueError("start_datetime must include a UTC offset")
        if self.update_interval_seconds <= 0:
            raise ValueError("update_interval_seconds must be greater than zero")

    def payload_at(self, simulation_time: float) -> dict[str, JsonValue]:
        current = self.datetime_at(simulation_time)
        hour = current.hour
        period = (
            "night"
            if hour < 6
            else "morning"
            if hour < 12
            else "afternoon"
            if hour < 18
            else "evening"
        )
        return {
            "datetime": current.isoformat(timespec="seconds"),
            "date": current.date().isoformat(),
            "time": current.timetz().isoformat(timespec="seconds"),
            "weekday": current.strftime("%A"),
            "period": period,
        }

    def datetime_at(self, simulation_time: float) -> datetime:
        return self.start_datetime + timedelta(seconds=simulation_time)
