
import pytest

from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    HomeostasisComponent,
    HomeostasisConfiguration,
    default_activity_rates,
)
from tests.helpers.paths import EXAMPLE_SCENARIOS


def test_working_trajectory_matches_configured_coefficients() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "homeostasis.json"
    runner = create_runner(load_scenario(scenario_path), run_id="working")

    runner.run_for(10)

    state = runner.registry.get_component("agent-001", HomeostasisComponent)
    assert state.snapshot() == {"satiety": 79.7, "energy": 74.5, "stress": 20.4}
    changes = [
        event
        for event in runner.events.events
        if event.event_type == "homeostasis.changed"
    ]
    assert len(changes) == 10
    assert changes[0].payload["activity"] == "WORKING"


def test_custom_coefficients_use_dt_and_clamp_every_meter() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "custom-rates",
            "dt": 2.0,
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": -2.0, "energy": 3.0, "stress": 60.0}
                }
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "homeostasis": {
                            "satiety": 1.0,
                            "energy": 99.0,
                            "stress": 10.0,
                        }
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    state = runner.registry.get_component("agent", HomeostasisComponent)
    assert state.snapshot() == {"satiety": 0.0, "energy": 100.0, "stress": 100.0}


def test_movement_temporarily_uses_walking_activity() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "walking-override",
            "world": {"width": 2, "height": 1},
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "movement": {"destination": {"x": 1, "y": 0}},
                        "homeostasis": {
                            "satiety": 50.0,
                            "energy": 50.0,
                            "stress": 50.0,
                        },
                        "activity": {"type": "WORKING"},
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)
    state = runner.registry.get_component("agent", HomeostasisComponent)
    activity = runner.registry.get_component("agent", ActivityComponent)
    assert state.satiety == pytest.approx(49.95)
    assert state.energy == pytest.approx(49.96)
    assert state.stress == pytest.approx(50.01)
    assert activity.current is ActivityType.WALKING

    runner.run_for(1)
    assert activity.current is ActivityType.WORKING
    assert state.satiety == pytest.approx(49.92)
    assert state.energy == pytest.approx(49.91)
    assert state.stress == pytest.approx(50.05)
    transitions = [
        event.payload
        for event in runner.events.events
        if event.event_type == "activity.changed"
    ]
    assert transitions == [
        {"previous": "WORKING", "current": "WALKING"},
        {"previous": "WALKING", "current": "WORKING"},
    ]


def test_default_configuration_covers_all_activity_types() -> None:
    configuration = HomeostasisConfiguration(default_activity_rates())

    assert set(configuration.activity_rates) == set(ActivityType)
    assert configuration.activity_rates[ActivityType.EATING].satiety > 0
    assert configuration.activity_rates[ActivityType.SLEEPING].energy > 0
    assert configuration.activity_rates[ActivityType.RELAXING].stress < 0


def test_homeostasis_event_log_is_reproducible() -> None:
    scenario_path = EXAMPLE_SCENARIOS / "homeostasis.json"
    scenario = load_scenario(scenario_path)
    first = create_runner(scenario, run_id="first")
    second = create_runner(scenario, run_id="second")

    first.run_for(10)
    second.run_for(10)

    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]
