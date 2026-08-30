from dataclasses import dataclass

from stage0_sim.domain.world import (
    Locator,
    TravelLeg,
    TravelMode,
    TravelStatus,
    WorldLocation,
)


@dataclass(slots=True)
class SpatialLocationComponent:
    location: WorldLocation
    city_space_id: str | None = None

    @property
    def locator(self) -> Locator | None:
        if (
            self.location.scale.value == "BUILDING"
            and self.location.local_coordinate is not None
        ):
            coordinate = self.location.local_coordinate
            return Locator(
                self.location.place_id,
                {"kind": "coordinate", "x": coordinate.x, "y": coordinate.y},
            )
        if self.city_space_id is None:
            return None
        if self.location.network_node_id is not None:
            return Locator(
                self.city_space_id,
                {
                    "kind": "node",
                    "node_id": self.location.network_node_id,
                },
            )
        if (
            self.location.edge_id is not None
            and self.location.edge_progress is not None
        ):
            return Locator(
                self.city_space_id,
                {
                    "kind": "edge",
                    "edge_id": self.location.edge_id,
                    "progress": self.location.edge_progress,
                },
            )
        return None


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
    destination_entrance_id: str | None = None
    failure_reason: str | None = None
