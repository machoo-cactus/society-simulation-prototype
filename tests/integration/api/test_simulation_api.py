import io
import json
import time
import zipfile
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from stage0_sim.adapters.persistence.sqlite_schema import DATABASE_SCHEMA_VERSION
from stage0_sim.api.app import app
from stage0_sim.application.data_management import selection_fingerprint
from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.manager import SimulationManager
from stage0_sim.domain.components import (
    ActionInstance,
    ActionOrigin,
    CharacterHandStateComponent,
    CustodyComponent,
    InteractionExecutionComponent,
    InteractionRequestComponent,
    MovementObstruction,
    OpenableComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    SpatialIndex,
    SpatialIndexEntry,
    SpatialParentRelationComponent,
    VisionObstruction,
)
from stage0_sim.domain.interactions import (
    InteractionSpecification,
    InteractionVerb,
)
from tests.helpers.paths import CATALOG_SCENARIOS


def load_scenario_payload(name: str) -> dict[str, Any]:
    path = CATALOG_SCENARIOS / name
    return json.loads(path.read_text(encoding="utf-8"))


def create_run(
    client: TestClient,
    scenario_name: str = "needs-and-preemption.json",
) -> str:
    scenario_response = client.post(
        "/simulation/scenarios",
        json={
            "scenario": load_scenario_payload(scenario_name),
            "character_assignments": {},
        },
    )
    assert scenario_response.status_code == 201
    scenario_id = scenario_response.json()["scenario_id"]
    run_response = client.post(
        "/simulation/runs",
        json={"scenario_id": scenario_id, "realtime": False},
    )
    assert run_response.status_code == 201
    return str(run_response.json()["run_id"])


class _StaticElementLibrary:
    def __init__(
        self,
        elements: tuple[ScenarioElementDefinition, ...],
    ) -> None:
        self._elements = {element.id: element for element in elements}

    def get(
        self,
        element_id: str,
        expected_kind: ElementKind | None = None,
    ) -> ScenarioElementDefinition:
        element = self._elements[element_id]
        if (
            expected_kind is not None
            and ElementKind(element.kind) is not expected_kind
        ):
            raise AssertionError(
                f"element {element_id} has unexpected kind {element.kind}"
            )
        return element


def _element_reference(
    element: ScenarioElementDefinition,
) -> dict[str, str]:
    return {
        "kind": ElementKind(element.kind).value,
        "id": element.id,
        "content_hash": element_content_hash(element),
    }


def _physical_api_source() -> tuple[
    _StaticElementLibrary,
    dict[str, Any],
]:
    display = ObjectElementDefinition.model_validate(
        {
            "schema_version": 5,
            "id": "physical-api-display",
            "name": "Display Stand",
            "kind": "object",
            "physical": {
                "footprint": {"cells": [{"x": 0, "y": 0}]},
            },
        }
    )
    cabinet = ObjectElementDefinition.model_validate(
        {
            "schema_version": 5,
            "id": "physical-api-cabinet",
            "name": "Opaque Cabinet",
            "kind": "object",
            "physical": {
                "footprint": {
                    "cells": [
                        {"x": 1, "y": 0},
                        {"x": 0, "y": 1},
                        {"x": 0, "y": 0},
                    ]
                },
                "obstruction": {
                    "movement": "HARD",
                    "vision": "OPAQUE",
                },
                "capabilities": {
                    "slots": [
                        {
                            "id": "z-inside",
                            "accepted_relations": [
                                "ON_SUPPORT",
                                "IN_CONTAINER",
                            ],
                            "capacity": 2,
                        },
                        {
                            "id": "a-shelf",
                            "accepted_relations": ["ON_SUPPORT"],
                            "capacity": 1,
                        },
                    ],
                    "support": {
                        "slot_ids": ["z-inside", "a-shelf"],
                    },
                    "container": {"slot_ids": ["z-inside"]},
                    "openable": {"initially_locked": False},
                },
                "initial_open": False,
                "owner_id": "private-cabinet-owner",
            },
        }
    )
    secret = ObjectElementDefinition.model_validate(
        {
            "schema_version": 5,
            "id": "physical-api-secret",
            "name": "Private Letter",
            "kind": "object",
            "physical": {
                "footprint": {
                    "cells": [
                        {"x": 1, "y": 0},
                        {"x": 0, "y": 0},
                    ]
                },
                "capabilities": {
                    "portable": {"two_handed": False},
                    "readable": {"document_id": "private-letter"},
                },
                "owner_id": "private-secret-owner",
            },
        }
    )
    room = RoomElementDefinition.model_validate(
        {
            "schema_version": 5,
            "id": "physical-api-room",
            "name": "Physical API Room",
            "kind": "room",
            "room_type": "TEST",
            "width": 4,
            "height": 3,
            "spatial_metric": {"microcells_per_legacy_cell": 9},
            "objects": [
                {
                    "key": "cabinet",
                    "id": "object-z-cabinet",
                    "element": _element_reference(cabinet),
                    "placement": {
                        "anchor": {"x": 15, "y": 8},
                        "orientation": "EAST",
                        "parent_relation": {"kind": "ON_FLOOR"},
                    },
                },
                {
                    "key": "display",
                    "id": "object-a-display",
                    "element": _element_reference(display),
                    "placement": {
                        "anchor": {"x": 25, "y": 12},
                        "parent_relation": {"kind": "ON_FLOOR"},
                    },
                },
                {
                    "key": "secret",
                    "id": "object-m-secret",
                    "element": _element_reference(secret),
                    "placement": {
                        "anchor": {"x": 15, "y": 8},
                        "parent_relation": {
                            "kind": "IN_CONTAINER",
                            "parent_id": "cabinet",
                            "slot_id": "z-inside",
                        },
                    },
                },
            ],
        }
    )
    building = BuildingElementDefinition.model_validate(
        {
            "schema_version": 5,
            "id": "physical-api-building",
            "name": "Physical API Building",
            "kind": "building",
            "rooms": [
                {
                    "key": "room",
                    "element": _element_reference(room),
                }
            ],
            "entrances": [
                {
                    "key": "front",
                    "room_key": "room",
                    "local_coordinate": {"x": 0, "y": 0},
                }
            ],
        }
    )
    source = ScenarioSourceDefinition.model_validate(
        {
            "schema_version": 9,
            "name": "Physical API snapshot",
            "character_situation_synthesis": {"enabled": False},
            "world": {
                "type": "city",
                "city": {
                    "id": "physical-api-city",
                    "name": "Physical API City",
                    "bounds_meters": {
                        "min_x": 0,
                        "min_y": 0,
                        "max_x": 10,
                        "max_y": 10,
                    },
                },
                "city_zones": [
                    {
                        "id": "physical-api-center",
                        "name": "Center",
                        "center": {"x": 5, "y": 5},
                        "buildings": [
                            {
                                "id": "physical-api-building",
                                "element": _element_reference(building),
                                "city_position": {"x": 5, "y": 5},
                                "entrance_node_ids": {
                                    "front": "physical-api-node",
                                },
                            }
                        ],
                    }
                ],
                "transport": {
                    "nodes": [
                        {
                            "id": "physical-api-node",
                            "kind": "BUILDING_ENTRANCE",
                            "position": {"x": 5, "y": 5},
                            "place_id": "physical-api-building",
                        }
                    ]
                },
            },
            "entities": [
                {
                    "id": "physical-agent",
                    "components": {
                        "character_slot": {
                            "label": "Physical Agent",
                            "default_character_id": "alex-chen",
                        },
                        "metadata": {"display_name": "Physical Agent"},
                        "controller": {"enabled": False},
                        "spatial_location": {
                            "scale": "BUILDING",
                            "place_id": "physical-api-building.room",
                            "local_coordinate": {"x": 0, "y": 0},
                            "network_node_id": None,
                            "edge_id": None,
                            "edge_progress": None,
                        },
                    },
                }
            ],
        }
    )
    return (
        _StaticElementLibrary((display, cabinet, secret, room, building)),
        source.model_dump(mode="json"),
    )


