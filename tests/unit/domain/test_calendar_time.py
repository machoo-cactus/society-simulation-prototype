from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import build_runtime_snapshot


def _skip_turn() -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id="skip",
                name="skip",
                arguments={"reconsider_after_seconds": 999},
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="scripted-v1",
        latency_ms=0,
    )


def _scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "calendar-test",
            "dt": 1,
            "calendar": {
                "start_datetime": "2026-08-30T08:29:58+08:00",
                "update_interval_seconds": 2,
            },
            "cognition": {},
            "world": {
                "width": 1,
                "height": 1,
                "zones": [
                    {
                        "id": "room",
                        "name": "Room",
                        "type": "ROOM",
                        "tiles": [{"x": 0, "y": 0}],
                    }
                ],
                "stations": [],
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 80,
                            "energy": 80,
                            "stress": 20,
                        },
                        "character_slot": {"label": "Tester"},
                        "metadata": {"display_name": "Alex"},
                        "controller": {"enabled": True},
                    },
                }
            ],
        }
    )


def test_calendar_time_reaches_controller_and_telemetry() -> None:
    runner = create_runner(
        _scenario(),
        model_client=ScriptedModelClient((_skip_turn(), _skip_turn())),
    )

    runner.run_for(2)

    updates = [
        event for event in runner.events.events
        if event.event_type == "time.updated"
    ]
    requests = [
        event for event in runner.events.events
        if event.event_type == "cognition.requested"
    ]
    snapshot = build_runtime_snapshot(runner)

    assert updates[0].payload["datetime"] == "2026-08-30T08:30:00+08:00"
    assert requests[-1].payload["trigger"] == "time_update"
    assert snapshot["calendar_time"] == {
        "datetime": "2026-08-30T08:30:00+08:00",
        "date": "2026-08-30",
        "time": "08:30:00+08:00",
        "weekday": "Sunday",
        "period": "morning",
    }
    assert snapshot["environment"]["time"] == snapshot["calendar_time"]


def test_calendar_start_requires_utc_offset() -> None:
    payload = _scenario().model_dump(mode="json")
    payload["calendar"]["start_datetime"] = "2026-08-30T08:00:00"

    try:
        ScenarioDefinition.model_validate(payload)
    except ValueError as error:
        assert "UTC offset" in str(error)
    else:
        raise AssertionError("naive calendar start was accepted")
