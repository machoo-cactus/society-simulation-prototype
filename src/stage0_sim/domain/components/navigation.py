from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.components.planning import ActionInstance
from stage0_sim.domain.interactions import InteractionSpecification
from stage0_sim.domain.world import Locator, Route, TravelMode


class NavigationStatus(StrEnum):
    IDLE = "IDLE"
    REQUESTED = "REQUESTED"
    PLANNED = "PLANNED"
    NAVIGATING = "NAVIGATING"
    ARRIVED = "ARRIVED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class NavigationPrimitiveKind(StrEnum):
    MOVE = "MOVE"
    INTERACT = "INTERACT"
    TRANSITION = "TRANSITION"
    TRAVEL = "TRAVEL"


@dataclass(frozen=True, slots=True)
class NavigationPrimitive:
    kind: NavigationPrimitiveKind
    origin: Locator
    destination: Locator
    route_leg_start: int
    route_leg_end: int
    transition_id: str | None = None
    destination_id: str | None = None
    mode: TravelMode | None = None
    entrance_transition_id: str | None = None
    outbound_transition_id: str | None = None
    origin_network_node_id: str | None = None
    route_edge_ids: tuple[str, ...] = ()
    interaction: InteractionSpecification | None = None

    def __post_init__(self) -> None:
        if self.route_leg_start < 0:
            raise ValueError("navigation route_leg_start must not be negative")
        if self.route_leg_end <= self.route_leg_start:
            raise ValueError(
                "navigation route_leg_end must exceed route_leg_start"
            )
        if self.kind is NavigationPrimitiveKind.INTERACT:
            if self.interaction is None:
                raise ValueError(
                    "interaction navigation primitives require interaction"
                )
            if (
                self.transition_id is not None
                or self.destination_id is not None
                or self.mode is not None
                or self.entrance_transition_id is not None
                or self.outbound_transition_id is not None
                or self.origin_network_node_id is not None
                or self.route_edge_ids
            ):
                raise ValueError(
                    "interaction navigation primitives cannot contain travel fields"
                )
        elif self.interaction is not None:
            raise ValueError(
                "interaction is only valid for interaction primitives"
            )
        elif self.kind is NavigationPrimitiveKind.TRAVEL:
            if self.destination_id is None or self.mode is None:
                raise ValueError(
                    "travel navigation primitives require destination and mode"
                )
            if any(not edge_id for edge_id in self.route_edge_ids):
                raise ValueError(
                    "travel navigation route edge IDs must not be empty"
                )
            if len(self.route_edge_ids) != len(set(self.route_edge_ids)):
                raise ValueError(
                    "travel navigation route edge IDs must be unique"
                )
            if self.transition_id is not None:
                raise ValueError(
                    "travel navigation primitives cannot contain transition_id"
                )
        elif self.kind is NavigationPrimitiveKind.TRANSITION:
            if self.transition_id is None:
                raise ValueError(
                    "transition navigation primitives require transition_id"
                )
            if (
                self.destination_id is not None
                or self.mode is not None
                or self.entrance_transition_id is not None
                or self.outbound_transition_id is not None
                or self.origin_network_node_id is not None
                or self.route_edge_ids
            ):
                raise ValueError(
                    "transition navigation primitives cannot contain travel fields"
                )
        elif (
            self.transition_id is not None
            or self.destination_id is not None
            or self.mode is not None
            or self.entrance_transition_id is not None
            or self.outbound_transition_id is not None
            or self.origin_network_node_id is not None
            or self.route_edge_ids
        ):
            raise ValueError(
                "move navigation primitives cannot contain travel fields"
            )


@dataclass(slots=True)
class NavigationComponent:
    target_id: str | None = None
    preferred_mode: TravelMode | None = None
    reason: str | None = None
    route: Route | None = None
    primitives: tuple[NavigationPrimitive, ...] = ()
    current_primitive_index: int = 0
    completed_route_legs: int = 0
    status: NavigationStatus = NavigationStatus.IDLE
    correlation_id: str | None = None
    failure_reason: str | None = None
    action_instance: ActionInstance | None = None

    def request(
        self,
        target_id: str,
        *,
        preferred_mode: TravelMode | None = None,
        reason: str | None = None,
        action_instance: ActionInstance | None = None,
    ) -> None:
        if not target_id:
            raise ValueError("navigation target_id must not be empty")
        self.target_id = target_id
        self.preferred_mode = preferred_mode
        self.reason = reason
        self.route = None
        self.primitives = ()
        self.current_primitive_index = 0
        self.completed_route_legs = 0
        self.status = NavigationStatus.REQUESTED
        self.correlation_id = None
        self.failure_reason = None
        self.action_instance = action_instance

    def clear(self, status: NavigationStatus = NavigationStatus.IDLE) -> None:
        self.target_id = None
        self.preferred_mode = None
        self.reason = None
        self.route = None
        self.primitives = ()
        self.current_primitive_index = 0
        self.completed_route_legs = 0
        self.status = status
        self.correlation_id = None
        self.failure_reason = None
        self.action_instance = None
