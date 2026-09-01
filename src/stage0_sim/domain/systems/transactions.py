from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActionInstance,
    NpcComponent,
    PositionComponent,
    PossessionsComponent,
    TransactionExecutionComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.economy import (
    ItemAmount,
    TransactionOffer,
    TransactionOperation,
    TransactionPoint,
    TransactionPointRegistry,
    apply_exchange,
    can_debit,
)
from stage0_sim.domain.environment import EnvironmentAvailabilityRegistry
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.lineage import (
    action_lineage_payload,
    emit_action_lifecycle,
)
from stage0_sim.domain.npcs import NpcPoolRegistry
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.spatial_context import (
    local_world_for_agent,
    shares_local_map,
)


@dataclass(frozen=True, slots=True)
class TransactionExecutionSystem:
    name: str = "transaction_execution"
    order: int = 185

    def update(self, context: SystemContext) -> None:
        active = tuple(
            context.registry.query_entities(TransactionExecutionComponent)
        )
        for agent_id in active:
            self._advance(context, agent_id)

        for agent_id in context.registry.query_entities(
            TransactionRequestComponent,
            PositionComponent,
            PossessionsComponent,
        ):
            if agent_id in active:
                continue
            request = context.registry.get_component(
                agent_id, TransactionRequestComponent
            )
            if context.registry.has_component(
                agent_id, TransactionExecutionComponent
            ):
                continue
            if request.status == "requested":
                self._prepare_request(context, agent_id, request)
            elif request.status in {
                "awaiting_staff",
                "awaiting_authorization",
            }:
                self._check_timeout(context, agent_id, request)
            elif request.status == "authorized":
                started, failure = self._start(
                    context,
                    agent_id,
                    request.point_id,
                    request.offer_id,
                    source=request.source,
                    operator_id=request.authorized_by,
                    action_instance=request.action_instance,
                )
                if started:
                    request.status = "running"
                    self._advance(context, agent_id)
                else:
                    request.status = "failed"
                    request.failure_reason = failure

    def _prepare_request(
        self,
        context: SystemContext,
        agent_id: str,
        request: TransactionRequestComponent,
    ) -> None:
        resolved = self._resolve(
            context,
            agent_id,
            request.point_id,
            request.offer_id,
        )
        if isinstance(resolved, str):
            self._emit_failure(
                context,
                agent_id,
                request.point_id,
                request.offer_id,
                resolved,
                request.action_instance,
            )
            request.status = "failed"
            request.failure_reason = resolved
            return
        point, offer = resolved
        failure = self._precondition_failure(
            context,
            agent_id,
            point,
            offer,
            check_capacity=True,
            operator_id=None,
            require_staff=False,
        )
        if failure is not None:
            self._emit_failure(
                context,
                agent_id,
                point.id,
                offer.id,
                failure,
                request.action_instance,
            )
            request.status = "failed"
            request.failure_reason = failure
            return
        if not request.request_id:
            request.request_id = (
                f"transaction-request:{agent_id}:{context.clock.tick}:"
                f"{point.id}:{offer.id}"
            )
        request.requested_tick = context.clock.tick
        request.requested_at = context.clock.simulation_time
        context.events.emit(
            "transaction.requested",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "request_id": request.request_id,
                "point_id": point.id,
                "offer_id": offer.id,
                "operation": point.operation.value,
                **action_lineage_payload(request.action_instance),
            },
            correlation_id=(
                request.action_instance.root_correlation_id
                if request.action_instance is not None
                else None
            ),
        )
        if point.operation is TransactionOperation.STAFFED:
            if point.staffing is None:
                raise RuntimeError("staffed transaction point lost staffing")
            request.timeout_at = (
                context.clock.simulation_time
                + point.staffing.request_timeout
            )
            request.status = "awaiting_staff"
            context.events.emit(
                "transaction.awaiting_staff",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "request_id": request.request_id,
                    "point_id": point.id,
                    "offer_id": offer.id,
                    "timeout_at": request.timeout_at,
                    **action_lineage_payload(request.action_instance),
                },
                correlation_id=(
                    request.action_instance.root_correlation_id
                    if request.action_instance is not None
                    else None
                ),
            )
            return
        started, failure = self._start(
            context,
            agent_id,
            point.id,
            offer.id,
            source=request.source,
            operator_id=None,
            action_instance=request.action_instance,
        )
        if started:
            request.status = "running"
            self._advance(context, agent_id)
        else:
            request.status = "failed"
            request.failure_reason = failure

    def _check_timeout(
        self,
        context: SystemContext,
        agent_id: str,
        request: TransactionRequestComponent,
    ) -> None:
        if (
            request.timeout_at is None
            or context.clock.simulation_time < request.timeout_at
        ):
            return
        request.status = "failed"
        request.failure_reason = "transaction_service_timed_out"
        context.events.emit(
            "transaction.timed_out",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "request_id": request.request_id,
                "point_id": request.point_id,
                "offer_id": request.offer_id,
                "operator_id": request.operator_id,
                "reason": request.failure_reason,
                **action_lineage_payload(request.action_instance),
            },
            correlation_id=(
                request.action_instance.root_correlation_id
                if request.action_instance is not None
                else None
            ),
        )
        self._emit_failure(
            context,
            agent_id,
            request.point_id,
            request.offer_id,
            request.failure_reason,
            request.action_instance,
        )

    def _start(
        self,
        context: SystemContext,
        agent_id: str,
        point_id: str,
        offer_id: str,
        *,
        source: str,
        operator_id: str | None,
        action_instance: ActionInstance | None,
    ) -> tuple[bool, str | None]:
        resolved = self._resolve(context, agent_id, point_id, offer_id)
        if isinstance(resolved, str):
            self._emit_failure(
                context,
                agent_id,
                point_id,
                offer_id,
                resolved,
                action_instance,
            )
            return False, resolved
        point, offer = resolved
        failure = self._precondition_failure(
            context,
            agent_id,
            point,
            offer,
            check_capacity=True,
            operator_id=operator_id,
            require_staff=True,
        )
        if failure is not None:
            self._emit_failure(
                context,
                agent_id,
                point.id,
                offer.id,
                failure,
                action_instance,
            )
            return False, failure
        started = context.events.emit(
            "transaction.started",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "point_id": point.id,
                "offer_id": offer.id,
                "duration": offer.duration,
                "character_gives": _amounts_payload(offer.character_gives),
                "character_receives": _amounts_payload(
                    offer.character_receives
                ),
                "operator_id": operator_id,
                **action_lineage_payload(action_instance),
            },
            correlation_id=(
                action_instance.root_correlation_id
                if action_instance is not None
                else None
            ),
        )
        context.registry.add_component(
            agent_id,
            TransactionExecutionComponent(
                point_id=point.id,
                offer=offer,
                elapsed=0.0,
                correlation_id=(
                    action_instance.root_correlation_id
                    if action_instance is not None
                    else started.event_id
                ),
                source=source,
                operator_id=operator_id,
                action_instance=action_instance,
            ),
        )
        return True, None

    def _advance(self, context: SystemContext, agent_id: str) -> None:
        execution = context.registry.get_component(
            agent_id, TransactionExecutionComponent
        )
        resolved = self._resolve(
            context,
            agent_id,
            execution.point_id,
            execution.offer.id,
        )
        if isinstance(resolved, str):
            cancel_transaction(context, agent_id, resolved)
            return
        point, offer = resolved
        failure = self._precondition_failure(
            context,
            agent_id,
            point,
            offer,
            check_capacity=False,
            operator_id=execution.operator_id,
            require_staff=True,
        )
        if failure is not None:
            cancel_transaction(context, agent_id, failure)
            return
        remaining = offer.duration - execution.elapsed
        execution.elapsed = round(
            execution.elapsed + min(context.clock.dt, remaining),
            12,
        )
        context.events.emit(
            "transaction.progressed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "point_id": point.id,
                "offer_id": offer.id,
                "operator_id": execution.operator_id,
                "elapsed": execution.elapsed,
                "duration": offer.duration,
                "progress": round(execution.elapsed / offer.duration, 12),
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if execution.action_instance is not None:
            emit_action_lifecycle(
                context,
                "action.progressed",
                agent_id,
                execution.action_instance,
                {
                    "point_id": point.id,
                    "offer_id": offer.id,
                    "progress": round(execution.elapsed / offer.duration, 12),
                },
            )
        if execution.elapsed >= offer.duration:
            self._complete(context, agent_id, execution, offer)

    @staticmethod
    def _complete(
        context: SystemContext,
        agent_id: str,
        execution: TransactionExecutionComponent,
        offer: TransactionOffer,
    ) -> None:
        possessions = context.registry.get_component(
            agent_id, PossessionsComponent
        )
        point_state = context.registry.get_resource(
            TransactionPointRegistry
        ).state(execution.point_id)
        character_before = _holdings_payload(possessions.holdings)
        point_before = _holdings_payload(point_state.holdings)
        apply_exchange(possessions.holdings, point_state.holdings, offer)
        context.events.emit(
            "transaction.completed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "point_id": execution.point_id,
                "offer_id": offer.id,
                "operator_id": execution.operator_id,
                "character_gives": _amounts_payload(offer.character_gives),
                "character_receives": _amounts_payload(
                    offer.character_receives
                ),
                "character_holdings_before": character_before,
                "character_holdings_after": _holdings_payload(
                    possessions.holdings
                ),
                "point_holdings_before": point_before,
                "point_holdings_after": _holdings_payload(
                    point_state.holdings
                ),
                **action_lineage_payload(execution.action_instance),
            },
            correlation_id=execution.correlation_id,
        )
        if context.registry.has_component(
            agent_id, TransactionRequestComponent
        ):
            request = context.registry.get_component(
                agent_id, TransactionRequestComponent
            )
            request.status = "completed"
        context.registry.remove_component(
            agent_id, TransactionExecutionComponent
        )

    @staticmethod
    def _resolve(
        context: SystemContext,
        agent_id: str,
        point_id: str,
        offer_id: str,
    ) -> tuple[TransactionPoint, TransactionOffer] | str:
        world = local_world_for_agent(context.registry, agent_id)
        if world is None:
            return "local_space_unavailable"
        try:
            point = world.transaction_point(point_id)
        except KeyError:
            return "transaction_point_not_found"
        try:
            offer = point.offer(offer_id)
        except KeyError:
            return "offer_not_found"
        return point, offer

    @staticmethod
    def _precondition_failure(
        context: SystemContext,
        agent_id: str,
        point: TransactionPoint,
        offer: TransactionOffer,
        *,
        check_capacity: bool,
        operator_id: str | None,
        require_staff: bool,
    ) -> str | None:
        available = point.available
        unavailable_reason = "transaction_point_unavailable"
        if context.registry.has_resource(EnvironmentAvailabilityRegistry):
            availability = context.registry.get_resource(
                EnvironmentAvailabilityRegistry
            ).state(point.id, base_available=point.available)
            available = availability.available
            unavailable_reason = availability.reason.value
        if not available:
            return unavailable_reason
        if (
            require_staff
            and point.operation is TransactionOperation.STAFFED
        ):
            if operator_id is None:
                return "transaction_not_authorized"
            if not context.registry.has_component(
                operator_id, NpcComponent
            ):
                return "transaction_operator_unavailable"
            if not shares_local_map(
                context.registry, agent_id, operator_id
            ):
                return "transaction_operator_not_at_staff_position"
            staffing = context.registry.get_resource(
                NpcPoolRegistry
            ).staffing(point.id).assignment
            operator_position = context.registry.get_component(
                operator_id, PositionComponent
            )
            if operator_position.coordinate != staffing.staff_position:
                return "transaction_operator_not_at_staff_position"
            npc = context.registry.get_component(operator_id, NpcComponent)
            if npc.staffed_point_id != point.id:
                return "transaction_operator_mismatch"
        position = context.registry.get_component(
            agent_id, PositionComponent
        )
        if position.coordinate != point.position:
            return "character_not_at_transaction_point"
        if check_capacity:
            active_count = sum(
                execution.point_id == point.id
                for other_id, execution in context.registry.query(
                    TransactionExecutionComponent
                )
                if other_id != agent_id
            )
            if active_count >= point.capacity:
                return "transaction_point_at_capacity"
        possessions = context.registry.get_component(
            agent_id, PossessionsComponent
        )
        if not can_debit(possessions.holdings, offer.character_gives):
            return "insufficient_character_holdings"
        point_state = context.registry.get_resource(
            TransactionPointRegistry
        ).state(point.id)
        if not can_debit(point_state.holdings, offer.character_receives):
            return "insufficient_transaction_point_holdings"
        return None

    @staticmethod
    def _emit_failure(
        context: SystemContext,
        agent_id: str,
        point_id: str,
        offer_id: str,
        reason: str,
        action_instance: ActionInstance | None = None,
    ) -> None:
        context.events.emit(
            "transaction.failed",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=agent_id,
            payload={
                "point_id": point_id,
                "offer_id": offer_id,
                "reason": reason,
                **action_lineage_payload(action_instance),
            },
            correlation_id=(
                action_instance.root_correlation_id
                if action_instance is not None
                else None
            ),
        )


