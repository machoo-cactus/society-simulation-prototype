import pytest

from stage0_sim.domain.components import (
    CardinalOrientation,
    Footprint,
    MovementObstruction,
    OccupancySlot,
    PhysicalPose,
    PhysicalRelationKind,
    PhysicalStateComponent,
    SenseModality,
    SenseTransmission,
    SpatialCollisionError,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialMetric,
    SpatialParentRelationComponent,
    VisionObstruction,
    validate_spatial_relation_acyclicity,
)
from stage0_sim.domain.perception import sensory_sweep, supercover_line
from stage0_sim.domain.world import Coordinate, WorldGrid


def test_spatial_metric_scales_legacy_cells_exactly() -> None:
    metric = SpatialMetric()

    assert metric.scale_legacy_coordinate(Coordinate(2, 3)) == Coordinate(18, 27)
    assert metric.scale_legacy_extent(4) == 36
    assert metric.unscale_exact_coordinate(Coordinate(18, 27)) == Coordinate(2, 3)
    assert len(metric.legacy_cell_microcells(Coordinate(1, 1))) == 81

    with pytest.raises(ValueError, match="not aligned"):
        metric.unscale_exact_coordinate(Coordinate(10, 9))


def test_footprint_rotation_translation_and_contact_envelope() -> None:
    footprint = Footprint(
        frozenset({Coordinate(0, 0), Coordinate(1, 0), Coordinate(1, 1)})
    )

    assert footprint.rotated(CardinalOrientation.EAST).cells == frozenset(
        {Coordinate(0, 0), Coordinate(0, 1), Coordinate(-1, 1)}
    )
    occupied = footprint.translated_cells(
        Coordinate(5, 5),
        CardinalOrientation.EAST,
    )
    assert occupied == frozenset(
        {Coordinate(5, 5), Coordinate(5, 6), Coordinate(4, 6)}
    )
    envelope = footprint.contact_envelope(
        Coordinate(5, 5),
        CardinalOrientation.EAST,
    )
    assert occupied.isdisjoint(envelope)
    assert Coordinate(3, 5) in envelope
    assert footprint.dilated_cells(
        Coordinate(5, 5),
        CardinalOrientation.EAST,
    ) == occupied | envelope


def test_relation_and_slot_invariants_are_explicit() -> None:
    with pytest.raises(ValueError, match="require a slot_id"):
        SpatialParentRelationComponent(
            parent_id="table",
            kind=PhysicalRelationKind.ON_SUPPORT,
        )
    with pytest.raises(ValueError, match="cannot define a slot_id"):
        SpatialParentRelationComponent(
            parent_id="room",
            kind=PhysicalRelationKind.ON_FLOOR,
            slot_id="top",
        )
    with pytest.raises(ValueError, match="unslotted"):
        OccupancySlot(
            id="invalid",
            accepted_relations=frozenset({PhysicalRelationKind.ON_FLOOR}),
        )
    with pytest.raises(ValueError, match="relation cycle"):
        validate_spatial_relation_acyclicity(
            {
                "a": SpatialParentRelationComponent(
                    parent_id="b",
                    kind=PhysicalRelationKind.ATTACHED_TO,
                ),
                "b": SpatialParentRelationComponent(
                    parent_id="a",
                    kind=PhysicalRelationKind.ATTACHED_TO,
                ),
            }
        )


