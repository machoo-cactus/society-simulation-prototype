import json
from dataclasses import FrozenInstanceError

import pytest

from stage0_sim.domain.content import (
    TextAccessGrant,
    TextAccessPolicy,
    TextAddress,
    TextArtifactMode,
    TextAttribution,
    TextAttributionDisplay,
    TextBlockDraft,
    TextBlockKind,
    TextCollection,
    TextCollectionKind,
    TextContentError,
    TextContentRegistry,
    TextMediaKind,
    TextOperation,
    TextPrincipal,
)

ACTOR = "writer"
READER = "reader"


def _policy(
    principal: TextPrincipal,
    *operations: TextOperation,
) -> TextAccessPolicy:
    return TextAccessPolicy(
        tuple(TextAccessGrant(operation, (principal,)) for operation in operations)
    )


def _attribution(actor_id: str = ACTOR) -> TextAttribution:
    return TextAttribution(
        authoritative_actor_id=actor_id,
        display=TextAttributionDisplay.VERIFIED,
        display_label=actor_id,
    )


def _registry_with_document() -> tuple[TextContentRegistry, str, str]:
    writer = TextPrincipal.character(ACTOR)
    collection = TextCollection(
        id="documents",
        kind=TextCollectionKind.DOCUMENT_SET,
        revision=1,
        members=(),
        capacity=10,
        access_policy=_policy(writer, TextOperation.CREATE, TextOperation.LIST),
    )
    registry = TextContentRegistry(collections=(collection,))
    artifact = registry.create_artifact_in_collection(
        collection_id=collection.id,
        expected_collection_revision=1,
        media_kind=TextMediaKind.DOCUMENT,
        mode=TextArtifactMode.MUTABLE,
        blocks=(
            TextBlockDraft("Heading\r\nOne", TextBlockKind.TITLE),
            TextBlockDraft("A😀B", TextBlockKind.PARAGRAPH),
        ),
        access_policy=_policy(
            TextPrincipal.public(),
            TextOperation.READ,
            TextOperation.APPEND,
            TextOperation.REPLACE,
            TextOperation.EDIT,
            TextOperation.DELETE,
        ),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=1,
        simulation_time=1.0,
        operation_id="create-document",
    )
    return registry, artifact.id, artifact.current.blocks[1].id


def test_read_receipt_is_immutable_and_pinned_to_exact_revision() -> None:
    registry, artifact_id, block_id = _registry_with_document()
    receipt = registry.read_current(
        artifact_id=artifact_id,
        actor_id=READER,
        endpoint_id="terminal",
        target_id="documents",
        simulation_time=2.0,
        operation_id="read-one",
    )
    registry.replace_block(
        artifact_id=artifact_id,
        block_id=block_id,
        expected_artifact_revision=1,
        expected_block_revision=1,
        text="changed",
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="replace-one",
    )

    assert receipt.artifact_revision == 1
    assert receipt.rendered_text == "Heading\nOne\nA😀B"
    assert receipt.content_hash == registry.artifact(artifact_id).history[0].content_hash
    with pytest.raises(FrozenInstanceError):
        receipt.rendered_text = "mutated"  # type: ignore[misc]


def test_read_selects_requested_live_blocks_in_artifact_order() -> None:
    registry, artifact_id, second_block_id = _registry_with_document()
    first_block_id = registry.artifact(artifact_id).current.blocks[0].id
    receipt = registry.read_current(
        artifact_id=artifact_id,
        actor_id=READER,
        endpoint_id="terminal",
        target_id="documents",
        simulation_time=2.0,
        block_ids=(second_block_id, first_block_id),
        operation_id="select-blocks",
    )

    assert receipt.block_ids == (first_block_id, second_block_id)
    assert receipt.rendered_text == "Heading\nOne\nA😀B"
    with pytest.raises(ValueError, match="unique"):
        registry.read_current(
            artifact_id=artifact_id,
            actor_id=READER,
            endpoint_id="terminal",
            target_id="documents",
            simulation_time=2.0,
            block_ids=(first_block_id, first_block_id),
            operation_id="duplicate-blocks",
        )

    registry.tombstone_block(
        artifact_id=artifact_id,
        block_id=second_block_id,
        expected_artifact_revision=1,
        expected_block_revision=1,
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="delete-selected-block",
    )
    for requested_id in (second_block_id, "unknown-block"):
        with pytest.raises(TextContentError) as caught:
            registry.read_current(
                artifact_id=artifact_id,
                actor_id=READER,
                endpoint_id="terminal",
                target_id="documents",
                simulation_time=3.0,
                block_ids=(requested_id,),
                operation_id=f"read-{requested_id}",
            )
        assert caught.value.reason == "not_found"


