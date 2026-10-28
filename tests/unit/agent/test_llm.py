import json
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage

from rca.agent.llm import (
    FALLBACK_BETA,
    AnthropicClient,
    LLMError,
    RecordingLLM,
    ReplayLLM,
    ScriptedLLM,
    Usage,
    tool_response,
)
from rca.agent.llm import tool_use_block as use
from rca.agent.loop import run_triage
from tests.unit.agent.helpers import FakeExecutor, FakeFallback, make_ctx, valid_report

SCRIPT = [
    tool_response(use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"})),
    tool_response(use("toolu_2", "submit_rca", valid_report("toolu_1"))),
]


def triage(llm: Any) -> Any:
    return run_triage(
        make_ctx(), llm, max_steps=5, token_budget=10**6,
        executor=FakeExecutor(), fallback=FakeFallback(),
    )  # fmt: skip


def test_record_then_replay_reproduces_the_run(tmp_path: Path) -> None:
    path = tmp_path / "llm" / "case.json"
    recorded = triage(RecordingLLM(ScriptedLLM(list(SCRIPT)), path, label="unit"))
    transcript = json.loads(path.read_text())
    assert transcript["label"] == "unit" and len(transcript["turns"]) == 2
    assert transcript["turns"][1]["request"]["tool_results"] == ["toolu_1"]

    replay = ReplayLLM(path)
    replayed = triage(replay)
    assert replay.remaining == 0
    assert replayed.report == recorded.report and replayed.steps == recorded.steps


def test_replay_detects_a_changed_conversation_shape(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    triage(RecordingLLM(ScriptedLLM(list(SCRIPT)), path))
    data = json.loads(path.read_text())
    data["turns"][1]["request"]["tool_results"] = ["x", "y"]
    path.write_text(json.dumps(data))
    outcome = triage(ReplayLLM(path))
    assert outcome.source == "fallback" and "re-record" in outcome.fallback_reason


def test_exhausted_replay_raises(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"model": "m", "turns": []}))
    with pytest.raises(LLMError, match="exhausted"):
        ReplayLLM(path).complete(system="", messages=[], tools=[], max_tokens=1)


class FakeMessages:
    def __init__(self, result: BetaMessage | Exception) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> BetaMessage:
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeAnthropic:
    def __init__(self, result: BetaMessage | Exception) -> None:
        self.messages = FakeMessages(result)
        self.beta = self


MESSAGE = BetaMessage.model_validate({
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
    "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_incident", "input": {}}],
    "stop_reason": "tool_use", "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100},
})  # fmt: skip


def test_anthropic_client_request_shape_and_response_conversion() -> None:
    fake = FakeAnthropic(MESSAGE)
    client = AnthropicClient("claude-opus-5-5", effort="high", client=fake)  # type: ignore[arg-type]
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    response = client.complete(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        max_tokens=1000,  # type: ignore[list-item]
    )
    sent = fake.messages.kwargs
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 1000
    assert sent["output_config"] == {"effort": "high"}
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["betas"] == [FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert "tool_choice" not in sent and "thinking" not in sent  # auto + adaptive defaults
    # Thinking blocks are kept (with signature) so the loop can send them back unchanged.
    assert response.content[0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert response.tool_uses()[0]["name"] == "get_incident"
    assert response.usage == Usage(10, 5, 100, 0) and response.usage.total == 115


def test_server_fallbacks_can_be_disabled() -> None:
    fake = FakeAnthropic(MESSAGE)
    AnthropicClient("m", server_fallbacks=False, client=fake).complete(  # type: ignore[arg-type]
        system="s", messages=[], tools=[], max_tokens=1
    )
    assert fake.messages.kwargs["betas"] is anthropic.omit
    assert fake.messages.kwargs["fallbacks"] is anthropic.omit


def test_missing_credentials_become_an_llm_error_so_triage_falls_back() -> None:
    error = TypeError('"Could not resolve authentication method. Expected one of api_key, ..."')
    client = AnthropicClient("m", client=FakeAnthropic(error))  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="no Anthropic credentials"):
        client.complete(system="s", messages=[], tools=[], max_tokens=1)


def test_other_type_errors_are_not_swallowed() -> None:
    client = AnthropicClient("m", client=FakeAnthropic(TypeError("bad argument")))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bad argument"):
        client.complete(system="s", messages=[], tools=[], max_tokens=1)


def test_api_errors_become_llm_errors() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid"))
    client = AnthropicClient("m", client=FakeAnthropic(error))  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="APIConnectionError"):
        client.complete(system="s", messages=[], tools=[], max_tokens=1)
