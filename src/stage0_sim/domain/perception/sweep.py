from __future__ import annotations

from dataclasses import dataclass

from stage0_sim.domain.world import (
    Coordinate,
    SenseModality,
    SpatialIndex,
    WorldGrid,
)


@dataclass(frozen=True, slots=True)
class SensorySweepResult:
    clear: bool
    distance: int | None
    candidate_count: int
    blocking_cell: Coordinate | None = None
    blocking_entity_id: str | None = None


def sensory_sweep(
    grid: WorldGrid,
    *,
    room_id: str,
    origin_cells: frozenset[Coordinate],
    target_cells: frozenset[Coordinate],
    maximum_range: int,
    modality: SenseModality,
    spatial_index: SpatialIndex | None = None,
    ignored_entity_ids: frozenset[str] = frozenset(),
) -> SensorySweepResult:
    if maximum_range < 0:
        raise ValueError("sensory sweep range must not be negative")
    origins = _boundary_cells(origin_cells)
    targets = _boundary_cells(target_cells)
    candidates = sorted(
        (
            (
                abs(origin.x - target.x) + abs(origin.y - target.y),
                origin,
                target,
            )
            for origin in origins
            for target in targets
            if abs(origin.x - target.x) + abs(origin.y - target.y)
            <= maximum_range
        ),
        key=lambda item: (
            item[0],
            item[1].y,
            item[1].x,
            item[2].y,
            item[2].x,
        ),
    )
    first_blocking_cell: Coordinate | None = None
    first_blocking_entity: str | None = None
    for distance, origin, target in candidates:
        blocked = False
        for cell in supercover_line(origin, target)[1:-1]:
            if cell in grid.blocked:
                if first_blocking_cell is None:
                    first_blocking_cell = cell
                blocked = True
                break
            if spatial_index is None:
                continue
            blockers = (
                set(spatial_index.sensory_blockers(room_id, cell, modality))
                - ignored_entity_ids
            )
            if blockers:
                if first_blocking_cell is None:
                    first_blocking_cell = cell
                    first_blocking_entity = min(blockers)
                blocked = True
                break
        if not blocked:
            return SensorySweepResult(
                clear=True,
                distance=distance,
                candidate_count=len(candidates),
            )
    return SensorySweepResult(
        clear=False,
        distance=candidates[0][0] if candidates else None,
        candidate_count=len(candidates),
        blocking_cell=first_blocking_cell,
        blocking_entity_id=first_blocking_entity,
    )


def supercover_line(
    origin: Coordinate,
    target: Coordinate,
) -> tuple[Coordinate, ...]:
    x = origin.x
    y = origin.y
    dx = target.x - origin.x
    dy = target.y - origin.y
    nx = abs(dx)
    ny = abs(dy)
    sign_x = 1 if dx > 0 else -1 if dx < 0 else 0
    sign_y = 1 if dy > 0 else -1 if dy < 0 else 0
    ix = 0
    iy = 0
    cells = [origin]
    while ix < nx or iy < ny:
        decision_x = (1 + 2 * ix) * ny
        decision_y = (1 + 2 * iy) * nx
        if decision_x == decision_y:
            if sign_x:
                cells.append(Coordinate(x + sign_x, y))
            if sign_y:
                cells.append(Coordinate(x, y + sign_y))
            x += sign_x
            y += sign_y
            ix += 1
            iy += 1
        elif decision_x < decision_y:
            x += sign_x
            ix += 1
        else:
            y += sign_y
            iy += 1
        cells.append(Coordinate(x, y))
    return tuple(dict.fromkeys(cells))


def _boundary_cells(
    cells: frozenset[Coordinate],
) -> tuple[Coordinate, ...]:
    if not cells:
        raise ValueError("sensory sweep footprints must not be empty")
    boundary = [
        cell
        for cell in cells
        if any(
            Coordinate(cell.x + dx, cell.y + dy) not in cells
            for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1))
        )
    ]
    return tuple(sorted(boundary or cells, key=lambda cell: (cell.y, cell.x)))
