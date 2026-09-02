from dataclasses import replace

import pytest

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
    ModelToolCall,
    ObservedTarget,
)
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.components import (
    ActionOrigin,
    ActionType,
    ActivityComponent,
    CharacterEmbodimentComponent,
    CharacterHandStateComponent,
    CharacterPosture,
    CharacterPostureComponent,
    ConsumableComponent,
    ContainerComponent,
    DriveComponent,
    EffectiveSensesComponent,
    EffectOperation,
    EquipmentSlot,
    EquipmentStateComponent,
    InteractionRequestComponent,
    MovementComponent,
    ObjectEffect,
    ObjectIntrinsicComponent,
    OccupancySlot,
    OccupancySlotsComponent,
    OpenableComponent,
    PhysicalInteractionRegistry,
    PhysicalInteractionTarget,
    PhysicalObjectIdentityComponent,
    PhysicalPose,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PlanAction,
    PlanComponent,
    PortableComponent,
    PositionComponent,
    ReadableComponent,
    SenseEffectTarget,
    SensesComponent,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    SupportComponent,
    UsableComponent,
    WearableComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from stage0_sim.domain.lineage import queue_plan_actions
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.effects import (
    CharacterEffectResolutionSystem,
    resolve_character_effects,
)
from stage0_sim.domain.systems.interactions import InteractionExecutionSystem
from stage0_sim.domain.systems.plans import (
    PlanExecutionSystem,
    TimedPlanActionSystem,
)
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    Coordinate,
    Footprint,
    LocalCoordinateSystem,
    MovementObstruction,
    SpatialScale,
    WorldGrid,
    WorldLocation,
    WorldMap,
)


def _registry() -> Registry:
    registry = Registry()
    registry.set_resource(
        WorldMap(
            WorldGrid(40, 30),
            coordinate_system=LocalCoordinateSystem.MICROCELL,
            microcells_per_legacy_cell=9,
        )
    )
    registry.set_resource(SpatialIndex())
    registry.set_resource(PhysicalInteractionRegistry({}, {}))
    _add_actor(registry, "actor", Coordinate(5, 10))
    return registry


def _add_actor(
    registry: Registry,
    actor_id: str,
    position: Coordinate,
) -> None:
    registry.create_entity(actor_id)
    registry.add_component(actor_id, PositionComponent(position))
    registry.add_component(
        actor_id,
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
    registry.add_component(actor_id, state)
    registry.get_resource(SpatialIndex).add(
        SpatialIndexEntry(actor_id, state, dynamic=True)
    )
    registry.add_component(actor_id, CharacterHandStateComponent())
    registry.add_component(actor_id, CharacterPostureComponent())
    registry.add_component(actor_id, MovementComponent())
    registry.add_component(actor_id, ActivityComponent())
    registry.add_component(actor_id, DriveComponent())
    registry.add_component(actor_id, PlanComponent())


def _add_object(
    registry: Registry,
    object_id: str,
    position: Coordinate,
    *,
    obstruction: MovementObstruction = MovementObstruction.NONE,
    indexed: bool = True,
) -> None:
    registry.create_entity(object_id)
    state = PhysicalStateComponent(
        PhysicalPose("implicit-building", position),
        Footprint(frozenset({Coordinate(0, 0)})),
        obstruction,
    )
    registry.add_component(
        object_id,
        PhysicalObjectIdentityComponent(object_id, object_id),
    )
    registry.add_component(object_id, state)
    registry.add_component(
        object_id,
        SpatialParentRelationComponent(
            "implicit-building",
            PhysicalRelationKind.ON_FLOOR,
        ),
    )
    if indexed:
        registry.get_resource(SpatialIndex).add(
            SpatialIndexEntry(object_id, state)
        )


def _run_interaction(
    registry: Registry,
    specification: InteractionSpecification,
    *,
    actor_id: str = "actor",
) -> SimulationRunner:
    registry.add_component(
        actor_id,
        InteractionRequestComponent(specification, "test"),
    )
    systems = SystemExecutor()
    systems.add(InteractionExecutionSystem())
    systems.add(CharacterEffectResolutionSystem())
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="interaction-test"),
        registry=registry,
        systems=systems,
    )
    runner.run_for(1)
    return runner


