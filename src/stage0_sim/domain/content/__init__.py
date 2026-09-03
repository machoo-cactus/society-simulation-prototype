from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn, TypeVar

from stage0_sim.domain.events import JsonValue

MAX_TEXT_ID_LENGTH = 128
MAX_DISPLAY_LABEL_LENGTH = 256
MAX_BLOCK_TEXT_LENGTH = 65_536
MAX_ARTIFACT_TEXT_LENGTH = 1_048_576
MAX_BLOCKS_PER_ARTIFACT = 1_024
MAX_COLLECTION_CAPACITY = 100_000

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")


class TextOperation(StrEnum):
    DISCOVER = "discover"
    LIST = "list"
    READ = "read"
    CREATE = "create"
    APPEND = "append"
    REPLACE = "replace"
    EDIT = "edit"
    DELETE = "delete"
    SEND = "send"
    RECEIVE = "receive"


class TextArtifactMode(StrEnum):
    IMMUTABLE = "immutable"
    MUTABLE = "mutable"
    APPEND_ONLY = "append_only"


class TextMediaKind(StrEnum):
    BOOK = "book"
    LETTER = "letter"
    NOTE = "note"
    DOCUMENT = "document"
    MESSAGE = "message"
    POST = "post"
    NEWS = "news"


class TextAttributionDisplay(StrEnum):
    VERIFIED = "verified"
    PSEUDONYMOUS = "pseudonymous"
    ANONYMOUS = "anonymous"
    UNVERIFIED = "unverified"


TextAttributionDisplayMode = TextAttributionDisplay


class TextBlockKind(StrEnum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    ENTRY = "entry"


class TextCollectionKind(StrEnum):
    LIBRARY = "library"
    FEED = "feed"
    BOARD = "board"
    MAILBOX = "mailbox"
    SENT = "sent"
    DOCUMENT_SET = "document_set"


class TextPrincipalKind(StrEnum):
    CHARACTER = "character"
    GROUP = "group"
    ADDRESS = "address"
    PUBLIC = "public"


class TextContentErrorReason(StrEnum):
    REVISION_CONFLICT = "revision_conflict"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    INVALID_OPERATION = "invalid_operation"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    SENDER_NOT_AUTHORIZED = "sender_not_authorized"
    RECIPIENT_REJECTED = "recipient_rejected"
    DELETED = "deleted"


class TextContentError(ValueError):
    reason: str

    def __init__(self, reason: str | TextContentErrorReason, message: str) -> None:
        self.reason = str(reason)
        super().__init__(message)


def normalize_plain_text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True, order=True)
class TextPrincipal:
    kind: TextPrincipalKind
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TextPrincipalKind):
            raise ValueError("text principal kind must be a TextPrincipalKind")
        if self.kind is TextPrincipalKind.PUBLIC:
            if self.id != "public":
                raise ValueError("public text principal ID must be 'public'")
        else:
            _validate_id(self.id, "text principal ID")

    @classmethod
    def character(cls, character_id: str) -> TextPrincipal:
        return cls(TextPrincipalKind.CHARACTER, character_id)

    @classmethod
    def group(cls, group_id: str) -> TextPrincipal:
        return cls(TextPrincipalKind.GROUP, group_id)

    @classmethod
    def address(cls, address_id: str) -> TextPrincipal:
        return cls(TextPrincipalKind.ADDRESS, address_id)

    @classmethod
    def public(cls) -> TextPrincipal:
        return cls(TextPrincipalKind.PUBLIC, "public")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"kind": self.kind.value, "id": self.id}

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextPrincipal:
        _require_exact_keys(payload, {"kind", "id"}, "text principal")
        return cls(
            kind=TextPrincipalKind(
                _require_string(payload.get("kind"), "text principal kind")
            ),
            id=_require_string(payload.get("id"), "text principal ID"),
        )


@dataclass(frozen=True, slots=True)
class TextAccessGrant:
    operation: TextOperation
    principals: tuple[TextPrincipal, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.operation, TextOperation):
            raise ValueError("access grant operation must be a TextOperation")
        if not self.principals:
            raise ValueError("access grant must contain at least one principal")
        _validate_unique(self.principals, "access grant principals")

    def to_dict(self) -> dict[str, JsonValue]:
        principals: list[JsonValue] = [
            principal.to_dict() for principal in self.principals
        ]
        return {
            "operation": self.operation.value,
            "principals": principals,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextAccessGrant:
        _require_exact_keys(
            payload,
            {"operation", "principals"},
            "text access grant",
        )
        principals = _require_list(
            payload.get("principals"), "text access grant principals"
        )
        return cls(
            operation=TextOperation(
                _require_string(
                    payload.get("operation"), "text access grant operation"
                )
            ),
            principals=tuple(
                TextPrincipal.from_dict(
                    _require_object(value, "text access grant principal")
                )
                for value in principals
            ),
        )


@dataclass(frozen=True, slots=True)
class TextAccessPolicy:
    grants: tuple[TextAccessGrant, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(grant, TextAccessGrant) for grant in self.grants):
            raise ValueError("access policy grants must be TextAccessGrant values")
        operations = [grant.operation for grant in self.grants]
        if len(operations) != len(set(operations)):
            raise ValueError("access policy may define each operation only once")

    @classmethod
    def from_mapping(
        cls,
        grants: Mapping[TextOperation, Iterable[TextPrincipal]],
    ) -> TextAccessPolicy:
        return cls(
            tuple(
                TextAccessGrant(operation, tuple(principals))
                for operation, principals in sorted(
                    grants.items(), key=lambda item: item[0].value
                )
            )
        )

    def allows(
        self,
        operation: TextOperation,
        actor_id: str,
        group_memberships: Iterable[str] = (),
        controlled_address_ids: Iterable[str] = (),
    ) -> bool:
        _validate_id(actor_id, "actor ID")
        if not isinstance(operation, TextOperation):
            raise ValueError("operation must be a TextOperation")
        principals = {
            TextPrincipal.character(actor_id),
            *(TextPrincipal.group(group_id) for group_id in group_memberships),
            *(
                TextPrincipal.address(address_id)
                for address_id in controlled_address_ids
            ),
            TextPrincipal.public(),
        }
        return self.allows_principals(operation, principals)

    def allows_principals(
        self,
        operation: TextOperation,
        principals: Iterable[TextPrincipal],
    ) -> bool:
        candidates = set(principals)
        candidates.add(TextPrincipal.public())
        return any(
            grant.operation is operation
            and any(principal in candidates for principal in grant.principals)
            for grant in self.grants
        )

    def to_dict(self) -> dict[str, JsonValue]:
        grants: list[JsonValue] = [grant.to_dict() for grant in self.grants]
        return {"grants": grants}

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextAccessPolicy:
        _require_exact_keys(payload, {"grants"}, "text access policy")
        grants = _require_list(payload.get("grants"), "text access policy grants")
        return cls(
            tuple(
                TextAccessGrant.from_dict(
                    _require_object(value, "text access policy grant")
                )
                for value in grants
            )
        )


@dataclass(frozen=True, slots=True)
class TextAttribution:
    authoritative_actor_id: str
    display: TextAttributionDisplay
    sender_address_id: str | None = None
    display_label: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.authoritative_actor_id, "authoritative actor ID")
        if not isinstance(self.display, TextAttributionDisplay):
            raise ValueError("attribution display must be a TextAttributionDisplay")
        if self.sender_address_id is not None:
            _validate_id(self.sender_address_id, "sender address ID")
        if self.display_label is not None:
            _validate_label(self.display_label, "attribution display label")
        if self.display is TextAttributionDisplay.ANONYMOUS:
            if self.sender_address_id is not None or self.display_label is not None:
                raise ValueError("anonymous attribution cannot expose an identity")
        elif self.display is TextAttributionDisplay.PSEUDONYMOUS:
            if self.display_label is None or self.sender_address_id is not None:
                raise ValueError(
                    "pseudonymous attribution requires only a display label"
                )
        elif self.display is TextAttributionDisplay.UNVERIFIED:
            if self.display_label is None or self.sender_address_id is not None:
                raise ValueError(
                    "unverified attribution requires only a display label"
                )
        elif self.sender_address_id is None and self.display_label is None:
            raise ValueError(
                "verified attribution requires a sender address or display label"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "authoritative_actor_id": self.authoritative_actor_id,
            "display": self.display.value,
            "sender_address_id": self.sender_address_id,
            "display_label": self.display_label,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextAttribution:
        _require_exact_keys(
            payload,
            {
                "authoritative_actor_id",
                "display",
                "sender_address_id",
                "display_label",
            },
            "text attribution",
        )
        return cls(
            authoritative_actor_id=_require_string(
                payload.get("authoritative_actor_id"),
                "attribution authoritative actor ID",
            ),
            display=TextAttributionDisplay(
                _require_string(payload.get("display"), "attribution display")
            ),
            sender_address_id=_optional_string(
                payload.get("sender_address_id"), "attribution sender address ID"
            ),
            display_label=_optional_string(
                payload.get("display_label"), "attribution display label"
            ),
        )


@dataclass(frozen=True, slots=True)
class TextBlockDraft:
    text: str
    kind: TextBlockKind = TextBlockKind.PARAGRAPH

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TextBlockKind):
            raise ValueError("text block draft kind must be a TextBlockKind")
        normalized = normalize_plain_text(self.text)
        _validate_text_length(normalized)
        object.__setattr__(self, "text", normalized)


