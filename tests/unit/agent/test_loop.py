import json
from typing import Any

import pytest

from rca.agent.llm import LLMError, LLMResponse, ScriptedLLM, Usage, text_block, tool_response
from rca.agent.llm import tool_use_block as use
from rca.agent.loop import TriageOutcome, run_triage
from tests.unit.agent.helpers import FakeExecutor, FakeFallback, make_ctx, valid_report

Run = tuple[TriageOutcome, ScriptedLLM, FakeExecutor, FakeFallback]


def triage(script: list[Any], **kw: Any) -> Run:
    llm, executor, fallback = ScriptedLLM(script), FakeExecutor(), FakeFallback()
    outcome = run_triage(
        make_ctx(), llm, max_steps=kw.pop("max_steps", 8),
        token_budget=kw.pop("token_budget", 100_000), executor=executor, fallback=fallback, **kw,
    )  # fmt: skip
    return outcome, llm, executor, fallback


def tool_results(request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = request[-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    return last["content"]


# --------------------------------------------------------------------------- happy path


def test_scripted_happy_path() -> None:
    outcome, llm, executor, fallback = triage([
        tool_response(text_block("Checking the failed task."),
                      use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"})),
        tool_response(use("toolu_2", "submit_rca", valid_report("toolu_1"))),
    ])  # fmt: skip

    assert outcome.source == "agent" and outcome.fallback_reason is None
    assert outcome.report.root_entity == "raw.order_items"
    assert (outcome.steps, outcome.tool_calls, outcome.repairs) == (2, 1, 0)
    assert outcome.usage == Usage(200, 100)
    assert fallback.reasons == []
    # Context was prefetched for the model; then exactly the tool it asked for ran.
    assert [name for name, _ in executor.calls] == ["get_incident", "get_lineage", "get_task_runs"]
    brief = llm.requests[0][0]["content"]
    assert "incident #7" in brief and "tool_call_id: ctx-incident" in brief
    assert '<tool_output tool="get_incident" untrusted="true">' in brief


def test_tool_results_are_wrapped_as_untrusted_and_history_is_append_only() -> None:
    _, llm, _, _ = triage([
        tool_response(use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"})),
        tool_response(use("toolu_2", "submit_rca", valid_report("toolu_1"))),
    ])  # fmt: skip
    first, second = llm.requests
    assert second[: len(first)] == first  # earlier turns never edited
    assert second[len(first)]["role"] == "assistant"  # the model's turn appended verbatim
    (result,) = tool_results(second)
    assert result["tool_use_id"] == "toolu_1" and result["is_error"] is False
    assert result["content"].startswith('<tool_output tool="get_task_runs" untrusted="true">')


def test_parallel_tool_calls_return_in_one_user_message() -> None:
    _, llm, _, _ = triage([
        tool_response(use("a", "get_task_runs", {"task_id": "stg_order_items"}),
                      use("b", "get_lineage", {"table": "stg.order_items"})),
        tool_response(use("c", "submit_rca", valid_report("a"))),
    ])  # fmt: skip
    assert [r["tool_use_id"] for r in tool_results(llm.requests[1])] == ["a", "b"]


def test_context_ids_are_valid_evidence() -> None:
    outcome, *_ = triage([tool_response(use("t", "submit_rca", valid_report("ctx-incident")))])
    assert outcome.source == "agent"


# --------------------------------------------------------------------------- invalid tool use


def test_invalid_tool_args_are_returned_as_errors_and_the_model_continues() -> None:
    outcome, llm, _, _ = triage([
        tool_response(use("toolu_1", "get_lineage", {"table": "stg.order_items", "depth": 9})),
        tool_response(use("toolu_2", "run_readonly_sql", {"sql": "DELETE FROM raw.orders"})),
        tool_response(use("toolu_3", "no_such_tool", {})),
        tool_response(use("toolu_4", "submit_rca", valid_report("toolu_1"))),
    ])  # fmt: skip
    assert outcome.source == "agent"
    errors = [tool_results(r)[0] for r in llm.requests[1:4]]
    assert all(e["is_error"] for e in errors)
    assert "invalid arguments for get_lineage" in errors[0]["content"]
    assert "query rejected (not_select)" in errors[1]["content"]
    assert "unknown tool" in errors[2]["content"]
    assert outcome.guardrail_blocks == 2  # invalid args + SQL guard


# --------------------------------------------------------------------------- budgets → fallback


def looping_tool(i: int) -> LLMResponse:
    return tool_response(use(f"toolu_{i}", "get_task_runs", {"task_id": "stg_order_items"}))


def test_step_budget_exhaustion_falls_back() -> None:
    outcome, llm, _, fallback = triage([looping_tool(i) for i in range(10)], max_steps=3)
    assert outcome.source == "fallback"
    assert len(llm.requests) == 3
    assert fallback.reasons == ["step budget exhausted (3 turns)"]
    assert outcome.report.summary == "heuristic"
    assert [e["type"] for e in outcome.trace[-2:]] == ["fallback", "result"]


def test_token_budget_exhaustion_falls_back() -> None:
    expensive = tool_response(
        use("toolu_1", "get_task_runs", {"task_id": "x"}), usage=Usage(90_000, 20_000)
    )
    outcome, llm, _, fallback = triage([expensive, looping_tool(2)], token_budget=100_000)
    assert outcome.source == "fallback" and len(llm.requests) == 1
    assert fallback.reasons[0].startswith("token budget exhausted (110000 >= 100000)")


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([LLMError("APIConnectionError: boom")], "LLM error: APIConnectionError: boom"),
        ([LLMResponse([], "refusal", Usage(1, 0))], "model refused"),
        ([LLMResponse([text_block("…")], "max_tokens", Usage(1, 1))], "response hit max_tokens"),
    ],
)
def test_llm_failures_fall_back(script: list[Any], reason: str) -> None:
    outcome, *_, fallback = triage(script)
    assert outcome.source == "fallback" and fallback.reasons == [reason]


def test_stopping_without_submit_gets_one_reminder_then_falls_back() -> None:
    _, llm, _, fallback = triage([
        tool_response(text_block("I think it is schema drift.")),
        tool_response(text_block("Yes, schema drift.")),
    ])  # fmt: skip
    assert "calling submit_rca" in llm.requests[1][-1]["content"]
    assert fallback.reasons == ["model stopped without calling submit_rca"]


def test_reminder_can_rescue_the_run() -> None:
    outcome, *_ = triage([
        tool_response(text_block("Done investigating.")),
        tool_response(use("t", "submit_rca", valid_report("ctx-incident"))),
    ])  # fmt: skip
    assert outcome.source == "agent"


# --------------------------------------------------------------------------- submit_rca repair


def test_schema_invalid_submit_gets_one_repair() -> None:
    bad = {k: v for k, v in valid_report().items() if k != "evidence"} | {"confidence": 3}
    outcome, llm, _, fallback = triage([
        tool_response(use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"})),
        tool_response(use("toolu_2", "submit_rca", bad)),
        tool_response(use("toolu_3", "submit_rca", valid_report("toolu_1"))),
    ])  # fmt: skip
    assert outcome.source == "agent" and outcome.repairs == 1 and fallback.reasons == []
    (rejection,) = tool_results(llm.requests[2])
    assert rejection["tool_use_id"] == "toolu_2" and rejection["is_error"] is True
    assert "evidence: Field required" in rejection["content"]
    assert "confidence: Input should be less than or equal to 1" in rejection["content"]
    assert any(e["type"] == "repair" for e in outcome.trace)


def test_evidence_must_reference_real_tool_calls() -> None:
    outcome, llm, *_ = triage([
        tool_response(use("toolu_1", "submit_rca", valid_report("toolu_made_up"))),
        tool_response(use("toolu_2", "submit_rca", valid_report("ctx-lineage"))),
    ])  # fmt: skip
    assert outcome.source == "agent" and outcome.repairs == 1
    assert "never made: toolu_made_up" in tool_results(llm.requests[1])[0]["content"]


def test_second_invalid_submit_falls_back() -> None:
    bad = valid_report() | {"category": "aliens"}
    outcome, *_, fallback = triage([
        tool_response(use("t1", "submit_rca", bad)),
        tool_response(use("t2", "submit_rca", bad)),
    ])  # fmt: skip
    assert outcome.source == "fallback"
    assert fallback.reasons[0].startswith("submit_rca still invalid after repair: category")


def test_trace_is_json_serialisable() -> None:
    outcome, *_ = triage([
        tool_response(use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"})),
        tool_response(use("toolu_2", "submit_rca", valid_report("toolu_1"))),
    ])  # fmt: skip
    kinds = [e["type"] for e in json.loads(json.dumps(outcome.trace, default=str))]
    assert kinds == ["context", "context", "llm", "tool", "llm", "result"]
