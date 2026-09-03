from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import cast

from stage0_sim.application.memory import EpisodicMemoryStore
from stage0_sim.application.runner import SimulationRunner
from stage0_sim.domain.calendar import SimulationCalendar
from stage0_sim.domain.components import (
    ActionInstance,
    ActivityComponent,
    CarriedLoadComponent,
    CharacterEmbodimentComponent,
    CharacterHandStateComponent,
    CharacterPostureComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ConsumableComponent,
    ContainerComponent,
    ContentEndpointComponent,
    ControllerComponent,
    ConversationComponent,
    CustodyComponent,
    DriveComponent,
    EffectiveSensesComponent,
    EngagementExecutionComponent,
    EngagementProgram,
    EngagementProgramComponent,
    EquipmentStateComponent,
    HomeostasisComponent,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MemoryComponent,
    MovementComponent,
    NavigationComponent,
    NpcComponent,
    ObjectIntrinsicComponent,
    OccupancySlotsComponent,
    OpenableComponent,
    PendingEngagementComponent,
    PerceptionComponent,
    PhysicalInteractionRegistry,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PlanComponent,
    PortableComponent,
    PositionComponent,
    PossessionsComponent,
    ReadableComponent,
    ScentSourceComponent,
    SensesComponent,
    SpatialIndex,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    SupportComponent,
    TransactionRequestComponent,
    TravelComponent,
    UsableComponent,
    WearableComponent,
)
from stage0_sim.domain.economy import (
    ItemCatalog,
    TransactionPoint,
    TransactionPointRegistry,
)
from stage0_sim.domain.ecs import Registry
from stage0_sim.domain.environment import (
    EnvironmentAvailabilityRegistry,
    EnvironmentAvailabilityRules,
    SurfaceConditionRegistry,
    WeatherRuntime,
    wetness_band,
)
from stage0_sim.domain.events import (
    DomainEvent,
    JsonValue,
    event_payload_is_private,
)
from stage0_sim.domain.interactions import InteractionSpecification
from stage0_sim.domain.npcs import NpcPoolRegistry
from stage0_sim.domain.systems.spatial_context import local_world_for_agent
from stage0_sim.domain.world import (
    CityWorld,
    Coordinate,
    VehicleRegistry,
    WorldMap,
    WorldObject,
)

TELEMETRY_SCHEMA_VERSION = "stage0.telemetry.v5"


@dataclass(frozen=True, slots=True)
class TelemetryMessage:
    sequence: int
    message_type: str
    run_id: str
    simulation_tick: int
    simulation_time: float
    payload: dict[str, JsonValue]
    domain_event_offset: int | None = None
    snapshot_revision: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        content: dict[str, JsonValue] = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "sequence": self.sequence,
            "type": self.message_type,
            "run_id": self.run_id,
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "payload": self.payload,
        }
        if self.domain_event_offset is not None:
            content["domain_event_offset"] = self.domain_event_offset
        if self.snapshot_revision is not None:
            content["snapshot_revision"] = self.snapshot_revision
        return content


class TelemetryBroker:
    def __init__(self, runner: SimulationRunner, history_limit: int = 10_000) -> None:
        if history_limit <= 0:
            raise ValueError("history_limit must be greater than zero")
        self.runner = runner
        self._sequence = 0
        self._history_limit = history_limit
        self._messages: dict[int, TelemetryMessage] = {}
        self._domain_event_offset = 0
        self._snapshot_revision = 0
        self._latest_snapshot: TelemetryMessage | None = None
        runner.events.subscribe(self._on_event)

    @property
    def latest_sequence(self) -> int:
        return self._sequence

    @property
    def oldest_sequence(self) -> int:
        return next(iter(self._messages), self._sequence + 1)

    @property
    def domain_event_offset(self) -> int:
        return self._domain_event_offset

    @property
    def snapshot_revision(self) -> int:
        return self._snapshot_revision

    @property
    def latest_snapshot(self) -> TelemetryMessage | None:
        return self._latest_snapshot

    def can_resume_after(self, sequence: int) -> bool:
        return sequence >= self.oldest_sequence - 1

    def messages_after(self, sequence: int) -> tuple[TelemetryMessage, ...]:
        first = max(sequence + 1, self.oldest_sequence)
        return tuple(
            self._messages[candidate]
            for candidate in range(first, self._sequence + 1)
            if candidate in self._messages
        )

    def publish_event(self, event: DomainEvent) -> TelemetryMessage:
        self._domain_event_offset += 1
        return self._publish(
            _message_type_for_event(event.event_type),
            event.simulation_tick,
            event.simulation_time,
            {"event": event.to_dict()},
            domain_event_offset=self._domain_event_offset,
        )

    def _on_event(self, event: DomainEvent) -> None:
        projected = project_operator_event(event)
        if projected is None:
            self._domain_event_offset += 1
            return
        self.publish_event(projected)

    def publish_snapshot(self) -> TelemetryMessage:
        self._snapshot_revision += 1
        self._latest_snapshot = TelemetryMessage(
            sequence=self._sequence,
            message_type="world_snapshot",
            run_id=self.runner.events.run_id,
            simulation_tick=self.runner.clock.tick,
            simulation_time=self.runner.clock.simulation_time,
            payload=build_runtime_snapshot(self.runner),
            snapshot_revision=self._snapshot_revision,
        )
        return self._latest_snapshot

    def publish_status(self) -> TelemetryMessage:
        return self._publish(
            "simulation_status",
            self.runner.clock.tick,
            self.runner.clock.simulation_time,
            {
                "status": self.runner.status.value,
                "speed": self.runner.speed,
                "cognition_phase": self.runner.cognition_phase.value,
                "cognition_pending_decision_ids": list(
                    self.runner.cognition_pending_decision_ids
                ),
                "cognition_pending_engagement_ids": list(
                    self.runner.cognition_pending_engagement_ids
                ),
                "cognition_wait_elapsed_seconds": (
                    self.runner.cognition_wait_elapsed_seconds
                ),
            },
        )

    def _publish(
        self,
        message_type: str,
        simulation_tick: int,
        simulation_time: float,
        payload: dict[str, JsonValue],
        *,
        domain_event_offset: int | None = None,
    ) -> TelemetryMessage:
        self._sequence += 1
        message = TelemetryMessage(
            sequence=self._sequence,
            message_type=message_type,
            run_id=self.runner.events.run_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            payload=payload,
            domain_event_offset=domain_event_offset,
        )
        self._messages[message.sequence] = message
        while len(self._messages) > self._history_limit:
            self._messages.pop(next(iter(self._messages)))
        return message


def build_world_object_snapshot(
    runner: SimulationRunner,
    object_id: str,
    *,
    operator: bool = False,
) -> dict[str, JsonValue] | None:
    """Project one world object without exposing private physical state."""
    registry = runner.registry
    city = (
        registry.get_resource(CityWorld)
        if registry.has_resource(CityWorld)
        else None
    )
    definition = _city_object(city, object_id)
    is_entity = object_id in registry.entities()
    is_physical = (
        is_entity
        and registry.has_component(
            object_id,
            PhysicalObjectIdentityComponent,
        )
        and registry.has_component(object_id, PhysicalStateComponent)
    )
    if not is_physical:
        return (
            _legacy_world_object_payload(definition, city)
            if definition is not None and city is not None
            else None
        )
    if not _physical_entity_is_visible(
        runner,
        object_id,
        operator=operator,
    ):
        return None

    identity = registry.get_component(
        object_id,
        PhysicalObjectIdentityComponent,
    )
    state = registry.get_component(object_id, PhysicalStateComponent)
    world = city.room_world(state.pose.room_id) if city is not None else None
    building_id = (
        city.building_for_room(state.pose.room_id).id
        if city is not None
        else (definition.building_id if definition is not None else None)
    )
    kind = definition.object_kind if definition is not None else "physical"
    payload: dict[str, JsonValue] = {
        "id": object_id,
        "definition_id": identity.definition_id,
        "name": identity.name,
        "kind": kind,
        "building_id": building_id,
        "room_id": state.pose.room_id,
        "position": (
            world.to_legacy_coordinate(state.pose.anchor).to_payload()
            if world is not None
            else state.pose.anchor.to_payload()
        ),
        "supported_actions": (
            list(definition.station.supported_actions)
            if definition is not None and definition.station is not None
            else []
        ),
        "offers": (
            [
                {"id": offer.id, "name": offer.name}
                for offer in definition.transaction_point.offers
            ]
            if definition is not None
            and definition.transaction_point is not None
            else []
        ),
        "station": (
            _station_payload(definition)
            if definition is not None
            else None
        ),
        "transaction_point": (
            _transaction_point_payload(definition, world)
            if definition is not None
            else None
        ),
        "physical": _physical_state_payload(
            runner,
            object_id,
            state,
            kind=kind,
            definition_id=identity.definition_id,
            operator=operator,
        ),
    }
    return payload


