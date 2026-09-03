from dataclasses import dataclass

from stage0_sim.domain.content import (
    TextAttributionDisplay,
    TextBlockDraft,
    TextOperation,
)


@dataclass(frozen=True, slots=True)
class TextAttributionRequest:
    display: TextAttributionDisplay = TextAttributionDisplay.VERIFIED
    sender_address_id: str | None = None
    display_label: str | None = None

    def __post_init__(self) -> None:
        if self.sender_address_id == "":
            raise ValueError("sender_address_id must not be empty")
        if self.display_label == "":
            raise ValueError("display_label must not be empty")


@dataclass(frozen=True, slots=True)
class TextReadSpecification:
    target_id: str
    endpoint_id: str
    artifact_id: str
    block_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.target_id or not self.endpoint_id or not self.artifact_id:
            raise ValueError("text read identity must not be empty")
        if any(not block_id for block_id in self.block_ids):
            raise ValueError("text read block IDs must not be empty")
        if len(self.block_ids) != len(set(self.block_ids)):
            raise ValueError("text read block IDs must be unique")


@dataclass(frozen=True, slots=True)
class TextWriteSpecification:
    operation: TextOperation
    target_id: str
    endpoint_id: str
    attribution: TextAttributionRequest
    artifact_id: str | None = None
    expected_artifact_revision: int | None = None
    expected_collection_revision: int | None = None
    expected_sent_collection_revision: int | None = None
    block_id: str | None = None
    expected_block_revision: int | None = None
    blocks: tuple[TextBlockDraft, ...] = ()
    text: str | None = None
    start: int | None = None
    end: int | None = None
    recipient_address_id: str | None = None
    artifact_id_hint: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in {
            TextOperation.CREATE,
            TextOperation.APPEND,
            TextOperation.REPLACE,
            TextOperation.EDIT,
            TextOperation.DELETE,
        }:
            raise ValueError("unsupported text write operation")
        if not self.target_id or not self.endpoint_id:
            raise ValueError("text write target and endpoint must not be empty")
        if self.artifact_id == "":
            raise ValueError("artifact_id must not be empty")
        if self.block_id == "":
            raise ValueError("block_id must not be empty")
        if self.recipient_address_id == "":
            raise ValueError("recipient_address_id must not be empty")
        if self.artifact_id_hint == "":
            raise ValueError("artifact_id_hint must not be empty")
        if self.operation is TextOperation.CREATE:
            if self.expected_collection_revision is None or not self.blocks:
                raise ValueError(
                    "create requires expected_collection_revision and blocks"
                )
            if any(
                value is not None
                for value in (
                    self.artifact_id,
                    self.expected_artifact_revision,
                    self.block_id,
                    self.expected_block_revision,
                    self.text,
                    self.start,
                    self.end,
                )
            ):
                raise ValueError("create contains fields for another operation")
            message_fields = (
                self.recipient_address_id,
                self.expected_sent_collection_revision,
            )
            if any(value is not None for value in message_fields) and any(
                value is None for value in message_fields
            ):
                raise ValueError(
                    "message create requires recipient and sent collection revision"
                )
            return
        if self.artifact_id is None or self.expected_artifact_revision is None:
            raise ValueError(
                f"{self.operation.value} requires artifact_id and expected revision"
            )
        if self.operation is TextOperation.APPEND:
            if not self.blocks:
                raise ValueError("append requires blocks")
            if any(
                value is not None
                for value in (
                    self.block_id,
                    self.expected_block_revision,
                    self.text,
                    self.start,
                    self.end,
                    self.expected_collection_revision,
                    self.expected_sent_collection_revision,
                    self.recipient_address_id,
                    self.artifact_id_hint,
                )
            ):
                raise ValueError("append contains fields for another operation")
            return
        if self.operation in {
            TextOperation.REPLACE,
            TextOperation.EDIT,
        }:
            if (
                self.block_id is None
                or self.expected_block_revision is None
                or self.text is None
            ):
                raise ValueError(
                    f"{self.operation.value} requires block revision and text"
                )
            if self.operation is TextOperation.EDIT:
                if self.start is None or self.end is None:
                    raise ValueError("edit requires start and end")
            elif self.start is not None or self.end is not None:
                raise ValueError("replace does not accept edit indices")
            if self.blocks or any(
                value is not None
                for value in (
                    self.expected_collection_revision,
                    self.expected_sent_collection_revision,
                    self.recipient_address_id,
                    self.artifact_id_hint,
                )
            ):
                raise ValueError(
                    f"{self.operation.value} contains fields for another operation"
                )
            return
        if self.text is not None or self.blocks or any(
            value is not None
            for value in (
                self.start,
                self.end,
                self.expected_collection_revision,
                self.expected_sent_collection_revision,
                self.recipient_address_id,
                self.artifact_id_hint,
            )
        ):
            raise ValueError("delete contains fields for another operation")
        if (self.block_id is None) != (self.expected_block_revision is None):
            raise ValueError(
                "block delete requires both block_id and expected_block_revision"
            )
