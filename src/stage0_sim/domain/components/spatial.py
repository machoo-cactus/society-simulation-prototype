from dataclasses import dataclass

from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.world import Coordinate


@dataclass(slots=True)
class PositionComponent:
    coordinate: Coordinate


@dataclass(slots=True)
class MovementComponent:
    destination: Coordinate | None = None
    path: tuple[Coordinate, ...] = ()
    retry_after_tick: int = 0
    path_correlation_id: str | None = None
    action_instance: ActionInstance | None = None
    speed_legacy_cells_per_second: float = 1.0
    distance_remainder: float = 0.0
    planned_spatial_revision: int | None = None

    def __post_init__(self) -> None:
        if self.speed_legacy_cells_per_second <= 0:
            raise ValueError("movement speed must be greater than zero")
        if not 0 <= self.distance_remainder < 1:
            raise ValueError("movement distance remainder must be in [0, 1)")