def _create_physical_api_run(client: TestClient) -> str:
    library, source = _physical_api_source()
    original_library = app.state.element_library
    app.state.element_library = library
    try:
        scenario_response = client.post(
            "/simulation/scenarios",
            json={"scenario": source, "character_assignments": {}},
        )
    finally:
        app.state.element_library = original_library
    assert scenario_response.status_code == 201
    run_response = client.post(
        "/simulation/runs",
        json={
            "scenario_id": scenario_response.json()["scenario_id"],
            "realtime": False,
        },
    )
    assert run_response.status_code == 201
    return str(run_response.json()["run_id"])


def test_simulation_api_rejects_schema_v2_scenario_input() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/simulation/scenarios",
            json={
                "scenario": {
                    "schema_version": 2,
                    "name": "Legacy input",
                },
                "character_assignments": {},
            },
        )

    assert response.status_code == 422
    assert "Input should be 9" in response.text


def test_rest_lifecycle_step_speed_agent_and_event_history() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)

        initial = client.get(f"/simulation/runs/{run_id}")
        assert initial.json()["status"] == "running"
        assert initial.json()["tick"] == 0
        assert client.post(f"/simulation/runs/{run_id}/step").status_code == 409

        assert client.post(f"/simulation/runs/{run_id}/pause").status_code == 200
        speed = client.post(
            f"/simulation/runs/{run_id}/speed",
            json={"speed": 4},
        )
        assert speed.json() == {"speed": 4.0}
        step = client.post(f"/simulation/runs/{run_id}/step")
        assert step.status_code == 200
        assert step.json()["tick"] == 1

        agent = client.get(
            f"/simulation/runs/{run_id}/agents/agent-001"
        ).json()["agent"]
        assert agent["position"] == {"x": 5, "y": 1}
        assert agent["homeostasis"]["satiety"] == 9.95
        assert agent["system1"]["active_drive"] == "SATIETY"
        assert agent["movement"]["destination"] == {"x": 1, "y": 1}

        first_page = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"offset": 0, "limit": 2},
        ).json()
        second_page = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"offset": first_page["next_offset"], "limit": 100},
        ).json()
        assert len(first_page["events"]) == 2
        assert first_page["next_offset"] == 2
        assert first_page["total"] == len(first_page["events"]) + len(
            second_page["events"]
        )

        assert client.post(f"/simulation/runs/{run_id}/resume").status_code == 200
        assert client.post(f"/simulation/runs/{run_id}/stop").json() == {
            "status": "stopped"
        }


