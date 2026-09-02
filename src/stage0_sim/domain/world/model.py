from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.economy import TransactionPoint
from stage0_sim.domain.events import JsonValue


@dataclass(frozen=True, order=True, slots=True)
class Coordinate:
    x: int
    y: int

    def to_payload(self) -> dict[str, JsonValue]:
        return {"x": self.x, "y": self.y}


class LocalCoordinateSystem(StrEnum):
    LEGACY_CELL = "legacy_cell"
    MICROCELL = "microcell"


@dataclass(frozen=True, slots=True)
class WorldGrid:
    width: int
    height: int
    blocked: frozenset[Coordinate] = frozenset()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("grid dimensions must be greater than zero")
        outside = [coordinate for coordinate in self.blocked if not self.contains(coordinate)]
        if outside:
            raise ValueError(f"blocked coordinate outside grid: {outside[0]}")

    def contains(self, coordinate: Coordinate) -> bool:
        return 0 <= coordinate.x < self.width and 0 <= coordinate.y < self.height

    def is_walkable(self, coordinate: Coordinate) -> bool:
        return self.contains(coordinate) and coordinate not in self.blocked

    def are_walkable(self, coordinates: frozenset[Coordinate]) -> bool:
        return all(self.is_walkable(coordinate) for coordinate in coordinates)

    def neighbors(self, coordinate: Coordinate) -> tuple[Coordinate, ...]:
        candidates = (
            Coordinate(coordinate.x, coordinate.y - 1),
            Coordinate(coordinate.x - 1, coordinate.y),
            Coordinate(coordinate.x + 1, coordinate.y),
            Coordinate(coordinate.x, coordinate.y + 1),
        )
        return tuple(candidate for candidate in candidates if self.is_walkable(candidate))


@dataclass(frozen=True, slots=True)
class Zone:
    id: str
    name: str
    zone_type: str
    tiles: frozenset[Coordinate]

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.zone_type:
            raise ValueError("zone id, name, and type must not be empty")
        if not self.tiles:
            raise ValueError(f"zone {self.id} must contain at least one tile")


@dataclass(frozen=True, slots=True)
class HomeostasisEffect:
    satiety_delta: float = 0.0
    energy_delta: float = 0.0
    stress_delta: float = 0.0
    satiety_target: float | None = None
    energy_target: float | None = None
    stress_target: float | None = None

    def __post_init__(self) -> None:
        for name, target in (
            ("satiety_target", self.satiety_target),
            ("energy_target", self.energy_target),
            ("stress_target", self.stress_target),
        ):
            if target is not None and not 0 <= target <= 100:
                raise ValueError(f"{name} must be between 0 and 100")

    def final_values(
        self, satiety: float, energy: float, stress: float
    ) -> tuple[float, float, float]:
        return (
            self.satiety_target
            if self.satiety_target is not None
            else _clamp_meter(satiety + self.satiety_delta),
            self.energy_target
            if self.energy_target is not None
            else _clamp_meter(energy + self.energy_delta),
            self.stress_target
            if self.stress_target is not None
            else _clamp_meter(stress + self.stress_delta),
        )


@dataclass(frozen=True, slots=True)
class AffordanceAction:
    action: str
    duration: float
    effect: HomeostasisEffect = HomeostasisEffect()

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("affordance action must not be empty")
        if self.duration <= 0:
            raise ValueError("affordance duration must be greater than zero")


@dataclass(frozen=True, slots=True)
class AffordanceStation:
    id: str
    name: str
    position: Coordinate
    actions: tuple[AffordanceAction, ...]
    available: bool = True
    capacity: int = 1

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("station id and name must not be empty")
        if not self.actions:
            raise ValueError(f"station {self.id} must support at least one action")
        if self.capacity <= 0:
            raise ValueError("station capacity must be greater than zero")
        action_names = [action.action for action in self.actions]
        if len(action_names) != len(set(action_names)):
            raise ValueError(f"station {self.id} action names must be unique")

    @property
    def supported_actions(self) -> tuple[str, ...]:
        return tuple(action.action for action in self.actions)

    def action(self, action_name: str) -> AffordanceAction:
        try:
            return next(action for action in self.actions if action.action == action_name)
        except StopIteration as error:
            raise KeyError(
                f"station {self.id} does not support action {action_name}"
            ) from error