def test_pick_up_and_put_down_update_hands_custody_and_spatial_index() -> None:
    registry = _registry()
    _add_object(registry, "book", Coordinate(8, 10))
    registry.add_component("book", PortableComponent())

    pickup = _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "book"),
    )

    hands = registry.get_component("actor", CharacterHandStateComponent)
    assert hands.left_hand_object_id == "book"
    assert not registry.get_resource(SpatialIndex).contains("book")
    relation = registry.get_component("book", SpatialParentRelationComponent)
    assert relation.kind is PhysicalRelationKind.HELD_BY
    assert [
        event.event_type
        for event in pickup.events.events
        if event.event_type.startswith("interaction.")
    ] == [
        "interaction.requested",
        "interaction.started",
        "interaction.completed",
    ]

    registry.remove_component("actor", InteractionRequestComponent)
    putdown = _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PUT_DOWN, "book"),
    )

    assert hands.held_object_ids == ()
    assert registry.get_resource(SpatialIndex).contains("book")
    assert (
        registry.get_component(
            "book",
            SpatialParentRelationComponent,
        ).kind
        is PhysicalRelationKind.ON_FLOOR
    )
    assert any(
        event.event_type == "interaction.completed"
        for event in putdown.events.events
    )


def test_equip_and_unequip_apply_and_remove_sense_effects() -> None:
    registry = _registry()
    registry.add_component("actor", SensesComponent(8, 5, 10, 1))
    registry.add_component("actor", EffectiveSensesComponent(8, 5, 10, 1))
    registry.add_component("actor", CharacterEmbodimentComponent())
    registry.add_component("actor", EquipmentStateComponent())
    _add_object(registry, "glasses", Coordinate(8, 10))
    registry.add_component("glasses", PortableComponent())
    registry.add_component(
        "glasses",
        ObjectIntrinsicComponent(mass_kg=0.05),
    )
    registry.add_component(
        "glasses",
        WearableComponent(
            compatible_slots=frozenset({EquipmentSlot.EYES}),
            effects=(
                ObjectEffect(
                    id="corrective-vision",
                    target=SenseEffectTarget.VISION_RANGE,
                    operation=EffectOperation.ADD,
                    value=6,
                ),
            ),
        ),
    )

    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "glasses"),
    )
    registry.remove_component("actor", InteractionRequestComponent)
    equipped = _run_interaction(
        registry,
        InteractionSpecification(
            InteractionVerb.EQUIP,
            "glasses",
            slot_id=EquipmentSlot.EYES.value,
        ),
    )

    relation = registry.get_component(
        "glasses",
        SpatialParentRelationComponent,
    )
    assert relation.kind is PhysicalRelationKind.ATTACHED_TO
    assert relation.slot_id == EquipmentSlot.EYES.value
    assert registry.get_component(
        "actor",
        EffectiveSensesComponent,
    ).vision_range == 14
    assert any(
        event.event_type == "character.effects_changed"
        for event in equipped.events.events
    )

    registry.remove_component("actor", InteractionRequestComponent)
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.UNEQUIP, "glasses"),
    )

    assert registry.get_component(
        "actor",
        EffectiveSensesComponent,
    ).vision_range == 8
    assert registry.get_component(
        "glasses",
        SpatialParentRelationComponent,
    ).kind is PhysicalRelationKind.HELD_BY


def test_pickup_rejects_known_mass_over_character_limits() -> None:
    registry = _registry()
    registry.add_component(
        "actor",
        CharacterEmbodimentComponent(
            max_single_object_mass_kg=5,
            max_carried_mass_kg=10,
        ),
    )
    _add_object(registry, "anvil", Coordinate(8, 10))
    registry.add_component("anvil", PortableComponent(two_handed=True))
    registry.add_component("anvil", ObjectIntrinsicComponent(mass_kg=20))

    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "anvil"),
    )

    assert registry.get_component(
        "actor",
        InteractionRequestComponent,
    ).failure_reason == "object_too_heavy"


