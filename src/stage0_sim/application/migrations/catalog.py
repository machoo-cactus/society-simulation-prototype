from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from stage0_sim.application.element_library import ElementLibrary, ElementSummary
from stage0_sim.application.elements import (
    SCENARIO_ELEMENT_ADAPTER,
    ElementKind,
    ScenarioElementDefinition,
    ScenarioSourceDefinition,
    element_content_hash,
)
from stage0_sim.application.migrations.models import (
    MigrationContext,
    MigrationResult,
    ResourceKind,
)
from stage0_sim.application.migrations.registry import (
    CONTENT_MIGRATION_REGISTRY,
)
from stage0_sim.application.scenario_resolution import (
    ScenarioResolutionError,
    resolve_scenario,
)

CatalogMode = Literal["check", "output", "write"]


class CatalogMigrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogMigrationOptions:
    characters_dir: Path | None = None
    elements_dir: Path | None = None
    scenarios_dir: Path | None = None
    mode: CatalogMode = "check"
    output_dir: Path | None = None
    backup_dir: Path | None = None
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CatalogManifestEntry:
    resource_kind: ResourceKind
    resource_id: str
    source_path: str
    output_path: str
    from_version: int
    to_version: int
    changed: bool
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "resource_kind": self.resource_kind.value,
            "resource_id": self.resource_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changed": self.changed,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class CatalogMigrationReport:
    mode: CatalogMode
    results: tuple[MigrationResult, ...]
    manifest: tuple[CatalogManifestEntry, ...]
    errors: tuple[str, ...] = ()
    output_dir: str | None = None
    backup_dir: str | None = None

    @property
    def succeeded(self) -> bool:
        return not self.errors and all(result.succeeded for result in self.results)

    @property
    def changed_count(self) -> int:
        return sum(entry.changed for entry in self.manifest)

    def to_payload(self) -> dict[str, object]:
        counts = {
            kind.value: sum(
                entry.resource_kind is kind for entry in self.manifest
            )
            for kind in ResourceKind
        }
        return {
            "mode": self.mode,
            "succeeded": self.succeeded,
            "changed_count": self.changed_count,
            "counts": counts,
            "output_dir": self.output_dir,
            "backup_dir": self.backup_dir,
            "errors": list(self.errors),
            "results": [
                result.model_dump(mode="json") for result in self.results
            ],
            "manifest": [entry.to_payload() for entry in self.manifest],
        }


@dataclass(frozen=True, slots=True)
class _RawResource:
    kind: ResourceKind
    resource_id: str
    path: Path
    raw: dict[str, Any]
    original_text: str


@dataclass(frozen=True, slots=True)
class _MigratedResource:
    source: _RawResource
    result: MigrationResult
    text: str


def migrate_catalog(
    options: CatalogMigrationOptions,
) -> CatalogMigrationReport:
    try:
        sources = _validate_options(options)
        raw_resources = _read_catalogs(sources)
        migrated = _migrate_all(raw_resources)
        _validate_complete_catalog(migrated)
        manifest = _build_manifest(migrated, options)
        if options.mode == "output":
            _write_output(migrated, cast(Path, options.output_dir))
        elif options.mode == "write":
            _write_in_place(
                migrated,
                sources,
                cast(Path, options.backup_dir),
            )
        report = CatalogMigrationReport(
            mode=options.mode,
            results=tuple(item.result for item in migrated),
            manifest=manifest,
            output_dir=(
                str(options.output_dir.resolve())
                if options.output_dir is not None
                else None
            ),
            backup_dir=(
                str(options.backup_dir.resolve())
                if options.backup_dir is not None
                else None
            ),
        )
    except CatalogMigrationError as error:
        report = CatalogMigrationReport(
            mode=options.mode,
            results=(),
            manifest=(),
            errors=(str(error),),
            output_dir=(
                str(options.output_dir.resolve())
                if options.output_dir is not None
                else None
            ),
            backup_dir=(
                str(options.backup_dir.resolve())
                if options.backup_dir is not None
                else None
            ),
        )
    if options.report_path is not None:
        _write_report(options.report_path, report)
    return report


