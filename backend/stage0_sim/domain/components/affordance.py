from dataclasses import dataclass

from stage0_sim.domain.components.physiology import ActivityType
from stage0_sim.domain.world.model import AffordanceAction


@dataclass(slots=True)
class AffordanceRequestComponent:
    station_id: str
    action: str
    source: str
    status: str = "requested"
    failure_reason: str | None = None


@dataclass(slots=True)
class AffordanceExecutionComponent:
    station_id: str
    definition: AffordanceAction
    elapsed: float
    starting_satiety: float
    starting_energy: float
    starting_stress: float
    previous_activity: ActivityType
    correlation_id: str
    source: str = "system1"
