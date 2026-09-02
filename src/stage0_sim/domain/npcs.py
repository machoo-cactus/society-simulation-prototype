from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.world.model import Coordinate


class NpcControlMode(StrEnum):
    AUTO = "auto"
    MODEL = "model"
    DETERMINISTIC = "deterministic"


@dataclass(frozen=True, slots=True)
class NpcRole:
    id: str
    name: str
    briefing: str
    tool_allowlist: tuple[str, ...] = (
        "serve_transaction",
        "say",
        "wait",
        "skip",
    )
    vision_range: int = 6
    recognition_range: int = 4
    hearing_range: int = 10
    smell_range: int = 0

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("NPC role id and name must not be empty")
        if not self.tool_allowlist:
            raise ValueError("NPC role tool allowlist must not be empty")
        if min(
            self.vision_range,
            self.recognition_range,
            self.hearing_range,
            self.smell_range,
        ) < 0:
            raise ValueError("NPC role sense ranges must not be negative")
        if self.recognition_range > self.vision_range:
            raise ValueError("NPC role recognition range must not exceed vision range")


@dataclass(frozen=True, slots=True)
class NpcStaffingAssignment:
    point_id: str
    role_id: str
    place_id: str | None
    staff_position: Coordinate
    request_timeout: float

    def __post_init__(self) -> None:
        if not self.point_id or not self.role_id:
            raise ValueError("NPC staffing point and role IDs must not be empty")
        if self.request_timeout <= 0:
            raise ValueError(
                "NPC staffing request timeout must be greater than zero"
            )


@dataclass(slots=True)
class NpcStaffingState:
    assignment: NpcStaffingAssignment
    npc_id: str | None = None
    spawn_sequence: int = 0
    last_spawn_blocked_tick: int | None = None


@dataclass(frozen=True, slots=True)
class NpcRoleRegistry:
    roles: dict[str, NpcRole]

    def role(self, role_id: str) -> NpcRole:
        try:
            return self.roles[role_id]
        except KeyError as error:
            raise KeyError(f"unknown NPC role: {role_id}") from error


@dataclass(slots=True)
class NpcPoolRegistry:
    staffings: dict[str, NpcStaffingState]
    requested_mode: NpcControlMode
    effective_mode: NpcControlMode

    def staffing(self, point_id: str) -> NpcStaffingState:
        try:
            return self.staffings[point_id]
        except KeyError as error:
            raise KeyError(
                f"transaction point has no NPC staffing: {point_id}"
            ) from error
