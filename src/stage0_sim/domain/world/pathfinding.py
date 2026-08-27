import heapq

from stage0_sim.domain.world.model import Coordinate, WorldGrid


def manhattan_distance(start: Coordinate, goal: Coordinate) -> int:
    return abs(start.x - goal.x) + abs(start.y - goal.y)


def find_path(
    grid: WorldGrid,
    start: Coordinate,
    goal: Coordinate,
    occupied: frozenset[Coordinate] = frozenset(),
) -> tuple[Coordinate, ...] | None:
    """Find a deterministic shortest path, excluding the starting coordinate."""
    if not grid.is_walkable(start) or not grid.is_walkable(goal):
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

        for neighbor in grid.neighbors(current):
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
