import copy
import math
from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    ActivityType,
    CharacterPostureComponent,
    ContentAccessMode,
    ContentEndpoint,
    ContentEndpointComponent,
    DriveComponent,
    PendingTextReceiptsComponent,
    PhysicalRelationKind,
    SpatialParentRelationComponent,
    System1State,
    TextActionExecutionComponent,
    TextActionRequestComponent,
    TextContentPersistenceBinding,
)
from stage0_sim.domain.content import (
    TextArtifact,
    TextAttribution,
    TextAttributionDisplay,
    TextContentError,
    TextContentRegistry,
    TextDeliveryResult,
    TextOperation,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import action_lineage_payload
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.interactions import (
    is_at_interaction_approach,
    physical_object_is_exposed,
)
from stage0_sim.domain.text_actions import (
    TextReadSpecification,
    TextWriteSpecification,
)


@dataclass(frozen=True, slots=True)
class TextActionExecutionSystem:
    name: str = "text_action_execution"
    order: int = 146

    def update(self, context: SystemContext) -> None:
        active = tuple(
            context.registry.query_entities(TextActionExecutionComponent)
        )
        for actor_id in active:
            self._advance(context, actor_id)
        for actor_id in context.registry.query_entities(
            TextActionRequestComponent
        ):
            if actor_id in active or context.registry.has_component(
                actor_id, TextActionExecutionComponent
            ):
                continue
            request = context.registry.get_component(
                actor_id, TextActionRequestComponent
            )
            if request.status != "requested":
                continue
            self._start(context, actor_id, request)

    def _start(
        self,
        context: SystemContext,
        actor_id: str,
        request: TextActionRequestComponent,
    ) -> None:
        family = "read" if request.read is not None else "write"
        specification = request.read or request.write
        if specification is None:
            raise AssertionError("text request lost its specification")
        requested = context.events.emit(
            f"text.{family}_requested",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(request),
                **action_lineage_payload(request.action_instance),
            },
            correlation_id=_correlation_id(request),
        )
        failure = _endpoint_failure(
            context,
            actor_id,
            specification,
            _request_operation(request),
        )
        if failure is not None:
            self._fail_request(
                context,
                actor_id,
                request,
                failure,
                causation_id=requested.event_id,
            )
            return
        operation_id = (
            f"text-operation:{request.action_instance.action_id}"
            if request.action_instance is not None
            else context.registry.get_resource(
                TextContentRegistry
            ).next_operation_id()
        )
        pinned_receipt = None
        if request.read is not None:
            candidate = copy.deepcopy(
                context.registry.get_resource(TextContentRegistry)
            )
            try:
                pinned_receipt = candidate.read_current(
                    artifact_id=request.read.artifact_id,
                    actor_id=actor_id,
                    endpoint_id=request.read.endpoint_id,
                    target_id=request.read.target_id,
                    simulation_time=context.clock.simulation_time,
                    operation_id=operation_id,
                    block_ids=request.read.block_ids,
                )
            except TextContentError as error:
                self._fail_request(
                    context,
                    actor_id,
                    request,
                    error.reason,
                    causation_id=requested.event_id,
                )
                return
        duration = _duration_seconds(
            len(pinned_receipt.rendered_text)
            if pinned_receipt is not None
            else _write_character_count(request.write)
        )
        started = context.events.emit(
            f"text.{family}_started",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(request),
                "duration": duration,
                "pinned_revision": (
                    pinned_receipt.artifact_revision
                    if pinned_receipt is not None
                    else None
                ),
                **action_lineage_payload(request.action_instance),
            },
            causation_id=requested.event_id,
            correlation_id=_correlation_id(request),
        )
        previous_activity = None
        if context.registry.has_component(actor_id, ActivityComponent):
            activity = context.registry.get_component(
                actor_id, ActivityComponent
            )
            previous_activity = activity.current
            activity.current = (
                ActivityType.READING
                if request.read is not None
                else ActivityType.WRITING
            )
            activity.previous = None
            activity.movement_override = False
            if activity.current is not previous_activity:
                context.events.emit(
                    "activity.changed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=actor_id,
                    payload={
                        "previous": previous_activity.value,
                        "current": activity.current.value,
                        "reason": "text_action_started",
                        **action_lineage_payload(request.action_instance),
                    },
                    causation_id=started.event_id,
                    correlation_id=_correlation_id(request),
                )
        context.registry.add_component(
            actor_id,
            TextActionExecutionComponent(
                read=request.read,
                write=request.write,
                duration=duration,
                operation_id=operation_id,
                pinned_revision=(
                    pinned_receipt.artifact_revision
                    if pinned_receipt is not None
                    else None
                ),
                pinned_receipt=pinned_receipt,
                previous_activity=previous_activity,
                action_instance=request.action_instance,
            ),
        )
        request.status = "running"
        self._advance(context, actor_id, causation_id=started.event_id)

    def _advance(
        self,
        context: SystemContext,
        actor_id: str,
        *,
        causation_id: str | None = None,
    ) -> None:
        execution = context.registry.get_component(
            actor_id, TextActionExecutionComponent
        )
        operation = (
            TextOperation.READ
            if execution.read is not None
            else execution.write.operation
            if execution.write is not None
            else TextOperation.READ
        )
        specification = execution.read or execution.write
        if specification is None:
            raise AssertionError("text execution lost its specification")
        failure = _endpoint_failure(
            context,
            actor_id,
            specification,
            operation,
        )
        if failure is not None:
            self._cancel(context, actor_id, execution, failure)
            return
        execution.elapsed = round(
            min(execution.duration, execution.elapsed + context.clock.dt),
            12,
        )
        if execution.elapsed < execution.duration:
            return
        if execution.read is not None:
            self._complete_read(context, actor_id, execution, causation_id)
            return
        self._complete_write(context, actor_id, execution, causation_id)

    def _complete_read(
        self,
        context: SystemContext,
        actor_id: str,
        execution: TextActionExecutionComponent,
        causation_id: str | None,
    ) -> None:
        receipt = execution.pinned_receipt
        if receipt is None:
            self._cancel(context, actor_id, execution, "read_snapshot_missing")
            return
        if context.registry.has_component(
            actor_id, PendingTextReceiptsComponent
        ):
            pending = context.registry.get_component(
                actor_id, PendingTextReceiptsComponent
            )
            pending.receipts.append(receipt)
        else:
            context.registry.add_component(
                actor_id,
                PendingTextReceiptsComponent([receipt]),
            )
        event = context.events.emit(
            "text.read_completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                "target_id": receipt.target_id,
                "endpoint_id": receipt.endpoint_id,
                "artifact_id": receipt.artifact_id,
                "artifact_revision": receipt.artifact_revision,
                "block_ids": list(receipt.block_ids),
                "content_hash": receipt.content_hash,
                "rendered_hash": receipt.rendered_hash,
                "text_length": len(receipt.rendered_text),
                **action_lineage_payload(execution.action_instance),
            },
            causation_id=causation_id,
            correlation_id=_correlation_id(execution),
        )
        self._finish_component(context, actor_id, "completed", None)
        _bump_actor_revision(context, actor_id)
        del event

    def _complete_write(
        self,
        context: SystemContext,
        actor_id: str,
        execution: TextActionExecutionComponent,
        causation_id: str | None,
    ) -> None:
        specification = execution.write
        if specification is None:
            raise AssertionError("write execution lost its specification")
        live = context.registry.get_resource(TextContentRegistry)
        candidate = copy.deepcopy(live)
        try:
            result = _apply_write(
                candidate,
                actor_id,
                specification,
                execution.operation_id,
                context.clock.tick,
                context.clock.simulation_time,
                _endpoint(
                    context,
                    specification.target_id,
                    specification.endpoint_id,
                ),
            )
            if context.registry.has_resource(TextContentPersistenceBinding):
                context.registry.get_resource(
                    TextContentPersistenceBinding
                ).save_snapshot(
                    context.events.run_id,
                    candidate.to_dict(),
                )
        except TextContentError as error:
            self._cancel(context, actor_id, execution, error.reason)
            return
        context.registry.set_resource(candidate)
        artifact = (
            result.artifact
            if isinstance(result, TextDeliveryResult)
            else result
        )
        if isinstance(result, TextDeliveryResult):
            context.events.emit(
                "text.delivery_completed",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=actor_id,
                payload={
                    "artifact_id": result.artifact.id,
                    "artifact_revision": result.artifact.current_revision,
                    "recipient_collection_id": result.recipient_collection.id,
                    "sent_collection_id": result.sent_collection.id,
                    "recipient_address_id": (
                        specification.recipient_address_id
                    ),
                    "unread_count": result.unread_count,
                    **action_lineage_payload(execution.action_instance),
                },
                causation_id=causation_id,
                correlation_id=_correlation_id(execution),
            )
        context.events.emit(
            "text.write_completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                "target_id": specification.target_id,
                "endpoint_id": specification.endpoint_id,
                "operation": specification.operation.value,
                "artifact_id": artifact.id,
                "artifact_revision": artifact.current_revision,
                "content_hash": artifact.current.content_hash,
                "text_length": sum(
                    len(block.text)
                    for block in artifact.current.blocks
                    if not block.tombstone
                ),
                "display_attribution": _display_attribution(
                    artifact.current.attribution
                ),
                **action_lineage_payload(execution.action_instance),
            },
            causation_id=causation_id,
            correlation_id=_correlation_id(execution),
        )
        self._finish_component(context, actor_id, "completed", None)
        _bump_actor_revision(context, actor_id)

    def _fail_request(
        self,
        context: SystemContext,
        actor_id: str,
        request: TextActionRequestComponent,
        reason: str,
        *,
        causation_id: str | None,
    ) -> None:
        family = "read" if request.read is not None else "write"
        context.events.emit(
            f"text.{family}_failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(request),
                "reason": reason,
                **action_lineage_payload(request.action_instance),
            },
            causation_id=causation_id,
            correlation_id=_correlation_id(request),
        )
        request.status = "failed"
        request.failure_reason = reason

    def _cancel(
        self,
        context: SystemContext,
        actor_id: str,
        execution: TextActionExecutionComponent,
        reason: str,
    ) -> None:
        family = "read" if execution.read is not None else "write"
        context.events.emit(
            f"text.{family}_cancelled",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=actor_id,
            payload={
                **_specification_payload(execution),
                "reason": reason,
                "elapsed": execution.elapsed,
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=_correlation_id(execution),
        )
        self._finish_component(context, actor_id, "failed", reason)

    @staticmethod
    def _finish_component(
        context: SystemContext,
        actor_id: str,
        status: str,
        reason: str | None,
    ) -> None:
        if context.registry.has_component(
            actor_id, TextActionRequestComponent
        ):
            request = context.registry.get_component(
                actor_id, TextActionRequestComponent
            )
            request.status = status
            request.failure_reason = reason
        execution = context.registry.get_component(
            actor_id, TextActionExecutionComponent
        )
        if (
            execution.previous_activity is not None
            and context.registry.has_component(actor_id, ActivityComponent)
        ):
            activity = context.registry.get_component(
                actor_id, ActivityComponent
            )
            previous = activity.current
            activity.current = execution.previous_activity
            if previous is not activity.current:
                context.events.emit(
                    "activity.changed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=actor_id,
                    payload={
                        "previous": previous.value,
                        "current": activity.current.value,
                        "reason": f"text_action_{status}",
                        **action_lineage_payload(execution.action_instance),
                    },
                    correlation_id=_correlation_id(execution),
                )
        context.registry.remove_component(
            actor_id, TextActionExecutionComponent
        )


def cancel_text_action(
    context: SystemContext,
    actor_id: str,
    reason: str,
) -> None:
    if context.registry.has_component(
        actor_id, TextActionExecutionComponent
    ):
        TextActionExecutionSystem()._cancel(
            context,
            actor_id,
            context.registry.get_component(
                actor_id, TextActionExecutionComponent
            ),
            reason,
        )
    elif context.registry.has_component(
        actor_id, TextActionRequestComponent
    ):
        request = context.registry.get_component(
            actor_id, TextActionRequestComponent
        )
        if request.status == "requested":
            TextActionExecutionSystem()._fail_request(
                context,
                actor_id,
                request,
                reason,
                causation_id=None,
            )


def _apply_write(
    registry: TextContentRegistry,
    actor_id: str,
    specification: TextWriteSpecification,
    operation_id: str,
    simulation_tick: int,
    simulation_time: float,
    endpoint: ContentEndpoint,
) -> TextArtifact | TextDeliveryResult:
    attribution = TextAttribution(
        authoritative_actor_id=actor_id,
        display=specification.attribution.display,
        sender_address_id=specification.attribution.sender_address_id,
        display_label=(
            specification.attribution.display_label
            if specification.attribution.display_label is not None
            else actor_id
            if specification.attribution.display is TextAttributionDisplay.VERIFIED
            and specification.attribution.sender_address_id is None
            else None
        ),
    )
    if specification.operation is TextOperation.CREATE:
        if specification.recipient_address_id is not None:
            sender_address_id = attribution.sender_address_id
            if sender_address_id is None:
                raise TextContentError(
                    "sender_not_authorized",
                    "message creation requires a sender address",
                )
            return registry.send_message(
                sender_address_id=sender_address_id,
                recipient_address_id=specification.recipient_address_id,
                expected_recipient_collection_revision=_required(
                    specification.expected_collection_revision
                ),
                expected_sent_collection_revision=_required(
                    specification.expected_sent_collection_revision
                ),
                blocks=specification.blocks,
                attribution=attribution,
                actor_id=actor_id,
                simulation_tick=simulation_tick,
                simulation_time=simulation_time,
                operation_id=operation_id,
                sender_sent_collection_id=endpoint.resource_id,
                artifact_id=specification.artifact_id_hint,
            )
        if (
            endpoint.created_media_kind is None
            or endpoint.created_mode is None
            or endpoint.created_access_policy is None
        ):
            raise TextContentError(
                "invalid_operation",
                "endpoint has no created artifact policy",
            )
        return registry.create_artifact_in_collection(
            collection_id=endpoint.resource_id,
            expected_collection_revision=_required(
                specification.expected_collection_revision
            ),
            media_kind=endpoint.created_media_kind,
            mode=endpoint.created_mode,
            blocks=specification.blocks,
            access_policy=endpoint.created_access_policy,
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
            artifact_id=specification.artifact_id_hint,
        )
    artifact_id = specification.artifact_id
    if artifact_id is None:
        raise TextContentError("invalid_operation", "artifact ID is required")
    if specification.operation is TextOperation.APPEND:
        return registry.append_blocks(
            artifact_id=artifact_id,
            expected_artifact_revision=_required(
                specification.expected_artifact_revision
            ),
            blocks=specification.blocks,
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
        )
    if specification.operation is TextOperation.REPLACE:
        return registry.replace_block(
            artifact_id=artifact_id,
            block_id=_required_text(specification.block_id),
            expected_artifact_revision=_required(
                specification.expected_artifact_revision
            ),
            expected_block_revision=_required(
                specification.expected_block_revision
            ),
            text=_required_text(specification.text),
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
        )
    if specification.operation is TextOperation.EDIT:
        return registry.edit_block(
            artifact_id=artifact_id,
            block_id=_required_text(specification.block_id),
            expected_artifact_revision=_required(
                specification.expected_artifact_revision
            ),
            expected_block_revision=_required(
                specification.expected_block_revision
            ),
            start=_required(specification.start),
            end=_required(specification.end),
            replacement=_required_text(specification.text, allow_empty=True),
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
        )
    if specification.block_id is not None:
        return registry.tombstone_block(
            artifact_id=artifact_id,
            block_id=specification.block_id,
            expected_artifact_revision=_required(
                specification.expected_artifact_revision
            ),
            expected_block_revision=_required(
                specification.expected_block_revision
            ),
            attribution=attribution,
            actor_id=actor_id,
            simulation_tick=simulation_tick,
            simulation_time=simulation_time,
            operation_id=operation_id,
        )
    return registry.tombstone_artifact(
        artifact_id=artifact_id,
        expected_artifact_revision=_required(
            specification.expected_artifact_revision
        ),
        attribution=attribution,
        actor_id=actor_id,
        simulation_tick=simulation_tick,
        simulation_time=simulation_time,
        operation_id=operation_id,
    )


def _endpoint_failure(
    context: SystemContext,
    actor_id: str,
    specification: TextReadSpecification | TextWriteSpecification,
    operation: TextOperation,
) -> str | None:
    if (
        context.registry.has_component(actor_id, DriveComponent)
        and context.registry.get_component(actor_id, DriveComponent).state
        is not System1State.NORMAL
    ):
        return "system1_preemption"
    try:
        endpoint = _endpoint(
            context,
            specification.target_id,
            specification.endpoint_id,
        )
    except KeyError:
        return "endpoint_not_found"
    if operation not in endpoint.operations:
        return "operation_not_supported"
    if not content_endpoint_accessible(
        context,
        actor_id,
        specification.target_id,
        endpoint,
    ):
        return "endpoint_not_accessible"
    content = context.registry.get_resource(TextContentRegistry)
    artifact_id = (
        specification.artifact_id
        if isinstance(specification, TextReadSpecification)
        else specification.artifact_id
    )
    if endpoint.kind.value == "artifact":
        if artifact_id != endpoint.resource_id:
            return "artifact_not_available"
        try:
            content.artifact(endpoint.resource_id)
        except TextContentError:
            return "artifact_not_found"
    elif artifact_id is not None and operation is not TextOperation.CREATE:
        try:
            collection = content.collection(endpoint.resource_id)
        except TextContentError:
            return "collection_not_found"
        if artifact_id not in collection.members:
            return "artifact_not_available"
    return None


def _endpoint(
    context: SystemContext,
    target_id: str,
    endpoint_id: str,
) -> ContentEndpoint:
    if target_id not in context.registry.entities() or not context.registry.has_component(
        target_id, ContentEndpointComponent
    ):
        raise KeyError(target_id)
    return context.registry.get_component(
        target_id, ContentEndpointComponent
    ).endpoint(endpoint_id)


def content_endpoint_accessible(
    context: SystemContext,
    actor_id: str,
    target_id: str,
    endpoint: ContentEndpoint,
) -> bool:
    if endpoint.access_mode is ContentAccessMode.LOGICAL:
        return True
    if not physical_object_is_exposed(context.registry, target_id):
        return False
    held = (
        context.registry.has_component(
            target_id, SpatialParentRelationComponent
        )
        and (
            relation := context.registry.get_component(
                target_id, SpatialParentRelationComponent
            )
        ).kind
        is PhysicalRelationKind.HELD_BY
        and relation.parent_id == actor_id
    )
    if endpoint.access_mode is ContentAccessMode.HELD:
        return held
    reachable = held or is_at_interaction_approach(
        context.registry, actor_id, target_id
    )
    if endpoint.access_mode in {
        ContentAccessMode.EXPOSED_REACHABLE,
        ContentAccessMode.HELD_OR_REACHABLE,
    }:
        return reachable
    if endpoint.access_mode is ContentAccessMode.OCCUPIED_TERMINAL:
        return (
            context.registry.has_component(
                actor_id, CharacterPostureComponent
            )
            and context.registry.get_component(
                actor_id, CharacterPostureComponent
            ).support_id
            == target_id
        )
    return False


def _specification_payload(
    value: TextActionRequestComponent | TextActionExecutionComponent,
) -> dict[str, JsonValue]:
    read = value.read
    if read is not None:
        return {
            "target_id": read.target_id,
            "endpoint_id": read.endpoint_id,
            "artifact_id": read.artifact_id,
            "block_ids": list(read.block_ids),
        }
    write = value.write
    if write is None:
        return {}
    return {
        "target_id": write.target_id,
        "endpoint_id": write.endpoint_id,
        "operation": write.operation.value,
        "artifact_id": write.artifact_id,
        "block_id": write.block_id,
        "recipient_address_id": write.recipient_address_id,
        "expected_artifact_revision": write.expected_artifact_revision,
        "expected_block_revision": write.expected_block_revision,
        "expected_collection_revision": write.expected_collection_revision,
        "expected_sent_collection_revision": (
            write.expected_sent_collection_revision
        ),
        "text_length": _write_character_count(write),
        "attribution_display": write.attribution.display.value,
    }


def _write_character_count(specification: TextWriteSpecification | None) -> int:
    if specification is None:
        return 0
    return sum(len(block.text) for block in specification.blocks) + len(
        specification.text or ""
    )


def _duration_seconds(character_count: int) -> float:
    return float(max(1, min(300, math.ceil(max(1, character_count) / 120))))


def _display_attribution(
    attribution: TextAttribution,
) -> dict[str, JsonValue]:
    return {
        "mode": attribution.display.value,
        "sender_address_id": attribution.sender_address_id,
        "display_label": attribution.display_label,
        "verified": attribution.display is TextAttributionDisplay.VERIFIED,
    }


def _correlation_id(
    value: TextActionRequestComponent | TextActionExecutionComponent,
) -> str | None:
    return (
        value.action_instance.root_correlation_id
        if value.action_instance is not None
        else None
    )


def _required(value: int | None) -> int:
    if value is None:
        raise TextContentError("invalid_operation", "required revision is missing")
    return value


def _required_text(value: str | None, *, allow_empty: bool = False) -> str:
    if value is None or (not allow_empty and not value):
        raise TextContentError("invalid_operation", "required text value is missing")
    return value


def _request_operation(
    request: TextActionRequestComponent,
) -> TextOperation:
    if request.read is not None:
        return TextOperation.READ
    if request.write is None:
        raise AssertionError("text request lost its write specification")
    return request.write.operation


def _bump_actor_revision(context: SystemContext, actor_id: str) -> None:
    from stage0_sim.domain.components import ControllerComponent

    if context.registry.has_component(actor_id, ControllerComponent):
        context.registry.get_component(
            actor_id, ControllerComponent
        ).state_revision += 1