def test_data_management_api_catalog_aggregate_exports_and_safe_delete() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        fingerprint = selection_fingerprint((run_id,))
        selection = {
            "run_ids": [run_id],
            "selection_fingerprint": fingerprint,
        }

        catalog = client.get(
            "/simulation/data/runs",
            params={"effective_status": "running", "limit": 10},
        )
        active_preview = client.post(
            "/simulation/data/deletion-preview",
            json=selection,
        )
        removed_catalog = client.get("/simulation/data-management/runs")

        assert catalog.status_code == 200
        catalog_run = next(
            run for run in catalog.json()["runs"] if run["run_id"] == run_id
        )
        assert catalog_run["live"] is True
        assert catalog_run["effective_status"] == "running"
        assert catalog_run["live_cognition_phase"] == "idle"
        assert catalog_run["deletion_ready"] is False
        assert active_preview.status_code == 200
        assert active_preview.json()["eligible"] is False
        assert removed_catalog.status_code == 404

        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        client.post(f"/simulation/runs/{run_id}/stop")
        aggregate = client.post(
            "/simulation/data/aggregate",
            json={**selection, "include_private_derived": True},
        )
        json_export = client.get(
            "/simulation/data/aggregate.json",
            params={
                "run_id": run_id,
                "selection_fingerprint": fingerprint,
                "include_private_derived": "false",
            },
        )
        csv_export = client.get(
            "/simulation/data/aggregate.csv",
            params={
                "run_id": run_id,
                "selection_fingerprint": fingerprint,
            },
        )
        preview = client.post(
            "/simulation/data/deletion-preview",
            json=selection,
        )
        stale = client.post(
            "/simulation/data/delete",
            json={
                **selection,
                "confirmation_token": "0" * 64,
                "confirmed": True,
                "confirmation_phrase": "DELETE 1 RUNS",
            },
        )
        deleted = client.post(
            "/simulation/data/delete",
            json={
                **selection,
                "confirmation_token": preview.json()["confirmation_token"],
                "confirmed": True,
                "confirmation_phrase": "DELETE 1 RUNS",
            },
        )

        assert aggregate.status_code == 200
        assert aggregate.json()["selection"]["run_ids"] == [run_id]
        assert aggregate.json()["private_derived_warning"] is not None
        assert json_export.status_code == 200
        assert json_export.json()["include_private_derived"] is False
        assert csv_export.status_code == 200
        assert "text/csv" in csv_export.headers["content-type"]
        assert csv_export.headers["X-Stage0-Private-Included"] == "false"
        assert json_export.headers["X-Stage0-Private-Included"] == "false"
        assert preview.json()["eligible"] is True
        assert stale.status_code == 409
        assert deleted.status_code == 200
        assert client.get(f"/simulation/runs/{run_id}").status_code == 404
        assert client.get(f"/simulation/runs/{run_id}/data").status_code == 404


def test_controlled_vital_mutation_triggers_survival_on_next_step() -> None:
    with TestClient(app) as client:
        run_id = create_run(client, "grid-navigation.json")
        client.post(f"/simulation/runs/{run_id}/pause")

        mutation = client.patch(
            f"/simulation/runs/{run_id}/agents/agent-001/vitals",
            json={"satiety": 10, "happiness": 25},
        )
        assert mutation.status_code == 200
        assert mutation.json()["agent"]["homeostasis"]["satiety"] == 10
        assert mutation.json()["agent"]["homeostasis"]["happiness"] == 25

        client.post(f"/simulation/runs/{run_id}/step")
        agent = client.get(
            f"/simulation/runs/{run_id}/agents/agent-001"
        ).json()["agent"]
        assert agent["system1"]["active_drive"] == "SATIETY"
        events = client.get(f"/simulation/runs/{run_id}/events").json()["events"]
        mutated = next(
            event
            for event in events
            if event["event_type"] == "homeostasis.mutated"
        )
        operator_action_id = mutated["payload"]["action_id"]
        assert mutated["payload"]["action_origin"] == "operator"
        assert mutated["payload"]["operator_intervention_id"].startswith(
            "intervention-"
        )
        assert any(
            event["event_type"] == "action.completed"
            and event["payload"]["action_id"] == operator_action_id
            for event in events
        )
        assert any(event["event_type"] == "system1.activated" for event in events)


def test_telemetry_clock_does_not_advance_paused_simulation() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        before = client.get(f"/simulation/runs/{run_id}").json()

        time.sleep(0.25)

        after = client.get(f"/simulation/runs/{run_id}").json()
        assert after["tick"] == before["tick"] == 0
        assert after["simulation_time"] == before["simulation_time"] == 0.0
        assert after["latest_sequence"] == before["latest_sequence"]
        assert after["snapshot_revision"] > before["snapshot_revision"]


def test_checkpoint_api_saves_lists_and_resumes_after_restart() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        running_save = client.post(
            f"/simulation/runs/{run_id}/checkpoints",
            json={"label": "too-early"},
        )
        assert running_save.status_code == 409
        assert client.post(f"/simulation/runs/{run_id}/pause").status_code == 200
        saved = client.post(
            f"/simulation/runs/{run_id}/checkpoints",
            json={"label": "restart-point"},
        )
        assert saved.status_code == 201
        checkpoint_id = saved.json()["checkpoint_id"]
        listed = client.get(
            f"/simulation/runs/{run_id}/checkpoints"
        ).json()["checkpoints"]
        assert listed[0]["checkpoint_id"] == checkpoint_id
        assert listed[0]["is_head"] is True

    with TestClient(app) as client:
        restored = client.post(
            f"/simulation/checkpoints/{checkpoint_id}/restore"
        )
        assert restored.status_code == 201
        assert restored.json() == {
            "checkpoint_id": checkpoint_id,
            "source_run_id": run_id,
            "run_id": run_id,
            "branched": False,
            "status": "paused",
        }
        stepped = client.post(f"/simulation/runs/{run_id}/step")
        assert stepped.status_code == 200
        assert stepped.json()["tick"] == 1
        assert client.post(
            f"/simulation/runs/{run_id}/stop"
        ).status_code == 200


def test_checkpoint_api_branches_from_history_and_filters_lineage() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        assert client.post(f"/simulation/runs/{run_id}/pause").status_code == 200
        historical = client.post(
            f"/simulation/runs/{run_id}/checkpoints",
            json={"label": "fork-point"},
        ).json()
        assert client.post(f"/simulation/runs/{run_id}/step").status_code == 200
        assert client.post(
            f"/simulation/runs/{run_id}/checkpoints",
            json={"label": "new-head"},
        ).status_code == 201

        restored = client.post(
            f"/simulation/checkpoints/{historical['checkpoint_id']}/restore"
        )
        assert restored.status_code == 201
        branch_run_id = restored.json()["run_id"]
        assert branch_run_id != run_id
        assert restored.json()["branched"] is True

        branch_catalog = client.get(
            "/simulation/data/runs",
            params={"lineage_kind": "branch"},
        ).json()["runs"]
        assert [run["run_id"] for run in branch_catalog] == [branch_run_id]
        assert branch_catalog[0]["root_run_id"] == run_id
        assert branch_catalog[0]["parent_run_id"] == run_id
        assert (
            branch_catalog[0]["parent_checkpoint_id"]
            == historical["checkpoint_id"]
        )

        mainline_catalog = client.get(
            "/simulation/data/runs",
            params={"lineage_kind": "mainline"},
        ).json()["runs"]
        assert run_id in {run["run_id"] for run in mainline_catalog}
        assert branch_run_id not in {
            run["run_id"] for run in mainline_catalog
        }

        assert client.post(
            f"/simulation/runs/{branch_run_id}/stop"
        ).status_code == 200
        assert client.post(
            f"/simulation/runs/{run_id}/stop"
        ).status_code == 200


