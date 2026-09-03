import pytest

from stage0_sim.application.checkpoints import (
    CheckpointCompatibilityError,
    CheckpointState,
    validate_checkpoint_state,
)


def test_checkpoint_integrity_is_fail_closed() -> None:
    state = CheckpointState(
        prepared_scenario={},
        runner={},
        registry={},
        collector={},
        integrity="0" * 64,
    )

    with pytest.raises(
        CheckpointCompatibilityError,
        match="integrity validation failed",
    ):
        validate_checkpoint_state(state)
