import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.data_capture import (
    DatasetQueryFilter,
    DatasetRecordFilter,
)
from stage0_sim.application.data_management import DatasetManagementService
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import (
    EngagementProgramComponent,
    HomeostasisComponent,
)
from stage0_sim.domain.systems.system1 import System1ArbitrationSystem
from tests.integration.simulation.test_engagement_runtime import (
    COMPILE_ENGAGEMENT_TOOL,
    _auditory_group,
    _bounded_group,
    _compiled_turn,
    _disable_controller,
    _engage_turn,
    _expressive_group,
    _scenario,
    _turn,
)


def _scenario_for(run_id: str) -> ScenarioDefinition:
    payload = _scenario().model_dump(mode="json")
    payload["run_id"] = run_id
    return ScenarioDefinition.model_validate(payload)


def _capture_partial_engagement(
    store: SQLiteDatasetStore,
    run_id: str,
) -> str:
    rejected = _expressive_group(
        group_id="compiler-rejected",
        invocation_id="compiler-rejected-1",
    )
    rejected_invocations = rejected["invocations"]
    assert isinstance(rejected_invocations, list)
    rejected_invocation = rejected_invocations[0]
    assert isinstance(rejected_invocation, dict)
    rejected_invocation["capability"] = "unknown_capability"
    scenario = _scenario_for(run_id)
    runner = create_runner(
        scenario,
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _compiled_turn(
                    _expressive_group(),
                    _auditory_group(),
                    _bounded_group(),
                    rejected,
                ),
            )
        ),
    )
    RunDataCollector(
        store=store,
        runner=runner,
        scenario=scenario.model_dump(mode="json"),
    )
    runner.run_for(1)
    _disable_controller(runner)
    component = runner.registry.get_component(
        "alex",
        EngagementProgramComponent,
    )
    invalid_invocation = replace(
        component.program.groups[2].invocations[0],
        capability="runtime_missing_capability",
    )
    invalid_group = replace(
        component.program.groups[2],
        invocations=(invalid_invocation,),
    )
    runner.registry.set_component(
        "alex",
        EngagementProgramComponent(
            replace(
                component.program,
                groups=(
                    component.program.groups[0],
                    component.program.groups[1],
                    invalid_group,
                ),
            )
        ),
    )
    runner.run_for(2)
    runner.stop()
    return component.program.engagement_id


