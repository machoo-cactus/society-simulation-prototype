import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stage0_sim.application.manager import (
    ManagedRun,
    SimulationConflictError,
    SimulationManager,
    SimulationNotFoundError,
)
from stage0_sim.application.scenario import ScenarioDefinition
from stage0_sim.application.telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    build_agent_snapshot,
    build_ui_bootstrap,
    build_world_snapshot,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.world import CityWorld

router = APIRouter(prefix="/simulation", tags=["simulation"])


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    realtime: bool = True
    speed: float | None = Field(default=None, gt=0)


class ScenarioCompositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioDefinition
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


def get_manager(request: Request) -> SimulationManager:
    return cast(SimulationManager, request.app.state.simulation_manager)


@router.post("/scenarios", status_code=201)
async def create_scenario(
    body: ScenarioCompositionRequest,
    request: Request,
) -> dict[str, object]:
    manager = get_manager(request)
    try:
        scenario_id = manager.add_scenario(
            body.scenario,
            body.character_assignments,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    prepared = manager.get_scenario(scenario_id)
    return {
        "scenario_id": scenario_id,
        "name": body.scenario.name,
        "characters": list(prepared.entity_summaries()),
        "character_assignments": dict(prepared.assignments),
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
        )
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    managed = manager.get_run(run_id)
    return {
        "run_id": run_id,
        "status": managed.runner.status.value,
        "realtime": body.realtime,
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, object]:
    managed = _managed_run(get_manager(request), run_id)
    return {
        "run_id": run_id,
        "status": managed.runner.status.value,
        "cognition_phase": managed.runner.cognition_phase.value,
        "cognition_execution_mode": (
            managed.runner.configuration.cognition_execution_mode
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
        local_map = city.local_map_for_building(building_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
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
                    "local_coordinate": entrance.local_coordinate.to_payload(),
                    "network_node_id": entrance.network_node_id,
                }
                for entrance in building.entrances
            ],
            "local_map": {
                "width": local_map.grid.width,
                "height": local_map.grid.height,
                "blocked": [
                    coordinate.to_payload()
                    for coordinate in sorted(local_map.grid.blocked)
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
                    for zone in local_map.zones
                ],
                "stations": [
                    {
                        "id": station.id,
                        "name": station.name,
                        "position": station.position.to_payload(),
                        "actions": list(station.supported_actions),
                        "available": station.available,
                    }
                    for station in local_map.stations
                ],
            },
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
    managed = _managed_run(get_manager(request), run_id)
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
    _managed_run(manager, run_id)
    return manager.dataset_store.summary(run_id)


@router.get("/runs/{run_id}/export")
async def export_dataset(
    run_id: str,
    request: Request,
) -> StreamingResponse:
    manager = get_manager(request)
    _managed_run(manager, run_id)

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


def _managed_run(manager: SimulationManager, run_id: str) -> ManagedRun:
    try:
        return manager.get_run(run_id)
    except SimulationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
