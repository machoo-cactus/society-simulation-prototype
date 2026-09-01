from dataclasses import dataclass

from stage0_sim.domain.components import (
    MovementComponent,
    PositionComponent,
    SpatialLocationComponent,
    TravelComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    emit_action_lifecycle,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)
from stage0_sim.domain.world import Coordinate, find_path


def _positions(context: SystemContext) -> dict[str, Coordinate]:
    return {
        entity_id: position.coordinate
        for entity_id, position in context.registry.query(PositionComponent)
    }


def _emit_for_agent(
    context: SystemContext,
    event_type: str,
    agent_id: str,
    payload: dict[str, JsonValue],
    *,
    causation_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    event = context.events.emit(
        event_type,
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload=payload,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    return event.event_id


@dataclass(frozen=True, slots=True)
class PathfindingSystem:
    name: str = "pathfinding"
    order: int = 100
    retry_interval_ticks: int = 1

    def __post_init__(self) -> None:
        if self.retry_interval_ticks <= 0:
            raise ValueError("retry_interval_ticks must be greater than zero")

    def update(self, context: SystemContext) -> None:
        positions = _positions(context)

        for agent_id in context.registry.query_entities(
            PositionComponent, MovementComponent
        ):
            if (
                context.registry.has_component(agent_id, TravelComponent)
                and context.registry.get_component(
                    agent_id, TravelComponent
                ).status.value
                in {"ROUTE_PLANNED", "TRAVELLING"}
            ):
                continue
            occupied = frozenset(
                coordinate
                for other_id, coordinate in positions.items()
                if shares_local_map(context.registry, agent_id, other_id)
            )
            position = context.registry.get_component(agent_id, PositionComponent)
            movement = context.registry.get_component(agent_id, MovementComponent)
            destination = movement.destination
            if destination is None:
                continue
            world = local_world_for_agent(context.registry, agent_id)
            if world is None:
                movement.retry_after_tick = (
                    context.clock.tick + self.retry_interval_ticks
                )
                _emit_for_agent(
                    context,
                    "path.failed",
                    agent_id,
                    {
                        "origin": position.coordinate.to_payload(),
                        "destination": destination.to_payload(),
                        "reason": "local_space_unavailable",
                        "retry_at_tick": movement.retry_after_tick,
                        **action_lineage_payload(movement.action_instance),
                    },
                    correlation_id=movement.path_correlation_id,
                )
                continue
            if position.coordinate == destination:
                self._complete_path(context, agent_id, movement)
                continue
            if context.clock.tick < movement.retry_after_tick:
                continue

            if movement.path:
                next_coordinate = movement.path[0]
                other_positions = occupied - {position.coordinate}
                if (
                    world.grid.is_walkable(next_coordinate)
                    and next_coordinate not in other_positions
                ):
                    continue
                self._invalidate_path(
                    context,
                    agent_id,
                    movement,
                    "occupancy_changed"
                    if next_coordinate in other_positions
                    else "tile_not_walkable",
                )

            correlation_id = movement.path_correlation_id
            request_id = _emit_for_agent(
                context,
                "path.requested",
                agent_id,
                {
                    "origin": position.coordinate.to_payload(),
                    "destination": destination.to_payload(),
                    **action_lineage_payload(movement.action_instance),
                },
                correlation_id=correlation_id,
            )
            if correlation_id is None:
                correlation_id = request_id
                movement.path_correlation_id = correlation_id
            path = find_path(
                world.grid,
                position.coordinate,
                destination,
                occupied - {position.coordinate},
            )
            if path is None:
                movement.retry_after_tick = context.clock.tick + self.retry_interval_ticks
                _emit_for_agent(
                    context,
                    "path.failed",
                    agent_id,
                    {
                        "origin": position.coordinate.to_payload(),
                        "destination": destination.to_payload(),
                        "reason": "no_path",
                        "retry_at_tick": movement.retry_after_tick,
                        **action_lineage_payload(movement.action_instance),
                    },
                    causation_id=request_id,
                    correlation_id=correlation_id,
                )
                continue

            movement.path = path
            movement.retry_after_tick = 0
            _emit_for_agent(
                context,
                "path.planned",
                agent_id,
                {
                    "destination": destination.to_payload(),
                    "length": len(path),
                    "path": [coordinate.to_payload() for coordinate in path],
                    **action_lineage_payload(movement.action_instance),
                },
                causation_id=request_id,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _complete_path(
        context: SystemContext,
        agent_id: str,
        movement: MovementComponent,
    ) -> None:
        destination = movement.destination
        if destination is None:
            return
        _emit_for_agent(
            context,
            "path.completed",
            agent_id,
            {
                "destination": destination.to_payload(),
                **action_lineage_payload(movement.action_instance),
            },
            correlation_id=movement.path_correlation_id,
        )
        movement.destination = None
        movement.path = ()
        movement.path_correlation_id = None
        movement.action_instance = None

    @staticmethod
    def _invalidate_path(
        context: SystemContext,
        agent_id: str,
        movement: MovementComponent,
        reason: str,
    ) -> None:
        _emit_for_agent(
            context,
            "path.invalidated",
            agent_id,
            {
                "reason": reason,
                **action_lineage_payload(movement.action_instance),
            },
            correlation_id=movement.path_correlation_id,
        )
        movement.path = ()


@dataclass(frozen=True, slots=True)
class MovementSystem:
    name: str = "movement"
    order: int = 200

    def update(self, context: SystemContext) -> None:
        positions = _positions(context)

        for agent_id in context.registry.query_entities(
            PositionComponent, MovementComponent
        ):
            if (
                context.registry.has_component(agent_id, TravelComponent)
                and context.registry.get_component(
                    agent_id, TravelComponent
                ).status.value
                in {"ROUTE_PLANNED", "TRAVELLING"}
            ):
                continue
            occupied = {
                coordinate: other_id
                for other_id, coordinate in positions.items()
                if shares_local_map(context.registry, agent_id, other_id)
            }
            position = context.registry.get_component(agent_id, PositionComponent)
            movement = context.registry.get_component(agent_id, MovementComponent)
            if not movement.path:
                continue
            world = local_world_for_agent(context.registry, agent_id)
            if world is None:
                _emit_for_agent(
                    context,
                    "path.invalidated",
                    agent_id,
                    {
                        "reason": "local_space_unavailable",
                        **action_lineage_payload(movement.action_instance),
                    },
                    correlation_id=movement.path_correlation_id,
                )
                movement.path = ()
                movement.retry_after_tick = context.clock.tick + 1
                continue
            next_coordinate = movement.path[0]
            blocking_agent = occupied.get(next_coordinate)
            if blocking_agent is not None and blocking_agent != agent_id:
                _emit_for_agent(
                    context,
                    "path.invalidated",
                    agent_id,
                    {
                        "reason": "movement_conflict",
                        "blocked_by": blocking_agent,
                        "coordinate": next_coordinate.to_payload(),
                        **action_lineage_payload(movement.action_instance),
                    },
                    correlation_id=movement.path_correlation_id,
                )
                movement.path = ()
                movement.retry_after_tick = context.clock.tick + 1
                continue
            if not world.grid.is_walkable(next_coordinate):
                _emit_for_agent(
                    context,
                    "path.invalidated",
                    agent_id,
                    {
                        "reason": "tile_not_walkable",
                        "coordinate": next_coordinate.to_payload(),
                        **action_lineage_payload(movement.action_instance),
                    },
                    correlation_id=movement.path_correlation_id,
                )
                movement.path = ()
                movement.retry_after_tick = context.clock.tick + 1
                continue

            previous = position.coordinate
            occupied.pop(previous)
            occupied[next_coordinate] = agent_id
            position.coordinate = next_coordinate
            positions[agent_id] = next_coordinate
            if context.registry.has_component(
                agent_id, SpatialLocationComponent
            ):
                spatial = context.registry.get_component(
                    agent_id, SpatialLocationComponent
                )
                if spatial.location.local_coordinate is not None:
                    from dataclasses import replace

                    spatial.location = replace(
                        spatial.location,
                        local_coordinate=next_coordinate,
                    )
            movement.path = movement.path[1:]
            _emit_for_agent(
                context,
                "agent.moved",
                agent_id,
                {
                    "from": previous.to_payload(),
                    "to": next_coordinate.to_payload(),
                    **action_lineage_payload(movement.action_instance),
                },
                correlation_id=movement.path_correlation_id,
            )
            if movement.action_instance is not None:
                emit_action_lifecycle(
                    context,
                    "action.progressed",
                    agent_id,
                    movement.action_instance,
                    {
                        "from": previous.to_payload(),
                        "to": next_coordinate.to_payload(),
                        "remaining_path_steps": len(movement.path),
                    },
                )

            if movement.destination == next_coordinate and not movement.path:
                PathfindingSystem._complete_path(context, agent_id, movement)
