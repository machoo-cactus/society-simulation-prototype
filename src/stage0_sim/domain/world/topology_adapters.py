from dataclasses import dataclass

from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.world.city import (
    CityWorld,
    TravelMode,
    find_transport_route,
)
from stage0_sim.domain.world.model import Coordinate, WorldMap
from stage0_sim.domain.world.pathfinding import find_path
from stage0_sim.domain.world.topology import (
    LocalRoute,
    Locator,
    RouteLeg,
    Transition,
    TraversalContext,
)


@dataclass(frozen=True, slots=True)
class GridTopology:
    space_id: str
    world: WorldMap

    def locator(self, coordinate: Coordinate) -> Locator:
        if not self.world.grid.contains(coordinate):
            raise ValueError(f"coordinate outside space {self.space_id}")
        return Locator(
            self.space_id,
            {"kind": "coordinate", "x": coordinate.x, "y": coordinate.y},
        )

    def resolve(self, reference: JsonValue) -> Locator:
        coordinate = self._coordinate(reference)
        if not self.world.grid.contains(coordinate):
            raise ValueError(f"coordinate outside space {self.space_id}")
        return self.locator(coordinate)

    def coordinate(self, locator: Locator) -> Coordinate:
        if locator.space_id != self.space_id:
            raise ValueError(f"locator is not in space {self.space_id}")
        return self._coordinate(locator.local_reference)

    def plan_local_route(
        self,
        origin: Locator,
        destination: Locator,
        traversal_context: TraversalContext,
    ) -> LocalRoute | None:
        start = self.coordinate(origin)
        goal = self.coordinate(destination)
        occupied = frozenset(
            self.coordinate(locator)
            for locator in traversal_context.occupied_locators
            if locator.space_id == self.space_id
        )
        path = find_path(self.world.grid, start, goal, occupied)
        if path is None:
            return None
        legs: list[RouteLeg] = []
        current = origin
        for coordinate in path:
            next_locator = self.locator(coordinate)
            legs.append(
                RouteLeg(
                    origin=current,
                    destination=next_locator,
                    traversal_kind="grid_step",
                    executor_id="movement",
                    cost=1.0,
                )
            )
            current = next_locator
        return LocalRoute(
            origin=origin,
            destination=destination,
            legs=tuple(legs),
            total_cost=float(len(path)),
        )

    def outgoing_transitions(
        self,
        locator: Locator,
    ) -> tuple[Transition, ...]:
        self.coordinate(locator)
        return ()

    @staticmethod
    def _coordinate(reference: JsonValue) -> Coordinate:
        if not isinstance(reference, dict) or reference.get("kind") != "coordinate":
            raise ValueError("grid locator must be a coordinate reference")
        x = reference.get("x")
        y = reference.get("y")
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            raise ValueError("grid coordinate values must be integers")
        return Coordinate(x, y)


@dataclass(frozen=True, slots=True)
class SparseGraphTopology:
    space_id: str
    city: CityWorld

    def node_locator(self, node_id: str) -> Locator:
        self.city.node(node_id)
        return Locator(
            self.space_id,
            {"kind": "node", "node_id": node_id},
        )

    def edge_locator(self, edge_id: str, progress: float) -> Locator:
        self.city.edge(edge_id)
        if (
            isinstance(progress, bool)
            or not isinstance(progress, (int, float))
            or not 0.0 <= progress <= 1.0
        ):
            raise ValueError("edge progress must be between zero and one")
        normalized_progress = float(progress)
        return Locator(
            self.space_id,
            {
                "kind": "edge",
                "edge_id": edge_id,
                "progress": normalized_progress,
            },
        )

    def resolve(self, reference: JsonValue) -> Locator:
        if not isinstance(reference, dict):
            raise ValueError("graph locator reference must be an object")
        kind = reference.get("kind")
        if kind == "node":
            node_id = reference.get("node_id")
            if not isinstance(node_id, str):
                raise ValueError("graph node locator requires node_id")
            return self.node_locator(node_id)
        if kind == "edge":
            edge_id = reference.get("edge_id")
            progress = reference.get("progress")
            if not isinstance(edge_id, str) or not isinstance(progress, (int, float)):
                raise ValueError("graph edge locator requires edge_id and progress")
            if isinstance(progress, bool):
                raise ValueError("graph edge progress must be numeric")
            return self.edge_locator(edge_id, float(progress))
        raise ValueError("unknown graph locator kind")

    def plan_local_route(
        self,
        origin: Locator,
        destination: Locator,
        traversal_context: TraversalContext,
    ) -> LocalRoute | None:
        origin_node = self._node_id(origin)
        destination_node = self._node_id(destination)
        try:
            mode = TravelMode(traversal_context.requested_mode or TravelMode.WALK.value)
        except ValueError as error:
            raise ValueError(
                f"unknown requested travel mode: {traversal_context.requested_mode}"
            ) from error
        route = find_transport_route(
            self.city,
            origin_node,
            destination_node,
            mode,
            allowed_edge_ids=(
                frozenset(
                    transition_id.removesuffix(":reverse")
                    for transition_id in traversal_context.allowed_transition_ids
                )
                if traversal_context.allowed_transition_ids is not None
                else None
            ),
        )
        if route is None:
            return None
        legs = tuple(
            RouteLeg(
                origin=self.node_locator(leg.from_node_id),
                destination=self.node_locator(leg.to_node_id),
                traversal_kind=f"transport_{leg.mode.value.lower()}",
                executor_id="travel",
                cost=leg.duration_seconds,
                transition_id=leg.edge_id,
                metadata={"mode": leg.mode.value},
            )
            for leg in route
        )
        return LocalRoute(
            origin=origin,
            destination=destination,
            legs=legs,
            total_cost=sum(leg.duration_seconds for leg in route),
        )

    def outgoing_transitions(
        self,
        locator: Locator,
    ) -> tuple[Transition, ...]:
        node_id = self._node_id(locator)
        transitions: list[Transition] = []
        for edge in sorted(self.city.edges, key=lambda item: item.id):
            if edge.from_node_id == node_id:
                transitions.append(
                    self._edge_transition(
                        edge.id,
                        edge.from_node_id,
                        edge.to_node_id,
                    )
                )
            if edge.bidirectional and edge.to_node_id == node_id:
                transitions.append(
                    self._edge_transition(
                        f"{edge.id}:reverse",
                        edge.to_node_id,
                        edge.from_node_id,
                    )
                )
        return tuple(transitions)

    def _node_id(self, locator: Locator) -> str:
        if locator.space_id != self.space_id:
            raise ValueError(f"locator is not in space {self.space_id}")
        reference = locator.local_reference
        if not isinstance(reference, dict) or reference.get("kind") != "node":
            raise ValueError("local graph routes currently require node locators")
        node_id = reference.get("node_id")
        if not isinstance(node_id, str):
            raise ValueError("graph node locator requires node_id")
        self.city.node(node_id)
        return node_id

    def _edge_transition(
        self,
        transition_id: str,
        from_node_id: str,
        to_node_id: str,
    ) -> Transition:
        edge_id = transition_id.removesuffix(":reverse")
        edge = self.city.edge(edge_id)
        return Transition(
            id=transition_id,
            from_locator=self.node_locator(from_node_id),
            to_locator=self.node_locator(to_node_id),
            traversal_kind="transport_edge",
            executor_id="travel",
            cost_model_id="transport_duration",
            metadata={
                "edge_id": edge.id,
                "allowed_modes": [
                    mode.value for mode in sorted(edge.allowed_modes)
                ],
            },
        )
