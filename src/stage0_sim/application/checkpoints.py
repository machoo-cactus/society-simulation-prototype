from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from inspect import isclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from stage0_sim import __version__
from stage0_sim.application.characters import PreparedScenario
from stage0_sim.application.data_capture import qualified_type_name
from stage0_sim.application.information import InformationStore
from stage0_sim.application.memory import EpisodicMemoryStore, MemoryRecord
from stage0_sim.application.runner import RunnerStatus, SimulationRunner
from stage0_sim.domain import components as component_module
from stage0_sim.domain.content import TextContentRegistry
from stage0_sim.domain.events import DomainEvent, JsonValue
from stage0_sim.domain.information import (
    information_document_from_dict,
)
from stage0_sim.domain.world import SpaceRegistry, SpatialIndex, SpatialIndexEntry, WorldMap

CHECKPOINT_SCHEMA_VERSION = "stage0.checkpoint.v1"
RUNTIME_CHECKPOINT_COMPATIBILITY_VERSION = "stage0.runtime-checkpoint.v1"

_OPERATIONAL_RESOURCES = frozenset(
    {
        "stage0_sim.application.data_capture.recorder.BufferedResearchRecorder",
        "stage0_sim.application.agents.coordinator.AgentWorkCoordinator",
        "stage0_sim.application.engagements.coordinator.EngagementWorkCoordinator",
        "stage0_sim.application.environment.EnvironmentInformationService",
        "stage0_sim.application.information.retrieval.InformationRetriever",
        "stage0_sim.application.memory_recording.MemoryWorkCoordinator",
        "stage0_sim.application.navigation.service.NavigationService",
        "stage0_sim.domain.components.text_action.TextContentPersistenceBinding",
    }
)
_VALIDATE_ONLY_RESOURCES = (SpaceRegistry, WorldMap)


class CheckpointCompatibilityError(ValueError):
    pass


class CheckpointState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = CHECKPOINT_SCHEMA_VERSION
    runtime_compatibility_version: str = RUNTIME_CHECKPOINT_COMPATIBILITY_VERSION
    application_version: str = __version__
    prepared_scenario: dict[str, JsonValue]
    runner: dict[str, JsonValue]
    registry: dict[str, JsonValue]
    collector: dict[str, JsonValue]
    integrity: str = Field(min_length=64, max_length=64)


class CheckpointSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    run_id: str
    label: str | None = None
    simulation_tick: int
    simulation_time: float
    speed: float
    event_count: int
    dataset_sequence: int
    created_at: datetime
    is_head: bool = False
    compatible: bool = True
    restore_mode: str = "branch"


class CheckpointRestoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    source_run_id: str
    run_id: str
    branched: bool
    status: str = RunnerStatus.PAUSED.value


def create_checkpoint_state(
    runner: SimulationRunner,
    prepared: PreparedScenario,
    collector_state: dict[str, JsonValue],
) -> CheckpointState:
    runner.require_checkpoint_boundary()
    content: dict[str, JsonValue] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "runtime_compatibility_version": RUNTIME_CHECKPOINT_COMPATIBILITY_VERSION,
        "application_version": __version__,
        "prepared_scenario": prepared.to_checkpoint_payload(),
        "runner": {
            "tick": runner.clock.tick,
            "speed": runner.speed,
            "status": runner.status.value,
            "cognition_phase": runner.cognition_phase.value,
            "rng_state": _json_rng_state(runner.rng.getstate()),
            "event_count": len(runner.events.events),
            "events": [event.to_dict() for event in runner.events.events],
            "research_next_sequence": runner.research_recorder.next_sequence,
            "npc_control_mode": runner.configuration.npc_control_mode.value,
            "effective_npc_control_mode": (
                runner.configuration.effective_npc_control_mode.value
            ),
        },
        "registry": _capture_registry(runner),
        "collector": collector_state,
    }
    return CheckpointState(
        prepared_scenario=prepared.to_checkpoint_payload(),
        runner=cast(dict[str, JsonValue], content["runner"]),
        registry=cast(dict[str, JsonValue], content["registry"]),
        collector=collector_state,
        integrity=_fingerprint(content),
    )