def test_websocket_stream_has_ordered_sequences_and_authoritative_snapshot() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        run = client.get(f"/simulation/runs/{run_id}").json()
        latest = run["latest_sequence"]

        with client.websocket_connect(
            f"/simulation/runs/{run_id}/stream?after_sequence={latest}"
            f"&after_snapshot_revision=0"
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

        assert [first["type"], second["type"]] == ["hello", "world_snapshot"]
        assert first["schema_version"] == "stage0.telemetry.v5"
        assert second["sequence"] == first["sequence"] == latest
        assert second["snapshot_revision"] >= 1
        assert second["simulation_tick"] == 1
        snapshot = second["payload"]
        assert snapshot["tick"] == 1
        assert snapshot["agents"][0]["position"] == {"x": 5, "y": 1}
        assert snapshot["agents"][0]["homeostasis"]["satiety"] == 9.95


def test_api_snapshot_and_telemetry_project_engagement_without_private_content() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        manager = app.state.simulation_manager
        assert isinstance(manager, SimulationManager)
        managed = manager.get_run(run_id)
        actor_id = "agent-001"
        lineage = {
            "engagement_id": "engagement-api-1",
            "action_id": "action-api-1",
            "plan_id": "plan-api-1",
            "plan_revision": 2,
            "decision_id": "decision-api-1",
            "tool_call_id": "tool-api-1",
            "root_correlation_id": "decision-api-1",
        }
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.requested",
                simulation_tick=1,
                simulation_time=1.0,
                agent_id=actor_id,
                payload={
                    **lineage,
                    "reference_ids": ["kitchen"],
                    "intent": "PRIVATE API INTENT",
                    "reason": "PRIVATE API REASON",
                    "visibility": "private",
                },
            )
        )
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.compilation_completed",
                simulation_tick=1,
                simulation_time=1.0,
                agent_id=actor_id,
                payload={
                    **lineage,
                    "summary": "PRIVATE COMPILER SUMMARY",
                    "scene": {"hidden": "PRIVATE SCENE"},
                    "group_count": 2,
                    "visibility": "private",
                },
            )
        )
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.capability_committed",
                simulation_tick=2,
                simulation_time=2.0,
                agent_id=actor_id,
                payload={
                    **lineage,
                    "group_id": "gesture",
                    "group_ordinal": 0,
                    "invocation_id": "gesture-1",
                    "capability": "expressive_behavior",
                    "modality": "visual",
                    "public_text": "A grounded wave.",
                    "expression_band": "moderate",
                    "target_id": "kitchen",
                    "energy_cost": 99,
                    "visibility": "private",
                },
            )
        )
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.partial",
                simulation_tick=2,
                simulation_time=2.0,
                agent_id=actor_id,
                payload={
                    **lineage,
                    "completed_group_count": 1,
                    "failed_group_count": 1,
                    "reason": "PRIVATE TERMINAL REASON",
                    "group_statuses": [
                        {
                            "group_id": "gesture",
                            "group_ordinal": 0,
                            "status": "completed",
                            "failure_reason": None,
                        },
                        {
                            "group_id": "blocked",
                            "group_ordinal": 1,
                            "status": "failed",
                            "failure_reason": "PRIVATE GROUP REASON",
                        },
                    ],
                },
            )
        )
        failed_lineage = {
            **lineage,
            "engagement_id": "engagement-api-2",
            "action_id": "action-api-2",
        }
        client.portal.call(
            lambda: managed.runner.events.emit(
                "engagement.compilation_failed",
                simulation_tick=3,
                simulation_time=3.0,
                agent_id=actor_id,
                payload={
                    **failed_lineage,
                    "reason": "PRIVATE COMPILATION FAILURE",
                    "summary": "PRIVATE FAILED SUMMARY",
                    "visibility": "private",
                },
            )
        )

        response = client.get(f"/simulation/runs/{run_id}/snapshot")
        messages = managed.broker.messages_after(0)

    assert response.status_code == 200
    engagement = next(
        agent["engagement"]
        for agent in response.json()["snapshot"]["agents"]
        if agent["id"] == actor_id
    )
    recent_by_id = {
        item["engagement_id"]: item for item in engagement["recent"]
    }
    partial = recent_by_id["engagement-api-1"]
    failed = recent_by_id["engagement-api-2"]
    assert partial["status"] == "partial"
    assert failed["status"] == "failed"
    assert failed["compiler_status"] == "failed"
    assert partial["reference_ids"] == ["kitchen"]
    assert partial["participant_ids"] == [
        "agent-001",
        "kitchen",
    ]
    assert [group["status"] for group in partial["groups"]] == [
        "completed",
        "failed",
    ]
    assert partial["evidence"][0]["public_text"] == (
        "A grounded wave."
    )
    engagement_messages = [
        message.to_dict()
        for message in messages
        if message.message_type == "engagement_event"
    ]
    assert {
        message["payload"]["event"]["payload"]["engagement_status"]
        for message in engagement_messages
    } >= {"requested", "succeeded", "committed", "partial", "failed"}
    serialized = json.dumps(
        {
            "snapshot": response.json(),
            "messages": engagement_messages,
        }
    )
    for private_value in (
        "PRIVATE API INTENT",
        "PRIVATE API REASON",
        "PRIVATE COMPILER SUMMARY",
        "PRIVATE SCENE",
        "PRIVATE TERMINAL REASON",
        "PRIVATE GROUP REASON",
        "PRIVATE COMPILATION FAILURE",
        "PRIVATE FAILED SUMMARY",
        "energy_cost",
    ):
        assert private_value not in serialized


