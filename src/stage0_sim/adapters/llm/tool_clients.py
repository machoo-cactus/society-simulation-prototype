import asyncio
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelClientError,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
)
from stage0_sim.domain.events import JsonValue


@dataclass(slots=True)
class ScriptedModelClient(ModelClient):
    turns: deque[ModelTurn]
    provider_name: str = "scripted"
    synchronous = True

    def __init__(self, turns: tuple[ModelTurn, ...]) -> None:
        self.turns = deque(turns)
        self.provider_name = "scripted"

    async def complete(self, request: ModelRequest) -> ModelTurn:
        del request
        if not self.turns:
            raise ModelClientError("scripted model response queue is empty")
        return self.turns.popleft()


class ReplayModelClient(ModelClient):
    synchronous = True

    def __init__(self, recordings: dict[str, ModelTurn]) -> None:
        self._recordings = dict(recordings)

    @classmethod
    def from_jsonl(cls, path: Path) -> "ReplayModelClient":
        recordings: dict[str, ModelTurn] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise ModelClientError(f"could not read replay file: {error}") from error
        for line in lines:
            payload = json.loads(line)
            request_hash = str(payload["request_hash"])
            recordings[request_hash] = _turn_from_dict(payload["response"])
        return cls(recordings)

    async def complete(self, request: ModelRequest) -> ModelTurn:
        request_hash = model_request_hash(request)
        try:
            return self._recordings[request_hash]
        except KeyError as error:
            raise ModelClientError(
                f"no replay response for request hash {request_hash}"
            ) from error


class RecordingModelClient(ModelClient):
    def __init__(self, client: ModelClient, path: Path) -> None:
        self._client = client
        self._path = path
        self._lock = threading.Lock()
        self.synchronous = bool(getattr(client, "synchronous", False))

    async def complete(self, request: ModelRequest) -> ModelTurn:
        turn = await self._client.complete(request)
        record = {
            "request_hash": model_request_hash(request),
            "request": _request_dict(request),
            "response": asdict(turn),
        }
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        return turn


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfiguration:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 30.0
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0
    tool_choice: str = "required"

    def __post_init__(self) -> None:
        if not self.base_url or not self.model:
            raise ValueError("LLM base_url and model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("LLM timeout must be greater than zero")
        if self.retry_attempts <= 0:
            raise ValueError("LLM retry_attempts must be greater than zero")
        if self.retry_delay_seconds < 0:
            raise ValueError("LLM retry_delay_seconds must not be negative")
        if self.tool_choice not in {"auto", "required", "none"}:
            raise ValueError("LLM tool_choice must be auto, required, or none")


class OpenAICompatibleClient(ModelClient):
    provider_name = "openai-compatible"

    def __init__(self, configuration: OpenAICompatibleConfiguration) -> None:
        self.configuration = configuration

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return await asyncio.to_thread(self._complete_sync, request)

    def _complete_sync(self, request: ModelRequest) -> ModelTurn:
        started = time.perf_counter()
        payload = {
            "model": self.configuration.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    **(
                        {"tool_call_id": message.tool_call_id}
                        if message.tool_call_id is not None
                        else {}
                    ),
                }
                for message in request.messages
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ],
            "tool_choice": self.configuration.tool_choice,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "stage0-simulation/0.1",
        }
        if self.configuration.api_key:
            headers["Authorization"] = f"Bearer {self.configuration.api_key}"
        http_request = urllib.request.Request(
            _chat_completions_url(self.configuration.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = min(request.timeout_seconds, self.configuration.timeout_seconds)
        body: Any = None
        last_error: ModelClientError | None = None
        for attempt in range(self.configuration.retry_attempts):
            try:
                with urllib.request.urlopen(http_request, timeout=timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as error:
                detail = _http_error_detail(error)
                last_error = ModelClientError(
                    f"model HTTP {error.code} from {http_request.full_url}"
                    f"{f': {detail}' if detail else ''}"
                )
                if (
                    error.code in {408, 429, 500, 502, 503, 504}
                    and attempt + 1 < self.configuration.retry_attempts
                ):
                    _retry_sleep(
                        error.headers.get("Retry-After"),
                        self.configuration.retry_delay_seconds,
                        attempt,
                    )
                    continue
                raise last_error from error
            except TimeoutError as error:
                raise ModelClientError(
                    "model request timed out", reason="provider_timeout"
                ) from error
            except urllib.error.URLError as error:
                last_error = ModelClientError(
                    f"model transport failed for {http_request.full_url}: "
                    f"{error.reason}"
                )
                if attempt + 1 < self.configuration.retry_attempts:
                    _retry_sleep(
                        None,
                        self.configuration.retry_delay_seconds,
                        attempt,
                    )
                    continue
                raise last_error from error
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ModelClientError("model returned invalid JSON") from error
        if body is None:
            raise last_error or ModelClientError("model returned no response")
        try:
            choice = body["choices"][0]
            message = choice["message"]
            raw_calls = message.get("tool_calls", [])
            calls = tuple(_parse_tool_call(item) for item in raw_calls)
            usage = body.get("usage", {})
            return ModelTurn(
                text=message.get("content"),
                tool_calls=calls,
                finish_reason=str(choice.get("finish_reason", "unknown")),
                provider=self.provider_name,
                model=str(body.get("model", self.configuration.model)),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                input_tokens=_optional_int(usage.get("prompt_tokens")),
                output_tokens=_optional_int(usage.get("completion_tokens")),
                provider_request_id=(
                    str(body["id"]) if body.get("id") is not None else None
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelClientError("model response shape is invalid") from error


def model_request_hash(request: ModelRequest) -> str:
    encoded = json.dumps(
        _request_dict(request), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_dict(request: ModelRequest) -> dict[str, JsonValue]:
    return {
        "request_id": request.request_id,
        "correlation_id": request.correlation_id,
        "messages": [asdict(message) for message in request.messages],
        "tools": [asdict(tool) for tool in request.tools],
        "model": request.model,
        "timeout_seconds": request.timeout_seconds,
        "max_output_tokens": request.max_output_tokens,
        "prompt_version": request.prompt_version,
    }


def _turn_from_dict(payload: dict[str, Any]) -> ModelTurn:
    return ModelTurn(
        text=payload.get("text"),
        tool_calls=tuple(
            ModelToolCall(
                call_id=str(call["call_id"]),
                name=str(call["name"]),
                arguments=dict(call["arguments"]),
            )
            for call in payload.get("tool_calls", [])
        ),
        finish_reason=str(payload["finish_reason"]),
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        latency_ms=float(payload["latency_ms"]),
        input_tokens=_optional_int(payload.get("input_tokens")),
        output_tokens=_optional_int(payload.get("output_tokens")),
        provider_request_id=payload.get("provider_request_id"),
    )


def _parse_tool_call(payload: dict[str, Any]) -> ModelToolCall:
    function = payload["function"]
    arguments = json.loads(function["arguments"])
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return ModelToolCall(
        call_id=str(payload["id"]),
        name=str(function["name"]),
        arguments=arguments,
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return int(value)
    raise ValueError("token count must be numeric")


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + "/v1/chat/completions"


def _http_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        content = error.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(payload, dict):
        nested = payload.get("error")
        if isinstance(nested, dict):
            message = nested.get("message")
            if isinstance(message, str):
                return message
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return content


def _retry_sleep(
    retry_after: str | None,
    base_delay: float,
    attempt: int,
) -> None:
    delay = base_delay * (2**attempt)
    if retry_after is not None:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            retry_after = None
    if delay > 0:
        time.sleep(delay)