def test_all_mutations_preserve_order_history_and_tombstones() -> None:
    registry, artifact_id, block_id = _registry_with_document()
    appended = registry.append_blocks(
        artifact_id=artifact_id,
        expected_artifact_revision=1,
        blocks=("tail",),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="append-one",
    )
    replaced = registry.replace_block(
        artifact_id=artifact_id,
        block_id=block_id,
        expected_artifact_revision=2,
        expected_block_revision=1,
        text="replacement",
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=3,
        simulation_time=3.0,
        operation_id="replace-two",
    )
    edited = registry.edit_block(
        artifact_id=artifact_id,
        block_id=block_id,
        expected_artifact_revision=3,
        expected_block_revision=2,
        start=0,
        end=7,
        replacement="new",
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=4,
        simulation_time=4.0,
        operation_id="edit-one",
    )
    deleted_block = registry.tombstone_block(
        artifact_id=artifact_id,
        block_id=block_id,
        expected_artifact_revision=4,
        expected_block_revision=3,
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=5,
        simulation_time=5.0,
        operation_id="delete-block",
    )
    deleted_artifact = registry.tombstone_artifact(
        artifact_id=artifact_id,
        expected_artifact_revision=5,
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=6,
        simulation_time=6.0,
        operation_id="delete-artifact",
    )

    assert appended.current.blocks[-1].text == "tail"
    assert replaced.current.blocks[1].text == "replacement"
    assert edited.current.blocks[1].text == "newment"
    assert deleted_block.current.blocks[1].tombstone
    assert deleted_block.history[0].blocks[1].text == "A😀B"
    assert deleted_artifact.tombstone
    assert [item.revision for item in deleted_artifact.history] == [1, 2, 3, 4, 5, 6]
    with pytest.raises(TextContentError, match="deleted") as caught:
        registry.read_current(
            artifact_id=artifact_id,
            actor_id=READER,
            endpoint_id="terminal",
            target_id="documents",
            simulation_time=7.0,
        )
    assert caught.value.reason == "deleted"


def test_edit_indices_are_unicode_code_points() -> None:
    registry, artifact_id, block_id = _registry_with_document()
    result = registry.edit_block(
        artifact_id=artifact_id,
        block_id=block_id,
        expected_artifact_revision=1,
        expected_block_revision=1,
        start=1,
        end=2,
        replacement="界",
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="unicode-edit",
    )

    assert result.current.blocks[1].text == "A界B"
    assert result.current.blocks[1].revision == 2


def test_access_policy_supports_character_group_address_and_public() -> None:
    policy = TextAccessPolicy.from_mapping(
        {
            TextOperation.READ: (TextPrincipal.public(),),
            TextOperation.EDIT: (TextPrincipal.group("editors"),),
            TextOperation.SEND: (TextPrincipal.address("sender@example"),),
        }
    )

    assert policy.allows(TextOperation.READ, ACTOR)
    assert policy.allows(TextOperation.EDIT, ACTOR, ("editors",))
    assert policy.allows(
        TextOperation.SEND,
        ACTOR,
        controlled_address_ids=("sender@example",),
    )
    assert not policy.allows(TextOperation.DELETE, ACTOR)


