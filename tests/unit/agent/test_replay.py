import json
from pathlib import Path

from rca.agent.llm import RecordingLLM, ReplayLLM, ScriptedLLM, tool_response
from rca.agent.llm import tool_use_block as use
from rca.agent.loop import run_triage
from rca.agent.replay import ReplayExecutor, attach_tool_outputs, has_tool_outputs
from tests.unit.agent.helpers import FakeExecutor, FakeFallback, make_ctx, valid_report

SCRIPT = [
    tool_response(use("toolu_1", "get_task_runs", {"task_id": "stg_order_items"}),
                  use("toolu_2", "run_readonly_sql", {"sql": "DELETE FROM raw.orders"})),
    tool_response(use("toolu_3", "submit_rca", valid_report("toolu_1"))),
]  # fmt: skip


def record(tmp_path: Path) -> tuple[Path, object]:
    path = tmp_path / "case.json"
    outcome = run_triage(
        make_ctx(), RecordingLLM(ScriptedLLM(list(SCRIPT)), path), max_steps=5,
        token_budget=10**6, executor=FakeExecutor(), fallback=FakeFallback(),
    )  # fmt: skip
    attach_tool_outputs(path, outcome.trace)
    return path, outcome


def test_cassette_replay_reproduces_the_run_without_running_tools(tmp_path: Path) -> None:
    path, recorded = record(tmp_path)
    assert has_tool_outputs(path)
    names = [o["name"] for o in json.loads(path.read_text())["tool_outputs"]]
    assert names == ["get_incident", "get_lineage", "get_task_runs", "run_readonly_sql"]

    executor = ReplayExecutor(path)
    # make_ctx()'s connections raise if touched: the replay never runs a real tool.
    replayed = run_triage(
        make_ctx(), ReplayLLM(path), max_steps=5, token_budget=10**6,
        executor=executor, fallback=FakeFallback(),
    )  # fmt: skip
    assert replayed.source == "agent"
    assert replayed.report == recorded.report  # type: ignore[attr-defined]
    assert replayed.guardrail_blocks == 1  # the recorded SQL-guard block is replayed too
    assert executor.unused == 0


def test_unrecorded_calls_report_drift(tmp_path: Path) -> None:
    path, _ = record(tmp_path)
    result = ReplayExecutor(path)("get_lineage", {"table": "raw.orders"}, make_ctx())
    assert not result.ok and "replay drift" in (result.error or "")
