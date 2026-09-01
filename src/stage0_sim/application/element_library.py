from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from stage0_sim.application.elements import (
    BuildingElementDefinition,
    ElementKind,
    ObjectElementDefinition,
    RoomElementDefinition,
    ScenarioElementDefinition,
    element_content_hash,
)
from stage0_sim.domain.events import JsonValue

ELEMENT_RESOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


class ElementLibraryError(ValueError):
    pass


class ElementNotFoundError(ElementLibraryError):
    pass


class ElementConflictError(ElementLibraryError):
    pass


class ElementDependencyError(ElementLibraryError):
    pass


@dataclass(frozen=True, slots=True)
class ElementSummary:
    id: str
    name: str
    kind: ElementKind
    schema_version: int
    content_hash: str
    dependency_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "dependency_ids": list(self.dependency_ids),
        }


class ElementLibrary(Protocol):
    def list(
        self,
        kind: ElementKind | None = None,
    ) -> tuple[ElementSummary, ...]: ...

    def get(
        self,
        element_id: str,
        expected_kind: ElementKind | None = None,
    ) -> ScenarioElementDefinition: ...

    def create(
        self,
        element: ScenarioElementDefinition,
    ) -> ScenarioElementDefinition: ...

    def update(
        self,
        element_id: str,
        element: ScenarioElementDefinition,
        expected_hash: str,
    ) -> ScenarioElementDefinition: ...

    def rename(
        self,
        element_id: str,
        new_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition: ...

    def delete(
        self,
        element_id: str,
        expected_hash: str,
    ) -> ScenarioElementDefinition: ...

    def dependents(self, element_id: str) -> tuple[ElementSummary, ...]: ...


def validate_element_resource_id(element_id: str) -> str:
    if not ELEMENT_RESOURCE_ID_PATTERN.fullmatch(element_id):
        raise ElementLibraryError(
            "element ID must use lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )
    reserved_base = element_id.split(".", maxsplit=1)[0].casefold()
    if reserved_base in WINDOWS_RESERVED_NAMES:
        raise ElementLibraryError(f"reserved element ID: {element_id}")
    return element_id


def element_dependency_ids(
    element: ScenarioElementDefinition,
) -> tuple[str, ...]:
    dependency_ids: set[str] = set()
    if isinstance(element, BuildingElementDefinition):
        dependency_ids.update(room.element.id for room in element.rooms)
    elif isinstance(element, RoomElementDefinition):
        dependency_ids.update(item.element.id for item in element.objects)
    elif (
        isinstance(element, ObjectElementDefinition)
        and element.npc_role is not None
    ):
        dependency_ids.add(element.npc_role.id)
    return tuple(sorted(dependency_ids))


def element_summary(
    element: ScenarioElementDefinition,
) -> ElementSummary:
    return ElementSummary(
        id=element.id,
        name=element.name,
        kind=ElementKind(element.kind),
        schema_version=element.schema_version,
        content_hash=element_content_hash(element),
        dependency_ids=element_dependency_ids(element),
    )