def build_physical_room_snapshot(
    runner: SimulationRunner,
    room_id: str,
    *,
    operator: bool = False,
) -> dict[str, JsonValue]:
    registry = runner.registry
    city = registry.get_resource(CityWorld)
    room = city.room(room_id)
    object_payloads: list[dict[str, JsonValue]] = []
    physical_ids = set(
        registry.query_entities(
            PhysicalObjectIdentityComponent,
            PhysicalStateComponent,
        )
    )
    object_ids = physical_ids | {
        item.id for item in city.objects if item.id not in physical_ids
    }
    for object_id in sorted(object_ids):
        if object_id in physical_ids:
            state = registry.get_component(object_id, PhysicalStateComponent)
            if state.pose.room_id != room_id:
                continue
        else:
            definition = city.world_object(object_id)
            if definition.room_id != room_id:
                continue
        payload = build_world_object_snapshot(
            runner,
            object_id,
            operator=operator,
        )
        if payload is not None:
            object_payloads.append(payload)
    indexed_entity_ids = (
        [
            entry.entity_id
            for entry in registry.get_resource(SpatialIndex).entries(room_id)
            if _physical_entity_is_visible(
                runner,
                entry.entity_id,
                operator=operator,
            )
        ]
        if registry.has_resource(SpatialIndex)
        else []
    )
    object_values: list[JsonValue] = list(object_payloads)
    return {
        "spatial": _room_spatial_payload(room.world),
        "object_ids": _string_payloads(
            str(item["id"]) for item in object_payloads
        ),
        "indexed_entity_ids": _string_payloads(indexed_entity_ids),
        "objects": object_values,
    }


def build_physical_world_snapshot(
    runner: SimulationRunner,
    *,
    operator: bool = False,
) -> dict[str, JsonValue]:
    registry = runner.registry
    rooms: list[JsonValue] = []
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        rooms = [
            {
                "id": room.id,
                **build_physical_room_snapshot(
                    runner,
                    room.id,
                    operator=operator,
                ),
            }
            for room in sorted(city.rooms, key=lambda item: item.id)
        ]
    objects: list[JsonValue] = [
        payload
        for object_id in registry.query_entities(
            PhysicalObjectIdentityComponent,
            PhysicalStateComponent,
        )
        if (
            payload := build_world_object_snapshot(
                runner,
                object_id,
                operator=operator,
            )
        )
        is not None
    ]
    index = (
        registry.get_resource(SpatialIndex)
        if registry.has_resource(SpatialIndex)
        else None
    )
    return {
        "spatial_metric": _runtime_spatial_metric_payload(runner),
        "spatial_index": (
            {
                "revision": index.revision,
                "topology_revision": index.topology_revision,
            }
            if index is not None
            else None
        ),
        "rooms": rooms,
        "objects": objects,
    }


def _physical_state_payload(
    runner: SimulationRunner,
    entity_id: str,
    state: PhysicalStateComponent,
    *,
    kind: str | None = None,
    definition_id: str | None = None,
    operator: bool = False,
) -> dict[str, JsonValue]:
    registry = runner.registry
    relation = (
        registry.get_component(
            entity_id,
            SpatialParentRelationComponent,
        )
        if registry.has_component(
            entity_id,
            SpatialParentRelationComponent,
        )
        else None
    )
    relation_payload: JsonValue = None
    if relation is not None and (
        operator
        or relation.parent_id not in registry.entities()
        or _physical_entity_is_visible(
            runner,
            relation.parent_id,
            operator=operator,
        )
    ):
        relation_payload = {
            "parent_id": relation.parent_id,
            "kind": relation.kind.value,
            "slot_id": relation.slot_id,
        }
    custody_id = (
        registry.get_component(entity_id, CustodyComponent).custodian_id
        if registry.has_component(entity_id, CustodyComponent)
        else None
    )
    custody = (
        custody_id
        if custody_id is not None
        and (
            operator
            or custody_id not in registry.entities()
            or _physical_entity_is_visible(
                runner,
                custody_id,
                operator=operator,
            )
        )
        else None
    )
    held_by = (
        relation.parent_id
        if relation is not None
        and relation.kind is PhysicalRelationKind.HELD_BY
        and relation_payload is not None
        else None
    )
    openable = (
        registry.get_component(entity_id, OpenableComponent)
        if registry.has_component(entity_id, OpenableComponent)
        else None
    )
    return {
        "coordinate_system": "microcell",
        "identity": (
            {
                "definition_id": definition_id,
                "kind": kind,
            }
            if definition_id is not None
            else None
        ),
        "pose": {
            "room_id": state.pose.room_id,
            "anchor": state.pose.anchor.to_payload(),
            "orientation": state.pose.orientation.value,
        },
        "footprint": {
            "coordinate_system": "local_microcell_offset",
            "cells": _coordinate_payloads(state.footprint.cells),
        },
        "occupied_cells": _coordinate_payloads(state.occupied_cells),
        "obstruction": {
            "movement": state.movement_obstruction.value,
            "vision": state.vision_obstruction.value,
            "hearing": state.hearing_transmission.value,
            "smell": state.smell_transmission.value,
            "blocks_movement": state.movement_obstruction.blocks_movement,
            "blocks_vision": state.vision_obstruction.blocks_vision,
            "blocks_hearing": state.hearing_transmission.blocks,
            "blocks_smell": state.smell_transmission.blocks,
        },
        "openable": (
            {
                "is_open": openable.is_open,
                "is_locked": openable.is_locked,
            }
            if openable is not None
            else None
        ),
        "capabilities": _physical_capabilities_payload(registry, entity_id),
        "intrinsics": _physical_intrinsics_payload(registry, entity_id),
        "parent_relation": relation_payload,
        "custodian_id": custody,
        "held_by": held_by,
        "slots": _occupancy_slots_payload(
            runner,
            entity_id,
            operator=operator,
        ),
        "spatial_indexed": (
            registry.get_resource(SpatialIndex).contains(entity_id)
            if registry.has_resource(SpatialIndex)
            else False
        ),
        "interaction_target": _interaction_target_payload(
            registry,
            entity_id,
        ),
    }


