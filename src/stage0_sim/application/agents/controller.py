from dataclasses import dataclass

from stage0_sim.application.agents.contracts import (
    CharacterController,
    CharacterDecision,
    CharacterDecisionRequest,
    ModelClient,
    ModelRequest,
)
from stage0_sim.application.agents.prompts import PROMPT_VERSION, build_messages
from stage0_sim.application.agents.tools import ToolRegistry


@dataclass(slots=True)
class ToolCallingCharacterController(CharacterController):
    model_client: ModelClient
    tool_registry: ToolRegistry
    model: str = "default"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 512

    @property
    def synchronous(self) -> bool:
        return bool(getattr(self.model_client, "synchronous", False))

    async def decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision:
        model_request = ModelRequest(
            request_id=request.decision_id,
            correlation_id=request.decision_id,
            messages=build_messages(request),
            tools=self.tool_registry.definitions(request.allowed_tools),
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            max_output_tokens=self.max_output_tokens,
            prompt_version=PROMPT_VERSION,
        )
        turn = await self.model_client.complete(model_request)
        if len(turn.tool_calls) != 1:
            return CharacterDecision(
                decision_id=request.decision_id,
                tool_call=None,
                model_turn=turn,
                error="exactly_one_tool_required",
            )
        return CharacterDecision(
            decision_id=request.decision_id,
            tool_call=turn.tool_calls[0],
            model_turn=turn,
        )
