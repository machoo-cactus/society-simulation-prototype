import json
import math
from collections.abc import Mapping, Sequence, Set
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
