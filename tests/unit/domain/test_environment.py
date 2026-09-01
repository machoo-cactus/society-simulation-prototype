from datetime import UTC, datetime

import pytest

from stage0_sim.application.environment import (
    EnvironmentAccessRequest,
    EnvironmentInformationService,
)
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.application.telemetry import build_runtime_snapshot
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import AffordanceExecutionComponent, SpatialLocationComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import (
    EnvironmentAvailabilityRegistry,
    SurfaceConditionRegistry,
    WeatherCondition,
    WeatherEffects,
    WeatherRuntime,
    WeatherState,
    WeatherTimeline,
    WeatherTransition,
    WeeklyOpeningWindow,
    WeeklySchedule,
    WetnessBand,
    wetness_band,
)
from stage0_sim.domain.world import SpatialScale, WorldLocation


def _weather(
    condition: WeatherCondition,
    *,
    precipitation: float = 0.0,
) -> WeatherState:
    return WeatherState(
        condition=condition,
        temperature_c=18,
        precipitation_mm_per_hour=precipitation,
        wind_speed_mps=3,
        wind_direction_degrees=90,
        visibility_meters=5000,
    )


def test_weather_timeline_and_wetness_bands_are_deterministic() -> None:
    clear = _weather(WeatherCondition.CLEAR)
    rain = _weather(WeatherCondition.RAIN, precipitation=4)
    timeline = WeatherTimeline(
        clear,
        (WeatherTransition(60, rain), WeatherTransition(120, clear)),
    )

    assert timeline.state_at(59) == clear
    assert timeline.state_at(60) == rain
    assert timeline.state_at(999) == clear
    assert wetness_band(0.0) is WetnessBand.DRY
    assert wetness_band(0.2) is WetnessBand.DAMP
    assert wetness_band(0.5) is WetnessBand.WET
    assert wetness_band(0.9) is WetnessBand.SOAKED


def test_weather_transition_times_must_be_unique_and_increasing() -> None:
    clear = _weather(WeatherCondition.CLEAR)

    with pytest.raises(ValueError, match="unique and increasing"):
        WeatherTimeline(
            clear,
            (WeatherTransition(20, clear), WeatherTransition(10, clear)),
        )


def test_weekly_schedule_supports_daytime_and_overnight_windows() -> None:
    schedule = WeeklySchedule(
        (
            WeeklyOpeningWindow(frozenset({0}), 9 * 60, 17 * 60),
            WeeklyOpeningWindow(frozenset({4}), 22 * 60, 2 * 60),
        )
    )

    assert schedule.is_open(datetime(2026, 8, 31, 12, tzinfo=UTC))
    assert not schedule.is_open(datetime(2026, 8, 31, 18, tzinfo=UTC))
    assert schedule.is_open(datetime(2026, 9, 5, 1, tzinfo=UTC))
    assert schedule.next_transition(
        datetime(2026, 8, 31, 12, tzinfo=UTC)
    ) == datetime(2026, 8, 31, 17, tzinfo=UTC)


def test_environment_information_uses_one_policy_boundary() -> None:
    registry = Registry()
    registry.create_entity("alex")
    registry.add_component(
        "alex",
        SpatialLocationComponent(
            WorldLocation(
                scale=SpatialScale.CITY,
                place_id="city",
                edge_id="road",
                edge_progress=0.5,
            )
        ),
    )
    registry.set_resource(
        SimulationCalendar(datetime(2026, 8, 31, 8, tzinfo=UTC))
    )
    registry.set_resource(
        WeatherRuntime(
            WeatherTimeline(_weather(WeatherCondition.CLEAR)),
            {WeatherCondition.CLEAR: WeatherEffects()},
        )
    )
    surfaces = SurfaceConditionRegistry()
    surfaces.set_value("edge:road", 0.4)
    registry.set_resource(surfaces)
    registry.set_resource(EnvironmentAvailabilityRegistry())

    result = EnvironmentInformationService(registry).query("alex", 60)

    assert set(result.values) == {
        "time",
        "weather",
        "surface_conditions",
        "availability",
    }
    assert result.values["surface_conditions"] == {
        "surface_id": "edge:road",
        "wetness": 0.4,
        "band": "WET",
    }
    assert result.unavailable_topics == ()


def test_environment_policy_reports_denied_topics_explicitly() -> None:
    class TimeOnlyPolicy:
        def allowed_topics(
            self,
            registry: Registry,
            request: EnvironmentAccessRequest,
        ) -> frozenset[str]:
            del registry
            return request.topics & {"time"}

    registry = Registry()
    registry.create_entity("alex")
    registry.set_resource(
        SimulationCalendar(datetime(2026, 8, 31, 8, tzinfo=UTC))
    )
    registry.set_resource(
        WeatherRuntime(
            WeatherTimeline(_weather(WeatherCondition.CLEAR)),
            {},
        )
    )

    result = EnvironmentInformationService(
        registry,
        TimeOnlyPolicy(),
    ).query("alex", 0, frozenset({"time", "weather"}))

    assert set(result.values) == {"time"}
    assert result.unavailable_topics == ("weather",)


