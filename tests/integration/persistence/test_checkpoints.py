from pathlib import Path

import pytest

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.data_management import PersistedRunFilter
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.runner import RunnerStatus
from stage0_sim.application.scenario import load_scenario
from tests.helpers.paths import CATALOG_CHARACTERS, CATALOG_SCENARIOS


def _manager(database: Path) -> SimulationManager:
    return SimulationManager(
        SQLiteDatasetStore(database),
        character_library=FileSystemCharacterLibrary(CATALOG_CHARACTERS),
    )


@pytest.mark.asyncio
async def test_head_checkpoint_resumes_and_historical_checkpoint_branches(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    manager = _manager(database)
    scenario_id = await manager.add_scenario(
        load_scenario(CATALOG_SCENARIOS / "needs-and-preemption.json")
    )
    run_id = await manager.start_run(scenario_id, realtime=False)
    manager.pause(run_id)
    await manager.step(run_id)
    first = manager.save_checkpoint(run_id, label="before-step")
    _, first_state = manager.dataset_store.load_checkpoint(
        first.checkpoint_id
    )
    expected_action_episodes = first_state.collector["action_episodes"]
    expected_goal_episodes = first_state.collector["goal_episodes"]
    assert expected_action_episodes
    manager.set_speed(run_id, 2.0)
    second = manager.save_checkpoint(run_id, label="after-step")
    expected_random_values = [
        manager.get_run(run_id).runner.rng.randrange(1_000_000)
        for _ in range(5)
    ]

    checkpoints = manager.list_checkpoints(run_id)
    assert [(item.label, item.is_head) for item in checkpoints] == [
        ("after-step", True),
        ("before-step", False),
    ]
    await manager.close()

    reopened = _manager(database)
    continued = await reopened.restore_checkpoint(second.checkpoint_id)
    assert continued.run_id == run_id
    assert continued.branched is False
    assert reopened.get_run(run_id).runner.status is RunnerStatus.PAUSED
    assert reopened.get_run(run_id).runner.clock.tick == 1
    assert [
        reopened.get_run(run_id).runner.rng.randrange(1_000_000)
        for _ in range(5)
    ] == expected_random_values

    await reopened.stop_run(run_id)
    branched = await reopened.restore_checkpoint(first.checkpoint_id)
    assert branched.run_id != run_id
    assert branched.branched is True
    branch = reopened.get_run(branched.run_id)
    assert branch.runner.status is RunnerStatus.PAUSED
    assert branch.runner.clock.tick == 1
    branch_capture = branch.collector.checkpoint_state()
    assert branch_capture["action_episodes"] == expected_action_episodes
    assert branch_capture["goal_episodes"] == expected_goal_episodes
    assert branch_capture["sequence"] < first.dataset_sequence

    summaries = {
        summary.run_id: summary
        for summary in reopened.persisted_runs().runs
    }
    branch_summary = summaries[branched.run_id]
    assert branch_summary.lineage_kind == "branch"
    assert branch_summary.root_run_id == run_id
    assert branch_summary.parent_run_id == run_id
    assert branch_summary.parent_checkpoint_id == first.checkpoint_id
    assert {
        summary.run_id
        for summary in reopened.persisted_runs(
            filters=PersistedRunFilter(lineage_kinds=("branch",))
        ).runs
    } == {branched.run_id}

    await reopened.stop_run(branched.run_id)
    await reopened.close()


@pytest.mark.asyncio
async def test_checkpoint_requires_paused_settled_runner(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path / "boundary.sqlite3")
    scenario_id = await manager.add_scenario(
        load_scenario(CATALOG_SCENARIOS / "needs-and-preemption.json")
    )
    run_id = await manager.start_run(scenario_id, realtime=False)

    with pytest.raises(
        RuntimeError,
        match="requires a paused simulation",
    ):
        manager.save_checkpoint(run_id)

    await manager.stop_run(run_id)
    await manager.close()
