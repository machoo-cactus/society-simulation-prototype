import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from stage0_sim.application.scenario import CityWorldDefinition, ScenarioDefinition
from stage0_sim.domain.events import JsonValue

SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
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

type ScenarioWorldKind = Literal["none", "grid", "city"]


class ScenarioLibraryError(ValueError):
    pass


class ScenarioNotFoundError(ScenarioLibraryError):
    pass


class ScenarioConflictError(ScenarioLibraryError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    id: str
    name: str
    schema_version: int
    world_kind: ScenarioWorldKind
    entity_count: int
    content_hash: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "name": self.name,
            "schema_version": self.schema_version,
            "world_kind": self.world_kind,
            "entity_count": self.entity_count,
            "content_hash": self.content_hash,
        }


class ScenarioLibrary(Protocol):
    def list(self) -> tuple[ScenarioSummary, ...]: ...

    def get(self, scenario_id: str) -> ScenarioDefinition: ...

    def create(
        self,
        scenario_id: str,
        scenario: ScenarioDefinition,
    ) -> ScenarioDefinition: ...

    def update(
        self,
        scenario_id: str,
        scenario: ScenarioDefinition,
        expected_hash: str,
    ) -> ScenarioDefinition: ...

    def rename(
        self,
        scenario_id: str,
        new_id: str,
        expected_hash: str,
    ) -> ScenarioDefinition: ...

    def delete(
        self,
        scenario_id: str,
        expected_hash: str,
    ) -> ScenarioDefinition: ...


def validate_scenario_id(scenario_id: str) -> str:
    if not SCENARIO_ID_PATTERN.fullmatch(scenario_id):
        raise ScenarioLibraryError(
            "scenario ID must use lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )
    reserved_base = scenario_id.split(".", maxsplit=1)[0].casefold()
    if reserved_base in WINDOWS_RESERVED_NAMES:
        raise ScenarioLibraryError(f"reserved scenario ID: {scenario_id}")
    return scenario_id


def scenario_content_hash(scenario: ScenarioDefinition) -> str:
    canonical = json.dumps(
        scenario.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scenario_world_kind(scenario: ScenarioDefinition) -> ScenarioWorldKind:
    if scenario.world is None:
        return "none"
    if isinstance(scenario.world, CityWorldDefinition):
        return "city"
    return "grid"


def scenario_summary(
    scenario_id: str,
    scenario: ScenarioDefinition,
) -> ScenarioSummary:
    return ScenarioSummary(
        id=scenario_id,
        name=scenario.name,
        schema_version=scenario.schema_version,
        world_kind=scenario_world_kind(scenario),
        entity_count=len(scenario.entities),
        content_hash=scenario_content_hash(scenario),
    )
