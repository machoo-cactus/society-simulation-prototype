from dataclasses import dataclass

from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.environment import (
    AvailabilityReason,
    AvailabilityState,
    EnvironmentAvailabilityRegistry,
    EnvironmentAvailabilityRules,
    SurfaceConditionRegistry,
    WeatherEffects,
    WeatherRuntime,
    WeatherState,
    wetness_band,
)
from stage0_sim.domain.systems import SystemContext


@dataclass(frozen=True, slots=True)
class WeatherUpdateSystem:
    name: str = "weather_update"
    order: int = 70

    def update(self, context: SystemContext) -> None:
        runtime = context.registry.get_resource(WeatherRuntime)
        transitions = runtime.timeline.transitions
        while (
            runtime.transition_index < len(transitions)
            and transitions[runtime.transition_index].at_seconds
            <= context.clock.simulation_time
        ):
            transition = transitions[runtime.transition_index]
            previous = runtime.current
            runtime.current = transition.state
            runtime.transition_index += 1
            context.events.emit(
                "weather.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                payload={
                    "previous": previous.to_payload(),
                    "current": runtime.current.to_payload(),
                    "boundary_simulation_time": transition.at_seconds,
                },
            )


@dataclass(frozen=True, slots=True)
class SurfaceConditionSystem:
    name: str = "surface_conditions"
    order: int = 75

    def update(self, context: SystemContext) -> None:
        weather = context.registry.get_resource(WeatherRuntime)
        surfaces = context.registry.get_resource(SurfaceConditionRegistry)
        rules = context.registry.get_resource(EnvironmentAvailabilityRules)
        surface_ids = {"city:exterior"}
        surface_ids.update(
            (
                f"edge:{rule.resource_id}"
                if rule.resource_kind == "transport_edge"
                else f"place:{rule.resource_id}"
            )
            for rule in rules.rules
            if rule.resource_kind in {"transport_edge", "outdoor"}
        )
        for surface_id in sorted(surface_ids):
            before = surfaces.value(surface_id)
            surfaces.set_value(
                surface_id,
                _wetness_after_interval(
                    weather,
                    before,
                    max(0.0, context.clock.simulation_time - context.clock.dt),
                    context.clock.simulation_time,
                ),
            )
            after = surfaces.value(surface_id)
            before_band = wetness_band(before)
            after_band = wetness_band(after)
            if before_band is after_band:
                continue
            context.events.emit(
                "surface_condition.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                payload={
                    "surface_id": surface_id,
                    "previous_wetness": before,
                    "wetness": after,
                    "previous_band": before_band.value,
                    "band": after_band.value,
                    "weather_condition": weather.current.condition.value,
                },
            )


def _wetness_after_interval(
    runtime: WeatherRuntime,
    initial: float,
    start: float,
    end: float,
) -> float:
    value = initial
    cursor = start
    state = runtime.timeline.state_at(start)
    boundaries = [
        transition
        for transition in runtime.timeline.transitions
        if start < transition.at_seconds <= end
    ]
    for transition in boundaries:
        value = _apply_weather_segment(
            runtime,
            state,
            value,
            transition.at_seconds - cursor,
        )
        cursor = transition.at_seconds
        state = transition.state
    return _apply_weather_segment(runtime, state, value, end - cursor)


def _apply_weather_segment(
    runtime: WeatherRuntime,
    state: WeatherState,
    value: float,
    duration: float,
) -> float:
    effects = runtime.effects_by_condition.get(state.condition)
    if effects is None:
        effects = WeatherEffects()
    if state.raining:
        delta = (
            state.precipitation_mm_per_hour
            * effects.wetness_gain_per_mm_hour_second
            * duration
        )
    else:
        drying = (
            effects.base_drying_per_second
            + state.wind_speed_mps * effects.wind_drying_per_mps_second
            + max(0.0, state.temperature_c)
            * effects.temperature_drying_per_degree_second
        )
        delta = -drying * duration
    return min(1.0, max(0.0, value + delta))


@dataclass(frozen=True, slots=True)
class EnvironmentAvailabilitySystem:
    name: str = "environment_availability"
    order: int = 80

    def update(self, context: SystemContext) -> None:
        rules = context.registry.get_resource(EnvironmentAvailabilityRules)
        states = context.registry.get_resource(EnvironmentAvailabilityRegistry)
        calendar = (
            context.registry.get_resource(SimulationCalendar)
            if context.registry.has_resource(SimulationCalendar)
            else None
        )
        weather = (
            context.registry.get_resource(WeatherRuntime)
            if context.registry.has_resource(WeatherRuntime)
            else None
        )
        current_datetime = (
            calendar.datetime_at(context.clock.simulation_time)
            if calendar is not None
            else None
        )
        for rule in rules.rules:
            next_transition = None
            if not rule.base_available:
                current = AvailabilityState(
                    False,
                    AvailabilityReason.BASE_UNAVAILABLE,
                )
            elif (
                weather is not None
                and weather.current.condition in rule.closed_weather
            ):
                current = AvailabilityState(
                    False,
                    AvailabilityReason.CLOSED_BY_WEATHER,
                )
            elif (
                rule.schedule is not None
                and current_datetime is not None
                and not rule.schedule.is_open(current_datetime)
            ):
                next_transition = rule.schedule.next_transition(
                    current_datetime
                ).isoformat(timespec="minutes")
                current = AvailabilityState(
                    False,
                    AvailabilityReason.CLOSED_BY_SCHEDULE,
                    next_transition,
                )
            else:
                if rule.schedule is not None and current_datetime is not None:
                    next_transition = rule.schedule.next_transition(
                        current_datetime
                    ).isoformat(timespec="minutes")
                current = AvailabilityState(
                    True,
                    AvailabilityReason.OPEN,
                    next_transition,
                )
            previous = states.states.get(rule.resource_id)
            states.states[rule.resource_id] = current
            if previous is None or previous == current:
                continue
            context.events.emit(
                "availability.changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                payload={
                    "resource_id": rule.resource_id,
                    "resource_kind": rule.resource_kind,
                    "previous": previous.to_payload(),
                    "current": current.to_payload(),
                    "weather_condition": (
                        weather.current.condition.value
                        if weather is not None
                        else None
                    ),
                },
            )