@dataclass(frozen=True, slots=True)
class TextBlock:
    id: str
    revision: int
    text: str
    kind: TextBlockKind = TextBlockKind.PARAGRAPH
    tombstone: bool = False

    def __post_init__(self) -> None:
        _validate_id(self.id, "text block ID")
        _validate_revision(self.revision, "text block revision")
        if not isinstance(self.kind, TextBlockKind):
            raise ValueError("text block kind must be a TextBlockKind")
        normalized = normalize_plain_text(self.text)
        _validate_text_length(normalized)
        if self.tombstone and normalized:
            raise ValueError("a tombstoned text block must have empty text")
        object.__setattr__(self, "text", normalized)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "revision": self.revision,
            "text": self.text,
            "kind": self.kind.value,
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextBlock:
        _require_exact_keys(
            payload,
            {"id", "revision", "text", "kind", "tombstone"},
            "text block",
        )
        return cls(
            id=_require_string(payload.get("id"), "text block ID"),
            revision=_require_integer(
                payload.get("revision"), "text block revision"
            ),
            text=_require_string(payload.get("text"), "text block text"),
            kind=TextBlockKind(
                _require_string(payload.get("kind"), "text block kind")
            ),
            tombstone=_require_boolean(
                payload.get("tombstone"), "text block tombstone"
            ),
        )