def test_effect_resolution_applies_additions_before_multipliers_stably() -> None:
    registry = Registry()
    registry.create_entity("actor")
    registry.add_component("actor", SensesComponent(10, 5, 10, 0))
    registry.add_component("actor", EffectiveSensesComponent(10, 5, 10, 0))
    for object_id, effect in (
        (
            "multiplier",
            ObjectEffect(
                "multiply",
                SenseEffectTarget.VISION_RANGE,
                EffectOperation.MULTIPLY,
                2,
            ),
        ),
        (
            "addition",
            ObjectEffect(
                "add",
                SenseEffectTarget.VISION_RANGE,
                EffectOperation.ADD,
                5,
            ),
        ),
    ):
        registry.create_entity(object_id)
        registry.add_component(
            object_id,
            WearableComponent(
                frozenset({EquipmentSlot.EYES}),
                (effect,),
            ),
        )
        registry.add_component(
            object_id,
            SpatialParentRelationComponent(
                "actor",
                PhysicalRelationKind.ATTACHED_TO,
                EquipmentSlot.EYES.value,
            ),
        )

    resolve_character_effects(registry, "actor")

    assert registry.get_component(
        "actor",
        EffectiveSensesComponent,
    ).vision_range == 30


def test_hand_limits_and_pickup_conflicts_are_deterministic() -> None:
    registry = _registry()
    for object_id in ("a", "b", "c"):
        _add_object(registry, object_id, Coordinate(8, 10))
        registry.add_component(object_id, PortableComponent())
    for object_id in ("a", "b"):
        _run_interaction(
            registry,
            InteractionSpecification(InteractionVerb.PICK_UP, object_id),
        )
        registry.remove_component("actor", InteractionRequestComponent)

    failed = _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "c"),
    )

    request = registry.get_component("actor", InteractionRequestComponent)
    assert request.failure_reason == "hands_full"
    assert registry.get_component(
        "actor",
        CharacterHandStateComponent,
    ).held_object_ids == ("a", "b")
    assert any(
        event.event_type == "interaction.failed"
        and event.payload["reason"] == "hands_full"
        for event in failed.events.events
    )

    two_handed_registry = _registry()
    for object_id, two_handed in (("cup", False), ("crate", True)):
        _add_object(
            two_handed_registry,
            object_id,
            Coordinate(8, 10),
        )
        two_handed_registry.add_component(
            object_id,
            PortableComponent(two_handed=two_handed),
        )
    _run_interaction(
        two_handed_registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "cup"),
    )
    two_handed_registry.remove_component(
        "actor",
        InteractionRequestComponent,
    )
    _run_interaction(
        two_handed_registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "crate"),
    )
    assert two_handed_registry.get_component(
        "actor",
        InteractionRequestComponent,
    ).failure_reason == "hands_full"


def test_open_close_updates_live_obstruction_and_locked_open_fails() -> None:
    registry = _registry()
    _add_object(
        registry,
        "door",
        Coordinate(8, 10),
        obstruction=MovementObstruction.HARD,
    )
    registry.add_component("door", OpenableComponent())
    initial_revision = registry.get_resource(SpatialIndex).topology_revision

    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.OPEN, "door"),
    )

    assert registry.get_component("door", OpenableComponent).is_open
    assert registry.get_resource(SpatialIndex).hard_occupants(
        "implicit-building",
        Coordinate(8, 10),
    ) == ()
    assert (
        registry.get_resource(SpatialIndex).topology_revision
        == initial_revision + 1
    )

    registry.remove_component("actor", InteractionRequestComponent)
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.CLOSE, "door"),
    )
    assert not registry.get_component("door", OpenableComponent).is_open
    assert registry.get_resource(SpatialIndex).hard_occupants(
        "implicit-building",
        Coordinate(8, 10),
    ) == ("door",)

    registry.remove_component("actor", InteractionRequestComponent)
    registry.get_component("door", OpenableComponent).is_locked = True
    locked = _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.OPEN, "door"),
    )
    assert any(
        event.event_type == "interaction.failed"
        and event.payload["reason"] == "object_locked"
        for event in locked.events.events
    )


