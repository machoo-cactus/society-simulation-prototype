import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.characters import (
    CharacterConflictError,
    CharacterDefinition,
    prepare_scenario,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.scenario import ScenarioLoadError, create_runner, load_scenario
from stage0_sim.config import create_model_client, get_settings
from stage0_sim.domain.events import DomainEvent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage0-sim")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run a JSON scenario")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--ticks", type=int, default=10)
    run_parser.add_argument("--speed", type=float)
    run_parser.add_argument("--realtime", action="store_true")
    run_parser.add_argument("--full-events", action="store_true")
    run_parser.add_argument("--output", type=Path)
    run_parser.add_argument("--database", type=Path)
    run_parser.add_argument("--export", type=Path)
    run_parser.add_argument("--characters-dir", type=Path)
    characters_parser = subparsers.add_parser(
        "characters",
        help="manage reusable character files",
    )
    character_subparsers = characters_parser.add_subparsers(
        dest="characters_command",
        required=True,
    )
    extract_parser = character_subparsers.add_parser(
        "extract",
        help="extract an inline scenario profile catalog",
    )
    extract_parser.add_argument("scenario", type=Path)
    extract_parser.add_argument("--directory", type=Path)
    extract_parser.add_argument("--output", type=Path)
    extract_parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "characters":
        return extract_characters(args)
    if args.command != "run":
        return 2
    if args.ticks < 0:
        print("--ticks must not be negative", file=sys.stderr)
        return 2

    try:
        scenario = load_scenario(args.scenario)
        settings = get_settings()
        character_directory = args.characters_dir or settings.character_directory
        prepared = prepare_scenario(
            scenario,
            FileSystemCharacterLibrary(character_directory),
        )
        runner = create_runner(
            scenario,
            resolved_characters={
                character_id: character.profile()
                for character_id, character in prepared.characters.items()
            },
            speed=args.speed,
            model_client=create_model_client(settings),
            model_max_output_tokens=settings.llm_max_output_tokens,
            model_max_concurrency=settings.llm_max_concurrency,
        )
        database_path = args.database or (
            Path("data/runs") / f"{runner.events.run_id}.sqlite3"
        )
        store = SQLiteDatasetStore(database_path)
        collector = RunDataCollector(
            store=store,
            runner=runner,
            scenario=prepared.dataset_payload(),
        )
    except (ScenarioLoadError, ValueError) as error:
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


def extract_characters(args: argparse.Namespace) -> int:
    try:
        scenario = load_scenario(args.scenario)
        settings = get_settings()
        directory = args.directory or settings.character_directory
        library = FileSystemCharacterLibrary(directory)
        migrated = scenario.model_dump(mode="json")
        profiles = migrated.pop("character_profiles", {})
        if not profiles:
            print("scenario has no inline character profiles", file=sys.stderr)
            return 2
        actions: list[str] = []
        for profile_id, profile in profiles.items():
            character = CharacterDefinition.model_validate(
                {
                    "schema_version": 1,
                    "id": profile_id,
                    **profile,
                }
            )
            try:
                existing = library.get(profile_id)
            except ValueError:
                existing = None
            if existing is not None:
                if existing.model_dump(mode="json") != character.model_dump(
                    mode="json"
                ):
                    raise CharacterConflictError(
                        f"character already exists with different content: "
                        f"{profile_id}"
                    )
                actions.append(f"reuse {directory / f'{profile_id}.json'}")
            else:
                actions.append(f"create {directory / f'{profile_id}.json'}")
                if args.write:
                    library.create(character)
        for entity in migrated.get("entities", []):
            profile = entity.get("components", {}).get("character_profile")
            if not isinstance(profile, dict):
                continue
            reference = profile.pop("profile_ref", None)
            if isinstance(reference, str):
                profile["character_id"] = reference
        output = args.output or args.scenario.with_name(
            f"{args.scenario.stem}.characters.json"
        )
        for action in actions:
            print(action)
        print(f"{'write' if args.write else 'would write'} {output}")
        if args.write:
            output.write_text(
                f"{json.dumps(migrated, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
                newline="\n",
            )
        return 0
    except (ScenarioLoadError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
