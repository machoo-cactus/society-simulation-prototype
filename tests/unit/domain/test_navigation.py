
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.domain.components import (
    MovementComponent,
    PhysicalObjectIdentityComponent,
    PhysicalStateComponent,
    PositionComponent,
    SensesComponent,
    SpatialIndex,
    SpatialIndexEntry,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    Coordinate,
    Footprint,
    MovementObstruction,
    PhysicalPose,
    SpatialMetric,
    WorldGrid,
    WorldMap,
    find_path,
)
from tests.helpers.paths import EXAMPLE_SCENARIOS


def build_navigation_runner(
    width: int,
    height: int,
    agents: dict[str, tuple[Coordinate, Coordinate]],
    *,
    blocked: frozenset[Coordinate] = frozenset(),
) -> SimulationRunner:
    registry = Registry()
    registry.set_resource(WorldMap(WorldGrid(width, height, blocked)))
    for agent_id, (position, destination) in agents.items():
        registry.create_entity(agent_id)
        registry.add_component(agent_id, PositionComponent(position))
        registry.add_component(agent_id, MovementComponent(destination=destination))
    systems = SystemExecutor()
    systems.add(PathfindingSystem())
    systems.add(MovementSystem())
    return SimulationRunner(
        RunConfiguration(seed=1, run_id="navigation-test"),
        registry=registry,
        systems=systems,
    )


def test_astar_uses_deterministic_shortest_route() -> None:
    grid = WorldGrid(3, 3, frozenset({Coordinate(1, 1)}))

    path = find_path(grid, Coordinate(0, 1), Coordinate(2, 1))

    assert path == (
        Coordinate(0, 0),
        Coordinate(1, 0),
        Coordinate(2, 0),
        Coordinate(2, 1),
    )


def test_four_zone_scenario_reaches_destination() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "navigation.json"
    runner = create_runner(load_scenario(scenario_path), run_id="four-zone")

    runner.run_for(20)

    position = runner.registry.get_component("agent-001", PositionComponent)
    movement = runner.registry.get_component("agent-001", MovementComponent)
    world = runner.registry.get_resource(WorldMap)
    assert position.coordinate == Coordinate(94, 58)
    assert movement.destination is None
    zone = world.zone_at(position.coordinate)
    assert zone is not None
    assert zone.name == "Lounge"
    assert world.station("sofa-1").position == position.coordinate
    assert sum(event.event_type == "path.completed" for event in runner.events.events) == 1


def test_navigation_event_log_is_reproducible() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "navigation.json"
    scenario = load_scenario(scenario_path)
    first = create_runner(scenario, run_id="first-navigation-run")
    second = create_runner(scenario, run_id="second-navigation-run")

    first.run_for(20)
    second.run_for(20)

    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]


def test_movement_conflict_is_resolved_in_agent_id_order() -> None:
    runner = build_navigation_runner(
        3,
        1,
        {
            "agent-b": (Coordinate(2, 0), Coordinate(1, 0)),
            "agent-a": (Coordinate(0, 0), Coordinate(1, 0)),
        },
    )

    runner.run_for(1)

    assert runner.registry.get_component(
        "agent-a", PositionComponent
    ).coordinate == Coordinate(1, 0)
    assert runner.registry.get_component(
        "agent-b", PositionComponent
    ).coordinate == Coordinate(2, 0)
    invalidation = next(
        event
        for event in runner.events.events
        if event.event_type == "path.invalidated"
    )
    assert invalidation.agent_id == "agent-b"
    assert invalidation.payload["reason"] == "movement_conflict"
    assert invalidation.payload["blocked_by"] == "agent-a"


def test_no_path_emits_failure_and_retries_deterministically() -> None:
    runner = build_navigation_runner(
        3,
        1,
        {"agent": (Coordinate(0, 0), Coordinate(2, 0))},
        blocked=frozenset({Coordinate(1, 0)}),
    )

    runner.run_for(2)

    failures = [
        event for event in runner.events.events if event.event_type == "path.failed"
    ]
    assert [event.simulation_tick for event in failures] == [1, 2]
    assert all(event.payload["reason"] == "no_path" for event in failures)
    assert runner.registry.get_component(
        "agent", PositionComponent
    ).coordinate == Coordinate(0, 0)