def test_spatial_index_rejects_collisions_and_tracks_topology_revision() -> None:
    index = SpatialIndex()
    footprint = Footprint(frozenset({Coordinate(0, 0)}))
    index.add(
        SpatialIndexEntry(
            "rug",
            PhysicalStateComponent(
                pose=PhysicalPose("room", Coordinate(4, 4)),
                footprint=footprint,
            ),
        )
    )
    assert index.revision == 0
    first = SpatialIndexEntry(
        "table",
        PhysicalStateComponent(
            pose=PhysicalPose("room", Coordinate(4, 4)),
            footprint=footprint,
            movement_obstruction=MovementObstruction.HARD,
            vision_obstruction=VisionObstruction.OPAQUE,
        ),
    )
    index.add(first)

    assert index.revision == 1
    assert index.hard_occupants("room", Coordinate(4, 4)) == ("table",)
    assert index.opaque_occupants("room", Coordinate(4, 4)) == ("table",)

    overlapping = SpatialIndexEntry(
        "lamp",
        PhysicalStateComponent(
            pose=PhysicalPose("room", Coordinate(4, 4)),
            footprint=footprint,
            movement_obstruction=MovementObstruction.HARD,
        ),
    )
    with pytest.raises(SpatialCollisionError, match="collides with table"):
        index.add(overlapping)

    index.add(overlapping, authorized_overlaps=frozenset({"table"}))
    assert index.revision == 2
    assert index.hard_occupants("room", Coordinate(4, 4)) == ("lamp", "table")

    moved = SpatialIndexEntry(
        "lamp",
        PhysicalStateComponent(
            pose=PhysicalPose("room", Coordinate(5, 4)),
            footprint=footprint,
            movement_obstruction=MovementObstruction.HARD,
        ),
    )
    index.update(moved)
    assert index.revision == 3
    assert index.hard_occupants("room", Coordinate(4, 4)) == ("table",)
    assert index.hard_occupants("room", Coordinate(5, 4)) == ("lamp",)

    index.update(moved)
    assert index.revision == 3
    assert index.remove("table") == first
    assert index.revision == 4


def test_supercover_line_includes_corner_touching_cells() -> None:
    line = supercover_line(Coordinate(0, 0), Coordinate(2, 2))

    assert Coordinate(1, 0) in line
    assert Coordinate(0, 1) in line
    assert Coordinate(1, 1) in line
    assert line[-1] == Coordinate(2, 2)


def test_sensory_sweep_uses_independent_structural_modalities() -> None:
    grid = WorldGrid(10, 10)
    index = SpatialIndex()
    window = PhysicalStateComponent(
        pose=PhysicalPose("room", Coordinate(4, 5)),
        footprint=Footprint(frozenset({Coordinate(0, 0)})),
        movement_obstruction=MovementObstruction.HARD,
        vision_obstruction=VisionObstruction.TRANSPARENT,
        hearing_transmission=SenseTransmission.BLOCK,
        smell_transmission=SenseTransmission.BLOCK,
    )
    index.add(SpatialIndexEntry("window", window))
    arguments = {
        "grid": grid,
        "room_id": "room",
        "origin_cells": frozenset({Coordinate(1, 5)}),
        "target_cells": frozenset({Coordinate(8, 5)}),
        "maximum_range": 10,
        "spatial_index": index,
    }

    assert sensory_sweep(
        modality=SenseModality.VISION,
        **arguments,
    ).clear
    hearing = sensory_sweep(
        modality=SenseModality.HEARING,
        **arguments,
    )
    smell = sensory_sweep(
        modality=SenseModality.SMELL,
        **arguments,
    )

    assert not hearing.clear
    assert hearing.blocking_entity_id == "window"
    assert not smell.clear


def test_sensory_sweep_accepts_partial_footprint_exposure() -> None:
    grid = WorldGrid(10, 10)
    index = SpatialIndex()
    blocker = PhysicalStateComponent(
        pose=PhysicalPose("room", Coordinate(4, 4)),
        footprint=Footprint(frozenset({Coordinate(0, 0)})),
        vision_obstruction=VisionObstruction.OPAQUE,
    )
    index.add(SpatialIndexEntry("post", blocker))

    result = sensory_sweep(
        grid,
        room_id="room",
        origin_cells=frozenset({Coordinate(1, 4), Coordinate(1, 5)}),
        target_cells=frozenset({Coordinate(8, 4), Coordinate(8, 5)}),
        maximum_range=10,
        modality=SenseModality.VISION,
        spatial_index=index,
    )

    assert result.clear
    assert result.candidate_count == 4
