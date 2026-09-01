import heapq
from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.economy import TransactionPoint
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.world.model import (
    AffordanceStation,
    Coordinate,
    WorldMap,
)


class SpatialScale(StrEnum):
    BUILDING = "BUILDING"
    NEIGHBORHOOD = "NEIGHBORHOOD"
    CITY = "CITY"


class TravelMode(StrEnum):
    WALK = "WALK"
    CYCLE = "CYCLE"
    CAR = "CAR"
    METRO = "METRO"


class TravelStatus(StrEnum):
    IDLE = "IDLE"
    ROUTE_PLANNED = "ROUTE_PLANNED"
    TRAVELLING = "TRAVELLING"
    ARRIVED = "ARRIVED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, order=True, slots=True)
class MapPoint:
    x: float
    y: float

    def to_payload(self) -> dict[str, JsonValue]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True, slots=True)
class CityBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True, slots=True)
class District:
    id: str
    name: str
    center: MapPoint


@dataclass(frozen=True, slots=True)
class CityZone:
    id: str
    name: str
    center: MapPoint


@dataclass(frozen=True, slots=True)
class BuildingEntrance:
    id: str
    room_id: str
    local_coordinate: Coordinate
    network_node_id: str


@dataclass(frozen=True, slots=True)
class Building:
    id: str
    name: str
    district_id: str
    city_position: MapPoint
    room_ids: tuple[str, ...]
    entrances: tuple[BuildingEntrance, ...]


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    key: str
    name: str
    room_type: str
    building_id: str
    offset: Coordinate
    world: WorldMap


@dataclass(frozen=True, slots=True)
class BuildingPortal:
    id: str
    building_id: str
    from_room_id: str
    from_coordinate: Coordinate
    to_room_id: str
    to_coordinate: Coordinate
    bidirectional: bool = True
    available: bool = True


@dataclass(frozen=True, slots=True)
class WorldObject:
    id: str
    name: str
    object_kind: str
    building_id: str
    room_id: str
    position: Coordinate
    station: AffordanceStation | None = None
    transaction_point: TransactionPoint | None = None


@dataclass(frozen=True, slots=True)
class OutdoorPlace:
    id: str
    name: str
    district_id: str
    city_position: MapPoint
    network_node_id: str


@dataclass(frozen=True, slots=True)
class TransportNode:
    id: str
    kind: str
    position: MapPoint
    place_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransportEdge:
    id: str
    from_node_id: str
    to_node_id: str
    allowed_modes: frozenset[TravelMode]
    distance_meters: float
    geometry: tuple[MapPoint, ...]
    speed_limit_mps: float | None = None
    bidirectional: bool = False

    def travel_seconds(
        self,
        requested_mode: TravelMode,
        speeds: dict[TravelMode, float],
    ) -> float:
        mode = edge_mode(self, requested_mode)
        speed = speeds[mode]
        if mode is TravelMode.CAR and self.speed_limit_mps is not None:
            speed = min(speed, self.speed_limit_mps)
        return self.distance_meters / speed


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: str
    vehicle_type: TravelMode
    name: str
    capacity: int
    network_node_id: str


@dataclass(slots=True)
class VehicleState:
    network_node_id: str | None
    edge_id: str | None = None
    edge_progress: float | None = None
    driver_id: str | None = None


@dataclass(slots=True)
class VehicleRegistry:
    states: dict[str, VehicleState]


@dataclass(frozen=True, slots=True)
class WorldLocation:
    scale: SpatialScale
    place_id: str
    local_coordinate: Coordinate | None = None
    network_node_id: str | None = None
    edge_id: str | None = None
    edge_progress: float | None = None

    def __post_init__(self) -> None:
        if not self.place_id:
            raise ValueError("world location place_id must not be empty")
        if self.local_coordinate is not None:
            if self.scale is not SpatialScale.BUILDING:
                raise ValueError(
                    "room coordinates require BUILDING compatibility scale"
                )
            if (
                self.network_node_id is not None
                or self.edge_id is not None
                or self.edge_progress is not None
            ):
                raise ValueError(
                    "room locations cannot include network position fields"
                )
            return
        if self.scale is SpatialScale.BUILDING:
            raise ValueError(
                "BUILDING compatibility scale requires a room coordinate"
            )
        if (self.edge_id is None) != (self.edge_progress is None):
            raise ValueError("edge_id and edge_progress must be provided together")
        if self.network_node_id is not None and self.edge_id is not None:
            raise ValueError("location cannot be both on a node and edge")


