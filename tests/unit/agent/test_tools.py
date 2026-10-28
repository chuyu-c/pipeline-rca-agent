import json
from typing import Any

import pytest
from pydantic import BaseModel

from rca.agent.tools import (
    REGISTRY,
    ToolContext,
    ToolResult,
    ToolSpec,
    anthropic_tools,
    execute,
    json_schema,
)
from rca.knowledge.embed import HashEmbedder

DESIGN_TOOLS = [
    "get_incident", "get_task_runs", "get_task_logs", "get_lineage", "run_readonly_sql",
    "get_recent_deploys", "search_knowledge", "submit_rca",
]  # fmt: skip


class Untouchable:
    """Stands in for a DB connection that must not be used."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"connection used: .{name}")


def ctx() -> ToolContext:
    return ToolContext(Untouchable(), Untouchable(), HashEmbedder(), incident_id=1)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- registry / schemas


def test_registry_matches_design_and_only_submit_is_terminal() -> None:
    assert list(REGISTRY) == DESIGN_TOOLS
    assert [n for n, s in REGISTRY.items() if s.terminal] == ["submit_rca"]


def test_no_write_tools() -> None:
    writes = ("insert", "update", "delete", "rerun", "backfill", "revert", "approve", "execute")
    assert not [n for n in REGISTRY if any(w in n for w in writes)]


def walk(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        return [node, *(d for v in node.values() for d in walk(v))]
    if isinstance(node, list):
        return [d for v in node for d in walk(v)]
    return []


def test_anthropic_tool_definitions_are_self_contained_json_schema() -> None:
    tools = anthropic_tools()
    assert [t["name"] for t in tools] == DESIGN_TOOLS
    assert json.loads(json.dumps(tools)) == tools  # plain JSON
    for t in tools:
        assert set(t) == {"name", "description", "input_schema"}
        assert len(t["description"]) > 40
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        nodes = walk(schema)
        assert not [n for n in nodes if "$ref" in n or "$defs" in n or "title" in n]


def test_definitions_are_stable_for_prompt_caching() -> None:
    assert json.dumps(anthropic_tools(), sort_keys=False) == json.dumps(anthropic_tools())


def test_submit_rca_schema_exposes_the_closed_action_set() -> None:
    schema = REGISTRY["submit_rca"].definition()["input_schema"]
    actions = schema["properties"]["proposed_actions"]["items"]["oneOf"]
    types = {a["properties"]["type"]["const"] for a in actions}
    assert types == {
        "rerun_task", "backfill_partition", "quarantine_partition", "revert_deploy", "escalate",
    }  # fmt: skip
    assert set(schema["properties"]["category"]["enum"]) >= {"schema_drift", "unknown"}
    assert schema["properties"]["confidence"] == {"maximum": 1, "minimum": 0, "type": "number"}


def test_limits_from_design_are_in_the_schemas() -> None:
    props = {t["name"]: t["input_schema"]["properties"] for t in anthropic_tools()}
    assert props["get_lineage"]["depth"]["maximum"] == 3
    assert props["search_knowledge"]["k"]["maximum"] == 8
    assert props["get_task_logs"]["limit"]["maximum"] == 200


# --------------------------------------------------------------------------- execute()


def test_unknown_tool() -> None:
    result = execute("drop_table", {}, ctx())
    assert not result.ok and "unknown tool" in (result.error or "")
    assert json.loads(result.content)["ok"] is False


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_lineage", {"table": "raw.orders", "depth": 4}),
        ("get_lineage", {"table": "raw.orders", "direction": "sideways"}),
        ("get_task_logs", {}),
        ("get_incident", {"incident_id": 999}),  # extra field: the incident is fixed by context
        ("search_knowledge", {"query": "x" * 2000}),
        ("run_readonly_sql", {"sql": "SELECT 1", "role": "rca_admin"}),
    ],
)
def test_invalid_arguments_are_rejected_before_any_db_access(
    name: str, args: dict[str, Any]
) -> None:
    result = execute(name, args, ctx())
    assert not result.ok and result.blocked_reason == "invalid_args"
    assert f"invalid arguments for {name}" in result.content


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM raw.orders", "not_select"),
        ("SELECT * FROM ops.task_runs", "schema_not_allowed"),
        ("SELECT pg_sleep(60)", "denied_function"),
        ("SELECT 1 FROM raw.orders; DROP TABLE raw.orders", "multiple_statements"),
        ("WITH d AS (DELETE FROM raw.orders RETURNING *) SELECT * FROM d", "forbidden_clause"),
    ],
)
def test_sql_guard_blocks_before_the_readonly_connection_is_touched(sql: str, reason: str) -> None:
    result = execute("run_readonly_sql", {"sql": sql}, ctx())
    assert not result.ok and result.blocked_reason == reason
    assert "query rejected" in result.content


class EchoArgs(BaseModel):
    text: str


def test_all_output_is_redacted_and_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    def leaky(c: ToolContext, a: EchoArgs) -> ToolResult:
        return ToolResult.success({"log": a.text * 1000 + " password=hunter2 jane@example.com"})

    spec = ToolSpec("leaky", "test tool that returns a lot", EchoArgs, leaky)
    monkeypatch.setitem(REGISTRY, "leaky", spec)
    result = execute("leaky", {"text": "abcdef"}, ctx())
    assert result.ok
    assert "hunter2" not in result.content and "jane@example.com" not in result.content
    assert len(result.content) <= 6000 and "characters truncated" in result.content
    assert "hunter2" in result.data["log"]  # raw data kept for the audit trace only


def test_a_crashing_tool_returns_an_error_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(c: ToolContext, a: EchoArgs) -> ToolResult:
        raise ZeroDivisionError

    monkeypatch.setitem(REGISTRY, "broken", ToolSpec("broken", "always fails", EchoArgs, broken))
    result = execute("broken", {"text": "x"}, ctx())
    assert not result.ok and result.error == "internal error in broken: ZeroDivisionError"


def test_json_schema_inlines_nested_models() -> None:
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner
        many: list[Inner]

    schema = json_schema(Outer)
    assert schema["properties"]["inner"]["properties"]["x"]["type"] == "integer"
    assert schema["properties"]["many"]["items"]["required"] == ["x"]