def cancel_transaction(
    context: SystemContext,
    agent_id: str,
    reason: str,
) -> None:
    if not context.registry.has_component(
        agent_id, TransactionExecutionComponent
    ):
        return
    execution = context.registry.get_component(
        agent_id, TransactionExecutionComponent
    )
    context.events.emit(
        "transaction.cancelled",
        simulation_tick=context.clock.tick,
        simulation_time=context.clock.simulation_time,
        agent_id=agent_id,
        payload={
            "point_id": execution.point_id,
            "offer_id": execution.offer.id,
            "operator_id": execution.operator_id,
            "elapsed": execution.elapsed,
            "reason": reason,
            **action_lineage_payload(execution.action_instance),
        },
        correlation_id=execution.correlation_id,
    )
    if context.registry.has_component(
        agent_id, TransactionRequestComponent
    ):
        request = context.registry.get_component(
            agent_id, TransactionRequestComponent
        )
        request.status = "failed"
        request.failure_reason = reason
    context.registry.remove_component(
        agent_id, TransactionExecutionComponent
    )


def _amounts_payload(
    amounts: tuple[ItemAmount, ...],
) -> list[JsonValue]:
    return [
        {"item_id": amount.item_id, "quantity": amount.quantity}
        for amount in amounts
    ]


def _holdings_payload(holdings: dict[str, int]) -> dict[str, JsonValue]:
    return {
        item_id: quantity
        for item_id, quantity in sorted(holdings.items())
    }
