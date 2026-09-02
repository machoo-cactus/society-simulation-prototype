from dataclasses import dataclass, replace
from math import floor

from stage0_sim.domain.components import (
    MovementComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PositionComponent,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    TravelComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.interactions import sync_held_object_poses
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)
from stage0_sim.domain.world import CardinalOrientation, Coordinate, find_path


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


def _physical_context(
    context: SystemContext,
    agent_id: str,
) -> tuple[PhysicalStateComponent | None, SpatialIndex | None, frozenset[str]]:
    state = (
        context.registry.get_component(agent_id, PhysicalStateComponent)
        if context.registry.has_component(agent_id, PhysicalStateComponent)
        else None
    )
    spatial_index = (
        context.registry.get_resource(SpatialIndex)
        if context.registry.has_resource(SpatialIndex)
        else None
    )
    authorized: frozenset[str] = frozenset()
    if context.registry.has_component(
        agent_id,
        SpatialParentRelationComponent,
    ):
        relation = context.registry.get_component(
            agent_id,
            SpatialParentRelationComponent,
        )
        if relation.kind is PhysicalRelationKind.OCCUPIES_SLOT:
            authorized = frozenset({relation.parent_id})
    return state, spatial_index, authorized


def _path_payload(path: tuple[Coordinate, ...]) -> dict[str, JsonValue]:
    if len(path) <= 64:
        return {
            "path": [coordinate.to_payload() for coordinate in path],
            "path_encoding": "full",
        }
    stride = max(1, (len(path) + 62) // 63)
    sampled = [path[index] for index in range(0, len(path), stride)]
    if sampled[-1] != path[-1]:
        sampled.append(path[-1])
    return {
        "path": [coordinate.to_payload() for coordinate in sampled],
        "path_encoding": "sampled_waypoints",
        "path_stride": stride,
    }


def _anchor_path_is_clear(
    context: SystemContext,
    agent_id: str,
    coordinates: tuple[Coordinate, ...],
) -> bool:
    if not coordinates:
        return True
    world = local_world_for_agent(context.registry, agent_id)
    if world is None:
        return False
    state, spatial_index, authorized = _physical_context(context, agent_id)
    if state is None or spatial_index is None:
        other_positions = {
            position.coordinate
            for other_id, position in context.registry.query(PositionComponent)
            if other_id != agent_id
            and shares_local_map(context.registry, agent_id, other_id)
        }
        return all(
            world.grid.is_walkable(coordinate)
            and coordinate not in other_positions
            for coordinate in coordinates
        )
    return all(
        world.grid.are_walkable(
            state.footprint.translated_cells(
                coordinate,
                state.pose.orientation,
            )
        )
        and spatial_index.can_place(
            replace(
                state,
                pose=replace(state.pose, anchor=coordinate),
            ),
            excluding=agent_id,
            authorized_overlaps=authorized,
        )
        for coordinate in coordinates
    )


def _blocking_entity_at(
    context: SystemContext,
    agent_id: str,
    coordinate: Coordinate,
) -> str | None:
    state, spatial_index, authorized = _physical_context(context, agent_id)
    if state is not None and spatial_index is not None:
        blockers = spatial_index.blocking_entities(
            replace(
                state,
                pose=replace(state.pose, anchor=coordinate),
            ),
            excluding=agent_id,
            authorized_overlaps=authorized,
        )
        return blockers[0] if blockers else None
    return next(
        (
            other_id
            for other_id, position in context.registry.query(PositionComponent)
            if other_id != agent_id
            and position.coordinate == coordinate
            and shares_local_map(context.registry, agent_id, other_id)
        ),
        None,
    )


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
            PositionComponent,
            MovementComponent,
        ):
            if (
                context.registry.has_component(agent_id, TravelComponent)
                and context.registry.get_component(
                    agent_id,
                    TravelComponent,
                ).status.value
                in {"ROUTE_PLANNED", "TRAVELLING"}
            ):
                continue
            position = context.registry.get_component(
                agent_id,
                PositionComponent,
            )
            movement = context.registry.get_component(
                agent_id,
                MovementComponent,
            )
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

            state, spatial_index, authorized = _physical_context(
                context,
                agent_id,
            )
            if movement.path:
                revision_changed = (
                    spatial_index is not None
                    and movement.planned_spatial_revision
                    != spatial_index.revision
                )
                if _anchor_path_is_clear(
                    context,
                    agent_id,
                    movement.path[:1],
                ):
                    if revision_changed and spatial_index is not None:
                        movement.planned_spatial_revision = spatial_index.revision
                    continue
                self._invalidate_path(
                    context,
                    agent_id,
                    movement,
                    (
                        "spatial_revision_changed"
                        if revision_changed
                        else "occupancy_changed"
                    ),
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
            fallback_occupied = frozenset(
                coordinate
                for other_id, coordinate in positions.items()
                if other_id != agent_id
                and shares_local_map(context.registry, agent_id, other_id)
                and not context.registry.has_component(
                    other_id,
                    PhysicalStateComponent,
                )
            )
            path = find_path(
                world.grid,
                position.coordinate,
                destination,
                fallback_occupied,
                footprint=state.footprint if state is not None else None,
                orientation=(
                    state.pose.orientation
                    if state is not None
                    else CardinalOrientation.NORTH
                ),
                spatial_index=spatial_index if state is not None else None,
                room_id=state.pose.room_id if state is not None else None,
                entity_id=agent_id if state is not None else None,
                authorized_overlaps=authorized,
            )
            if path is None:
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
            movement.planned_spatial_revision = (
                spatial_index.revision if spatial_index is not None else None
            )
            _emit_for_agent(
                context,
                "path.planned",
                agent_id,
                {
                    "destination": destination.to_payload(),
                    "length": len(path),
                    **_path_payload(path),
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
        movement.distance_remainder = 0.0
        movement.planned_spatial_revision = None

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
        movement.planned_spatial_revision = None

@dataclass(frozen=True, slots=True)
class MovementSystem:
    name: str = "movement"
    order: int = 200

    def update(self, context: SystemContext) -> None:
        for agent_id in context.registry.query_entities(
            PositionComponent,
            MovementComponent,
        ):
            if (
                context.registry.has_component(agent_id, TravelComponent)
                and context.registry.get_component(
                    agent_id,
                    TravelComponent,
                ).status.value
                in {"ROUTE_PLANNED", "TRAVELLING"}
            ):
                continue
            movement = context.registry.get_component(
                agent_id,
                MovementComponent,
            )
            if not movement.path:
                continue
            world = local_world_for_agent(context.registry, agent_id)
            if world is None:
                self._invalidate(
                    context,
                    agent_id,
                    movement,
                    "local_space_unavailable",
                )
                continue
            distance = (
                movement.speed_legacy_cells_per_second
                * world.local_distance_per_legacy_cell()
                * context.clock.dt
                + movement.distance_remainder
            )
            step_count = min(len(movement.path), floor(distance))
            if step_count <= 0:
                movement.distance_remainder = distance
                continue
            swept = movement.path[:step_count]
            if not _anchor_path_is_clear(context, agent_id, swept):
                blocked_coordinate = next(
                    coordinate
                    for coordinate in swept
                    if not _anchor_path_is_clear(
                        context,
                        agent_id,
                        (coordinate,),
                    )
                )
                blocked_by = _blocking_entity_at(
                    context,
                    agent_id,
                    blocked_coordinate,
                )
                self._invalidate(
                    context,
                    agent_id,
                    movement,
                    (
                        "movement_conflict"
                        if blocked_by is not None
                        else "tile_not_walkable"
                    ),
                    coordinate=blocked_coordinate,
                    blocked_by=blocked_by,
                )
                continue

            position = context.registry.get_component(
                agent_id,
                PositionComponent,
            )
            previous = position.coordinate
            destination = swept[-1]
            state, spatial_index, authorized = _physical_context(
                context,
                agent_id,
            )
            if state is not None and spatial_index is not None:
                next_state = replace(
                    state,
                    pose=replace(state.pose, anchor=destination),
                )
                spatial_index.update(
                    SpatialIndexEntry(agent_id, next_state, dynamic=True),
                    authorized_overlaps=authorized,
                )
                context.registry.set_component(agent_id, next_state)
            position.coordinate = destination
            if context.registry.has_component(
                agent_id,
                SpatialLocationComponent,
            ):
                spatial = context.registry.get_component(
                    agent_id,
                    SpatialLocationComponent,
                )
                if spatial.location.local_coordinate is not None:
                    spatial.location = replace(
                        spatial.location,
                        local_coordinate=destination,
                    )
            if state is not None:
                sync_held_object_poses(
                    context.registry,
                    agent_id,
                    state.pose.room_id,
                    destination,
                )
            movement.path = movement.path[step_count:]
            movement.distance_remainder = (
                0.0
                if not movement.path
                else round(distance - step_count, 12)
            )
            movement.planned_spatial_revision = (
                spatial_index.revision if spatial_index is not None else None
            )
            _emit_for_agent(
                context,
                "agent.moved",
                agent_id,
                {
                    "from": previous.to_payload(),
                    "to": destination.to_payload(),
                    "distance_microcells": step_count,
                    **action_lineage_payload(movement.action_instance),
                },
                correlation_id=movement.path_correlation_id,
            )
            if movement.destination == destination and not movement.path:
                PathfindingSystem._complete_path(context, agent_id, movement)

    @staticmethod
    def _invalidate(
        context: SystemContext,
        agent_id: str,
        movement: MovementComponent,
        reason: str,
        *,
        coordinate: Coordinate | None = None,
        blocked_by: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "reason": reason,
            **action_lineage_payload(movement.action_instance),
        }
        if coordinate is not None:
            payload["coordinate"] = coordinate.to_payload()
        if blocked_by is not None:
            payload["blocked_by"] = blocked_by
        _emit_for_agent(
            context,
            "path.invalidated",
            agent_id,
            payload,
            correlation_id=movement.path_correlation_id,
        )
        movement.path = ()
        movement.retry_after_tick = context.clock.tick + 1
        movement.planned_spatial_revision = None
