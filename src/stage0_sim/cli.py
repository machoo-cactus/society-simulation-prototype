import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.character_synthesis import (
    ModelCharacterSituationSynthesizer,
    compose_character_situations,
)
from stage0_sim.application.characters import (
    PreparedScenario,
    prepare_scenario,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.scenario import ScenarioLoadError, create_runner
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    load_and_resolve_scenario,
    resolve_scenario,
)
from stage0_sim.config import create_model_client, get_settings
from stage0_sim.domain.events import DomainEvent
from stage0_sim.domain.npcs import NpcControlMode
from stage0_sim.resources import bundled_demo_source, ensure_bundled_demo_character


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage0-sim")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run a JSON scenario")
    run_parser.add_argument(
        "scenario",
        help="scenario JSON path, or 'demo' for the packaged example",
    )
    run_parser.add_argument("--ticks", type=int, default=10)
    run_parser.add_argument("--speed", type=float)
    run_parser.add_argument(
        "--npc-control",
        choices=[mode.value for mode in NpcControlMode],
        help="override automatic, model, or deterministic NPC control",
    )
    run_parser.add_argument("--realtime", action="store_true")
    run_parser.add_argument("--full-events", action="store_true")
    run_parser.add_argument("--output", type=Path)
    run_parser.add_argument("--database", type=Path)
    run_parser.add_argument("--export", type=Path)
    run_parser.add_argument("--characters-dir", type=Path)
    run_parser.add_argument("--elements-dir", type=Path)
    run_parser.add_argument(
        "--character",
        action="append",
        default=[],
        metavar="SLOT_ID=CHARACTER_ID",
        help="assign a reusable character to a scenario slot",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 2
    if args.ticks < 0:
        print("--ticks must not be negative", file=sys.stderr)
        return 2

    try:
        settings = get_settings()
        element_library = FileSystemElementLibrary(
            args.elements_dir or settings.element_directory
        )
        bundled_demo = args.scenario == "demo"
        resolved = (
            resolve_scenario(bundled_demo_source(), element_library)
            if bundled_demo
            else load_and_resolve_scenario(Path(args.scenario), element_library)
        )
        scenario = resolved.scenario
        scenario_source = resolved.source.model_dump(mode="json")
        resolved_elements = resolved.provenance_payload()
        character_directory = args.characters_dir or settings.character_directory
        assignments = _parse_character_assignments(args.character)
        character_library = FileSystemCharacterLibrary(character_directory)
        if bundled_demo:
            ensure_bundled_demo_character(character_library)
        prepared = prepare_scenario(
            scenario,
            character_library,
            assignments,
        )
        model_client = create_model_client(settings)
        situations = asyncio.run(
            compose_character_situations(
                scenario=prepared.scenario,
                assignments=prepared.assignments,
                characters=prepared.characters,
                synthesizer=(
                    ModelCharacterSituationSynthesizer(model_client)
                    if model_client is not None
                    else None
                ),
            )
        )
        prepared = PreparedScenario(
            scenario=prepared.scenario,
            assignments=prepared.assignments,
            characters=prepared.characters,
            situations=situations,
            scenario_source=scenario_source,
            resolved_elements=resolved_elements,
        )
        runner = create_runner(
            prepared.scenario,
            resolved_characters=prepared.runtime_characters(),
            resolved_situations=prepared.runtime_situations(),
            speed=args.speed,
            model_client=model_client,
            model_max_output_tokens=settings.llm_max_output_tokens,
            model_max_concurrency=settings.llm_max_concurrency,
            npc_control_mode=args.npc_control,
        )
        database_path = args.database or (
            settings.data_directory / f"{runner.events.run_id}.sqlite3"
        )
        store = SQLiteDatasetStore(database_path)
        collector = RunDataCollector(
            store=store,
            runner=runner,
            scenario=prepared.dataset_payload(),
            private_provenance=prepared.private_research_provenance(),
        )
    except (
        OSError,
        json.JSONDecodeError,
        ScenarioLoadError,
        ScenarioResolutionError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 2

    output = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    write_events = True

    def write_event(event: DomainEvent) -> None:
        if not write_events:
            return
        content = event.to_dict() if args.full_events else event.canonical_dict()
        print(json.dumps(content, sort_keys=True, separators=(",", ":")), file=output)

    runner.events.subscribe(write_event)
    completed = False
    try:
        if args.realtime:
            asyncio.run(runner.run_realtime(args.ticks))
        else:
            runner.run_for(args.ticks)
        completed = True
    finally:
        collector.finalize("completed" if completed else "failed")
        write_events = False
        runner.stop()
        if args.export:
            args.export.parent.mkdir(parents=True, exist_ok=True)
            with args.export.open("w", encoding="utf-8", newline="\n") as export:
                for line in store.iter_jsonl(runner.events.run_id):
                    export.write(f"{line}\n")
        store.close()
        if output is not sys.stdout:
            output.close()
    return 0


def _parse_character_assignments(values: Sequence[str]) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for value in values:
        slot_id, separator, character_id = value.partition("=")
        slot_id = slot_id.strip()
        character_id = character_id.strip()
        if not separator or not slot_id or not character_id:
            raise ValueError(
                "--character must use SLOT_ID=CHARACTER_ID"
            )
        if slot_id in assignments:
            raise ValueError(f"duplicate character assignment for {slot_id}")
        assignments[slot_id] = character_id
    return assignments
if __name__ == "__main__":
    raise SystemExit(main())
