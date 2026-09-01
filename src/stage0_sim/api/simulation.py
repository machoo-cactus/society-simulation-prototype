import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask

from stage0_sim.api.persisted_data import router as persisted_data_router
from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetRecordFilter,
    RecordCategory,
    RecordVisibility,
)
from stage0_sim.application.element_library import ElementLibrary
from stage0_sim.application.elements import ScenarioSourceDefinition
from stage0_sim.application.manager import (
    ManagedRun,
    SimulationConflictError,
    SimulationManager,
    SimulationNotFoundError,
)
from stage0_sim.application.runner import RunnerStatus
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)
from stage0_sim.application.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    build_agent_snapshot,
    build_ui_bootstrap,
    build_world_snapshot,
)
from stage0_sim.domain.components import TransactionRequestComponent
from stage0_sim.domain.economy import (
    TransactionPoint,
    TransactionPointRegistry,
)
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.npcs import NpcControlMode, NpcPoolRegistry
from stage0_sim.domain.world import CityWorld, Room

router = APIRouter(prefix="/simulation", tags=["simulation"])
router.include_router(persisted_data_router)


def get_manager(request: Request) -> SimulationManager:
    return cast(SimulationManager, request.app.state.simulation_manager)


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    realtime: bool = True
    speed: float | None = Field(default=None, gt=0)
    npc_control_mode: NpcControlMode | None = None


class ScenarioCompositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioSourceDefinition
    character_assignments: dict[str, str] = Field(default_factory=dict)


class SpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: float = Field(gt=0)


class VitalsMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satiety: float | None = Field(default=None, ge=0, le=100)
    energy: float | None = Field(default=None, ge=0, le=100)
    stress: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def has_value(self) -> "VitalsMutationRequest":
        if self.satiety is None and self.energy is None and self.stress is None:
            raise ValueError("at least one vital must be supplied")
        return self


class DatasetQueryParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: str | None = None
    category: RecordCategory | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    entity_id: str | None = None
    primary_entity_id: str | None = None
    related_entity_id: str | None = None
    minimum_tick: int | None = Field(default=None, ge=0)
    maximum_tick: int | None = Field(default=None, ge=0)
    minimum_time: float | None = Field(default=None, ge=0)
    maximum_time: float | None = Field(default=None, ge=0)
    visibility: RecordVisibility | None = None
    goal_id: str | None = None
    plan_id: str | None = None
    action_id: str | None = None
    decision_id: str | None = None
    model_request_id: str | None = None
    tool_call_id: str | None = None
    interaction_id: str | None = None
    perception_fact_id: str | None = None
    memory_id: str | None = None
    transaction_request_id: str | None = None
    operator_intervention_id: str | None = None
    status: str | None = None
    outcome: str | None = None
    include_private: bool = False
    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    kind: str | None = None
    family: str | None = None

    @model_validator(mode="after")
    def validate_ranges_and_private(self) -> "DatasetQueryParameters":
        if (
            self.entity_id is not None
            and self.primary_entity_id is not None
            and self.entity_id != self.primary_entity_id
        ):
            raise ValueError("entity_id and primary_entity_id must match")
        if (
            self.minimum_tick is not None
            and self.maximum_tick is not None
            and self.minimum_tick > self.maximum_tick
        ):
            raise ValueError("minimum_tick must not exceed maximum_tick")
        if (
            self.minimum_time is not None
            and self.maximum_time is not None
            and self.minimum_time > self.maximum_time
        ):
            raise ValueError("minimum_time must not exceed maximum_time")
        if (
            self.visibility is RecordVisibility.PRIVATE_RESEARCH
            and not self.include_private
        ):
            raise ValueError(
                "include_private=true is required for PRIVATE_RESEARCH"
            )
        return self

    def to_filter(self) -> DatasetQueryFilter:
        return DatasetQueryFilter(
            record_type=self.record_type,
            category=self.category,
            schema_id=self.schema_id,
            schema_version=self.schema_version,
            primary_entity_id=self.primary_entity_id or self.entity_id,
            related_entity_id=self.related_entity_id,
            minimum_tick=self.minimum_tick,
            maximum_tick=self.maximum_tick,
            minimum_time=self.minimum_time,
            maximum_time=self.maximum_time,
            visibility=self.visibility,
            goal_id=self.goal_id,
            plan_id=self.plan_id,
            action_id=self.action_id,
            decision_id=self.decision_id,
            model_request_id=self.model_request_id,
            tool_call_id=self.tool_call_id,
            interaction_id=self.interaction_id,
            perception_fact_id=self.perception_fact_id,
            memory_id=self.memory_id,
            transaction_request_id=self.transaction_request_id,
            operator_intervention_id=self.operator_intervention_id,
            status=self.status,
            outcome=self.outcome,
            include_private=self.include_private,
            cursor=self.cursor,
            limit=self.limit,
        )