def validate_checkpoint_state(state: CheckpointState) -> None:
    if state.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointCompatibilityError(
            f"unsupported checkpoint schema version {state.schema_version}; "
            f"expected {CHECKPOINT_SCHEMA_VERSION}"
        )
    if (
        state.runtime_compatibility_version
        != RUNTIME_CHECKPOINT_COMPATIBILITY_VERSION
    ):
        raise CheckpointCompatibilityError(
            "checkpoint runtime contract is incompatible with this application"
        )
    content = state.model_dump(mode="json", exclude={"integrity"})
    if _fingerprint(cast(dict[str, JsonValue], content)) != state.integrity:
        raise CheckpointCompatibilityError("checkpoint integrity validation failed")


def restore_checkpoint_state(
    runner: SimulationRunner,
    state: CheckpointState,
    *,
    continue_identity: bool,
) -> None:
    validate_checkpoint_state(state)
    requested_mode = state.runner.get("npc_control_mode")
    effective_mode = state.runner.get("effective_npc_control_mode")
    if (
        requested_mode != runner.configuration.npc_control_mode.value
        or effective_mode
        != runner.configuration.effective_npc_control_mode.value
    ):
        raise CheckpointCompatibilityError(
            "checkpoint NPC control configuration is unavailable"
        )
    _restore_registry(runner, state.registry)
    runner_state = state.runner
    tick = _required_int(runner_state, "tick")
    speed = _required_float(runner_state, "speed")
    event_count = _required_int(runner_state, "event_count")
    event_history = (
        _events_from_payload(runner_state.get("events"))
        if continue_identity
        else ()
    )
    research_next_sequence = _required_int(
        runner_state,
        "research_next_sequence",
    )
    rng_state = runner_state.get("rng_state")
    runner.restore_paused(
        tick=tick,
        speed=speed,
        rng_state=_tuple_rng_state(rng_state),
        event_count=event_count if continue_identity else 0,
        event_history=event_history,
        research_next_sequence=(
            research_next_sequence if continue_identity else 1
        ),
    )


def checkpoint_system_manifest(
    runner: SimulationRunner,
) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        {
            "type": qualified_type_name(system),
            "name": system.name,
            "order": system.order,
            "ordinal": ordinal,
        }
        for ordinal, system in enumerate(runner.systems.systems)
    )


def _capture_registry(runner: SimulationRunner) -> dict[str, JsonValue]:
    registry = runner.registry
    entities: list[JsonValue] = []
    component_manifest: set[str] = set()
    for entity_id in registry.entities():
        components: dict[str, JsonValue] = {}
        for component in registry.components(entity_id):
            type_name = qualified_type_name(component)
            component_manifest.add(type_name)
            components[type_name] = cast(
                JsonValue,
                TypeAdapter(type(component)).dump_python(component, mode="json"),
            )
        entities.append({"entity_id": entity_id, "components": components})
    resources: dict[str, JsonValue] = {}
    resource_manifest: list[JsonValue] = []
    for resource_type, resource in registry.resource_items():
        type_name = qualified_type_name(resource_type)
        if type_name in _OPERATIONAL_RESOURCES:
            resource_manifest.append(
                {"type": type_name, "classification": "operational"}
            )
            continue
        resources[type_name] = _dump_resource(resource)
        resource_manifest.append(
            {
                "type": type_name,
                "classification": (
                    "validate_only"
                    if isinstance(resource, _VALIDATE_ONLY_RESOURCES)
                    else "authoritative"
                ),
            }
        )
    return {
        "next_entity_number": registry.next_entity_number,
        "entities": entities,
        "resources": resources,
        "component_manifest": cast(
            JsonValue,
            sorted(component_manifest),
        ),
        "resource_manifest": resource_manifest,
        "system_manifest": list(checkpoint_system_manifest(runner)),
    }


