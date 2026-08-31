import json
from dataclasses import dataclass

from stage0_sim.application.agents.contracts import (
    CharacterController,
    CharacterDecision,
    CharacterDecisionRequest,
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelTurn,
    ReadToolExecution,
)
from stage0_sim.application.agents.prompts import PROMPT_VERSION, build_messages
from stage0_sim.application.agents.tools import ToolRegistry, ToolValidationError


@dataclass(slots=True)
class ToolCallingCharacterController(CharacterController):
    model_client: ModelClient
    tool_registry: ToolRegistry
    model: str = "default"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 512
    max_read_tool_rounds: int = 1

    @property
    def synchronous(self) -> bool:
        return bool(getattr(self.model_client, "synchronous", False))

    async def decide(
        self, request: CharacterDecisionRequest
    ) -> CharacterDecision:
        messages = list(build_messages(request))
        turns: list[ModelTurn] = []
        reads: list[ReadToolExecution] = []
        while True:
            round_number = len(turns) + 1
            model_request = ModelRequest(
                request_id=f"{request.decision_id}:round:{round_number}",
                correlation_id=request.decision_id,
                messages=tuple(messages),
                tools=self.tool_registry.definitions(request.allowed_tools),
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                max_output_tokens=self.max_output_tokens,
                prompt_version=PROMPT_VERSION,
            )
            turn = await self.model_client.complete(model_request)
            turns.append(turn)
            aggregate = _aggregate_turns(turns)
            if len(turn.tool_calls) != 1:
                return CharacterDecision(
                    decision_id=request.decision_id,
                    tool_call=None,
                    model_turn=aggregate,
                    error="exactly_one_tool_required",
                    read_tools=tuple(reads),
                )
            call = turn.tool_calls[0]
            if not self.tool_registry.is_read_only(call.name):
                return CharacterDecision(
                    decision_id=request.decision_id,
                    tool_call=call,
                    model_turn=aggregate,
                    read_tools=tuple(reads),
                )
            if len(reads) >= self.max_read_tool_rounds:
                return CharacterDecision(
                    decision_id=request.decision_id,
                    tool_call=None,
                    model_turn=aggregate,
                    error="read_tool_round_limit",
                    read_tools=tuple(reads),
                )
            try:
                result = self.tool_registry.read(request, call)
            except ToolValidationError as error:
                return CharacterDecision(
                    decision_id=request.decision_id,
                    tool_call=None,
                    model_turn=aggregate,
                    error=f"{error.reason}: {error}",
                    read_tools=tuple(reads),
                )
            reads.append(ReadToolExecution(call, result))
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=turn.text,
                    tool_calls=turn.tool_calls,
                )
            )
            messages.append(
                ModelMessage(
                    role="tool",
                    content=json.dumps(
                        result,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    tool_call_id=call.call_id,
                )
            )


def _aggregate_turns(turns: list[ModelTurn]) -> ModelTurn:
    final = turns[-1]
    return ModelTurn(
        text=final.text,
        tool_calls=final.tool_calls,
        finish_reason=final.finish_reason,
        provider=final.provider,
        model=final.model,
        latency_ms=sum(turn.latency_ms for turn in turns),
        input_tokens=sum(turn.input_tokens or 0 for turn in turns),
        output_tokens=sum(turn.output_tokens or 0 for turn in turns),
        provider_request_id=final.provider_request_id,
    )
