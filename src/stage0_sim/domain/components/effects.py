from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum


class ObjectSizeClass(StrEnum):
    TINY = "TINY"
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    BULKY = "BULKY"


@dataclass(frozen=True, slots=True)
class ObjectDimensions:
    length_cm: float
    width_cm: float
    height_cm: float

    def __post_init__(self) -> None:
        values = (self.length_cm, self.width_cm, self.height_cm)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("object dimensions must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class ObjectIntrinsicComponent:
    mass_kg: float | None = None
    dimensions: ObjectDimensions | None = None
    size_class: ObjectSizeClass | None = None

    def __post_init__(self) -> None:
        if self.mass_kg is not None and (
            not math.isfinite(self.mass_kg) or self.mass_kg <= 0
        ):
            raise ValueError("object mass must be finite and greater than zero")


class SenseEffectTarget(StrEnum):
    VISION_RANGE = "VISION_RANGE"
    RECOGNITION_RANGE = "RECOGNITION_RANGE"
    HEARING_RANGE = "HEARING_RANGE"
    SMELL_RANGE = "SMELL_RANGE"


class EffectOperation(StrEnum):
    ADD = "ADD"
    MULTIPLY = "MULTIPLY"


@dataclass(frozen=True, slots=True)
class ObjectEffect:
    id: str
    target: SenseEffectTarget
    operation: EffectOperation
    value: float

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("object effect id must not be empty")
        if not math.isfinite(self.value):
            raise ValueError("object effect value must be finite")
        if self.operation is EffectOperation.MULTIPLY and self.value < 0:
            raise ValueError("multiplicative object effects must not be negative")


class EquipmentSlot(StrEnum):
    EYES = "EYES"
    EARS = "EARS"
    FACE = "FACE"
    HEAD = "HEAD"
    NECK = "NECK"
    TORSO = "TORSO"
    BACK = "BACK"
    WRIST = "WRIST"
    LEGS = "LEGS"
    FEET = "FEET"


@dataclass(frozen=True, slots=True)
class WearableComponent:
    compatible_slots: frozenset[EquipmentSlot]
    effects: tuple[ObjectEffect, ...] = ()

    def __post_init__(self) -> None:
        if not self.compatible_slots:
            raise ValueError("wearable objects require at least one compatible slot")
        effect_ids = [effect.id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("wearable object effect IDs must be unique")


@dataclass(frozen=True, slots=True)
class ScentSourceComponent:
    scent_id: str
    description: str
    emission_range: int

    def __post_init__(self) -> None:
        if not self.scent_id or not self.description:
            raise ValueError("scent source identity and description must not be empty")
        if self.emission_range <= 0:
            raise ValueError("scent emission range must be greater than zero")


def default_equipment_slots() -> dict[EquipmentSlot, int]:
    return {slot: 1 for slot in EquipmentSlot}


@dataclass(frozen=True, slots=True)
class CharacterEmbodimentComponent:
    max_single_object_mass_kg: float = 25.0
    max_carried_mass_kg: float = 35.0
    equipment_slot_capacities: dict[EquipmentSlot, int] = field(
        default_factory=default_equipment_slots
    )

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.max_single_object_mass_kg)
            or self.max_single_object_mass_kg <= 0
            or not math.isfinite(self.max_carried_mass_kg)
            or self.max_carried_mass_kg <= 0
        ):
            raise ValueError("character mass limits must be finite and greater than zero")
        if self.max_single_object_mass_kg > self.max_carried_mass_kg:
            raise ValueError(
                "maximum single-object mass must not exceed total carried mass"
            )
        if any(capacity <= 0 for capacity in self.equipment_slot_capacities.values()):
            raise ValueError("equipment slot capacities must be greater than zero")


@dataclass(slots=True)
class EquipmentStateComponent:
    equipped_object_ids: dict[EquipmentSlot, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class CarriedLoadComponent:
    known_mass_kg: float = 0.0
    unknown_mass_object_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.known_mass_kg) or self.known_mass_kg < 0:
            raise ValueError("known carried mass must be finite and non-negative")
        if len(self.unknown_mass_object_ids) != len(
            set(self.unknown_mass_object_ids)
        ):
            raise ValueError("unknown carried-mass object IDs must be unique")