def _validate_options(
    options: CatalogMigrationOptions,
) -> dict[ResourceKind, Path]:
    if options.mode not in {"check", "output", "write"}:
        raise CatalogMigrationError(f"unknown migration mode: {options.mode}")
    sources = {
        kind: path.resolve()
        for kind, path in (
            (ResourceKind.CHARACTER, options.characters_dir),
            (ResourceKind.ELEMENT, options.elements_dir),
            (ResourceKind.SCENARIO, options.scenarios_dir),
        )
        if path is not None
    }
    if not sources:
        raise CatalogMigrationError("at least one catalog directory is required")
    for kind, path in sources.items():
        if not path.is_dir():
            raise CatalogMigrationError(
                f"{kind.value} directory does not exist: {path}"
            )
    if options.mode == "check":
        if options.output_dir is not None or options.backup_dir is not None:
            raise CatalogMigrationError(
                "check mode cannot use output or backup directories"
            )
    elif options.mode == "output":
        if options.output_dir is None:
            raise CatalogMigrationError("output mode requires --output")
        if options.backup_dir is not None:
            raise CatalogMigrationError(
                "output mode cannot use a backup directory"
            )
        _require_safe_destination(options.output_dir, sources, "output")
    else:
        if options.output_dir is not None:
            raise CatalogMigrationError("write mode cannot use --output")
        if options.backup_dir is None:
            raise CatalogMigrationError("write mode requires --backup-dir")
        _require_safe_destination(options.backup_dir, sources, "backup")
    return sources


def _require_safe_destination(
    destination: Path,
    sources: Mapping[ResourceKind, Path],
    label: str,
) -> None:
    resolved = destination.resolve()
    if resolved.exists():
        raise CatalogMigrationError(
            f"{label} directory already exists: {resolved}"
        )
    for source in sources.values():
        if (
            resolved == source
            or resolved.is_relative_to(source)
            or source.is_relative_to(resolved)
        ):
            raise CatalogMigrationError(
                f"{label} directory collides with source directory {source}"
            )


def _read_catalogs(
    sources: Mapping[ResourceKind, Path],
) -> tuple[_RawResource, ...]:
    resources: list[_RawResource] = []
    seen: dict[tuple[ResourceKind, str], Path] = {}
    roots: dict[Path, set[ResourceKind]] = {}
    for kind, root in sources.items():
        roots.setdefault(root, set()).add(kind)
    for root, allowed_kinds in sorted(
        roots.items(),
        key=lambda item: str(item[0]),
    ):
        for path in sorted(root.glob("*.json"), key=lambda item: item.name):
            if path.name.startswith("."):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                raw = json.loads(text)
            except OSError as error:
                raise CatalogMigrationError(
                    f"could not read {kind.value} file {path}: {error}"
                ) from error
            except json.JSONDecodeError as error:
                raise CatalogMigrationError(
                    f"{kind.value} file {path} is malformed JSON: {error}"
                ) from error
            if not isinstance(raw, dict):
                raise CatalogMigrationError(
                    f"content file {path} must contain a JSON object"
                )
            kind = _classify_resource(raw)
            if kind not in allowed_kinds:
                if allowed_kinds == {ResourceKind.SCENARIO}:
                    continue
                expected = sorted(item.value for item in allowed_kinds)
                raise CatalogMigrationError(
                    f"content file {path} is a {kind.value}, but its directory "
                    f"was configured for {expected}"
                )
            resource_id = path.stem
            if kind is not ResourceKind.SCENARIO:
                declared_id = raw.get("id")
                if not isinstance(declared_id, str):
                    raise CatalogMigrationError(
                        f"{kind.value} file {path} requires a string id"
                    )
                if declared_id != resource_id:
                    raise CatalogMigrationError(
                        f"{kind.value} filename/ID mismatch: {path.name} "
                        f"declares {declared_id!r}"
                    )
                resource_id = declared_id
            key = (kind, resource_id.casefold())
            if key in seen:
                raise CatalogMigrationError(
                    f"duplicate {kind.value} ID {resource_id!r}: "
                    f"{seen[key]} and {path}"
                )
            seen[key] = path
            resources.append(
                _RawResource(kind, resource_id, path, raw, text)
            )
    return tuple(resources)


