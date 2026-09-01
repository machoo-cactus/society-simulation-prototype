import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from stage0_sim.application.elements import ScenarioSourceDefinition
from stage0_sim.application.scenarios import (
    ScenarioConflictError,
    ScenarioLibraryError,
    ScenarioNotFoundError,
    ScenarioSummary,
    scenario_content_hash,
    scenario_summary,
    validate_scenario_id,
)


class FileSystemScenarioLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> tuple[ScenarioSummary, ...]:
        return tuple(
            scenario_summary(path.stem, self._read(path.stem))
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name)
            if not path.name.startswith(".")
        )

    def get(self, scenario_id: str) -> ScenarioSourceDefinition:
        return self._read(scenario_id)

    def create(
        self,
        scenario_id: str,
        scenario: ScenarioSourceDefinition,
    ) -> ScenarioSourceDefinition:
        path = self._path(scenario_id)
        if path.exists():
            raise ScenarioConflictError(
                f"scenario already exists: {scenario_id}"
            )
        self._write(scenario_id, scenario)
        return self._read(scenario_id)

    def update(
        self,
        scenario_id: str,
        scenario: ScenarioSourceDefinition,
        expected_hash: str,
    ) -> ScenarioSourceDefinition:
        current = self._read(scenario_id)
        self._require_hash(scenario_id, current, expected_hash)
        self._write(scenario_id, scenario)
        return self._read(scenario_id)

    def rename(
        self,
        scenario_id: str,
        new_id: str,
        expected_hash: str,
    ) -> ScenarioSourceDefinition:
        current = self._read(scenario_id)
        self._require_hash(scenario_id, current, expected_hash)
        destination = self._path(new_id)
        if destination.exists():
            raise ScenarioConflictError(f"scenario already exists: {new_id}")
        try:
            os.replace(self._path(scenario_id), destination)
        except OSError as error:
            raise ScenarioLibraryError(
                f"could not rename scenario {scenario_id} to {new_id}: {error}"
            ) from error
        return self._read(new_id)

    def delete(
        self,
        scenario_id: str,
        expected_hash: str,
    ) -> ScenarioSourceDefinition:
        current = self._read(scenario_id)
        self._require_hash(scenario_id, current, expected_hash)
        try:
            self._path(scenario_id).unlink()
        except OSError as error:
            raise ScenarioLibraryError(
                f"could not delete scenario {scenario_id}: {error}"
            ) from error
        return current

    def _path(self, scenario_id: str) -> Path:
        validate_scenario_id(scenario_id)
        path = (self.root / f"{scenario_id}.json").resolve()
        if path.parent != self.root:
            raise ScenarioLibraryError("scenario path escapes library root")
        return path

    def _read(self, scenario_id: str) -> ScenarioSourceDefinition:
        path = self._path(scenario_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ScenarioNotFoundError(
                f"unknown scenario: {scenario_id}"
            ) from error
        except OSError as error:
            raise ScenarioLibraryError(
                f"could not read scenario {scenario_id}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise ScenarioLibraryError(
                f"scenario {scenario_id} is not valid JSON: {error}"
            ) from error
        if not isinstance(raw, dict) or raw.get("schema_version") != 4:
            raise ScenarioLibraryError(
                f"scenario {scenario_id} requires schema version 4"
            )
        try:
            return ScenarioSourceDefinition.model_validate(raw)
        except ValidationError as error:
            raise ScenarioLibraryError(
                f"scenario {scenario_id} validation failed: {error}"
            ) from error

    def _write(
        self,
        scenario_id: str,
        scenario: ScenarioSourceDefinition,
    ) -> None:
        path = self._path(scenario_id)
        temporary = self.root / f".{scenario_id}.{uuid4().hex}.tmp"
        payload = json.dumps(
            scenario.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            temporary.write_text(f"{payload}\n", encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        except OSError as error:
            raise ScenarioLibraryError(
                f"could not write scenario {scenario_id}: {error}"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _require_hash(
        scenario_id: str,
        scenario: ScenarioSourceDefinition,
        expected_hash: str,
    ) -> None:
        if scenario_content_hash(scenario) != expected_hash:
            raise ScenarioConflictError(
                f"scenario changed since it was loaded: {scenario_id}"
            )