def test_two_writes_from_same_revision_conflict_without_partial_change() -> None:
    registry, artifact_id, _ = _registry_with_document()
    first = registry.append_blocks(
        artifact_id=artifact_id,
        expected_artifact_revision=1,
        blocks=("first",),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="first-writer",
    )

    with pytest.raises(TextContentError) as caught:
        registry.append_blocks(
            artifact_id=artifact_id,
            expected_artifact_revision=1,
            blocks=("second",),
            attribution=_attribution(),
            actor_id=ACTOR,
            simulation_tick=2,
            simulation_time=2.0,
            operation_id="second-writer",
        )

    assert caught.value.reason == "revision_conflict"
    assert registry.artifact(artifact_id) == first
    assert [block.text for block in first.current.blocks][-1] == "first"


def _mail_registry(*, mailbox_capacity: int = 2) -> TextContentRegistry:
    sender = TextPrincipal.character(ACTOR)
    recipient = TextPrincipal.character(READER)
    inbox = TextCollection(
        id="reader-inbox",
        kind=TextCollectionKind.MAILBOX,
        revision=1,
        members=(),
        capacity=mailbox_capacity,
        access_policy=_policy(recipient, TextOperation.RECEIVE),
    )
    sent = TextCollection(
        id="writer-sent",
        kind=TextCollectionKind.SENT,
        revision=1,
        members=(),
        capacity=2,
        access_policy=_policy(sender, TextOperation.SEND),
    )
    return TextContentRegistry(
        collections=(inbox, sent),
        addresses=(
            TextAddress(
                id="writer-address",
                owner=sender,
                mailbox_id="writer-sent",
                sent_collection_id="writer-sent",
                display_label="Writer",
                accepted_senders=(TextPrincipal.public(),),
            ),
            TextAddress(
                id="reader-address",
                owner=recipient,
                mailbox_id="reader-inbox",
                display_label="Reader",
                accepted_senders=(sender,),
            ),
        ),
    )


def _message_attribution() -> TextAttribution:
    return TextAttribution(
        authoritative_actor_id=ACTOR,
        display=TextAttributionDisplay.VERIFIED,
        sender_address_id="writer-address",
        display_label="Writer",
    )


def test_failed_message_delivery_is_atomic() -> None:
    registry = _mail_registry()

    with pytest.raises(TextContentError) as caught:
        registry.send_message(
            sender_address_id="writer-address",
            recipient_address_id="reader-address",
            expected_recipient_collection_revision=99,
            expected_sent_collection_revision=1,
            blocks=("hello",),
            attribution=_message_attribution(),
            actor_id=ACTOR,
            simulation_tick=1,
            simulation_time=1.0,
            operation_id="failed-delivery",
        )

    assert caught.value.reason == "revision_conflict"
    assert registry.artifacts == {}
    assert registry.collection("reader-inbox").members == ()
    assert registry.collection("writer-sent").members == ()
    assert registry.unread_count("reader-address") == 0


def test_successful_message_delivery_updates_both_collections_and_unread() -> None:
    registry = _mail_registry()
    result = registry.send_message(
        sender_address_id="writer-address",
        recipient_address_id="reader-address",
        expected_recipient_collection_revision=1,
        expected_sent_collection_revision=1,
        blocks=("hello\r\nreader",),
        attribution=_message_attribution(),
        actor_id=ACTOR,
        simulation_tick=1,
        simulation_time=1.0,
        operation_id="successful-delivery",
    )

    assert result.artifact.mode is TextArtifactMode.IMMUTABLE
    assert result.artifact.current.blocks[0].text == "hello\nreader"
    assert result.recipient_collection.members == (result.artifact.id,)
    assert result.sent_collection.members == (result.artifact.id,)
    assert registry.unread_count("reader-address") == 1