def _dump_resource(resource: object) -> JsonValue:
    if isinstance(resource, InformationStore):
        documents: list[JsonValue] = []
        for current in resource.documents():
            documents.extend(
                document.to_dict() for document in resource.history(current.id)
            )
        return {"documents": documents}
    if isinstance(resource, EpisodicMemoryStore):
        return {
            "records": cast(
                JsonValue,
                TypeAdapter(tuple[MemoryRecord, ...]).dump_python(
                    resource.records,
                    mode="json",
                ),
            ),
            "next_id": resource.next_id,
        }
    if isinstance(resource, TextContentRegistry):
        return resource.to_dict()
    if isinstance(resource, SpatialIndex):
        return {
            "entries": cast(
                JsonValue,
                TypeAdapter(tuple[SpatialIndexEntry, ...]).dump_python(
                    resource.entries(),
                    mode="json",
                ),
            ),
            "revision": resource.revision,
            "topology_revision": resource.topology_revision,
        }
    if isinstance(resource, SpaceRegistry):
        from stage0_sim.application.data_capture import serialize_authoritative

        return {
            "revision": resource.revision,
            "spaces": serialize_authoritative(resource.spaces()),
            "containment": [
                {
                    "parent_id": parent.id,
                    "child_id": child.id,
                }
                for parent in resource.spaces()
                for child in resource.child_spaces(parent.id)
            ],
            "transitions": serialize_authoritative(resource.transitions()),
            "destinations": {
                destination_id: serialize_authoritative(
                    resource.destination_locators(destination_id)
                )
                for destination_id in resource.destination_ids()
            },
        }
    try:
        return cast(
            JsonValue,
            TypeAdapter(type(resource)).dump_python(resource, mode="json"),
        )
    except Exception:
        from stage0_sim.application.data_capture import serialize_authoritative

        return serialize_authoritative(resource)


def _restore_registry(
    runner: SimulationRunner,
    payload: Mapping[str, JsonValue],
) -> None:
    registry = runner.registry
    system_manifest = payload.get("system_manifest")
    if system_manifest != list(checkpoint_system_manifest(runner)):
        raise CheckpointCompatibilityError(
            "checkpoint ordered-system manifest does not match this runtime"
        )
    entities = payload.get("entities")
    if not isinstance(entities, list):
        raise CheckpointCompatibilityError("checkpoint entities must be a list")
    type_registry = _component_type_registry(runner)
    expected_entity_ids: set[str] = set()
    decoded_components: dict[str, dict[type[object], object]] = {}
    for item in entities:
        if not isinstance(item, dict):
            raise CheckpointCompatibilityError(
                "checkpoint entity entries must be objects"
            )
        entity_id = item.get("entity_id")
        components = item.get("components")
        if not isinstance(entity_id, str) or not isinstance(components, dict):
            raise CheckpointCompatibilityError("invalid checkpoint entity entry")
        expected_entity_ids.add(entity_id)
        decoded: dict[type[object], object] = {}
        for type_name, component_payload in components.items():
            component_type = type_registry.get(type_name)
            if component_type is None:
                raise CheckpointCompatibilityError(
                    f"unsupported checkpoint component type: {type_name}"
                )
            decoded[component_type] = TypeAdapter(component_type).validate_python(
                component_payload
            )
        decoded_components[entity_id] = decoded
    for entity_id in set(registry.entities()) - expected_entity_ids:
        registry.delete_entity(entity_id)
    for entity_id in sorted(expected_entity_ids - set(registry.entities())):
        registry.create_entity(entity_id)
    for entity_id in sorted(expected_entity_ids):
        expected_types = set(decoded_components[entity_id])
        for component in registry.components(entity_id):
            if type(component) not in expected_types:
                registry.remove_component(entity_id, type(component))
        for component in decoded_components[entity_id].values():
            registry.set_component(entity_id, component)
    next_entity_number = payload.get("next_entity_number")
    if not isinstance(next_entity_number, int) or isinstance(
        next_entity_number,
        bool,
    ):
        raise CheckpointCompatibilityError(
            "checkpoint next entity number must be an integer"
        )
    registry.restore_next_entity_number(next_entity_number)
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        raise CheckpointCompatibilityError("checkpoint resources must be an object")
    for resource_type, resource in registry.resource_items():
        type_name = qualified_type_name(resource_type)
        if type_name in _OPERATIONAL_RESOURCES:
            continue
        if type_name not in resources:
            raise CheckpointCompatibilityError(
                f"checkpoint is missing resource state: {type_name}"
            )
        resource_payload = resources[type_name]
        if isinstance(resource, _VALIDATE_ONLY_RESOURCES):
            if _dump_resource(resource) != resource_payload:
                raise CheckpointCompatibilityError(
                    f"checkpoint construction resource changed: {type_name}"
                )
            continue
        _restore_resource(registry, resource, resource_payload)