def _classify_resource(raw: dict[str, Any]) -> ResourceKind:
    if raw.get("kind") in {"npc_role", "object", "room", "building"}:
        return ResourceKind.ELEMENT
    if "id" in raw and (
        "identity" in raw or raw.get("template_id") == "human-v1"
    ):
        return ResourceKind.CHARACTER
    return ResourceKind.SCENARIO


def _migrate_all(
    resources: tuple[_RawResource, ...],
) -> tuple[_MigratedResource, ...]:
    migrated: list[_MigratedResource] = []
    context = MigrationContext()
    characters = sorted(
        (
            item
            for item in resources
            if item.kind is ResourceKind.CHARACTER
        ),
        key=lambda item: item.resource_id,
    )
    for resource in characters:
        migrated.append(_migrate_one(resource, context))

    elements = {
        item.resource_id: item
        for item in resources
        if item.kind is ResourceKind.ELEMENT
    }
    dependencies = {
        element_id: _element_dependencies(resource.raw)
        for element_id, resource in elements.items()
    }
    for element_id, required in dependencies.items():
        missing = sorted(required - elements.keys())
        if missing:
            raise CatalogMigrationError(
                f"element {element_id} references missing dependencies: {missing}"
            )
    pending = set(elements)
    while pending:
        ready = sorted(
            element_id
            for element_id in pending
            if dependencies[element_id] <= context.element_definitions.keys()
        )
        if not ready:
            details = {
                element_id: sorted(
                    dependencies[element_id] - context.element_definitions.keys()
                )
                for element_id in sorted(pending)
            }
            raise CatalogMigrationError(
                f"element dependency graph is cyclic or incomplete: {details}"
            )
        for element_id in ready:
            resource = elements[element_id]
            rewritten, changed_paths = _rewrite_hashes(
                resource.raw,
                context.element_hashes,
            )
            rewritten_resource = _RawResource(
                resource.kind,
                resource.resource_id,
                resource.path,
                rewritten,
                resource.original_text,
            )
            item = _migrate_one(rewritten_resource, context)
            if changed_paths:
                item = _add_result_changes(item, changed_paths)
            canonical = cast(dict[str, Any], item.result.canonical_json)
            element = SCENARIO_ELEMENT_ADAPTER.validate_python(canonical)
            context.element_definitions[element_id] = canonical
            context.element_hashes[element_id] = element_content_hash(element)
            migrated.append(item)
            pending.remove(element_id)

    scenarios = sorted(
        (
            item
            for item in resources
            if item.kind is ResourceKind.SCENARIO
        ),
        key=lambda item: item.resource_id,
    )
    for resource in scenarios:
        rewritten, changed_paths = _rewrite_hashes(
            resource.raw,
            context.element_hashes,
        )
        rewritten_resource = _RawResource(
            resource.kind,
            resource.resource_id,
            resource.path,
            rewritten,
            resource.original_text,
        )
        item = _migrate_one(rewritten_resource, context)
        if changed_paths:
            item = _add_result_changes(item, changed_paths)
        migrated.append(item)
    return tuple(
        sorted(
            migrated,
            key=lambda item: (
                list(ResourceKind).index(item.source.kind),
                item.source.resource_id,
            ),
        )
    )


def _migrate_one(
    resource: _RawResource,
    context: MigrationContext,
) -> _MigratedResource:
    result = CONTENT_MIGRATION_REGISTRY.migrate(
        resource.kind,
        resource.resource_id,
        resource.raw,
        context,
    )
    if not result.succeeded:
        raise CatalogMigrationError(
            f"{resource.kind.value} {resource.resource_id} migration failed: "
            + "; ".join(result.errors)
        )
    text = _canonical_text(cast(dict[str, Any], result.canonical_json))
    if text != resource.original_text and not result.changed_paths:
        result = result.model_copy(
            update={"changed_paths": ["$ (canonical formatting)"]}
        )
    return _MigratedResource(resource, result, text)


def _add_result_changes(
    item: _MigratedResource,
    changed_paths: tuple[str, ...],
) -> _MigratedResource:
    result = item.result.model_copy(
        update={
            "changed_paths": sorted(
                set(item.result.changed_paths) | set(changed_paths)
            ),
            "warnings": sorted(
                set(item.result.warnings)
                | {"rewrote stale element content hashes"}
            ),
        }
    )
    return _MigratedResource(
        item.source,
        result,
        _canonical_text(cast(dict[str, Any], result.canonical_json)),
    )


