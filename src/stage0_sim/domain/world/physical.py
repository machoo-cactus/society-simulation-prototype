from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from stage0_sim.domain.world.model import Coordinate


@dataclass(frozen=True, order=True, slots=True)
class LegacyCellCoordinate:
    """A source-authored coordinate measured in legacy room cells."""

    x: int
    y: int

    @classmethod
    def from_coordinate(cls, coordinate: Coordinate) -> LegacyCellCoordinate:
        return cls(coordinate.x, coordinate.y)

    def to_coordinate(self) -> Coordinate:
        return Coordinate(self.x, self.y)


@dataclass(frozen=True, order=True, slots=True)
class MicrocellCoordinate:
    """A runtime-local coordinate measured in physical microcells."""

    x: int
    y: int

    @classmethod
    def from_coordinate(cls, coordinate: Coordinate) -> MicrocellCoordinate:
        return cls(coordinate.x, coordinate.y)

    def to_coordinate(self) -> Coordinate:
        return Coordinate(self.x, self.y)


@dataclass(frozen=True, slots=True)
class SpatialMetric:
    """Exact integer conversion between legacy cells and physical microcells."""

    microcells_per_legacy_cell: int = 9

    def __post_init__(self) -> None:
        if self.microcells_per_legacy_cell != 9:
            raise ValueError("the physical spatial metric requires 9 microcells per cell")

    def scale_legacy_coordinate(self, coordinate: Coordinate) -> Coordinate:
        """Return the lower-left microcell origin of a legacy cell."""
        return Coordinate(
            coordinate.x * self.microcells_per_legacy_cell,
            coordinate.y * self.microcells_per_legacy_cell,
        )

    def center_legacy_coordinate(
        self,
        coordinate: LegacyCellCoordinate | Coordinate,
    ) -> MicrocellCoordinate:
        legacy = (
            coordinate
            if isinstance(coordinate, LegacyCellCoordinate)
            else LegacyCellCoordinate.from_coordinate(coordinate)
        )
        offset = self.microcells_per_legacy_cell // 2
        return MicrocellCoordinate(
            legacy.x * self.microcells_per_legacy_cell + offset,
            legacy.y * self.microcells_per_legacy_cell + offset,
        )

    def scale_legacy_extent(self, value: int) -> int:
        if value < 0:
            raise ValueError("legacy extents must not be negative")
        return value * self.microcells_per_legacy_cell

    def scale_legacy_range(self, value: int) -> int:
        if value < 0:
            raise ValueError("legacy ranges must not be negative")
        return value * self.microcells_per_legacy_cell

    def unscale_exact_coordinate(self, coordinate: Coordinate) -> Coordinate:
        scale = self.microcells_per_legacy_cell
        if coordinate.x % scale or coordinate.y % scale:
            raise ValueError("microcell coordinate is not aligned to a legacy cell")
        return Coordinate(coordinate.x // scale, coordinate.y // scale)

    def legacy_cell_microcells(
        self,
        coordinate: Coordinate,
    ) -> frozenset[Coordinate]:
        origin = self.scale_legacy_coordinate(coordinate)
        scale = self.microcells_per_legacy_cell
        return frozenset(
            Coordinate(origin.x + x, origin.y + y)
            for y in range(scale)
            for x in range(scale)
        )


class CardinalOrientation(StrEnum):
    NORTH = "NORTH"
    EAST = "EAST"
    SOUTH = "SOUTH"
    WEST = "WEST"


@dataclass(frozen=True, slots=True)
class Footprint:
    cells: frozenset[Coordinate]

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError("a footprint must contain at least one cell")

    def rotated(self, orientation: CardinalOrientation) -> Footprint:
        return Footprint(
            frozenset(
                _rotate_coordinate(coordinate, orientation)
                for coordinate in self.cells
            )
        )

    def translated_cells(
        self,
        anchor: Coordinate,
        orientation: CardinalOrientation = CardinalOrientation.NORTH,
    ) -> frozenset[Coordinate]:
        return frozenset(
            Coordinate(anchor.x + cell.x, anchor.y + cell.y)
            for cell in self.rotated(orientation).cells
        )

    def dilated_cells(
        self,
        anchor: Coordinate,
        orientation: CardinalOrientation = CardinalOrientation.NORTH,
        distance: int = 1,
    ) -> frozenset[Coordinate]:
        if distance < 0:
            raise ValueError("dilation distance must not be negative")
        occupied = self.translated_cells(anchor, orientation)
        return frozenset(
            Coordinate(cell.x + dx, cell.y + dy)
            for cell in occupied
            for dy in range(-distance, distance + 1)
            for dx in range(-distance, distance + 1)
        )

    def contact_envelope(
        self,
        anchor: Coordinate,
        orientation: CardinalOrientation = CardinalOrientation.NORTH,
    ) -> frozenset[Coordinate]:
        occupied = self.translated_cells(anchor, orientation)
        return self.dilated_cells(anchor, orientation) - occupied

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return (
            min(cell.x for cell in self.cells),
            min(cell.y for cell in self.cells),
            max(cell.x for cell in self.cells),
            max(cell.y for cell in self.cells),
        )


def _rotate_coordinate(
    coordinate: Coordinate,
    orientation: CardinalOrientation,
) -> Coordinate:
    if orientation is CardinalOrientation.NORTH:
        return coordinate
    if orientation is CardinalOrientation.EAST:
        return Coordinate(-coordinate.y, coordinate.x)
    if orientation is CardinalOrientation.SOUTH:
        return Coordinate(-coordinate.x, -coordinate.y)
    return Coordinate(coordinate.y, -coordinate.x)


@dataclass(frozen=True, slots=True)
class PhysicalPose:
    room_id: str
    anchor: Coordinate
    orientation: CardinalOrientation = CardinalOrientation.NORTH

    def __post_init__(self) -> None:
        if not self.room_id:
            raise ValueError("physical pose room_id must not be empty")


class MovementObstruction(StrEnum):
    NONE = "NONE"
    HARD = "HARD"

    @property
    def blocks_movement(self) -> bool:
        return self is MovementObstruction.HARD


class VisionObstruction(StrEnum):
    TRANSPARENT = "TRANSPARENT"
    OPAQUE = "OPAQUE"

    @property
    def blocks_vision(self) -> bool:
        return self is VisionObstruction.OPAQUE


class SenseTransmission(StrEnum):
    PASS = "PASS"
    BLOCK = "BLOCK"

    @property
    def blocks(self) -> bool:
        return self is SenseTransmission.BLOCK


class SenseModality(StrEnum):
    VISION = "VISION"
    HEARING = "HEARING"
    SMELL = "SMELL"


class PhysicalRelationKind(StrEnum):
    ON_FLOOR = "ON_FLOOR"
    ON_SUPPORT = "ON_SUPPORT"
    IN_CONTAINER = "IN_CONTAINER"
    HELD_BY = "HELD_BY"
    ATTACHED_TO = "ATTACHED_TO"
    OCCUPIES_SLOT = "OCCUPIES_SLOT"


_SLOTTED_RELATIONS = frozenset(
    {
        PhysicalRelationKind.ON_SUPPORT,
        PhysicalRelationKind.IN_CONTAINER,
        PhysicalRelationKind.HELD_BY,
        PhysicalRelationKind.OCCUPIES_SLOT,
    }
)
_OPTIONALLY_SLOTTED_RELATIONS = frozenset({PhysicalRelationKind.ATTACHED_TO})


@dataclass(frozen=True, slots=True)
class SpatialParentRelationComponent:
    parent_id: str
    kind: PhysicalRelationKind
    slot_id: str | None = None

    def __post_init__(self) -> None:
        if not self.parent_id:
            raise ValueError("spatial parent_id must not be empty")
        if self.kind in _SLOTTED_RELATIONS and not self.slot_id:
            raise ValueError(f"{self.kind.value} relations require a slot_id")
        if (
            self.kind not in _SLOTTED_RELATIONS
            and self.kind not in _OPTIONALLY_SLOTTED_RELATIONS
            and self.slot_id is not None
        ):
            raise ValueError(f"{self.kind.value} relations cannot define a slot_id")


def validate_spatial_relation_acyclicity(
    relations: Mapping[str, SpatialParentRelationComponent],
) -> None:
    for entity_id in sorted(relations):
        path: list[str] = []
        current_id = entity_id
        while current_id in relations:
            if current_id in path:
                cycle_start = path.index(current_id)
                cycle = [*path[cycle_start:], current_id]
                raise ValueError(
                    "spatial parent relation cycle: " + " -> ".join(cycle)
                )
            path.append(current_id)
            current_id = relations[current_id].parent_id


@dataclass(frozen=True, slots=True)
class PhysicalObjectIdentityComponent:
    definition_id: str
    name: str

    def __post_init__(self) -> None:
        if not self.definition_id or not self.name:
            raise ValueError("physical object definition_id and name must not be empty")


@dataclass(frozen=True, slots=True)
class PhysicalStateComponent:
    pose: PhysicalPose
    footprint: Footprint
    movement_obstruction: MovementObstruction = MovementObstruction.NONE
    vision_obstruction: VisionObstruction = VisionObstruction.TRANSPARENT
    hearing_transmission: SenseTransmission = SenseTransmission.PASS
    smell_transmission: SenseTransmission = SenseTransmission.PASS

    @property
    def occupied_cells(self) -> frozenset[Coordinate]:
        return self.footprint.translated_cells(
            self.pose.anchor,
            self.pose.orientation,
        )


STANDING_CHARACTER_FOOTPRINT = Footprint(
    frozenset(
        Coordinate(x, y)
        for y in range(-2, 3)
        for x in range(-2, 3)
    )
)


def footprints_touch(
    first: PhysicalStateComponent,
    second: PhysicalStateComponent,
) -> bool:
    if first.pose.room_id != second.pose.room_id:
        return False
    return bool(first.occupied_cells & second.footprint.contact_envelope(
        second.pose.anchor,
        second.pose.orientation,
    ))


@dataclass(frozen=True, slots=True)
class OccupancySlot:
    id: str
    accepted_relations: frozenset[PhysicalRelationKind]
    capacity: int = 1

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("occupancy slot id must not be empty")
        if self.capacity <= 0:
            raise ValueError("occupancy slot capacity must be greater than zero")
        if not self.accepted_relations:
            raise ValueError("occupancy slots must accept at least one relation")
        invalid = self.accepted_relations - _SLOTTED_RELATIONS
        if invalid:
            raise ValueError(
                "occupancy slots cannot accept unslotted relations: "
                f"{sorted(item.value for item in invalid)}"
            )


@dataclass(frozen=True, slots=True)
class OccupancySlotsComponent:
    slots: tuple[OccupancySlot, ...]

    def __post_init__(self) -> None:
        ids = [slot.id for slot in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("occupancy slot IDs must be unique")

    def slot(self, slot_id: str) -> OccupancySlot:
        try:
            return next(slot for slot in self.slots if slot.id == slot_id)
        except StopIteration as error:
            raise KeyError(f"unknown occupancy slot: {slot_id}") from error


@dataclass(frozen=True, slots=True)
class SupportComponent:
    slot_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_slot_ids(self.slot_ids, "support")


@dataclass(frozen=True, slots=True)
class ContainerComponent:
    slot_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_slot_ids(self.slot_ids, "container")


def _validate_slot_ids(slot_ids: tuple[str, ...], label: str) -> None:
    if not slot_ids or any(not slot_id for slot_id in slot_ids):
        raise ValueError(f"{label} slot IDs must not be empty")
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError(f"{label} slot IDs must be unique")


@dataclass(frozen=True, slots=True)
class PortableComponent:
    two_handed: bool = False


@dataclass(frozen=True, slots=True)
class ReadableComponent:
    document_id: str

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("readable document_id must not be empty")


@dataclass(frozen=True, slots=True)
class ConsumableComponent:
    item_id: str
    servings: int = 1

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("consumable item_id must not be empty")
        if self.servings <= 0:
            raise ValueError("consumable servings must be greater than zero")


@dataclass(frozen=True, slots=True)
class UsableComponent:
    use_kind: str

    def __post_init__(self) -> None:
        if not self.use_kind:
            raise ValueError("usable use_kind must not be empty")


@dataclass(slots=True)
class OpenableComponent:
    is_open: bool = False
    is_locked: bool = False
    closed_movement_obstruction: MovementObstruction = MovementObstruction.HARD
    closed_vision_obstruction: VisionObstruction = VisionObstruction.OPAQUE
    closed_hearing_transmission: SenseTransmission = SenseTransmission.PASS
    closed_smell_transmission: SenseTransmission = SenseTransmission.PASS

    def __post_init__(self) -> None:
        if self.is_open and self.is_locked:
            raise ValueError("an open object cannot be locked")


@dataclass(frozen=True, slots=True)
class OwnershipComponent:
    owner_id: str

    def __post_init__(self) -> None:
        if not self.owner_id:
            raise ValueError("owner_id must not be empty")


@dataclass(frozen=True, slots=True)
class CustodyComponent:
    custodian_id: str

    def __post_init__(self) -> None:
        if not self.custodian_id:
            raise ValueError("custodian_id must not be empty")


class CharacterPosture(StrEnum):
    STANDING = "STANDING"
    SITTING = "SITTING"
    LYING = "LYING"


@dataclass(slots=True)
class CharacterHandStateComponent:
    left_hand_object_id: str | None = None
    right_hand_object_id: str | None = None

    @property
    def held_object_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                object_id
                for object_id in (
                    self.left_hand_object_id,
                    self.right_hand_object_id,
                )
                if object_id is not None
            )
        )

    def free_hand_ids(self) -> tuple[str, ...]:
        free: list[str] = []
        if self.left_hand_object_id is None:
            free.append("left")
        if self.right_hand_object_id is None:
            free.append("right")
        return tuple(free)


