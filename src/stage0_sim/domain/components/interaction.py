from dataclasses import dataclass

from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.interactions import InteractionSpecification


@dataclass(slots=True)
class InteractionRequestComponent:
    specification: InteractionSpecification
    source: str
    status: str = "requested"
    failure_reason: str | None = None
    action_instance: ActionInstance | None = None


@dataclass(slots=True)
class InteractionExecutionComponent:
    specification: InteractionSpecification
    source: str
    elapsed: float = 0.0
    duration: float = 1.0
    correlation_id: str | None = None
    action_instance: ActionInstance | None = None

    def __post_init__(self) -> None:
        if self.elapsed < 0 or self.duration <= 0:
            raise ValueError("interaction timing must be positive")