@dataclass(frozen=True, slots=True)
class TextRevision:
    revision: int
    parent_revision: int | None
    operation_id: str
    operation: TextOperation
    attribution: TextAttribution
    simulation_tick: int
    simulation_time: float
    content_hash: str
    blocks: tuple[TextBlock, ...]

    def __post_init__(self) -> None:
        _validate_revision(self.revision, "text artifact revision")
        if self.revision == 1:
            if self.parent_revision is not None:
                raise ValueError("first text revision cannot have a parent")
        elif self.parent_revision != self.revision - 1:
            raise ValueError("text revision parent must be the preceding revision")
        _validate_id(self.operation_id, "text operation ID")
        if not isinstance(self.operation, TextOperation):
            raise ValueError("text revision operation must be a TextOperation")
        if not isinstance(self.attribution, TextAttribution):
            raise ValueError("text revision attribution must be TextAttribution")
        _validate_tick_time(self.simulation_tick, self.simulation_time)
        _validate_blocks(self.blocks)
        if self.content_hash != text_blocks_hash(self.blocks):
            raise ValueError("text revision content_hash does not match blocks")

    @property
    def operation_kind(self) -> TextOperation:
        return self.operation

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        parent_revision: int | None,
        operation_id: str,
        operation: TextOperation,
        attribution: TextAttribution,
        simulation_tick: int,
        simulation_time: float,
        blocks: tuple[TextBlock, ...],
    ) -> TextRevision:
        return cls(
            revision=revision,
            parent_revision=parent_revision,
            operation_id=operation_id,
            operation=operation,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            content_hash=text_blocks_hash(blocks),
            blocks=blocks,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        blocks: list[JsonValue] = [block.to_dict() for block in self.blocks]
        return {
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "operation_id": self.operation_id,
            "operation": self.operation.value,
            "attribution": self.attribution.to_dict(),
            "simulation_tick": self.simulation_tick,
            "simulation_time": self.simulation_time,
            "content_hash": self.content_hash,
            "blocks": blocks,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextRevision:
        _require_exact_keys(
            payload,
            {
                "revision",
                "parent_revision",
                "operation_id",
                "operation",
                "attribution",
                "simulation_tick",
                "simulation_time",
                "content_hash",
                "blocks",
            },
            "text revision",
        )
        blocks = _require_list(payload.get("blocks"), "text revision blocks")
        return cls(
            revision=_require_integer(
                payload.get("revision"), "text revision number"
            ),
            parent_revision=_optional_integer(
                payload.get("parent_revision"), "text revision parent"
            ),
            operation_id=_require_string(
                payload.get("operation_id"), "text revision operation ID"
            ),
            operation=TextOperation(
                _require_string(payload.get("operation"), "text revision operation")
            ),
            attribution=TextAttribution.from_dict(
                _require_object(
                    payload.get("attribution"), "text revision attribution"
                )
            ),
            simulation_tick=_require_integer(
                payload.get("simulation_tick"), "text revision simulation tick"
            ),
            simulation_time=_require_number(
                payload.get("simulation_time"), "text revision simulation time"
            ),
            content_hash=_require_string(
                payload.get("content_hash"), "text revision content hash"
            ),
            blocks=tuple(
                TextBlock.from_dict(_require_object(value, "text revision block"))
                for value in blocks
            ),
        )


@dataclass(frozen=True, slots=True)
class TextArtifact:
    id: str
    media_kind: TextMediaKind
    mode: TextArtifactMode
    current_revision: int
    history: tuple[TextRevision, ...]
    access_policy: TextAccessPolicy
    tombstone: bool = False

    def __post_init__(self) -> None:
        _validate_id(self.id, "text artifact ID")
        if not isinstance(self.media_kind, TextMediaKind):
            raise ValueError("artifact media_kind must be a TextMediaKind")
        if not isinstance(self.mode, TextArtifactMode):
            raise ValueError("artifact mode must be a TextArtifactMode")
        if not isinstance(self.access_policy, TextAccessPolicy):
            raise ValueError("artifact access_policy must be a TextAccessPolicy")
        _validate_revision(self.current_revision, "artifact current revision")
        if not self.history:
            raise ValueError("text artifact must retain revision history")
        expected = tuple(range(1, self.current_revision + 1))
        actual = tuple(revision.revision for revision in self.history)
        if actual != expected or self.history[-1].revision != self.current_revision:
            raise ValueError("text artifact history must be complete and ordered")

    @property
    def current(self) -> TextRevision:
        return self.history[-1]

    @classmethod
    def create(
        cls,
        *,
        id: str,
        media_kind: TextMediaKind,
        mode: TextArtifactMode,
        blocks: tuple[TextBlock, ...],
        access_policy: TextAccessPolicy,
        operation_id: str,
        attribution: TextAttribution,
        simulation_tick: int,
        simulation_time: float,
    ) -> TextArtifact:
        revision = TextRevision.create(
            revision=1,
            parent_revision=None,
            operation_id=operation_id,
            operation=TextOperation.CREATE,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            blocks=blocks,
        )
        return cls(
            id=id,
            media_kind=media_kind,
            mode=mode,
            current_revision=1,
            history=(revision,),
            access_policy=access_policy,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        history: list[JsonValue] = [revision.to_dict() for revision in self.history]
        return {
            "id": self.id,
            "media_kind": self.media_kind.value,
            "mode": self.mode.value,
            "current_revision": self.current_revision,
            "history": history,
            "access_policy": self.access_policy.to_dict(),
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextArtifact:
        _require_exact_keys(
            payload,
            {
                "id",
                "media_kind",
                "mode",
                "current_revision",
                "history",
                "access_policy",
                "tombstone",
            },
            "text artifact",
        )
        history = _require_list(payload.get("history"), "text artifact history")
        return cls(
            id=_require_string(payload.get("id"), "text artifact ID"),
            media_kind=TextMediaKind(
                _require_string(
                    payload.get("media_kind"), "text artifact media kind"
                )
            ),
            mode=TextArtifactMode(
                _require_string(payload.get("mode"), "text artifact mode")
            ),
            current_revision=_require_integer(
                payload.get("current_revision"),
                "text artifact current revision",
            ),
            history=tuple(
                TextRevision.from_dict(
                    _require_object(value, "text artifact revision")
                )
                for value in history
            ),
            access_policy=TextAccessPolicy.from_dict(
                _require_object(
                    payload.get("access_policy"), "text artifact access policy"
                )
            ),
            tombstone=_require_boolean(
                payload.get("tombstone"), "text artifact tombstone"
            ),
        )


@dataclass(frozen=True, slots=True)
class TextCollection:
    id: str
    kind: TextCollectionKind
    revision: int
    members: tuple[str, ...]
    capacity: int
    access_policy: TextAccessPolicy

    def __post_init__(self) -> None:
        _validate_id(self.id, "text collection ID")
        if not isinstance(self.kind, TextCollectionKind):
            raise ValueError("collection kind must be a TextCollectionKind")
        _validate_revision(self.revision, "text collection revision")
        if (
            isinstance(self.capacity, bool)
            or not isinstance(self.capacity, int)
            or self.capacity <= 0
            or self.capacity > MAX_COLLECTION_CAPACITY
        ):
            raise ValueError(
                f"collection capacity must be between 1 and {MAX_COLLECTION_CAPACITY}"
            )
        for member_id in self.members:
            _validate_id(member_id, "text collection member ID")
        _validate_unique(self.members, "text collection members")
        if len(self.members) > self.capacity:
            raise ValueError("text collection members exceed capacity")
        if not isinstance(self.access_policy, TextAccessPolicy):
            raise ValueError("collection access_policy must be a TextAccessPolicy")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "revision": self.revision,
            "members": list(self.members),
            "capacity": self.capacity,
            "access_policy": self.access_policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextCollection:
        _require_exact_keys(
            payload,
            {
                "id",
                "kind",
                "revision",
                "members",
                "capacity",
                "access_policy",
            },
            "text collection",
        )
        members = _require_list(payload.get("members"), "text collection members")
        return cls(
            id=_require_string(payload.get("id"), "text collection ID"),
            kind=TextCollectionKind(
                _require_string(payload.get("kind"), "text collection kind")
            ),
            revision=_require_integer(
                payload.get("revision"), "text collection revision"
            ),
            members=tuple(
                _require_string(value, "text collection member ID")
                for value in members
            ),
            capacity=_require_integer(
                payload.get("capacity"), "text collection capacity"
            ),
            access_policy=TextAccessPolicy.from_dict(
                _require_object(
                    payload.get("access_policy"), "text collection access policy"
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class TextAddress:
    id: str
    owner: TextPrincipal
    mailbox_id: str
    display_label: str
    accepted_senders: tuple[TextPrincipal, ...]
    sent_collection_id: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.id, "text address ID")
        if not isinstance(self.owner, TextPrincipal):
            raise ValueError("text address owner must be a TextPrincipal")
        if self.owner.kind not in {
            TextPrincipalKind.CHARACTER,
            TextPrincipalKind.GROUP,
        }:
            raise ValueError("text address owner must be a character or group")
        _validate_id(self.mailbox_id, "mailbox ID")
        _validate_label(self.display_label, "address display label")
        if self.sent_collection_id is not None:
            _validate_id(self.sent_collection_id, "sent collection ID")
        if any(
            not isinstance(principal, TextPrincipal)
            for principal in self.accepted_senders
        ):
            raise ValueError("accepted senders must be TextPrincipal values")
        _validate_unique(self.accepted_senders, "accepted sender principals")

    @property
    def accepts_public(self) -> bool:
        return TextPrincipal.public() in self.accepted_senders

    def accepts(
        self,
        sender_actor_id: str,
        group_memberships: Iterable[str] = (),
        sender_address_ids: Iterable[str] = (),
    ) -> bool:
        candidates = {
            TextPrincipal.character(sender_actor_id),
            *(TextPrincipal.group(group_id) for group_id in group_memberships),
            *(
                TextPrincipal.address(address_id)
                for address_id in sender_address_ids
            ),
            TextPrincipal.public(),
        }
        return any(principal in candidates for principal in self.accepted_senders)

    def to_dict(self) -> dict[str, JsonValue]:
        accepted_senders: list[JsonValue] = [
            principal.to_dict() for principal in self.accepted_senders
        ]
        return {
            "id": self.id,
            "owner": self.owner.to_dict(),
            "mailbox_id": self.mailbox_id,
            "display_label": self.display_label,
            "accepted_senders": accepted_senders,
            "sent_collection_id": self.sent_collection_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> TextAddress:
        _require_exact_keys(
            payload,
            {
                "id",
                "owner",
                "mailbox_id",
                "display_label",
                "accepted_senders",
                "sent_collection_id",
            },
            "text address",
        )
        accepted_senders = _require_list(
            payload.get("accepted_senders"), "text address accepted senders"
        )
        return cls(
            id=_require_string(payload.get("id"), "text address ID"),
            owner=TextPrincipal.from_dict(
                _require_object(payload.get("owner"), "text address owner")
            ),
            mailbox_id=_require_string(
                payload.get("mailbox_id"), "text address mailbox ID"
            ),
            display_label=_require_string(
                payload.get("display_label"), "text address display label"
            ),
            accepted_senders=tuple(
                TextPrincipal.from_dict(
                    _require_object(value, "text address accepted sender")
                )
                for value in accepted_senders
            ),
            sent_collection_id=_optional_string(
                payload.get("sent_collection_id"),
                "text address sent collection ID",
            ),
        )


@dataclass(frozen=True, slots=True)
class TextReadReceipt:
    artifact_id: str
    artifact_revision: int
    block_ids: tuple[str, ...]
    rendered_text: str
    endpoint_id: str
    target_id: str
    content_hash: str
    rendered_hash: str
    reader_id: str
    simulation_time: float

    def __post_init__(self) -> None:
        _validate_id(self.artifact_id, "receipt artifact ID")
        _validate_revision(self.artifact_revision, "receipt artifact revision")
        for block_id in self.block_ids:
            _validate_id(block_id, "receipt block ID")
        _validate_unique(self.block_ids, "receipt block IDs")
        normalized = normalize_plain_text(self.rendered_text)
        if normalized != self.rendered_text:
            raise ValueError("receipt rendered text must be LF-normalized")
        _validate_id(self.endpoint_id, "receipt endpoint ID")
        _validate_id(self.target_id, "receipt target ID")
        _validate_hash(self.content_hash, "receipt content hash")
        if self.rendered_hash != _text_hash(self.rendered_text):
            raise ValueError("receipt rendered_hash does not match rendered text")
        _validate_id(self.reader_id, "receipt reader ID")
        _validate_time(self.simulation_time, "receipt simulation time")

    @property
    def hash(self) -> str:
        return self.rendered_hash


@dataclass(frozen=True, slots=True)
class TextDeliveryResult:
    artifact: TextArtifact
    recipient_collection: TextCollection
    sent_collection: TextCollection
    unread_count: int


_ResultT = TypeVar(
    "_ResultT",
    TextArtifact,
    TextReadReceipt,
    TextDeliveryResult,
)


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    operation: TextOperation
    result: TextArtifact | TextReadReceipt | TextDeliveryResult


BlockInput = TextBlockDraft | tuple[TextBlockKind, str] | str


class TextContentRegistry:
    def __init__(
        self,
        *,
        artifacts: Iterable[TextArtifact] = (),
        collections: Iterable[TextCollection] = (),
        addresses: Iterable[TextAddress] = (),
        groups: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self._artifacts: dict[str, TextArtifact] = {}
        self._collections: dict[str, TextCollection] = {}
        self._addresses: dict[str, TextAddress] = {}
        self._groups: dict[str, tuple[str, ...]] = {}
        self._unread_counts: dict[str, int] = {}
        self._operation_results: dict[str, _OperationRecord] = {}
        self._operation_counter = 0
        for artifact in artifacts:
            self.register_artifact(artifact)
        for collection in collections:
            self.register_collection(collection)
        for address in addresses:
            self.register_address(address)
        if groups is not None:
            for group_id, member_ids in groups.items():
                self.register_group(group_id, member_ids)

    @property
    def artifacts(self) -> Mapping[str, TextArtifact]:
        return MappingProxyType(dict(self._artifacts))

    @property
    def collections(self) -> Mapping[str, TextCollection]:
        return MappingProxyType(dict(self._collections))

    @property
    def addresses(self) -> Mapping[str, TextAddress]:
        return MappingProxyType(dict(self._addresses))

    @property
    def groups(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType(dict(self._groups))

    def to_dict(self) -> dict[str, JsonValue]:
        artifacts: list[JsonValue] = [
            artifact.to_dict()
            for artifact in sorted(
                self._artifacts.values(), key=lambda value: value.id
            )
        ]
        collections: list[JsonValue] = [
            collection.to_dict()
            for collection in sorted(
                self._collections.values(), key=lambda value: value.id
            )
        ]
        addresses: list[JsonValue] = [
            address.to_dict()
            for address in sorted(
                self._addresses.values(), key=lambda value: value.id
            )
        ]
        groups: dict[str, JsonValue] = {
            group_id: list(self._groups[group_id])
            for group_id in sorted(self._groups)
        }
        unread_counts: dict[str, JsonValue] = {
            address_id: self._unread_counts[address_id]
            for address_id in sorted(self._unread_counts)
        }
        return {
            "operation_counter": self._operation_counter,
            "unread_counts": unread_counts,
            "groups": groups,
            "artifacts": artifacts,
            "collections": collections,
            "addresses": addresses,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, JsonValue],
    ) -> TextContentRegistry:
        _require_exact_keys(
            payload,
            {
                "operation_counter",
                "unread_counts",
                "groups",
                "artifacts",
                "collections",
                "addresses",
            },
            "text content registry",
        )
        artifacts_payload = _require_list(
            payload.get("artifacts"), "text content registry artifacts"
        )
        collections_payload = _require_list(
            payload.get("collections"), "text content registry collections"
        )
        addresses_payload = _require_list(
            payload.get("addresses"), "text content registry addresses"
        )
        groups_payload = _require_object(
            payload.get("groups"), "text content registry groups"
        )
        groups: dict[str, tuple[str, ...]] = {}
        for group_id in sorted(groups_payload):
            members_payload = _require_list(
                groups_payload[group_id],
                f"text group {group_id} members",
            )
            groups[group_id] = tuple(
                _require_string(member, f"text group {group_id} member ID")
                for member in members_payload
            )
        registry = cls(
            artifacts=(
                TextArtifact.from_dict(
                    _require_object(value, "text content registry artifact")
                )
                for value in artifacts_payload
            ),
            collections=(
                TextCollection.from_dict(
                    _require_object(value, "text content registry collection")
                )
                for value in collections_payload
            ),
            addresses=(
                TextAddress.from_dict(
                    _require_object(value, "text content registry address")
                )
                for value in addresses_payload
            ),
            groups=groups,
        )
        operation_counter = _require_integer(
            payload.get("operation_counter"),
            "text content registry operation counter",
        )
        if operation_counter < 0:
            raise ValueError(
                "text content registry operation counter must not be negative"
            )
        unread_payload = _require_object(
            payload.get("unread_counts"),
            "text content registry unread counts",
        )
        if set(unread_payload) != set(registry._addresses):
            raise ValueError(
                "text content registry unread counts must match address IDs"
            )
        unread_counts: dict[str, int] = {}
        for address_id in sorted(unread_payload):
            unread_count = _require_integer(
                unread_payload[address_id],
                f"text address {address_id} unread count",
            )
            if unread_count < 0:
                raise ValueError("text address unread count must not be negative")
            unread_counts[address_id] = unread_count
        missing_members = sorted(
            member_id
            for collection in registry._collections.values()
            for member_id in collection.members
            if member_id not in registry._artifacts
        )
        if missing_members:
            raise ValueError(
                "text collections reference unknown artifacts: "
                + ", ".join(missing_members)
            )
        registry._operation_counter = operation_counter
        registry._unread_counts = unread_counts
        return registry

    def register_artifact(self, artifact: TextArtifact) -> None:
        if not isinstance(artifact, TextArtifact):
            raise ValueError("artifact must be a TextArtifact")
        if artifact.id in self._artifacts:
            raise ValueError(f"duplicate text artifact ID: {artifact.id}")
        self._artifacts[artifact.id] = artifact

    def register_collection(self, collection: TextCollection) -> None:
        if not isinstance(collection, TextCollection):
            raise ValueError("collection must be a TextCollection")
        if collection.id in self._collections:
            raise ValueError(f"duplicate text collection ID: {collection.id}")
        self._collections[collection.id] = collection

    def register_address(self, address: TextAddress) -> None:
        if not isinstance(address, TextAddress):
            raise ValueError("address must be a TextAddress")
        if address.id in self._addresses:
            raise ValueError(f"duplicate text address ID: {address.id}")
        self._addresses[address.id] = address
        self._unread_counts[address.id] = 0

    def register_group(self, group_id: str, member_ids: Iterable[str]) -> None:
        _validate_id(group_id, "group ID")
        if group_id in self._groups:
            raise ValueError(f"duplicate text group ID: {group_id}")
        members = tuple(sorted(member_ids))
        for member_id in members:
            _validate_id(member_id, "group member ID")
        _validate_unique(members, "group members")
        self._groups[group_id] = members

    def artifact(self, artifact_id: str) -> TextArtifact:
        try:
            return self._artifacts[artifact_id]
        except KeyError as error:
            raise TextContentError(
                TextContentErrorReason.NOT_FOUND,
                f"text artifact not found: {artifact_id}",
            ) from error

    def collection(self, collection_id: str) -> TextCollection:
        try:
            return self._collections[collection_id]
        except KeyError as error:
            raise TextContentError(
                TextContentErrorReason.NOT_FOUND,
                f"text collection not found: {collection_id}",
            ) from error

    def address(self, address_id: str) -> TextAddress:
        try:
            return self._addresses[address_id]
        except KeyError as error:
            raise TextContentError(
                TextContentErrorReason.NOT_FOUND,
                f"text address not found: {address_id}",
            ) from error

    def group_memberships(self, actor_id: str) -> tuple[str, ...]:
        _validate_id(actor_id, "actor ID")
        return tuple(
            sorted(
                group_id
                for group_id, members in self._groups.items()
                if actor_id in members
            )
        )

    def controlled_address_ids(self, actor_id: str) -> tuple[str, ...]:
        memberships = set(self.group_memberships(actor_id))
        return tuple(
            sorted(
                address.id
                for address in self._addresses.values()
                if (
                    address.owner.kind is TextPrincipalKind.CHARACTER
                    and address.owner.id == actor_id
                )
                or (
                    address.owner.kind is TextPrincipalKind.GROUP
                    and address.owner.id in memberships
                )
            )
        )

    def unread_count(self, address_id: str) -> int:
        self.address(address_id)
        return self._unread_counts[address_id]

    def next_operation_id(self) -> str:
        self._operation_counter += 1
        return f"operation-{self._operation_counter:012d}"

    def discoverable_collections(self, actor_id: str) -> tuple[TextCollection, ...]:
        return tuple(
            collection
            for collection in sorted(
                self._collections.values(), key=lambda item: item.id
            )
            if self._allows(collection.access_policy, TextOperation.DISCOVER, actor_id)
        )

    def list_collection(
        self,
        *,
        collection_id: str,
        actor_id: str,
    ) -> tuple[TextArtifact, ...]:
        collection = self.collection(collection_id)
        self._require_access(
            collection.access_policy,
            TextOperation.LIST,
            actor_id,
            f"list collection {collection_id}",
        )
        return tuple(self.artifact(member_id) for member_id in collection.members)

    def create_artifact_in_collection(
        self,
        *,
        collection_id: str,
        expected_collection_revision: int,
        media_kind: TextMediaKind,
        mode: TextArtifactMode,
        blocks: Sequence[BlockInput],
        access_policy: TextAccessPolicy,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
        artifact_id: str | None = None,
    ) -> TextArtifact:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id, TextOperation.CREATE, TextArtifact
        )
        if duplicate is not None:
            return duplicate
        collection = self.collection(collection_id)
        self._require_revision(
            collection.revision,
            expected_collection_revision,
            f"text collection {collection_id}",
        )
        self._require_access(
            collection.access_policy,
            TextOperation.CREATE,
            actor_id,
            f"create in collection {collection_id}",
        )
        self._validate_attribution(attribution, actor_id)
        if len(collection.members) >= collection.capacity:
            self._raise(
                TextContentErrorReason.CAPACITY_EXCEEDED,
                f"text collection is at capacity: {collection_id}",
            )
        resolved_artifact_id = artifact_id or _derived_id(
            "artifact", resolved_operation_id
        )
        _validate_id(resolved_artifact_id, "text artifact ID")
        if resolved_artifact_id in self._artifacts:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"text artifact already exists: {resolved_artifact_id}",
            )
        new_blocks = self._new_blocks(blocks, resolved_operation_id)
        candidate_artifact = TextArtifact.create(
            id=resolved_artifact_id,
            media_kind=media_kind,
            mode=mode,
            blocks=new_blocks,
            access_policy=access_policy,
            operation_id=resolved_operation_id,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
        )
        candidate_collection = TextCollection(
            id=collection.id,
            kind=collection.kind,
            revision=collection.revision + 1,
            members=(*collection.members, candidate_artifact.id),
            capacity=collection.capacity,
            access_policy=collection.access_policy,
        )
        self._artifacts[candidate_artifact.id] = candidate_artifact
        self._collections[collection.id] = candidate_collection
        self._record(
            resolved_operation_id,
            TextOperation.CREATE,
            candidate_artifact,
            automatic,
        )
        return candidate_artifact

    def append_blocks(
        self,
        *,
        artifact_id: str,
        expected_artifact_revision: int,
        blocks: Sequence[BlockInput],
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
    ) -> TextArtifact:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id, TextOperation.APPEND, TextArtifact
        )
        if duplicate is not None:
            return duplicate
        artifact = self._mutable_artifact(
            artifact_id,
            expected_artifact_revision,
            TextOperation.APPEND,
            actor_id,
            allow_append_only=True,
        )
        self._validate_attribution(attribution, actor_id)
        added = self._new_blocks(
            blocks,
            resolved_operation_id,
            existing_ids={block.id for block in artifact.current.blocks},
        )
        candidate = self._revise_artifact(
            artifact,
            blocks=(*artifact.current.blocks, *added),
            operation=TextOperation.APPEND,
            operation_id=resolved_operation_id,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
        )
        self._commit_artifact(candidate, resolved_operation_id, automatic)
        return candidate

    def replace_block(
        self,
        *,
        artifact_id: str,
        block_id: str,
        expected_artifact_revision: int,
        expected_block_revision: int,
        text: str,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
    ) -> TextArtifact:
        return self._change_block(
            artifact_id=artifact_id,
            block_id=block_id,
            expected_artifact_revision=expected_artifact_revision,
            expected_block_revision=expected_block_revision,
            operation=TextOperation.REPLACE,
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
            transform=lambda block: TextBlock(
                id=block.id,
                revision=block.revision + 1,
                text=text,
                kind=block.kind,
            ),
        )

    def edit_block(
        self,
        *,
        artifact_id: str,
        block_id: str,
        expected_artifact_revision: int,
        expected_block_revision: int,
        start: int,
        end: int,
        replacement: str,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
    ) -> TextArtifact:
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end < start
        ):
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "edit indices must be non-negative ordered integers",
            )
        normalized_replacement = normalize_plain_text(replacement)

        def edit(block: TextBlock) -> TextBlock:
            if end > len(block.text):
                self._raise(
                    TextContentErrorReason.INVALID_OPERATION,
                    "edit range exceeds block text",
                )
            return TextBlock(
                id=block.id,
                revision=block.revision + 1,
                text=block.text[:start] + normalized_replacement + block.text[end:],
                kind=block.kind,
            )

        return self._change_block(
            artifact_id=artifact_id,
            block_id=block_id,
            expected_artifact_revision=expected_artifact_revision,
            expected_block_revision=expected_block_revision,
            operation=TextOperation.EDIT,
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
            transform=edit,
        )

    def tombstone_block(
        self,
        *,
        artifact_id: str,
        block_id: str,
        expected_artifact_revision: int,
        expected_block_revision: int,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
    ) -> TextArtifact:
        return self._change_block(
            artifact_id=artifact_id,
            block_id=block_id,
            expected_artifact_revision=expected_artifact_revision,
            expected_block_revision=expected_block_revision,
            operation=TextOperation.DELETE,
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
            transform=lambda block: TextBlock(
                id=block.id,
                revision=block.revision + 1,
                text="",
                kind=block.kind,
                tombstone=True,
            ),
        )

    def tombstone_artifact(
        self,
        *,
        artifact_id: str,
        expected_artifact_revision: int,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
    ) -> TextArtifact:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id, TextOperation.DELETE, TextArtifact
        )
        if duplicate is not None:
            return duplicate
        artifact = self._mutable_artifact(
            artifact_id,
            expected_artifact_revision,
            TextOperation.DELETE,
            actor_id,
        )
        self._validate_attribution(attribution, actor_id)
        blocks = tuple(
            block
            if block.tombstone
            else TextBlock(
                id=block.id,
                revision=block.revision + 1,
                text="",
                kind=block.kind,
                tombstone=True,
            )
            for block in artifact.current.blocks
        )
        candidate = self._revise_artifact(
            artifact,
            blocks=blocks,
            operation=TextOperation.DELETE,
            operation_id=resolved_operation_id,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            tombstone=True,
        )
        self._commit_artifact(candidate, resolved_operation_id, automatic)
        return candidate

    def read_current(
        self,
        *,
        artifact_id: str,
        actor_id: str,
        endpoint_id: str,
        target_id: str,
        simulation_time: float,
        block_ids: tuple[str, ...] = (),
        operation_id: str | None = None,
    ) -> TextReadReceipt:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id, TextOperation.READ, TextReadReceipt
        )
        if duplicate is not None:
            return duplicate
        artifact = self.artifact(artifact_id)
        if artifact.tombstone:
            self._raise(
                TextContentErrorReason.DELETED,
                f"text artifact is deleted: {artifact_id}",
            )
        self._require_access(
            artifact.access_policy,
            TextOperation.READ,
            actor_id,
            f"read text artifact {artifact_id}",
        )
        for block_id in block_ids:
            _validate_id(block_id, "requested text block ID")
        _validate_unique(block_ids, "requested text block IDs")
        live_blocks = tuple(
            block for block in artifact.current.blocks if not block.tombstone
        )
        if block_ids:
            requested = set(block_ids)
            live_ids = {block.id for block in live_blocks}
            unavailable = sorted(requested - live_ids)
            if unavailable:
                self._raise(
                    TextContentErrorReason.NOT_FOUND,
                    "requested text blocks are unknown or deleted: "
                    + ", ".join(unavailable),
                )
            blocks = tuple(block for block in live_blocks if block.id in requested)
        else:
            blocks = live_blocks
        rendered_text = "\n".join(block.text for block in blocks)
        receipt = TextReadReceipt(
            artifact_id=artifact.id,
            artifact_revision=artifact.current_revision,
            block_ids=tuple(block.id for block in blocks),
            rendered_text=rendered_text,
            endpoint_id=endpoint_id,
            target_id=target_id,
            content_hash=artifact.current.content_hash,
            rendered_hash=_text_hash(rendered_text),
            reader_id=actor_id,
            simulation_time=simulation_time,
        )
        self._record(
            resolved_operation_id,
            TextOperation.READ,
            receipt,
            automatic,
        )
        return receipt

    def send_message(
        self,
        *,
        sender_address_id: str,
        recipient_address_id: str,
        expected_recipient_collection_revision: int,
        expected_sent_collection_revision: int,
        blocks: Sequence[BlockInput],
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None = None,
        sender_sent_collection_id: str | None = None,
        artifact_id: str | None = None,
    ) -> TextDeliveryResult:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id,
            TextOperation.SEND,
            TextDeliveryResult,
        )
        if duplicate is not None:
            return duplicate
        sender = self.address(sender_address_id)
        recipient = self.address(recipient_address_id)
        controlled_addresses = self.controlled_address_ids(actor_id)
        if sender.id not in controlled_addresses:
            self._raise(
                TextContentErrorReason.SENDER_NOT_AUTHORIZED,
                f"actor {actor_id} does not control sender address {sender.id}",
            )
        self._validate_attribution(attribution, actor_id)
        if (
            attribution.display is TextAttributionDisplay.VERIFIED
            and attribution.sender_address_id != sender.id
        ):
            self._raise(
                TextContentErrorReason.SENDER_NOT_AUTHORIZED,
                "verified message attribution must use the selected sender address",
            )
        memberships = self.group_memberships(actor_id)
        if not recipient.accepts(actor_id, memberships, (sender.id,)):
            self._raise(
                TextContentErrorReason.RECIPIENT_REJECTED,
                f"recipient address rejected sender: {recipient.id}",
            )
        sent_collection_id = (
            sender_sent_collection_id or sender.sent_collection_id
        )
        if sent_collection_id is None:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"sender address has no sent collection: {sender.id}",
            )
        if sent_collection_id == recipient.mailbox_id:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "recipient mailbox and sender sent collection must differ",
            )
        recipient_collection = self.collection(recipient.mailbox_id)
        sent_collection = self.collection(sent_collection_id)
        if recipient_collection.kind is not TextCollectionKind.MAILBOX:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "recipient address must resolve to a mailbox collection",
            )
        if sent_collection.kind is not TextCollectionKind.SENT:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "sender sent collection must have kind sent",
            )
        self._require_revision(
            recipient_collection.revision,
            expected_recipient_collection_revision,
            f"recipient collection {recipient_collection.id}",
        )
        self._require_revision(
            sent_collection.revision,
            expected_sent_collection_revision,
            f"sent collection {sent_collection.id}",
        )
        self._require_access(
            sent_collection.access_policy,
            TextOperation.SEND,
            actor_id,
            f"send through collection {sent_collection.id}",
        )
        recipient_principals = {recipient.owner, TextPrincipal.address(recipient.id)}
        if not recipient_collection.access_policy.allows_principals(
            TextOperation.RECEIVE, recipient_principals
        ):
            self._raise(
                TextContentErrorReason.ACCESS_DENIED,
                f"receive denied for collection {recipient_collection.id}",
            )
        if (
            len(recipient_collection.members) >= recipient_collection.capacity
            or len(sent_collection.members) >= sent_collection.capacity
        ):
            self._raise(
                TextContentErrorReason.CAPACITY_EXCEEDED,
                "message delivery collection is at capacity",
            )
        resolved_artifact_id = artifact_id or _derived_id(
            "message", resolved_operation_id
        )
        _validate_id(resolved_artifact_id, "message artifact ID")
        if resolved_artifact_id in self._artifacts:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"text artifact already exists: {resolved_artifact_id}",
            )
        message_blocks = self._new_blocks(blocks, resolved_operation_id)
        message_policy = _message_access_policy(
            sender_actor_id=actor_id,
            recipient_owner=recipient.owner,
        )
        candidate_artifact = TextArtifact.create(
            id=resolved_artifact_id,
            media_kind=TextMediaKind.MESSAGE,
            mode=TextArtifactMode.IMMUTABLE,
            blocks=message_blocks,
            access_policy=message_policy,
            operation_id=resolved_operation_id,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
        )
        candidate_recipient = TextCollection(
            id=recipient_collection.id,
            kind=recipient_collection.kind,
            revision=recipient_collection.revision + 1,
            members=(*recipient_collection.members, candidate_artifact.id),
            capacity=recipient_collection.capacity,
            access_policy=recipient_collection.access_policy,
        )
        candidate_sent = TextCollection(
            id=sent_collection.id,
            kind=sent_collection.kind,
            revision=sent_collection.revision + 1,
            members=(*sent_collection.members, candidate_artifact.id),
            capacity=sent_collection.capacity,
            access_policy=sent_collection.access_policy,
        )
        unread_count = self._unread_counts[recipient.id] + 1
        result = TextDeliveryResult(
            artifact=candidate_artifact,
            recipient_collection=candidate_recipient,
            sent_collection=candidate_sent,
            unread_count=unread_count,
        )
        self._artifacts[candidate_artifact.id] = candidate_artifact
        self._collections[candidate_recipient.id] = candidate_recipient
        self._collections[candidate_sent.id] = candidate_sent
        self._unread_counts[recipient.id] = unread_count
        self._record(
            resolved_operation_id,
            TextOperation.SEND,
            result,
            automatic,
        )
        return result

    def _change_block(
        self,
        *,
        artifact_id: str,
        block_id: str,
        expected_artifact_revision: int,
        expected_block_revision: int,
        operation: TextOperation,
        attribution: TextAttribution,
        actor_id: str,
        simulation_tick: int,
        simulation_time: float,
        operation_id: str | None,
        transform: Callable[[TextBlock], TextBlock],
    ) -> TextArtifact:
        resolved_operation_id, automatic = self._resolve_operation_id(operation_id)
        duplicate = self._duplicate(
            resolved_operation_id, operation, TextArtifact
        )
        if duplicate is not None:
            return duplicate
        artifact = self._mutable_artifact(
            artifact_id,
            expected_artifact_revision,
            operation,
            actor_id,
        )
        self._validate_attribution(attribution, actor_id)
        block_index = next(
            (
                index
                for index, block in enumerate(artifact.current.blocks)
                if block.id == block_id
            ),
            None,
        )
        if block_index is None:
            self._raise(
                TextContentErrorReason.NOT_FOUND,
                f"text block not found: {block_id}",
            )
        block = artifact.current.blocks[block_index]
        if block.tombstone:
            self._raise(
                TextContentErrorReason.DELETED,
                f"text block is deleted: {block_id}",
            )
        self._require_revision(
            block.revision,
            expected_block_revision,
            f"text block {block_id}",
        )
        replacement = transform(block)
        blocks = list(artifact.current.blocks)
        blocks[block_index] = replacement
        candidate = self._revise_artifact(
            artifact,
            blocks=tuple(blocks),
            operation=operation,
            operation_id=resolved_operation_id,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
        )
        self._commit_artifact(candidate, resolved_operation_id, automatic)
        return candidate

    def _mutable_artifact(
        self,
        artifact_id: str,
        expected_revision: int,
        operation: TextOperation,
        actor_id: str,
        *,
        allow_append_only: bool = False,
    ) -> TextArtifact:
        artifact = self.artifact(artifact_id)
        self._require_revision(
            artifact.current_revision,
            expected_revision,
            f"text artifact {artifact_id}",
        )
        if artifact.tombstone:
            self._raise(
                TextContentErrorReason.DELETED,
                f"text artifact is deleted: {artifact_id}",
            )
        self._require_access(
            artifact.access_policy,
            operation,
            actor_id,
            f"{operation.value} text artifact {artifact_id}",
        )
        if artifact.mode is TextArtifactMode.IMMUTABLE:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"text artifact is immutable: {artifact_id}",
            )
        if artifact.mode is TextArtifactMode.APPEND_ONLY and not allow_append_only:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"text artifact is append-only: {artifact_id}",
            )
        return artifact

    def _revise_artifact(
        self,
        artifact: TextArtifact,
        *,
        blocks: tuple[TextBlock, ...],
        operation: TextOperation,
        operation_id: str,
        attribution: TextAttribution,
        simulation_tick: int,
        simulation_time: float,
        tombstone: bool | None = None,
    ) -> TextArtifact:
        revision_number = artifact.current_revision + 1
        revision = TextRevision.create(
            revision=revision_number,
            parent_revision=artifact.current_revision,
            operation_id=operation_id,
            operation=operation,
            attribution=attribution,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            blocks=blocks,
        )
        return TextArtifact(
            id=artifact.id,
            media_kind=artifact.media_kind,
            mode=artifact.mode,
            current_revision=revision_number,
            history=(*artifact.history, revision),
            access_policy=artifact.access_policy,
            tombstone=artifact.tombstone if tombstone is None else tombstone,
        )

    def _new_blocks(
        self,
        blocks: Sequence[BlockInput],
        operation_id: str,
        *,
        existing_ids: set[str] | None = None,
    ) -> tuple[TextBlock, ...]:
        if not blocks:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "at least one text block is required",
            )
        if len(blocks) > MAX_BLOCKS_PER_ARTIFACT:
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                "too many text blocks",
            )
        known_ids = existing_ids or set()
        result: list[TextBlock] = []
        for index, value in enumerate(blocks):
            draft = _coerce_block_draft(value)
            block_id = _derived_id("block", f"{operation_id}:{index}")
            if block_id in known_ids:
                self._raise(
                    TextContentErrorReason.INVALID_OPERATION,
                    f"generated duplicate text block ID: {block_id}",
                )
            known_ids.add(block_id)
            result.append(
                TextBlock(
                    id=block_id,
                    revision=1,
                    text=draft.text,
                    kind=draft.kind,
                )
            )
        return tuple(result)

    def _validate_attribution(
        self,
        attribution: TextAttribution,
        actor_id: str,
    ) -> None:
        if attribution.authoritative_actor_id != actor_id:
            self._raise(
                TextContentErrorReason.SENDER_NOT_AUTHORIZED,
                "attribution authoritative actor does not match acting character",
            )
        if (
            attribution.display is TextAttributionDisplay.VERIFIED
            and attribution.sender_address_id is not None
            and attribution.sender_address_id
            not in self.controlled_address_ids(actor_id)
        ):
            self._raise(
                TextContentErrorReason.SENDER_NOT_AUTHORIZED,
                "actor does not control verified attribution address",
            )

    def _allows(
        self,
        policy: TextAccessPolicy,
        operation: TextOperation,
        actor_id: str,
    ) -> bool:
        return policy.allows(
            operation,
            actor_id,
            self.group_memberships(actor_id),
            self.controlled_address_ids(actor_id),
        )

    def _require_access(
        self,
        policy: TextAccessPolicy,
        operation: TextOperation,
        actor_id: str,
        description: str,
    ) -> None:
        if not self._allows(policy, operation, actor_id):
            self._raise(
                TextContentErrorReason.ACCESS_DENIED,
                f"access denied: {description}",
            )

    def _require_revision(
        self,
        actual: int,
        expected: int,
        description: str,
    ) -> None:
        if actual != expected:
            self._raise(
                TextContentErrorReason.REVISION_CONFLICT,
                f"{description} revision is {actual}, expected {expected}",
            )

    def _resolve_operation_id(
        self,
        operation_id: str | None,
    ) -> tuple[str, bool]:
        if operation_id is not None:
            _validate_id(operation_id, "text operation ID")
            return operation_id, False
        return f"operation-{self._operation_counter + 1:012d}", True

    def _duplicate(
        self,
        operation_id: str,
        operation: TextOperation,
        result_type: type[_ResultT],
    ) -> _ResultT | None:
        record = self._operation_results.get(operation_id)
        if record is None:
            return None
        if record.operation is not operation or not isinstance(
            record.result, result_type
        ):
            self._raise(
                TextContentErrorReason.INVALID_OPERATION,
                f"operation ID already belongs to {record.operation.value}",
            )
        return record.result

    def _record(
        self,
        operation_id: str,
        operation: TextOperation,
        result: TextArtifact | TextReadReceipt | TextDeliveryResult,
        automatic: bool,
    ) -> None:
        self._operation_results[operation_id] = _OperationRecord(operation, result)
        if automatic:
            self._operation_counter += 1

    def _commit_artifact(
        self,
        artifact: TextArtifact,
        operation_id: str,
        automatic: bool,
    ) -> None:
        self._artifacts[artifact.id] = artifact
        self._record(
            operation_id,
            artifact.current.operation,
            artifact,
            automatic,
        )

    @staticmethod
    def _raise(reason: TextContentErrorReason, message: str) -> NoReturn:
        raise TextContentError(reason, message)