@dataclass(frozen=True, slots=True)
class TravelLeg:
    edge_id: str
    from_node_id: str
    to_node_id: str
    mode: TravelMode
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CityWorld:
    id: str
    name: str
    bounds: CityBounds
    districts: tuple[District, ...]
    buildings: tuple[Building, ...]
    outdoor_places: tuple[OutdoorPlace, ...]
    nodes: tuple[TransportNode, ...]
    edges: tuple[TransportEdge, ...]
    city_zones: tuple[CityZone, ...] = ()
    rooms: tuple[Room, ...] = ()
    portals: tuple[BuildingPortal, ...] = ()
    objects: tuple[WorldObject, ...] = ()
    vehicles: tuple[Vehicle, ...] = ()
    walking_speed_mps: float = 1.4
    cycling_speed_mps: float = 4.5
    car_speed_mps: float = 13.9
    metro_speed_mps: float = 16.0

    def __post_init__(self) -> None:
        building_by_id = {item.id: item for item in self.buildings}
        room_by_id = {item.id: item for item in self.rooms}
        if len(building_by_id) != len(self.buildings):
            raise ValueError("building IDs must be unique")
        if len(room_by_id) != len(self.rooms):
            raise ValueError("room IDs must be unique")
        identifiers = [
            self.id,
            *(item.id for item in self.city_zones),
            *building_by_id,
            *room_by_id,
            *(item.id for item in self.portals),
            *(item.id for item in self.objects),
            *(
                entrance.id
                for building in self.buildings
                for entrance in building.entrances
            ),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("city hierarchy IDs must be globally unique")
        for building in self.buildings:
            expected_room_ids = {
                room.id
                for room in self.rooms
                if room.building_id == building.id
            }
            if set(building.room_ids) != expected_room_ids:
                raise ValueError(
                    f"building {building.id} room IDs are invalid"
                )
            for entrance in building.entrances:
                room = room_by_id.get(entrance.room_id)
                if room is None or room.building_id != building.id:
                    raise ValueError(
                        f"entrance {entrance.id} references invalid room"
                    )
                if not room.world.grid.is_walkable(
                    entrance.local_coordinate
                ):
                    raise ValueError(
                        f"entrance {entrance.id} must be on a walkable tile"
                    )
                self.node(entrance.network_node_id)
        endpoint_pairs: set[
            tuple[str, Coordinate, str, Coordinate]
        ] = set()
        for portal in self.portals:
            from_room = room_by_id.get(portal.from_room_id)
            to_room = room_by_id.get(portal.to_room_id)
            if (
                from_room is None
                or to_room is None
                or from_room.building_id != portal.building_id
                or to_room.building_id != portal.building_id
                or portal.from_room_id == portal.to_room_id
            ):
                raise ValueError(
                    f"portal {portal.id} references invalid rooms"
                )
            if not from_room.world.grid.is_walkable(
                portal.from_coordinate
            ) or not to_room.world.grid.is_walkable(
                portal.to_coordinate
            ):
                raise ValueError(
                    f"portal {portal.id} endpoints must be walkable"
                )
            endpoint = (
                portal.from_room_id,
                portal.from_coordinate,
                portal.to_room_id,
                portal.to_coordinate,
            )
            reverse = (
                portal.to_room_id,
                portal.to_coordinate,
                portal.from_room_id,
                portal.from_coordinate,
            )
            if endpoint in endpoint_pairs or reverse in endpoint_pairs:
                raise ValueError(
                    f"portal {portal.id} duplicates another endpoint"
                )
            endpoint_pairs.add(endpoint)
        for item in self.objects:
            room = room_by_id.get(item.room_id)
            if (
                room is None
                or room.building_id != item.building_id
                or not room.world.grid.is_walkable(item.position)
            ):
                raise ValueError(
                    f"world object {item.id} references invalid room placement"
                )
            if (
                item.object_kind == "affordance"
                and item.station is None
            ) or (
                item.object_kind == "transaction"
                and item.transaction_point is None
            ):
                raise ValueError(
                    f"world object {item.id} lacks runtime details"
                )

    def building(self, building_id: str) -> Building:
        try:
            return next(item for item in self.buildings if item.id == building_id)
        except StopIteration as error:
            raise KeyError(f"unknown building: {building_id}") from error

    def node(self, node_id: str) -> TransportNode:
        try:
            return next(item for item in self.nodes if item.id == node_id)
        except StopIteration as error:
            raise KeyError(f"unknown transport node: {node_id}") from error

    def city_zone(self, city_zone_id: str) -> CityZone:
        try:
            return next(
                item for item in self.city_zones if item.id == city_zone_id
            )
        except StopIteration as error:
            raise KeyError(f"unknown city zone: {city_zone_id}") from error

    def room(self, room_id: str) -> Room:
        try:
            return next(item for item in self.rooms if item.id == room_id)
        except StopIteration as error:
            raise KeyError(f"unknown room: {room_id}") from error

    def room_world(self, room_id: str) -> WorldMap:
        return self.room(room_id).world

    def building_for_room(self, room_id: str) -> Building:
        return self.building(self.room(room_id).building_id)

    def portal(self, portal_id: str) -> BuildingPortal:
        base_id = portal_id.removesuffix(":reverse")
        try:
            return next(item for item in self.portals if item.id == base_id)
        except StopIteration as error:
            raise KeyError(f"unknown building portal: {portal_id}") from error

    def world_object(self, object_id: str) -> WorldObject:
        try:
            return next(item for item in self.objects if item.id == object_id)
        except StopIteration as error:
            raise KeyError(f"unknown world object: {object_id}") from error

    def outdoor_place(self, place_id: str) -> OutdoorPlace:
        try:
            return next(
                item for item in self.outdoor_places if item.id == place_id
            )
        except StopIteration as error:
            raise KeyError(f"unknown outdoor place: {place_id}") from error

    def edge(self, edge_id: str) -> TransportEdge:
        try:
            return next(item for item in self.edges if item.id == edge_id)
        except StopIteration as error:
            raise KeyError(f"unknown transport edge: {edge_id}") from error

    @property
    def speeds(self) -> dict[TravelMode, float]:
        return {
            TravelMode.WALK: self.walking_speed_mps,
            TravelMode.CYCLE: self.cycling_speed_mps,
            TravelMode.CAR: self.car_speed_mps,
            TravelMode.METRO: self.metro_speed_mps,
        }


def edge_mode(edge: TransportEdge, requested_mode: TravelMode) -> TravelMode:
    if requested_mode in edge.allowed_modes:
        return requested_mode
    if TravelMode.WALK in edge.allowed_modes:
        return TravelMode.WALK
    raise ValueError(f"edge {edge.id} does not support {requested_mode}")


def find_transport_route(
    city: CityWorld,
    origin_node_id: str,
    destination_node_id: str,
    requested_mode: TravelMode,
    *,
    allowed_edge_ids: frozenset[str] | None = None,
) -> tuple[TravelLeg, ...] | None:
    if origin_node_id == destination_node_id:
        return ()
    edges_from: dict[str, list[tuple[str, TransportEdge]]] = {}
    for edge in city.edges:
        if allowed_edge_ids is not None and edge.id not in allowed_edge_ids:
            continue
        try:
            edge_mode(edge, requested_mode)
        except ValueError:
            continue
        edges_from.setdefault(edge.from_node_id, []).append((edge.to_node_id, edge))
        if edge.bidirectional:
            edges_from.setdefault(edge.to_node_id, []).append(
                (edge.from_node_id, edge)
            )
    frontier: list[tuple[float, int, tuple[str, ...], str]] = [
        (0.0, 0, (), origin_node_id)
    ]
    best: dict[str, tuple[float, int, tuple[str, ...]]] = {
        origin_node_id: (0.0, 0, ())
    }
    previous: dict[str, tuple[str, TransportEdge]] = {}
    while frontier:
        cost, transfers, edge_ids, node_id = heapq.heappop(frontier)
        if best.get(node_id) != (cost, transfers, edge_ids):
            continue
        if node_id == destination_node_id:
            return _reconstruct_transport_route(
                city,
                previous,
                origin_node_id,
                destination_node_id,
                requested_mode,
            )
        candidates = sorted(
            edges_from.get(node_id, ()),
            key=lambda item: (item[1].id, item[0]),
        )
        for next_node, edge in candidates:
            mode = edge_mode(edge, requested_mode)
            seconds = edge.travel_seconds(requested_mode, city.speeds)
            next_cost = round(cost + seconds, 12)
            next_transfers = transfers + int(mode is not requested_mode)
            next_edge_ids = (*edge_ids, edge.id)
            score = (next_cost, next_transfers, next_edge_ids)
            if score >= best.get(next_node, (float("inf"), 10**9, ())):
                continue
            best[next_node] = score
            previous[next_node] = (node_id, edge)
            heapq.heappush(
                frontier,
                (next_cost, next_transfers, next_edge_ids, next_node),
            )
    return None


def _reconstruct_transport_route(
    city: CityWorld,
    previous: dict[str, tuple[str, TransportEdge]],
    origin: str,
    destination: str,
    requested_mode: TravelMode,
) -> tuple[TravelLeg, ...]:
    node_id = destination
    reversed_legs: list[TravelLeg] = []
    while node_id != origin:
        prior_node, edge = previous[node_id]
        reversed_legs.append(
            TravelLeg(
                edge_id=edge.id,
                from_node_id=prior_node,
                to_node_id=node_id,
                mode=edge_mode(edge, requested_mode),
                duration_seconds=edge.travel_seconds(
                    requested_mode, city.speeds
                ),
            )
        )
        node_id = prior_node
    return tuple(reversed(reversed_legs))
