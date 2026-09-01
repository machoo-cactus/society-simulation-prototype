import argparse
import asyncio
import copy
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
    CharacterConflictError,
    CharacterDefinition,
    PreparedScenario,
    prepare_scenario,
)
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    ScenarioLoadError,
    create_runner,
)
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    load_and_resolve_scenario,
)
from stage0_sim.config import create_model_client, get_settings
from stage0_sim.domain.events import DomainEvent
from stage0_sim.domain.npcs import NpcControlMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage0-sim")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run a JSON scenario")
    run_parser.add_argument("scenario", type=Path)
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
        settings = get_settings()
        resolved = load_and_resolve_scenario(
            args.scenario,
            FileSystemElementLibrary(
                args.elements_dir or settings.element_directory
            ),
        )
        scenario = resolved.scenario
        scenario_source = resolved.source.model_dump(mode="json")
        resolved_elements = resolved.provenance_payload()
        character_directory = args.characters_dir or settings.character_directory
        assignments = _parse_character_assignments(args.character)
        prepared = prepare_scenario(
            scenario,
            FileSystemCharacterLibrary(character_directory),
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
            Path("data/runs") / f"{runner.events.run_id}.sqlite3"
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


def extract_characters(args: argparse.Namespace) -> int:
    try:
        raw = json.loads(args.scenario.read_text(encoding="utf-8"))
        settings = get_settings()
        directory = args.directory or settings.character_directory
        library = FileSystemCharacterLibrary(directory)
        migrated, extracted = _migrate_legacy_scenario(raw)
        actions: list[str] = []
        for profile_id, character in extracted.items():
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
        ScenarioDefinition.model_validate(migrated)
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
    except (OSError, json.JSONDecodeError, ScenarioLoadError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _migrate_legacy_scenario(
    raw: dict[str, object],
) -> tuple[dict[str, object], dict[str, CharacterDefinition]]:
    migrated = copy.deepcopy(raw)
    migrated["schema_version"] = 2
    catalog = migrated.pop("character_profiles", {})
    migrated.pop("character_profile_templates", None)
    if not isinstance(catalog, dict):
        raise ValueError("character_profiles must be an object")
    extracted: dict[str, CharacterDefinition] = {}
    entities = migrated.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("entities must be an array")
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        components = entity.get("components")
        if not isinstance(entity_id, str) or not isinstance(components, dict):
            continue
        raw_profile = components.pop("character_profile", None)
        if raw_profile is None:
            metadata = components.get("metadata", {})
            label = (
                str(metadata.get("display_name", entity_id))
                if isinstance(metadata, dict)
                else entity_id
            )
            components["character_slot"] = {
                "label": label,
                "briefing": "",
                "default_character_id": None,
                "constraints": {},
            }
            continue
        if not isinstance(raw_profile, dict):
            raise ValueError(f"entity {entity_id} character_profile must be an object")
        character_id = raw_profile.get("character_id")
        profile_ref = raw_profile.get("profile_ref")
        profile_data: dict[str, object] | None = None
        if isinstance(character_id, str):
            selected_id = character_id
        elif isinstance(profile_ref, str):
            selected_id = profile_ref
            catalog_profile = catalog.get(profile_ref)
            if isinstance(catalog_profile, dict):
                profile_data = copy.deepcopy(catalog_profile)
        else:
            selected_id = entity_id
            profile_data = copy.deepcopy(raw_profile)
        planner = components.setdefault("planner", {})
        if not isinstance(planner, dict):
            raise ValueError(f"entity {entity_id} planner must be an object")
        label = entity_id
        if profile_data is not None:
            _normalize_legacy_character_profile(profile_data)
            identity = profile_data.get("identity")
            if isinstance(identity, dict):
                display_name = identity.get("display_name")
                if isinstance(display_name, str) and display_name:
                    label = display_name
            motivations = profile_data.get("motivations")
            if isinstance(motivations, dict):
                goals = motivations.pop("goals", None)
                priorities = motivations.pop("current_priorities", None)
                if isinstance(goals, list) and goals:
                    planner.setdefault("daily_goals", goals)
                if isinstance(priorities, list) and priorities:
                    planner.setdefault("current_priorities", priorities)
            legacy_goals = profile_data.pop("goals", None)
            if isinstance(legacy_goals, list) and legacy_goals:
                planner.setdefault("daily_goals", legacy_goals)
            character = CharacterDefinition.model_validate(
                {
                    "schema_version": 1,
                    "id": selected_id,
                    **profile_data,
                }
            )
            existing = extracted.get(selected_id)
            if existing is not None and existing != character:
                raise CharacterConflictError(
                    f"legacy profiles define different content for {selected_id}"
                )
            extracted[selected_id] = character
        components["character_slot"] = {
            "label": label,
            "briefing": "",
            "default_character_id": selected_id,
            "constraints": {},
        }
    return migrated, extracted


def _normalize_legacy_character_profile(profile: dict[str, object]) -> None:
    display_name = profile.pop("display_name", None)
    role = profile.pop("role", None)
    if display_name is not None or role is not None:
        identity = profile.setdefault("identity", {})
        if not isinstance(identity, dict):
            raise ValueError("legacy character identity must be an object")
        if isinstance(display_name, str):
            identity.setdefault("display_name", display_name)
        if isinstance(role, str):
            identity.setdefault("occupation", role)
    traits = profile.pop("traits", None)
    if isinstance(traits, list):
        personality = profile.setdefault("personality", {})
        if not isinstance(personality, dict):
            raise ValueError("legacy character personality must be an object")
        personality.setdefault("traits", traits)
    values = profile.pop("values", None)
    if isinstance(values, list):
        motivations = profile.setdefault("motivations", {})
        if not isinstance(motivations, dict):
            raise ValueError("legacy character motivations must be an object")
        motivations.setdefault("values", values)


if __name__ == "__main__":
    raise SystemExit(main())