def test_public_physical_snapshot_contract_and_closed_container_privacy() -> None:
    cabinet_id = "object-z-cabinet"
    secret_id = "object-m-secret"
    room_id = "physical-api-building.room"
    with TestClient(app) as client:
        run_id = _create_physical_api_run(client)
        manager = app.state.simulation_manager
        assert isinstance(manager, SimulationManager)
        registry = manager.get_run(run_id).runner.registry

        snapshot = client.get(
            f"/simulation/runs/{run_id}/snapshot"
        ).json()["snapshot"]
        room_response = client.get(
            f"/simulation/runs/{run_id}/world/rooms/{room_id}"
        ).json()["room"]
        cabinet = client.get(
            f"/simulation/runs/{run_id}/world/objects/{cabinet_id}"
        ).json()["object"]
        hidden = client.get(
            f"/simulation/runs/{run_id}/world/objects/{secret_id}"
        )

        physical_world = snapshot["world"]["physical"]
        assert physical_world["spatial_metric"] == {
            "coordinate_system": "microcell",
            "microcells_per_legacy_cell": 9,
        }
        assert [item["id"] for item in physical_world["objects"]] == [
            "object-a-display",
            cabinet_id,
        ]
        physical_room = next(
            item
            for item in physical_world["rooms"]
            if item["id"] == room_id
        )
        assert physical_room["spatial"] == {
            "coordinate_system": "microcell",
            "microcells_per_legacy_cell": 9,
            "width_microcells": 36,
            "height_microcells": 27,
            "width_legacy_cells": 4,
            "height_legacy_cells": 3,
        }
        assert room_response["spatial"] == physical_room["spatial"]
        assert room_response["object_ids"] == [
            "object-a-display",
            cabinet_id,
        ]
        assert hidden.status_code == 404
        assert cabinet["definition_id"] == "physical-api-cabinet"
        assert cabinet["kind"] == "physical"
        assert cabinet["physical"]["pose"] == {
            "room_id": room_id,
            "anchor": {"x": 15, "y": 8},
            "orientation": "EAST",
        }
        assert cabinet["physical"]["footprint"]["cells"] == [
            {"x": 0, "y": 0},
            {"x": 1, "y": 0},
            {"x": 0, "y": 1},
        ]
        assert cabinet["physical"]["occupied_cells"] == [
            {"x": 14, "y": 8},
            {"x": 15, "y": 8},
            {"x": 15, "y": 9},
        ]
        assert cabinet["physical"]["obstruction"] == {
            "movement": "HARD",
            "vision": "OPAQUE",
            "hearing": "PASS",
            "smell": "PASS",
            "blocks_movement": True,
            "blocks_vision": True,
            "blocks_hearing": False,
            "blocks_smell": False,
        }
        assert cabinet["physical"]["openable"] == {
            "is_open": False,
            "is_locked": False,
        }
        assert [
            slot["id"] for slot in cabinet["physical"]["slots"]
        ] == ["a-shelf", "z-inside"]
        assert cabinet["physical"]["slots"][1] == {
            "id": "z-inside",
            "accepted_relations": ["IN_CONTAINER", "ON_SUPPORT"],
            "capacity": 2,
            "occupancy": None,
        }
        assert cabinet["physical"]["interaction_target"][
            "approach_anchors"
        ] == sorted(
            cabinet["physical"]["interaction_target"]["approach_anchors"],
            key=lambda item: (item["y"], item["x"]),
        )
        serialized = json.dumps(snapshot, sort_keys=True)
        assert "private-cabinet-owner" not in serialized
        assert "private-secret-owner" not in serialized
        assert secret_id not in serialized

        openable = registry.get_component(cabinet_id, OpenableComponent)
        openable.is_open = True
        state = registry.get_component(cabinet_id, PhysicalStateComponent)
        opened_state = replace(
            state,
            movement_obstruction=MovementObstruction.NONE,
            vision_obstruction=VisionObstruction.TRANSPARENT,
        )
        registry.get_resource(SpatialIndex).update(
            SpatialIndexEntry(cabinet_id, opened_state)
        )
        registry.set_component(cabinet_id, opened_state)

        opened = client.get(
            f"/simulation/runs/{run_id}/world/objects/{cabinet_id}"
        ).json()["object"]
        exposed = client.get(
            f"/simulation/runs/{run_id}/world/objects/{secret_id}"
        )
        assert opened["physical"]["openable"]["is_open"] is True
        assert opened["physical"]["obstruction"]["movement"] == "NONE"
        assert opened["physical"]["obstruction"]["vision"] == "TRANSPARENT"
        assert opened["physical"]["slots"][1]["occupancy"] == {
            "entity_ids": [secret_id],
            "count": 1,
            "remaining_capacity": 1,
        }
        assert exposed.status_code == 200
        assert "private-letter" not in exposed.text
        assert "private-secret-owner" not in exposed.text


