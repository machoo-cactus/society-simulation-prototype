import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Protocol

from pydantic import Field, model_validator

from stage0_sim.application.scenario import (
    CharacterProfileDefinition,
    CharacterSlotDefinition,
    ResolvedCharacterProfile,
    ResolvedCharacterSituation,
    ScenarioDefinition,
)
from stage0_sim.domain.events import JsonValue

if TYPE_CHECKING:
    from stage0_sim.application.character_synthesis import (
        CharacterSituationArtifact,
    )

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
    schema_version: int = Field(default=2, ge=1)
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def is_standalone_character(self) -> "CharacterDefinition":
        validate_character_id(self.id)
        if self.template_id != "human-v1":
            raise ValueError(
                f"unsupported character template: {self.template_id}"
            )
        if self.schema_version >= 2:
            if self.identity.age is not None:
                raise ValueError(
                    "character schema version 2 uses identity.birth_date; "
                    "identity.age is legacy version-1 data"
                )
            if self.appearance.height.strip():
                raise ValueError(
                    "character schema version 2 uses "
                    "body_measurements.height_cm; appearance.height is legacy"
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
    birth_date: date | None
    age: int | None
    gender: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "template_id": self.template_id,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "birth_date": (
                self.birth_date.isoformat()
                if self.birth_date is not None
                else None
            ),
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
    situations: Mapping[str, "CharacterSituationArtifact"]
    scenario_source: Mapping[str, JsonValue] | None = None
    resolved_elements: Mapping[str, JsonValue] = field(default_factory=dict)

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
        scenario_payload["resolved_character_situations"] = {
            entity_id: artifact.model_dump(
                mode="json",
                exclude={
                    "generation": {
                        "request",
                        "result",
                    }
                },
            )
            for entity_id, artifact in sorted(self.situations.items())
        }
        if self.scenario_source is not None:
            scenario_payload["source_scenario"] = dict(self.scenario_source)
        if self.resolved_elements:
            scenario_payload["resolved_elements"] = dict(
                sorted(self.resolved_elements.items())
            )
        return scenario_payload

    def private_research_provenance(self) -> dict[str, JsonValue]:
        return {
            "resolved_character_situations": {
                entity_id: artifact.model_dump(mode="json")
                for entity_id, artifact in sorted(self.situations.items())
            }
        }

    def runtime_characters(self) -> dict[str, ResolvedCharacterProfile]:
        return {
            entity_id: ResolvedCharacterProfile(
                character_id=character_id,
                profile=self.characters[character_id].profile(),
            )
            for entity_id, character_id in self.assignments.items()
        }

    def runtime_situations(self) -> dict[str, ResolvedCharacterSituation]:
        return {
            entity_id: ResolvedCharacterSituation(
                character_id=artifact.character_id,
                profile_content_hash=artifact.profile_content_hash,
                input_hash=artifact.input_hash,
                content_hash=artifact.content_hash,
                description=artifact.description,
                data=artifact.data.model_dump(mode="json"),
                generation=artifact.generation.model_dump(
                    mode="json",
                    exclude={"request", "result"},
                ),
            )
            for entity_id, artifact in self.situations.items()
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


def age_on(birth_date: date, reference_date: date) -> int:
    if birth_date > reference_date:
        raise ValueError("birth date is after the reference date")
    return (
        reference_date.year
        - birth_date.year
        - (
            (reference_date.month, reference_date.day)
            < (birth_date.month, birth_date.day)
        )
    )


def character_age(
    character: CharacterDefinition,
    reference_date: date | None,
) -> int | None:
    identity = character.identity
    if identity.birth_date is not None:
        if reference_date is None:
            return None
        return age_on(identity.birth_date, reference_date)
    return identity.age


def character_summary(
    character: CharacterDefinition,
    reference_date: date | None = None,
) -> CharacterSummary:
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
        birth_date=identity.birth_date,
        age=character_age(character, reference_date),
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
    reference_date = (
        scenario.calendar.start_datetime.date()
        if scenario.calendar is not None
        else None
    )
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
        violations = character_constraint_violations(
            character,
            slot,
            reference_date=reference_date,
        )
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
        situations={},
    )


def character_constraint_violations(
    character: CharacterDefinition,
    slot: CharacterSlotDefinition,
    *,
    reference_date: date | None = None,
) -> tuple[str, ...]:
    constraints = slot.constraints
    identity = character.identity
    violations: list[str] = []
    constrained_age: int | None = None
    if (
        constraints.minimum_age is not None
        or constraints.maximum_age is not None
    ):
        if identity.birth_date is not None and reference_date is None:
            violations.append(
                "scenario calendar is required to evaluate birth-date age"
            )
        else:
            try:
                constrained_age = character_age(character, reference_date)
            except ValueError as error:
                violations.append(str(error))
    if constraints.minimum_age is not None:
        if constrained_age is None and not violations:
            violations.append("age is required by the slot")
        elif (
            constrained_age is not None
            and constrained_age < constraints.minimum_age
        ):
            violations.append(
                f"age {constrained_age} is below minimum "
                f"{constraints.minimum_age}"
            )
    if constraints.maximum_age is not None:
        if constrained_age is None and not violations:
            violations.append("age is required by the slot")
        elif (
            constrained_age is not None
            and constrained_age > constraints.maximum_age
        ):
            violations.append(
                f"age {constrained_age} exceeds maximum "
                f"{constraints.maximum_age}"
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
