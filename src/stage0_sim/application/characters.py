import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field, model_validator

from stage0_sim.application.scenario import (
    CharacterProfileDefinition,
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
        if self.profile_ref is not None:
            raise ValueError("standalone character cannot use profile_ref")
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

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "template_id": self.template_id,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
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
    characters: Mapping[str, CharacterDefinition]
    warnings: tuple[str, ...] = ()

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
        return scenario_payload

    def entity_summaries(self) -> tuple[dict[str, JsonValue], ...]:
        summaries: list[dict[str, JsonValue]] = []
        for entity in self.scenario.entities:
            profile = entity.components.get("character_profile", {})
            character_id = profile.get("character_id")
            if not isinstance(character_id, str):
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
    )


def prepare_scenario(
    scenario: ScenarioDefinition,
    library: CharacterLibrary,
) -> PreparedScenario:
    scenario = scenario.model_copy(deep=True)
    resolved: dict[str, CharacterDefinition] = {}
    warnings: list[str] = []
    for entity in scenario.entities:
        profile = entity.components.get("character_profile")
        if not profile:
            continue
        character_id = profile.get("character_id")
        profile_ref = profile.get("profile_ref")
        if character_id is not None and profile_ref is not None:
            raise CharacterLibraryError(
                f"entity {entity.id} cannot use both character_id and profile_ref"
            )
        if isinstance(character_id, str):
            character = library.get(character_id)
            inline = scenario.character_profiles.get(character_id)
            if inline is not None:
                if inline.model_dump(mode="json") != character.profile().model_dump(
                    mode="json"
                ):
                    raise CharacterConflictError(
                        f"scenario and character library define different "
                        f"content for {character_id}"
                    )
                warnings.append(
                    f"deprecated inline profile {character_id} matches the "
                    "character library and was ignored"
                )
            if character.template_id not in scenario.character_profile_templates:
                raise CharacterLibraryError(
                    f"character {character_id} uses unknown template "
                    f"{character.template_id}"
                )
            resolved[character_id] = character
            extra_fields = set(profile) - {"character_id"}
            if extra_fields:
                raise CharacterLibraryError(
                    f"entity {entity.id} character reference cannot contain "
                    f"profile overrides: {sorted(extra_fields)}"
                )
            continue
        if isinstance(profile_ref, str):
            if profile_ref in scenario.character_profiles:
                inline = scenario.character_profiles[profile_ref]
                try:
                    character = library.get(profile_ref)
                except CharacterNotFoundError:
                    warnings.append(
                        f"entity {entity.id} uses deprecated inline profile "
                        f"{profile_ref}"
                    )
                else:
                    if inline.model_dump(
                        mode="json"
                    ) != character.profile().model_dump(mode="json"):
                        raise CharacterConflictError(
                            f"scenario and character library define different "
                            f"content for {profile_ref}"
                        )
                    resolved[profile_ref] = character
                    profile["character_id"] = profile_ref
                    profile.pop("profile_ref", None)
                    warnings.append(
                        f"entity {entity.id} inline profile {profile_ref} "
                        "matches the character library; use character_id"
                    )
                continue
            character = library.get(profile_ref)
            resolved[profile_ref] = character
            profile["character_id"] = profile_ref
            profile.pop("profile_ref", None)
            warnings.append(
                f"entity {entity.id} uses deprecated profile_ref; "
                "use character_id"
            )
            continue
        warnings.append(
            f"entity {entity.id} uses a deprecated inline character profile"
        )
    return PreparedScenario(
        scenario=scenario,
        characters=resolved,
        warnings=tuple(warnings),
    )
