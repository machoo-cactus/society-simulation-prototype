from dataclasses import dataclass

from stage0_sim.domain.npcs import NpcControlMode


@dataclass(frozen=True, slots=True)
class NpcComponent:
    role_id: str
    role_name: str
    staffed_point_id: str
    spawn_sequence: int
    spawned_at: float
    control_mode: NpcControlMode
    transient: bool = True

    def __post_init__(self) -> None:
        if not self.role_id or not self.role_name or not self.staffed_point_id:
            raise ValueError("NPC role and staffing identity must not be empty")
        if self.spawn_sequence <= 0:
            raise ValueError("NPC spawn sequence must be greater than zero")
        if self.spawned_at < 0:
            raise ValueError("NPC spawn time must not be negative")