def text_blocks_hash(blocks: tuple[TextBlock, ...]) -> str:
    payload = {
        "blocks": [
            {
                "id": block.id,
                "kind": block.kind.value,
                "revision": block.revision,
                "text": block.text,
                "tombstone": block.tombstone,
            }
            for block in blocks
        ]
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_access_policy(
    *,
    sender_actor_id: str,
    recipient_owner: TextPrincipal,
) -> TextAccessPolicy:
    principals = tuple(
        dict.fromkeys((TextPrincipal.character(sender_actor_id), recipient_owner))
    )
    return TextAccessPolicy(
        (
            TextAccessGrant(TextOperation.DISCOVER, principals),
            TextAccessGrant(TextOperation.READ, principals),
        )
    )


def _coerce_block_draft(value: BlockInput) -> TextBlockDraft:
    if isinstance(value, TextBlockDraft):
        return value
    if isinstance(value, str):
        return TextBlockDraft(value)
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], TextBlockKind)
        and isinstance(value[1], str)
    ):
        return TextBlockDraft(text=value[1], kind=value[0])
    raise ValueError("text block input must be text, TextBlockDraft, or (kind, text)")


def _require_exact_keys(
    payload: Mapping[str, JsonValue],
    expected: set[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError(f"{label} fields are invalid: {'; '.join(details)}")


def _require_object(value: JsonValue | None, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_list(value: JsonValue | None, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _require_string(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _optional_string(value: JsonValue | None, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _require_integer(value: JsonValue | None, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _optional_integer(value: JsonValue | None, label: str) -> int | None:
    if value is None:
        return None
    return _require_integer(value, label)


def _require_number(value: JsonValue | None, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a finite number")
    return value


def _require_boolean(value: JsonValue | None, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _validate_blocks(blocks: tuple[TextBlock, ...]) -> None:
    if len(blocks) > MAX_BLOCKS_PER_ARTIFACT:
        raise ValueError("text revision has too many blocks")
    if any(not isinstance(block, TextBlock) for block in blocks):
        raise ValueError("text revision blocks must be TextBlock values")
    _validate_unique((block.id for block in blocks), "text block IDs")
    if sum(len(block.text) for block in blocks) > MAX_ARTIFACT_TEXT_LENGTH:
        raise ValueError("text artifact exceeds maximum text length")


def _validate_id(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TEXT_ID_LENGTH
        or _ID_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(
            f"{label} must be 1-{MAX_TEXT_ID_LENGTH} stable ID characters"
        )


def _validate_label(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > MAX_DISPLAY_LABEL_LENGTH
        or any(character in value for character in "\r\n")
    ):
        raise ValueError(
            f"{label} must be non-empty single-line text no longer than "
            f"{MAX_DISPLAY_LABEL_LENGTH} characters"
        )


def _validate_text_length(value: str) -> None:
    if len(value) > MAX_BLOCK_TEXT_LENGTH:
        raise ValueError(
            f"text block exceeds maximum length {MAX_BLOCK_TEXT_LENGTH}"
        )


def _validate_revision(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _validate_tick_time(tick: int, simulation_time: float) -> None:
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ValueError("simulation tick must be a non-negative integer")
    _validate_time(simulation_time, "simulation time")


def _validate_time(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be a finite non-negative number")


def _validate_hash(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _validate_unique(values: Iterable[object], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _derived_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
