import json
from pathlib import Path

from fastapi.testclient import TestClient

from stage0_sim.adapters.llm import FakePlanner, ScriptedPlanner
from stage0_sim.api.app import app
from stage0_sim.api.fake_llm import app as fake_llm_app
from stage0_sim.application.scenario import ScenarioDefinition, create_runner, load_scenario
from stage0_sim.domain.components import (
    ActionType,
    DriveComponent,
    DriveType,
    HomeostasisComponent,
    PlanAction,
    PlanComponent,
    PlannerComponent,
    PositionComponent,
)
from stage0_sim.domain.world import Coordinate


def test_fake_llm_is_a_separate_openai_compatible_api() -> None:
    request = {
        "model": "stage0-fake",
        "messages": [{"role": "user", "content": "Choose an action."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "wait",
                    "description": "Wait.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "duration_seconds": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["duration_seconds"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
    }
    with TestClient(fake_llm_app) as client:
        first = client.post("/v1/chat/completions", json=request)
        second = client.post("/v1/chat/completions", json=request)
        models = client.get("/v1/models")
    with TestClient(app) as client:
        embedded_route = client.post("/v1/chat/completions", json=request)

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    first_number = int(first_payload["id"].rsplit("-", 1)[1])
    second_number = int(second_payload["id"].rsplit("-", 1)[1])
    assert second_number == first_number + 1
    first_call = first_payload["choices"][0]["message"]["tool_calls"][0]
    second_call = second_payload["choices"][0]["message"]["tool_calls"][0]
    assert first_call["function"]["name"] == "wait"
    assert json.loads(second_call["function"]["arguments"])[
        "duration_seconds"
    ] == second_number
    assert models.json()["data"][0]["id"] == "stage0-fake"
    assert embedded_route.status_code == 404


def test_fake_planner_generates_and_executes_work_routine() -> None:
    scenario_path = Path(__file__).parents[1] / "scenarios" / "fake-llm-planning.json"
    fake_planner = FakePlanner()
    runner = create_runner(
        load_scenario(scenario_path),
        run_id="fake-planning",
        planner=fake_planner,
    )

    runner.run_for(10)

    position = runner.registry.get_component("agent-001", PositionComponent)
    plan = runner.registry.get_component("agent-001", PlanComponent)
    planner_state = runner.registry.get_component("agent-001", PlannerComponent)
    assert fake_planner.call_count == 1
    assert planner_state.request_count == 1
    assert position.coordinate == Coordinate(5, 0)
    assert plan.current is not None
    assert plan.current.action is ActionType.WORK
    event_types = [event.event_type for event in runner.events.events]
    assert "planner.requested" in event_types
    assert "planner.completed" in event_types
    assert "plan.action_started" in event_types
    assert "path.completed" in event_types


def test_physical_scenario_makes_no_planner_calls() -> None:
    scenario_path = Path(__file__).parents[1] / "scenarios" / "navigation.json"
    fake_planner = FakePlanner()
    runner = create_runner(load_scenario(scenario_path), planner=fake_planner)

    runner.run_for(20)

    assert fake_planner.call_count == 0
    assert not any(
        event.event_type.startswith("planner.") for event in runner.events.events
    )


def test_invalid_planner_output_emits_failure_without_stopping_ticks() -> None:
    scenario_path = Path(__file__).parents[1] / "scenarios" / "fake-llm-planning.json"
    invalid_planner = ScriptedPlanner(
        actions=(PlanAction(ActionType.MOVE_TO, target="missing-place"),)
    )
    runner = create_runner(
        load_scenario(scenario_path),
        planner=invalid_planner,
    )

    runner.run_for(3)

    planner_state = runner.registry.get_component("agent-001", PlannerComponent)
    assert runner.clock.tick == 3
    assert invalid_planner.call_count == 3
    assert planner_state.failure_count == 3
    assert sum(
        event.event_type == "planner.failed" for event in runner.events.events
    ) == 3


def test_system1_clears_generated_plan_without_another_planner_call() -> None:
    scenario_path = Path(__file__).parents[1] / "scenarios" / "fake-llm-planning.json"
    fake_planner = FakePlanner()
    runner = create_runner(load_scenario(scenario_path), planner=fake_planner)
    state = runner.registry.get_component("agent-001", HomeostasisComponent)

    runner.run_for(1)
    state.satiety = 10
    runner.run_for(1)

    plan = runner.registry.get_component("agent-001", PlanComponent)
    drive = runner.registry.get_component("agent-001", DriveComponent)
    assert plan.current is None
    assert plan.queue == []
    assert drive.active_drive is DriveType.SATIETY
    assert fake_planner.call_count == 1


def test_scripted_plan_can_execute_station_affordance() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "planned-eating",
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
                        "actions": [
                            {
                                "action": "EAT",
                                "duration": 2,
                                "effect": {"satiety_delta": 60},
                            }
                        ],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {"satiety": 30, "energy": 80, "stress": 20},
                        "planner": {"daily_goals": ["Eat"]},
                    },
                }
            ],
        }
    )
    scripted = ScriptedPlanner(
        actions=(
            PlanAction(ActionType.MOVE_TO, target="fridge"),
            PlanAction(ActionType.EAT, target="fridge"),
        )
    )
    runner = create_runner(scenario, planner=scripted)

    runner.run_for(6)

    state = runner.registry.get_component("agent", HomeostasisComponent)
    assert state.satiety == 90.0
    assert sum(
        event.event_type == "affordance.completed" for event in runner.events.events
    ) == 1
