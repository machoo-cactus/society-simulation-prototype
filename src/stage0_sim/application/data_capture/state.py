import json
import math
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import cast

from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import CityWorld

_OPERATIONAL_RESOURCE_EXCLUSIONS = {
    "stage0_sim.application.data_capture.recorder.BufferedResearchRecorder": (
        "application-only private research trace buffer"
    ),
    "stage0_sim.application.agents.coordinator.AgentWorkCoordinator": (
        "owns controller/model clients, futures, and a thread pool"
    ),
    "stage0_sim.application.environment.EnvironmentInformationService": (
        "derived query facade over authoritative registry state"
    ),
    "stage0_sim.application.information.retrieval.InformationRetriever": (
        "retrieval service containing an embedding provider"
    ),
    "stage0_sim.application.memory_recording.MemoryWorkCoordinator": (
        "memory-work queue and embedding provider"
    ),
    "stage0_sim.application.navigation.service.NavigationService": (
        "derived navigation service referencing the registry"
    ),
}
_CUSTOM_RESOURCE_PROJECTORS = {
    "stage0_sim.application.information.store.InformationStore",
    "stage0_sim.application.memory.EpisodicMemoryStore",
    "stage0_sim.domain.world.physical.SpatialIndex",
    "stage0_sim.domain.world.topology.SpaceRegistry",
}
_APPLICATION_AUTHORITATIVE_RESOURCES = {
    "stage0_sim.application.perception.system.PerceptionConfiguration",
}


class UnsupportedAuthoritativeValue(TypeError):
    pass


class CaptureCoverageError(RuntimeError):
    pass