def test_live_physical_relation_agent_and_rest_runtime_snapshot_alignment() -> None:
    agent_id = "physical-agent"
    cabinet_id = "object-z-cabinet"
    secret_id = "object-m-secret"
    room_id = "physical-api-building.room"
    with TestClient(app) as client:
        run_id = _create_physical_api_run(client)
        manager = app.state.simulation_manager
        assert isinstance(manager, SimulationManager)
        managed = manager.get_run(run_id)
        registry = managed.runner.registry

        cabinet_openable = registry.get_component(
            cabinet_id,
            OpenableComponent,
        )
        cabinet_state = registry.get_component(
            cabinet_id,
            PhysicalStateComponent,
        )
        cabinet_openable.is_open = True
        opened_cabinet = replace(
            cabinet_state,
            movement_obstruction=MovementObstruction.NONE,
            vision_obstruction=VisionObstruction.TRANSPARENT,
        )
        registry.get_resource(SpatialIndex).update(
            SpatialIndexEntry(cabinet_id, opened_cabinet)
        )
        registry.set_component(cabinet_id, opened_cabinet)

        agent_state = registry.get_component(
            agent_id,
            PhysicalStateComponent,
        )
        secret_state = registry.get_component(
            secret_id,
            PhysicalStateComponent,
        )
        registry.set_component(
            secret_id,
            replace(secret_state, pose=agent_state.pose),
        )
        registry.set_component(
            secret_id,
            SpatialParentRelationComponent(
                agent_id,
                PhysicalRelationKind.HELD_BY,
                "left",
            ),
        )
        registry.set_component(secret_id, CustodyComponent(agent_id))
        hands = registry.get_component(
            agent_id,
            CharacterHandStateComponent,
        )
        hands.left_hand_object_id = secret_id
        cabinet_openable.is_open = False
        closed_cabinet = replace(
            opened_cabinet,
            movement_obstruction=MovementObstruction.HARD,
            vision_obstruction=VisionObstruction.OPAQUE,
        )
        registry.get_resource(SpatialIndex).update(
            SpatialIndexEntry(cabinet_id, closed_cabinet)
        )
        registry.set_component(cabinet_id, closed_cabinet)

        action = ActionInstance(
            action_id="action-public-interaction",
            origin=ActionOrigin.OPERATOR,
            created_tick=0,
            created_at=0.0,
            root_correlation_id="correlation-public-interaction",
            action_name="OPEN",
            target_id=cabinet_id,
        )
        specification = InteractionSpecification(
            InteractionVerb.OPEN,
            cabinet_id,
        )
        registry.add_component(
            agent_id,
            InteractionRequestComponent(
                specification,
                "operator",
                status="running",
                action_instance=action,
            ),
        )
        registry.add_component(
            agent_id,
            InteractionExecutionComponent(
                specification,
                "operator",
                elapsed=0.25,
                duration=1.0,
                correlation_id=action.root_correlation_id,
                action_instance=action,
            ),
        )

        object_response = client.get(
            f"/simulation/runs/{run_id}/world/objects/{secret_id}"
        ).json()["object"]
        room_response = client.get(
            f"/simulation/runs/{run_id}/world/rooms/{room_id}"
        ).json()["room"]
        building_response = client.get(
            f"/simulation/runs/{run_id}/world/buildings/"
            "physical-api-building"
        ).json()["building"]
        city_response = client.get(
            f"/simulation/runs/{run_id}/world/city"
        ).json()["city"]
        agent_response = client.get(
            f"/simulation/runs/{run_id}/agents/{agent_id}"
        ).json()["agent"]
        rest_snapshot = client.get(
            f"/simulation/runs/{run_id}/snapshot"
        ).json()["snapshot"]
        runtime_snapshot = managed.broker.publish_snapshot().payload

        assert object_response["physical"]["pose"] == {
            "room_id": room_id,
            "anchor": agent_state.pose.anchor.to_payload(),
            "orientation": agent_state.pose.orientation.value,
        }
        assert object_response["physical"]["parent_relation"] == {
            "parent_id": agent_id,
            "kind": "HELD_BY",
            "slot_id": "left",
        }
        assert object_response["physical"]["held_by"] == agent_id
        assert object_response["physical"]["custodian_id"] == agent_id
        expected_object_ids = [
            "object-a-display",
            secret_id,
            cabinet_id,
        ]
        assert room_response["object_ids"] == expected_object_ids
        assert [
            item["id"]
            for item in building_response["rooms"][0]["objects"]
        ] == expected_object_ids
        assert [
            item["id"] for item in city_response["objects"]
        ] == expected_object_ids
        assert agent_response["physical"]["pose"] == {
            "room_id": room_id,
            "anchor": agent_state.pose.anchor.to_payload(),
            "orientation": agent_state.pose.orientation.value,
        }
        assert agent_response["physical"]["posture"] == {
            "value": "STANDING",
            "support_id": None,
        }
        assert agent_response["physical"]["hands"] == {
            "left_object_id": secret_id,
            "right_object_id": None,
            "held_object_ids": [secret_id],
        }
        assert agent_response["interaction"]["request"]["status"] == "running"
        assert agent_response["interaction"]["execution"] == {
            "verb": "OPEN",
            "target_id": cabinet_id,
            "destination_id": None,
            "slot_id": None,
            "source": "operator",
            "status": "running",
            "elapsed": 0.25,
            "duration": 1.0,
            "correlation_id": "correlation-public-interaction",
            "action": {
                "action_id": "action-public-interaction",
                "status": "running",
                "origin": "operator",
                "action_name": "OPEN",
                "target_id": cabinet_id,
                "created_tick": 0,
                "created_at": 0.0,
                "root_correlation_id": "correlation-public-interaction",
                "plan_id": None,
                "plan_revision": None,
                "goal_ids": [],
            },
        }
        assert rest_snapshot["world"]["physical"] == runtime_snapshot[
            "world"
        ]["physical"]
        assert rest_snapshot["city"]["objects"] == city_response["objects"]
        serialized = json.dumps(rest_snapshot, sort_keys=True)
        assert "private-cabinet-owner" not in serialized
        assert "private-secret-owner" not in serialized


def test_websocket_subscription_does_not_mutate_shared_sequence() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        before = client.get(f"/simulation/runs/{run_id}").json()

        with client.websocket_connect(
            f"/simulation/runs/{run_id}/stream"
            f"?after_sequence={before['latest_sequence']}"
            f"&after_snapshot_revision={before['snapshot_revision']}"
        ) as websocket:
            hello = websocket.receive_json()

        after = client.get(f"/simulation/runs/{run_id}").json()

    assert hello["type"] == "hello"
    assert after["latest_sequence"] == before["latest_sequence"]


