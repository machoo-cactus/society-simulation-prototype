from collections.abc import Iterable
from dataclasses import dataclass

from stage0_sim.domain.components.perception import (
    EffectiveSensesComponent,
    SensesComponent,
)
from stage0_sim.domain.components.physical import PhysicalStateComponent
from stage0_sim.domain.components.spatial import PositionComponent
from stage0_sim.domain.components.travel import SpatialLocationComponent
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.perception.sweep import sensory_sweep
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import Coordinate, SenseModality, SpatialIndex


@dataclass(frozen=True, slots=True)
class AuditoryRecipient:
    entity_id: str
    distance: int


def resolve_auditory_recipients(
    registry: Registry,
    source_id: str,
    *,
    maximum_range: int,
    listener_ids: Iterable[str] | None = None,
) -> tuple[AuditoryRecipient, ...]:
    if maximum_range < 0:
        raise ValueError("auditory range must not be negative")
    entities = frozenset(registry.entities())
    if (
        source_id not in entities
        or not registry.has_component(source_id, PositionComponent)
    ):
        return ()
    candidates = (
        listener_ids
        if listener_ids is not None
        else registry.query_entities(PositionComponent, SensesComponent)
    )
    source_position = registry.get_component(
        source_id,
        PositionComponent,
    ).coordinate
    spatial_index = (
        registry.get_resource(SpatialIndex)
        if registry.has_resource(SpatialIndex)
        else None
    )
    recipients: list[AuditoryRecipient] = []
    for listener_id in sorted(set(candidates)):
        if (
            listener_id == source_id
            or listener_id not in entities
            or not registry.has_component(listener_id, PositionComponent)
            or not registry.has_component(listener_id, SensesComponent)
            or not _same_local_place(registry, source_id, listener_id)
        ):
            continue
        world = local_world_for_agent(registry, listener_id)
        if world is None:
            continue
        listener_position = registry.get_component(
            listener_id,
            PositionComponent,
        ).coordinate
        hearing_range = _effective_senses(
            registry,
            listener_id,
        ).hearing_range
        result = sensory_sweep(
            world.grid,
            room_id=_room_id(registry, listener_id),
            origin_cells=_entity_cells(
                registry,
                source_id,
                source_position,
            ),
            target_cells=_entity_cells(
                registry,
                listener_id,
                listener_position,
            ),
            maximum_range=min(maximum_range, hearing_range),
            modality=SenseModality.HEARING,
            spatial_index=spatial_index,
            ignored_entity_ids=frozenset({source_id, listener_id}),
        )
        if result.clear and result.distance is not None:
            recipients.append(
                AuditoryRecipient(
                    entity_id=listener_id,
                    distance=result.distance,
                )
            )
    return tuple(recipients)


def _effective_senses(
    registry: Registry,
    listener_id: str,
) -> EffectiveSensesComponent:
    if registry.has_component(listener_id, EffectiveSensesComponent):
        return registry.get_component(
            listener_id,
            EffectiveSensesComponent,
        )
    base = registry.get_component(listener_id, SensesComponent)
    return EffectiveSensesComponent(
        vision_range=base.vision_range,
        recognition_range=base.recognition_range,
        hearing_range=base.hearing_range,
        smell_range=base.smell_range,
    )


def _same_local_place(
    registry: Registry,
    first_id: str,
    second_id: str,
) -> bool:
    if not (
        registry.has_component(first_id, SpatialLocationComponent)
        and registry.has_component(second_id, SpatialLocationComponent)
    ):
        return True
    first = registry.get_component(
        first_id,
        SpatialLocationComponent,
    ).location
    second = registry.get_component(
        second_id,
        SpatialLocationComponent,
    ).location
    return (
        first.local_coordinate is not None
        and second.local_coordinate is not None
        and first.place_id == second.place_id
    )


def _room_id(registry: Registry, entity_id: str) -> str:
    if registry.has_component(entity_id, PhysicalStateComponent):
        return registry.get_component(
            entity_id,
            PhysicalStateComponent,
        ).pose.room_id
    if registry.has_component(entity_id, SpatialLocationComponent):
        return registry.get_component(
            entity_id,
            SpatialLocationComponent,
        ).location.place_id
    return "implicit-building"


def _entity_cells(
    registry: Registry,
    entity_id: str,
    fallback: Coordinate,
) -> frozenset[Coordinate]:
    if registry.has_component(entity_id, PhysicalStateComponent):
        return registry.get_component(
            entity_id,
            PhysicalStateComponent,
        ).occupied_cells
    return frozenset({fallback})
