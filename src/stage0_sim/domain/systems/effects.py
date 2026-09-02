from __future__ import annotations

from dataclasses import dataclass

from stage0_sim.domain.components import (
    CarriedLoadComponent,
    ControllerComponent,
    EffectiveSensesComponent,
    EffectOperation,
    EquipmentSlot,
    EquipmentStateComponent,
    ObjectEffect,
    ObjectIntrinsicComponent,
    PhysicalRelationKind,
    SenseEffectTarget,
    SensesComponent,
    SpatialParentRelationComponent,
    WearableComponent,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.systems import SystemContext

_MAX_EFFECTIVE_RANGE = 1_000_000


def resolve_character_effects(registry: Registry, character_id: str) -> bool:
    base = registry.get_component(character_id, SensesComponent)
    active: list[tuple[str, ObjectEffect]] = []
    equipped: dict[EquipmentSlot, list[str]] = {}
    carried_ids: list[str] = []
    for object_id, relation in registry.query(SpatialParentRelationComponent):
        if relation.parent_id != character_id or relation.kind not in {
            PhysicalRelationKind.HELD_BY,
            PhysicalRelationKind.ATTACHED_TO,
        }:
            continue
        carried_ids.append(object_id)
        if (
            relation.kind is not PhysicalRelationKind.ATTACHED_TO
            or relation.slot_id is None
            or not registry.has_component(object_id, WearableComponent)
        ):
            continue
        try:
            slot = EquipmentSlot(relation.slot_id)
        except ValueError:
            continue
        wearable = registry.get_component(object_id, WearableComponent)
        if slot not in wearable.compatible_slots:
            continue
        equipped.setdefault(slot, []).append(object_id)
        active.extend((object_id, effect) for effect in wearable.effects)

    values = {
        SenseEffectTarget.VISION_RANGE: float(base.vision_range),
        SenseEffectTarget.RECOGNITION_RANGE: float(base.recognition_range),
        SenseEffectTarget.HEARING_RANGE: float(base.hearing_range),
        SenseEffectTarget.SMELL_RANGE: float(base.smell_range),
    }
    ordered = sorted(
        active,
        key=lambda item: (
            item[1].target.value,
            0 if item[1].operation is EffectOperation.ADD else 1,
            item[1].id,
            item[0],
        ),
    )
    for _object_id, effect in ordered:
        if effect.operation is EffectOperation.ADD:
            values[effect.target] += effect.value
    for _object_id, effect in ordered:
        if effect.operation is EffectOperation.MULTIPLY:
            values[effect.target] *= effect.value

    resolved = {
        target: max(0, min(_MAX_EFFECTIVE_RANGE, round(value)))
        for target, value in values.items()
    }
    effective = EffectiveSensesComponent(
        vision_range=resolved[SenseEffectTarget.VISION_RANGE],
        recognition_range=min(
            resolved[SenseEffectTarget.RECOGNITION_RANGE],
            resolved[SenseEffectTarget.VISION_RANGE],
        ),
        hearing_range=resolved[SenseEffectTarget.HEARING_RANGE],
        smell_range=resolved[SenseEffectTarget.SMELL_RANGE],
    )
    equipment = EquipmentStateComponent(
        {
            slot: tuple(sorted(object_ids))
            for slot, object_ids in sorted(
                equipped.items(),
                key=lambda item: item[0].value,
            )
        }
    )
    known_mass = 0.0
    unknown_mass: list[str] = []
    for object_id in sorted(carried_ids):
        intrinsic = (
            registry.get_component(object_id, ObjectIntrinsicComponent)
            if registry.has_component(object_id, ObjectIntrinsicComponent)
            else None
        )
        if intrinsic is None or intrinsic.mass_kg is None:
            unknown_mass.append(object_id)
        else:
            known_mass += intrinsic.mass_kg
    load = CarriedLoadComponent(
        known_mass_kg=round(known_mass, 12),
        unknown_mass_object_ids=tuple(unknown_mass),
    )

    changed = False
    for component in (effective, equipment, load):
        component_type = type(component)
        previous = (
            registry.get_component(character_id, component_type)
            if registry.has_component(character_id, component_type)
            else None
        )
        if previous != component:
            registry.set_component(character_id, component)
            changed = True
    if changed and registry.has_component(character_id, ControllerComponent):
        registry.get_component(
            character_id,
            ControllerComponent,
        ).state_revision += 1
    return changed


@dataclass(frozen=True, slots=True)
class CharacterEffectResolutionSystem:
    name: str = "character_effect_resolution"
    order: int = 147

    def update(self, context: SystemContext) -> None:
        for character_id in context.registry.query_entities(SensesComponent):
            if not resolve_character_effects(context.registry, character_id):
                continue
            effective = context.registry.get_component(
                character_id,
                EffectiveSensesComponent,
            )
            load = context.registry.get_component(
                character_id,
                CarriedLoadComponent,
            )
            context.events.emit(
                "character.effects_changed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=character_id,
                payload={
                    "visibility": "private",
                    "effective_senses": {
                        "vision_range": effective.vision_range,
                        "recognition_range": effective.recognition_range,
                        "hearing_range": effective.hearing_range,
                        "smell_range": effective.smell_range,
                    },
                    "known_carried_mass_kg": load.known_mass_kg,
                    "unknown_mass_object_ids": list(
                        load.unknown_mass_object_ids
                    ),
                },
            )
