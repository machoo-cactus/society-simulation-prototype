from pathlib import Path

from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import create_runner, load_scenario
from stage0_sim.domain.components import MovementComponent, PositionComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.navigation import MovementSystem, PathfindingSystem
from stage0_sim.domain.world import Coordinate, WorldGrid, WorldMap, find_path


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
    scenario_path = Path(__file__).parents[2] / "scenarios" / "navigation.json"
    runner = create_runner(load_scenario(scenario_path), run_id="four-zone")

    runner.run_for(20)

    position = runner.registry.get_component("agent-001", PositionComponent)
    movement = runner.registry.get_component("agent-001", MovementComponent)
    world = runner.registry.get_resource(WorldMap)
    assert position.coordinate == Coordinate(10, 6)
    assert movement.destination is None
    zone = world.zone_at(position.coordinate)
    assert zone is not None
    assert zone.name == "Lounge"
    assert world.station("sofa-1").position == position.coordinate
    assert sum(event.event_type == "path.completed" for event in runner.events.events) == 1


def test_navigation_event_log_is_reproducible() -> None:
    scenario_path = Path(__file__).parents[2] / "scenarios" / "navigation.json"
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
