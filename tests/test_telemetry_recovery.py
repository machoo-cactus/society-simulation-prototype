from stage0_sim.application.runner import RunConfiguration, SimulationRunner
from stage0_sim.application.telemetry import TelemetryBroker


def test_broker_keeps_latest_snapshot_outside_replay_history() -> None:
    runner = SimulationRunner(RunConfiguration(seed=1, run_id="telemetry"))
    broker = TelemetryBroker(runner, history_limit=2)
    runner.start()

    for _ in range(20):
        broker.publish_snapshot()

    assert len(broker.messages_after(0)) == 1
    assert broker.latest_snapshot is not None
    assert broker.snapshot_revision == 20
    assert broker.latest_sequence == 1


def test_broker_reports_expired_recovery_cursor() -> None:
    runner = SimulationRunner(RunConfiguration(seed=1, run_id="telemetry"))
    broker = TelemetryBroker(runner, history_limit=2)
    runner.start()
    runner.events.emit(
        "test.one",
        simulation_tick=0,
        simulation_time=0,
    )
    runner.events.emit(
        "test.two",
        simulation_tick=0,
        simulation_time=0,
    )

    assert broker.oldest_sequence == 2
    assert not broker.can_resume_after(0)
    assert broker.can_resume_after(1)
    assert broker.domain_event_offset == 3
