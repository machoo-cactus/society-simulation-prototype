import json
from pathlib import Path

import pytest

from stage0_sim.application.scenario import (
    ScenarioComponents,
    ScenarioLoadError,
    create_runner,
    load_scenario,
)
from stage0_sim.cli import main
from tests.helpers.paths import EXAMPLE_CHARACTERS, EXAMPLE_SCENARIOS


def test_scenario_loader_bootstraps_entities(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "schema_version": 8,
                "name": "test",
                "seed": 7,
                "entities": [
                    {
                        "id": "agent",
                        "components": {"metadata": {"name": "A"}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    scenario = load_scenario(scenario_path)
    runner = create_runner(scenario)

    assert runner.configuration.seed == 7
    assert runner.registry.entities() == ("agent",)
    component = runner.registry.get_component("agent", ScenarioComponents)
    assert component.values["metadata"]["name"] == "A"


def test_scenario_loader_rejects_duplicate_entities(tmp_path: Path) -> None:
    scenario_path = tmp_path / "invalid.json"
    scenario_path.write_text(
        '{"schema_version":8,"name":"invalid",'
        '"entities":[{"id":"same"},{"id":"same"}]}',
        encoding="utf-8",
    )

    with pytest.raises(ScenarioLoadError, match="entity IDs must be unique"):
        load_scenario(scenario_path)


def test_cli_emits_canonical_jsonl(tmp_path: Path) -> None:
    output_path = tmp_path / "events.jsonl"
    scenario_path = EXAMPLE_SCENARIOS / "minimal.json"

    exit_code = main(
        [
            "run",
            str(scenario_path),
            "--ticks",
            "2",
            "--characters-dir",
            str(EXAMPLE_CHARACTERS),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    events = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events] == [
        "simulation.started",
        "simulation.tick",
        "simulation.tick",
    ]
    assert "wall_time" not in events[0]
    assert "agent_id" not in events[0]
    assert "causation_id" not in events[0]
    assert "correlation_id" not in events[0]


def test_cli_rejects_schema_v2_user_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario_path = tmp_path / "legacy.json"
    scenario_path.write_text(
        '{"schema_version":2,"name":"legacy"}',
        encoding="utf-8",
    )

    exit_code = main(["run", str(scenario_path), "--ticks", "0"])

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "scenario schema version 8 is required" in error
    assert "stage0-sim migrate content" in error


def test_cli_runs_packaged_demo_without_repository_examples(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "events.jsonl"

    exit_code = main(
        [
            "run",
            "demo",
            "--ticks",
            "1",
            "--characters-dir",
            str(tmp_path / "characters"),
            "--elements-dir",
            str(tmp_path / "elements"),
            "--database",
            str(tmp_path / "demo.sqlite3"),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "characters" / "bundled-demo-character.json").is_file()
    assert '"event_type":"simulation.tick"' in output_path.read_text(
        encoding="utf-8"
    )


def test_cli_migrate_content_defaults_to_check_and_supports_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy = Path("tests/fixtures/migrations/catalog/legacy")
    common = [
        "migrate",
        "content",
        "--characters-dir",
        str(legacy / "characters"),
        "--elements-dir",
        str(legacy / "elements"),
        "--scenarios-dir",
        str(legacy / "scenarios"),
    ]

    assert main(common) == 1
    checked = json.loads(capsys.readouterr().out)
    assert checked["succeeded"] is True
    assert checked["changed_count"] == 6

    output = tmp_path / "output"
    report = tmp_path / "report.json"
    assert main([*common, "--output", str(output), "--report", str(report)]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["succeeded"] is True
    assert report.is_file()
    assert (output / "scenarios/legacy-city.json").is_file()
