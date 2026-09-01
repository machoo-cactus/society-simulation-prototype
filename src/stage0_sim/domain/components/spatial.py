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