def test_attribution_display_validation_and_sender_authority() -> None:
    with pytest.raises(ValueError, match="anonymous"):
        TextAttribution(
            authoritative_actor_id=ACTOR,
            display=TextAttributionDisplay.ANONYMOUS,
            display_label="Not anonymous",
        )
    registry = _mail_registry()
    forged = TextAttribution(
        authoritative_actor_id=ACTOR,
        display=TextAttributionDisplay.VERIFIED,
        sender_address_id="reader-address",
    )

    with pytest.raises(TextContentError) as caught:
        registry.send_message(
            sender_address_id="writer-address",
            recipient_address_id="reader-address",
            expected_recipient_collection_revision=1,
            expected_sent_collection_revision=1,
            blocks=("hello",),
            attribution=forged,
            actor_id=ACTOR,
            simulation_tick=1,
            simulation_time=1.0,
            operation_id="forged-delivery",
        )
    assert caught.value.reason == "sender_not_authorized"


def test_duplicate_operation_id_is_idempotent_before_revision_checks() -> None:
    registry, artifact_id, _ = _registry_with_document()
    first = registry.append_blocks(
        artifact_id=artifact_id,
        expected_artifact_revision=1,
        blocks=("once",),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="idempotent-append",
    )
    duplicate = registry.append_blocks(
        artifact_id=artifact_id,
        expected_artifact_revision=1,
        blocks=("would otherwise conflict",),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=9,
        simulation_time=9.0,
        operation_id="idempotent-append",
    )

    assert duplicate is first
    assert registry.artifact(artifact_id).current_revision == 2
    assert registry.artifact(artifact_id).current.blocks[-1].text == "once"


def test_registry_json_round_trip_preserves_authoritative_state() -> None:
    registry = _mail_registry()
    registry.register_group("correspondents", (READER, ACTOR))
    registry.register_collection(
        TextCollection(
            id="shared-documents",
            kind=TextCollectionKind.DOCUMENT_SET,
            revision=1,
            members=(),
            capacity=4,
            access_policy=_policy(
                TextPrincipal.group("correspondents"),
                TextOperation.CREATE,
            ),
        )
    )
    document = registry.create_artifact_in_collection(
        collection_id="shared-documents",
        expected_collection_revision=1,
        media_kind=TextMediaKind.NOTE,
        mode=TextArtifactMode.MUTABLE,
        blocks=("first revision",),
        access_policy=_policy(
            TextPrincipal.group("correspondents"),
            TextOperation.READ,
            TextOperation.APPEND,
        ),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=1,
        simulation_time=1.0,
        operation_id="round-trip-create",
    )
    revised = registry.append_blocks(
        artifact_id=document.id,
        expected_artifact_revision=1,
        blocks=("second revision",),
        attribution=_attribution(),
        actor_id=ACTOR,
        simulation_tick=2,
        simulation_time=2.0,
        operation_id="round-trip-append",
    )
    delivery = registry.send_message(
        sender_address_id="writer-address",
        recipient_address_id="reader-address",
        expected_recipient_collection_revision=1,
        expected_sent_collection_revision=1,
        blocks=("persist me",),
        attribution=_message_attribution(),
        actor_id=ACTOR,
        simulation_tick=3,
        simulation_time=3.0,
        operation_id="round-trip-message",
    )
    assert registry.next_operation_id() == "operation-000000000001"

    payload = registry.to_dict()
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    restored = TextContentRegistry.from_dict(json.loads(encoded))

    assert json.dumps(
        restored.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == encoded
    assert restored.artifact(revised.id) == revised
    assert [item.content_hash for item in restored.artifact(revised.id).history] == [
        item.content_hash for item in revised.history
    ]
    assert restored.artifact(delivery.artifact.id) == delivery.artifact
    assert restored.collection("reader-inbox").members == (
        delivery.artifact.id,
    )
    assert restored.unread_count("reader-address") == 1
    assert restored.groups["correspondents"] == (READER, ACTOR)
    assert restored.address("writer-address") == registry.address("writer-address")
    assert restored.next_operation_id() == "operation-000000000002"
