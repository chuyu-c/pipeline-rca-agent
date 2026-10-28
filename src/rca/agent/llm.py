"""LLM access. All model calls go through the `LLMClient` protocol (CLAUDE.md).

- `AnthropicClient`: the real Messages API.
- `ScriptedLLM`: canned responses for unit tests.
- `ReplayLLM`: replays a JSON transcript recorded by `RecordingLLM` (tests/fixtures/llm/).

Responses are plain JSON-able dicts in the Messages API wire shape, so transcripts can be stored and
replayed byte-for-byte, and the loop appends `content` back unchanged (thinking blocks included,
which Claude needs to see again on the next turn).
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import anthropic
from anthropic import Omit
from anthropic.types import ToolParam
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam

Block = dict[str, Any]
Message = dict[str, Any]  # {"role": "user" | "assistant", "content": str | list[Block]}

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The model could not produce a response (network, auth, rate limit, exhausted replay)."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass(frozen=True)
class LLMResponse:
    content: list[Block]
    stop_reason: str
    usage: Usage = Usage()
    model: str = ""

    def tool_uses(self) -> list[Block]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text")

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "usage": asdict(self.usage)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LLMResponse":
        return cls(data["content"], data["stop_reason"], Usage(**data["usage"]), data["model"])


class LLMClient(Protocol):
    @property
    def model(self) -> str: ...

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse: ...


class AnthropicClient:
    """Claude via the Messages API.

    Thinking is left at its adaptive default (it can't be disabled on current Opus models);
    `effort` controls depth. Tool choice stays `auto` because forced tool choice is rejected on
    current models — the prompt asks for `submit_rca` instead. Tools and the system prompt are
    stable, so automatic prompt caching covers them on every step.
    """

    def __init__(
        self,
        model: str,
        *,
        effort: str = "high",
        timeout_s: float = 120.0,
        server_fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._effort = effort
        self._server_fallbacks = server_fallbacks
        self._client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=2)

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse:
        # Our messages/tools are wire-shaped dicts; the SDK types them as TypedDicts.
        wire_messages = cast("list[BetaMessageParam]", list(messages))
        output_config = cast("BetaOutputConfigParam", {"effort": self._effort})
        betas: list[str] | Omit = [FALLBACK_BETA] if self._server_fallbacks else anthropic.omit
        fallbacks: Literal["default"] | Omit = (
            "default" if self._server_fallbacks else anthropic.omit
        )
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=wire_messages,
                tools=tools,
                output_config=output_config,
                cache_control={"type": "ephemeral"},
                betas=betas,
                fallbacks=fallbacks,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        except TypeError as exc:
            # The SDK raises TypeError (not an APIError) when no credentials resolve at all.
            if "authentication" not in str(exc).lower():
                raise
            raise LLMError(
                "no Anthropic credentials (set ANTHROPIC_API_KEY in .env, or use LLM=none)"
            ) from exc
        usage = response.usage
        return LLMResponse(
            content=[
                block.model_dump(mode="json", exclude_none=True) for block in response.content
            ],
            stop_reason=response.stop_reason or "unknown",
            usage=Usage(
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_input_tokens or 0,
                usage.cache_creation_input_tokens or 0,
            ),
            model=response.model,
        )


class NoLLM:
    """Always unavailable, so triage goes straight to the deterministic fallback (eval baseline)."""

    model = "none"

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse:
        raise LLMError("no LLM configured")


Scripted = LLMResponse | Callable[[Sequence[Message]], LLMResponse] | Exception


@dataclass
class ScriptedLLM:
    """Returns scripted responses in order. An entry may be a callable (sees the messages so far,
    e.g. to echo a tool_use id) or an exception to raise. Records every request."""

    script: list[Scripted]
    model: str = "scripted"
    requests: list[list[Message]] = field(default_factory=list)

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse:
        self.requests.append(json.loads(json.dumps(list(messages), default=str)))
        if not self.script:
            raise LLMError("scripted LLM has no responses left")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step


@dataclass
class RecordingLLM:
    """Wraps a live client and writes every response to a transcript `ReplayLLM` can play back."""

    inner: LLMClient
    path: Path
    label: str = ""
    _turns: list[dict[str, Any]] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse:
        response = self.inner.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        self._turns.append({"request": _request_summary(messages), "response": response.to_json()})
        self.save()
        return response

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        transcript = {
            "label": self.label,
            "model": self.inner.model,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "turns": self._turns,
        }
        self.path.write_text(json.dumps(transcript, indent=2, default=str) + "\n", encoding="utf-8")


def _request_summary(messages: Sequence[Message]) -> dict[str, Any]:
    """Enough to debug a replay drift without storing tool outputs (which contain timestamps)."""
    last = messages[-1]["content"] if messages else ""
    tool_results = (
        [b.get("tool_use_id") for b in last if b.get("type") == "tool_result"]
        if isinstance(last, list)
        else []
    )
    return {"messages": len(messages), "tool_results": tool_results}


class ReplayLLM:
    """Plays back a recorded transcript, one response per call, in order.

    Requests aren't matched byte-for-byte (tool output contains run ids and timestamps that differ
    between runs); instead the number of tool results answered is checked against the recording, so
    a loop change that alters the conversation shape fails loudly rather than drifting silently.
    """

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self._model: str = data["model"]
        self._turns: list[dict[str, Any]] = data["turns"]
        self._position = 0
        self.path = path

    @property
    def model(self) -> str:
        return self._model

    @property
    def remaining(self) -> int:
        return len(self._turns) - self._position

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[ToolParam], max_tokens: int
    ) -> LLMResponse:
        if self._position >= len(self._turns):
            raise LLMError(f"replay {self.path.name} exhausted after {len(self._turns)} turns")
        turn = self._turns[self._position]
        expected = turn["request"]["tool_results"]
        actual = _request_summary(messages)["tool_results"]
        if len(expected) != len(actual):
            raise LLMError(
                f"replay {self.path.name} turn {self._position}: expected {len(expected)} tool "
                f"results, got {len(actual)} — re-record the transcript"
            )
        self._position += 1
        return LLMResponse.from_json(turn["response"])


# --------------------------------------------------------------------------- block builders


def text_block(text: str) -> Block:
    return {"type": "text", "text": text}


def tool_use_block(tool_use_id: str, name: str, tool_input: dict[str, Any]) -> Block:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def tool_response(*blocks: Block, usage: Usage | None = None) -> LLMResponse:
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return LLMResponse(list(blocks), stop, usage or Usage(100, 50), "scripted")
