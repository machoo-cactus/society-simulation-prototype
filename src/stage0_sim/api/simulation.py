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

router = APIRouter(prefix="/simulation", tags=["simulation"])


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    realtime: bool = True
    speed: float | None = Field(default=None, gt=0)


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
    scenario: ScenarioDefinition,
    request: Request,
) -> dict[str, str]:
    scenario_id = get_manager(request).add_scenario(scenario)
    return {"scenario_id": scenario_id, "name": scenario.name}


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
        manager.step(run_id)
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