def test_support_container_placement_and_relation_cycles() -> None:
    registry = _registry()
    _add_object(registry, "item", Coordinate(8, 10))
    registry.add_component("item", PortableComponent())
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "item"),
    )
    registry.remove_component("actor", InteractionRequestComponent)

    _add_object(
        registry,
        "table",
        Coordinate(8, 10),
        obstruction=MovementObstruction.HARD,
    )
    registry.add_component(
        "table",
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "top",
                    frozenset({PhysicalRelationKind.ON_SUPPORT}),
                ),
            )
        ),
    )
    registry.add_component("table", SupportComponent(("top",)))
    _run_interaction(
        registry,
        InteractionSpecification(
            InteractionVerb.PLACE_ON,
            "item",
            destination_id="table",
        ),
    )

    relation = registry.get_component("item", SpatialParentRelationComponent)
    assert relation == SpatialParentRelationComponent(
        "table",
        PhysicalRelationKind.ON_SUPPORT,
        "top",
    )

    container_registry = _registry()
    _add_object(container_registry, "bottle", Coordinate(8, 10))
    container_registry.add_component("bottle", PortableComponent())
    _run_interaction(
        container_registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "bottle"),
    )
    container_registry.remove_component(
        "actor",
        InteractionRequestComponent,
    )
    _add_object(container_registry, "bag", Coordinate(8, 10))
    container_registry.add_component("bag", OpenableComponent(is_open=True))
    container_registry.add_component(
        "bag",
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "inside",
                    frozenset({PhysicalRelationKind.IN_CONTAINER}),
                ),
            )
        ),
    )
    container_registry.add_component(
        "bag",
        ContainerComponent(("inside",)),
    )
    _run_interaction(
        container_registry,
        InteractionSpecification(
            InteractionVerb.PLACE_IN,
            "bottle",
            destination_id="bag",
        ),
    )
    assert container_registry.get_component(
        "bottle",
        SpatialParentRelationComponent,
    ).kind is PhysicalRelationKind.IN_CONTAINER
    assert not container_registry.get_resource(SpatialIndex).contains(
        "bottle"
    )

    cycle_registry = _registry()
    _add_object(cycle_registry, "box", Coordinate(8, 10))
    cycle_registry.add_component("box", PortableComponent())
    cycle_registry.add_component(
        "box",
        OpenableComponent(is_open=True),
    )
    cycle_registry.add_component(
        "box",
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "inside",
                    frozenset({PhysicalRelationKind.IN_CONTAINER}),
                ),
            )
        ),
    )
    cycle_registry.add_component("box", ContainerComponent(("inside",)))
    _add_object(cycle_registry, "inner", Coordinate(8, 10), indexed=False)
    cycle_registry.set_component(
        "inner",
        SpatialParentRelationComponent(
            "box",
            PhysicalRelationKind.IN_CONTAINER,
            "inside",
        ),
    )
    cycle_registry.add_component(
        "inner",
        OpenableComponent(is_open=True),
    )
    cycle_registry.add_component(
        "inner",
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "inside",
                    frozenset({PhysicalRelationKind.IN_CONTAINER}),
                ),
            )
        ),
    )
    cycle_registry.add_component("inner", ContainerComponent(("inside",)))
    _run_interaction(
        cycle_registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "box"),
    )
    cycle_registry.remove_component("actor", InteractionRequestComponent)
    cycle = _run_interaction(
        cycle_registry,
        InteractionSpecification(
            InteractionVerb.PLACE_IN,
            "box",
            destination_id="inner",
        ),
    )
    assert cycle_registry.get_component(
        "actor",
        InteractionRequestComponent,
    ).failure_reason == "relation_cycle"
    assert any(
        event.payload.get("reason") == "relation_cycle"
        for event in cycle.events.events
        if event.event_type == "interaction.failed"
    )


def test_sit_stand_lie_and_get_up_use_deterministic_slot_poses() -> None:
    registry = _registry()
    _add_object(
        registry,
        "seat",
        Coordinate(8, 10),
        obstruction=MovementObstruction.HARD,
    )
    registry.add_component(
        "seat",
        OccupancySlotsComponent(
            (
                OccupancySlot(
                    "person",
                    frozenset({PhysicalRelationKind.OCCUPIES_SLOT}),
                ),
            )
        ),
    )
    registry.set_resource(
        PhysicalInteractionRegistry(
            {
                "seat": PhysicalInteractionTarget(
                    "seat",
                    "implicit-building",
                    approach_anchors=(Coordinate(5, 10),),
                    occupancy_anchors={
                        "person": (Coordinate(8, 10),),
                    },
                )
            },
            {},
        )
    )

    _run_interaction(
        registry,
        InteractionSpecification(
            InteractionVerb.SIT,
            "seat",
            slot_id="person",
        ),
    )
    assert registry.get_component(
        "actor",
        CharacterPostureComponent,
    ).posture is CharacterPosture.SITTING

    registry.remove_component("actor", InteractionRequestComponent)
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.STAND, "seat"),
    )
    assert registry.get_component(
        "actor",
        CharacterPostureComponent,
    ).posture is CharacterPosture.STANDING
    assert registry.get_component(
        "actor",
        PositionComponent,
    ).coordinate == Coordinate(5, 10)

    registry.remove_component("actor", InteractionRequestComponent)
    _run_interaction(
        registry,
        InteractionSpecification(
            InteractionVerb.LIE_DOWN,
            "seat",
            slot_id="person",
        ),
    )
    registry.remove_component("actor", InteractionRequestComponent)
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.GET_UP, "seat"),
    )
    assert registry.get_component(
        "actor",
        CharacterPostureComponent,
    ).posture is CharacterPosture.STANDING