@dataclass(frozen=True, slots=True)
class WorldMap:
    grid: WorldGrid
    zones: tuple[Zone, ...] = ()
    stations: tuple[AffordanceStation, ...] = ()
    transaction_points: tuple[TransactionPoint, ...] = ()
    coordinate_system: LocalCoordinateSystem = LocalCoordinateSystem.LEGACY_CELL
    microcells_per_legacy_cell: int = 1

    def __post_init__(self) -> None:
        if self.coordinate_system is LocalCoordinateSystem.MICROCELL:
            if self.microcells_per_legacy_cell != 9:
                raise ValueError("microcell worlds require a 9x spatial metric")
        elif self.microcells_per_legacy_cell != 1:
            raise ValueError("legacy-cell worlds require a unit spatial metric")
        self._validate_unique_ids("zone", [zone.id for zone in self.zones])
        self._validate_unique_ids("station", [station.id for station in self.stations])
        self._validate_unique_ids(
            "transaction point",
            [point.id for point in self.transaction_points],
        )
        destination_ids = [
            *(zone.id for zone in self.zones),
            *(station.id for station in self.stations),
            *(point.id for point in self.transaction_points),
        ]
        self._validate_unique_ids("world destination", destination_ids)
        for zone in self.zones:
            for tile in zone.tiles:
                if not self.grid.contains(tile):
                    raise ValueError(f"zone {zone.id} has tile outside grid: {tile}")
        for station in self.stations:
            if not self.grid.is_walkable(station.position):
                raise ValueError(
                    f"station {station.id} must be placed on a walkable grid tile"
                )
        for point in self.transaction_points:
            if not self.grid.is_walkable(point.position):
                raise ValueError(
                    f"transaction point {point.id} must be placed on a walkable grid tile"
                )

    def zone_at(self, coordinate: Coordinate) -> Zone | None:
        return next((zone for zone in self.zones if coordinate in zone.tiles), None)

    def local_distance_per_legacy_cell(self) -> int:
        return self.microcells_per_legacy_cell

    def legacy_dimensions(self) -> tuple[int, int]:
        scale = self.microcells_per_legacy_cell
        return self.grid.width // scale, self.grid.height // scale

    def to_legacy_coordinate(self, coordinate: Coordinate) -> Coordinate:
        scale = self.microcells_per_legacy_cell
        return Coordinate(coordinate.x // scale, coordinate.y // scale)

    def legacy_coordinates(
        self,
        coordinates: frozenset[Coordinate],
    ) -> tuple[Coordinate, ...]:
        return tuple(
            sorted(
                {
                    self.to_legacy_coordinate(coordinate)
                    for coordinate in coordinates
                },
                key=lambda item: (item.y, item.x),
            )
        )

    def station(self, station_id: str) -> AffordanceStation:
        try:
            return next(station for station in self.stations if station.id == station_id)
        except StopIteration as error:
            raise KeyError(f"unknown station: {station_id}") from error

    def transaction_point(self, point_id: str) -> TransactionPoint:
        try:
            return next(
                point for point in self.transaction_points if point.id == point_id
            )
        except StopIteration as error:
            raise KeyError(f"unknown transaction point: {point_id}") from error

    @staticmethod
    def _validate_unique_ids(kind: str, identifiers: list[str]) -> None:
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"{kind} IDs must be unique")


def default_affordance_action(action: str) -> AffordanceAction:
    if action == "EAT":
        return AffordanceAction(
            action=action,
            duration=5.0,
            effect=HomeostasisEffect(satiety_delta=60.0),
        )
    if action == "SLEEP":
        return AffordanceAction(
            action=action,
            duration=10.0,
            effect=HomeostasisEffect(energy_target=100.0),
        )
    if action == "RELAX":
        return AffordanceAction(
            action=action,
            duration=10.0,
            effect=HomeostasisEffect(stress_delta=-40.0),
        )
    return AffordanceAction(action=action, duration=1.0)


def _clamp_meter(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 12)
