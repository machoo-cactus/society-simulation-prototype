from dataclasses import dataclass

from stage0_sim.application.navigation.destinations import DestinationResolver
from stage0_sim.application.navigation.knowledge import (
    KnownDestination,
    KnownTopologyProjection,
)
from stage0_sim.application.navigation.planner import (
    NavigationPlanningError,
    RecursiveRoutePlanner,
)
from stage0_sim.domain.components import (
    NavigationPrimitive,
    NavigationPrimitiveKind,
    SpatialLocationComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.world import (
    CityWorld,
    Locator,
    Route,
    SpaceRegistry,
    TravelMode,
    TraversalContext,
)


@dataclass(frozen=True, slots=True)
class PlannedNavigation:
    destination: KnownDestination
    route: Route
    primitives: tuple[NavigationPrimitive, ...]


class NavigationService:
    def __init__(
        self,
        registry: Registry,
        topology: SpaceRegistry,
        known_topology: KnownTopologyProjection,
        *,
        resolver: DestinationResolver | None = None,
        planner: RecursiveRoutePlanner | None = None,
    ) -> None:
        self.registry = registry
        self.topology = topology
        self.known_topology = known_topology
        self.resolver = resolver or DestinationResolver()
        self.planner = planner or RecursiveRoutePlanner()

    def plan(
        self,
        character_id: str,
        target_id: str,
        preferred_mode: TravelMode | None,
    ) -> PlannedNavigation:
        origin = self.registry.get_component(
            character_id,
            SpatialLocationComponent,
        ).locator
        if origin is None:
            raise NavigationPlanningError("current_locator_unavailable")
        destination = self.resolver.resolve(
            self.known_topology,
            character_id,
            target_id,
        )
        route = self.planner.plan(
            self.topology,
            origin,
            destination.locators,
            TraversalContext(
                character_id=character_id,
                requested_mode=(
                    preferred_mode.value
                    if preferred_mode is not None
                    else TravelMode.WALK.value
                ),
                occupied_locators=self._occupied_locators(character_id),
            ),
            allowed_transition_ids=self.known_topology.transition_ids(character_id),
        )
        return PlannedNavigation(
            destination=destination,
            route=route,
            primitives=self._compile(route, destination, preferred_mode),
        )

    def _occupied_locators(self, character_id: str) -> tuple[Locator, ...]:
        occupied: list[tuple[str, Locator]] = []
        for entity_id, spatial in self.registry.query(SpatialLocationComponent):
            if entity_id == character_id:
                continue
            locator = spatial.locator
            if locator is None:
                continue
            try:
                canonical = self.topology.resolve(
                    locator.space_id,
                    locator.local_reference,
                )
            except (KeyError, ValueError):
                continue
            occupied.append((entity_id, canonical))
        occupied.sort(key=lambda item: (item[0], item[1].stable_key))
        return tuple(locator for _, locator in occupied)

    def _compile(
        self,
        route: Route,
        destination: KnownDestination,
        preferred_mode: TravelMode | None,
    ) -> tuple[NavigationPrimitive, ...]:
        primitives: list[NavigationPrimitive] = []
        index = 0
        while index < len(route.legs):
            leg = route.legs[index]
            if (
                leg.executor_id == "movement"
                and leg.origin.space_id == leg.destination.space_id
            ):
                end = index + 1
                while end < len(route.legs):
                    candidate = route.legs[end]
                    if (
                        candidate.executor_id != "movement"
                        or candidate.origin.space_id
                        != candidate.destination.space_id
                    ):
                        break
                    end += 1
                primitives.append(
                    NavigationPrimitive(
                        kind=NavigationPrimitiveKind.MOVE,
                        origin=leg.origin,
                        destination=route.legs[end - 1].destination,
                        route_leg_start=index,
                        route_leg_end=end,
                    )
                )
                index = end
                continue

            if not self.registry.has_resource(CityWorld):
                raise NavigationPlanningError(
                    "unsupported_navigation_executor"
                )
            city = self.registry.get_resource(CityWorld)
            end = index + 1
            while end < len(route.legs):
                candidate = route.legs[end]
                if (
                    candidate.executor_id == "movement"
                    and candidate.origin.space_id
                    == candidate.destination.space_id
                ):
                    break
                end += 1
            segment = route.legs[index:end]
            terminal = segment[-1].destination
            destination_id = (
                terminal.space_id
                if terminal.space_id != city.id
                else destination.id
            )
            inbound_transition_id = next(
                (
                    candidate.transition_id
                    for candidate in reversed(segment)
                    if candidate.transition_id is not None
                    and candidate.destination.space_id != city.id
                ),
                None,
            )
            outbound_leg = next(
                (
                    candidate
                    for candidate in segment
                    if candidate.transition_id is not None
                    and candidate.origin.space_id != city.id
                    and candidate.destination.space_id == city.id
                ),
                None,
            )
            origin_network_node_id = self._network_node_id(
                outbound_leg.destination
                if outbound_leg is not None
                else leg.origin
                if leg.origin.space_id == city.id
                else None
            )
            route_edge_ids = tuple(
                dict.fromkeys(
                    candidate.transition_id.removesuffix(":reverse")
                    for candidate in segment
                    if candidate.transition_id is not None
                    and candidate.origin.space_id == city.id
                    and candidate.destination.space_id == city.id
                )
            )
            primitives.append(
                NavigationPrimitive(
                    kind=NavigationPrimitiveKind.TRAVEL,
                    origin=leg.origin,
                    destination=terminal,
                    route_leg_start=index,
                    route_leg_end=end,
                    destination_id=destination_id,
                    mode=preferred_mode or TravelMode.WALK,
                    entrance_transition_id=inbound_transition_id,
                    outbound_transition_id=(
                        outbound_leg.transition_id
                        if outbound_leg is not None
                        else None
                    ),
                    origin_network_node_id=origin_network_node_id,
                    route_edge_ids=route_edge_ids,
                )
            )
            index = end
        return tuple(primitives)

    @staticmethod
    def _network_node_id(locator: Locator | None) -> str | None:
        if locator is None:
            return None
        reference = locator.local_reference
        if not isinstance(reference, dict) or reference.get("kind") != "node":
            return None
        node_id = reference.get("node_id")
        return node_id if isinstance(node_id, str) else None
