from dataclasses import dataclass
from enum import StrEnum

from stage0_sim.domain.content import (
    TextAccessPolicy,
    TextArtifactMode,
    TextMediaKind,
    TextOperation,
)


class ContentEndpointKind(StrEnum):
    ARTIFACT = "artifact"
    COLLECTION = "collection"
    FEED = "feed"
    BOARD = "board"
    MAILBOX = "mailbox"


class ContentAccessMode(StrEnum):
    EXPOSED_REACHABLE = "exposed_reachable"
    HELD_OR_REACHABLE = "held_or_reachable"
    HELD = "held"
    OCCUPIED_TERMINAL = "occupied_terminal"
    LOGICAL = "logical"


@dataclass(frozen=True, slots=True)
class ContentEndpoint:
    id: str
    label: str
    kind: ContentEndpointKind
    resource_id: str
    operations: tuple[TextOperation, ...]
    access_mode: ContentAccessMode = ContentAccessMode.EXPOSED_REACHABLE
    lists_items: bool = False
    originates_messages: bool = False
    notifies_owner: bool = False
    created_media_kind: TextMediaKind | None = None
    created_mode: TextArtifactMode | None = None
    created_access_policy: TextAccessPolicy | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.label or not self.resource_id:
            raise ValueError("content endpoint identity must not be empty")
        if not self.operations or any(
            not isinstance(operation, TextOperation) for operation in self.operations
        ):
            raise ValueError("content endpoint operations must not be empty")
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("content endpoint operations must be unique")
        create_fields = (
            self.created_media_kind,
            self.created_mode,
            self.created_access_policy,
        )
        if TextOperation.CREATE in self.operations:
            if any(value is None for value in create_fields):
                raise ValueError(
                    "create-capable endpoints require media kind, mode, and access policy"
                )
        elif any(value is not None for value in create_fields):
            raise ValueError("created artifact settings require the create operation")


@dataclass(frozen=True, slots=True)
class ContentEndpointComponent:
    endpoints: tuple[ContentEndpoint, ...]

    def __post_init__(self) -> None:
        if not self.endpoints:
            raise ValueError("content endpoint component must not be empty")
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("content endpoint IDs must be unique")

    def endpoint(self, endpoint_id: str) -> ContentEndpoint:
        try:
            return next(endpoint for endpoint in self.endpoints if endpoint.id == endpoint_id)
        except StopIteration as error:
            raise KeyError(f"unknown content endpoint: {endpoint_id}") from error


@dataclass(frozen=True, slots=True)
class KnownTextAddressesComponent:
    address_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not address_id for address_id in self.address_ids):
            raise ValueError("known text address IDs must not be empty")
        if len(self.address_ids) != len(set(self.address_ids)):
            raise ValueError("known text address IDs must be unique")
