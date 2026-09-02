from dataclasses import dataclass

from stage0_sim.application.navigation.destinations import DestinationResolver
from stage0_sim.application.navigation.knowledge import (
    KnownDestination,
    KnownTopologyProjection,
)
from stage0_sim.domain.components import (
    NavigationPrimitive,
    NavigationPrimitiveKind,
    OpenableComponent,
    PhysicalInteractionRegistry,
    PhysicalRelationKind,
    PhysicalStateComponent,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.world import (
    CityWorld,
    Locator,
    Route,
    SpaceRegistry,
    TravelMode,
    TraversalContext,
)
from stage0_sim.domain.world.routing import (
    NavigationPlanningError,
    RecursiveRoutePlanner,
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
        *,
        authoritative: bool = False,
    ) -> PlannedNavigation:
        origin = self.registry.get_component(
            character_id,
            SpatialLocationComponent,
        ).locator
        if origin is None:
            raise NavigationPlanningError("current_locator_unavailable")
        destination = (
            self._authoritative_destination(target_id)
            if authoritative
            else self.resolver.resolve(
                self.known_topology,
                character_id,
                target_id,
            )
        )
        allowed_transition_ids = self._available_transition_ids(
            self._authoritative_transition_ids()
            if authoritative
            else self.known_topology.transition_ids(character_id)
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
                actor_footprint=(
                    self.registry.get_component(
                        character_id,
                        PhysicalStateComponent,
                    ).footprint
                    if self.registry.has_component(
                        character_id,
                        PhysicalStateComponent,
                    )
                    else None
                ),
                authorized_overlap_ids=self._authorized_overlap_ids(
                    character_id
                ),
            ),
            allowed_transition_ids=allowed_transition_ids,
        )
        return PlannedNavigation(
            destination=destination,
            route=route,
            primitives=self._compile(route, destination, preferred_mode),
        )

    def _authoritative_destination(
        self,
        target_id: str,
    ) -> KnownDestination:
        locators = self.topology.destination_locators(target_id)
        if not locators:
            raise NavigationPlanningError(
                "authoritative_destination_has_no_locator"
            )
        if self.registry.has_resource(CityWorld):
            city = self.registry.get_resource(CityWorld)
            try:
                item = city.world_object(target_id)
            except KeyError:
                item = None
            if item is not None:
                return KnownDestination(
                    id=item.id,
                    kind=(
                        "station"
                        if item.station is not None
                        else "transaction_point"
                        if item.transaction_point is not None
                        else "physical_object"
                    ),
                    name=item.name,
                    locators=locators,
                    supported_actions=(
                        item.station.supported_actions
                        if item.station is not None
                        else ()
                    ),
                    offers=(
                        item.transaction_point.offers
                        if item.transaction_point is not None
                        else ()
                    ),
                )
            try:
                room = city.room(target_id)
            except KeyError:
                room = None
            if room is not None:
                return KnownDestination(
                    target_id, "room", room.name, locators
                )
            try:
                building = city.building(target_id)
            except KeyError:
                building = None
            if building is not None:
                return KnownDestination(
                    target_id, "building", building.name, locators
                )
        return KnownDestination(target_id, "place", target_id, locators)

    def _authoritative_transition_ids(self) -> frozenset[str]:
        transition_ids = {
            transition.id
            for transition in self.topology.transitions()
        }
        if self.registry.has_resource(CityWorld):
            transition_ids.update(
                edge.id
                for edge in self.registry.get_resource(CityWorld).edges
            )
        return frozenset(transition_ids)

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
            if leg.executor_id == "portal":
                if leg.transition_id is None:
                    raise NavigationPlanningError(
                        "portal_transition_missing_id"
                    )
                door_id = leg.metadata.get("door_object_id")
                if isinstance(door_id, str) and door_id:
                    primitives.append(
                        NavigationPrimitive(
                            kind=NavigationPrimitiveKind.INTERACT,
                            origin=leg.origin,
                            destination=leg.origin,
                            route_leg_start=index,
                            route_leg_end=index + 1,
                            interaction=InteractionSpecification(
                                InteractionVerb.OPEN,
                                door_id,
                            ),
                        )
                    )
                primitives.append(
                    NavigationPrimitive(
                        kind=NavigationPrimitiveKind.TRANSITION,
                        origin=leg.origin,
                        destination=leg.destination,
                        route_leg_start=index,
                        route_leg_end=index + 1,
                        transition_id=leg.transition_id,
                    )
                )
                index += 1
                continue
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
                    candidate.executor_id == "portal"
                    or candidate.executor_id == "movement"
                    and candidate.origin.space_id
                    == candidate.destination.space_id
                ):
                    break
                end += 1
            segment = route.legs[index:end]
            terminal = segment[-1].destination
            if terminal.space_id == city.id:
                destination_id = destination.id
            else:
                try:
                    destination_id = city.room(
                        terminal.space_id
                    ).building_id
                except KeyError:
                    destination_id = terminal.space_id
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
            inbound_leg = next(
                (
                    candidate
                    for candidate in reversed(segment)
                    if candidate.transition_id is not None
                    and candidate.origin.space_id == city.id
                    and candidate.destination.space_id != city.id
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
            if outbound_leg is not None:
                outbound_door_id = outbound_leg.metadata.get(
                    "door_object_id"
                )
                if isinstance(outbound_door_id, str) and outbound_door_id:
                    primitives.append(
                        NavigationPrimitive(
                            kind=NavigationPrimitiveKind.INTERACT,
                            origin=outbound_leg.origin,
                            destination=outbound_leg.origin,
                            route_leg_start=index,
                            route_leg_end=end,
                            interaction=InteractionSpecification(
                                InteractionVerb.OPEN,
                                outbound_door_id,
                            ),
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
            if inbound_leg is not None:
                inbound_door_id = inbound_leg.metadata.get(
                    "door_object_id"
                )
                if isinstance(inbound_door_id, str) and inbound_door_id:
                    primitives.append(
                        NavigationPrimitive(
                            kind=NavigationPrimitiveKind.INTERACT,
                            origin=inbound_leg.destination,
                            destination=inbound_leg.destination,
                            route_leg_start=index,
                            route_leg_end=end,
                            interaction=InteractionSpecification(
                                InteractionVerb.OPEN,
                                inbound_door_id,
                            ),
                        )
                    )
            index = end
        return tuple(primitives)

    def _available_transition_ids(
        self,
        known_transition_ids: frozenset[str],
    ) -> frozenset[str]:
        availability = (
            self.registry.get_resource(EnvironmentAvailabilityRegistry)
            if self.registry.has_resource(EnvironmentAvailabilityRegistry)
            else None
        )
        allowed: set[str] = set()
        for transition_id in sorted(known_transition_ids):
            base_id = transition_id.removesuffix(":reverse")
            try:
                transition = self.topology.transition(base_id)
            except KeyError:
                allowed.add(transition_id)
                continue
            base_available = transition.metadata.get("available", True)
            if not isinstance(base_available, bool):
                base_available = True
            door_available = self._door_available(base_id)
            if door_available and (
                availability is None
                or availability.state(
                    base_id,
                    base_available=base_available,
                ).available
            ):
                allowed.add(transition_id)
        return frozenset(allowed)

    def _door_available(self, transition_id: str) -> bool:
        if not self.registry.has_resource(PhysicalInteractionRegistry):
            return True
        door_id = self.registry.get_resource(
            PhysicalInteractionRegistry
        ).door_for_transition(transition_id)
        if door_id is None or not self.registry.has_component(
            door_id,
            OpenableComponent,
        ):
            return True
        return True

    def _authorized_overlap_ids(
        self,
        character_id: str,
    ) -> frozenset[str]:
        if not self.registry.has_component(
            character_id,
            SpatialParentRelationComponent,
        ):
            return frozenset()
        relation = self.registry.get_component(
            character_id,
            SpatialParentRelationComponent,
        )
        if relation.kind is not PhysicalRelationKind.OCCUPIES_SLOT:
            return frozenset()
        return frozenset({relation.parent_id})

    @staticmethod
    def _network_node_id(locator: Locator | None) -> str | None:
        if locator is None:
            return None
        reference = locator.local_reference
        if not isinstance(reference, dict) or reference.get("kind") != "node":
            return None
        node_id = reference.get("node_id")
        return node_id if isinstance(node_id, str) else None
