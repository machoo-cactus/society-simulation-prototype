from pathlib import Path

from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.domain.components import (
    ActionType,
    ActivityComponent,
    ActivityType,
    DriveComponent,
    DriveType,
    HomeostasisComponent,
    MovementComponent,
    PlanAction,
    PlanComponent,
    System1State,
)
from stage0_sim.domain.world import Coordinate


def test_critical_satiety_preempts_plan_and_targets_fridge_on_next_tick() -> None:
    scenario_path = Path(__file__).parents[1] / "scenarios" / "system1-preemption.json"
    runner = create_runner(load_scenario(scenario_path), run_id="preemption")

    runner.run_for(1)

    plan = runner.registry.get_component("agent-001", PlanComponent)
    movement = runner.registry.get_component("agent-001", MovementComponent)
    activity = runner.registry.get_component("agent-001", ActivityComponent)
    drive = runner.registry.get_component("agent-001", DriveComponent)
    assert plan.current is None
    assert plan.queue == []
    assert movement.destination == Coordinate(1, 1)
    assert movement.path == ()
    assert activity.current is ActivityType.IDLE
    assert drive.active_drive is DriveType.SATIETY
    assert drive.target_station_id == "fridge-near"
    assert drive.state is System1State.NAVIGATING_TO_CORRECTION
    event_types = [event.event_type for event in runner.events.events]
    assert "threshold.breached" in event_types
    assert "system1.activated" in event_types
    assert "plan.cleared" in event_types
    assert "system1.target_selected" in event_types


def test_nearest_correction_uses_reachable_path_cost_not_manhattan_distance() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "actual-path-cost",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 5,
                "height": 3,
                "blocked": [{"x": 1, "y": 1}],
                "stations": [
                    {
                        "id": "fridge-manhattan-near",
                        "name": "Near Around Wall",
                        "position": {"x": 0, "y": 1},
                        "supported_actions": ["EAT"],
                    },
                    {
                        "id": "fridge-path-near",
                        "name": "Reachable Near",
                        "position": {"x": 4, "y": 1},
                        "supported_actions": ["EAT"],
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 2, "y": 1},
                        "homeostasis": {"satiety": 10, "energy": 80, "stress": 20},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    drive = runner.registry.get_component("agent", DriveComponent)
    assert drive.target_station_id == "fridge-path-near"


def test_simultaneous_drives_use_severity_then_configured_tie_break() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "drive-tie",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "system1": {"tie_break_order": ["ENERGY", "SATIETY", "STRESS"]},
            "world": {
                "width": 3,
                "height": 1,
                "stations": [
                    {
                        "id": "fridge",
                        "name": "Fridge",
                        "position": {"x": 0, "y": 0},
                        "supported_actions": ["EAT"],
                    },
                    {
                        "id": "bed",
                        "name": "Bed",
                        "position": {"x": 2, "y": 0},
                        "supported_actions": ["SLEEP"],
                    },
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "homeostasis": {"satiety": 10, "energy": 10, "stress": 20},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    drive = runner.registry.get_component("agent", DriveComponent)
    assert drive.active_drive is DriveType.ENERGY
    assert drive.target_station_id == "bed"


def test_high_stress_targets_relaxation_station() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "stress-correction",
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
                        "id": "sofa",
                        "name": "Sofa",
                        "position": {"x": 1, "y": 0},
                        "supported_actions": ["RELAX"],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {"satiety": 80, "energy": 80, "stress": 90},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    drive = runner.registry.get_component("agent", DriveComponent)
    assert drive.active_drive is DriveType.STRESS
    assert drive.target_station_id == "sofa"


def test_system1_hysteresis_holds_until_recovery_threshold() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "recovery-hysteresis",
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
                        "id": "fridge",
                        "name": "Fridge",
                        "position": {"x": 0, "y": 0},
                        "supported_actions": ["EAT"],
                    }
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
    drive = runner.registry.get_component("agent", DriveComponent)

    runner.run_for(1)
    state.satiety = 20
    runner.run_for(1)
    assert drive.state is System1State.EXECUTING_CORRECTION
    assert drive.active_drive is DriveType.SATIETY

    state.satiety = 30
    runner.run_for(1)
    assert drive.state is System1State.EXECUTING_CORRECTION

    runner.run_for(2)
    assert drive.state is System1State.NORMAL
    assert drive.active_drive is None
    assert sum(
        event.event_type == "system1.resolved" for event in runner.events.events
    ) == 1


def test_active_system1_clears_injected_plan_and_blocked_survival_is_observable() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "blocked",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {"width": 2, "height": 1, "stations": []},
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {"satiety": 10, "energy": 80, "stress": 20},
                        "plan": {"current": {"action": "WORK"}},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)
    plan = runner.registry.get_component("agent", PlanComponent)
    drive = runner.registry.get_component("agent", DriveComponent)

    runner.run_for(1)
    assert drive.state is System1State.BLOCKED_SURVIVAL
    plan.current = PlanAction(ActionType.WORK)
    runner.run_for(1)

    assert plan.current is None
    assert sum(
        event.event_type == "system1.blocked" for event in runner.events.events
    ) == 1
    assert sum(event.event_type == "plan.cleared" for event in runner.events.events) == 2
