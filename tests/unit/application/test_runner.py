from dataclasses import dataclass

from stage0_sim.application.runner import (
    RunConfiguration,
    RunnerStatus,
    SimulationRunner,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemContext, SystemExecutor


@dataclass(slots=True)
class Samples:
    values: list[int]


class SamplingSystem:
    name = "sampling"
    order = 10

    def update(self, context: SystemContext) -> None:
        for _, samples in context.registry.query(Samples):
            samples.values.append(context.rng.randrange(1_000_000))


class RecordingSystem:
    def __init__(self, name: str, order: int, calls: list[str]) -> None:
        self.name = name
        self.order = order
        self._calls = calls

    def update(self, context: SystemContext) -> None:
        self._calls.append(f"{context.clock.tick}:{self.name}")


def build_sampling_runner(run_id: str) -> SimulationRunner:
    registry = Registry()
    entity_id = registry.create_entity("agent")
    registry.add_component(entity_id, Samples([]))
    systems = SystemExecutor()
    systems.add(SamplingSystem())
    return SimulationRunner(
        RunConfiguration(seed=42, run_id=run_id),
        registry=registry,
        systems=systems,
    )


def test_same_seed_produces_identical_state_and_canonical_events() -> None:
    first = build_sampling_runner("first")
    second = build_sampling_runner("second")

    first.run_for(5)
    second.run_for(5)

    assert first.registry.get_component("agent", Samples).values == second.registry.get_component(
        "agent", Samples
    ).values
    assert [event.canonical_dict() for event in first.events.events] == [
        event.canonical_dict() for event in second.events.events
    ]
    tick_events = [event for event in first.events.events if event.event_type == "simulation.tick"]
    assert [event.simulation_tick for event in tick_events] == [1, 2, 3, 4, 5]
    assert first.clock.simulation_time == 5.0


def test_systems_execute_by_order_then_registration_order() -> None:
    calls: list[str] = []
    systems = SystemExecutor()
    systems.add(RecordingSystem("late", 20, calls))
    systems.add(RecordingSystem("first-equal", 10, calls))
    systems.add(RecordingSystem("second-equal", 10, calls))
    runner = SimulationRunner(RunConfiguration(seed=0), systems=systems)

    runner.run_for(1)

    assert calls == ["1:first-equal", "1:second-equal", "1:late"]


def test_pause_resume_single_step_and_speed_controls() -> None:
    runner = SimulationRunner(RunConfiguration(seed=0))
    runner.start()
    runner.pause()

    runner.single_step()
    assert runner.status is RunnerStatus.PAUSED
    assert runner.clock.tick == 1

    runner.set_speed(4.0)
    runner.resume()
    runner.step()

    assert runner.speed == 4.0
    assert runner.clock.tick == 2
    assert runner.status is RunnerStatus.RUNNING


def test_registry_queries_entities_in_stable_order() -> None:
    registry = Registry()
    registry.create_entity("z-agent")
    registry.create_entity("a-agent")
    registry.add_component("z-agent", Samples([]))
    registry.add_component("a-agent", Samples([]))

    assert [entity_id for entity_id, _ in registry.query(Samples)] == [
        "a-agent",
        "z-agent",
    ]