def test_live_event_surfaces_share_private_visibility_classification() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        manager = app.state.simulation_manager
        assert isinstance(manager, SimulationManager)
        managed = manager.get_run(run_id)
        initial_sequence = managed.broker.latest_sequence
        assert client.portal is not None
        private_event_ids: set[str] = set()
        for payload in (
            {"visibility": "PRIVATE_RESEARCH"},
            {"visibility": {"level": "private"}},
            {"content_visibility": "Private_Research"},
            {"private_visibility": True},
        ):
            private_event_ids.add(
                client.portal.call(
                    lambda payload=payload: managed.runner.events.emit(
                        "diagnostic.private",
                        simulation_tick=managed.runner.clock.tick,
                        simulation_time=managed.runner.clock.simulation_time,
                        payload=payload,
                    ).event_id
                )
            )
        assert managed.broker.latest_sequence == initial_sequence
        public_event_id = client.portal.call(
            lambda: managed.runner.events.emit(
                "diagnostic.public",
                simulation_tick=managed.runner.clock.tick,
                simulation_time=managed.runner.clock.simulation_time,
                payload={"marker": "public"},
            ).event_id
        )

        public_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]
        all_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"include_private": True, "limit": 1000},
        ).json()["events"]
        messages = managed.broker.messages_after(initial_sequence)

    public_ids = {event["event_id"] for event in public_events}
    all_ids = {event["event_id"] for event in all_events}
    assert public_event_id in public_ids
    assert private_event_ids.isdisjoint(public_ids)
    assert private_event_ids.issubset(all_ids)
    assert len(messages) == 1
    assert messages[0].payload["event"]["event_id"] == public_event_id


def test_unknown_resources_and_invalid_mutation_are_rejected() -> None:
    with TestClient(app) as client:
        assert client.get("/simulation/runs/missing").status_code == 404
        run_id = create_run(client)
        invalid = client.patch(
            f"/simulation/runs/{run_id}/agents/agent-001/vitals",
            json={"stress": 101},
        )
        assert invalid.status_code == 422
        missing_agent = client.get(
            f"/simulation/runs/{run_id}/agents/missing"
        )
        assert missing_agent.status_code == 404


def test_openapi_contains_only_canonical_dataset_routes() -> None:
    with TestClient(app) as client:
        paths = set(client.get("/openapi.json").json()["paths"])

    assert "/simulation/data/runs" in paths
    assert "/simulation/runs/{run_id}/data/records" in paths
    assert "/simulation/runs/{run_id}/data/physical-object-states" in paths
    assert "/simulation/runs/{run_id}/data/physical-relations" in paths
    assert "/simulation/runs/{run_id}/data/physical-objects" not in paths
    assert "/simulation/runs/{run_id}/data/relations" not in paths
    assert "/simulation/runs/{run_id}/exports/complete" in paths
    assert "/simulation/runs/{run_id}/exports/records" in paths
    assert "/simulation/runs/{run_id}/exports/bundle" in paths
    assert not any(path.startswith("/simulation/data-management/") for path in paths)
    assert "/simulation/runs/{run_id}/records" not in paths
    assert "/simulation/runs/{run_id}/export" not in paths
    assert "/simulation/runs/{run_id}/export/records" not in paths
    assert "/simulation/runs/{run_id}/data/export/records" not in paths
    assert "/simulation/runs/{run_id}/export/bundle" not in paths
    assert "/simulation/runs/{run_id}/data/export/bundle" not in paths


def test_synthesis_enabled_scenario_requires_configured_provider() -> None:
    payload = load_scenario_payload("baseline.json")
    payload["character_situation_synthesis"] = {"enabled": True}

    with TestClient(app) as client:
        response = client.post(
            "/simulation/scenarios",
            json={"scenario": payload, "character_assignments": {}},
        )

    assert response.status_code == 422
    assert "no model provider is configured" in response.json()["detail"]