def test_engagement_projection_privacy_rebuild_dictionary_and_bundle(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "engagement-data.sqlite3")
    run_id = "engagement-partial-data"
    engagement_id = _capture_partial_engagement(store, run_id)
    private_filter = DatasetQueryFilter(include_private=True, limit=1000)

    engagement = store.query_table(
        run_id,
        "engagements",
        private_filter,
    ).rows[0]
    groups = store.query_table(
        run_id,
        "engagement_groups",
        private_filter,
    ).rows
    invocations = store.query_table(
        run_id,
        "engagement_invocations",
        private_filter,
    ).rows
    public_engagement = store.query_table(run_id, "engagements").rows[0]
    public_groups = store.query_table(run_id, "engagement_groups").rows
    public_invocations = store.query_table(
        run_id,
        "engagement_invocations",
    ).rows
    public_target_probe = store.query_table(
        run_id,
        "engagement_invocations",
        DatasetQueryFilter(primary_entity_id="office"),
    ).rows
    private_target_probe = store.query_table(
        run_id,
        "engagement_invocations",
        DatasetQueryFilter(
            primary_entity_id="office",
            include_private=True,
        ),
    ).rows
    interaction = store.query_table(
        run_id,
        "interaction_episodes",
        DatasetQueryFilter(interaction_type="engagement"),
    ).rows[0]
    typed_records = store.query_records(
        run_id,
        DatasetRecordFilter(
            engagement_id=engagement_id,
            include_private=True,
            limit=1000,
        ),
    )
    typed_group = store.query_table(
        run_id,
        "engagement_groups",
        DatasetQueryFilter(
            engagement_id=engagement_id,
            engagement_group_id="stretch",
            include_private=True,
        ),
    )
    typed_invocation = store.query_table(
        run_id,
        "engagement_invocations",
        DatasetQueryFilter(
            engagement_invocation_id="gesture-1",
            include_private=True,
        ),
    )
    schema = store.data_dictionary(run_id, include_private=True)
    summary = store.summary(run_id, include_private=True)
    management = DatasetManagementService(store)
    aggregate = management.aggregate(
        management.selection([run_id]),
        include_private_derived=True,
    )
    before = {
        table: store.query_table(run_id, table, private_filter).rows
        for table in (
            "engagements",
            "engagement_groups",
            "engagement_invocations",
        )
    }
    rebuild = store.rebuild_run_projections(run_id)
    after = {
        table: store.query_table(run_id, table, private_filter).rows
        for table in before
    }
    public_bundle = io.BytesIO()
    store.write_analysis_bundle(
        run_id,
        public_bundle,
        DatasetQueryFilter(limit=1000),
    )

    assert engagement["status"] == "partial"
    assert engagement["terminal_outcome"] == "partial"
    assert engagement["private_intent"] == (
        "Wave and perform a short calming stretch."
    )
    assert engagement["private_compiler_summary"] == (
        "Alex performs a bounded engagement."
    )
    group_statuses = {
        row["engagement_group_id"]: (
            row["validation_status"],
            row["execution_status"],
        )
        for row in groups
    }
    assert group_statuses == {
        "compiler-rejected": ("rejected", "not_run"),
        "gesture": ("valid", "completed"),
        "stretch": ("valid", "failed"),
        "warning": ("valid", "completed"),
    }
    invocation_statuses = {
        row["engagement_invocation_id"]: row["status"]
        for row in invocations
    }
    assert invocation_statuses == {
        "gesture-1": "committed",
        "stretch-1": "failed",
        "warning-1": "committed",
    }
    assert {
        row["engagement_group_id"] for row in public_groups
    } == {"gesture", "stretch", "warning"}
    assert {
        row["engagement_invocation_id"] for row in public_invocations
    } == {"gesture-1", "warning-1"}
    assert "private_intent" not in public_engagement
    assert "private_compiler_summary" not in public_engagement
    assert "private_proposal" not in public_engagement
    assert "private_result" not in public_engagement
    assert "target_id" not in public_invocations[0]
    assert "private_result" not in public_invocations[0]
    assert public_target_probe == ()
    assert private_target_probe
    assert interaction["interaction_type"] == "engagement"
    assert interaction["status"] == "partial"
    assert interaction["episode"]["feature_schema"] == (
        "stage0.feature.interaction_episode.v2"
    )
    assert typed_records.records
    assert len(typed_group.rows) == 1
    assert len(typed_invocation.rows) == 1
    assert schema["dataset_schema_version"] == "stage0.dataset.v6"
    assert schema["sqlite_schema_version"] == 12
    assert schema["feature_schema_versions"][
        "stage0.feature.engagement"
    ] == "1"
    assert schema["feature_schema_versions"][
        "stage0.feature.interaction_episode"
    ] == "2"
    assert {
        table["name"]
        for table in schema["normalized_and_derived_tables"]
    } >= {
        "engagements",
        "engagement_groups",
        "engagement_invocations",
    }
    assert schema["retention"]["policy"] == (
        "explicit_guarded_run_deletion_only"
    )
    assert "engagements" in schema["retention"]["run_scoped_tables"]
    assert summary["engagement_integrity"]["valid"] is True
    assert aggregate.distributions["feature.family"]["engagements"] == 1
    assert aggregate.distributions["feature.family"][
        "engagement_groups"
    ] == 4
    assert any(
        metric.name == "engagements.count"
        and metric.pooled.count == 1
        for metric in aggregate.metrics
    )
    assert rebuild["derived_feature_counts"]["engagements"] == 1
    assert before == after

    public_json = json.dumps(
        {
            "engagement": public_engagement,
            "groups": public_groups,
            "invocations": public_invocations,
            "interaction": interaction,
            "records": [
                record.to_dict()
                for record in store.query_records(
                    run_id,
                    DatasetRecordFilter(
                        engagement_id=engagement_id,
                        limit=1000,
                    ),
                ).records
            ],
        },
        sort_keys=True,
    )
    assert "Wave and perform a short calming stretch." not in public_json
    assert "Alex performs a bounded engagement." not in public_json
    assert "listener_stress_delta" not in public_json
    assert '"target_id": "office"' not in public_json

    with zipfile.ZipFile(public_bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        schema_document = json.loads(archive.read("schema.json"))
        bundle_text = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.endswith((".json", ".ndjson", ".csv"))
        ).decode("utf-8")
    assert manifest["dataset_schema_version"] == "stage0.dataset.v6"
    assert manifest["sqlite_schema_version"] == 12
    assert "tables/engagements.csv" in manifest["files"]
    assert schema_document["feature_schema_versions"][
        "stage0.feature.engagement"
    ] == "1"
    assert "Wave and perform a short calming stretch." not in bundle_text
    assert "Alex performs a bounded engagement." not in bundle_text
    assert "private_intent" not in bundle_text
    assert "private_result" not in bundle_text
    store.close()


