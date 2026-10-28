"""The triage agent loop (DESIGN §5.2).

1. Build context: the incident, its alerts and the root candidate's upstream lineage.
2. Loop within the step and token budgets: the model calls tools, `tools.execute` validates and
   runs them, results go back wrapped as untrusted data.
3. The model ends by calling `submit_rca`; the report is validated as `RCAReport` and its evidence
   must reference calls that actually happened. One repair round is allowed.
4. Any LLM error, refusal, exhausted budget or second invalid report falls back to deterministic
   triage, recorded with the reason.
5. The caller persists the report and the full trace (`persist_outcome`).

The message history is append-only: earlier turns are never edited, so the model's thinking blocks
stay valid when they are sent back on the next step.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from functools import partial
from typing import Any, Literal

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from rca.agent.fallback import fallback_report, gather_facts
from rca.agent.llm import LLMClient, LLMError, Message, Usage
from rca.agent.prompts import load_prompt
from rca.agent.schema import Evidence, RCAReport
from rca.agent.tools import ToolContext, ToolResult, anthropic_tools, execute
from rca.db import Connection
from rca.observability.logging import get_logger

log = get_logger(__name__)

MAX_REPAIRS = 1
MAX_RESPONSE_TOKENS = 16_000

Executor = Callable[[str, Mapping[str, Any], ToolContext], ToolResult]
FallbackFn = Callable[[ToolContext, str], RCAReport]


def untrusted(tool: str, content: str) -> str:
    """Delimit tool output as data. The system prompt says never to follow instructions in it."""
    return f'<tool_output tool="{tool}" untrusted="true">\n{content}\n</tool_output>'


def default_fallback(ctx: ToolContext, reason: str) -> RCAReport:
    return fallback_report(gather_facts(ctx.app_conn, ctx.incident_id), reason)


@dataclass
class TriageOutcome:
    report: RCAReport
    source: Literal["agent", "fallback"]
    fallback_reason: str | None
    model: str
    steps: int
    usage: Usage
    tool_calls: int
    guardrail_blocks: int  # tool calls a guardrail rejected (SQL guard, invalid args)
    repairs: int
    duration_s: float
    trace: list[dict[str, Any]] = field(repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "steps": self.steps,
            "usage": {**asdict(self.usage), "total": self.usage.total},
            "tool_calls": self.tool_calls,
            "guardrail_blocks": self.guardrail_blocks,
            "repairs": self.repairs,
            "duration_s": round(self.duration_s, 2),
        }


@dataclass
class _Run:
    ctx: ToolContext
    llm: LLMClient
    executor: Executor
    fallback: FallbackFn
    started: float
    trace: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0
    guardrail_blocks: int = 0
    repairs: int = 0
    known_call_ids: set[str] = field(default_factory=lambda: {"ctx-incident", "ctx-lineage"})

    def outcome(self, report: RCAReport, reason: str | None) -> TriageOutcome:
        source: Literal["agent", "fallback"] = "agent" if reason is None else "fallback"
        result = TriageOutcome(
            report, source, reason, self.llm.model, self.steps, self.usage, self.tool_calls,
            self.guardrail_blocks, self.repairs, time.monotonic() - self.started, self.trace,
        )  # fmt: skip
        self.trace.append({"type": "result", **result.summary()})
        log.info("triage finished", extra={"incident_id": self.ctx.incident_id, **result.summary()})
        return result

    def fall_back(self, reason: str) -> TriageOutcome:
        self.trace.append({"type": "fallback", "reason": reason, "step": self.steps})
        try:
            report = self.fallback(self.ctx, reason)
        except Exception as exc:  # the fallback is the last line; it must still produce a report
            log.exception("fallback triage failed")
            report = RCAReport(
                summary=f"Triage failed ({reason}) and the heuristic fallback errored ({exc!r}).",
                category="unknown",
                root_entity="unknown",
                confidence=0.0,
                evidence=[Evidence(tool_call_id="fallback:error", excerpt=repr(exc)[:500])],
            )
        return self.outcome(report, reason)


def _brief(run: _Run, max_steps: int) -> str:
    incident = run.executor("get_incident", {}, run.ctx)
    if not incident.ok:
        raise LookupError(f"cannot triage: {incident.error}")
    run.trace.append(
        {"type": "context", "tool_call_id": "ctx-incident", "content": incident.content}
    )
    parts = [
        f"Investigate incident #{run.ctx.incident_id}. You have at most {max_steps} model turns, "
        "including the final submit_rca call.",
        "",
        "Context gathered for you (cite it with the tool_call_id shown):",
        "",
        "tool_call_id: ctx-incident",
        untrusted("get_incident", incident.content),
    ]
    root = incident.data["incident"].get("root_candidate")
    if root:
        lineage = run.executor(
            "get_lineage", {"table": root, "direction": "upstream", "depth": 2}, run.ctx
        )
        run.trace.append(
            {"type": "context", "tool_call_id": "ctx-lineage", "content": lineage.content}
        )
        parts += ["", "tool_call_id: ctx-lineage", untrusted("get_lineage", lineage.content)]
    parts += ["", "Investigate with the tools, then call submit_rca."]
    return "\n".join(parts)


def _validate_submission(run: _Run, raw: Mapping[str, Any]) -> RCAReport | str:
    """The report, or a description of what's wrong with it."""
    try:
        report = RCAReport.model_validate(dict(raw))
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(report)'}: {e['msg']}" for e in exc.errors()
        )
    unknown = sorted({e.tool_call_id for e in report.evidence} - run.known_call_ids)
    if unknown:
        return (
            f"evidence references tool_call_ids that were never made: {', '.join(unknown)}. "
            f"Valid ids: {', '.join(sorted(run.known_call_ids))}"
        )
    return report