def test_scenario_weather_updates_wetness_and_schedule_availability() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "environment",
            "dt": 60,
            "calendar": {
                "start_datetime": "2026-08-31T08:59:00+00:00",
                "update_interval_seconds": 900,
            },
            "weather": {
                "initial": {
                    "condition": "CLEAR",
                    "temperature_c": 20,
                },
                "transitions": [
                    {
                        "at_seconds": 60,
                        "state": {
                            "condition": "RAIN",
                            "temperature_c": 18,
                            "precipitation_mm_per_hour": 10,
                            "visibility_meters": 1000,
                        },
                    }
                ],
                "effects": {
                    "RAIN": {
                        "walking_speed_multiplier": 0.8,
                        "cycling_speed_multiplier": 0.7,
                        "visibility_multiplier": 0.5,
                        "wetness_gain_per_mm_hour_second": 0.001,
                    }
                },
            },
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
                "stations": [
                    {
                        "id": "shop",
                        "name": "Shop",
                        "position": {"x": 0, "y": 0},
                        "supported_actions": ["READ"],
                        "environment": {
                            "schedule": {
                                "windows": [
                                    {
                                        "weekdays": ["MONDAY"],
                                        "opens": "09:00",
                                        "closes": "09:01",
                                    }
                                ]
                            },
                            "closed_weather": ["STORM"],
                        },
                    }
                ],
            },
        }
    )
    runner = create_runner(scenario)

    runner.run_for(2)

    weather = runner.registry.get_resource(WeatherRuntime)
    surfaces = runner.registry.get_resource(SurfaceConditionRegistry)
    availability = runner.registry.get_resource(EnvironmentAvailabilityRegistry)
    event_types = [event.event_type for event in runner.events.events]
    assert weather.current.condition is WeatherCondition.RAIN
    assert surfaces.value("city:exterior") == pytest.approx(0.6)
    assert availability.state("shop").available is False
    assert availability.state("shop").reason.value == "closed_by_schedule"
    assert "weather.changed" in event_types
    assert "availability.changed" in event_types
    snapshot = build_runtime_snapshot(runner)
    environment = snapshot["environment"]
    assert isinstance(environment, dict)
    assert environment["weather"]["condition"] == "RAIN"
    assert environment["surface_conditions"][0]["band"] == "WET"
    assert environment["availability"][0]["reason"] == "closed_by_schedule"


def test_large_tick_integrates_every_crossed_weather_period() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "crossed-weather",
            "dt": 180,
            "weather": {
                "initial": {
                    "condition": "CLEAR",
                    "temperature_c": 20,
                },
                "transitions": [
                    {
                        "at_seconds": 60,
                        "state": {
                            "condition": "RAIN",
                            "temperature_c": 15,
                            "precipitation_mm_per_hour": 10,
                        },
                    },
                    {
                        "at_seconds": 120,
                        "state": {
                            "condition": "CLEAR",
                            "temperature_c": 18,
                        },
                    },
                ],
                "effects": {
                    "RAIN": {
                        "wetness_gain_per_mm_hour_second": 0.001,
                    },
                    "CLEAR": {
                        "base_drying_per_second": 0,
                        "temperature_drying_per_degree_second": 0,
                    },
                },
            },
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)

    weather_events = [
        event
        for event in runner.events.events
        if event.event_type == "weather.changed"
    ]
    surfaces = runner.registry.get_resource(SurfaceConditionRegistry)
    assert [
        event.payload["boundary_simulation_time"]
        for event in weather_events
    ] == [60.0, 120.0]
    assert surfaces.value("city:exterior") == pytest.approx(0.6)


def test_affordance_started_before_closing_can_finish() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "finish-after-closing",
            "dt": 60,
            "calendar": {
                "start_datetime": "2026-08-31T08:58:00+00:00",
            },
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
                        "id": "cafe",
                        "name": "Cafe",
                        "position": {"x": 0, "y": 0},
                        "actions": [
                            {
                                "action": "EAT",
                                "duration": 120,
                                "effect": {"satiety_target": 80},
                            }
                        ],
                        "environment": {
                            "schedule": {
                                "windows": [
                                    {
                                        "weekdays": ["MONDAY"],
                                        "opens": "08:00",
                                        "closes": "09:00",
                                    }
                                ]
                            }
                        },
                    }
                ],
            },
            "entities": [
                {
                    "id": "alex",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {
                            "satiety": 10,
                            "energy": 80,
                            "stress": 20,
                        },
                    },
                }
            ],
        }
    )
    runner = create_runner(scenario)

    runner.run_for(1)
    assert runner.registry.has_component(
        "alex",
        AffordanceExecutionComponent,
    )

    runner.run_for(1)

    assert not runner.registry.has_component(
        "alex",
        AffordanceExecutionComponent,
    )
    assert (
        runner.registry.get_resource(
            EnvironmentAvailabilityRegistry
        ).state("cafe").reason.value
        == "closed_by_schedule"
    )
    assert any(
        event.event_type == "affordance.completed"
        for event in runner.events.events
    )


def test_schedule_without_calendar_is_rejected() -> None:
    with pytest.raises(ValueError, match="schedules require a calendar"):
        ScenarioDefinition.model_validate(
            {
                "name": "invalid-schedule",
                "world": {
                    "width": 1,
                    "height": 1,
                    "zones": [],
                    "stations": [
                        {
                            "id": "shop",
                            "name": "Shop",
                            "position": {"x": 0, "y": 0},
                            "supported_actions": ["READ"],
                            "environment": {
                                "schedule": {
                                    "windows": [
                                        {
                                            "weekdays": ["MONDAY"],
                                            "opens": "09:00",
                                            "closes": "17:00",
                                        }
                                    ]
                                }
                            },
                        }
                    ],
                },
            }
        )
