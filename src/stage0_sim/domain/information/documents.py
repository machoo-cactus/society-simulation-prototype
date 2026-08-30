import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from stage0_sim.domain.events import JsonValue


class VisibilityLevel(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"
    OPERATOR = "operator"


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: float | None = None
    end: float | None = None

    def __post_init__(self) -> None:
        if self.start is None and self.end is None:
            raise ValueError("time range must have a start or end")
        if self.start is not None and (
            isinstance(self.start, bool)
            or not isinstance(self.start, (int, float))
            or not math.isfinite(self.start)
        ):
            raise ValueError("time range start must be finite")
        if self.end is not None and (
            isinstance(self.end, bool)
            or not isinstance(self.end, (int, float))
            or not math.isfinite(self.end)
        ):
            raise ValueError("time range end must be finite")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("time range end must not precede start")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True, init=False, eq=False)
class InformationSource:
    type: str
    observer_id: str | None
    reference_ids: tuple[str, ...]
    _metadata_json: str = field(repr=False)

    def __init__(
        self,
        type: str,
        observer_id: str | None = None,
        reference_ids: tuple[str, ...] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        _require_text(type, "information source type")
        if observer_id is not None:
            _require_text(observer_id, "information source observer_id")
        _validate_unique_ids(reference_ids, "information source reference IDs")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("information source metadata must be an object")
        metadata_value: dict[str, JsonValue] = metadata or {}
        _validate_json(metadata_value)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "observer_id", observer_id)
        object.__setattr__(self, "reference_ids", reference_ids)
        object.__setattr__(
            self,
            "_metadata_json",
            canonical_json(metadata_value),
        )

    @property
    def metadata(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self._metadata_json))

    def __hash__(self) -> int:
        return hash(
            (
                self.type,
                self.observer_id,
                self.reference_ids,
                self._metadata_json,
            )
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, InformationSource)
            and self.type == other.type
            and self.observer_id == other.observer_id
            and self.reference_ids == other.reference_ids
            and self._metadata_json == other._metadata_json
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "type": self.type,
            "observer_id": self.observer_id,
            "reference_ids": list(self.reference_ids),
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class VisibilityPolicy:
    level: VisibilityLevel = VisibilityLevel.PRIVATE
    owner_ids: tuple[str, ...] = ()
    reader_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.level, VisibilityLevel):
            raise ValueError("visibility level must be a VisibilityLevel")
        _validate_unique_ids(self.owner_ids, "visibility owner IDs")
        _validate_unique_ids(self.reader_ids, "visibility reader IDs")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "level": self.level.value,
            "owner_ids": list(self.owner_ids),
            "reader_ids": list(self.reader_ids),
        }


@dataclass(frozen=True, slots=True, init=False, eq=False)
class InformationDocument:
    id: str
    namespace_id: str
    kind: str
    schema_id: str
    subject_ids: tuple[str, ...]
    _content_json: str = field(repr=False)
    source: InformationSource
    valid_time: TimeRange | None
    recorded_at: float | None
    visibility: VisibilityPolicy
    revision: int
    content_hash: str

    def __init__(
        self,
        id: str,
        namespace_id: str,
        kind: str,
        schema_id: str,
        subject_ids: tuple[str, ...],
        content: JsonValue,
        source: InformationSource,
        valid_time: TimeRange | None,
        recorded_at: float | None,
        visibility: VisibilityPolicy,
        revision: int,
        content_hash: str,
    ) -> None:
        _require_text(id, "information document ID")
        _require_text(namespace_id, "information namespace ID")
        _require_text(kind, "information document kind")
        _require_text(schema_id, "information schema ID")
        _validate_unique_ids(subject_ids, "information subject IDs")
        if not isinstance(source, InformationSource):
            raise ValueError("information source must be an InformationSource")
        if valid_time is not None and not isinstance(valid_time, TimeRange):
            raise ValueError("information valid_time must be a TimeRange or null")
        if not isinstance(visibility, VisibilityPolicy):
            raise ValueError("information visibility must be a VisibilityPolicy")
        content_json = canonical_json(content)
        if recorded_at is not None and (
            isinstance(recorded_at, bool)
            or not isinstance(recorded_at, (int, float))
            or not math.isfinite(recorded_at)
        ):
            raise ValueError("information recorded_at must be finite")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
        ):
            raise ValueError("information revision must be greater than zero")
        if content_hash != _canonical_json_text_hash(content_json):
            raise ValueError("information content_hash does not match content")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "namespace_id", namespace_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "subject_ids", subject_ids)
        object.__setattr__(self, "_content_json", content_json)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "valid_time", valid_time)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "content_hash", content_hash)

    @property
    def content(self) -> JsonValue:
        return cast(JsonValue, json.loads(self._content_json))

    def __hash__(self) -> int:
        return hash(
            (
                self.id,
                self.namespace_id,
                self.kind,
                self.schema_id,
                self.subject_ids,
                self._content_json,
                self.source,
                self.valid_time,
                self.recorded_at,
                self.visibility,
                self.revision,
                self.content_hash,
            )
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, InformationDocument)
            and self.id == other.id
            and self.namespace_id == other.namespace_id
            and self.kind == other.kind
            and self.schema_id == other.schema_id
            and self.subject_ids == other.subject_ids
            and self._content_json == other._content_json
            and self.source == other.source
            and self.valid_time == other.valid_time
            and self.recorded_at == other.recorded_at
            and self.visibility == other.visibility
            and self.revision == other.revision
            and self.content_hash == other.content_hash
        )

    @classmethod
    def create(
        cls,
        *,
        id: str,
        namespace_id: str,
        kind: str,
        schema_id: str,
        subject_ids: tuple[str, ...],
        content: JsonValue,
        source: InformationSource,
        valid_time: TimeRange | None = None,
        recorded_at: float | None = None,
        visibility: VisibilityPolicy | None = None,
        revision: int = 1,
    ) -> "InformationDocument":
        content_json = canonical_json(content)
        return cls(
            id=id,
            namespace_id=namespace_id,
            kind=kind,
            schema_id=schema_id,
            subject_ids=subject_ids,
            content=content,
            source=source,
            valid_time=valid_time,
            recorded_at=recorded_at,
            visibility=visibility or VisibilityPolicy(),
            revision=revision,
            content_hash=_canonical_json_text_hash(content_json),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "namespace_id": self.namespace_id,
            "kind": self.kind,
            "schema_id": self.schema_id,
            "subject_ids": list(self.subject_ids),
            "content": self.content,
            "source": self.source.to_dict(),
            "valid_time": (
                self.valid_time.to_dict() if self.valid_time is not None else None
            ),
            "recorded_at": self.recorded_at,
            "visibility": self.visibility.to_dict(),
            "revision": self.revision,
            "content_hash": self.content_hash,
        }


