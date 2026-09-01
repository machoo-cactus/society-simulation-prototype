import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.context import build_character_observation
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.goals import retire_goal
from stage0_sim.application.planning import build_planner_context
from stage0_sim.application.scenario import (
    PlannerComponentDefinition,
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.domain.components import (
    GoalComponent,
    GoalStatus,
    PlannerComponent,
)
from stage0_sim.domain.systems.spatial_context import local_world_for_agent


def _scenario(
    goals: list[dict[str, object]],
    *,
    daily_goals: list[str] | None = None,
    current_priorities: list[str] | None = None,
) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "structured-goals",
            "seed": 17,
            "dt": 1,
            "items": [{"id": "coin", "name": "Coin", "unit": "coin"}],
            "world": {
                "width": 2,
                "height": 1,
                "zones": [
                    {
                        "id": "office",
                        "name": "Office",
                        "type": "OFFICE",
                        "tiles": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
                    }
                ],
            },
            "entities": [
                {
                    "id": "alice",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "activity": {"type": "IDLE"},
                        "possessions": {"holdings": {"coin": 2}},
                        "metadata": {"display_name": "Alice"},
                        "controller": {"enabled": False},
                        "planner": {
                            "daily_goals": daily_goals or [],
                            "current_priorities": current_priorities or [],
                            "goals": goals,
                            "needs_plan": False,
                        },
                    },
                },
                {
                    "id": "bob",
                    "components": {
                        "position": {"x": 1, "y": 0},
                        "metadata": {"display_name": "Bob"},
                    },
                },
            ],
        }
    )


