from dataclasses import dataclass

from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.systems import SystemContext


@dataclass(slots=True)
class CalendarUpdateSystem:
    name: str = "calendar_update"
    order: int = 240
    _last_boundary: int = 0

    def update(self, context: SystemContext) -> None:
        calendar = context.registry.get_resource(SimulationCalendar)
        current_boundary = int(
            context.clock.simulation_time
            // calendar.update_interval_seconds
        )
        for boundary in range(self._last_boundary + 1, current_boundary + 1):
            boundary_time = boundary * calendar.update_interval_seconds
            context.events.emit(
                "time.updated",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                payload={
                    **calendar.payload_at(boundary_time),
                    "boundary_simulation_time": boundary_time,
                },
            )
        self._last_boundary = current_boundary