def canonical_json(value: JsonValue) -> str:
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_hash(value: JsonValue) -> str:
    return _canonical_json_text_hash(canonical_json(value))


def _canonical_json_text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def character_information_namespace_id(character_id: str) -> str:
    _require_text(character_id, "character ID")
    return f"character:{character_id}"


def character_dossier_document_id(character_id: str) -> str:
    _require_text(character_id, "character ID")
    return f"character-dossier:{character_id}"


def character_can_access_information(
    document: InformationDocument,
    character_id: str,
) -> bool:
    _require_text(character_id, "character ID")
    visibility = document.visibility
    if visibility.level is VisibilityLevel.OPERATOR:
        return False
    if visibility.level is VisibilityLevel.PUBLIC:
        return True
    if visibility.level is VisibilityLevel.PRIVATE:
        return character_id in visibility.owner_ids
    return (
        character_id in visibility.owner_ids
        or character_id in visibility.reader_ids
    )


def information_document_from_dict(payload: dict[str, JsonValue]) -> InformationDocument:
    source_payload = _require_object(payload.get("source"), "information source")
    visibility_payload = _require_object(
        payload.get("visibility"), "information visibility"
    )
    valid_time_payload = payload.get("valid_time")
    valid_time = (
        None
        if valid_time_payload is None
        else TimeRange(
            start=_optional_number(
                _require_object(valid_time_payload, "information valid_time").get(
                    "start"
                ),
                "valid_time.start",
            ),
            end=_optional_number(
                _require_object(valid_time_payload, "information valid_time").get("end"),
                "valid_time.end",
            ),
        )
    )
    content = payload.get("content")
    _validate_json(content)
    return InformationDocument(
        id=_require_string(payload.get("id"), "information document ID"),
        namespace_id=_require_string(
            payload.get("namespace_id"), "information namespace ID"
        ),
        kind=_require_string(payload.get("kind"), "information document kind"),
        schema_id=_require_string(payload.get("schema_id"), "information schema ID"),
        subject_ids=_require_string_tuple(
            payload.get("subject_ids"), "information subject IDs"
        ),
        content=_copy_json(content),
        source=InformationSource(
            type=_require_string(source_payload.get("type"), "information source type"),
            observer_id=_optional_string(
                source_payload.get("observer_id"), "information source observer_id"
            ),
            reference_ids=_require_string_tuple(
                source_payload.get("reference_ids"),
                "information source reference IDs",
            ),
            metadata=_copy_object(
                source_payload.get("metadata"), "information source metadata"
            ),
        ),
        valid_time=valid_time,
        recorded_at=_optional_number(
            payload.get("recorded_at"), "information recorded_at"
        ),
        visibility=VisibilityPolicy(
            level=VisibilityLevel(
                _require_string(
                    visibility_payload.get("level"), "information visibility level"
                )
            ),
            owner_ids=_require_string_tuple(
                visibility_payload.get("owner_ids"), "visibility owner IDs"
            ),
            reader_ids=_require_string_tuple(
                visibility_payload.get("reader_ids"), "visibility reader IDs"
            ),
        ),
        revision=_require_integer(payload.get("revision"), "information revision"),
        content_hash=_require_string(
            payload.get("content_hash"), "information content hash"
        ),
    )


def _validate_json(value: JsonValue) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json(item)
        return
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    return value


def _copy_object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _copy_json(_require_object(value, label)))


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")


def _validate_unique_ids(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must not contain empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _require_object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: JsonValue, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: JsonValue, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _require_string_tuple(value: JsonValue, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    return tuple(cast(list[str], value))


def _optional_number(value: JsonValue, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number or null")
    return float(value)


def _require_integer(value: JsonValue, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value
