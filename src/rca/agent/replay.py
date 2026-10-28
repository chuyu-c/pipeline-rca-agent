"""Tool-output cassettes for faithful replays.

A recorded transcript holds the model's responses. Replaying them against freshly executed tools
drifts: a new pipeline run has new run ids and timestamps, while the recorded model keeps using the
old ones. So recordings also keep every tool output the model saw, and `ReplayExecutor` serves those
instead of running the tools. A replay then reproduces the recorded run exactly and re-scores it
without API calls; a call that wasn't recorded returns an explicit drift error.
"""

import json
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rca.agent.tools import ToolContext, ToolResult


def _key(name: str, tool_input: Mapping[str, Any]) -> str:
    return f"{name}:{json.dumps(tool_input, sort_keys=True, default=str)}"


def tool_outputs_from_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool output the model saw, in call order (context first)."""
    outputs = []
    for event in trace:
        if event.get("type") == "context":
            outputs.append({
                "tool_call_id": event["tool_call_id"], "name": event["name"],
                "input": event["input"], "ok": True, "blocked_reason": None,
                "content": event["content"],
            })  # fmt: skip
        elif event.get("type") == "tool":
            outputs.append(
                {
                    k: event[k]
                    for k in ("tool_call_id", "name", "input", "ok", "blocked_reason", "content")
                }
            )
    return outputs


def attach_tool_outputs(path: Path, trace: list[dict[str, Any]]) -> None:
    transcript = json.loads(path.read_text(encoding="utf-8"))
    transcript["tool_outputs"] = tool_outputs_from_trace(trace)
    path.write_text(json.dumps(transcript, indent=2, default=str) + "\n", encoding="utf-8")


def has_tool_outputs(path: Path) -> bool:
    return "tool_outputs" in json.loads(path.read_text(encoding="utf-8"))


class ReplayExecutor:
    """Serves recorded tool outputs by (tool name, exact input), first-recorded first."""

    def __init__(self, path: Path) -> None:
        recorded = json.loads(path.read_text(encoding="utf-8"))["tool_outputs"]
        self._queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for output in recorded:
            self._queues[_key(output["name"], output["input"])].append(output)

    @property
    def unused(self) -> int:
        return sum(len(q) for q in self._queues.values())

    def __call__(self, name: str, raw_input: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        queue = self._queues.get(_key(name, raw_input))
        if not queue:
            result = ToolResult.failure(
                f"replay drift: no recorded output for {name}({dict(raw_input)})"
            )
            result.content = json.dumps({"ok": False, "error": result.error})
            return result
        recorded = queue.popleft()
        result = ToolResult(ok=recorded["ok"], blocked_reason=recorded["blocked_reason"])
        result.content = recorded["content"]
        try:  # structured data, for callers that read it (the context brief does)
            result.data = json.loads(recorded["content"]).get("data")
        except (json.JSONDecodeError, AttributeError):
            result.data = None
        if not result.ok:
            result.error = recorded["content"]
        return result