@dataclass(slots=True)
class CharacterPostureComponent:
    posture: CharacterPosture = CharacterPosture.STANDING
    support_id: str | None = None

    def __post_init__(self) -> None:
        if self.posture is CharacterPosture.STANDING and self.support_id is not None:
            raise ValueError("standing posture cannot define a support_id")


class SpatialCollisionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SpatialIndexEntry:
    entity_id: str
    state: PhysicalStateComponent
    dynamic: bool = False

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("spatial index entity_id must not be empty")


class SpatialIndex:
    """Authoritative, deterministic physical topology index scoped by room."""

    def __init__(self) -> None:
        self._entries: dict[str, SpatialIndexEntry] = {}
        self._hard_cells: dict[tuple[str, Coordinate], set[str]] = {}
        self._opaque_cells: dict[tuple[str, Coordinate], set[str]] = {}
        self._hearing_block_cells: dict[tuple[str, Coordinate], set[str]] = {}
        self._smell_block_cells: dict[tuple[str, Coordinate], set[str]] = {}
        self._revision = 0
        self._topology_revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def topology_revision(self) -> int:
        return self._topology_revision

    def contains(self, entity_id: str) -> bool:
        return entity_id in self._entries

    def entries(self, room_id: str | None = None) -> tuple[SpatialIndexEntry, ...]:
        return tuple(
            self._entries[entity_id]
            for entity_id in sorted(self._entries)
            if room_id is None
            or self._entries[entity_id].state.pose.room_id == room_id
        )

    def entry(self, entity_id: str) -> SpatialIndexEntry:
        try:
            return self._entries[entity_id]
        except KeyError as error:
            raise KeyError(f"physical entity is not indexed: {entity_id}") from error

    def hard_occupants(
        self,
        room_id: str,
        coordinate: Coordinate,
    ) -> tuple[str, ...]:
        return tuple(sorted(self._hard_cells.get((room_id, coordinate), set())))

    def opaque_occupants(
        self,
        room_id: str,
        coordinate: Coordinate,
    ) -> tuple[str, ...]:
        return tuple(sorted(self._opaque_cells.get((room_id, coordinate), set())))

    def sensory_blockers(
        self,
        room_id: str,
        coordinate: Coordinate,
        modality: SenseModality,
    ) -> tuple[str, ...]:
        index = (
            self._opaque_cells
            if modality is SenseModality.VISION
            else self._hearing_block_cells
            if modality is SenseModality.HEARING
            else self._smell_block_cells
        )
        return tuple(sorted(index.get((room_id, coordinate), set())))

    def add(
        self,
        entry: SpatialIndexEntry,
        *,
        authorized_overlaps: frozenset[str] = frozenset(),
    ) -> None:
        if entry.entity_id in self._entries:
            raise ValueError(f"physical entity is already indexed: {entry.entity_id}")
        self._reject_collisions(entry, authorized_overlaps)
        self._entries[entry.entity_id] = entry
        self._add_topology(entry)
        if any(self._topology_cells(entry)):
            self._revision += 1
            if not entry.dynamic:
                self._topology_revision += 1

    def update(
        self,
        entry: SpatialIndexEntry,
        *,
        authorized_overlaps: frozenset[str] = frozenset(),
    ) -> None:
        previous = self.entry(entry.entity_id)
        self._reject_collisions(entry, authorized_overlaps, excluding=entry.entity_id)
        previous_topology = self._topology_cells(previous)
        next_topology = self._topology_cells(entry)
        self._remove_topology(previous)
        self._entries[entry.entity_id] = entry
        self._add_topology(entry)
        if previous_topology != next_topology:
            self._revision += 1
            if not previous.dynamic or not entry.dynamic:
                self._topology_revision += 1

    def remove(self, entity_id: str) -> SpatialIndexEntry:
        entry = self.entry(entity_id)
        del self._entries[entity_id]
        self._remove_topology(entry)
        if any(self._topology_cells(entry)):
            self._revision += 1
            if not entry.dynamic:
                self._topology_revision += 1
        return entry

    def blocking_entities(
        self,
        state: PhysicalStateComponent,
        *,
        excluding: str | None = None,
        authorized_overlaps: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        collisions: set[str] = set()
        for cell in state.occupied_cells:
            collisions.update(
                self._hard_cells.get((state.pose.room_id, cell), set())
            )
        if excluding is not None:
            collisions.discard(excluding)
        return tuple(sorted(collisions - authorized_overlaps))

    def can_place(
        self,
        state: PhysicalStateComponent,
        *,
        excluding: str | None = None,
        authorized_overlaps: frozenset[str] = frozenset(),
    ) -> bool:
        return not self.blocking_entities(
            state,
            excluding=excluding,
            authorized_overlaps=authorized_overlaps,
        )

    def _reject_collisions(
        self,
        entry: SpatialIndexEntry,
        authorized_overlaps: frozenset[str],
        *,
        excluding: str | None = None,
    ) -> None:
        state = entry.state
        if not state.movement_obstruction.blocks_movement:
            return
        blockers = self.blocking_entities(
            state,
            excluding=excluding,
            authorized_overlaps=authorized_overlaps,
        )
        if blockers:
            other_id = blockers[0]
            raise SpatialCollisionError(
                f"physical entity {entry.entity_id} collides with {other_id}"
            )

    def _add_topology(self, entry: SpatialIndexEntry) -> None:
        hard, opaque, hearing, smell = self._topology_cells(entry)
        room_id = entry.state.pose.room_id
        for cell in hard:
            self._hard_cells.setdefault((room_id, cell), set()).add(entry.entity_id)
        for cell in opaque:
            self._opaque_cells.setdefault((room_id, cell), set()).add(entry.entity_id)
        for cell in hearing:
            self._hearing_block_cells.setdefault((room_id, cell), set()).add(
                entry.entity_id
            )
        for cell in smell:
            self._smell_block_cells.setdefault((room_id, cell), set()).add(
                entry.entity_id
            )

    def _remove_topology(self, entry: SpatialIndexEntry) -> None:
        hard, opaque, hearing, smell = self._topology_cells(entry)
        room_id = entry.state.pose.room_id
        for cell in hard:
            _remove_indexed_entity(
                self._hard_cells,
                (room_id, cell),
                entry.entity_id,
            )
        for cell in opaque:
            _remove_indexed_entity(
                self._opaque_cells,
                (room_id, cell),
                entry.entity_id,
            )
        for cell in hearing:
            _remove_indexed_entity(
                self._hearing_block_cells,
                (room_id, cell),
                entry.entity_id,
            )
        for cell in smell:
            _remove_indexed_entity(
                self._smell_block_cells,
                (room_id, cell),
                entry.entity_id,
            )

    @staticmethod
    def _topology_cells(
        entry: SpatialIndexEntry,
    ) -> tuple[
        frozenset[Coordinate],
        frozenset[Coordinate],
        frozenset[Coordinate],
        frozenset[Coordinate],
    ]:
        cells = entry.state.occupied_cells
        hard = (
            cells
            if entry.state.movement_obstruction.blocks_movement
            else frozenset()
        )
        opaque = (
            cells
            if entry.state.vision_obstruction.blocks_vision
            else frozenset()
        )
        hearing = (
            cells if entry.state.hearing_transmission.blocks else frozenset()
        )
        smell = cells if entry.state.smell_transmission.blocks else frozenset()
        return hard, opaque, hearing, smell


def _remove_indexed_entity(
    index: dict[tuple[str, Coordinate], set[str]],
    key: tuple[str, Coordinate],
    entity_id: str,
) -> None:
    occupants = index[key]
    occupants.remove(entity_id)
    if not occupants:
        del index[key]


@dataclass(frozen=True, slots=True)
class PhysicalInteractionTarget:
    target_id: str
    room_id: str
    approach_anchors: tuple[Coordinate, ...] = ()
    occupancy_anchors: Mapping[str, tuple[Coordinate, ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not self.target_id or not self.room_id:
            raise ValueError("physical interaction target identity must not be empty")


@dataclass(slots=True)
class PhysicalInteractionRegistry:
    targets: dict[str, PhysicalInteractionTarget]
    transition_doors: dict[str, str]
    transaction_staff_anchors: dict[str, tuple[Coordinate, ...]] = field(
        default_factory=dict
    )

    def target(self, target_id: str) -> PhysicalInteractionTarget:
        try:
            return self.targets[target_id]
        except KeyError as error:
            raise KeyError(f"unknown physical interaction target: {target_id}") from error

    def approach_anchors(self, target_id: str) -> tuple[Coordinate, ...]:
        target = self.targets.get(target_id)
        return target.approach_anchors if target is not None else ()

    def occupancy_anchors(
        self,
        target_id: str,
        slot_id: str,
    ) -> tuple[Coordinate, ...]:
        target = self.targets.get(target_id)
        if target is None:
            return ()
        return target.occupancy_anchors.get(slot_id, ())

    def door_for_transition(self, transition_id: str) -> str | None:
        return self.transition_doors.get(transition_id.removesuffix(":reverse"))

    def transaction_staff_positions(
        self,
        target_id: str,
    ) -> tuple[Coordinate, ...]:
        return self.transaction_staff_anchors.get(target_id, ())