def _physical_capabilities_payload(
    registry: Registry,
    entity_id: str,
) -> dict[str, JsonValue]:
    portable = (
        registry.get_component(entity_id, PortableComponent)
        if registry.has_component(entity_id, PortableComponent)
        else None
    )
    consumable = (
        registry.get_component(entity_id, ConsumableComponent)
        if registry.has_component(entity_id, ConsumableComponent)
        else None
    )
    usable = (
        registry.get_component(entity_id, UsableComponent)
        if registry.has_component(entity_id, UsableComponent)
        else None
    )
    wearable = (
        registry.get_component(entity_id, WearableComponent)
        if registry.has_component(entity_id, WearableComponent)
        else None
    )
    scent = (
        registry.get_component(entity_id, ScentSourceComponent)
        if registry.has_component(entity_id, ScentSourceComponent)
        else None
    )
    return {
        "portable": (
            {"two_handed": portable.two_handed}
            if portable is not None
            else None
        ),
        "support_slot_ids": (
            _string_payloads(
                sorted(
                    registry.get_component(
                        entity_id,
                        SupportComponent,
                    ).slot_ids
                )
            )
            if registry.has_component(entity_id, SupportComponent)
            else []
        ),
        "container_slot_ids": (
            _string_payloads(
                sorted(
                    registry.get_component(
                        entity_id,
                        ContainerComponent,
                    ).slot_ids
                )
            )
            if registry.has_component(entity_id, ContainerComponent)
            else []
        ),
        "openable": registry.has_component(
            entity_id,
            OpenableComponent,
        ),
        "readable": registry.has_component(
            entity_id,
            ReadableComponent,
        ),
        "content_endpoints": (
            [
                {
                    "id": endpoint.id,
                    "label": endpoint.label,
                    "kind": endpoint.kind.value,
                    "operations": [
                        operation.value
                        for operation in endpoint.operations
                    ],
                    "access_mode": endpoint.access_mode.value,
                    "lists_items": endpoint.lists_items,
                    "originates_messages": endpoint.originates_messages,
                    "notifies_owner": endpoint.notifies_owner,
                }
                for endpoint in registry.get_component(
                    entity_id, ContentEndpointComponent
                ).endpoints
            ]
            if registry.has_component(
                entity_id, ContentEndpointComponent
            )
            else []
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
    }


def _physical_intrinsics_payload(
    registry: Registry,
    entity_id: str,
) -> JsonValue:
    if not registry.has_component(entity_id, ObjectIntrinsicComponent):
        return None
    intrinsic = registry.get_component(entity_id, ObjectIntrinsicComponent)
    return {
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


def _occupancy_slots_payload(
    runner: SimulationRunner,
    parent_id: str,
    *,
    operator: bool,
) -> list[JsonValue]:
    registry = runner.registry
    if not registry.has_component(parent_id, OccupancySlotsComponent):
        return []
    relations = [
        (entity_id, relation)
        for entity_id, relation in registry.query(
            SpatialParentRelationComponent,
        )
        if relation.parent_id == parent_id
    ]
    slots = registry.get_component(parent_id, OccupancySlotsComponent).slots
    payloads: list[JsonValue] = []
    for slot in sorted(slots, key=lambda item: item.id):
        occupants = [
            entity_id
            for entity_id, relation in relations
            if relation.slot_id == slot.id
        ]
        visible_occupants = [
            entity_id
            for entity_id in occupants
            if _physical_entity_is_visible(
                runner,
                entity_id,
                operator=operator,
            )
        ]
        occupancy: JsonValue = None
        if operator or len(visible_occupants) == len(occupants):
            occupancy = {
                "entity_ids": _string_payloads(visible_occupants),
                "count": len(visible_occupants),
                "remaining_capacity": slot.capacity - len(occupants),
            }
        payloads.append(
            {
                "id": slot.id,
                "accepted_relations": _string_payloads(
                    sorted(
                        relation.value
                        for relation in slot.accepted_relations
                    )
                ),
                "capacity": slot.capacity,
                "occupancy": occupancy,
            }
        )
    return payloads


def _interaction_target_payload(
    registry: Registry,
    entity_id: str,
) -> JsonValue:
    if not registry.has_resource(PhysicalInteractionRegistry):
        return None
    interactions = registry.get_resource(PhysicalInteractionRegistry)
    if entity_id not in interactions.targets:
        return None
    target = interactions.target(entity_id)
    return {
        "room_id": target.room_id,
        "approach_anchors": _coordinate_payloads(target.approach_anchors),
        "occupancy_anchors": {
            slot_id: _coordinate_payloads(anchors)
            for slot_id, anchors in sorted(target.occupancy_anchors.items())
        },
    }


def _physical_entity_is_visible(
    runner: SimulationRunner,
    entity_id: str,
    *,
    operator: bool,
) -> bool:
    if operator:
        return True
    registry = runner.registry
    if entity_id not in registry.entities():
        return True
    visited: set[str] = set()
    current_id = entity_id
    while current_id not in visited:
        visited.add(current_id)
        if not registry.has_component(
            current_id,
            SpatialParentRelationComponent,
        ):
            return True
        relation = registry.get_component(
            current_id,
            SpatialParentRelationComponent,
        )
        parent_id = relation.parent_id
        if parent_id not in registry.entities():
            return True
        if (
            relation.kind is PhysicalRelationKind.IN_CONTAINER
            and registry.has_component(parent_id, OpenableComponent)
            and not registry.get_component(
                parent_id,
                OpenableComponent,
            ).is_open
            and registry.has_component(parent_id, PhysicalStateComponent)
            and registry.get_component(
                parent_id,
                PhysicalStateComponent,
            ).vision_obstruction.blocks_vision
        ):
            return False
        current_id = parent_id
    return False


def _room_spatial_payload(world: WorldMap) -> dict[str, JsonValue]:
    legacy_width, legacy_height = world.legacy_dimensions()
    return {
        "coordinate_system": world.coordinate_system.value,
        "microcells_per_legacy_cell": world.microcells_per_legacy_cell,
        "width_microcells": world.grid.width,
        "height_microcells": world.grid.height,
        "width_legacy_cells": legacy_width,
        "height_legacy_cells": legacy_height,
    }


def _runtime_spatial_metric_payload(
    runner: SimulationRunner,
) -> dict[str, JsonValue]:
    registry = runner.registry
    world: WorldMap | None = None
    if registry.has_resource(WorldMap):
        world = registry.get_resource(WorldMap)
    elif registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        world = city.rooms[0].world if city.rooms else None
    return {
        "coordinate_system": (
            world.coordinate_system.value if world is not None else "microcell"
        ),
        "microcells_per_legacy_cell": (
            world.microcells_per_legacy_cell if world is not None else 9
        ),
    }


def _coordinate_payloads(
    coordinates: Iterable[Coordinate],
) -> list[JsonValue]:
    return [
        coordinate.to_payload()
        for coordinate in sorted(
            coordinates,
            key=lambda item: (item.y, item.x),
        )
    ]


def _string_payloads(values: Iterable[str]) -> list[JsonValue]:
    return [value for value in values]


def _city_object(
    city: CityWorld | None,
    object_id: str,
) -> WorldObject | None:
    if city is None:
        return None
    return next(
        (item for item in city.objects if item.id == object_id),
        None,
    )


def _legacy_world_object_payload(
    item: WorldObject,
    city: CityWorld,
) -> dict[str, JsonValue]:
    world = city.room_world(item.room_id)
    return {
        "id": item.id,
        "name": item.name,
        "kind": item.object_kind,
        "building_id": item.building_id,
        "room_id": item.room_id,
        "position": world.to_legacy_coordinate(item.position).to_payload(),
        "supported_actions": (
            list(item.station.supported_actions)
            if item.station is not None
            else []
        ),
        "offers": (
            [
                {"id": offer.id, "name": offer.name}
                for offer in item.transaction_point.offers
            ]
            if item.transaction_point is not None
            else []
        ),
        "station": _station_payload(item),
        "transaction_point": _transaction_point_payload(item, world),
        "physical": None,
    }


def _station_payload(item: WorldObject) -> JsonValue:
    if item.station is None:
        return None
    return {
        "supported_actions": list(item.station.supported_actions),
        "available": item.station.available,
        "capacity": item.station.capacity,
    }


def _transaction_point_payload(
    item: WorldObject,
    world: WorldMap | None,
) -> JsonValue:
    point = item.transaction_point
    if point is None:
        return None
    return {
        "available": point.available,
        "capacity": point.capacity,
        "operation": point.operation.value,
        "staffing": (
            {
                "role_id": point.staffing.role_id,
                "staff_position": (
                    world.to_legacy_coordinate(
                        point.staffing.staff_position,
                    ).to_payload()
                    if world is not None
                    else point.staffing.staff_position.to_payload()
                ),
                "request_timeout": point.staffing.request_timeout,
            }
            if point.staffing is not None
            else None
        ),
        "offers": [
            {
                "id": offer.id,
                "name": offer.name,
                "duration": offer.duration,
            }
            for offer in point.offers
        ],
    }


def _bootstrap_city_room_payload(
    runner: SimulationRunner,
    room_id: str,
) -> dict[str, JsonValue]:
    city = runner.registry.get_resource(CityWorld)
    room = city.room(room_id)
    physical = build_physical_room_snapshot(runner, room_id)
    return {
        "id": room.id,
        "name": room.name,
        "type": room.room_type,
        "building_id": room.building_id,
        "key": room.key,
        "offset": room.world.to_legacy_coordinate(
            room.offset,
        ).to_payload(),
        "spatial": physical["spatial"],
        "map": {
            "width": room.world.legacy_dimensions()[0],
            "height": room.world.legacy_dimensions()[1],
            "blocked": [
                coordinate.to_payload()
                for coordinate in room.world.legacy_coordinates(
                    room.world.grid.blocked
                )
            ],
            "zones": [
                {
                    "id": zone.id,
                    "name": zone.name,
                    "type": zone.zone_type,
                    "tiles": [
                        coordinate.to_payload()
                        for coordinate in room.world.legacy_coordinates(
                            zone.tiles,
                        )
                    ],
                }
                for zone in sorted(room.world.zones, key=lambda item: item.id)
            ],
            "stations": [
                {
                    "id": station.id,
                    "name": station.name,
                    "position": room.world.to_legacy_coordinate(
                        station.position,
                    ).to_payload(),
                    "supported_actions": list(
                        station.supported_actions,
                    ),
                    "available": station.available,
                    "capacity": station.capacity,
                }
                for station in sorted(
                    room.world.stations,
                    key=lambda item: item.id,
                )
            ],
            "transaction_points": [
                {
                    "id": point.id,
                    "name": point.name,
                    "position": room.world.to_legacy_coordinate(
                        point.position,
                    ).to_payload(),
                    "available": point.available,
                    "capacity": point.capacity,
                    "operation": point.operation.value,
                    "offers": [
                        {
                            "id": offer.id,
                            "name": offer.name,
                            "duration": offer.duration,
                        }
                        for offer in point.offers
                    ],
                }
                for point in sorted(
                    room.world.transaction_points,
                    key=lambda item: item.id,
                )
            ],
        },
        "object_ids": physical["object_ids"],
        "indexed_entity_ids": physical["indexed_entity_ids"],
    }


def _city_world_object_payloads(
    runner: SimulationRunner,
) -> list[JsonValue]:
    registry = runner.registry
    city = registry.get_resource(CityWorld)
    physical_ids = set(
        registry.query_entities(
            PhysicalObjectIdentityComponent,
            PhysicalStateComponent,
        )
    )
    object_ids = physical_ids | {item.id for item in city.objects}
    return [
        payload
        for object_id in sorted(object_ids)
        if (
            payload := build_world_object_snapshot(
                runner,
                object_id,
            )
        )
        is not None
    ]


def build_ui_bootstrap(runner: SimulationRunner) -> dict[str, JsonValue]:
    registry = runner.registry
    world_payload: dict[str, JsonValue] | None = None
    if registry.has_resource(WorldMap):
        world = registry.get_resource(WorldMap)
        width, height = world.legacy_dimensions()
        world_payload = {
            "width": width,
            "height": height,
            "spatial": _room_spatial_payload(world),
            "blocked": [
                coordinate.to_payload()
                for coordinate in world.legacy_coordinates(
                    world.grid.blocked
                )
            ],
            "zones": [
                {
                    "id": zone.id,
                    "name": zone.name,
                    "type": zone.zone_type,
                    "tiles": [
                        coordinate.to_payload()
                        for coordinate in world.legacy_coordinates(zone.tiles)
                    ],
                }
                for zone in sorted(world.zones, key=lambda item: item.id)
            ],
            "stations": [
                {
                    "id": station.id,
                    "name": station.name,
                    "position": world.to_legacy_coordinate(
                        station.position
                    ).to_payload(),
                    "actions": list(station.supported_actions),
                    "available": station.available,
                    "capacity": station.capacity,
                }
                for station in sorted(world.stations, key=lambda item: item.id)
            ],
            "transaction_points": [
                {
                    "id": point.id,
                    "name": point.name,
                    "position": world.to_legacy_coordinate(
                        point.position
                    ).to_payload(),
                    "available": point.available,
                    "capacity": point.capacity,
                    "operation": point.operation.value,
                    "staffing": (
                        {
                            "role_id": point.staffing.role_id,
                            "staff_position": (
                                world.to_legacy_coordinate(
                                    point.staffing.staff_position
                                ).to_payload()
                            ),
                            "request_timeout": (
                                point.staffing.request_timeout
                            ),
                        }
                        if point.staffing is not None
                        else None
                    ),
                    "offers": [
                        {
                            "id": offer.id,
                            "name": offer.name,
                            "duration": offer.duration,
                            "character_gives": [
                                {
                                    "item_id": amount.item_id,
                                    "quantity": amount.quantity,
                                }
                                for amount in offer.character_gives
                            ],
                            "character_receives": [
                                {
                                    "item_id": amount.item_id,
                                    "quantity": amount.quantity,
                                }
                                for amount in offer.character_receives
                            ],
                        }
                        for offer in point.offers
                    ],
                }
                for point in sorted(
                    world.transaction_points,
                    key=lambda item: item.id,
                )
            ],
        }
    city_payload: dict[str, JsonValue] | None = None
    if registry.has_resource(CityWorld):
        city = registry.get_resource(CityWorld)
        city_payload = {
            "id": city.id,
            "name": city.name,
            "spatial_metric": _runtime_spatial_metric_payload(runner),
            "bounds": {
                "min_x": city.bounds.min_x,
                "min_y": city.bounds.min_y,
                "max_x": city.bounds.max_x,
                "max_y": city.bounds.max_y,
            },
            "districts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "center": item.center.to_payload(),
                }
                for item in city.districts
            ],
            "city_zones": [
                {
                    "id": item.id,
                    "name": item.name,
                    "center": item.center.to_payload(),
                }
                for item in city.city_zones
            ],
            "buildings": [
                {
                    "id": item.id,
                    "name": item.name,
                    "district_id": item.district_id,
                    "city_zone_id": item.district_id,
                    "position": item.city_position.to_payload(),
                    "room_ids": list(item.room_ids),
                    "entrances": [
                        {
                            "id": entrance.id,
                            "room_id": entrance.room_id,
                            "network_node_id": entrance.network_node_id,
                            "local_coordinate": city.room_world(
                                entrance.room_id
                            ).to_legacy_coordinate(
                                entrance.local_coordinate
                            ).to_payload(),
                        }
                        for entrance in item.entrances
                    ],
                }
                for item in city.buildings
            ],
            "outdoor_places": [
                {
                    "id": item.id,
                    "name": item.name,
                    "district_id": item.district_id,
                    "city_zone_id": item.district_id,
                    "position": item.city_position.to_payload(),
                    "network_node_id": item.network_node_id,
                }
                for item in city.outdoor_places
            ],
            "rooms": [
                _bootstrap_city_room_payload(runner, item.id)
                for item in sorted(city.rooms, key=lambda room: room.id)
            ],
            "portals": [
                {
                    "id": item.id,
                    "building_id": item.building_id,
                    "from_room_id": item.from_room_id,
                    "from_coordinate": city.room_world(
                        item.from_room_id
                    ).to_legacy_coordinate(
                        item.from_coordinate
                    ).to_payload(),
                    "to_room_id": item.to_room_id,
                    "to_coordinate": city.room_world(
                        item.to_room_id
                    ).to_legacy_coordinate(
                        item.to_coordinate
                    ).to_payload(),
                    "bidirectional": item.bidirectional,
                    "available": item.available,
                }
                for item in city.portals
            ],
            "objects": _city_world_object_payloads(runner),
            "nodes": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "position": item.position.to_payload(),
                    "place_id": item.place_id,
                }
                for item in city.nodes
            ],
            "edges": [
                {
                    "id": item.id,
                    "from_node_id": item.from_node_id,
                    "to_node_id": item.to_node_id,
                    "allowed_modes": [
                        mode.value for mode in sorted(item.allowed_modes)
                    ],
                    "geometry": [
                        point.to_payload() for point in item.geometry
                    ],
                    "bidirectional": item.bidirectional,
                }
                for item in city.edges
            ],
            "vehicles": [
                {
                    "id": item.id,
                    "name": item.name,
                    "type": item.vehicle_type.value,
                    "capacity": item.capacity,
                    "network_node_id": item.network_node_id,
                }
                for item in city.vehicles
            ],
        }
    return {
        "world": world_payload,
        "city": city_payload,
        "item_catalog": [
            {
                "id": item.id,
                "name": item.name,
                "unit": item.unit,
            }
            for item in (
                registry.get_resource(ItemCatalog).items
                if registry.has_resource(ItemCatalog)
                else ()
            )
        ],
        "agents": [
            _build_agent_static_snapshot(runner, entity_id)
            for entity_id in _agent_entity_ids(registry)
        ],
    }


