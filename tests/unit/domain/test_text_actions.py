from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.domain.components import (
    ActionOrigin,
    ActionType,
    ActivityComponent,
    ContentAccessMode,
    ContentEndpoint,
    ContentEndpointComponent,
    ContentEndpointKind,
    DriveComponent,
    MovementComponent,
    PendingTextReceiptsComponent,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PlanAction,
    PlanComponent,
    PositionComponent,
    SpatialParentRelationComponent,
)
from stage0_sim.domain.content import (
    TextAccessGrant,
    TextAccessPolicy,
    TextArtifact,
    TextArtifactMode,
    TextAttribution,
    TextAttributionDisplay,
    TextBlock,
    TextBlockDraft,
    TextBlockKind,
    TextContentRegistry,
    TextMediaKind,
    TextOperation,
    TextPrincipal,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.lineage import queue_plan_actions
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.plans import PlanExecutionSystem
from stage0_sim.domain.systems.text_actions import TextActionExecutionSystem
from stage0_sim.domain.text_actions import (
    TextAttributionRequest,
    TextReadSpecification,
    TextWriteSpecification,
)
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    Coordinate,
    Footprint,
    MovementObstruction,
    PhysicalPose,
    WorldGrid,
    WorldMap,
)


def _policy(*operations: TextOperation) -> TextAccessPolicy:
    return TextAccessPolicy(
        tuple(
            TextAccessGrant(operation, (TextPrincipal.public(),))
            for operation in operations
        )
    )


def _registry(*actor_ids: str) -> Registry:
    registry = Registry()
    registry.set_resource(WorldMap(WorldGrid(30, 20)))
    artifact = TextArtifact.create(
        id="shared-note",
        media_kind=TextMediaKind.NOTE,
        mode=TextArtifactMode.MUTABLE,
        blocks=(
            TextBlock(
                "body",
                1,
                "Initial text",
                TextBlockKind.PARAGRAPH,
            ),
        ),
        access_policy=_policy(
            TextOperation.READ,
            TextOperation.APPEND,
            TextOperation.REPLACE,
            TextOperation.EDIT,
            TextOperation.DELETE,
        ),
        operation_id="scenario-create:shared-note",
        attribution=TextAttribution(
            "author",
            TextAttributionDisplay.VERIFIED,
            display_label="Author",
        ),
        simulation_tick=0,
        simulation_time=0,
    )
    registry.set_resource(TextContentRegistry(artifacts=(artifact,)))
    registry.create_entity("notebook")
    registry.add_component(
        "notebook",
        PhysicalObjectIdentityComponent("notebook", "Notebook"),
    )
    registry.add_component(
        "notebook",
        PhysicalStateComponent(
            PhysicalPose("room", Coordinate(8, 5)),
            Footprint(frozenset({Coordinate(0, 0)})),
            MovementObstruction.NONE,
        ),
    )
    registry.add_component(
        "notebook",
        SpatialParentRelationComponent(
            "room", PhysicalRelationKind.ON_FLOOR
        ),
    )
    registry.add_component(
        "notebook",
        ContentEndpointComponent(
            (
                ContentEndpoint(
                    "main",
                    "Main text",
                    ContentEndpointKind.ARTIFACT,
                    "shared-note",
                    (
                        TextOperation.READ,
                        TextOperation.APPEND,
                        TextOperation.REPLACE,
                        TextOperation.EDIT,
                        TextOperation.DELETE,
                    ),
                    ContentAccessMode.EXPOSED_REACHABLE,
                ),
            )
        ),
    )
    for actor_id in actor_ids:
        registry.create_entity(actor_id)
        registry.add_component(
            actor_id, PositionComponent(Coordinate(5, 5))
        )
        registry.add_component(actor_id, MovementComponent())
        registry.add_component(actor_id, ActivityComponent())
        registry.add_component(actor_id, DriveComponent())
        registry.add_component(actor_id, PlanComponent())
        registry.add_component(
            actor_id,
            PhysicalStateComponent(
                PhysicalPose("room", Coordinate(5, 5)),
                STANDING_CHARACTER_FOOTPRINT,
                MovementObstruction.HARD,
            ),
        )
    return registry


def _runner(registry: Registry) -> SimulationRunner:
    systems = SystemExecutor()
    systems.add(PlanExecutionSystem())
    systems.add(TextActionExecutionSystem())
    return SimulationRunner(
        RunConfiguration(seed=1, run_id="text-action-test"),
        registry=registry,
        systems=systems,
    )


def test_embodied_read_delivers_pinned_receipt_after_completion() -> None:
    registry = _registry("reader")
    runner = _runner(registry)
    plan = registry.get_component("reader", PlanComponent)
    queue_plan_actions(
        runner.context,
        "reader",
        plan,
        (
            PlanAction(
                ActionType.READ_TEXT,
                text_read=TextReadSpecification(
                    "notebook",
                    "main",
                    "shared-note",
                    ("body",),
                ),
            ),
        ),
        origin=ActionOrigin.CONTROLLER,
    )

    runner.run_for(2)

    receipt = registry.get_component(
        "reader", PendingTextReceiptsComponent
    ).receipts[0]
    assert receipt.artifact_revision == 1
    assert receipt.rendered_text == "Initial text"
    assert any(
        event.event_type == "text.read_completed"
        for event in runner.events.events
    )
    assert any(
        event.event_type == "action.completed"
        and event.payload["action"] == "READ_TEXT"
        for event in runner.events.events
    )


def test_same_revision_writes_resolve_in_stable_actor_order() -> None:
    registry = _registry("alice", "bob")
    runner = _runner(registry)
    for actor_id, text in (("alice", "Alice"), ("bob", "Bob")):
        queue_plan_actions(
            runner.context,
            actor_id,
            registry.get_component(actor_id, PlanComponent),
            (
                PlanAction(
                    ActionType.WRITE_TEXT,
                    text_write=TextWriteSpecification(
                        operation=TextOperation.APPEND,
                        target_id="notebook",
                        endpoint_id="main",
                        attribution=TextAttributionRequest(
                            TextAttributionDisplay.VERIFIED,
                            display_label=actor_id,
                        ),
                        artifact_id="shared-note",
                        expected_artifact_revision=1,
                        blocks=(TextBlockDraft(text),),
                    ),
                ),
            ),
            origin=ActionOrigin.CONTROLLER,
        )

    runner.run_for(2)

    artifact = registry.get_resource(TextContentRegistry).artifact(
        "shared-note"
    )
    assert artifact.current_revision == 2
    assert artifact.current.blocks[-1].text == "Alice"
    failed = [
        event
        for event in runner.events.events
        if event.event_type == "action.failed"
    ]
    assert len(failed) == 1
    assert failed[0].agent_id == "bob"
    assert failed[0].payload["reason"] == "revision_conflict"
