from dataclasses import replace

from stage0_sim.application.perception import (
    PerceptionConfiguration,
    PerceptionSystem,
)
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.components import (
    CharacterProfileComponent,
    InteractionRequestComponent,
    ObjectDimensions,
    ObjectIntrinsicComponent,
    ObjectSizeClass,
    OpenableComponent,
    OwnershipComponent,
    PerceptionComponent,
    PhysicalObjectIdentityComponent,
    PhysicalPose,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PositionComponent,
    ScentSourceComponent,
    SensesComponent,
    SenseTransmission,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.interactions import InteractionExecutionSystem
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    Coordinate,
    Footprint,
    LocalCoordinateSystem,
    MovementObstruction,
    SpatialScale,
    VisionObstruction,
    WorldGrid,
    WorldLocation,
    WorldMap,
)


def _add_character(
    registry: Registry,
    entity_id: str,
    position: Coordinate,
) -> None:
    registry.create_entity(entity_id)
    registry.add_component(entity_id, PositionComponent(position))
    registry.add_component(
        entity_id,
        SpatialLocationComponent(
            WorldLocation(
                SpatialScale.BUILDING,
                "implicit-building",
                local_coordinate=position,
            )
        ),
    )
    state = PhysicalStateComponent(
        PhysicalPose("implicit-building", position),
        STANDING_CHARACTER_FOOTPRINT,
        MovementObstruction.HARD,
    )
    registry.add_component(entity_id, state)
    registry.get_resource(SpatialIndex).add(
        SpatialIndexEntry(entity_id, state, dynamic=True)
    )
    registry.add_component(
        entity_id,
        CharacterProfileComponent(
            entity_id,
            "human-v1",
            1,
            f"hash-{entity_id}",
            entity_id,
            "",
            {},
        ),
    )
    registry.add_component(entity_id, SensesComponent(vision_range=30))
    registry.add_component(entity_id, PerceptionComponent())


def _add_object(
    registry: Registry,
    entity_id: str,
    position: Coordinate,
    *,
    movement: MovementObstruction = MovementObstruction.NONE,
    vision: VisionObstruction = VisionObstruction.TRANSPARENT,
    hearing: SenseTransmission = SenseTransmission.PASS,
    smell: SenseTransmission = SenseTransmission.PASS,
    footprint: Footprint | None = None,
    indexed: bool = True,
) -> None:
    registry.create_entity(entity_id)
    registry.add_component(
        entity_id,
        PhysicalObjectIdentityComponent(entity_id, entity_id),
    )
    state = PhysicalStateComponent(
        PhysicalPose("implicit-building", position),
        footprint or Footprint(frozenset({Coordinate(0, 0)})),
        movement,
        vision,
        hearing,
        smell,
    )
    registry.add_component(entity_id, state)
    if indexed:
        registry.get_resource(SpatialIndex).add(
            SpatialIndexEntry(entity_id, state)
        )


def _runner(registry: Registry) -> SimulationRunner:
    systems = SystemExecutor()
    systems.add(PerceptionSystem())
    return SimulationRunner(
        RunConfiguration(seed=1, run_id="physical-perception"),
        registry=registry,
        systems=systems,
    )


def _registry() -> Registry:
    registry = Registry()
    registry.set_resource(
        WorldMap(
            WorldGrid(30, 20),
            coordinate_system=LocalCoordinateSystem.MICROCELL,
            microcells_per_legacy_cell=9,
        )
    )
    registry.set_resource(SpatialIndex())
    registry.set_resource(
        PerceptionConfiguration(
            vision_range=30,
            recognition_range=30,
            voice_range=30,
            whisper_range=9,
        )
    )
    return registry


def test_live_opaque_microcells_block_character_line_of_sight() -> None:
    registry = _registry()
    _add_character(registry, "observer", Coordinate(5, 10))
    _add_character(registry, "subject", Coordinate(18, 10))
    _add_object(
        registry,
        "wall",
        Coordinate(11, 10),
        movement=MovementObstruction.HARD,
        vision=VisionObstruction.OPAQUE,
        footprint=Footprint(
            frozenset(Coordinate(0, y) for y in range(-4, 5))
        ),
    )
    runner = _runner(registry)

    runner.run_for(1)

    perception = registry.get_component("observer", PerceptionComponent)
    assert "subject" not in perception.visible_now
    assert "wall" in perception.visible_objects_now


def test_closed_containers_hide_contents_and_public_facts_omit_ownership() -> None:
    registry = _registry()
    _add_character(registry, "observer", Coordinate(5, 10))
    _add_object(registry, "box", Coordinate(9, 10))
    registry.add_component("box", OpenableComponent(is_open=False))
    registry.add_component("box", OwnershipComponent("private-owner"))
    _add_object(registry, "secret", Coordinate(9, 10), indexed=False)
    registry.add_component(
        "secret",
        SpatialParentRelationComponent(
            "box",
            PhysicalRelationKind.IN_CONTAINER,
            "inside",
        ),
    )
    runner = _runner(registry)

    runner.run_for(1)

    perception = registry.get_component("observer", PerceptionComponent)
    assert "box" in perception.visible_objects_now
    assert "secret" not in perception.visible_objects_now
    box_fact = next(
        item.fact
        for item in perception.inbox
        if item.fact.fact_type == "physical_object_seen"
        and item.fact.subject_id == "box"
    )
    assert "owner_id" not in str(box_fact.properties)
    registry.get_component("box", OpenableComponent).is_open = True

    runner.run_for(1)

    assert "secret" in perception.visible_objects_now


