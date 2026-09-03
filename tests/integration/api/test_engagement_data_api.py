import io
import json
import zipfile

from fastapi.testclient import TestClient

from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.api.app import app
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from tests.integration.simulation.test_engagement_runtime import (
    _compiled_turn,
    _disable_controller,
    _engage_turn,
    _expressive_group,
    _scenario,
)


async def _capture_api_engagement(manager: SimulationManager) -> str:
    payload = _scenario().model_dump(mode="json")
    payload["run_id"] = "engagement-api-data"
    scenario = ScenarioDefinition.model_validate(payload)
    runner = create_runner(
        scenario,
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_expressive_group()))
        ),
    )
    RunDataCollector(
        store=manager.dataset_store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )
    await runner.run_for_async(1)
    _disable_controller(runner)
    await runner.run_for_async(2)
    runner.stop()
    return runner.events.run_id


def test_canonical_engagement_data_routes_filters_and_exports() -> None:
    with TestClient(app) as client:
        manager = client.app.state.simulation_manager
        run_id = client.portal.call(
            lambda: _capture_api_engagement(manager)
        )
        engagements = client.get(
            f"/simulation/runs/{run_id}/data/engagements"
        )
        engagement_id = engagements.json()["rows"][0]["engagement_id"]
        groups = client.get(
            f"/simulation/runs/{run_id}/data/engagement-groups",
            params={"engagement_id": engagement_id},
        )
        invocations = client.get(
            f"/simulation/runs/{run_id}/data/engagement-invocations",
            params={
                "engagement_id": engagement_id,
                "engagement_invocation_id": "gesture-1",
            },
        )
        private = client.get(
            f"/simulation/runs/{run_id}/data/engagements",
            params={"engagement_id": engagement_id, "include_private": True},
        )
        raw = client.get(
            f"/simulation/runs/{run_id}/data/records",
            params={"engagement_id": engagement_id, "limit": 1000},
        )
        exported = client.get(
            f"/simulation/runs/{run_id}/exports/records",
            params={"engagement_id": engagement_id},
        )
        bundle = client.get(
            f"/simulation/runs/{run_id}/exports/bundle",
            params={"engagement_id": engagement_id},
        )
        paths = set(client.get("/openapi.json").json()["paths"])

    assert engagements.status_code == 200
    assert engagements.json()["rows"][0]["status"] == "completed"
    assert groups.status_code == 200
    assert [row["engagement_group_id"] for row in groups.json()["rows"]] == [
        "gesture"
    ]
    assert invocations.status_code == 200
    invocation = invocations.json()["rows"][0]
    assert invocation["engagement_invocation_id"] == "gesture-1"
    assert "target_id" not in invocation
    assert "private_result" not in invocation
    assert private.status_code == 200
    assert private.json()["rows"][0]["private_intent"] == (
        "Wave and perform a short calming stretch."
    )
    public_text = json.dumps(
        {
            "engagements": engagements.json(),
            "groups": groups.json(),
            "invocations": invocations.json(),
            "raw": raw.json(),
        },
        sort_keys=True,
    )
    assert "Wave and perform a short calming stretch." not in public_text
    assert "Alex performs a bounded engagement." not in public_text
    assert '"target_id": "office"' not in public_text
    assert exported.headers["X-Stage0-Private-Included"] == "false"
    assert "Wave and perform a short calming stretch." not in exported.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        bundle_text = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.endswith((".json", ".ndjson", ".csv"))
        ).decode("utf-8")
    assert manifest["filters"]["engagement_id"] == engagement_id
    assert "tables/engagements.csv" in manifest["files"]
    assert "Wave and perform a short calming stretch." not in bundle_text
    assert "private_intent" not in bundle_text
    assert "private_result" not in bundle_text
    assert "/simulation/runs/{run_id}/data/engagements" in paths
    assert "/simulation/runs/{run_id}/data/engagement-groups" in paths
    assert "/simulation/runs/{run_id}/data/engagement-invocations" in paths
