from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResourceKind(StrEnum):
    CHARACTER = "character"
    ELEMENT = "element"
    SCENARIO = "scenario"


class MigrationContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    element_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    element_hashes: dict[str, str] = Field(default_factory=dict)


class MigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_kind: ResourceKind
    resource_id: str
    from_version: int | None
    to_version: int
    canonical_json: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors and self.canonical_json is not None

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)
