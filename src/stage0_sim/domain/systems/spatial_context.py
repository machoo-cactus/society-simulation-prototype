from stage0_sim.domain.components import SpatialLocationComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.world import CityWorld, SpatialScale, WorldMap


def local_world_for_agent(registry: Registry, agent_id: str) -> WorldMap:
    if (
        registry.has_resource(CityWorld)
        and registry.has_component(agent_id, SpatialLocationComponent)
    ):
        location = registry.get_component(
            agent_id, SpatialLocationComponent
        ).location
        if location.scale is SpatialScale.BUILDING:
            return registry.get_resource(CityWorld).local_map_for_building(
                location.place_id
            )
    return registry.get_resource(WorldMap)


def shares_local_map(
    registry: Registry,
    first_id: str,
    second_id: str,
) -> bool:
    if not registry.has_resource(CityWorld):
        return True
    if not (
        registry.has_component(first_id, SpatialLocationComponent)
        and registry.has_component(second_id, SpatialLocationComponent)
    ):
        return True
    first = registry.get_component(
        first_id, SpatialLocationComponent
    ).location
    second = registry.get_component(
        second_id, SpatialLocationComponent
    ).location
    return (
        first.scale is SpatialScale.BUILDING
        and second.scale is SpatialScale.BUILDING
        and first.place_id == second.place_id
    )
