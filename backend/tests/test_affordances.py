from pathlib import Path

import pytest

from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.domain.components import (
    ActivityComponent,
    AffordanceExecutionComponent,
    DriveComponent,
    DriveType,
    HomeostasisComponent,
    PositionComponent,
    System1Configuration,
    System1State,
    default_drive_thresholds,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemExecutor
from stage0_sim.domain.systems.affordances import AffordanceExecutionSystem
from stage0_sim.domain.world import (
    AffordanceAction,
    AffordanceStation,
    Coordinate,
    HomeostasisEffect,
    WorldGrid,
    WorldMap,
)


@pytest.mark.parametrize(
    ("action", "initial", "effect", "midpoint", "final"),
    [
        (
            "EAT",
            {"satiety": 10, "energy": 80, "stress": 20},
            {"satiety_delta": 60},
            {"satiety": 40.0, "energy": 80.0, "stress": 20.0},
            {"satiety": 70.0, "energy": 80.0, "stress": 20.0},
        ),
        (
            "SLEEP",
            {"satiety": 80, "energy": 10, "stress": 20},
            {"energy_target": 100},
            {"satiety": 80.0, "energy": 55.0, "stress": 20.0},
            {"satiety": 80.0, "energy": 100.0, "stress": 20.0},
        ),
        (
            "RELAX",
            {"satiety": 80, "energy": 80, "stress": 90},
            {"stress_delta": -40},
            {"satiety": 80.0, "energy": 80.0, "stress": 70.0},
            {"satiety": 80.0, "energy": 80.0, "stress": 50.0},
        ),
    ],
)
def test_corrective_affordances_apply_time_based_exact_outcomes(
    action: str,
    initial: dict[str, int],
    effect: dict[str, int],
    midpoint: dict[str, float],
    final: dict[str, float],
) -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": f"{action.lower()}-recovery",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 1,
                "height": 1,
                "stations": [
                    {
                        "id": "station",
                        "name": "Recovery Station",
                        "position": {"x": 0, "y": 0},
                        "actions": [
                            {"action": action, "duration": 2, "effect": effect}
                        ],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": initial,
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)
    state = runner.registry.get_component("agent", HomeostasisComponent)

    runner.run_for(1)
    assert state.snapshot() == midpoint
    assert runner.registry.has_component("agent", AffordanceExecutionComponent)

    runner.run_for(1)
    assert state.snapshot() == final
    assert not runner.registry.has_component("agent", AffordanceExecutionComponent)
    assert (
        runner.registry.get_component("agent", DriveComponent).state
        is System1State.NORMAL
    )
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.count("affordance.started") == 1
    assert event_types.count("affordance.progressed") == 2
    assert event_types.count("affordance.completed") == 1
    assert event_types.count("system1.resolved") == 1
    affordance_changes = [
        event
        for event in runner.events.events
        if event.event_type == "homeostasis.changed"
        and event.payload.get("source") == "affordance"
    ]
    assert len(affordance_changes) == 2


def test_unavailable_station_fails_explicit_precondition() -> None:
    registry = Registry()
    station = AffordanceStation(
        id="fridge",
        name="Fridge",
        position=Coordinate(0, 0),
        actions=(
            AffordanceAction(
                action="EAT",
                duration=2,
                effect=HomeostasisEffect(satiety_delta=60),
            ),
        ),
        available=False,
    )
    registry.set_resource(WorldMap(WorldGrid(1, 1), stations=(station,)))
    registry.set_resource(System1Configuration(default_drive_thresholds()))
    registry.create_entity("agent")
    registry.add_component("agent", PositionComponent(Coordinate(0, 0)))
    registry.add_component(
        "agent", HomeostasisComponent(satiety=10, energy=80, stress=20)
    )
    registry.add_component("agent", ActivityComponent())
    registry.add_component(
        "agent",
        DriveComponent(
            state=System1State.EXECUTING_CORRECTION,
            active_drive=DriveType.SATIETY,
            target_station_id="fridge",
        ),
    )
    systems = SystemExecutor()
    systems.add(AffordanceExecutionSystem())
    runner = SimulationRunner(
        RunConfiguration(seed=0),
        registry=registry,
        systems=systems,
    )

    runner.run_for(1)

    failure = next(
        event
        for event in runner.events.events
        if event.event_type == "affordance.failed"
    )
    assert failure.payload["reason"] == "station_unavailable"
    assert not registry.has_component("agent", AffordanceExecutionComponent)


def test_new_more_severe_drive_cancels_active_correction_and_retargets() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "correction-retarget",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 2,
                "height": 1,
                "stations": [
                    {
                        "id": "fridge",
                        "name": "Fridge",
                        "position": {"x": 0, "y": 0},
                        "actions": [
                            {
                                "action": "EAT",
                                "duration": 5,
                                "effect": {"satiety_delta": 60},
                            }
                        ],
                    },
                    {
                        "id": "bed",
                        "name": "Bed",
                        "position": {"x": 1, "y": 0},
                        "actions": [
                            {
                                "action": "SLEEP",
                                "duration": 2,
                                "effect": {"energy_target": 100},
                            }
                        ],
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {"satiety": 10, "energy": 80, "stress": 20},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)
    state = runner.registry.get_component("agent", HomeostasisComponent)

    runner.run_for(1)
    state.energy = 0
    runner.run_for(1)

    drive = runner.registry.get_component("agent", DriveComponent)
    assert drive.active_drive is DriveType.ENERGY
    assert drive.target_station_id == "bed"
    assert not runner.registry.has_component("agent", AffordanceExecutionComponent)
    cancellation = next(
        event
        for event in runner.events.events
        if event.event_type == "affordance.cancelled"
    )
    assert cancellation.payload["reason"] == "drive_priority_changed"


def test_full_preemption_run_reaches_recovery_and_is_reproducible() -> None:
    scenario_path = Path(__file__).parents[2] / "scenarios" / "system1-preemption.json"
    scenario = load_scenario(scenario_path)
    first = create_runner(scenario, run_id="first")
    second = create_runner(scenario, run_id="second")

    first.run_for(10)
    second.run_for(10)

    state = first.registry.get_component("agent-001", HomeostasisComponent)
    assert state.satiety == 69.73
    assert (
        first.registry.get_component("agent-001", DriveComponent).state
        is System1State.NORMAL
    )
    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]
