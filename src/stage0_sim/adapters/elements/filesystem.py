from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from stage0_sim.application.element_library import (
    ElementConflictError,
    ElementDependencyError,
    ElementLibraryError,
    ElementNotFoundError,
    ElementSummary,
    element_summary,
    validate_element_resource_id,
)
from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    ElementKind,
    ScenarioElementDefinition,
    element_content_hash,
)
from stage0_sim.application.migrations.constants import ELEMENT_SCHEMA_VERSION


class FileSystemElementLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(
        self,
        kind: ElementKind | None = None,
    ) -> tuple[ElementSummary, ...]:
        summaries = tuple(
            element_summary(self._read(path.stem))
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name)
            if not path.name.startswith(".")
        )
        if kind is None:
            return summaries
        return tuple(item for item in summaries if item.kind is kind)

    def get(
        self,
        element_id: str,
        expected_kind: ElementKind | None = None,
    ) -> ScenarioElementDefinition:
        element = self._read(element_id)
        if expected_kind is not None and element.kind != expected_kind:
            raise ElementLibraryError(
                f"element {element_id} has kind {element.kind}, "
                f"expected {expected_kind.value}"
            )
        return element

    def create(
        self,
        element: ScenarioElementDefinition,
    ) -> ScenarioElementDefinition:
        path = self._path(element.id)
        if path.exists():
            raise ElementConflictError(f"element already exists: {element.id}")
        self._write(element.id, element)
        return self._read(element.id)

    def update(
        self,
        element_id: str,
        element: ScenarioElementDefinition,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        if element.id != element_id:
            raise ElementLibraryError(
                "updated element ID must match the resource ID"
            )
        current = self._read(element_id)
        self._require_hash(element_id, current, expected_hash)
        self._write(element_id, element)
        return self._read(element_id)

    def rename(
        self,
        element_id: str,
        new_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        current = self._read(element_id)
        self._require_hash(element_id, current, expected_hash)
        self._require_no_dependents(element_id)
        destination = self._path(new_id)
        if destination.exists():
            raise ElementConflictError(f"element already exists: {new_id}")
        renamed = current.model_copy(update={"id": new_id})
        temporary = self._temporary_path(new_id)
        try:
            self._write_to_path(temporary, renamed)
            os.replace(temporary, destination)
            self._path(element_id).unlink()
        except OSError as error:
            raise ElementLibraryError(
                f"could not rename element {element_id} to {new_id}: {error}"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return self._read(new_id)

    def delete(
        self,
        element_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        current = self._read(element_id)
        self._require_hash(element_id, current, expected_hash)
        self._require_no_dependents(element_id)
        try:
            self._path(element_id).unlink()
        except OSError as error:
            raise ElementLibraryError(
                f"could not delete element {element_id}: {error}"
            ) from error
        return current

    def dependents(self, element_id: str) -> tuple[ElementSummary, ...]:
        validate_element_resource_id(element_id)
        return tuple(
            summary
            for summary in self.list()
            if element_id in summary.dependency_ids
        )

    def _require_no_dependents(self, element_id: str) -> None:
        dependents = self.dependents(element_id)
        if dependents:
            references = ", ".join(
                f"{item.kind.value}:{item.id}" for item in dependents
            )
            raise ElementDependencyError(
                f"element {element_id} is referenced by {references}"
            )

    def _path(self, element_id: str) -> Path:
        validate_element_resource_id(element_id)
        path = (self.root / f"{element_id}.json").resolve()
        if path.parent != self.root:
            raise ElementLibraryError("element path escapes library root")
        return path

    def _temporary_path(self, element_id: str) -> Path:
        return self.root / f".{element_id}.{uuid4().hex}.tmp"

    def _read(self, element_id: str) -> ScenarioElementDefinition:
        path = self._path(element_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ElementNotFoundError(
                f"unknown element: {element_id}"
            ) from error
        except OSError as error:
            raise ElementLibraryError(
                f"could not read element {element_id}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise ElementLibraryError(
                f"element {element_id} is not valid JSON: {error}"
            ) from error
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != ELEMENT_SCHEMA_VERSION
        ):
            raise ElementLibraryError(
                f"element {element_id} requires schema version "
                f"{ELEMENT_SCHEMA_VERSION}; run 'stage0-sim migrate content'"
            )
        try:
            element = SCENARIO_ELEMENT_ADAPTER.validate_python(raw)
        except ValidationError as error:
            raise ElementLibraryError(
                f"element {element_id} validation failed: {error}"
            ) from error
        if element.id != element_id:
            raise ElementLibraryError(
                f"element file {element_id}.json declares ID {element.id}"
            )
        return element

    def _write(
        self,
        element_id: str,
        element: ScenarioElementDefinition,
    ) -> None:
        temporary = self._temporary_path(element_id)
        try:
            self._write_to_path(temporary, element)
            os.replace(temporary, self._path(element_id))
        except OSError as error:
            raise ElementLibraryError(
                f"could not write element {element_id}: {error}"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_to_path(
        path: Path,
        element: ScenarioElementDefinition,
    ) -> None:
        payload = json.dumps(
            element.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        path.write_text(f"{payload}\n", encoding="utf-8", newline="\n")

    @staticmethod
    def _require_hash(
        element_id: str,
        element: ScenarioElementDefinition,
        expected_hash: str,
    ) -> None:
        if element_content_hash(element) != expected_hash:
            raise ElementConflictError(
                f"element changed since it was loaded: {element_id}"
            )
