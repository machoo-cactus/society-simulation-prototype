import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from stage0_sim.application.characters import (
    CharacterConflictError,
    CharacterDefinition,
    CharacterLibraryError,
    CharacterNotFoundError,
    CharacterSummary,
    character_content_hash,
    character_summary,
    validate_character_id,
)


class FileSystemCharacterLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> tuple[CharacterSummary, ...]:
        return tuple(
            character_summary(self._read(path.stem))
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name)
            if not path.name.startswith(".")
        )

    def get(self, character_id: str) -> CharacterDefinition:
        return self._read(character_id)

    def create(self, character: CharacterDefinition) -> CharacterDefinition:
        path = self._path(character.id)
        if path.exists():
            raise CharacterConflictError(
                f"character already exists: {character.id}"
            )
        self._write(character)
        return self._read(character.id)

    def update(
        self,
        character_id: str,
        character: CharacterDefinition,
        expected_hash: str,
    ) -> CharacterDefinition:
        validate_character_id(character_id)
        if character.id != character_id:
            raise CharacterLibraryError(
                "character ID cannot change during update; use rename"
            )
        current = self._read(character_id)
        self._require_hash(current, expected_hash)
        self._write(character)
        return self._read(character_id)

    def rename(
        self,
        character_id: str,
        new_id: str,
        expected_hash: str,
    ) -> CharacterDefinition:
        validate_character_id(new_id)
        current = self._read(character_id)
        self._require_hash(current, expected_hash)
        destination = self._path(new_id)
        if destination.exists():
            raise CharacterConflictError(f"character already exists: {new_id}")
        renamed = current.model_copy(update={"id": new_id})
        self._write(renamed)
        self._path(character_id).unlink()
        return self._read(new_id)

    def delete(self, character_id: str, expected_hash: str) -> None:
        current = self._read(character_id)
        self._require_hash(current, expected_hash)
        try:
            self._path(character_id).unlink()
        except OSError as error:
            raise CharacterLibraryError(
                f"could not delete character {character_id}: {error}"
            ) from error

    def _path(self, character_id: str) -> Path:
        validate_character_id(character_id)
        path = (self.root / f"{character_id}.json").resolve()
        if path.parent != self.root:
            raise CharacterLibraryError("character path escapes library root")
        return path

    def _read(self, character_id: str) -> CharacterDefinition:
        path = self._path(character_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CharacterNotFoundError(
                f"unknown character: {character_id}"
            ) from error
        except OSError as error:
            raise CharacterLibraryError(
                f"could not read character {character_id}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise CharacterLibraryError(
                f"character {character_id} is not valid JSON: {error}"
            ) from error
        try:
            character = CharacterDefinition.model_validate(raw)
        except ValidationError as error:
            raise CharacterLibraryError(
                f"character {character_id} validation failed: {error}"
            ) from error
        if character.id != character_id:
            raise CharacterLibraryError(
                f"character file {path.name} contains mismatched ID "
                f"{character.id}"
            )
        return character

    def _write(self, character: CharacterDefinition) -> None:
        path = self._path(character.id)
        temporary = self.root / f".{character.id}.{uuid4().hex}.tmp"
        payload = json.dumps(
            character.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        try:
            temporary.write_text(f"{payload}\n", encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        except OSError as error:
            raise CharacterLibraryError(
                f"could not write character {character.id}: {error}"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _require_hash(
        character: CharacterDefinition,
        expected_hash: str,
    ) -> None:
        if character_content_hash(character) != expected_hash:
            raise CharacterConflictError(
                f"character changed since it was loaded: {character.id}"
            )