def build_world_snapshot(runner: SimulationRunner) -> dict[str, JsonValue]:
    bootstrap = build_ui_bootstrap(runner)
    runtime = build_runtime_snapshot(runner)
    static_agents = {
        str(agent["id"]): agent
        for agent in bootstrap["agents"]  # type: ignore[union-attr]
        if isinstance(agent, dict) and isinstance(agent.get("id"), str)
    }
    runtime_agents = runtime["agents"]
    if isinstance(runtime_agents, list):
        for agent in runtime_agents:
            if not isinstance(agent, dict) or not isinstance(agent.get("id"), str):
                continue
            static = static_agents.get(str(agent["id"]))
            if static is not None:
                agent.update(static)
    bootstrap_world = bootstrap["world"]
    runtime_world = runtime["world"]
    merged_world: dict[str, JsonValue] = {}
    if isinstance(bootstrap_world, dict):
        merged_world.update(bootstrap_world)
    if isinstance(runtime_world, dict):
        merged_world.update(runtime_world)
    return {
        **runtime,
        "world": merged_world,
        "city": bootstrap["city"],
        "item_catalog": bootstrap["item_catalog"],
    }


def build_runtime_snapshot(runner: SimulationRunner) -> dict[str, JsonValue]:
    registry = runner.registry
    station_states: list[JsonValue] = []
    if registry.has_resource(WorldMap):
        stations = {
            station.id: station
            for station in registry.get_resource(WorldMap).stations
        }
        if registry.has_resource(CityWorld):
            stations.update(
                {
                    item.id: item.station
                    for item in registry.get_resource(CityWorld).objects
                    if item.station is not None
                }
            )
        station_states = [
            {"id": station.id, "available": station.available}
            for station in sorted(
                stations.values(),
                key=lambda item: item.id,
            )
        ]
    vehicle_states: list[JsonValue] = []
    if registry.has_resource(VehicleRegistry):
        vehicle_states = [
            {
                "id": vehicle_id,
                "network_node_id": state.network_node_id,
                "edge_id": state.edge_id,
                "edge_progress": state.edge_progress,
                "driver_id": state.driver_id,
            }
            for vehicle_id, state in sorted(
                registry.get_resource(VehicleRegistry).states.items()
            )
        ]
    transaction_point_states: list[JsonValue] = []
    if registry.has_resource(TransactionPointRegistry):
        queued_requests: dict[str, int] = {}
        for _, request in registry.query(TransactionRequestComponent):
            if request.status in {
                "awaiting_staff",
                "awaiting_authorization",
                "authorized",
                "running",
            }:
                queued_requests[request.point_id] = (
                    queued_requests.get(request.point_id, 0) + 1
                )
        points: dict[str, TransactionPoint] = {}
        if registry.has_resource(WorldMap):
            points.update(
                {
                    point.id: point
                    for point in registry.get_resource(
                        WorldMap
                    ).transaction_points
                }
            )
        if registry.has_resource(CityWorld):
            points.update(
                {
                    item.id: item.transaction_point
                    for item in registry.get_resource(CityWorld).objects
                    if item.transaction_point is not None
                }
            )
        availability_registry = (
            registry.get_resource(EnvironmentAvailabilityRegistry)
            if registry.has_resource(EnvironmentAvailabilityRegistry)
            else None
        )
        transaction_point_states = [
            {
                "id": point_id,
                "holdings": dict(sorted(state.holdings.items())),
                "available": (
                    availability_registry.state(
                        point_id,
                        base_available=points[point_id].available,
                    ).available
                    if availability_registry is not None
                    else points[point_id].available
                ),
                "operation": points[point_id].operation.value,
                "queued_request_count": queued_requests.get(point_id, 0),
                "staffing": (
                    {
                        "npc_id": registry.get_resource(
                            NpcPoolRegistry
                        ).staffing(point_id).npc_id,
                        "role_id": registry.get_resource(
                            NpcPoolRegistry
                        ).staffing(point_id).assignment.role_id,
                    }
                    if points[point_id].staffing is not None
                    and registry.has_resource(NpcPoolRegistry)
                    else None
                ),
            }
            for point_id, state in sorted(
                registry.get_resource(
                    TransactionPointRegistry
                ).states.items()
            )
        ]
    calendar_time = (
        registry.get_resource(SimulationCalendar).payload_at(
            runner.clock.simulation_time
        )
        if registry.has_resource(SimulationCalendar)
        else None
    )
    environment: dict[str, JsonValue] = {
        "schema_version": "stage0.environment.v1",
        "time": calendar_time,
        "weather": None,
        "effects": None,
        "surface_conditions": [],
        "availability": [],
    }
    if registry.has_resource(WeatherRuntime):
        weather = registry.get_resource(WeatherRuntime)
        environment["weather"] = weather.current.to_payload()
        environment["effects"] = {
            "walking_speed_multiplier": weather.effects.walking_speed_multiplier,
            "cycling_speed_multiplier": weather.effects.cycling_speed_multiplier,
            "visibility_multiplier": weather.effects.visibility_multiplier,
        }
    if registry.has_resource(SurfaceConditionRegistry):
        surfaces = registry.get_resource(SurfaceConditionRegistry)
        environment["surface_conditions"] = [
            {
                "surface_id": surface_id,
                "wetness": value,
                "band": wetness_band(value).value,
            }
            for surface_id, value in sorted(surfaces.wetness.items())
        ]
    if registry.has_resource(EnvironmentAvailabilityRegistry):
        availability = registry.get_resource(EnvironmentAvailabilityRegistry)
        kinds = (
            {
                rule.resource_id: rule.resource_kind
                for rule in registry.get_resource(
                    EnvironmentAvailabilityRules
                ).rules
            }
            if registry.has_resource(EnvironmentAvailabilityRules)
            else {}
        )
        environment["availability"] = [
            {
                "resource_id": resource_id,
                "resource_kind": kinds.get(resource_id),
                **state.to_payload(),
            }
            for resource_id, state in sorted(availability.states.items())
        ]
    return {
        "status": runner.status.value,
        "speed": runner.speed,
        "cognition_phase": runner.cognition_phase.value,
        "cognition_pending_count": len(
            runner.cognition_pending_decision_ids
        )
        + len(
            runner.cognition_pending_engagement_ids
        ),
        "cognition_pending_decision_ids": list(
            runner.cognition_pending_decision_ids
        ),
        "cognition_pending_engagement_ids": list(
            runner.cognition_pending_engagement_ids
        ),
        "cognition_wait_elapsed_seconds": (
            runner.cognition_wait_elapsed_seconds
        ),
        "npc_control": {
            "requested": runner.configuration.npc_control_mode.value,
            "effective": (
                runner.configuration.effective_npc_control_mode.value
            ),
        },
        "tick": runner.clock.tick,
        "simulation_time": runner.clock.simulation_time,
        "calendar_time": calendar_time,
        "environment": environment,
        "world": {
            "station_states": station_states,
            "vehicle_states": vehicle_states,
            "transaction_point_states": transaction_point_states,
            "physical": build_physical_world_snapshot(runner),
        },
        "agents": [
            build_agent_snapshot(runner, entity_id, include_profile=False)
            for entity_id in _agent_entity_ids(registry)
        ],
    }


