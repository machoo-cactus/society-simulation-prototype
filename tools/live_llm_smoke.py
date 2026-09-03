import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from stage0_sim.application.agents.contracts import (
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ToolDefinition,
)
from stage0_sim.application.agents.tools import WaitArguments
from stage0_sim.application.engagements.catalog import build_v1_capability_catalog
from stage0_sim.application.engagements.contracts import (
    EngagementCompilationProposal,
)
from stage0_sim.config import Settings, create_model_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded OpenAI-compatible tool-call smoke checks."
    )
    parser.add_argument(
        "--operation",
        choices=("controller", "engagement", "both"),
        default="controller",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    return parser


def _validate_bounds(
    parser: argparse.ArgumentParser,
    timeout_seconds: float,
    max_output_tokens: int,
) -> None:
    if not 0 < timeout_seconds <= 60:
        parser.error("--timeout-seconds must be greater than 0 and at most 60")
    if not 0 < max_output_tokens <= 1024:
        parser.error("--max-output-tokens must be between 1 and 1024")


def _required_call(
    turn: ModelTurn,
    expected_name: str,
) -> ModelToolCall:
    if turn.text is not None and turn.text.strip():
        raise RuntimeError(f"{expected_name} smoke received unexpected prose")
    if len(turn.tool_calls) != 1:
        raise RuntimeError(
            f"{expected_name} smoke requires exactly one tool call; "
            f"received {len(turn.tool_calls)}"
        )
    call = turn.tool_calls[0]
    if call.name != expected_name:
        raise RuntimeError(
            f"{expected_name} smoke received unexpected tool {call.name!r}"
        )
    return call


def _controller_request(
    model: str,
    timeout_seconds: float,
    max_output_tokens: int,
) -> ModelRequest:
    return ModelRequest(
        request_id="live-smoke:controller",
        correlation_id="live-smoke",
        messages=(
            ModelMessage(
                role="system",
                content=(
                    "Return exactly one wait tool call with duration_seconds "
                    "between 1 and 5. Return no prose."
                ),
            ),
            ModelMessage(role="user", content="Wait briefly."),
        ),
        tools=(
            ToolDefinition(
                name="wait",
                description="Wait for a bounded simulated duration.",
                input_schema=WaitArguments.model_json_schema(),
            ),
        ),
        model=model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=min(max_output_tokens, 256),
        prompt_version="live_smoke.controller.v1",
    )


def _engagement_request(
    model: str,
    timeout_seconds: float,
    max_output_tokens: int,
) -> ModelRequest:
    scene = {
        "scene_version": "engagement-compiler-scene.v1",
        "decision_id": "live-smoke-decision",
        "run_id": "live-smoke-run",
        "requested_tick": 0,
        "state_revision": 0,
        "engagement": {
            "engagement_id": "live-smoke-engagement",
            "intent": "Wave once.",
            "reference_ids": [],
        },
        "actor": {
            "actor_id": "live-smoke-actor",
            "display_name": "Smoke Actor",
            "public_state": {
                "location_id": "live-smoke-room",
                "activity": "IDLE",
                "satiety": 50,
                "energy": 50,
                "stress": 10,
            },
        },
        "references": [],
        "offered_specialized_tools": ["wait"],
        "environment": {
            "simulation_time": 0,
            "location_id": "live-smoke-room",
        },
        "capability_catalog": build_v1_capability_catalog().to_payload(),
    }
    return ModelRequest(
        request_id="live-smoke:engagement",
        correlation_id="live-smoke",
        messages=(
            ModelMessage(
                role="system",
                content=(
                    "Operation: engagement_compilation. Return exactly one "
                    "compile_engagement tool call using only the supplied scene "
                    "and capability catalog. Return no prose."
                ),
            ),
            ModelMessage(
                role="user",
                content=json.dumps(
                    scene,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        ),
        tools=(
            ToolDefinition(
                name="compile_engagement",
                description="Return one strict engagement compilation proposal.",
                input_schema=EngagementCompilationProposal.model_json_schema(),
            ),
        ),
        model=model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        prompt_version="engagement_compilation.v1",
    )


def _safe_summary(
    operation: str,
    turn: ModelTurn,
    call: ModelToolCall,
) -> dict[str, object]:
    return {
        "operation": operation,
        "provider": turn.provider,
        "model": turn.model,
        "finish_reason": turn.finish_reason,
        "tool": call.name,
        "latency_ms": turn.latency_ms,
        "input_tokens": turn.input_tokens,
        "output_tokens": turn.output_tokens,
    }


async def _run_request(
    operation: str,
    complete: Callable[[ModelRequest], Awaitable[ModelTurn]],
    request: ModelRequest,
) -> dict[str, object]:
    turn = await complete(request)
    if not isinstance(turn, ModelTurn):
        raise RuntimeError("model client returned an invalid turn")
    expected_name = "wait" if operation == "controller" else "compile_engagement"
    call = _required_call(turn, expected_name)
    if operation == "controller":
        WaitArguments.model_validate(call.arguments)
    else:
        EngagementCompilationProposal.model_validate(call.arguments)
    return _safe_summary(operation, turn, call)


async def _run(args: argparse.Namespace) -> list[dict[str, object]]:
    settings = Settings()
    if settings.llm_provider != "openai-compatible":
        raise RuntimeError(
            "STAGE0_LLM_PROVIDER=openai-compatible is required for live smoke"
        )
    bounded_settings = settings.model_copy(
        update={
            "llm_timeout_seconds": min(
                settings.llm_timeout_seconds,
                args.timeout_seconds,
            ),
            "llm_retry_attempts": 1,
            "llm_max_output_tokens": min(
                settings.llm_max_output_tokens,
                args.max_output_tokens,
            ),
        }
    )
    client = create_model_client(bounded_settings)
    if client is None or bounded_settings.llm_model is None:
        raise RuntimeError("live model client configuration is incomplete")

    operations = (
        ("controller",)
        if args.operation == "controller"
        else ("engagement",)
        if args.operation == "engagement"
        else ("controller", "engagement")
    )
    builders = {
        "controller": _controller_request,
        "engagement": _engagement_request,
    }
    results: list[dict[str, object]] = []
    for operation in operations:
        request = builders[operation](
            bounded_settings.llm_model,
            bounded_settings.llm_timeout_seconds,
            bounded_settings.llm_max_output_tokens,
        )
        results.append(
            await _run_request(operation, client.complete, request)
        )
    return results


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    _validate_bounds(parser, args.timeout_seconds, args.max_output_tokens)
    try:
        results = asyncio.run(_run(args))
    except Exception as error:
        parser.exit(1, f"live LLM smoke failed: {error}\n")
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