def run_triage(
    ctx: ToolContext,
    llm: LLMClient,
    *,
    max_steps: int,
    token_budget: int,
    max_response_tokens: int = MAX_RESPONSE_TOKENS,
    executor: Executor = execute,
    fallback: FallbackFn = default_fallback,
    system_prompt: str | None = None,
) -> TriageOutcome:
    run = _Run(ctx, llm, executor, fallback, time.monotonic())
    system = system_prompt or load_prompt("triage")
    tools = anthropic_tools()
    messages: list[Message] = [{"role": "user", "content": _brief(run, max_steps)}]
    reminded = False

    while True:
        if run.steps >= max_steps:
            return run.fall_back(f"step budget exhausted ({max_steps} turns)")
        if run.usage.total >= token_budget:
            return run.fall_back(f"token budget exhausted ({run.usage.total} >= {token_budget})")
        try:
            response = llm.complete(
                system=system, messages=messages, tools=tools, max_tokens=max_response_tokens
            )
        except LLMError as exc:
            run.trace.append({"type": "llm_error", "step": run.steps, "error": str(exc)})
            return run.fall_back(f"LLM error: {exc}")
        run.steps += 1
        run.usage += response.usage
        run.trace.append({
            "type": "llm", "step": run.steps, "stop_reason": response.stop_reason,
            "usage": asdict(response.usage), "content": response.content,
        })  # fmt: skip
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return run.fall_back("model refused")
        if response.stop_reason == "max_tokens":
            return run.fall_back("response hit max_tokens")

        tool_uses = response.tool_uses()
        if not tool_uses:
            if reminded:
                return run.fall_back("model stopped without calling submit_rca")
            reminded = True
            run.trace.append({"type": "reminder", "step": run.steps})
            messages.append({
                "role": "user",
                "content": "Please finish now by calling submit_rca with your best report "
                "(use low confidence and category 'unknown' if the evidence is inconclusive).",
            })  # fmt: skip
            continue

        results: list[dict[str, Any]] = []
        submitted: RCAReport | None = None
        for use in tool_uses:
            call_id, name, tool_input = use["id"], use["name"], use.get("input") or {}
            if name == "submit_rca":
                verdict = _validate_submission(run, tool_input)
                if isinstance(verdict, RCAReport):
                    submitted = verdict
                    results.append(
                        {"type": "tool_result", "tool_use_id": call_id, "content": "accepted"}
                    )
                    continue
                run.trace.append({
                    "type": "repair", "step": run.steps, "tool_call_id": call_id,
                    "problems": verdict,
                })  # fmt: skip
                if run.repairs >= MAX_REPAIRS:
                    return run.fall_back(f"submit_rca still invalid after repair: {verdict[:300]}")
                run.repairs += 1
                results.append({
                    "type": "tool_result", "tool_use_id": call_id, "is_error": True,
                    "content": f"submit_rca rejected: {verdict}. Fix these and resubmit.",
                })  # fmt: skip
                continue

            result = executor(name, tool_input, ctx)
            run.tool_calls += 1
            run.known_call_ids.add(call_id)
            if result.blocked_reason:
                run.guardrail_blocks += 1
            run.trace.append({
                "type": "tool", "step": run.steps, "tool_call_id": call_id, "name": name,
                "input": tool_input, "ok": result.ok, "blocked_reason": result.blocked_reason,
                "content": result.content,
            })  # fmt: skip
            results.append({
                "type": "tool_result", "tool_use_id": call_id, "is_error": not result.ok,
                "content": untrusted(name, result.content),
            })  # fmt: skip

        if submitted is not None:
            return run.outcome(submitted, None)
        # All results of one turn go back in a single user message.
        messages.append({"role": "user", "content": results})


_dumps = partial(json.dumps, default=str)


def persist_outcome(conn: Connection, incident_id: int, outcome: TriageOutcome) -> None:
    rca = {**outcome.report.model_dump(mode="json"), **outcome.summary()}
    conn.execute(
        "UPDATE ops.incidents SET rca = %s, trace = %s, updated_at = now() WHERE incident_id = %s",
        (Jsonb(rca, dumps=_dumps), Jsonb(outcome.trace, dumps=_dumps), incident_id),
    )