def _element_dependencies(raw: dict[str, Any]) -> set[str]:
    dependencies: set[str] = set()
    _collect_element_references(raw, dependencies)
    dependencies.discard(cast(str, raw.get("id")))
    return dependencies


def _collect_element_references(value: Any, dependencies: set[str]) -> None:
    if isinstance(value, dict):
        if {"kind", "id", "content_hash"} <= value.keys():
            element_id = value.get("id")
            if isinstance(element_id, str):
                dependencies.add(element_id)
        for child in value.values():
            _collect_element_references(child, dependencies)
    elif isinstance(value, list):
        for child in value:
            _collect_element_references(child, dependencies)


def _rewrite_hashes(
    raw: dict[str, Any],
    hashes: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = json.loads(json.dumps(raw))
    changed: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if {"kind", "id", "content_hash"} <= value.keys():
                element_id = value.get("id")
                if not isinstance(element_id, str):
                    raise CatalogMigrationError(
                        f"{path}.id must be a string"
                    )
                expected = hashes.get(element_id)
                if expected is None:
                    raise CatalogMigrationError(
                        f"{path} references missing or unordered element "
                        f"{element_id!r}"
                    )
                if value.get("content_hash") != expected:
                    value["content_hash"] = expected
                    changed.append(f"{path}.content_hash")
            for key in sorted(value):
                visit(value[key], f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(payload, "$")
    return payload, tuple(changed)


def _validate_complete_catalog(
    migrated: tuple[_MigratedResource, ...],
) -> None:
    character_ids = {
        item.source.resource_id
        for item in migrated
        if item.source.kind is ResourceKind.CHARACTER
    }
    elements: dict[str, ScenarioElementDefinition] = {}
    for item in migrated:
        if item.source.kind is ResourceKind.ELEMENT:
            elements[item.source.resource_id] = (
                SCENARIO_ELEMENT_ADAPTER.validate_python(
                    item.result.canonical_json
                )
            )
    library = _MemoryElementLibrary(elements)
    for element_id, element in sorted(elements.items()):
        raw = element.model_dump(mode="json")
        for reference in _reference_records(raw):
            referenced_id = cast(str, reference["id"])
            referenced = elements.get(referenced_id)
            if referenced is None:
                raise CatalogMigrationError(
                    f"element {element_id} references missing element "
                    f"{referenced_id}"
                )
            actual_hash = element_content_hash(referenced)
            if reference["content_hash"] != actual_hash:
                raise CatalogMigrationError(
                    f"element {element_id} contains stale hash for "
                    f"{referenced_id}"
                )
    for item in migrated:
        if item.source.kind is not ResourceKind.SCENARIO:
            continue
        source = ScenarioSourceDefinition.model_validate(
            item.result.canonical_json
        )
        if character_ids:
            for entity in source.entities:
                slot = entity.components.get("character_slot")
                if not isinstance(slot, dict):
                    continue
                character_id = slot.get("default_character_id")
                if (
                    isinstance(character_id, str)
                    and character_id not in character_ids
                ):
                    raise CatalogMigrationError(
                        f"scenario {item.source.resource_id} references missing "
                        f"character {character_id!r}"
                    )
        try:
            resolve_scenario(source, library)
        except ScenarioResolutionError as error:
            raise CatalogMigrationError(
                f"scenario {item.source.resource_id} does not resolve: {error}"
            ) from error


def _reference_records(value: Any) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"kind", "id", "content_hash"} <= value.keys():
            records.append(value)
        for child in value.values():
            records.extend(_reference_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(_reference_records(child))
    return tuple(records)


class _MemoryElementLibrary(ElementLibrary):
    def __init__(self, elements: Mapping[str, ScenarioElementDefinition]) -> None:
        self.elements = dict(elements)

    def list(
        self,
        kind: ElementKind | None = None,
    ) -> tuple[ElementSummary, ...]:
        del kind
        raise NotImplementedError

    def get(
        self,
        element_id: str,
        expected_kind: ElementKind | None = None,
    ) -> ScenarioElementDefinition:
        try:
            element = self.elements[element_id]
        except KeyError as error:
            raise CatalogMigrationError(
                f"missing element dependency: {element_id}"
            ) from error
        if expected_kind is not None and element.kind != expected_kind:
            raise CatalogMigrationError(
                f"element {element_id} has kind {element.kind}, "
                f"expected {expected_kind.value}"
            )
        return element

    def create(
        self,
        element: ScenarioElementDefinition,
    ) -> ScenarioElementDefinition:
        raise NotImplementedError

    def update(
        self,
        element_id: str,
        element: ScenarioElementDefinition,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        raise NotImplementedError

    def rename(
        self,
        element_id: str,
        new_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        raise NotImplementedError

    def delete(
        self,
        element_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition:
        raise NotImplementedError

    def dependents(self, element_id: str) -> tuple[ElementSummary, ...]:
        raise NotImplementedError


def _build_manifest(
    migrated: tuple[_MigratedResource, ...],
    options: CatalogMigrationOptions,
) -> tuple[CatalogManifestEntry, ...]:
    entries: list[CatalogManifestEntry] = []
    for item in migrated:
        if options.mode == "output":
            output_path = (
                cast(Path, options.output_dir)
                / f"{item.source.kind.value}s"
                / item.source.path.name
            )
        else:
            output_path = item.source.path
        from_version = item.result.from_version
        if from_version is None:
            raise CatalogMigrationError(
                f"{item.source.resource_id} has no source version"
            )
        entries.append(
            CatalogManifestEntry(
                resource_kind=item.source.kind,
                resource_id=item.source.resource_id,
                source_path=str(item.source.path.resolve()),
                output_path=str(output_path.resolve()),
                from_version=from_version,
                to_version=item.result.to_version,
                changed=item.text != item.source.original_text,
                sha256=hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(entries)


def _write_output(
    migrated: tuple[_MigratedResource, ...],
    output_dir: Path,
) -> None:
    destination = output_dir.resolve()
    staging = destination.with_name(
        f".{destination.name}.stage-{uuid4().hex}"
    )
    try:
        for item in migrated:
            path = staging / f"{item.source.kind.value}s" / item.source.path.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.text, encoding="utf-8", newline="\n")
        os.replace(staging, destination)
    except OSError as error:
        raise CatalogMigrationError(
            f"could not create migration output {destination}: {error}"
        ) from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _write_in_place(
    migrated: tuple[_MigratedResource, ...],
    sources: Mapping[ResourceKind, Path],
    backup_dir: Path,
) -> None:
    backup = backup_dir.resolve()
    backup_staging = backup.with_name(f".{backup.name}.stage-{uuid4().hex}")
    originals = {
        item.source.path: item.source.original_text for item in migrated
    }
    replaced: list[Path] = []
    try:
        for kind, source in sources.items():
            destination = backup_staging / f"{kind.value}s"
            destination.mkdir(parents=True, exist_ok=True)
            for path in sorted(source.glob("*.json"), key=lambda item: item.name):
                shutil.copy2(path, destination / path.name)
        os.replace(backup_staging, backup)
        for item in migrated:
            if item.text == item.source.original_text:
                continue
            temporary = item.source.path.with_name(
                f".{item.source.path.name}.{uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    item.text,
                    encoding="utf-8",
                    newline="\n",
                )
                os.replace(temporary, item.source.path)
                replaced.append(item.source.path)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as error:
        rollback_errors: list[str] = []
        for path in reversed(replaced):
            try:
                temporary = path.with_name(f".{path.name}.{uuid4().hex}.rollback")
                temporary.write_text(
                    originals[path],
                    encoding="utf-8",
                    newline="",
                )
                os.replace(temporary, path)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        details = (
            f"; rollback errors: {rollback_errors}" if rollback_errors else ""
        )
        raise CatalogMigrationError(
            f"could not write migrated catalog: {error}{details}"
        ) from error
    finally:
        if backup_staging.exists():
            shutil.rmtree(backup_staging)


def _write_report(path: Path, report: CatalogMigrationReport) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = _canonical_text(report.to_payload())
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    except OSError as error:
        raise CatalogMigrationError(
            f"could not write migration report {destination}: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_text(payload: object) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