def test_failed_cancelled_unfinished_and_guarded_deletion_are_run_scoped(
    tmp_path: Path,
) -> None:
    store = SQLiteDatasetStore(tmp_path / "engagement-terminal.sqlite3")

    failed_scenario = _scenario_for("engagement-failed-data")
    failed_runner = create_runner(
        failed_scenario,
        model_client=ScriptedModelClient(
            (
                _engage_turn(),
                _turn(
                    COMPILE_ENGAGEMENT_TOOL,
                    {
                        "disposition": "unsupported",
                        "summary": "Private compiler rejection.",
                        "reason": "No grounded capability.",
                    },
                ),
            )
        ),
    )
    RunDataCollector(
        store=store,
        runner=failed_runner,
        scenario=failed_scenario.model_dump(mode="json"),
    )
    failed_runner.run_for(1)
    failed_runner.stop()

    cancelled_scenario = _scenario_for("engagement-cancelled-data")
    cancelled_runner = create_runner(
        cancelled_scenario,
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_bounded_group()))
        ),
    )
    RunDataCollector(
        store=store,
        runner=cancelled_runner,
        scenario=cancelled_scenario.model_dump(mode="json"),
    )
    cancelled_runner.run_for(1)
    _disable_controller(cancelled_runner)
    cancelled_runner.run_for(1)
    cancelled_runner.registry.get_component(
        "alex",
        HomeostasisComponent,
    ).energy = 0
    System1ArbitrationSystem().update(cancelled_runner.context)
    cancelled_runner.stop()

    unfinished_scenario = _scenario_for("engagement-unfinished-data")
    unfinished_runner = create_runner(
        unfinished_scenario,
        model_client=ScriptedModelClient(
            (_engage_turn(), _compiled_turn(_bounded_group()))
        ),
    )
    unfinished_collector = RunDataCollector(
        store=store,
        runner=unfinished_runner,
        scenario=unfinished_scenario.model_dump(mode="json"),
    )
    unfinished_runner.run_for(1)
    _disable_controller(unfinished_runner)
    unfinished_collector.finalize("stopped")
    unfinished_runner.stop()

    statuses = {
        run_id: store.query_table(run_id, "engagements").rows[0]["status"]
        for run_id in (
            "engagement-failed-data",
            "engagement-cancelled-data",
            "engagement-unfinished-data",
        )
    }
    assert statuses == {
        "engagement-failed-data": "failed",
        "engagement-cancelled-data": "cancelled",
        "engagement-unfinished-data": "unfinished",
    }

    service = DatasetManagementService(store)
    selection = service.selection(["engagement-cancelled-data"])
    preview = service.preview_deletion(selection)
    result = service.delete(selection, preview.confirmation_token)

    assert result.deleted_table_counts["engagements"] == 1
    assert store.query_table(
        "engagement-failed-data",
        "engagements",
    ).rows[0]["status"] == "failed"
    assert store.query_table(
        "engagement-unfinished-data",
        "engagements",
    ).rows[0]["status"] == "unfinished"
    store.close()
