import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.collection import RunDataCollector
from stage0_sim.application.scenario import ScenarioLoadError, create_runner, load_scenario
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 2
    if args.ticks < 0:
        print("--ticks must not be negative", file=sys.stderr)
        return 2

    try:
        scenario = load_scenario(args.scenario)
        runner = create_runner(scenario, speed=args.speed)
        database_path = args.database or (
            Path("data/runs") / f"{runner.events.run_id}.sqlite3"
        )
        store = SQLiteDatasetStore(database_path)
        collector = RunDataCollector(
            store=store,
            runner=runner,
            scenario=scenario.model_dump(mode="json"),
        )
    except (ScenarioLoadError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    output = args.output.open("w", encoding="utf-8") if args.output else sys.stdout

    def write_event(event: DomainEvent) -> None:
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
        if args.export:
            args.export.parent.mkdir(parents=True, exist_ok=True)
            with args.export.open("w", encoding="utf-8", newline="\n") as export:
                for line in store.iter_jsonl(runner.events.run_id):
                    export.write(f"{line}\n")
        store.close()
        if output is not sys.stdout:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