@router.post("/scenarios", status_code=201)
async def create_scenario(
    body: ScenarioCompositionRequest,
    request: Request,
) -> dict[str, object]:
    manager = get_manager(request)
    try:
        element_library = cast(
            ElementLibrary,
            request.app.state.element_library,
        )
        resolved = resolve_scenario(
            body.scenario,
            element_library,
        )
        scenario = resolved.scenario
        scenario_id = await manager.add_scenario(
            scenario,
            body.character_assignments,
            scenario_source=body.scenario.model_dump(mode="json"),
            resolved_elements=resolved.provenance_payload(),
        )
    except (ScenarioResolutionError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    prepared = manager.get_scenario(scenario_id)
    return {
        "scenario_id": scenario_id,
        "name": scenario.name,
        "characters": list(prepared.entity_summaries()),
        "character_assignments": dict(prepared.assignments),
        "character_situations": {
            entity_id: artifact.model_dump(mode="json")
            for entity_id, artifact in sorted(prepared.situations.items())
        },
    }


@router.post("/runs", status_code=201)
async def start_run(
    body: StartRunRequest,
    request: Request,
) -> dict[str, object]:
    manager = get_manager(request)
    try:
        run_id = await manager.start_run(
            body.scenario_id,
            realtime=body.realtime,
            speed=body.speed,
            npc_control_mode=body.npc_control_mode,
        )
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    managed = manager.get_run(run_id)
    return {
        "run_id": run_id,
        "status": managed.runner.status.value,
        "realtime": body.realtime,
        "npc_control_mode": managed.runner.configuration.npc_control_mode.value,
        "effective_npc_control_mode": (
            managed.runner.configuration.effective_npc_control_mode.value
        ),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    return {
        "run_id": run_id,
        "status": managed.runner.status.value,
        "cognition_phase": managed.runner.cognition_phase.value,
        "npc_control_mode": managed.runner.configuration.npc_control_mode.value,
        "effective_npc_control_mode": (
            managed.runner.configuration.effective_npc_control_mode.value
        ),
        "cognition_pending_decision_ids": list(
            managed.runner.cognition_pending_decision_ids
        ),
        "cognition_wait_elapsed_seconds": (
            managed.runner.cognition_wait_elapsed_seconds
        ),
        "speed": managed.runner.speed,
        "tick": managed.runner.clock.tick,
        "simulation_time": managed.runner.clock.simulation_time,
        "latest_sequence": managed.broker.latest_sequence,
        "oldest_sequence": managed.broker.oldest_sequence,
        "domain_event_offset": managed.broker.domain_event_offset,
        "snapshot_revision": managed.broker.snapshot_revision,
    }


@router.get("/runs/{run_id}/snapshot")
async def get_snapshot(run_id: str, request: Request) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": managed.broker.latest_sequence,
        "oldest_sequence": managed.broker.oldest_sequence,
        "domain_event_offset": managed.broker.domain_event_offset,
        "snapshot_revision": managed.broker.snapshot_revision,
        "snapshot": build_world_snapshot(managed.runner),
    }


@router.get("/runs/{run_id}/world/city")
async def get_city_world(run_id: str, request: Request) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    if not managed.runner.registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    bootstrap = build_ui_bootstrap(managed.runner)
    return {"run_id": run_id, "city": bootstrap["city"]}


@router.get("/runs/{run_id}/world/buildings/{building_id}")
async def get_building_world(
    run_id: str,
    building_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    registry = managed.runner.registry
    if not registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    city = registry.get_resource(CityWorld)
    try:
        building = city.building(building_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    point_states = registry.get_resource(TransactionPointRegistry)
    pool = (
        registry.get_resource(NpcPoolRegistry)
        if registry.has_resource(NpcPoolRegistry)
        else None
    )
    queued_requests: dict[str, int] = {}
    for _, transaction_request in registry.query(
        TransactionRequestComponent
    ):
        if transaction_request.status in {
            "awaiting_staff",
            "awaiting_authorization",
            "authorized",
            "running",
        }:
            queued_requests[transaction_request.point_id] = (
                queued_requests.get(transaction_request.point_id, 0) + 1
            )
    availability_registry = (
        registry.get_resource(EnvironmentAvailabilityRegistry)
        if registry.has_resource(EnvironmentAvailabilityRegistry)
        else None
    )
    return {
        "run_id": run_id,
        "building": {
            "id": building.id,
            "name": building.name,
            "district_id": building.district_id,
            "city_position": building.city_position.to_payload(),
            "entrances": [
                {
                    "id": entrance.id,
                    "room_id": entrance.room_id,
                    "local_coordinate": entrance.local_coordinate.to_payload(),
                    "network_node_id": entrance.network_node_id,
                }
                for entrance in building.entrances
            ],
            "rooms": [
                _room_runtime_payload(
                    room,
                    city,
                    point_states,
                    pool,
                    queued_requests,
                    availability_registry,
                )
                for room in city.rooms
                if room.building_id == building.id
            ],
            "portals": [
                {
                    "id": portal.id,
                    "from_room_id": portal.from_room_id,
                    "from_coordinate": portal.from_coordinate.to_payload(),
                    "to_room_id": portal.to_room_id,
                    "to_coordinate": portal.to_coordinate.to_payload(),
                    "bidirectional": portal.bidirectional,
                    "available": portal.available,
                }
                for portal in city.portals
                if portal.building_id == building.id
            ],
        },
    }


@router.get("/runs/{run_id}/world/city-zones/{city_zone_id}")
async def get_city_zone_world(
    run_id: str,
    city_zone_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    registry = managed.runner.registry
    if not registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    city = registry.get_resource(CityWorld)
    try:
        city_zone = city.city_zone(city_zone_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    building_ids = {
        building.id
        for building in city.buildings
        if building.district_id == city_zone.id
    }
    place_ids = building_ids | {
        place.id
        for place in city.outdoor_places
        if place.district_id == city_zone.id
    }
    node_ids = {
        node.id for node in city.nodes if node.place_id in place_ids
    }
    edges = [
        edge
        for edge in city.edges
        if edge.from_node_id in node_ids or edge.to_node_id in node_ids
    ]
    return {
        "run_id": run_id,
        "city_zone": {
            "id": city_zone.id,
            "name": city_zone.name,
            "center": city_zone.center.to_payload(),
            "buildings": [
                {
                    "id": building.id,
                    "name": building.name,
                    "position": building.city_position.to_payload(),
                }
                for building in city.buildings
                if building.id in building_ids
            ],
            "outdoor_places": [
                {
                    "id": place.id,
                    "name": place.name,
                    "position": place.city_position.to_payload(),
                }
                for place in city.outdoor_places
                if place.district_id == city_zone.id
            ],
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "position": node.position.to_payload(),
                    "place_id": node.place_id,
                }
                for node in city.nodes
                if node.id in node_ids
            ],
            "edges": [
                {
                    "id": edge.id,
                    "from_node_id": edge.from_node_id,
                    "to_node_id": edge.to_node_id,
                    "allowed_modes": [
                        mode.value for mode in sorted(edge.allowed_modes)
                    ],
                    "geometry": [
                        point.to_payload() for point in edge.geometry
                    ],
                }
                for edge in edges
            ],
        },
    }


@router.get("/runs/{run_id}/world/rooms/{room_id}")
async def get_room_world(
    run_id: str,
    room_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    registry = managed.runner.registry
    if not registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    city = registry.get_resource(CityWorld)
    try:
        room = city.room(room_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "run_id": run_id,
        "room": {
            "id": room.id,
            "key": room.key,
            "name": room.name,
            "type": room.room_type,
            "building_id": room.building_id,
            "offset": room.offset.to_payload(),
            "map": {
                "width": room.world.grid.width,
                "height": room.world.grid.height,
                "blocked": [
                    coordinate.to_payload()
                    for coordinate in sorted(room.world.grid.blocked)
                ],
                "zones": [
                    {
                        "id": zone.id,
                        "name": zone.name,
                        "type": zone.zone_type,
                        "tiles": [
                            coordinate.to_payload()
                            for coordinate in sorted(zone.tiles)
                        ],
                    }
                    for zone in room.world.zones
                ],
            },
            "objects": [
                {
                    "id": item.id,
                    "name": item.name,
                    "kind": item.object_kind,
                    "position": item.position.to_payload(),
                    "supported_actions": (
                        list(item.station.supported_actions)
                        if item.station is not None
                        else []
                    ),
                    "offers": (
                        [
                            {
                                "id": offer.id,
                                "name": offer.name,
                            }
                            for offer in item.transaction_point.offers
                        ]
                        if item.transaction_point is not None
                        else []
                    ),
                }
                for item in city.objects
                if item.room_id == room.id
            ],
        },
    }


@router.get("/runs/{run_id}/world/objects/{object_id}")
async def get_world_object(
    run_id: str,
    object_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    registry = managed.runner.registry
    if not registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    city = registry.get_resource(CityWorld)
    try:
        item = city.world_object(object_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    station_payload: dict[str, object] | None = None
    if item.station is not None:
        station_payload = {
            "supported_actions": list(item.station.supported_actions),
            "available": item.station.available,
            "capacity": item.station.capacity,
        }
    transaction_payload: dict[str, object] | None = None
    if item.transaction_point is not None:
        transaction_payload = {
            "available": item.transaction_point.available,
            "capacity": item.transaction_point.capacity,
            "operation": item.transaction_point.operation.value,
            "staffing": _transaction_staffing_payload(
                item.transaction_point
            ),
            "offers": [
                {
                    "id": offer.id,
                    "name": offer.name,
                    "duration": offer.duration,
                }
                for offer in item.transaction_point.offers
            ],
        }
    object_payload: dict[str, object] = {
        "id": item.id,
        "name": item.name,
        "kind": item.object_kind,
        "building_id": item.building_id,
        "room_id": item.room_id,
        "position": item.position.to_payload(),
        "station": station_payload,
        "transaction_point": transaction_payload,
    }
    return {
        "run_id": run_id,
        "object": object_payload,
    }


def _transaction_staffing_payload(
    point: TransactionPoint,
) -> dict[str, object] | None:
    if point.staffing is None:
        return None
    return {
        "role_id": point.staffing.role_id,
        "staff_position": point.staffing.staff_position.to_payload(),
        "request_timeout": point.staffing.request_timeout,
    }


def _room_runtime_payload(
    room: Room,
    city: CityWorld,
    point_states: TransactionPointRegistry,
    pool: NpcPoolRegistry | None,
    queued_requests: dict[str, int],
    availability_registry: EnvironmentAvailabilityRegistry | None,
) -> dict[str, object]:
    world = room.world
    return {
        "id": room.id,
        "key": room.key,
        "name": room.name,
        "type": room.room_type,
        "building_id": room.building_id,
        "offset": room.offset.to_payload(),
        "object_ids": [
            item.id for item in city.objects if item.room_id == room.id
        ],
        "map": {
            "width": world.grid.width,
            "height": world.grid.height,
            "blocked": [
                coordinate.to_payload()
                for coordinate in sorted(world.grid.blocked)
            ],
            "zones": [
                {
                    "id": zone.id,
                    "name": zone.name,
                    "type": zone.zone_type,
                    "tiles": [
                        coordinate.to_payload()
                        for coordinate in sorted(zone.tiles)
                    ],
                }
                for zone in world.zones
            ],
            "stations": [
                {
                    "id": station.id,
                    "name": station.name,
                    "position": station.position.to_payload(),
                    "actions": list(station.supported_actions),
                    "available": station.available,
                    "capacity": station.capacity,
                }
                for station in world.stations
            ],
            "transaction_points": [
                {
                    "id": point.id,
                    "name": point.name,
                    "position": point.position.to_payload(),
                    "available": point.available,
                    "capacity": point.capacity,
                    "operation": point.operation.value,
                    "staffing": _transaction_staffing_payload(point),
                    "runtime": {
                        "holdings": dict(
                            sorted(
                                point_states.state(point.id).holdings.items()
                            )
                        ),
                        "available": (
                            availability_registry.state(
                                point.id,
                                base_available=point.available,
                            ).available
                            if availability_registry is not None
                            else point.available
                        ),
                        "queued_request_count": queued_requests.get(point.id, 0),
                        "npc_id": (
                            pool.staffing(point.id).npc_id
                            if pool is not None and point.staffing is not None
                            else None
                        ),
                    },
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
                for point in world.transaction_points
            ],
        },
    }


@router.get("/runs/{run_id}/world/neighborhoods/{place_id}")
async def get_neighborhood_world(
    run_id: str,
    place_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    registry = managed.runner.registry
    if not registry.has_resource(CityWorld):
        raise HTTPException(status_code=404, detail="run has no city world")
    city = registry.get_resource(CityWorld)
    building_ids = {
        building.id
        for building in city.buildings
        if building.id == place_id or building.district_id == place_id
    }
    if not building_ids:
        try:
            building = city.building(place_id)
        except KeyError as error:
            try:
                outdoor = city.outdoor_place(place_id)
            except KeyError:
                raise HTTPException(status_code=404, detail=str(error)) from error
            node_ids = {outdoor.network_node_id}
        else:
            building_ids.add(building.id)
            node_ids = set()
    else:
        node_ids = set()
    node_ids.update(
        node.id for node in city.nodes if node.place_id in building_ids
    )
    edges = [
        edge
        for edge in city.edges
        if edge.from_node_id in node_ids or edge.to_node_id in node_ids
    ]
    node_ids.update(
        node_id
        for edge in edges
        for node_id in (edge.from_node_id, edge.to_node_id)
    )
    return {
        "run_id": run_id,
        "place_id": place_id,
        "buildings": [
            {
                "id": building.id,
                "name": building.name,
                "position": building.city_position.to_payload(),
            }
            for building in city.buildings
            if building.id in building_ids
        ],
        "outdoor_places": [
            {
                "id": place.id,
                "name": place.name,
                "position": place.city_position.to_payload(),
                "network_node_id": place.network_node_id,
            }
            for place in city.outdoor_places
            if place.id == place_id
        ],
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "position": node.position.to_payload(),
                "place_id": node.place_id,
            }
            for node in city.nodes
            if node.id in node_ids
        ],
        "edges": [
            {
                "id": edge.id,
                "from_node_id": edge.from_node_id,
                "to_node_id": edge.to_node_id,
                "allowed_modes": [
                    mode.value for mode in sorted(edge.allowed_modes)
                ],
                "geometry": [point.to_payload() for point in edge.geometry],
            }
            for edge in edges
        ],
    }


@router.post("/runs/{run_id}/pause")
async def pause_run(run_id: str, request: Request) -> dict[str, str]:
    manager = get_manager(request)
    try:
        manager.pause(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "paused"}


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request) -> dict[str, str]:
    manager = get_manager(request)
    try:
        manager.resume(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "running"}


@router.post("/runs/{run_id}/step")
async def step_run(run_id: str, request: Request) -> dict[str, object]:
    manager = get_manager(request)
    try:
        await manager.step(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, SimulationConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    runner = manager.get_run(run_id).runner
    return {
        "status": runner.status.value,
        "tick": runner.clock.tick,
        "simulation_time": runner.clock.simulation_time,
    }


@router.post("/runs/{run_id}/speed")
async def set_speed(
    run_id: str,
    body: SpeedRequest,
    request: Request,
) -> dict[str, float]:
    manager = get_manager(request)
    try:
        manager.set_speed(run_id, body.speed)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"speed": body.speed}


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict[str, str]:
    manager = get_manager(request)
    try:
        await manager.stop_run(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "stopped"}


@router.get("/runs/{run_id}/agents/{agent_id}")
async def get_agent(
    run_id: str,
    agent_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    if agent_id not in managed.runner.registry.entities():
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return {
        "run_id": run_id,
        "agent": build_agent_snapshot(managed.runner, agent_id),
    }


@router.get("/runs/{run_id}/agents/{agent_id}/spatial-context")
async def get_agent_spatial_context(
    run_id: str,
    agent_id: str,
    request: Request,
) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    if agent_id not in managed.runner.registry.entities():
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    agent = build_agent_snapshot(managed.runner, agent_id)
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "spatial_location": agent.get("spatial_location"),
        "travel": agent.get("travel"),
    }


@router.patch("/runs/{run_id}/agents/{agent_id}/vitals")
async def mutate_vitals(
    run_id: str,
    agent_id: str,
    body: VitalsMutationRequest,
    request: Request,
) -> dict[str, object]:
    manager = get_manager(request)
    values = {
        name: value
        for name, value in body.model_dump().items()
        if value is not None
    }
    try:
        manager.mutate_vitals(run_id, agent_id, values)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    managed = manager.get_run(run_id)
    return {
        "run_id": run_id,
        "agent": build_agent_snapshot(managed.runner, agent_id),
    }


@router.get("/runs/{run_id}/events")
async def get_events(
    run_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, object]:
    manager = get_manager(request)
    try:
        managed = manager.get_run(run_id)
    except SimulationNotFoundError:
        managed = None
    if managed is None or managed.runner.status is RunnerStatus.STOPPED:
        try:
            persisted, total = manager.dataset_store.persisted_events(
                run_id,
                offset=offset,
                limit=limit,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "events": list(persisted),
            "offset": offset,
            "next_offset": offset + len(persisted),
            "total": total,
        }
    events = managed.runner.events.events
    page = events[offset : offset + limit]
    return {
        "events": [event.to_dict() for event in page],
        "offset": offset,
        "next_offset": offset + len(page),
        "total": len(events),
    }


@router.get("/runs/{run_id}/data")
async def get_dataset_summary(
    run_id: str,
    request: Request,
) -> dict[str, JsonValue]:
    manager = get_manager(request)
    try:
        return manager.dataset_store.summary(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/runs/{run_id}/data/schema")
async def get_dataset_schema(
    run_id: str,
    request: Request,
) -> dict[str, JsonValue]:
    manager = get_manager(request)
    try:
        return manager.data_query.schema(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/runs/{run_id}/data/records")
async def get_dataset_records(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    dataset_filter = query.to_filter()
    after_sequence: int | None = None
    if dataset_filter.cursor is not None:
        try:
            after_sequence = int(dataset_filter.cursor)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="raw record cursor must be an integer",
            ) from error
        if after_sequence < 0:
            raise HTTPException(
                status_code=422,
                detail="raw record cursor must not be negative",
            )
    record_filter = DatasetRecordFilter(
        record_type=dataset_filter.record_type,
        category=dataset_filter.category,
        schema_id=dataset_filter.schema_id,
        schema_version=dataset_filter.schema_version,
        subject_id=dataset_filter.primary_entity_id,
        related_entity_id=dataset_filter.related_entity_id,
        minimum_tick=dataset_filter.minimum_tick,
        maximum_tick=dataset_filter.maximum_tick,
        minimum_time=dataset_filter.minimum_time,
        maximum_time=dataset_filter.maximum_time,
        visibility=dataset_filter.visibility,
        goal_id=dataset_filter.goal_id,
        plan_id=dataset_filter.plan_id,
        action_id=dataset_filter.action_id,
        decision_id=dataset_filter.decision_id,
        model_request_id=dataset_filter.model_request_id,
        tool_call_id=dataset_filter.tool_call_id,
        interaction_id=dataset_filter.interaction_id,
        perception_fact_id=dataset_filter.perception_fact_id,
        memory_id=dataset_filter.memory_id,
        transaction_request_id=dataset_filter.transaction_request_id,
        operator_intervention_id=dataset_filter.operator_intervention_id,
        status=dataset_filter.status,
        outcome=dataset_filter.outcome,
        include_private=dataset_filter.include_private,
        after_sequence=after_sequence,
        limit=dataset_filter.limit,
    )
    manager = get_manager(request)
    try:
        page = manager.data_query.records(run_id, record_filter)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "run_id": run_id,
        "records": [record.to_dict() for record in page.records],
        "next_cursor": page.next_cursor,
        "include_private": query.include_private,
    }


@router.get("/runs/{run_id}/data/goals")
async def get_dataset_goals(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "goals", query)


@router.get("/runs/{run_id}/data/decisions")
async def get_dataset_decisions(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "decisions", query)


@router.get("/runs/{run_id}/data/actions")
async def get_dataset_actions(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "action_instances", query)


@router.get("/runs/{run_id}/data/interactions")
async def get_dataset_interactions(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "interactions", query)


@router.get("/runs/{run_id}/data/state")
async def get_dataset_state(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    kind = query.kind or "sample"
    if kind not in {"sample", "delta"}:
        raise HTTPException(status_code=422, detail="kind must be sample or delta")
    table = "state_samples" if kind == "sample" else "state_deltas"
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/transitions")
async def get_dataset_transitions(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    kind = query.kind or "state"
    if kind not in {"state", "goal", "action"}:
        raise HTTPException(
            status_code=422,
            detail="kind must be state, goal, or action",
        )
    table = {
        "state": "transition_samples",
        "goal": "goal_transitions",
        "action": "action_transitions",
    }[kind]
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/aggregates")
async def get_dataset_aggregates(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    family = query.family or "population"
    if family not in {"population", "resource_samples", "resource_flows"}:
        raise HTTPException(
            status_code=422,
            detail="family must be population, resource_samples, or resource_flows",
        )
    table = {
        "population": "population_samples",
        "resource_samples": "resource_samples",
        "resource_flows": "resource_flows",
    }[family]
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/episodes/{family}")
async def get_dataset_episodes(
    run_id: str,
    family: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    table = {
        "actions": "action_episodes",
        "decisions": "decision_episodes",
        "goals": "goal_episodes",
        "interactions": "interaction_episodes",
    }.get(family)
    if table is None:
        raise HTTPException(status_code=404, detail=f"unknown episode family: {family}")
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/model-requests")
async def get_dataset_model_requests(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "model_requests", query)


@router.get("/runs/{run_id}/data/tool-executions")
async def get_dataset_tool_executions(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "tool_executions", query)


@router.get("/runs/{run_id}/data/perception")
async def get_dataset_perception(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    kind = query.kind or "facts"
    if kind not in {"facts", "deliveries"}:
        raise HTTPException(
            status_code=422,
            detail="kind must be facts or deliveries",
        )
    table = "perception_facts" if kind == "facts" else "perception_deliveries"
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/memory")
async def get_dataset_memory(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    kind = query.kind or "operations"
    if kind not in {"operations", "retrievals"}:
        raise HTTPException(
            status_code=422,
            detail="kind must be operations or retrievals",
        )
    table = (
        "memory_operations"
        if kind == "operations"
        else "information_retrievals"
    )
    return _query_table_response(request, run_id, table, query)


@router.get("/runs/{run_id}/data/opportunities")
async def get_dataset_opportunities(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> dict[str, object]:
    return _query_table_response(request, run_id, "opportunity_samples", query)


@router.get("/runs/{run_id}/exports/complete")
async def export_complete_dataset(
    run_id: str,
    request: Request,
) -> StreamingResponse:
    manager = get_manager(request)
    try:
        manager.data_query.summary(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    async def lines() -> AsyncIterator[str]:
        for line in manager.dataset_store.iter_jsonl(run_id):
            yield f"{line}\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.jsonl"'
        },
    )


@router.get("/runs/{run_id}/exports/records")
async def export_filtered_records(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> StreamingResponse:
    dataset_filter = query.to_filter()
    after_sequence: int | None = None
    if dataset_filter.cursor is not None:
        try:
            after_sequence = int(dataset_filter.cursor)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="raw record cursor must be an integer",
            ) from error
    filters = DatasetRecordFilter(
        record_type=dataset_filter.record_type,
        category=dataset_filter.category,
        schema_id=dataset_filter.schema_id,
        schema_version=dataset_filter.schema_version,
        subject_id=dataset_filter.primary_entity_id,
        related_entity_id=dataset_filter.related_entity_id,
        minimum_tick=dataset_filter.minimum_tick,
        maximum_tick=dataset_filter.maximum_tick,
        minimum_time=dataset_filter.minimum_time,
        maximum_time=dataset_filter.maximum_time,
        visibility=dataset_filter.visibility,
        goal_id=dataset_filter.goal_id,
        plan_id=dataset_filter.plan_id,
        action_id=dataset_filter.action_id,
        decision_id=dataset_filter.decision_id,
        model_request_id=dataset_filter.model_request_id,
        tool_call_id=dataset_filter.tool_call_id,
        interaction_id=dataset_filter.interaction_id,
        perception_fact_id=dataset_filter.perception_fact_id,
        memory_id=dataset_filter.memory_id,
        transaction_request_id=dataset_filter.transaction_request_id,
        operator_intervention_id=dataset_filter.operator_intervention_id,
        status=dataset_filter.status,
        outcome=dataset_filter.outcome,
        include_private=dataset_filter.include_private,
        after_sequence=after_sequence,
        limit=1000,
    )
    manager = get_manager(request)
    try:
        manager.data_query.summary(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    async def lines() -> AsyncIterator[str]:
        for line in manager.data_query.raw_ndjson(run_id, filters):
            yield f"{line}\n"

    filename = f"{_safe_download_name(run_id)}-records.ndjson"
    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Stage0-Private-Included": str(query.include_private).lower(),
        },
    )


@router.get("/runs/{run_id}/exports/bundle")
async def export_analysis_bundle(
    run_id: str,
    request: Request,
    query: Annotated[DatasetQueryParameters, Query()],
) -> FileResponse:
    manager = get_manager(request)
    filename = f"{_safe_download_name(run_id)}-analysis.zip"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{_safe_download_name(run_id)}-",
            suffix=".zip",
            dir=manager.dataset_store.path.parent,
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            manager.data_query.analysis_bundle(
                run_id,
                cast(BinaryIO, temporary),
                query.to_filter(),
            )
    except KeyError as error:
        if temporary_path is not None:
            os.unlink(temporary_path)
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        if temporary_path is not None:
            os.unlink(temporary_path)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        if temporary_path is not None:
            os.unlink(temporary_path)
        raise
    if temporary_path is None:
        raise HTTPException(
            status_code=500,
            detail="analysis bundle was not created",
        )
    return FileResponse(
        temporary_path,
        media_type="application/zip",
        filename=filename,
        headers={
            "X-Stage0-Private-Included": str(query.include_private).lower(),
        },
        background=BackgroundTask(os.unlink, temporary_path),
    )


@router.websocket("/runs/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: str) -> None:
    manager: SimulationManager = websocket.app.state.simulation_manager
    try:
        managed = manager.get_run(run_id)
    except SimulationNotFoundError:
        await websocket.close(code=4404, reason=f"unknown run: {run_id}")
        return
    raw_sequence = websocket.query_params.get("after_sequence", "0")
    raw_snapshot_revision = websocket.query_params.get(
        "after_snapshot_revision", "0"
    )
    try:
        cursor = max(0, int(raw_sequence))
        snapshot_cursor = max(0, int(raw_snapshot_revision))
    except ValueError:
        await websocket.close(
            code=4400,
            reason="telemetry cursors must be integers",
        )
        return

    await websocket.accept()
    broker = managed.broker
    await websocket.send_json(
        {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "sequence": broker.latest_sequence,
            "type": "hello",
            "run_id": run_id,
            "simulation_tick": managed.runner.clock.tick,
            "simulation_time": managed.runner.clock.simulation_time,
            "payload": {
                "oldest_sequence": broker.oldest_sequence,
                "latest_sequence": broker.latest_sequence,
                "domain_event_offset": broker.domain_event_offset,
                "snapshot_revision": broker.snapshot_revision,
                "bootstrap": build_ui_bootstrap(managed.runner),
            },
        }
    )
    if not broker.can_resume_after(cursor):
        await websocket.send_json(
            {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "sequence": broker.latest_sequence,
                "type": "resync_required",
                "run_id": run_id,
                "simulation_tick": managed.runner.clock.tick,
                "simulation_time": managed.runner.clock.simulation_time,
                "payload": {
                    "oldest_sequence": broker.oldest_sequence,
                    "latest_sequence": broker.latest_sequence,
                    "domain_event_offset": broker.domain_event_offset,
                    "snapshot_revision": broker.snapshot_revision,
                },
            }
        )
        await websocket.close(code=4409, reason="telemetry cursor expired")
        return
    try:
        while True:
            messages = broker.messages_after(cursor)
            for message in messages:
                await websocket.send_json(message.to_dict())
                cursor = message.sequence
            snapshot = broker.latest_snapshot
            if (
                snapshot is not None
                and snapshot.snapshot_revision is not None
                and snapshot.snapshot_revision > snapshot_cursor
            ):
                await websocket.send_json(snapshot.to_dict())
                snapshot_cursor = snapshot.snapshot_revision
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        return


def _query_table_response(
    request: Request,
    run_id: str,
    table: str,
    query: DatasetQueryParameters,
) -> dict[str, object]:
    manager = get_manager(request)
    try:
        page = manager.data_query.table(
            run_id,
            table,
            query.to_filter(),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "run_id": run_id,
        "table": table,
        "rows": list(page.rows),
        "next_cursor": page.next_cursor,
        "include_private": query.include_private,
    }


def _safe_download_name(value: str) -> str:
    safe = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "._-")
        else "_"
        for character in value
    )
    return safe or "run"


def _managed_run(manager: SimulationManager, run_id: str) -> ManagedRun:
    try:
        return manager.get_run(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
