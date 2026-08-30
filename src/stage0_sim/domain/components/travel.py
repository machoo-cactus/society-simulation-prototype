from dataclasses import dataclass

from stage0_sim.domain.world import (
    TravelLeg,
    TravelMode,
    TravelStatus,
    WorldLocation,
)


@dataclass(slots=True)
class SpatialLocationComponent:
    location: WorldLocation


@dataclass(slots=True)
class TravelComponent:
    destination_id: str | None = None
    requested_mode: TravelMode | None = None
    route: tuple[TravelLeg, ...] = ()
    current_leg_index: int = 0
    leg_elapsed_seconds: float = 0.0
    status: TravelStatus = TravelStatus.IDLE
    vehicle_id: str | None = None
    correlation_id: str | None = None
    interruption_requested: bool = False