def test_existing_path_is_invalidated_when_occupancy_changes() -> None:
    runner = build_navigation_runner(
        3,
        2,
        {"agent": (Coordinate(0, 0), Coordinate(2, 0))},
    )
    movement = runner.registry.get_component("agent", MovementComponent)
    movement.path = (Coordinate(1, 0), Coordinate(2, 0))
    blocker = runner.registry.create_entity("blocker")
    runner.registry.add_component(blocker, PositionComponent(Coordinate(1, 0)))

    runner.run_for(1)

    invalidation = next(
        event
        for event in runner.events.events
        if event.event_type == "path.invalidated"
    )
    assert invalidation.payload["reason"] == "occupancy_changed"
    assert runner.registry.get_component(
        "agent", PositionComponent
    ).coordinate == Coordinate(0, 1)


def test_runtime_expands_legacy_maps_zones_positions_and_sense_ranges() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "microcell-map",
            "world": {
                "width": 2,
                "height": 2,
                "blocked": [{"x": 1, "y": 1}],
                "zones": [
                    {
                        "id": "zone",
                        "name": "Zone",
                        "type": "ROOM",
                        "tiles": [{"x": 0, "y": 0}],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "senses": {
                            "vision_range": 2,
                            "recognition_range": 1,
                        },
                    },
                }
            ],
        }
    )

    runner = create_runner(scenario)
    world = runner.registry.get_resource(WorldMap)
    position = runner.registry.get_component("agent", PositionComponent)
    senses = runner.registry.get_component("agent", SensesComponent)
    physical = runner.registry.get_component(
        "agent",
        PhysicalStateComponent,
    )

    assert (world.grid.width, world.grid.height) == (18, 18)
    assert len(world.grid.blocked) == 81
    assert world.zone_at(Coordinate(8, 8)) is not None
    assert world.zone_at(Coordinate(9, 8)) is None
    assert position.coordinate == Coordinate(4, 4)
    assert physical.footprint == STANDING_CHARACTER_FOOTPRINT
    assert len(physical.occupied_cells) == 25
    assert runner.registry.get_resource(SpatialIndex).contains("agent")
    assert senses.vision_range == 18
    assert senses.recognition_range == 9


def test_five_by_five_footprint_rejects_four_microcell_passage() -> None:
    grid = WorldGrid(
        15,
        15,
        frozenset(
            Coordinate(x, y)
            for y in range(15)
            for x in range(15)
            if x not in {5, 6, 7, 8}
        ),
    )

    path = find_path(
        grid,
        Coordinate(6, 2),
        Coordinate(6, 12),
        footprint=STANDING_CHARACTER_FOOTPRINT,
        room_id="room",
    )

    assert path is None


def test_microcell_movement_preserves_legacy_crossing_time_and_event_bound() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "crossing-time",
            "world": {"width": 3, "height": 1},
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "movement": {"destination": {"x": 2, "y": 0}},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(2)

    assert runner.registry.get_component(
        "agent",
        PositionComponent,
    ).coordinate == SpatialMetric().center_legacy_coordinate(
        Coordinate(2, 0)
    ).to_coordinate()
    moved = [
        event
        for event in runner.events.events
        if event.event_type == "agent.moved"
    ]
    assert len(moved) == 2
    assert [event.payload["distance_microcells"] for event in moved] == [9, 9]


def test_path_replans_when_spatial_revision_blocks_the_next_footprint() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "spatial-replan",
            "world": {"width": 3, "height": 1},
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "movement": {"destination": {"x": 2, "y": 0}},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)
    movement = runner.registry.get_component("agent", MovementComponent)
    movement.speed_legacy_cells_per_second = 0.1

    runner.run_for(1)

    assert movement.path
    blocker_id = runner.registry.create_entity("dynamic-blocker")
    blocker_state = PhysicalStateComponent(
        PhysicalPose("implicit-building", Coordinate(7, 4)),
        Footprint(frozenset({Coordinate(0, 0)})),
        MovementObstruction.HARD,
    )
    runner.registry.add_component(
        blocker_id,
        PhysicalObjectIdentityComponent("dynamic-blocker", "Blocker"),
    )
    runner.registry.add_component(blocker_id, blocker_state)
    runner.registry.get_resource(SpatialIndex).add(
        SpatialIndexEntry(blocker_id, blocker_state)
    )

    runner.run_for(1)

    assert any(
        event.event_type == "path.invalidated"
        and event.payload["reason"] == "spatial_revision_changed"
        for event in runner.events.events
    )
