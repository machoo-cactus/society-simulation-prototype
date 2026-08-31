from typing import Literal, cast

from stage0_sim.application.agents.contracts import (
    CalendarTimeObservation,
    CharacterObservation,
    ObservationFact,
    ObservedTarget,
)
from stage0_sim.application.navigation import NavigationService
from stage0_sim.application.perception.renderer import (
    DeterministicPerceptionRenderer,
)
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActivityComponent,
    CharacterProfileComponent,
    ControllerComponent,
    HomeostasisComponent,
    PerceptionComponent,
    PlannerComponent,
    PositionComponent,
    SpatialLocationComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import CityWorld, TravelMode


def build_character_observation(
    context: SystemContext, agent_id: str
) -> CharacterObservation:
    registry = context.registry
    profile = registry.get_component(agent_id, CharacterProfileComponent)
    controller = registry.get_component(agent_id, ControllerComponent)
    position = registry.get_component(agent_id, PositionComponent)
    activity = registry.get_component(agent_id, ActivityComponent)
    homeostasis = registry.get_component(agent_id, HomeostasisComponent)
    perception = registry.get_component(agent_id, PerceptionComponent)
    world = local_world_for_agent(registry, agent_id)
    renderer = DeterministicPerceptionRenderer()
    known_characters = set(perception.visible_now) | set(perception.knowledge)
    navigation = registry.get_resource(NavigationService)
    targets = [
        ObservedTarget(
            id=destination.id,
            kind=cast(
                Literal["zone", "station", "building", "outdoor"],
                destination.kind,
            ),
            name=destination.name,
            supported_actions=destination.supported_actions,
            available=destination.available,
        )
        for destination in navigation.known_topology.destinations(agent_id)
        if destination.kind in {"zone", "station", "building", "outdoor"}
    ]
    available_travel_modes: tuple[str, ...] = (TravelMode.WALK.value,)
    spatial_payload: dict[str, JsonValue] | None = None
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        available = {TravelMode.WALK}
        available.update(vehicle.vehicle_type for vehicle in city.vehicles)
        if any(
            TravelMode.METRO in edge.allowed_modes for edge in city.edges
        ):
            available.add(TravelMode.METRO)
        available_travel_modes = tuple(
            mode.value for mode in TravelMode if mode in available
        )
    if registry.has_component(agent_id, SpatialLocationComponent):
        location = registry.get_component(
            agent_id, SpatialLocationComponent
        ).location
        spatial_payload = {
            "scale": location.scale.value,
            "place_id": location.place_id,
            "local_coordinate": (
                location.local_coordinate.to_payload()
                if location.local_coordinate is not None
                else None
            ),
            "network_node_id": location.network_node_id,
            "edge_id": location.edge_id,
            "edge_progress": location.edge_progress,
        }
    for target_id in sorted(known_characters):
        target_profile = (
            registry.get_component(target_id, CharacterProfileComponent)
            if registry.has_component(target_id, CharacterProfileComponent)
            else None
        )
        knowledge = perception.knowledge.get(target_id)
        targets.append(
            ObservedTarget(
                id=target_id,
                kind="character",
                name=(
                    target_profile.display_name
                    if target_profile is not None
                    else target_id
                ),
                last_observed_tick=(
                    knowledge.observed_tick if knowledge is not None else None
                ),
            )
        )
    facts = tuple(
        ObservationFact(
            fact_id=item.fact.fact_id,
            fact_type=item.fact.fact_type,
            text=renderer.render_fact(item),
            tick=item.fact.tick,
            subject_id=item.fact.subject_id,
        )
        for item in perception.inbox
    )
    perception.inbox.clear()
    zone = world.zone_at(position.coordinate)
    calendar_time = None
    if registry.has_resource(SimulationCalendar):
        calendar_payload = registry.get_resource(
            SimulationCalendar
        ).payload_at(context.clock.simulation_time)
        calendar_time = CalendarTimeObservation(
            datetime=str(calendar_payload["datetime"]),
            date=str(calendar_payload["date"]),
            time=str(calendar_payload["time"]),
            weekday=str(calendar_payload["weekday"]),
            period=str(calendar_payload["period"]),
        )
    planner = (
        registry.get_component(agent_id, PlannerComponent)
        if registry.has_component(agent_id, PlannerComponent)
        else PlannerComponent()
    )
    return CharacterObservation(
        agent_id=agent_id,
        display_name=profile.display_name,
        goals=planner.daily_goals,
        current_priorities=planner.current_priorities,
        simulation_time=context.clock.simulation_time,
        location_id=zone.id if zone is not None else None,
        activity=activity.current.value,
        satiety=homeostasis.satiety,
        energy=homeostasis.energy,
        stress=homeostasis.stress,
        targets=tuple(targets),
        facts=facts,
        recent_outcome=controller.last_outcome,
        spatial_location=spatial_payload,
        available_travel_modes=available_travel_modes,
        calendar_time=calendar_time,
    )