def build_agent_snapshot(
    runner: SimulationRunner,
    agent_id: str,
    *,
    include_profile: bool = True,
    operator: bool = False,
) -> dict[str, JsonValue]:
    registry = runner.registry
    local_world = local_world_for_agent(registry, agent_id)
    payload: dict[str, JsonValue] = {"id": agent_id}
    if registry.has_component(agent_id, NpcComponent):
        npc = registry.get_component(agent_id, NpcComponent)
        payload["actor_kind"] = "npc"
        payload["npc"] = {
            "role_id": npc.role_id,
            "role_name": npc.role_name,
            "staffed_point_id": npc.staffed_point_id,
            "spawn_sequence": npc.spawn_sequence,
            "spawned_at": npc.spawned_at,
            "control_mode": npc.control_mode.value,
            "transient": npc.transient,
        }
    if (
        include_profile
        and registry.has_component(agent_id, CharacterProfileComponent)
    ):
        profile = registry.get_component(agent_id, CharacterProfileComponent)
        payload["character_profile"] = {
            "profile_id": profile.profile_id,
            "template_id": profile.template_id,
            "template_version": profile.template_version,
            "content_hash": profile.content_hash,
            "display_name": profile.display_name,
            "description": profile.description,
            "data": profile.ui_data,
        }
    if registry.has_component(agent_id, PositionComponent):
        position = registry.get_component(
            agent_id, PositionComponent
        ).coordinate
        payload["position"] = (
            local_world.to_legacy_coordinate(position).to_payload()
            if local_world is not None
            else position.to_payload()
        )
    if registry.has_component(agent_id, SpatialLocationComponent):
        location = registry.get_component(
            agent_id, SpatialLocationComponent
        ).location
        room_id = None
        building_id = None
        city_zone_id = None
        hierarchy_path: list[JsonValue] = []
        if registry.has_resource(CityWorld):
            city = registry.get_resource(CityWorld)
            try:
                room = city.room(location.place_id)
            except KeyError:
                room = None
            if room is not None:
                building = city.building(room.building_id)
                room_id = room.id
                building_id = building.id
                city_zone_id = building.district_id
                hierarchy_path = [
                    city.id,
                    city_zone_id,
                    building_id,
                    room_id,
                ]
            else:
                try:
                    outdoor = city.outdoor_place(location.place_id)
                except KeyError:
                    outdoor = None
                if outdoor is not None:
                    city_zone_id = outdoor.district_id
                    hierarchy_path = [
                        city.id,
                        city_zone_id,
                        outdoor.id,
                    ]
                elif location.place_id == city.id:
                    hierarchy_path = [city.id]
        payload["spatial_location"] = {
            "scale": location.scale.value,
            "place_id": location.place_id,
            "room_id": room_id,
            "building_id": building_id,
            "city_zone_id": city_zone_id,
            "hierarchy_path": hierarchy_path,
            "local_coordinate": (
                (
                    local_world.to_legacy_coordinate(
                        location.local_coordinate
                    ).to_payload()
                    if local_world is not None
                    else location.local_coordinate.to_payload()
                )
                if location.local_coordinate is not None
                else None
            ),
            "network_node_id": location.network_node_id,
            "edge_id": location.edge_id,
            "edge_progress": location.edge_progress,
        }
    if registry.has_component(agent_id, TravelComponent):
        travel = registry.get_component(agent_id, TravelComponent)
        payload["travel"] = {
            "destination_id": travel.destination_id,
            "requested_mode": (
                travel.requested_mode.value
                if travel.requested_mode is not None
                else None
            ),
            "status": travel.status.value,
            "current_leg_index": travel.current_leg_index,
            "leg_count": len(travel.route),
            "vehicle_id": travel.vehicle_id,
            "interruption_requested": travel.interruption_requested,
        }
    if registry.has_component(agent_id, NavigationComponent):
        navigation = registry.get_component(agent_id, NavigationComponent)
        payload["navigation"] = {
            "target_id": navigation.target_id,
            "preferred_mode": (
                navigation.preferred_mode.value
                if navigation.preferred_mode is not None
                else None
            ),
            "status": navigation.status.value,
            "current_primitive_index": navigation.current_primitive_index,
            "primitive_count": len(navigation.primitives),
            "completed_route_legs": navigation.completed_route_legs,
            "route_leg_count": (
                len(navigation.route.legs)
                if navigation.route is not None
                else 0
            ),
            "failure_reason": navigation.failure_reason,
        }
    if registry.has_component(agent_id, HomeostasisComponent):
        payload["homeostasis"] = registry.get_component(
            agent_id, HomeostasisComponent
        ).snapshot()
    if registry.has_component(agent_id, EffectiveSensesComponent):
        senses = registry.get_component(agent_id, EffectiveSensesComponent)
        payload["effective_senses"] = {
            "vision_range": senses.vision_range,
            "recognition_range": senses.recognition_range,
            "hearing_range": senses.hearing_range,
            "smell_range": senses.smell_range,
        }
    if registry.has_component(agent_id, SensesComponent):
        base_senses = registry.get_component(agent_id, SensesComponent)
        payload["base_senses"] = {
            "vision_range": base_senses.vision_range,
            "recognition_range": base_senses.recognition_range,
            "hearing_range": base_senses.hearing_range,
            "smell_range": base_senses.smell_range,
        }
    if registry.has_component(agent_id, EquipmentStateComponent):
        equipment = registry.get_component(agent_id, EquipmentStateComponent)
        payload["equipment"] = {
            slot.value: list(object_ids)
            for slot, object_ids in sorted(
                equipment.equipped_object_ids.items(),
                key=lambda item: item[0].value,
            )
        }
    if registry.has_component(agent_id, CarriedLoadComponent):
        load = registry.get_component(agent_id, CarriedLoadComponent)
        embodiment = (
            registry.get_component(agent_id, CharacterEmbodimentComponent)
            if registry.has_component(
                agent_id,
                CharacterEmbodimentComponent,
            )
            else None
        )
        payload["carried_load"] = {
            "known_mass_kg": load.known_mass_kg,
            "unknown_mass_object_ids": list(load.unknown_mass_object_ids),
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
    if registry.has_component(agent_id, PossessionsComponent):
        possessions = registry.get_component(
            agent_id, PossessionsComponent
        )
        catalog = registry.get_resource(ItemCatalog)
        payload["possessions"] = [
            {
                "item_id": item_id,
                "name": catalog.item(item_id).name,
                "unit": catalog.item(item_id).unit,
                "quantity": quantity,
            }
            for item_id, quantity in sorted(possessions.holdings.items())
        ]
    if registry.has_component(agent_id, ActivityComponent):
        payload["activity"] = registry.get_component(
            agent_id, ActivityComponent
        ).current.value
    if registry.has_component(agent_id, MovementComponent):
        movement = registry.get_component(agent_id, MovementComponent)
        payload["movement"] = {
            "destination": (
                (
                    local_world.to_legacy_coordinate(
                        movement.destination
                    ).to_payload()
                    if local_world is not None
                    else movement.destination.to_payload()
                )
                if movement.destination is not None
                else None
            ),
            "path": [
                (
                    local_world.to_legacy_coordinate(coordinate).to_payload()
                    if local_world is not None
                    else coordinate.to_payload()
                )
                for coordinate in movement.path
            ],
        }
        if operator:
            payload["physical_movement"] = {
                "coordinate_system": (
                    local_world.coordinate_system.value
                    if local_world is not None
                    else "microcell"
                ),
                "destination": (
                    movement.destination.to_payload()
                    if movement.destination is not None
                    else None
                ),
                "path": [
                    coordinate.to_payload()
                    for coordinate in movement.path
                ],
            }
    if registry.has_component(agent_id, DriveComponent):
        drive = registry.get_component(agent_id, DriveComponent)
        payload["system1"] = {
            "state": drive.state.value,
            "active_drive": (
                drive.active_drive.value if drive.active_drive is not None else None
            ),
            "target_station_id": drive.target_station_id,
        }
    if registry.has_component(agent_id, PlanComponent):
        plan = registry.get_component(agent_id, PlanComponent)
        payload["plan"] = {
            "current": _plan_action_payload(plan.current),
            "queue": [_plan_action_payload(action) for action in plan.queue],
            "remaining_duration": plan.remaining_duration,
        }
    if registry.has_component(agent_id, ControllerComponent):
        controller = registry.get_component(agent_id, ControllerComponent)
        payload["controller"] = {
            "enabled": controller.enabled,
            "request_pending": controller.request_pending,
            "state_revision": controller.state_revision,
            "current_decision_id": controller.current_decision_id,
            "last_outcome": controller.last_outcome,
            "next_decision_time": controller.next_decision_time,
        }
    if registry.has_component(agent_id, CharacterSituationComponent):
        situation = registry.get_component(
            agent_id, CharacterSituationComponent
        )
        payload["character_situation"] = {
            "slot_id": situation.slot_id,
            "label": situation.label,
            "briefing": situation.briefing,
            "description": situation.description,
            "content_hash": situation.content_hash,
            "input_hash": situation.input_hash,
            "data": situation.data,
            "generation": situation.generation,
        }
    if registry.has_component(agent_id, PerceptionComponent):
        perception = registry.get_component(agent_id, PerceptionComponent)
        visible_now: list[JsonValue] = list(sorted(perception.visible_now))
        payload["perception"] = {
            "inbox_count": len(perception.inbox),
            "visible_now": visible_now,
            "known_character_count": len(perception.knowledge),
        }
    if registry.has_component(agent_id, MemoryComponent):
        store = registry.get_resource(EpisodicMemoryStore)
        payload["memory"] = {
            "count": sum(
                record.agent_id == agent_id for record in store.records
            )
        }
    if registry.has_component(agent_id, ConversationComponent):
        conversation = registry.get_component(agent_id, ConversationComponent)
        payload["conversation"] = {
            "turn_count": len(conversation.turns),
            "latest_turn": conversation.turns[-1] if conversation.turns else None,
        }
    payload["engagement"] = _engagement_snapshot(runner, agent_id)
    if registry.has_component(agent_id, PhysicalStateComponent):
        physical = _physical_state_payload(
            runner,
            agent_id,
            registry.get_component(agent_id, PhysicalStateComponent),
            operator=operator,
        )
        posture = (
            registry.get_component(agent_id, CharacterPostureComponent)
            if registry.has_component(
                agent_id,
                CharacterPostureComponent,
            )
            else None
        )
        hands = (
            registry.get_component(agent_id, CharacterHandStateComponent)
            if registry.has_component(
                agent_id,
                CharacterHandStateComponent,
            )
            else None
        )
        physical["posture"] = (
            {
                "value": posture.posture.value,
                "support_id": posture.support_id,
            }
            if posture is not None
            else None
        )
        physical["hands"] = (
            {
                "left_object_id": hands.left_hand_object_id,
                "right_object_id": hands.right_hand_object_id,
                "held_object_ids": _string_payloads(
                    sorted(hands.held_object_ids)
                ),
            }
            if hands is not None
            else None
        )
        payload["physical"] = physical
    interaction = _interaction_state_payload(registry, agent_id)
    if interaction is not None:
        payload["interaction"] = interaction
    return payload


def _build_agent_static_snapshot(
    runner: SimulationRunner,
    agent_id: str,
) -> dict[str, JsonValue]:
    registry = runner.registry
    payload: dict[str, JsonValue] = {"id": agent_id}
    if registry.has_component(agent_id, NpcComponent):
        npc = registry.get_component(agent_id, NpcComponent)
        payload["actor_kind"] = "npc"
        payload["npc"] = {
            "role_id": npc.role_id,
            "role_name": npc.role_name,
            "staffed_point_id": npc.staffed_point_id,
            "control_mode": npc.control_mode.value,
            "transient": npc.transient,
        }
    if registry.has_component(agent_id, CharacterProfileComponent):
        profile = registry.get_component(agent_id, CharacterProfileComponent)
        payload["character_profile"] = {
            "profile_id": profile.profile_id,
            "template_id": profile.template_id,
            "template_version": profile.template_version,
            "content_hash": profile.content_hash,
            "display_name": profile.display_name,
            "description": profile.description,
            "data": profile.ui_data,
        }
    return payload


def _agent_entity_ids(registry: Registry) -> tuple[str, ...]:
    return tuple(
        entity_id
        for entity_id in registry.entities()
        if not registry.has_component(
            entity_id,
            PhysicalObjectIdentityComponent,
        )
    )


def _interaction_state_payload(
    registry: Registry,
    agent_id: str,
) -> dict[str, JsonValue] | None:
    request = (
        registry.get_component(agent_id, InteractionRequestComponent)
        if registry.has_component(agent_id, InteractionRequestComponent)
        else None
    )
    execution = (
        registry.get_component(agent_id, InteractionExecutionComponent)
        if registry.has_component(agent_id, InteractionExecutionComponent)
        else None
    )
    if request is None and execution is None:
        return None
    return {
        "request": (
            {
                **_interaction_specification_payload(
                    request.specification,
                ),
                "source": request.source,
                "status": request.status,
                "failure_reason": request.failure_reason,
                "action": _action_instance_state_payload(
                    request.action_instance,
                    status=request.status,
                ),
            }
            if request is not None
            else None
        ),
        "execution": (
            {
                **_interaction_specification_payload(
                    execution.specification,
                ),
                "source": execution.source,
                "status": "running",
                "elapsed": execution.elapsed,
                "duration": execution.duration,
                "correlation_id": execution.correlation_id,
                "action": _action_instance_state_payload(
                    execution.action_instance,
                    status="running",
                ),
            }
            if execution is not None
            else None
        ),
    }


def _interaction_specification_payload(
    specification: InteractionSpecification,
) -> dict[str, JsonValue]:
    return {
        "verb": specification.verb.value,
        "target_id": specification.target_id,
        "destination_id": specification.destination_id,
        "slot_id": specification.slot_id,
    }


def _action_instance_state_payload(
    action: ActionInstance | None,
    *,
    status: str,
) -> JsonValue:
    if action is None:
        return None
    return {
        "action_id": action.action_id,
        "status": status,
        "origin": action.origin.value,
        "action_name": action.action_name,
        "target_id": action.target,
        "created_tick": action.created_tick,
        "created_at": action.created_at,
        "root_correlation_id": action.root_correlation_id,
        "plan_id": action.plan_id,
        "plan_revision": action.plan_revision,
        "goal_ids": _string_payloads(sorted(action.goal_ids)),
    }


def _plan_action_payload(
    action: ActionInstance | None,
) -> JsonValue:
    if action is None:
        return None
    payload: dict[str, JsonValue] = {"action": action.action.value}
    if action.target is not None:
        payload["target"] = action.target
    if action.duration is not None:
        payload["duration"] = action.duration
    if action.mode is not None:
        payload["mode"] = action.mode.value
    if action.offer_id is not None:
        payload["offer_id"] = action.offer_id
    return payload


def _message_type_for_event(event_type: str) -> str:
    if event_type == "homeostasis.changed":
        return "homeostasis_delta"
    if event_type.startswith(("plan.", "action.", "navigation.")):
        return "plan_changed"
    if event_type.startswith("system1.") or event_type == "threshold.breached":
        return "system1_event"
    if event_type.startswith("speech."):
        return "dialogue_event"
    if event_type.startswith(("cognition.", "tool.")):
        return "cognition_event"
    if event_type.startswith("engagement."):
        return "engagement_event"
    if event_type.startswith(("speech.", "perception.")):
        return "perception_event"
    if event_type.startswith(("travel.", "building.", "vehicle.", "metro.")):
        return "travel_event"
    if event_type.startswith(
        ("agent.", "path.", "activity.", "affordance.", "transaction.")
    ):
        return "agent_delta"
    return "event"


_ENGAGEMENT_SAFE_EVENT_FIELDS = (
    "engagement_id",
    "action_id",
    "plan_id",
    "plan_revision",
    "decision_id",
    "tool_call_id",
    "root_correlation_id",
    "reference_ids",
    "group_id",
    "group_ordinal",
    "required_atomic",
    "invocation_id",
    "invocation_ordinal",
    "invocation_ids",
    "capability",
    "consequence_tier",
    "modality",
    "disclosure",
    "public_text",
    "expression_band",
    "activity",
    "duration_band",
    "duration_seconds",
    "effort_band",
    "mode",
    "sound_band",
    "sound_range",
    "target_id",
    "recipient_ids",
    "group_count",
    "rejected_group_count",
    "completed_group_count",
    "failed_group_count",
)

_ENGAGEMENT_TERMINAL_STATUSES = frozenset(
    {"partial", "completed", "failed", "cancelled"}
)


def project_operator_event(event: DomainEvent) -> DomainEvent | None:
    """Return a privacy-safe event for public telemetry and operator views."""
    if not event.event_type.startswith("engagement."):
        return None if event_payload_is_private(event.payload) else event
    payload: dict[str, JsonValue] = {
        name: event.payload[name]
        for name in _ENGAGEMENT_SAFE_EVENT_FIELDS
        if name in event.payload
    }
    group_statuses = event.payload.get("group_statuses")
    if isinstance(group_statuses, list):
        payload["group_statuses"] = [
            {
                key: value[key]
                for key in (
                    "group_id",
                    "group_ordinal",
                    "required_atomic",
                    "invocation_ids",
                    "status",
                )
                if key in value
            }
            for value in group_statuses
            if isinstance(value, dict)
        ]
    phase, status = _engagement_event_phase_status(event.event_type)
    payload["engagement_phase"] = phase
    payload["engagement_status"] = status
    participant_ids = _engagement_participant_ids(
        event.agent_id,
        payload,
    )
    if participant_ids:
        payload["participant_ids"] = participant_ids
    return replace(event, payload=payload)


def _engagement_event_phase_status(event_type: str) -> tuple[str, str]:
    suffix = event_type.removeprefix("engagement.")
    return {
        "requested": ("execution", "requested"),
        "compilation_requested": ("compilation", "pending"),
        "compilation_completed": ("compilation", "succeeded"),
        "compilation_failed": ("compilation", "failed"),
        "compilation_cancelled": ("compilation", "cancelled"),
        "started": ("execution", "started"),
        "group_completed": ("execution", "group_completed"),
        "group_failed": ("execution", "group_failed"),
        "capability_committed": ("execution", "committed"),
        "partial": ("execution", "partial"),
        "completed": ("execution", "completed"),
        "failed": ("execution", "failed"),
        "cancelled": ("execution", "cancelled"),
    }.get(suffix, ("execution", suffix))


def _engagement_participant_ids(
    actor_id: str | None,
    payload: Mapping[str, JsonValue],
) -> list[JsonValue]:
    participant_ids: set[str] = {actor_id} if actor_id is not None else set()
    for field in ("reference_ids", "recipient_ids"):
        values = payload.get(field)
        if isinstance(values, list):
            participant_ids.update(
                value for value in values if isinstance(value, str)
            )
    target_id = payload.get("target_id")
    if isinstance(target_id, str):
        participant_ids.add(target_id)
    return list(sorted(participant_ids))


def _engagement_snapshot(
    runner: SimulationRunner,
    agent_id: str,
) -> dict[str, JsonValue]:
    registry = runner.registry
    projections = _engagement_event_projections(runner, agent_id)
    projection_by_id = {
        str(projection["engagement_id"]): projection
        for projection in projections
    }
    pending: dict[str, JsonValue] | None = None
    compiled: dict[str, JsonValue] | None = None
    active: dict[str, JsonValue] | None = None
    if registry.has_component(agent_id, PendingEngagementComponent):
        pending_component = registry.get_component(
            agent_id,
            PendingEngagementComponent,
        )
        pending = _pending_engagement_payload(
            runner,
            agent_id,
            pending_component,
            projection_by_id.get(pending_component.engagement_id),
        )
    if registry.has_component(agent_id, EngagementProgramComponent):
        program_component = registry.get_component(
            agent_id,
            EngagementProgramComponent,
        )
        compiled = _program_engagement_payload(
            runner,
            agent_id,
            program_component.program,
            status="compiled",
            compiler_status="succeeded",
            event_projection=projection_by_id.get(
                program_component.program.engagement_id
            ),
        )
    if registry.has_component(agent_id, EngagementExecutionComponent):
        execution_component = registry.get_component(
            agent_id,
            EngagementExecutionComponent,
        )
        active = _execution_engagement_payload(
            runner,
            agent_id,
            execution_component,
            projection_by_id.get(
                execution_component.program.engagement_id
            ),
        )
    current_ids = {
        str(item["engagement_id"])
        for item in (pending, compiled, active)
        if item is not None
    }
    recent = cast(
        list[JsonValue],
        [
        projection
        for projection in reversed(projections)
        if (
            str(projection.get("status")) in _ENGAGEMENT_TERMINAL_STATUSES
            or str(projection.get("compiler_status")) in {"failed", "cancelled"}
        )
        and str(projection["engagement_id"]) not in current_ids
        ][:5],
    )
    return {
        "pending": pending,
        "compiled": compiled,
        "active": active,
        "recent": recent,
    }


def _pending_engagement_payload(
    runner: SimulationRunner,
    agent_id: str,
    component: PendingEngagementComponent,
    event_projection: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    references = _current_engagement_reference_ids(
        runner,
        agent_id,
        component.engagement_id,
        event_projection,
    )
    return {
        "engagement_id": component.engagement_id,
        "actor_id": agent_id,
        "status": "pending",
        "compiler_status": "pending",
        "action_id": component.action_id,
        "plan_id": component.plan_id,
        "plan_revision": component.plan_revision,
        "decision_id": component.decision_id,
        "tool_call_id": component.tool_call_id,
        "root_correlation_id": component.root_correlation_id,
        "requested_tick": component.requested_tick,
        "reference_ids": _string_payloads(references),
        "participant_ids": list(sorted({agent_id, *references})),
        "current_group_id": None,
        "progress": _engagement_progress([]),
        "groups": [],
        "evidence": (
            _projected_evidence(event_projection)
        ),
    }


def _program_engagement_payload(
    runner: SimulationRunner,
    agent_id: str,
    program: EngagementProgram,
    *,
    status: str,
    compiler_status: str,
    event_projection: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    references = _current_engagement_reference_ids(
        runner,
        agent_id,
        program.engagement_id,
        event_projection,
    )
    groups: list[JsonValue] = [
        {
            "group_id": group.group_id,
            "ordinal": group.ordinal,
            "required_atomic": group.required_atomic,
            "status": "pending",
            "invocation_ids": [
                invocation.invocation_id
                for invocation in group.invocations
            ],
            "outcomes": [],
        }
        for group in program.groups
    ]
    payload: dict[str, JsonValue] = {
        "engagement_id": program.engagement_id,
        "actor_id": agent_id,
        "status": status,
        "compiler_status": compiler_status,
        "action_id": program.action_id,
        "plan_id": program.plan_id,
        "plan_revision": program.plan_revision,
        "decision_id": program.decision_id,
        "tool_call_id": program.tool_call_id,
        "root_correlation_id": program.root_correlation_id,
        "requested_tick": program.requested_tick,
        "reference_ids": _string_payloads(references),
        "participant_ids": list(sorted({agent_id, *references})),
        "current_group_id": None,
        "progress": _engagement_progress(groups),
        "groups": groups,
        "evidence": [],
    }
    return _merge_engagement_evidence(payload, event_projection)


def _execution_engagement_payload(
    runner: SimulationRunner,
    agent_id: str,
    execution: EngagementExecutionComponent,
    event_projection: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    payload = _program_engagement_payload(
        runner,
        agent_id,
        execution.program,
        status=execution.status.value,
        compiler_status="succeeded",
        event_projection=event_projection,
    )
    groups: list[JsonValue] = []
    for index, group_state in enumerate(execution.groups):
        group = execution.program.groups[index]
        groups.append(
            {
                "group_id": group.group_id,
                "ordinal": group.ordinal,
                "required_atomic": group.required_atomic,
                "status": group_state.status.value,
                "invocation_ids": [
                    invocation.invocation_id
                    for invocation in group.invocations
                ],
                "outcomes": _group_outcomes(
                    event_projection,
                    group.group_id,
                ),
            }
        )
    payload["groups"] = groups
    payload["current_group_id"] = execution.active_group_id
    payload["started_tick"] = execution.started_tick
    payload["progress"] = _engagement_progress(
        groups,
        current_group_id=execution.active_group_id,
    )
    return payload


def _merge_engagement_evidence(
    payload: dict[str, JsonValue],
    event_projection: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    if event_projection is None:
        return payload
    evidence = event_projection.get("evidence")
    if isinstance(evidence, list):
        payload["evidence"] = evidence
    participant_ids = event_projection.get("participant_ids")
    if isinstance(participant_ids, list):
        payload["participant_ids"] = participant_ids
    return payload


def _group_outcomes(
    event_projection: dict[str, JsonValue] | None,
    group_id: str,
) -> list[JsonValue]:
    if event_projection is None:
        return []
    evidence = event_projection.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("group_id") == group_id
    ]


def _engagement_progress(
    groups: list[JsonValue],
    *,
    current_group_id: str | None = None,
) -> dict[str, JsonValue]:
    statuses = [
        str(group.get("status"))
        for group in groups
        if isinstance(group, dict)
    ]
    return {
        "group_count": len(statuses),
        "completed_group_count": statuses.count("completed"),
        "failed_group_count": statuses.count("failed"),
        "cancelled_group_count": statuses.count("cancelled"),
        "current_group_id": current_group_id,
    }


def _current_engagement_reference_ids(
    runner: SimulationRunner,
    agent_id: str,
    engagement_id: str,
    event_projection: dict[str, JsonValue] | None,
) -> list[str]:
    registry = runner.registry
    if registry.has_component(agent_id, PlanComponent):
        plan = registry.get_component(agent_id, PlanComponent)
        for action in (plan.current, *plan.queue):
            if (
                action is not None
                and action.engagement is not None
                and action.engagement.engagement_id == engagement_id
            ):
                return list(sorted(action.engagement.reference_ids))
    if event_projection is not None:
        references = event_projection.get("reference_ids")
        if isinstance(references, list):
            return list(
                sorted(
                    value
                    for value in references
                    if isinstance(value, str)
                )
            )
    return []


def _engagement_event_projections(
    runner: SimulationRunner,
    agent_id: str,
) -> list[dict[str, JsonValue]]:
    projections: dict[str, dict[str, object]] = {}
    for source_event in runner.events.events:
        if source_event.agent_id != agent_id:
            continue
        event = project_operator_event(source_event)
        if event is None or not event.event_type.startswith("engagement."):
            continue
        engagement_id = event.payload.get("engagement_id")
        if not isinstance(engagement_id, str):
            continue
        projection = projections.setdefault(
            engagement_id,
            {
                "engagement_id": engagement_id,
                "actor_id": agent_id,
                "status": "requested",
                "compiler_status": None,
                "action_id": None,
                "plan_id": None,
                "plan_revision": None,
                "decision_id": None,
                "tool_call_id": None,
                "root_correlation_id": None,
                "requested_tick": source_event.simulation_tick,
                "started_tick": None,
                "terminal_tick": None,
                "reference_ids": [],
                "participant_ids": {agent_id},
                "current_group_id": None,
                "groups": {},
                "evidence": [],
                "last_tick": source_event.simulation_tick,
            },
        )
        projection["last_tick"] = source_event.simulation_tick
        for name in (
            "action_id",
            "plan_id",
            "plan_revision",
            "decision_id",
            "tool_call_id",
            "root_correlation_id",
        ):
            if name in event.payload:
                projection[name] = event.payload[name]
        references = event.payload.get("reference_ids")
        if isinstance(references, list):
            projection["reference_ids"] = [
                value for value in references if isinstance(value, str)
            ]
        participants = projection["participant_ids"]
        if isinstance(participants, set):
            participants.update(
                value
                for value in _engagement_participant_ids(
                    agent_id,
                    event.payload,
                )
                if isinstance(value, str)
            )
        phase = event.payload.get("engagement_phase")
        event_status = event.payload.get("engagement_status")
        if phase == "compilation":
            projection["compiler_status"] = event_status
            if event_status == "failed":
                projection["status"] = "failed"
                projection["terminal_tick"] = source_event.simulation_tick
            elif event_status == "cancelled":
                projection["status"] = "cancelled"
                projection["terminal_tick"] = source_event.simulation_tick
            elif event_status == "succeeded":
                projection["status"] = "compiled"
        elif event.event_type == "engagement.started":
            projection["status"] = "running"
            projection["started_tick"] = source_event.simulation_tick
        elif event_status in _ENGAGEMENT_TERMINAL_STATUSES:
            projection["status"] = event_status
            projection["terminal_tick"] = source_event.simulation_tick
        if event.event_type in {
            "engagement.group_completed",
            "engagement.group_failed",
        }:
            _update_projected_group(projection, event)
        elif event.event_type == "engagement.capability_committed":
            _append_projected_evidence(projection, event)
        group_statuses = event.payload.get("group_statuses")
        if isinstance(group_statuses, list):
            for group_status in group_statuses:
                if isinstance(group_status, dict):
                    _update_projected_group_status(
                        projection,
                        group_status,
                    )
    results: list[dict[str, JsonValue]] = []
    for projection in sorted(
        projections.values(),
        key=_engagement_projection_sort_key,
    ):
        groups = projection.pop("groups")
        participants = projection.pop("participant_ids")
        projection.pop("last_tick")
        group_rows = (
            sorted(
                groups.values(),
                key=lambda item: (
                    (
                        item.get("ordinal")
                        if isinstance(item.get("ordinal"), int)
                        else 1_000_000
                    ),
                    str(item["group_id"]),
                ),
            )
            if isinstance(groups, dict)
            else []
        )
        projection["groups"] = group_rows
        projection["progress"] = _engagement_progress(
            cast(list[JsonValue], group_rows),
        )
        projection["participant_ids"] = (
            list(sorted(participants))
            if isinstance(participants, set)
            else []
        )
        results.append(cast(dict[str, JsonValue], projection))
    return results


def _projected_evidence(
    event_projection: dict[str, JsonValue] | None,
) -> list[JsonValue]:
    if event_projection is None:
        return []
    evidence = event_projection.get("evidence")
    return evidence if isinstance(evidence, list) else []


def _engagement_projection_sort_key(
    projection: Mapping[str, object],
) -> tuple[int, str]:
    last_tick = projection.get("last_tick")
    return (
        last_tick if isinstance(last_tick, int) else -1,
        str(projection.get("engagement_id", "")),
    )


def _update_projected_group(
    projection: dict[str, object],
    event: DomainEvent,
) -> None:
    group_id = event.payload.get("group_id")
    if not isinstance(group_id, str):
        return
    groups = projection["groups"]
    if not isinstance(groups, dict):
        return
    group = groups.setdefault(group_id, {"group_id": group_id, "outcomes": []})
    if not isinstance(group, dict):
        return
    for source, target in (
        ("group_ordinal", "ordinal"),
        ("required_atomic", "required_atomic"),
        ("invocation_ids", "invocation_ids"),
    ):
        if source in event.payload:
            group[target] = event.payload[source]
    group["status"] = (
        "completed"
        if event.event_type == "engagement.group_completed"
        else "failed"
    )


def _update_projected_group_status(
    projection: dict[str, object],
    group_status: Mapping[str, JsonValue],
) -> None:
    group_id = group_status.get("group_id")
    if not isinstance(group_id, str):
        return
    groups = projection["groups"]
    if not isinstance(groups, dict):
        return
    group = groups.setdefault(group_id, {"group_id": group_id, "outcomes": []})
    if not isinstance(group, dict):
        return
    for source, target in (
        ("group_ordinal", "ordinal"),
        ("required_atomic", "required_atomic"),
        ("invocation_ids", "invocation_ids"),
        ("status", "status"),
    ):
        if source in group_status:
            group[target] = group_status[source]


def _append_projected_evidence(
    projection: dict[str, object],
    event: DomainEvent,
) -> None:
    evidence = projection["evidence"]
    if not isinstance(evidence, list):
        return
    item = {
        name: value
        for name, value in event.payload.items()
        if name
        in {
            "group_id",
            "group_ordinal",
            "invocation_id",
            "capability",
            "modality",
            "disclosure",
            "public_text",
            "expression_band",
            "activity",
            "duration_band",
            "duration_seconds",
            "effort_band",
            "mode",
            "sound_band",
            "sound_range",
            "target_id",
            "recipient_ids",
        }
    }
    item["simulation_tick"] = event.simulation_tick
    evidence.append(item)
    group_id = event.payload.get("group_id")
    groups = projection["groups"]
    if isinstance(group_id, str) and isinstance(groups, dict):
        group = groups.setdefault(
            group_id,
            {"group_id": group_id, "status": "running", "outcomes": []},
        )
        if isinstance(group, dict):
            outcomes = group.setdefault("outcomes", [])
            if isinstance(outcomes, list):
                outcomes.append(item)