def test_goal_schema_is_strict_and_legacy_lists_remain_valid() -> None:
    planner = PlannerComponentDefinition.model_validate(
        {
            "daily_goals": ["Finish the report"],
            "current_priorities": ["Stay focused"],
        }
    )

    assert planner.daily_goals == ["Finish the report"]
    assert planner.current_priorities == ["Stay focused"]
    assert planner.goals == []
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        PlannerComponentDefinition.model_validate(
            {
                "goals": [
                    {
                        "id": "unsafe",
                        "description": "Run arbitrary logic",
                        "criteria": [{"type": "expression", "code": "True"}],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="not available"):
        PlannerComponentDefinition.model_validate(
            {
                "goals": [
                    {
                        "id": "unsafe-field",
                        "description": "Read arbitrary state",
                        "criteria": [
                            {
                                "type": "state_comparison",
                                "component": "homeostasis",
                                "field": "__dict__",
                                "comparator": "eq",
                                "value": 1,
                            }
                        ],
                    }
                ]
            }
        )


def test_scenario_attaches_structured_and_deterministic_legacy_goals() -> None:
    scenario = _scenario(
        [{"id": "report", "description": "Finish the report", "priority": 8}],
        daily_goals=["Keep a routine"],
        current_priorities=["Avoid interruptions"],
    )
    first = create_runner(scenario, run_id="first")
    second = create_runner(scenario, run_id="second")

    first_goals = first.registry.get_component("alice", GoalComponent).goals
    second_goals = second.registry.get_component("alice", GoalComponent).goals
    planner = first.registry.get_component("alice", PlannerComponent)

    assert planner.daily_goals == ("Keep a routine", "Finish the report")
    assert planner.current_priorities == ("Avoid interruptions",)
    assert [goal.definition.id for goal in first_goals] == [
        goal.definition.id for goal in second_goals
    ]
    assert [goal.status for goal in first_goals] == [
        GoalStatus.PENDING,
        GoalStatus.ACTIVE,
        GoalStatus.ACTIVE,
    ]
    assert first_goals[1].definition.id.startswith("legacy-daily_goal-")
    assert first_goals[2].definition.id.startswith(
        "legacy-current_priority-"
    )


def test_each_initial_goal_criterion_family_evaluates_deterministically() -> None:
    goals = [
        {
            "id": "event",
            "description": "Observe the custom event",
            "criteria": [
                {
                    "type": "event_match",
                    "event_type": "custom.completed",
                    "payload_subset": {"result": {"ok": True}},
                }
            ],
        },
        {
            "id": "state",
            "description": "Maintain enough energy",
            "criteria": [
                {
                    "type": "state_comparison",
                    "component": "homeostasis",
                    "field": "energy",
                    "comparator": "gte",
                    "value": 50,
                }
            ],
        },
        {
            "id": "location",
            "description": "Be in the office",
            "criteria": [
                {
                    "type": "location_match",
                    "location_id": "office",
                    "location_kind": "zone",
                }
            ],
        },
        {
            "id": "possession",
            "description": "Hold two coins",
            "criteria": [
                {
                    "type": "possession_threshold",
                    "item_id": "coin",
                    "quantity": 2,
                }
            ],
        },
        {
            "id": "action",
            "description": "Complete work",
            "criteria": [
                {
                    "type": "action_outcome",
                    "action": "WORK",
                    "outcome": "completed",
                    "target": "office",
                }
            ],
        },
        {
            "id": "interaction",
            "description": "Talk to Bob twice",
            "criteria": [
                {
                    "type": "interaction_count",
                    "interaction_type": "dialogue",
                    "minimum_count": 2,
                    "target_id": "bob",
                }
            ],
        },
        {
            "id": "time",
            "description": "Reach one simulated second",
            "criteria": [
                {
                    "type": "simulation_time",
                    "simulation_time": 1,
                }
            ],
        },
    ]
    runner = create_runner(_scenario(goals), run_id="criteria")
    runner.events.emit(
        "custom.completed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="alice",
        payload={"result": {"ok": True, "detail": "kept"}},
    )
    runner.events.emit(
        "plan.action_completed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="alice",
        payload={"action": "WORK", "target": "office"},
    )
    for _ in range(2):
        runner.events.emit(
            "dialogue.generated",
            simulation_tick=0,
            simulation_time=0,
            agent_id="alice",
            payload={"target_id": "bob"},
        )

    runner.run_for(1)

    runtime = runner.registry.get_component("alice", GoalComponent)
    assert {goal.definition.id: goal.status for goal in runtime.goals} == {
        goal["id"]: GoalStatus.SUCCEEDED for goal in goals
    }
    event_types = [event.event_type for event in runner.events.events]
    assert event_types.count("goal.activated") == len(goals)
    assert event_types.count("goal.progressed") == len(goals)
    assert event_types.count("goal.succeeded") == len(goals)


def test_failure_expiry_and_retirement_emit_terminal_context() -> None:
    runner = create_runner(
        _scenario(
            [
                {
                    "id": "failed",
                    "description": "Do not encounter a blocker",
                    "criteria": [
                        {
                            "type": "simulation_time",
                            "simulation_time": 100,
                        },
                        {
                            "type": "event_match",
                            "event_type": "work.blocked",
                            "effect": "failure",
                        },
                    ],
                },
                {
                    "id": "expired",
                    "description": "Meet an unavailable condition in time",
                    "deadline_time": 1,
                    "criteria": [
                        {
                            "type": "event_match",
                            "event_type": "never.happens",
                        }
                    ],
                },
                {
                    "id": "retired",
                    "description": "An obsolete goal",
                    "criteria": [
                        {
                            "type": "simulation_time",
                            "simulation_time": 100,
                        }
                    ],
                },
            ]
        ),
        run_id="terminal",
    )
    runner.events.emit(
        "work.blocked",
        simulation_tick=0,
        simulation_time=0,
        agent_id="alice",
    )
    runner.start()
    retire_goal(runner.context, "alice", "retired", "superseded")
    runner.run_for(1)

    goals = runner.registry.get_component("alice", GoalComponent)
    assert goals.get("failed").status is GoalStatus.FAILED
    assert goals.get("expired").status is GoalStatus.EXPIRED
    assert goals.get("retired").status is GoalStatus.RETIRED
    terminal = {
        event.event_type: event
        for event in runner.events.events
        if event.event_type
        in {"goal.failed", "goal.expired", "goal.retired"}
    }
    assert terminal["goal.failed"].payload["evidence"]
    assert terminal["goal.expired"].payload["reason"] == "deadline_reached"
    assert terminal["goal.retired"].payload["reason"] == "superseded"


def test_completion_policy_controls_partial_progress() -> None:
    criteria = [
        {
            "type": "state_comparison",
            "component": "homeostasis",
            "field": "energy",
            "comparator": "gte",
            "value": 50,
        },
        {
            "type": "event_match",
            "event_type": "never.happens",
        },
    ]
    runner = create_runner(
        _scenario(
            [
                {
                    "id": "all",
                    "description": "Require every criterion",
                    "completion_policy": "all",
                    "criteria": criteria,
                },
                {
                    "id": "any",
                    "description": "Require one criterion",
                    "completion_policy": "any",
                    "criteria": criteria,
                },
            ]
        ),
        run_id="completion-policy",
    )

    runner.run_for(1)

    goals = runner.registry.get_component("alice", GoalComponent)
    assert goals.get("all").status is GoalStatus.ACTIVE
    assert goals.get("all").progress == 0.5
    assert goals.get("any").status is GoalStatus.SUCCEEDED
    assert goals.get("any").progress == 1.0


def test_controller_and_planner_context_expose_structured_goals() -> None:
    runner = create_runner(
        _scenario(
            [
                {
                    "id": "context-goal",
                    "description": "Expose this goal",
                    "priority": 9,
                    "tags": ["work"],
                }
            ],
            daily_goals=["Legacy description"],
        ),
        run_id="context",
    )

    observation = build_character_observation(runner.context, "alice")
    planner = runner.registry.get_component("alice", PlannerComponent)
    world = local_world_for_agent(runner.registry, "alice")
    planner_context = build_planner_context(
        runner.context, "alice", world, planner
    )

    assert observation.goals == (
        "Legacy description",
        "Expose this goal",
    )
    assert observation.structured_goals[0].id == "context-goal"
    assert observation.structured_goals[0].status == "pending"
    assert observation.structured_goals[0].priority == 9
    assert planner_context.structured_goals[0].id == "context-goal"


def test_goal_definitions_and_transitions_are_persisted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "goals.sqlite3"
    scenario = _scenario(
        [
            {
                "id": "persisted",
                "description": "Persist completion evidence",
                "criteria": [
                    {
                        "type": "simulation_time",
                        "simulation_time": 1,
                    }
                ],
            }
        ],
        daily_goals=["Legacy persisted goal"],
    )
    runner = create_runner(scenario, run_id="persistence")
    store = SQLiteDatasetStore(database)
    collector = RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )

    runner.run_for(1)
    collector.finalize()
    store.close()

    connection = sqlite3.connect(database)
    goals = connection.execute(
        """
        SELECT goal_id, status, goal_json FROM goals
        WHERE run_id = ? ORDER BY goal_id
        """,
        ("persistence",),
    ).fetchall()
    transitions = connection.execute(
        """
        SELECT from_status, to_status, transition_json
        FROM goal_transitions
        WHERE run_id = ? AND goal_id = ? ORDER BY simulation_tick, rowid
        """,
        ("persistence", "persisted"),
    ).fetchall()
    raw_definition_count = connection.execute(
        """
        SELECT COUNT(*) FROM records
        WHERE run_id = ? AND record_type = 'goal_definition'
        """,
        ("persistence",),
    ).fetchone()[0]
    connection.close()

    assert raw_definition_count == 2
    persisted = next(row for row in goals if row[0] == "persisted")
    assert persisted[1] == "succeeded"
    assert json.loads(persisted[2])["evidence"]
    assert [row[1] for row in transitions] == [
        "active",
        "active",
        "succeeded",
    ]
    assert json.loads(transitions[-1][2])["evidence"]


def test_equivalent_runs_have_identical_goal_results_and_events() -> None:
    scenario = _scenario(
        [
            {
                "id": "deterministic",
                "description": "Match a stable event",
                "criteria": [
                    {
                        "type": "event_match",
                        "event_type": "custom.done",
                    }
                ],
            }
        ],
        daily_goals=["Stable legacy goal"],
    )
    runners = [
        create_runner(scenario, run_id=run_id)
        for run_id in ("deterministic-a", "deterministic-b")
    ]
    for runner in runners:
        runner.events.emit(
            "custom.done",
            simulation_tick=0,
            simulation_time=0,
            agent_id="alice",
        )
        runner.run_for(1)

    assert [
        goal.definition.id
        for goal in runners[0].registry.get_component(
            "alice", GoalComponent
        ).goals
    ] == [
        goal.definition.id
        for goal in runners[1].registry.get_component(
            "alice", GoalComponent
        ).goals
    ]
    assert [
        event.canonical_dict() for event in runners[0].events.events
    ] == [
        event.canonical_dict() for event in runners[1].events.events
    ]