def test_committed_interactions_are_observed_as_public_execution_evidence() -> None:
    registry = _registry()
    _add_character(registry, "actor", Coordinate(5, 10))
    _add_character(registry, "observer", Coordinate(5, 16))
    _add_object(
        registry,
        "door",
        Coordinate(8, 10),
        movement=MovementObstruction.HARD,
        vision=VisionObstruction.OPAQUE,
    )
    registry.add_component("door", OpenableComponent())
    registry.add_component(
        "actor",
        InteractionRequestComponent(
            InteractionSpecification(InteractionVerb.OPEN, "door"),
            "test",
        ),
    )
    systems = SystemExecutor()
    systems.add(InteractionExecutionSystem())
    systems.add(PerceptionSystem())
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="interaction-perception"),
        registry=registry,
        systems=systems,
    )

    runner.run_for(1)

    facts = registry.get_component(
        "observer",
        PerceptionComponent,
    ).inbox
    assert any(
        item.fact.fact_type == "physical_interaction_observed"
        and item.fact.object_id == "door"
        and item.fact.properties["verb"] == "OPEN"
        for item in facts
    )


def test_scent_detection_and_loss_respect_structural_smell_blocking() -> None:
    registry = _registry()
    _add_character(registry, "observer", Coordinate(5, 10))
    registry.set_component(
        "observer",
        SensesComponent(vision_range=30, recognition_range=30, hearing_range=30, smell_range=30),
    )
    _add_object(registry, "coffee", Coordinate(18, 10))
    registry.add_component(
        "coffee",
        ScentSourceComponent(
            scent_id="coffee",
            description="fresh coffee",
            emission_range=30,
        ),
    )
    runner = _runner(registry)

    runner.run_for(1)

    perception = registry.get_component("observer", PerceptionComponent)
    assert "coffee" in perception.smelled_objects_now
    assert any(
        item.fact.fact_type == "scent_detected"
        and item.fact.subject_id == "coffee"
        for item in perception.inbox
    )

    _add_object(
        registry,
        "sealed-wall",
        Coordinate(11, 10),
        smell=SenseTransmission.BLOCK,
        footprint=Footprint(
            frozenset(Coordinate(0, y) for y in range(-4, 5))
        ),
    )
    runner.run_for(1)

    assert "coffee" not in perception.smelled_objects_now
    assert any(
        item.fact.fact_type == "scent_lost"
        and item.fact.subject_id == "coffee"
        for item in perception.inbox
    )


def test_recognition_range_gates_semantic_object_properties() -> None:
    registry = _registry()
    _add_character(registry, "observer", Coordinate(5, 10))
    registry.set_component(
        "observer",
        SensesComponent(
            vision_range=30,
            recognition_range=4,
            hearing_range=30,
            smell_range=0,
        ),
    )
    _add_object(registry, "phone", Coordinate(18, 10))
    registry.add_component(
        "phone",
        ObjectIntrinsicComponent(
            mass_kg=0.19,
            dimensions=ObjectDimensions(15, 7.2, 0.8),
            size_class=ObjectSizeClass.SMALL,
        ),
    )
    runner = _runner(registry)

    runner.run_for(1)

    perception = registry.get_component("observer", PerceptionComponent)
    distant = next(
        item.fact
        for item in perception.inbox
        if item.fact.fact_type == "physical_object_seen"
        and item.fact.subject_id == "phone"
    )
    assert "semantic_size" not in distant.properties["public_state"]
    state = registry.get_component("phone", PhysicalStateComponent)
    moved = replace(
        state,
        pose=replace(state.pose, anchor=Coordinate(10, 10)),
    )
    registry.set_component("phone", moved)
    registry.get_resource(SpatialIndex).update(
        SpatialIndexEntry("phone", moved)
    )

    runner.run_for(1)

    changed = next(
        item.fact
        for item in reversed(perception.inbox)
        if item.fact.fact_type == "physical_object_state_changed"
        and item.fact.subject_id == "phone"
    )
    assert changed.properties["public_state"]["semantic_size"] == {
        "dimensions_cm": {
            "length_cm": 15,
            "width_cm": 7.2,
            "height_cm": 0.8,
        },
        "size_class": "SMALL",
    }
    assert "mass_kg" not in changed.properties["public_state"]


def test_structural_hearing_blocker_prevents_speech_delivery() -> None:
    registry = _registry()
    _add_character(registry, "speaker", Coordinate(5, 10))
    _add_character(registry, "observer", Coordinate(18, 10))
    registry.set_component(
        "observer",
        SensesComponent(
            vision_range=30,
            recognition_range=30,
            hearing_range=30,
            smell_range=0,
        ),
    )
    _add_object(
        registry,
        "soundproof-wall",
        Coordinate(11, 10),
        hearing=SenseTransmission.BLOCK,
        footprint=Footprint(
            frozenset(Coordinate(0, y) for y in range(-4, 5))
        ),
    )
    runner = _runner(registry)
    runner.events.emit(
        "speech.started",
        simulation_tick=0,
        simulation_time=0,
        agent_id="speaker",
        payload={"text": "Can you hear me?", "channel": "voice"},
    )

    runner.run_for(1)

    delivered = [
        event
        for event in runner.events.events
        if event.event_type == "speech.delivered"
    ]
    assert delivered[-1].payload["recipient_ids"] == []
    assert not any(
        item.fact.fact_type == "heard_speech"
        for item in registry.get_component(
            "observer",
            PerceptionComponent,
        ).inbox
    )
