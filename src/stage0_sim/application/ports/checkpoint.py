from datetime import datetime
from typing import Protocol

from stage0_sim.application.checkpoints import CheckpointState, CheckpointSummary


class CheckpointRepository(Protocol):
    def save_checkpoint(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        label: str | None,
        simulation_tick: int,
        simulation_time: float,
        speed: float,
        event_count: int,
        dataset_sequence: int,
        created_at: datetime,
        state: CheckpointState,
    ) -> CheckpointSummary: ...

    def list_checkpoints(
        self,
        run_id: str | None = None,
    ) -> tuple[CheckpointSummary, ...]: ...

    def load_checkpoint(
        self,
        checkpoint_id: str,
    ) -> tuple[CheckpointSummary, CheckpointState]: ...

    def suspend_run(
        self,
        run_id: str,
        checkpoint_id: str,
    ) -> None: ...

    def reclaim_suspended_run(self, run_id: str) -> None: ...

    def can_resume_checkpoint(self, checkpoint_id: str) -> bool: ...


__all__ = ["CheckpointRepository"]