def qualified_type_name(value: object | type[object]) -> str:
    value_type = value if isinstance(value, type) else type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def serialize_authoritative(
    value: object,
    *,
    path: str = "$",
) -> JsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedAuthoritativeValue(
                f"{path}: non-finite floats are not supported"
            )
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Enum):
        return serialize_authoritative(value.value, path=path)
    from stage0_sim.domain.components import MovementComponent

    if isinstance(value, MovementComponent):
        return {
            "destination": (
                value.destination.to_payload()
                if value.destination is not None
                else None
            ),
            "remaining_path": compact_coordinate_path(value.path),
            "retry_after_tick": value.retry_after_tick,
            "path_correlation_id": value.path_correlation_id,
            "action_instance": serialize_authoritative(
                value.action_instance,
                path=f"{path}.action_instance",
            ),
            "speed_legacy_cells_per_second": (
                value.speed_legacy_cells_per_second
            ),
            "distance_remainder": value.distance_remainder,
            "planned_spatial_revision": value.planned_spatial_revision,
        }
    from stage0_sim.domain.world import SpatialIndex

    if isinstance(value, SpatialIndex):
        return {
            "revision": value.revision,
            "topology_revision": value.topology_revision,
            "entries": serialize_authoritative(
                value.entries(),
                path=f"{path}.entries",
            ),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: serialize_authoritative(
                getattr(value, field.name),
                path=f"{path}.{field.name}",
            )
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            serialized_key = _mapping_key(key, path)
            if serialized_key in result:
                raise UnsupportedAuthoritativeValue(
                    f"{path}: mapping keys collide after serialization: "
                    f"{serialized_key!r}"
                )
            result[serialized_key] = serialize_authoritative(
                item,
                path=f"{path}[{serialized_key!r}]",
            )
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, Set) and not isinstance(value, str | bytes):
        serialized = [
            serialize_authoritative(item, path=f"{path}[]")
            for item in value
        ]
        return sorted(serialized, key=_canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            serialize_authoritative(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise UnsupportedAuthoritativeValue(
        f"{path}: unsupported authoritative value "
        f"{qualified_type_name(value)}"
    )


def capture_coverage_manifest(registry: Registry) -> dict[str, JsonValue]:
    components: list[JsonValue] = [
        {
            "type": qualified_type_name(component_type),
            "classification": "authoritative",
            "projector": "deterministic_structural",
        }
        for component_type in registry.component_types()
    ]
    resources: list[JsonValue] = []
    for resource_type, _resource in registry.resource_items():
        type_name = qualified_type_name(resource_type)
        if type_name in _OPERATIONAL_RESOURCE_EXCLUSIONS:
            resources.append(
                {
                    "type": type_name,
                    "classification": "operational_exclusion",
                    "reason": _OPERATIONAL_RESOURCE_EXCLUSIONS[type_name],
                }
            )
        elif type_name in _CUSTOM_RESOURCE_PROJECTORS:
            resources.append(
                {
                    "type": type_name,
                    "classification": "authoritative",
                    "projector": "registered_custom",
                }
            )
        elif (
            resource_type.__module__.startswith("stage0_sim.domain.")
            or type_name in _APPLICATION_AUTHORITATIVE_RESOURCES
        ):
            resources.append(
                {
                    "type": type_name,
                    "classification": "authoritative",
                    "projector": "deterministic_structural",
                }
            )
        else:
            raise CaptureCoverageError(
                f"resource {type_name} has no authoritative projector "
                "or operational exclusion"
            )
    return {
        "components": components,
        "resources": resources,
        "declared_operational_exclusions": [
            {
                "type": type_name,
                "reason": _OPERATIONAL_RESOURCE_EXCLUSIONS[type_name],
            }
            for type_name in sorted(_OPERATIONAL_RESOURCE_EXCLUSIONS)
        ],
        "operational_values_are_authoritative": False,
    }


def capture_registry_state(registry: Registry) -> dict[str, JsonValue]:
    manifest = capture_coverage_manifest(registry)
    entities: list[JsonValue] = []
    for entity_id in registry.entities():
        component_state = {
            qualified_type_name(component): serialize_authoritative(
                component,
                path=f"entities[{entity_id!r}].{qualified_type_name(component)}",
            )
            for component in registry.components(entity_id)
        }
        entities.append(
            {
                "entity_id": entity_id,
                "components": component_state,
            }
        )
    resources: dict[str, JsonValue] = {}
    for resource_type, resource in registry.resource_items():
        type_name = qualified_type_name(resource_type)
        if type_name in _OPERATIONAL_RESOURCE_EXCLUSIONS:
            continue
        resources[type_name] = _serialize_resource(
            type_name,
            resource,
            path=f"resources.{type_name}",
        )
    return {
        "entities": entities,
        "resources": resources,
        "coverage": manifest,
    }


def compact_coordinate_path(
    coordinates: Iterable[object],
) -> dict[str, JsonValue]:
    """Encode a coordinate path as deterministic straight-line segments."""
    from stage0_sim.domain.world import Coordinate

    points = tuple(
        coordinate
        for coordinate in coordinates
        if isinstance(coordinate, Coordinate)
    )
    if not points:
        return {
            "encoding": "delta_segments.v1",
            "point_count": 0,
            "start": None,
            "segments": [],
        }
    segments: list[JsonValue] = []
    segment_start = points[0]
    previous = points[0]
    delta: tuple[int, int] | None = None
    steps = 0
    for point in points[1:]:
        next_delta = (point.x - previous.x, point.y - previous.y)
        if delta is not None and next_delta != delta:
            segments.append(
                _path_segment_payload(segment_start, previous, delta, steps)
            )
            segment_start = previous
            steps = 0
        delta = next_delta
        steps += 1
        previous = point
    if delta is not None:
        segments.append(
            _path_segment_payload(segment_start, previous, delta, steps)
        )
    return {
        "encoding": "delta_segments.v1",
        "point_count": len(points),
        "start": points[0].to_payload(),
        "segments": segments,
    }


def character_physical_state(
    registry: Registry,
    entity_id: str,
) -> dict[str, JsonValue] | None:
    """Project a self-contained, compact character physical observation."""
    from stage0_sim.domain.components import (
        CarriedLoadComponent,
        CharacterEmbodimentComponent,
        CharacterHandStateComponent,
        CharacterPostureComponent,
        EffectiveSensesComponent,
        EquipmentStateComponent,
        InteractionExecutionComponent,
        InteractionRequestComponent,
        MovementComponent,
        NavigationComponent,
        PhysicalObjectIdentityComponent,
        PhysicalStateComponent,
        PossessionsComponent,
        SensesComponent,
        SpatialIndex,
        SpatialParentRelationComponent,
    )
    from stage0_sim.domain.lineage import action_lineage_payload

    if (
        registry.has_component(entity_id, PhysicalObjectIdentityComponent)
        or not registry.has_component(entity_id, PhysicalStateComponent)
    ):
        return None
    physical = registry.get_component(entity_id, PhysicalStateComponent)
    posture = (
        registry.get_component(entity_id, CharacterPostureComponent)
        if registry.has_component(entity_id, CharacterPostureComponent)
        else None
    )
    hands = (
        registry.get_component(entity_id, CharacterHandStateComponent)
        if registry.has_component(entity_id, CharacterHandStateComponent)
        else None
    )
    relation = (
        registry.get_component(entity_id, SpatialParentRelationComponent)
        if registry.has_component(entity_id, SpatialParentRelationComponent)
        else None
    )
    movement = (
        registry.get_component(entity_id, MovementComponent)
        if registry.has_component(entity_id, MovementComponent)
        else None
    )
    navigation = (
        registry.get_component(entity_id, NavigationComponent)
        if registry.has_component(entity_id, NavigationComponent)
        else None
    )
    request = (
        registry.get_component(entity_id, InteractionRequestComponent)
        if registry.has_component(entity_id, InteractionRequestComponent)
        else None
    )
    execution = (
        registry.get_component(entity_id, InteractionExecutionComponent)
        if registry.has_component(entity_id, InteractionExecutionComponent)
        else None
    )
    possessions = (
        registry.get_component(entity_id, PossessionsComponent)
        if registry.has_component(entity_id, PossessionsComponent)
        else None
    )
    effective_senses = (
        registry.get_component(entity_id, EffectiveSensesComponent)
        if registry.has_component(entity_id, EffectiveSensesComponent)
        else None
    )
    base_senses = (
        registry.get_component(entity_id, SensesComponent)
        if registry.has_component(entity_id, SensesComponent)
        else None
    )
    equipment = (
        registry.get_component(entity_id, EquipmentStateComponent)
        if registry.has_component(entity_id, EquipmentStateComponent)
        else None
    )
    load = (
        registry.get_component(entity_id, CarriedLoadComponent)
        if registry.has_component(entity_id, CarriedLoadComponent)
        else None
    )
    embodiment = (
        registry.get_component(entity_id, CharacterEmbodimentComponent)
        if registry.has_component(entity_id, CharacterEmbodimentComponent)
        else None
    )
    index = (
        registry.get_resource(SpatialIndex)
        if registry.has_resource(SpatialIndex)
        else None
    )
    physically_custodied = tuple(
        object_id
        for object_id, custody in _physical_custody_entries(registry)
        if custody == entity_id
    )
    return {
        "feature_schema": "stage0.feature.character_physical_state.v2",
        "character_id": entity_id,
        "coordinate_system": "microcell",
        "pose": _pose_payload(physical),
        "footprint": {
            "coordinate_system": "local_microcell_offset",
            "cells": _coordinate_payloads(physical.footprint.cells),
        },
        "occupied_cells": _coordinate_payloads(physical.occupied_cells),
        "posture": (
            {
                "value": posture.posture.value,
                "support_id": posture.support_id,
            }
            if posture is not None
            else None
        ),
        "hands": (
            {
                "left_object_id": hands.left_hand_object_id,
                "right_object_id": hands.right_hand_object_id,
                "held_object_ids": list(sorted(hands.held_object_ids)),
            }
            if hands is not None
            else None
        ),
        "parent_relation": (
            {
                "parent_id": relation.parent_id,
                "relation_kind": relation.kind.value,
                "slot_id": relation.slot_id,
            }
            if relation is not None
            else None
        ),
        "interaction": {
            "request": (
                {
                    **_interaction_specification_payload(
                        request.specification
                    ),
                    "source": request.source,
                    "status": request.status,
                    "failure_reason": request.failure_reason,
                    "action_lineage": action_lineage_payload(
                        request.action_instance
                    ),
                }
                if request is not None
                else None
            ),
            "execution": (
                {
                    **_interaction_specification_payload(
                        execution.specification
                    ),
                    "source": execution.source,
                    "status": "running",
                    "elapsed": execution.elapsed,
                    "duration": execution.duration,
                    "correlation_id": execution.correlation_id,
                    "action_lineage": action_lineage_payload(
                        execution.action_instance
                    ),
                }
                if execution is not None
                else None
            ),
        },
        "movement": (
            {
                "destination": (
                    movement.destination.to_payload()
                    if movement.destination is not None
                    else None
                ),
                "remaining_path": compact_coordinate_path(movement.path),
                "retry_after_tick": movement.retry_after_tick,
                "path_correlation_id": movement.path_correlation_id,
                "speed_legacy_cells_per_second": (
                    movement.speed_legacy_cells_per_second
                ),
                "distance_remainder": movement.distance_remainder,
                "planned_spatial_revision": (
                    movement.planned_spatial_revision
                ),
                "action_lineage": action_lineage_payload(
                    movement.action_instance
                ),
            }
            if movement is not None
            else None
        ),
        "navigation": (
            {
                "target_id": navigation.target_id,
                "status": navigation.status.value,
                "current_primitive_index": (
                    navigation.current_primitive_index
                ),
                "completed_route_legs": navigation.completed_route_legs,
                "primitives": [
                    serialize_authoritative(primitive)
                    for primitive in navigation.primitives
                ],
                "action_lineage": action_lineage_payload(
                    navigation.action_instance
                ),
            }
            if navigation is not None
            else None
        ),
        "hybrid_possession": {
            "abstract_holdings": (
                dict(sorted(possessions.holdings.items()))
                if possessions is not None
                else {}
            ),
            "physically_held_object_ids": (
                list(sorted(hands.held_object_ids))
                if hands is not None
                else []
            ),
            "physically_custodied_object_ids": list(physically_custodied),
            "representations_are_independent": True,
        },
        "effective_senses": (
            {
                "vision_range": effective_senses.vision_range,
                "recognition_range": effective_senses.recognition_range,
                "hearing_range": effective_senses.hearing_range,
                "smell_range": effective_senses.smell_range,
            }
            if effective_senses is not None
            else None
        ),
        "base_senses": (
            {
                "vision_range": base_senses.vision_range,
                "recognition_range": base_senses.recognition_range,
                "hearing_range": base_senses.hearing_range,
                "smell_range": base_senses.smell_range,
            }
            if base_senses is not None
            else None
        ),
        "equipment": (
            {
                slot.value: list(object_ids)
                for slot, object_ids in sorted(
                    equipment.equipped_object_ids.items(),
                    key=lambda item: item[0].value,
                )
            }
            if equipment is not None
            else None
        ),
        "carried_load": (
            {
                "known_mass_kg": load.known_mass_kg,
                "unknown_mass_object_ids": list(
                    load.unknown_mass_object_ids
                ),
                "max_single_object_mass_kg": (
                    embodiment.max_single_object_mass_kg
                    if embodiment is not None
                    else None
                ),
                "max_carried_mass_kg": (
                    embodiment.max_carried_mass_kg
                    if embodiment is not None
                    else None
                ),
            }
            if load is not None
            else None
        ),
        "spatial_index": {
            "indexed": index.contains(entity_id) if index is not None else False,
            "revision": index.revision if index is not None else None,
            "topology_revision": (
                index.topology_revision if index is not None else None
            ),
        },
    }


def physical_object_states(
    registry: Registry,
) -> tuple[dict[str, JsonValue], ...]:
    """Return deterministic authoritative physical-object observations."""
    from stage0_sim.domain.components import (
        ConsumableComponent,
        ContainerComponent,
        CustodyComponent,
        ObjectIntrinsicComponent,
        OccupancySlotsComponent,
        OpenableComponent,
        OwnershipComponent,
        PhysicalInteractionRegistry,
        PhysicalObjectIdentityComponent,
        PhysicalRelationKind,
        PhysicalStateComponent,
        PortableComponent,
        ReadableComponent,
        ScentSourceComponent,
        SpatialIndex,
        SpatialParentRelationComponent,
        SupportComponent,
        UsableComponent,
        WearableComponent,
    )

    index = (
        registry.get_resource(SpatialIndex)
        if registry.has_resource(SpatialIndex)
        else None
    )
    interaction_targets = (
        registry.get_resource(PhysicalInteractionRegistry)
        if registry.has_resource(PhysicalInteractionRegistry)
        else None
    )
    relations = tuple(registry.query(SpatialParentRelationComponent))
    observations: list[dict[str, JsonValue]] = []
    for object_id in registry.query_entities(
        PhysicalObjectIdentityComponent,
        PhysicalStateComponent,
    ):
        identity = registry.get_component(
            object_id, PhysicalObjectIdentityComponent
        )
        physical = registry.get_component(object_id, PhysicalStateComponent)
        relation = (
            registry.get_component(object_id, SpatialParentRelationComponent)
            if registry.has_component(
                object_id, SpatialParentRelationComponent
            )
            else None
        )
        openable = (
            registry.get_component(object_id, OpenableComponent)
            if registry.has_component(object_id, OpenableComponent)
            else None
        )
        slots = (
            registry.get_component(object_id, OccupancySlotsComponent).slots
            if registry.has_component(object_id, OccupancySlotsComponent)
            else ()
        )
        slot_payloads: list[JsonValue] = []
        for slot in sorted(slots, key=lambda item: item.id):
            occupants = sorted(
                entity_id
                for entity_id, candidate in relations
                if candidate.parent_id == object_id
                and candidate.slot_id == slot.id
            )
            accepted_relations = _string_values(
                kind.value for kind in slot.accepted_relations
            )
            occupant_ids = _string_values(occupants)
            slot_payloads.append(
                {
                    "slot_id": slot.id,
                    "accepted_relations": accepted_relations,
                    "capacity": slot.capacity,
                    "occupant_ids": occupant_ids,
                    "occupancy": len(occupants),
                    "remaining_capacity": slot.capacity - len(occupants),
                }
            )
        portable = (
            registry.get_component(object_id, PortableComponent)
            if registry.has_component(object_id, PortableComponent)
            else None
        )
        consumable = (
            registry.get_component(object_id, ConsumableComponent)
            if registry.has_component(object_id, ConsumableComponent)
            else None
        )
        usable = (
            registry.get_component(object_id, UsableComponent)
            if registry.has_component(object_id, UsableComponent)
            else None
        )
        intrinsic = (
            registry.get_component(object_id, ObjectIntrinsicComponent)
            if registry.has_component(object_id, ObjectIntrinsicComponent)
            else None
        )
        wearable = (
            registry.get_component(object_id, WearableComponent)
            if registry.has_component(object_id, WearableComponent)
            else None
        )
        scent = (
            registry.get_component(object_id, ScentSourceComponent)
            if registry.has_component(object_id, ScentSourceComponent)
            else None
        )
        target = (
            interaction_targets.targets.get(object_id)
            if interaction_targets is not None
            else None
        )
        indexed = index is not None and index.contains(object_id)
        observations.append(
            {
                "feature_schema": "stage0.feature.physical_object_state.v2",
                "object_id": object_id,
                "definition_id": identity.definition_id,
                "name": identity.name,
                "coordinate_system": "microcell",
                "pose": _pose_payload(physical),
                "footprint": {
                    "coordinate_system": "local_microcell_offset",
                    "cells": _coordinate_payloads(
                        physical.footprint.cells
                    ),
                },
                "occupied_cells": _coordinate_payloads(
                    physical.occupied_cells
                ),
                "obstruction": {
                    "movement": physical.movement_obstruction.value,
                    "vision": physical.vision_obstruction.value,
                    "hearing": physical.hearing_transmission.value,
                    "smell": physical.smell_transmission.value,
                    "blocks_movement": (
                        physical.movement_obstruction.blocks_movement
                    ),
                    "blocks_vision": (
                        physical.vision_obstruction.blocks_vision
                    ),
                    "blocks_hearing": physical.hearing_transmission.blocks,
                    "blocks_smell": physical.smell_transmission.blocks,
                },
                "intrinsics": (
                    {
                        "mass_kg": intrinsic.mass_kg,
                        "dimensions_cm": (
                            {
                                "length_cm": intrinsic.dimensions.length_cm,
                                "width_cm": intrinsic.dimensions.width_cm,
                                "height_cm": intrinsic.dimensions.height_cm,
                            }
                            if intrinsic.dimensions is not None
                            else None
                        ),
                        "size_class": (
                            intrinsic.size_class.value
                            if intrinsic.size_class is not None
                            else None
                        ),
                    }
                    if intrinsic is not None
                    else None
                ),
                "openable": (
                    {
                        "is_open": openable.is_open,
                        "is_locked": openable.is_locked,
                        "closed_movement_obstruction": (
                            openable.closed_movement_obstruction.value
                        ),
                        "closed_vision_obstruction": (
                            openable.closed_vision_obstruction.value
                        ),
                        "closed_hearing_transmission": (
                            openable.closed_hearing_transmission.value
                        ),
                        "closed_smell_transmission": (
                            openable.closed_smell_transmission.value
                        ),
                    }
                    if openable is not None
                    else None
                ),
                "capabilities": {
                    "portable": (
                        {"two_handed": portable.two_handed}
                        if portable is not None
                        else None
                    ),
                    "support_slot_ids": (
                        _string_values(
                            registry.get_component(
                                object_id, SupportComponent
                            ).slot_ids
                        )
                        if registry.has_component(
                            object_id, SupportComponent
                        )
                        else []
                    ),
                    "container_slot_ids": (
                        _string_values(
                            registry.get_component(
                                object_id, ContainerComponent
                            ).slot_ids
                        )
                        if registry.has_component(
                            object_id, ContainerComponent
                        )
                        else []
                    ),
                    "openable": openable is not None,
                    "readable": (
                        {
                            "document_id": registry.get_component(
                                object_id, ReadableComponent
                            ).document_id
                        }
                        if registry.has_component(
                            object_id, ReadableComponent
                        )
                        else None
                    ),
                    "consumable": (
                        {
                            "item_id": consumable.item_id,
                            "remaining_servings": consumable.servings,
                        }
                        if consumable is not None
                        else None
                    ),
                    "usable": (
                        {"use_kind": usable.use_kind}
                        if usable is not None
                        else None
                    ),
                    "wearable": (
                        {
                            "compatible_slots": [
                                slot.value
                                for slot in sorted(
                                    wearable.compatible_slots,
                                    key=lambda item: item.value,
                                )
                            ],
                            "effects": [
                                {
                                    "id": effect.id,
                                    "target": effect.target.value,
                                    "operation": effect.operation.value,
                                    "value": effect.value,
                                }
                                for effect in wearable.effects
                            ],
                        }
                        if wearable is not None
                        else None
                    ),
                    "scent_source": (
                        {
                            "scent_id": scent.scent_id,
                            "description": scent.description,
                            "emission_range": scent.emission_range,
                        }
                        if scent is not None
                        else None
                    ),
                    "interaction_target": (
                        {
                            "room_id": target.room_id,
                            "approach_anchors": _coordinate_payloads(
                                target.approach_anchors
                            ),
                            "occupancy_anchors": {
                                slot_id: _coordinate_payloads(anchors)
                                for slot_id, anchors in sorted(
                                    target.occupancy_anchors.items()
                                )
                            },
                        }
                        if target is not None
                        else None
                    ),
                },
                "slots": slot_payloads,
                "parent_relation": (
                    {
                        "parent_id": relation.parent_id,
                        "relation_kind": relation.kind.value,
                        "slot_id": relation.slot_id,
                    }
                    if relation is not None
                    else None
                ),
                "custody": (
                    {
                        "custodian_id": registry.get_component(
                            object_id, CustodyComponent
                        ).custodian_id,
                        "held": (
                            relation is not None
                            and relation.kind is PhysicalRelationKind.HELD_BY
                        ),
                        "held_by_id": (
                            relation.parent_id
                            if relation is not None
                            and relation.kind
                            is PhysicalRelationKind.HELD_BY
                            else None
                        ),
                    }
                    if registry.has_component(object_id, CustodyComponent)
                    or (
                        relation is not None
                        and relation.kind is PhysicalRelationKind.HELD_BY
                    )
                    else None
                ),
                "ownership": (
                    {
                        "owner_id": registry.get_component(
                            object_id, OwnershipComponent
                        ).owner_id
                    }
                    if registry.has_component(object_id, OwnershipComponent)
                    else None
                ),
                "spatial_index": {
                    "indexed": indexed,
                    "dynamic": (
                        index.entry(object_id).dynamic
                        if indexed and index is not None
                        else None
                    ),
                    "revision": index.revision if index is not None else None,
                    "topology_revision": (
                        index.topology_revision
                        if index is not None
                        else None
                    ),
                },
            }
        )
    return tuple(observations)


def physical_relation_samples(
    registry: Registry,
) -> tuple[dict[str, JsonValue], ...]:
    """Return live parent-relation observations in stable child order."""
    from stage0_sim.domain.components import (
        CustodyComponent,
        PhysicalObjectIdentityComponent,
        PhysicalRelationKind,
        PhysicalStateComponent,
        SpatialIndex,
        SpatialParentRelationComponent,
    )

    index = (
        registry.get_resource(SpatialIndex)
        if registry.has_resource(SpatialIndex)
        else None
    )
    result: list[dict[str, JsonValue]] = []
    for entity_id, relation in registry.query(
        SpatialParentRelationComponent
    ):
        physical = (
            registry.get_component(entity_id, PhysicalStateComponent)
            if registry.has_component(entity_id, PhysicalStateComponent)
            else None
        )
        custodian_id = (
            registry.get_component(entity_id, CustodyComponent).custodian_id
            if registry.has_component(entity_id, CustodyComponent)
            else None
        )
        result.append(
            {
                "feature_schema": "stage0.feature.physical_relation_sample.v1",
                "object_id": entity_id,
                "entity_kind": (
                    "physical_object"
                    if registry.has_component(
                        entity_id, PhysicalObjectIdentityComponent
                    )
                    else "character"
                ),
                "room_id": (
                    physical.pose.room_id if physical is not None else None
                ),
                "parent_id": relation.parent_id,
                "parent_kind": _physical_parent_kind(
                    registry, relation.parent_id
                ),
                "relation_kind": relation.kind.value,
                "slot_id": relation.slot_id,
                "custodian_id": custodian_id,
                "held": relation.kind is PhysicalRelationKind.HELD_BY,
                "held_by_id": (
                    relation.parent_id
                    if relation.kind is PhysicalRelationKind.HELD_BY
                    else None
                ),
                "spatial_index": {
                    "revision": index.revision if index is not None else None,
                    "topology_revision": (
                        index.topology_revision
                        if index is not None
                        else None
                    ),
                },
            }
        )
    return tuple(result)


def state_delta(
    before: dict[str, JsonValue],
    after: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    changes: list[JsonValue] = []
    _collect_changes(before, after, "$", changes)
    return {
        "change_count": len(changes),
        "changed_fields": changes,
    }


def population_state(registry: Registry) -> dict[str, JsonValue]:
    from stage0_sim.domain.components import (
        ActivityComponent,
        DriveComponent,
        NpcComponent,
        SpatialLocationComponent,
    )

    component_counts = {
        qualified_type_name(component_type): sum(
            1 for _ in registry.query(component_type)
        )
        for component_type in registry.component_types()
    }
    actor_counts = {"character": 0, "npc": 0}
    activity_counts: dict[str, int] = {}
    system1_counts: dict[str, int] = {}
    place_counts: dict[str, int] = {}
    room_counts: dict[str, int] = {}
    building_counts: dict[str, int] = {}
    for entity_id in registry.entities():
        actor_kind = (
            "npc"
            if registry.has_component(entity_id, NpcComponent)
            else "character"
        )
        actor_counts[actor_kind] += 1
        if registry.has_component(entity_id, ActivityComponent):
            activity = registry.get_component(
                entity_id, ActivityComponent
            ).current.value
            activity_counts[activity] = activity_counts.get(activity, 0) + 1
        if registry.has_component(entity_id, DriveComponent):
            state = registry.get_component(entity_id, DriveComponent).state.value
            system1_counts[state] = system1_counts.get(state, 0) + 1
        if registry.has_component(entity_id, SpatialLocationComponent):
            location = registry.get_component(
                entity_id, SpatialLocationComponent
            ).location
            place = location.place_id
            place_counts[place] = place_counts.get(place, 0) + 1
            if registry.has_resource(CityWorld):
                city = registry.get_resource(CityWorld)
                try:
                    room = city.room(place)
                except KeyError:
                    room = None
                if room is not None:
                    room_counts[room.id] = room_counts.get(room.id, 0) + 1
                    building_counts[room.building_id] = (
                        building_counts.get(room.building_id, 0) + 1
                    )
    return {
        "entity_count": len(registry.entities()),
        "actor_counts": serialize_authoritative(actor_counts),
        "component_counts": serialize_authoritative(component_counts),
        "activity_counts": serialize_authoritative(activity_counts),
        "system1_state_counts": serialize_authoritative(system1_counts),
        "place_counts": serialize_authoritative(place_counts),
        "room_counts": serialize_authoritative(room_counts),
        "building_counts": serialize_authoritative(building_counts),
    }


def opportunity_state(
    registry: Registry,
    entity_id: str,
) -> tuple[dict[str, JsonValue], list[JsonValue]]:
    from stage0_sim.domain.components import (
        AffordanceExecutionComponent,
        PositionComponent,
        SpatialLocationComponent,
    )
    from stage0_sim.domain.economy import TransactionPointRegistry
    from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
    from stage0_sim.domain.world import CityWorld, VehicleRegistry, WorldMap

    context: dict[str, JsonValue] = {
        "perspective": "omniscient_research",
        "perception_filtered": False,
    }
    if registry.has_component(entity_id, PositionComponent):
        context["position"] = serialize_authoritative(
            registry.get_component(entity_id, PositionComponent)
        )
    if registry.has_component(entity_id, SpatialLocationComponent):
        context["spatial_location"] = serialize_authoritative(
            registry.get_component(entity_id, SpatialLocationComponent)
        )
    availability = (
        registry.get_resource(EnvironmentAvailabilityRegistry)
        if registry.has_resource(EnvironmentAvailabilityRegistry)
        else None
    )
    options: list[JsonValue] = []
    if registry.has_resource(WorldMap):
        world = local_world_for_agent(registry, entity_id)
        if world is None:
            return context, options
        occupancy: dict[str, int] = {}
        for _, execution in registry.query(AffordanceExecutionComponent):
            occupancy[execution.station_id] = (
                occupancy.get(execution.station_id, 0) + 1
            )
        for station in world.stations:
            state = (
                availability.state(
                    station.id,
                    base_available=station.available,
                )
                if availability is not None
                else None
            )
            for action in station.actions:
                options.append(
                    {
                        "option_id": f"affordance:{station.id}:{action.action}",
                        "kind": "affordance",
                        "station_id": station.id,
                        "action": action.action,
                        "position": serialize_authoritative(station.position),
                        "duration": action.duration,
                        "capacity": station.capacity,
                        "occupancy": occupancy.get(station.id, 0),
                        "available": (
                            state.available
                            if state is not None
                            else station.available
                        ),
                    }
                )
        point_states = (
            registry.get_resource(TransactionPointRegistry)
            if registry.has_resource(TransactionPointRegistry)
            else None
        )
        for point in world.transaction_points:
            state = (
                availability.state(point.id)
                if availability is not None
                else None
            )
            for offer in point.offers:
                options.append(
                    {
                        "option_id": f"transaction:{point.id}:{offer.id}",
                        "kind": "transaction",
                        "point_id": point.id,
                        "offer_id": offer.id,
                        "position": serialize_authoritative(point.position),
                        "available": state.available if state is not None else True,
                        "point_holdings": (
                            serialize_authoritative(
                                point_states.state(point.id).holdings
                            )
                            if point_states is not None
                            else {}
                        ),
                    }
                )
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        for building in city.buildings:
            options.append(
                {
                    "option_id": f"travel:{building.id}",
                    "kind": "travel",
                    "destination_id": building.id,
                }
            )
        for place in city.outdoor_places:
            options.append(
                {
                    "option_id": f"travel:{place.id}",
                    "kind": "travel",
                    "destination_id": place.id,
                }
            )
    if registry.has_resource(VehicleRegistry):
        vehicles = registry.get_resource(VehicleRegistry)
        for vehicle_id, vehicle_state in sorted(vehicles.states.items()):
            options.append(
                {
                    "option_id": f"vehicle:{vehicle_id}",
                    "kind": "vehicle",
                    "vehicle_id": vehicle_id,
                    "state": serialize_authoritative(vehicle_state),
                }
            )
    options.sort(key=lambda item: cast(str, cast(dict[str, JsonValue], item)["option_id"]))
    return context, options


def _serialize_resource(
    type_name: str,
    resource: object,
    *,
    path: str,
) -> JsonValue:
    if type_name == "stage0_sim.application.information.store.InformationStore":
        from stage0_sim.application.information import InformationStore

        information_store = cast(InformationStore, resource)
        return {
            "documents": [
                {
                    "id": document.id,
                    "revisions": serialize_authoritative(
                        information_store.history(document.id),
                        path=f"{path}.documents[{document.id!r}]",
                    ),
                }
                for document in information_store.documents()
            ]
        }
    if type_name == "stage0_sim.application.memory.EpisodicMemoryStore":
        from stage0_sim.application.memory import EpisodicMemoryStore

        memory_store = cast(EpisodicMemoryStore, resource)
        return {
            "configuration": serialize_authoritative(
                memory_store.configuration,
                path=f"{path}.configuration",
            ),
            "records": serialize_authoritative(
                memory_store.records,
                path=f"{path}.records",
            ),
        }
    if type_name == "stage0_sim.domain.world.topology.SpaceRegistry":
        from stage0_sim.domain.world import SpaceRegistry

        spaces = cast(SpaceRegistry, resource)
        return {
            "revision": spaces.revision,
            "spaces": serialize_authoritative(spaces.spaces(), path=f"{path}.spaces"),
            "containment": [
                {
                    "parent_id": parent.id,
                    "child_id": child.id,
                }
                for parent in spaces.spaces()
                for child in spaces.child_spaces(parent.id)
            ],
            "transitions": serialize_authoritative(
                spaces.transitions(),
                path=f"{path}.transitions",
            ),
            "destinations": {
                destination_id: serialize_authoritative(
                    spaces.destination_locators(destination_id),
                    path=f"{path}.destinations[{destination_id!r}]",
                )
                for destination_id in spaces.destination_ids()
            },
        }
    if type_name == "stage0_sim.domain.world.physical.SpatialIndex":
        from stage0_sim.domain.world import SpatialIndex

        spatial_index = cast(SpatialIndex, resource)
        return {
            "revision": spatial_index.revision,
            "topology_revision": spatial_index.topology_revision,
            "entries": serialize_authoritative(
                spatial_index.entries(),
                path=f"{path}.entries",
            ),
        }
    return serialize_authoritative(resource, path=path)


def _mapping_key(value: object, path: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    raise UnsupportedAuthoritativeValue(
        f"{path}: unsupported mapping key {qualified_type_name(value)}"
    )


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _coordinate_payloads(coordinates: Iterable[object]) -> list[JsonValue]:
    from stage0_sim.domain.world import Coordinate

    return [
        coordinate.to_payload()
        for coordinate in sorted(
            (
                coordinate
                for coordinate in coordinates
                if isinstance(coordinate, Coordinate)
            ),
            key=lambda item: (item.y, item.x),
        )
    ]


def _string_values(values: Iterable[str]) -> list[JsonValue]:
    return [value for value in sorted(values)]


def _pose_payload(physical: object) -> dict[str, JsonValue]:
    from stage0_sim.domain.components import PhysicalStateComponent

    if not isinstance(physical, PhysicalStateComponent):
        raise TypeError("physical state projection requires PhysicalStateComponent")
    return {
        "room_id": physical.pose.room_id,
        "anchor": physical.pose.anchor.to_payload(),
        "orientation": physical.pose.orientation.value,
    }


def _interaction_specification_payload(
    specification: object,
) -> dict[str, JsonValue]:
    from stage0_sim.domain.interactions import InteractionSpecification

    if not isinstance(specification, InteractionSpecification):
        raise TypeError("interaction projection requires InteractionSpecification")
    return {
        "verb": specification.verb.value,
        "target_id": specification.target_id,
        "destination_id": specification.destination_id,
        "slot_id": specification.slot_id,
    }


def _path_segment_payload(
    start: object,
    end: object,
    delta: tuple[int, int],
    steps: int,
) -> dict[str, JsonValue]:
    from stage0_sim.domain.world import Coordinate

    if not isinstance(start, Coordinate) or not isinstance(end, Coordinate):
        raise TypeError("path segments require Coordinate endpoints")
    return {
        "start": start.to_payload(),
        "end": end.to_payload(),
        "delta": {"x": delta[0], "y": delta[1]},
        "steps": steps,
    }


def _physical_custody_entries(
    registry: Registry,
) -> tuple[tuple[str, str], ...]:
    from stage0_sim.domain.components import CustodyComponent

    return tuple(
        sorted(
            (
                (entity_id, custody.custodian_id)
                for entity_id, custody in registry.query(CustodyComponent)
            ),
            key=lambda item: item[0],
        )
    )


def _physical_parent_kind(registry: Registry, parent_id: str) -> str:
    from stage0_sim.domain.components import (
        CharacterProfileComponent,
        NpcComponent,
        PhysicalObjectIdentityComponent,
        PhysicalStateComponent,
    )

    if parent_id not in registry.entities():
        return "space"
    if registry.has_component(parent_id, PhysicalObjectIdentityComponent):
        return "physical_object"
    if registry.has_component(parent_id, NpcComponent):
        return "npc"
    if registry.has_component(
        parent_id, CharacterProfileComponent
    ) or registry.has_component(parent_id, PhysicalStateComponent):
        return "character"
    return "space"


def _collect_changes(
    before: JsonValue,
    after: JsonValue,
    path: str,
    changes: list[JsonValue],
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append(
                    {"path": child_path, "operation": "added", "after": after[key]}
                )
            elif key not in after:
                changes.append(
                    {
                        "path": child_path,
                        "operation": "removed",
                        "before": before[key],
                    }
                )
            else:
                _collect_changes(before[key], after[key], child_path, changes)
        return
    if isinstance(before, list) and isinstance(after, list):
        if before != after:
            changes.append(
                {
                    "path": path,
                    "operation": "replaced",
                    "before": before,
                    "after": after,
                }
            )
        return
    if before != after:
        changes.append(
            {
                "path": path,
                "operation": "replaced",
                "before": before,
                "after": after,
            }
        )
