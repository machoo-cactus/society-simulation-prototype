import json
import sqlite3
from pathlib import Path

from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.domain.components import (
    ActionInstance,
    ActionOrigin,
    ActionType,
    PlanAction,
    PlanComponent,
)
from stage0_sim.domain.lineage import queue_plan_actions
from tests.helpers.paths import EXAMPLE_SCENARIOS, REPOSITORY_ROOT, SCENARIO_FIXTURES

ROOT = REPOSITORY_ROOT


def _controller_scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "action-lineage-controller",
            "homeostasis": {
                "activity_coefficients": {
                    "IDLE": {"satiety": 0, "energy": 0, "stress": 0}
                }
            },
            "world": {
                "width": 2,
                "height": 1,
                "zones": [
                    {
                        "id": "destination",
                        "name": "Destination",
                        "type": "room",
                        "tiles": [{"x": 1, "y": 0}],
                    }
                ],
                "stations": [],
            },
            "entities": [
                {
                    "id": "character",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "movement": {},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "goals": {
                            "goals": [
                                {
                                    "id": "goal-destination",
                                    "description": "Reach the destination",
                                    "criteria": [
                                        {
                                            "type": "location_match",
                                            "location_id": "destination",
                                            "location_kind": "zone",
                                        }
                                    ],
                                }
                            ],
                        },
                        "metadata": {"display_name": "Character"},
                        "controller": {"enabled": True},
                    },
                }
            ],
        }
    )


def _move_client() -> ScriptedModelClient:
    return ScriptedModelClient(
        (
            ModelTurn(
                text=None,
                tool_calls=(
                    ModelToolCall(
                        call_id="tool-call-1",
                        name="navigate_to",
                        arguments={"target_id": "destination"},
                    ),
                ),
                finish_reason="tool_calls",
                provider="scripted",
                model="scripted-v1",
                latency_ms=0,
            ),
        )
    )


def test_scenario_actions_receive_stable_ids() -> None:
    scenario = load_scenario(EXAMPLE_SCENARIOS / "system1-preemption.json")
    first = create_runner(scenario, run_id="lineage-first")
    second = create_runner(scenario, run_id="lineage-second")

    first.run_for(1)
    second.run_for(1)

    first_actions = [
        event
        for event in first.events.events
        if event.event_type == "action.queued"
        and event.payload["action_origin"] == "scenario"
    ]
    second_actions = [
        event
        for event in second.events.events
        if event.event_type == "action.queued"
        and event.payload["action_origin"] == "scenario"
    ]
    assert [event.payload["action_id"] for event in first_actions] == [
        "action-00000001",
        "action-00000002",
    ]
    assert [event.payload["action_id"] for event in second_actions] == [
        "action-00000001",
        "action-00000002",
    ]
    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]


def test_tool_commit_links_controller_action_to_terminal_outcome() -> None:
    runner = create_runner(
        _controller_scenario(),
        run_id="controller-lineage",
        model_client=_move_client(),
    )

    runner.run_for(3)

    committed = next(
        event
        for event in runner.events.events
        if event.event_type == "tool.committed"
    )
    action_id = committed.payload["action_id"]
    lifecycle = [
        event
        for event in runner.events.events
        if event.payload.get("action_id") == action_id
    ]
    terminal = next(
        event for event in lifecycle if event.event_type == "action.completed"
    )
    moved = next(event for event in lifecycle if event.event_type == "agent.moved")
    action_lifecycle = [
        event
        for event in lifecycle
        if event.event_type
        in {
            "action.queued",
            "action.started",
            "action.completed",
            "action.failed",
            "action.cancelled",
        }
    ]
    assert committed.payload["action_origin"] == "controller"
    assert terminal.payload["tool_call_id"] == "tool-call-1"
    assert terminal.payload["decision_id"] == committed.payload["decision_id"]
    assert moved.payload["plan_id"] == committed.payload["plan_id"]
    assert terminal.correlation_id == committed.payload["decision_id"]
    assert [event.event_type for event in action_lifecycle] == [
        "action.queued",
        "action.started",
        "action.completed",
    ]
    assert action_lifecycle[1].causation_id == action_lifecycle[0].event_id
    assert action_lifecycle[2].causation_id == action_lifecycle[1].event_id
    assert {
        (link["goal_id"], link["kind"])
        for link in terminal.payload["goal_links"]
    } >= {("goal-destination", "contextual")}


def test_controller_and_system1_origins_and_preemption_are_explicit() -> None:
    planning = create_runner(
        load_scenario(SCENARIO_FIXTURES / "scripted-tool-cognition.json"),
        run_id="planner-lineage",
        model_client=ScriptedModelClient(
            (
                ModelTurn(
                    text=None,
                    tool_calls=(
                        ModelToolCall(
                            call_id="tool-call-1",
                            name="navigate_to",
                            arguments={"target_id": "desk-1"},
                        ),
                    ),
                    finish_reason="tool_calls",
                    provider="scripted",
                    model="scripted-v1",
                    latency_ms=0,
                ),
            )
        ),
    )
    planning.run_for(1)
    controller_action = next(
        event
        for event in planning.events.events
        if event.event_type == "action.queued"
        and event.payload["action_origin"] == "controller"
    )
    assert controller_action.payload["plan_id"] == "plan-00000001"
    assert controller_action.payload["plan_revision"] == 1

    survival = create_runner(
        load_scenario(EXAMPLE_SCENARIOS / "system1-preemption.json"),
        run_id="system1-lineage",
    )
    survival.run_for(1)
    events = survival.events.events
    cancelled = [
        event for event in events if event.event_type == "action.cancelled"
    ]
    correction = next(
        event
        for event in events
        if event.event_type == "action.queued"
        and event.payload["action_origin"] == "system1"
    )
    assert len(cancelled) == 2
    assert {
        event.payload["reason"] for event in cancelled
    } == {"system1_preemption"}
    assert correction.payload["plan_id"] is None
    assert correction.payload["action"] == "EAT"


