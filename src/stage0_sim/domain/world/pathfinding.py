import heapq

from stage0_sim.domain.world.model import Coordinate, WorldGrid
from stage0_sim.domain.world.physical import (
    CardinalOrientation,
    Footprint,
    MovementObstruction,
    PhysicalPose,
    PhysicalStateComponent,
    SpatialIndex,
)


def manhattan_distance(start: Coordinate, goal: Coordinate) -> int:
    return abs(start.x - goal.x) + abs(start.y - goal.y)


def find_path(
    grid: WorldGrid,
    start: Coordinate,
    goal: Coordinate,
    occupied: frozenset[Coordinate] = frozenset(),
    *,
    footprint: Footprint | None = None,
    orientation: CardinalOrientation = CardinalOrientation.NORTH,
    spatial_index: SpatialIndex | None = None,
    room_id: str | None = None,
    entity_id: str | None = None,
    authorized_overlaps: frozenset[str] = frozenset(),
) -> tuple[Coordinate, ...] | None:
    """Find a deterministic shortest path, excluding the starting coordinate."""
    if not _anchor_is_walkable(
        grid,
        start,
        footprint=footprint,
        orientation=orientation,
        spatial_index=spatial_index,
        room_id=room_id,
        entity_id=entity_id,
        authorized_overlaps=authorized_overlaps,
    ) or not _anchor_is_walkable(
        grid,
        goal,
        footprint=footprint,
        orientation=orientation,
        spatial_index=spatial_index,
        room_id=room_id,
        entity_id=entity_id,
        authorized_overlaps=authorized_overlaps,
    ):
        return None
    if goal in occupied and goal != start:
        return None
    if start == goal:
        return ()

    frontier: list[tuple[int, int, int, int, Coordinate]] = []
    heapq.heappush(
        frontier,
        (manhattan_distance(start, goal), 0, start.y, start.x, start),
    )
    came_from: dict[Coordinate, Coordinate] = {}
    best_cost: dict[Coordinate, int] = {start: 0}

    while frontier:
        _, cost, _, _, current = heapq.heappop(frontier)
        if cost != best_cost[current]:
            continue
        if current == goal:
            return _reconstruct_path(came_from, start, goal)

        for neighbor in _neighbor_candidates(current):
            if not _anchor_is_walkable(
                grid,
                neighbor,
                footprint=footprint,
                orientation=orientation,
                spatial_index=spatial_index,
                room_id=room_id,
                entity_id=entity_id,
                authorized_overlaps=authorized_overlaps,
            ):
                continue
            if neighbor in occupied and neighbor != goal:
                continue
            next_cost = cost + 1
            if next_cost >= best_cost.get(neighbor, next_cost + 1):
                continue
            best_cost[neighbor] = next_cost
            came_from[neighbor] = current
            priority = next_cost + manhattan_distance(neighbor, goal)
            heapq.heappush(
                frontier,
                (priority, next_cost, neighbor.y, neighbor.x, neighbor),
            )
    return None


def _neighbor_candidates(coordinate: Coordinate) -> tuple[Coordinate, ...]:
    return (
        Coordinate(coordinate.x, coordinate.y - 1),
        Coordinate(coordinate.x - 1, coordinate.y),
        Coordinate(coordinate.x + 1, coordinate.y),
        Coordinate(coordinate.x, coordinate.y + 1),
    )


def _anchor_is_walkable(
    grid: WorldGrid,
    anchor: Coordinate,
    *,
    footprint: Footprint | None,
    orientation: CardinalOrientation,
    spatial_index: SpatialIndex | None,
    room_id: str | None,
    entity_id: str | None,
    authorized_overlaps: frozenset[str],
) -> bool:
    if footprint is None:
        return grid.is_walkable(anchor)
    occupied_cells = footprint.translated_cells(anchor, orientation)
    if not grid.are_walkable(occupied_cells):
        return False
    if spatial_index is None:
        return True
    if room_id is None:
        raise ValueError("room_id is required with a spatial index")
    return spatial_index.can_place(
        PhysicalStateComponent(
            pose=PhysicalPose(room_id, anchor, orientation),
            footprint=footprint,
            movement_obstruction=MovementObstruction.HARD,
        ),
        excluding=entity_id,
        authorized_overlaps=authorized_overlaps,
    )


def _reconstruct_path(
    came_from: dict[Coordinate, Coordinate],
    start: Coordinate,
    goal: Coordinate,
) -> tuple[Coordinate, ...]:
    current = goal
    reversed_path = [current]
    while current != start:
        current = came_from[current]
        if current != start:
            reversed_path.append(current)
    return tuple(reversed(reversed_path))
