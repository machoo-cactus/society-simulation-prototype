from dataclasses import dataclass
from enum import StrEnum


class InteractionVerb(StrEnum):
    PICK_UP = "PICK_UP"
    PUT_DOWN = "PUT_DOWN"
    PLACE_ON = "PLACE_ON"
    PLACE_IN = "PLACE_IN"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    SIT = "SIT"
    STAND = "STAND"
    LIE_DOWN = "LIE_DOWN"
    GET_UP = "GET_UP"
    USE = "USE"
    EQUIP = "EQUIP"
    UNEQUIP = "UNEQUIP"


class InteractionFailureReason(StrEnum):
    UNKNOWN_TARGET = "unknown_target"
    TARGET_NOT_OBSERVABLE = "target_not_observable"
    TARGET_NOT_REACHABLE = "target_not_reachable"
    DIFFERENT_ROOM = "different_room"
    CAPABILITY_MISSING = "capability_missing"
    INTERACTION_NOT_AVAILABLE = "interaction_not_available"
    OBJECT_NOT_PORTABLE = "object_not_portable"
    OBJECT_ALREADY_HELD = "object_already_held"
    OBJECT_NOT_HELD = "object_not_held"
    HANDS_FULL = "hands_full"
    DESTINATION_REQUIRED = "destination_required"
    DESTINATION_NOT_REACHABLE = "destination_not_reachable"
    SLOT_NOT_FOUND = "slot_not_found"
    SLOT_INCOMPATIBLE = "slot_incompatible"
    SLOT_AT_CAPACITY = "slot_at_capacity"
    CONTAINER_CLOSED = "container_closed"
    OBJECT_LOCKED = "object_locked"
    OBJECT_ALREADY_OPEN = "object_already_open"
    OBJECT_ALREADY_CLOSED = "object_already_closed"
    CLOSE_BLOCKED = "close_blocked"
    RELATION_CYCLE = "relation_cycle"
    POSTURE_INVALID = "posture_invalid"
    OCCUPANCY_POSE_UNAVAILABLE = "occupancy_pose_unavailable"
    EXIT_POSE_UNAVAILABLE = "exit_pose_unavailable"
    USE_NOT_SUPPORTED = "use_not_supported"
    OBJECT_NOT_WEARABLE = "object_not_wearable"
    OBJECT_NOT_EQUIPPED = "object_not_equipped"
    EQUIPMENT_SLOT_REQUIRED = "equipment_slot_required"
    EQUIPMENT_SLOT_UNSUPPORTED = "equipment_slot_unsupported"
    EQUIPMENT_SLOT_INCOMPATIBLE = "equipment_slot_incompatible"
    EQUIPMENT_SLOT_AT_CAPACITY = "equipment_slot_at_capacity"
    OBJECT_TOO_HEAVY = "object_too_heavy"
    CARRY_CAPACITY_EXCEEDED = "carry_capacity_exceeded"
    SYSTEM1_PREEMPTION = "system1_preemption"


@dataclass(frozen=True, slots=True)
class InteractionSpecification:
    verb: InteractionVerb
    target_id: str
    destination_id: str | None = None
    slot_id: str | None = None

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("interaction target_id must not be empty")
        if self.verb in {
            InteractionVerb.PLACE_ON,
            InteractionVerb.PLACE_IN,
        }:
            if not self.destination_id:
                raise ValueError(
                    f"{self.verb.value} requires destination_id"
                )
        elif self.destination_id is not None:
            raise ValueError(
                f"{self.verb.value} does not accept destination_id"
            )
        if self.verb is InteractionVerb.EQUIP and self.slot_id is None:
            raise ValueError("EQUIP requires slot_id")
        if self.slot_id is not None and self.verb not in {
            InteractionVerb.PLACE_ON,
            InteractionVerb.PLACE_IN,
            InteractionVerb.SIT,
            InteractionVerb.LIE_DOWN,
            InteractionVerb.EQUIP,
        }:
            raise ValueError(f"{self.verb.value} does not accept slot_id")
