import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.context import build_character_observation
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.goals import retire_goal
from stage0_sim.application.scenario import (
    GoalsComponentDefinition,
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.domain.components import GoalComponent, GoalStatus


def _scenario(
    goals: list[dict[str, object]],
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
                        "goals": {
                            "goals": goals,
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


def test_goal_schema_is_strict_and_rejects_textual_lists() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        GoalsComponentDefinition.model_validate(
            {"daily_goals": ["Finish the report"]}
        )
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        GoalsComponentDefinition.model_validate(
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
        GoalsComponentDefinition.model_validate(
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


@pytest.mark.parametrize(
    "field",
    ["hydration", "social_connection", "happiness", "fear"],
)
def test_goal_schema_accepts_expanded_homeostasis_fields(field: str) -> None:
    scenario = _scenario(
        [
            {
                "id": f"track-{field}",
                "description": f"Track {field}",
                "criteria": [
                    {
                        "type": "state_comparison",
                        "component": "homeostasis",
                        "field": field,
                        "comparator": "gte",
                        "value": 50,
                    }
                ],
            }
        ]
    )

    criterion = scenario.entities[0].components["goals"]["goals"][0]["criteria"][0]
    assert criterion["field"] == field


def test_scenario_attaches_only_structured_goals() -> None:
    scenario = _scenario(
        [{"id": "report", "description": "Finish the report", "priority": 8}]
    )
    first = create_runner(scenario, run_id="first")
    second = create_runner(scenario, run_id="second")

    first_goals = first.registry.get_component("alice", GoalComponent).goals
    second_goals = second.registry.get_component("alice", GoalComponent).goals
    assert [goal.definition.id for goal in first_goals] == [
        goal.definition.id for goal in second_goals
    ]
    assert [goal.status for goal in first_goals] == [GoalStatus.PENDING]


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
                    "interaction_type": "speech",
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
        "action.completed",
        simulation_tick=0,
        simulation_time=0,
        agent_id="alice",
        payload={"action": "WORK", "target": "office"},
    )
    for _ in range(2):
        runner.events.emit(
            "speech.started",
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


def test_controller_context_exposes_structured_goals() -> None:
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
        ),
        run_id="context",
    )

    observation = build_character_observation(runner.context, "alice")
    assert observation.structured_goals[0].id == "context-goal"
    assert observation.structured_goals[0].status == "pending"
    assert observation.structured_goals[0].priority == 9


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

    assert raw_definition_count == 1
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
