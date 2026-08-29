from stage0_sim.application.agents.contracts import (
    CharacterObservation,
    ObservationFact,
    ObservedTarget,
)
from stage0_sim.application.perception.renderer import (
    DeterministicPerceptionRenderer,
)
from stage0_sim.domain.components import (
    ActivityComponent,
    CharacterProfileComponent,
    ControllerComponent,
    HomeostasisComponent,
    PerceptionComponent,
    PositionComponent,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import WorldMap


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
    world = registry.get_resource(WorldMap)
    renderer = DeterministicPerceptionRenderer()
    known_characters = set(perception.visible_now) | set(perception.knowledge)
    targets = [
        ObservedTarget(id=zone.id, kind="zone", name=zone.name)
        for zone in sorted(world.zones, key=lambda item: item.id)
    ]
    targets.extend(
        ObservedTarget(
            id=station.id,
            kind="station",
            name=station.name,
            supported_actions=station.supported_actions,
            available=station.available,
        )
        for station in sorted(world.stations, key=lambda item: item.id)
    )
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
    return CharacterObservation(
        agent_id=agent_id,
        display_name=profile.display_name,
        goals=profile.goals,
        simulation_time=context.clock.simulation_time,
        location_id=zone.id if zone is not None else None,
        activity=activity.current.value,
        satiety=homeostasis.satiety,
        energy=homeostasis.energy,
        stress=homeostasis.stress,
        targets=tuple(targets),
        facts=facts,
        recent_outcome=controller.last_outcome,
    )
