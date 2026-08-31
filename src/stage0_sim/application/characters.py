import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field, model_validator

from stage0_sim.application.scenario import (
    CharacterProfileDefinition,
    CharacterSlotDefinition,
    ResolvedCharacterProfile,
    ScenarioDefinition,
)
from stage0_sim.domain.events import JsonValue

CHARACTER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
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


class CharacterLibraryError(ValueError):
    pass


class CharacterNotFoundError(CharacterLibraryError):
    pass


class CharacterConflictError(CharacterLibraryError):
    pass


class CharacterDefinition(CharacterProfileDefinition):
    schema_version: int = Field(default=1, ge=1)
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def is_standalone_character(self) -> "CharacterDefinition":
        validate_character_id(self.id)
        if self.template_id != "human-v1":
            raise ValueError(
                f"unsupported character template: {self.template_id}"
            )
        return self

    def profile(self) -> CharacterProfileDefinition:
        return CharacterProfileDefinition.model_validate(
            self.model_dump(
                mode="python",
                exclude={"schema_version", "id"},
            )
        )


@dataclass(frozen=True, slots=True)
class CharacterSummary:
    id: str
    display_name: str
    template_id: str
    schema_version: int
    content_hash: str
    age: int | None
    gender: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "template_id": self.template_id,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "age": self.age,
            "gender": self.gender,
        }


class CharacterLibrary(Protocol):
    def list(self) -> tuple[CharacterSummary, ...]: ...

    def get(self, character_id: str) -> CharacterDefinition: ...

    def create(self, character: CharacterDefinition) -> CharacterDefinition: ...

    def update(
        self,
        character_id: str,
        character: CharacterDefinition,
        expected_hash: str,
    ) -> CharacterDefinition: ...

    def rename(
        self,
        character_id: str,
        new_id: str,
        expected_hash: str,
    ) -> CharacterDefinition: ...

    def delete(self, character_id: str, expected_hash: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedScenario:
    scenario: ScenarioDefinition
    assignments: Mapping[str, str]
    characters: Mapping[str, CharacterDefinition]

    def dataset_payload(self) -> dict[str, JsonValue]:
        scenario_payload = self.scenario.model_dump(mode="json")
        resolved: dict[str, JsonValue] = {}
        for character_id, character in sorted(self.characters.items()):
            resolved[character_id] = {
                "schema_version": character.schema_version,
                "template_id": character.template_id,
                "content_hash": character_content_hash(character),
                "data": character.model_dump(mode="json"),
            }
        scenario_payload["resolved_characters"] = resolved
        scenario_payload["character_assignments"] = dict(
            sorted(self.assignments.items())
        )
        return scenario_payload

    def runtime_characters(self) -> dict[str, ResolvedCharacterProfile]:
        return {
            entity_id: ResolvedCharacterProfile(
                character_id=character_id,
                profile=self.characters[character_id].profile(),
            )
            for entity_id, character_id in self.assignments.items()
        }

    def entity_summaries(self) -> tuple[dict[str, JsonValue], ...]:
        summaries: list[dict[str, JsonValue]] = []
        for entity in self.scenario.entities:
            character_id = self.assignments.get(entity.id)
            if character_id is None:
                continue
            character = self.characters[character_id]
            identity = character.identity
            if identity is None:
                continue
            summaries.append(
                {
                    "entity_id": entity.id,
                    "character_id": character_id,
                    "display_name": identity.display_name,
                    "content_hash": character_content_hash(character),
                }
            )
        return tuple(summaries)


def validate_character_id(character_id: str) -> str:
    if not CHARACTER_ID_PATTERN.fullmatch(character_id):
        raise CharacterLibraryError(
            "character ID must use lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )
    if character_id.casefold() in WINDOWS_RESERVED_NAMES:
        raise CharacterLibraryError(f"reserved character ID: {character_id}")
    return character_id


def character_content_hash(character: CharacterDefinition) -> str:
    canonical = json.dumps(
        character.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def character_summary(character: CharacterDefinition) -> CharacterSummary:
    identity = character.identity
    if identity is None:
        raise CharacterLibraryError(
            f"character {character.id} is missing identity"
        )
    return CharacterSummary(
        id=character.id,
        display_name=identity.display_name,
        template_id=character.template_id,
        schema_version=character.schema_version,
        content_hash=character_content_hash(character),
        age=identity.age,
        gender=identity.gender,
    )


def prepare_scenario(
    scenario: ScenarioDefinition,
    library: CharacterLibrary,
    assignments: Mapping[str, str] | None = None,
) -> PreparedScenario:
    scenario = scenario.model_copy(deep=True)
    requested = dict(assignments or {})
    slot_ids = {entity.id for entity in scenario.entities}
    unknown_assignments = sorted(set(requested) - slot_ids)
    if unknown_assignments:
        raise CharacterLibraryError(
            f"assignments reference unknown character slots: {unknown_assignments}"
        )
    effective: dict[str, str] = {}
    resolved: dict[str, CharacterDefinition] = {}
    for entity in scenario.entities:
        if "character_slot" not in entity.components:
            raise CharacterLibraryError(
                f"entity {entity.id} requires a character_slot component"
            )
        slot = CharacterSlotDefinition.model_validate(
            entity.components["character_slot"]
        )
        character_id = requested.get(entity.id, slot.default_character_id)
        if character_id is None:
            raise CharacterLibraryError(
                f"character slot {entity.id} has no assignment or default"
            )
        character = library.get(character_id)
        violations = character_constraint_violations(character, slot)
        if violations:
            raise CharacterLibraryError(
                f"character {character_id} is ineligible for slot {entity.id}: "
                + "; ".join(violations)
            )
        effective[entity.id] = character_id
        resolved[character_id] = character
    return PreparedScenario(
        scenario=scenario,
        assignments=effective,
        characters=resolved,
    )


def character_constraint_violations(
    character: CharacterDefinition,
    slot: CharacterSlotDefinition,
) -> tuple[str, ...]:
    constraints = slot.constraints
    identity = character.identity
    violations: list[str] = []
    if constraints.minimum_age is not None:
        if identity.age is None:
            violations.append("age is required by the slot")
        elif identity.age < constraints.minimum_age:
            violations.append(
                f"age {identity.age} is below minimum {constraints.minimum_age}"
            )
    if constraints.maximum_age is not None:
        if identity.age is None:
            violations.append("age is required by the slot")
        elif identity.age > constraints.maximum_age:
            violations.append(
                f"age {identity.age} exceeds maximum {constraints.maximum_age}"
            )
    allowed_genders = {
        value.casefold() for value in constraints.allowed_genders
    }
    if allowed_genders:
        if not identity.gender:
            violations.append("gender is required by the slot")
        elif identity.gender.casefold() not in allowed_genders:
            violations.append(
                f"gender {identity.gender!r} is not allowed"
            )
    if (
        constraints.allowed_template_ids
        and character.template_id not in constraints.allowed_template_ids
    ):
        violations.append(
            f"template {character.template_id!r} is not allowed"
        )
    return tuple(dict.fromkeys(violations))