def test_read_drink_and_phone_use_require_compatible_reachable_objects() -> None:
    registry = _registry()
    _add_object(registry, "book", Coordinate(8, 10))
    registry.add_component("book", PortableComponent())
    registry.add_component("book", ReadableComponent("doc-book"))
    _run_interaction(
        registry,
        InteractionSpecification(InteractionVerb.PICK_UP, "book"),
    )
    registry.remove_component("actor", InteractionRequestComponent)

    book_state = registry.get_component("book", PhysicalStateComponent)
    registry.set_component(
        "book",
        replace(
            book_state,
            pose=replace(book_state.pose, anchor=Coordinate(5, 10)),
        ),
    )
    registry.add_component("book", ConsumableComponent("water", 1))
    registry.add_component("book", UsableComponent("phone"))
    systems = SystemExecutor()
    systems.add(PlanExecutionSystem())
    systems.add(InteractionExecutionSystem())
    systems.add(TimedPlanActionSystem())
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="bounded-object-actions"),
        registry=registry,
        systems=systems,
    )
    queue_plan_actions(
        runner.context,
        "actor",
        registry.get_component("actor", PlanComponent),
        (
            PlanAction(
                ActionType.READ,
                target="book",
                duration=1.0,
            ),
            PlanAction(
                ActionType.DRINK,
                target="book",
                duration=1.0,
            ),
            PlanAction(
                ActionType.INTERACT,
                interaction=InteractionSpecification(
                    InteractionVerb.USE,
                    "book",
                ),
            ),
        ),
        origin=ActionOrigin.SCENARIO,
    )

    runner.run_for(5)

    assert not registry.has_component("book", ConsumableComponent)
    assert any(
        event.event_type == "drink.completed"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "action.completed"
        and event.payload["action"] == "DRINK"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "interaction.completed"
        and event.payload["verb"] == "USE"
        and event.payload["use_kind"] == "phone"
        for event in runner.events.events
    )


def test_interact_tool_requires_an_observable_advertised_verb() -> None:
    request = CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run-1",
        agent_id="actor",
        requested_tick=1,
        state_revision=0,
        trigger="idle",
        character_description="",
        profile_id="actor",
        profile_template_version=1,
        profile_content_hash="hash",
        observation=CharacterObservation(
            agent_id="actor",
            display_name="Actor",
            simulation_time=1.0,
            location_id=None,
            activity="IDLE",
            satiety=80,
            energy=80,
            stress=20,
            targets=(
                ObservedTarget(
                    "book",
                    "physical_object",
                    "Book",
                    available_interactions=("PICK_UP",),
                ),
            ),
            facts=(),
            recent_outcome=None,
        ),
        memories=(),
        allowed_tools=("interact_with",),
    )
    tools = ToolRegistry()

    intent = tools.propose(
        request,
        ModelToolCall(
            "call-1",
            "interact_with",
            {"verb": "PICK_UP", "target_id": "book"},
        ),
    )

    assert intent.specification == InteractionSpecification(
        InteractionVerb.PICK_UP,
        "book",
    )
    with pytest.raises(
        ToolValidationError,
        match="does not advertise OPEN",
    ) as error:
        tools.propose(
            request,
            ModelToolCall(
                "call-2",
                "interact_with",
                {"verb": "OPEN", "target_id": "book"},
            ),
        )
    assert error.value.reason == "interaction_not_available"


def test_interaction_event_sequence_is_identical_across_repeated_runs() -> None:
    def execute() -> list[dict[str, object]]:
        registry = _registry()
        _add_object(registry, "book", Coordinate(8, 10))
        registry.add_component("book", PortableComponent())
        runner = _run_interaction(
            registry,
            InteractionSpecification(InteractionVerb.PICK_UP, "book"),
        )
        return [
            event.canonical_dict()
            for event in runner.events.events
        ]

    assert execute() == execute()