def test_failed_action_and_plan_clearing_do_not_create_success() -> None:
    source = _controller_scenario().model_dump(mode="json")
    source["entities"][0]["components"].pop("controller")
    source["entities"][0]["components"].pop("goals")
    source["entities"][0]["components"]["plan"] = {
        "queue": [
            {"action": "WORK"},
            {"action": "IDLE", "duration": 5},
        ]
    }
    runner = create_runner(
        ScenarioDefinition.model_validate(source),
        run_id="failed-lineage",
    )

    runner.run_for(1)

    failed = next(
        event for event in runner.events.events if event.event_type == "action.failed"
    )
    cancelled = next(
        event
        for event in runner.events.events
        if event.event_type == "action.cancelled"
    )
    assert failed.payload["reason"] == "duration_required"
    assert cancelled.payload["started"] is False
    assert not any(
        event.event_type == "action.completed"
        and event.payload["action_id"] == failed.payload["action_id"]
        for event in runner.events.events
    )
    plan = runner.registry.get_component("character", PlanComponent)
    assert plan.current is None
    assert plan.queue == []


def test_action_lineage_is_projected_with_goal_links_and_episode(
    tmp_path: Path,
) -> None:
    database = tmp_path / "action-lineage.sqlite3"
    scenario = _controller_scenario()
    runner = create_runner(
        scenario,
        run_id="persisted-lineage",
        model_client=_move_client(),
    )
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(3)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    action = connection.execute(
        """
        SELECT action_id, origin, status, plan_revision, action_json
        FROM action_instances
        WHERE run_id = ? AND origin = 'controller'
        """,
        ("persisted-lineage",),
    ).fetchone()
    assert action is not None
    action_id = action[0]
    assert action[1:4] == ("controller", "completed", 1)
    assert json.loads(action[4])["tool_call_id"] == "tool-call-1"
    transitions = connection.execute(
        """
        SELECT to_status FROM action_transitions
        WHERE run_id = ? AND action_id = ?
        ORDER BY rowid
        """,
        ("persisted-lineage", action_id),
    ).fetchall()
    assert [row[0] for row in transitions] == [
        "queued",
        "started",
        "completed",
    ]
    goal_link = connection.execute(
        """
        SELECT goal_id, link_kind FROM goal_action_links
        WHERE run_id = ? AND action_id = ? AND goal_id = 'goal-destination'
        """,
        ("persisted-lineage", action_id),
    ).fetchone()
    assert goal_link == ("goal-destination", "contextual")
    episode = connection.execute(
        """
        SELECT terminal_status, elapsed_simulation_time, source_event_ids_json
        FROM action_episodes
        WHERE run_id = ? AND action_id = ?
        """,
        ("persisted-lineage", action_id),
    ).fetchone()
    assert episode is not None
    assert episode[0] == "completed"
    assert episode[1] == 2.0
    assert len(json.loads(episode[2])) >= 4
    relation_count = connection.execute(
        """
        SELECT COUNT(*) FROM record_relations
        WHERE run_id = ? AND target_type = 'action' AND target_id = ?
        """,
        ("persisted-lineage", action_id),
    ).fetchone()[0]
    connection.close()
    assert relation_count >= 4


def test_plan_component_exposes_action_instance_without_changing_vocabulary() -> None:
    runner = create_runner(
        _controller_scenario(),
        run_id="envelope-compatibility",
        model_client=_move_client(),
    )
    runner.run_for(1)

    plan = runner.registry.get_component("character", PlanComponent)
    queued = plan.queue[0]
    assert isinstance(queued, ActionInstance)
    assert queued.origin is ActionOrigin.CONTROLLER
    assert queued.specification is not None
    assert queued.specification.action is ActionType.NAVIGATE


def test_plan_revision_retains_plan_identity_and_versions_new_actions() -> None:
    runner = SimulationRunner(
        RunConfiguration(seed=1, run_id="plan-revision")
    )
    runner.registry.create_entity("character")
    plan = PlanComponent()
    runner.registry.add_component("character", plan)

    first = queue_plan_actions(
        runner.context,
        "character",
        plan,
        [PlanAction(ActionType.IDLE, duration=1)],
        origin=ActionOrigin.SCENARIO,
    )[0]
    second = queue_plan_actions(
        runner.context,
        "character",
        plan,
        [PlanAction(ActionType.IDLE, duration=2)],
        origin=ActionOrigin.OPERATOR,
    )[0]

    assert first.plan_id == second.plan_id == "plan-00000001"
    assert first.plan_revision == 1
    assert second.plan_revision == 2
    assert [event.event_type for event in runner.events.events] == [
        "plan.created",
        "action.queued",
        "plan.revised",
        "action.queued",
    ]