def test_dataset_query_exports_private_opt_in_and_persisted_stopped_events() -> None:
    with TestClient(app) as client:
        run_id = create_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        assert client.post(f"/simulation/runs/{run_id}/stop").status_code == 200
        persisted_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]

        default_records = client.get(
            f"/simulation/runs/{run_id}/data/records",
            params={"limit": 2},
        ).json()
        next_records = client.get(
            f"/simulation/runs/{run_id}/data/records",
            params={"cursor": default_records["next_cursor"], "limit": 1000},
        ).json()
        private_records = client.get(
            f"/simulation/runs/{run_id}/data/records",
            params={
                "include_private": True,
                "visibility": "PRIVATE_RESEARCH",
                "limit": 1000,
            },
        ).json()
        rejected_private = client.get(
            f"/simulation/runs/{run_id}/data/records",
            params={"visibility": "PRIVATE_RESEARCH"},
        )
        schema = client.get(f"/simulation/runs/{run_id}/data/schema")
        private_schema = client.get(
            f"/simulation/runs/{run_id}/data/schema",
            params={"include_private": True},
        )
        bundle = client.get(f"/simulation/runs/{run_id}/exports/bundle")
        negative_export_cursor = client.get(
            f"/simulation/runs/{run_id}/exports/records",
            params={"cursor": -1},
        )
        summary = client.get(f"/simulation/runs/{run_id}/data")
        complete_export = client.get(
            f"/simulation/runs/{run_id}/exports/complete"
        )
        removed_paths = [
            f"/simulation/runs/{run_id}/schema",
            f"/simulation/runs/{run_id}/records",
            f"/simulation/runs/{run_id}/goals",
            f"/simulation/runs/{run_id}/decisions",
            f"/simulation/runs/{run_id}/actions",
            f"/simulation/runs/{run_id}/interactions",
            f"/simulation/runs/{run_id}/state",
            f"/simulation/runs/{run_id}/transitions",
            f"/simulation/runs/{run_id}/aggregates",
            f"/simulation/runs/{run_id}/episodes/actions",
            f"/simulation/runs/{run_id}/model-requests",
            f"/simulation/runs/{run_id}/tool-executions",
            f"/simulation/runs/{run_id}/perception",
            f"/simulation/runs/{run_id}/memory",
            f"/simulation/runs/{run_id}/opportunities",
            f"/simulation/runs/{run_id}/export",
            f"/simulation/runs/{run_id}/export/records",
            f"/simulation/runs/{run_id}/data/export/records",
            f"/simulation/runs/{run_id}/export/bundle",
            f"/simulation/runs/{run_id}/data/export/bundle",
        ]
        removed_statuses = [
            client.get(path).status_code for path in removed_paths
        ]

        stopped_events = client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]

    with TestClient(app) as restarted_client:
        restarted_events = restarted_client.get(
            f"/simulation/runs/{run_id}/events",
            params={"limit": 1000},
        ).json()["events"]
        missing_records_status = restarted_client.get(
            "/simulation/runs/missing/data/records"
        ).status_code

    returned = default_records["records"] + next_records["records"]
    assert all(
        record["visibility"] != "PRIVATE_RESEARCH" for record in returned
    )
    assert private_records["records"]
    assert all(
        record["visibility"] == "PRIVATE_RESEARCH"
        for record in private_records["records"]
    )
    assert rejected_private.status_code == 422
    assert negative_export_cursor.status_code == 422
    assert negative_export_cursor.json()["detail"] == (
        "raw record cursor must not be negative"
    )
    assert schema.status_code == 200
    assert schema.json()["schema_id"] == "stage0.data_dictionary"
    assert all(
        row["visibility"] != "PRIVATE_RESEARCH"
        for row in schema.json()["observed_record_schemas"]
    )
    assert any(
        row["visibility"] == "PRIVATE_RESEARCH"
        for row in private_schema.json()["observed_record_schemas"]
    )
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist()[:3] == [
            "manifest.json",
            "schema.json",
            "records.ndjson",
        ]
        bundle_schema = json.loads(archive.read("schema.json"))
        assert all(
            row["visibility"] != "PRIVATE_RESEARCH"
            for row in bundle_schema["observed_record_schemas"]
        )
    assert summary.status_code == 200
    assert "record_counts" in summary.json()
    assert complete_export.status_code == 200
    assert (
        json.loads(complete_export.text.splitlines()[0])["record_type"]
        == "run"
    )
    assert removed_statuses == [404] * len(removed_statuses)
    assert stopped_events == persisted_events
    assert restarted_events == persisted_events
    assert missing_records_status == 404


def test_physical_dataset_routes_filters_privacy_and_export_headers() -> None:
    cabinet_id = "object-z-cabinet"
    secret_id = "object-m-secret"
    room_id = "physical-api-building.room"
    with TestClient(app) as client:
        run_id = _create_physical_api_run(client)
        client.post(f"/simulation/runs/{run_id}/pause")
        client.post(f"/simulation/runs/{run_id}/step")
        client.post(f"/simulation/runs/{run_id}/stop")

        public_states = client.get(
            f"/simulation/runs/{run_id}/data/physical-object-states"
        )
        private_states = client.get(
            f"/simulation/runs/{run_id}/data/physical-object-states",
            params={
                "object_id": cabinet_id,
                "room_id": room_id,
                "phase": "run_initial",
                "is_open": False,
                "is_locked": False,
                "include_private": True,
            },
        )
        private_relations = client.get(
            f"/simulation/runs/{run_id}/data/physical-relations",
            params={
                "object_id": secret_id,
                "parent_id": cabinet_id,
                "relation_kind": "IN_CONTAINER",
                "include_private": True,
            },
        )
        invalid_phase = client.get(
            f"/simulation/runs/{run_id}/data/physical-object-states",
            params={"phase": "not-a-phase", "include_private": True},
        )
        public_summary = client.get(
            f"/simulation/runs/{run_id}/data"
        ).json()
        private_summary = client.get(
            f"/simulation/runs/{run_id}/data",
            params={"include_private": True},
        ).json()
        complete = client.get(
            f"/simulation/runs/{run_id}/exports/complete"
        )
        private_bundle = client.get(
            f"/simulation/runs/{run_id}/exports/bundle",
            params={"include_private": True, "object_id": cabinet_id},
        )

    assert public_states.status_code == 200
    assert public_states.json()["rows"] == []
    assert private_states.status_code == 200
    assert len(private_states.json()["rows"]) == 1
    assert private_states.json()["rows"][0]["object_id"] == cabinet_id
    assert private_relations.status_code == 200
    assert private_relations.json()["rows"]
    assert all(
        row["object_id"] == secret_id
        for row in private_relations.json()["rows"]
    )
    assert invalid_phase.status_code == 422
    assert public_summary["physical"]["state_sample_count"] == 0
    assert private_summary["physical"]["state_sample_count"] > 0
    assert complete.headers["X-Stage0-Private-Included"] == "true"
    assert "PRIVATE_RESEARCH" in complete.headers[
        "X-Stage0-Privacy-Warning"
    ]
    manifest = json.loads(complete.text.splitlines()[0])
    assert manifest["schema_version"] == "stage0.dataset.v6"
    assert manifest["payload"]["private_records_included"] is True
    assert private_bundle.headers["X-Stage0-Private-Included"] == "true"
    with zipfile.ZipFile(io.BytesIO(private_bundle.content)) as archive:
        bundle_manifest = json.loads(archive.read("manifest.json"))
        assert (
            bundle_manifest["sqlite_schema_version"]
            == DATABASE_SCHEMA_VERSION
        )
        assert bundle_manifest["private_records_included"] is True
