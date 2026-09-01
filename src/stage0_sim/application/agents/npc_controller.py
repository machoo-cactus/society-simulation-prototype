from dataclasses import dataclass

from stage0_sim.application.agents.contracts import (
    CharacterController,
    CharacterDecision,
    CharacterDecisionRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.domain.npcs import NpcControlMode


@dataclass(frozen=True, slots=True)
class DeterministicNpcController(CharacterController):
    synchronous = True

    async def decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision:
        pending = sorted(
            request.observation.service_requests,
            key=lambda item: (item.requested_at, item.request_id),
        )
        if pending:
            call = ModelToolCall(
                call_id=f"{request.decision_id}:deterministic",
                name="serve_transaction",
                arguments={
                    "request_id": pending[0].request_id,
                    "reason": "Serve the oldest assigned request.",
                },
            )
        else:
            call = ModelToolCall(
                call_id=f"{request.decision_id}:deterministic",
                name="skip",
                arguments={
                    "reconsider_after_seconds": 30,
                    "reason": "No assigned service request is waiting.",
                },
            )
        turn = ModelTurn(
            text=None,
            tool_calls=(call,),
            finish_reason="tool_calls",
            provider="deterministic",
            model="npc-service-v1",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
        )
        return CharacterDecision(
            decision_id=request.decision_id,
            tool_call=call,
            model_turn=turn,
        )


@dataclass(frozen=True, slots=True)
class RoutedCharacterController(CharacterController):
    model_controller: CharacterController | None
    npc_mode: NpcControlMode
    deterministic_npc_controller: CharacterController = (
        DeterministicNpcController()
    )

    @property
    def synchronous(self) -> bool:
        if self.model_controller is None:
            return True
        return bool(getattr(self.model_controller, "synchronous", False))

    def uses_model(self, actor_kind: str) -> bool:
        return actor_kind != "npc" or self.npc_mode is NpcControlMode.MODEL

    async def decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision:
        if (
            request.actor_kind == "npc"
            and self.npc_mode is NpcControlMode.DETERMINISTIC
        ):
            return await self.deterministic_npc_controller.decide(request)
        if self.model_controller is None:
            raise RuntimeError(
                "model-backed character control has no configured controller"
            )
        return await self.model_controller.decide(request)
