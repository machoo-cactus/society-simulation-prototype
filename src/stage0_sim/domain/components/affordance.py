from dataclasses import dataclass

from stage0_sim.domain.components.physiology import ActivityType
from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.world.model import AffordanceAction


@dataclass(slots=True)
class AffordanceRequestComponent:
    station_id: str
    action: str
    source: str
    status: str = "requested"
    failure_reason: str | None = None
    action_instance: ActionInstance | None = None


@dataclass(slots=True)
class AffordanceExecutionComponent:
    station_id: str
    definition: AffordanceAction
    elapsed: float
    starting_satiety: float
    starting_energy: float
    starting_stress: float
    starting_hydration: float
    starting_social_connection: float
    starting_happiness: float
    starting_fear: float
    previous_activity: ActivityType
    correlation_id: str
    source: str = "system1"
    action_instance: ActionInstance | None = None
