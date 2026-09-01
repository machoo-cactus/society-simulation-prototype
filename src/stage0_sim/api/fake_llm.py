import argparse
import json
import threading
import time
from collections.abc import Sequence
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from stage0_sim import __version__


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_call_id: str | None = None


class FunctionDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChatTool(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["function"]
    function: FunctionDefinition


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    tools: list[ChatTool] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = False


class Counter:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value


counter = Counter()
app = FastAPI(
    title="Stage 0 Fake OpenAI-Compatible API",
    version=__version__,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": "stage0-fake-openai"}


@app.get("/v1/models")
async def list_models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {
                "id": "stage0-fake",
                "object": "model",
                "created": 0,
                "owned_by": "stage0",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
) -> dict[str, object]:
    if request.stream:
        raise HTTPException(
            status_code=400,
            detail="the Stage 0 fake API does not support streaming",
        )
    call_number = counter.next()
    created = int(time.time())
    response_id = f"chatcmpl-stage0-{call_number:08d}"
    message: dict[str, object] = {
        "role": "assistant",
        "content": f"Fake response {call_number}",
    }
    finish_reason = "stop"
    if request.tools and request.tool_choice != "none":
        tool = _select_tool(
            request.tools,
            request.tool_choice,
            call_number,
        )
        arguments = _arguments_for(tool, call_number)
        message["content"] = None
        message["tool_calls"] = [
            {
                "id": f"call-stage0-{call_number:08d}",
                "type": "function",
                "function": {
                    "name": tool.function.name,
                    "arguments": json.dumps(
                        arguments,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
        ]
        finish_reason = "tool_calls"
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": max(1, len(request.messages)),
            "completion_tokens": 1,
            "total_tokens": max(1, len(request.messages)) + 1,
        },
    }


def _select_tool(
    tools: list[ChatTool],
    tool_choice: str | dict[str, Any] | None,
    call_number: int,
) -> ChatTool:
    del call_number
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict):
            requested_name = function.get("name")
            if isinstance(requested_name, str):
                selected = next(
                    (
                        tool
                        for tool in tools
                        if tool.function.name == requested_name
                    ),
                    None,
                )
                if selected is not None:
                    return selected
    return next(
        (tool for tool in tools if tool.function.name == "wait"),
        sorted(tools, key=lambda item: item.function.name)[0],
    )


def _arguments_for(tool: ChatTool, call_number: int) -> dict[str, object]:
    schema = tool.function.parameters
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    arguments: dict[str, object] = {}
    if tool.function.name == "wait":
        arguments["duration_seconds"] = min(600, call_number)
    elif tool.function.name == "skip":
        arguments["reconsider_after_seconds"] = 30
    elif tool.function.name == "say":
        arguments["target_id"] = _first_string_example(
            properties.get("target_id"), "agent-001"
        )
        arguments["text"] = f"Fake response {call_number}"
    elif tool.function.name == "navigate_to":
        arguments["target_id"] = _first_string_example(
            properties.get("target_id"), "unknown-target"
        )
    elif tool.function.name == "perform":
        arguments["action"] = _first_enum(properties.get("action"), "WORK")
        arguments["duration_seconds"] = min(600, call_number)
    else:
        for name in schema.get("required", []):
            if isinstance(name, str):
                arguments[name] = _simple_schema_value(
                    properties.get(name), call_number
                )
    if "reason" in properties:
        arguments["reason"] = f"Fake call {call_number}"
    return arguments


def _simple_schema_value(schema: object, call_number: int) -> object:
    if not isinstance(schema, dict):
        return call_number
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    value_type = schema.get("type")
    if value_type in {"number", "integer"}:
        return call_number
    if value_type == "boolean":
        return True
    if value_type == "array":
        return []
    if value_type == "object":
        return {}
    return f"fake-{call_number}"


def _first_enum(schema: object, default: str) -> str:
    if isinstance(schema, dict):
        values = schema.get("enum")
        if isinstance(values, list) and values and isinstance(values[0], str):
            return values[0]
    return default


def _first_string_example(schema: object, default: str) -> str:
    if isinstance(schema, dict):
        for key in ("const", "default", "example"):
            value = schema.get(key)
            if isinstance(value, str):
                return value
        values = schema.get("enum")
        if isinstance(values, list) and values and isinstance(values[0], str):
            return values[0]
    return default


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="stage0-fake-llm")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    uvicorn.run(
        "stage0_sim.api.fake_llm:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
