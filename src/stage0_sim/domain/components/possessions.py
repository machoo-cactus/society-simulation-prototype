from dataclasses import dataclass, field

from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.economy import TransactionOffer, validated_holdings


@dataclass(slots=True)
class PossessionsComponent:
    holdings: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.holdings = validated_holdings(self.holdings)


@dataclass(slots=True)
class TransactionRequestComponent:
    point_id: str
    offer_id: str
    source: str
    request_id: str = ""
    requested_tick: int = 0
    requested_at: float = 0.0
    timeout_at: float | None = None
    operator_id: str | None = None
    authorized_by: str | None = None
    status: str = "requested"
    failure_reason: str | None = None
    action_instance: ActionInstance | None = None


@dataclass(slots=True)
class TransactionExecutionComponent:
    point_id: str
    offer: TransactionOffer
    elapsed: float
    correlation_id: str
    source: str = "plan"
    operator_id: str | None = None
    action_instance: ActionInstance | None = None