def _restore_resource(
    registry: Any,
    resource: object,
    payload: JsonValue,
) -> None:
    if isinstance(resource, InformationStore):
        if not isinstance(payload, dict):
            raise CheckpointCompatibilityError(
                "invalid information-store checkpoint state"
            )
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise CheckpointCompatibilityError(
                "invalid information-store checkpoint state"
            )
        resource.restore_documents(
            tuple(
                information_document_from_dict(
                    document
                )
                for document in documents
                if isinstance(document, dict)
            )
        )
        return
    if isinstance(resource, EpisodicMemoryStore):
        if not isinstance(payload, dict):
            raise CheckpointCompatibilityError("invalid memory checkpoint state")
        records = TypeAdapter(tuple[MemoryRecord, ...]).validate_python(
            payload.get("records")
        )
        next_id = payload.get("next_id")
        if not isinstance(next_id, int) or isinstance(next_id, bool):
            raise CheckpointCompatibilityError("invalid memory next ID")
        resource.restore_records(records, next_id=next_id)
        return
    if isinstance(resource, TextContentRegistry):
        if not isinstance(payload, dict):
            raise CheckpointCompatibilityError("invalid text checkpoint state")
        registry.set_resource(TextContentRegistry.from_dict(payload))
        return
    if isinstance(resource, SpatialIndex):
        if not isinstance(payload, dict):
            raise CheckpointCompatibilityError("invalid spatial checkpoint state")
        entries = TypeAdapter(tuple[SpatialIndexEntry, ...]).validate_python(
            payload.get("entries")
        )
        revision = payload.get("revision")
        topology_revision = payload.get("topology_revision")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or not isinstance(topology_revision, int)
            or isinstance(topology_revision, bool)
        ):
            raise CheckpointCompatibilityError(
                "invalid spatial checkpoint revisions"
            )
        registry.set_resource(
            SpatialIndex.from_checkpoint(
                entries,
                revision=revision,
                topology_revision=topology_revision,
            )
        )
        return
    registry.set_resource(TypeAdapter(type(resource)).validate_python(payload))


def _component_type_registry(
    runner: SimulationRunner,
) -> dict[str, type[object]]:
    result = {
        qualified_type_name(component_type): component_type
        for component_type in runner.registry.component_types()
    }
    for value in vars(component_module).values():
        if isclass(value) and value.__module__.startswith(
            "stage0_sim.domain.components"
        ):
            result[qualified_type_name(value)] = value
    return result


def _fingerprint(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_rng_state(value: object) -> JsonValue:
    if isinstance(value, tuple):
        return [_json_rng_state(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"unsupported random state value: {type(value).__name__}")


def _tuple_rng_state(value: JsonValue) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise CheckpointCompatibilityError("checkpoint RNG state must be a list")

    def convert(item: JsonValue) -> Any:
        if isinstance(item, list):
            return tuple(convert(child) for child in item)
        return item

    return tuple(convert(item) for item in value)


def _required_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CheckpointCompatibilityError(f"checkpoint {key} must be an integer")
    return value


def _required_float(payload: Mapping[str, JsonValue], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise CheckpointCompatibilityError(f"checkpoint {key} must be a number")
    return float(value)


def checkpoint_created_at() -> datetime:
    return datetime.now(UTC)


def _events_from_payload(value: JsonValue) -> tuple[DomainEvent, ...]:
    if not isinstance(value, list):
        raise CheckpointCompatibilityError(
            "checkpoint event history must be a list"
        )
    events: list[DomainEvent] = []
    for item in value:
        if not isinstance(item, dict):
            raise CheckpointCompatibilityError(
                "checkpoint event history entries must be objects"
            )
        payload = item.get("payload")
        wall_time = item.get("wall_time")
        if not isinstance(payload, dict) or not isinstance(wall_time, str):
            raise CheckpointCompatibilityError(
                "invalid checkpoint event history entry"
            )
        events.append(
            DomainEvent(
                run_id=str(item.get("run_id")),
                event_id=str(item.get("event_id")),
                simulation_tick=_required_int(item, "simulation_tick"),
                simulation_time=_required_float(item, "simulation_time"),
                wall_time=datetime.fromisoformat(wall_time),
                event_type=str(item.get("event_type")),
                payload=payload,
                agent_id=(
                    str(item["agent_id"])
                    if item.get("agent_id") is not None
                    else None
                ),
                causation_id=(
                    str(item["causation_id"])
                    if item.get("causation_id") is not None
                    else None
                ),
                correlation_id=(
                    str(item["correlation_id"])
                    if item.get("correlation_id") is not None
                    else None
                ),
            )
        )
    return tuple(events)
