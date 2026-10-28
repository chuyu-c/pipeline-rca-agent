"""Run triage for one incident end to end: build the LLM and tool context, run the loop, persist."""

from pathlib import Path
from typing import Literal

from rca.agent.llm import AnthropicClient, LLMClient, NoLLM, RecordingLLM, ReplayLLM
from rca.agent.loop import Executor, TriageOutcome, persist_outcome, run_triage
from rca.agent.replay import ReplayExecutor, attach_tool_outputs, has_tool_outputs
from rca.agent.tools import ToolContext, execute
from rca.config import Settings
from rca.db import connect
from rca.knowledge.embed import make_embedder

LLMKind = Literal["anthropic", "replay", "none"]


def make_llm(
    settings: Settings, kind: LLMKind, *, replay: Path | None = None, record: Path | None = None
) -> LLMClient:
    if kind == "none":
        return NoLLM()
    if kind == "replay":
        if replay is None:
            raise ValueError("--llm replay needs a transcript path")
        return ReplayLLM(replay)
    llm: LLMClient = AnthropicClient(
        settings.model,
        effort=settings.effort,
        timeout_s=settings.llm_timeout_s,
        server_fallbacks=settings.llm_fallbacks,
    )
    return RecordingLLM(llm, record, label=record.stem) if record else llm


def executor_for(llm: LLMClient) -> Executor:
    """Replays with a tool-output cassette serve recorded outputs; everything else runs tools."""
    if isinstance(llm, ReplayLLM) and has_tool_outputs(llm.path):
        return ReplayExecutor(llm.path)
    return execute


def finish_recording(llm: LLMClient, outcome: TriageOutcome) -> None:
    """A recording also keeps the tool outputs the model saw, so it can be replayed faithfully."""
    if isinstance(llm, RecordingLLM):
        attach_tool_outputs(llm.path, outcome.trace)


def triage_incident(settings: Settings, incident_id: int, llm: LLMClient) -> TriageOutcome:
    embedder = make_embedder(
        settings.embedder, settings.embedding_model, settings.embedding_cache_dir
    )
    with (
        connect(settings.database_url, autocommit=True) as app,
        connect(settings.readonly_database_url, autocommit=True) as readonly,
    ):
        ctx = ToolContext(app, readonly, embedder, incident_id)
        outcome = run_triage(
            ctx, llm, max_steps=settings.max_steps, token_budget=settings.token_budget,
            executor=executor_for(llm),
        )  # fmt: skip
        persist_outcome(app, incident_id, outcome)
    finish_recording(llm, outcome)
    return outcome


def format_outcome(outcome: TriageOutcome) -> str:
    r = outcome.report
    lines = [
        f"source={outcome.source} model={outcome.model} steps={outcome.steps} "
        f"tool_calls={outcome.tool_calls} tokens={outcome.usage.total} "
        f"guardrail_blocks={outcome.guardrail_blocks} repairs={outcome.repairs} "
        f"{outcome.duration_s:.1f}s",
    ]
    if outcome.fallback_reason:
        lines.append(f"fallback reason: {outcome.fallback_reason}")
    lines += [
        f"category:    {r.category}",
        f"root_entity: {r.root_entity}",
        f"confidence:  {r.confidence:.2f}",
        f"summary:     {r.summary}",
        "evidence:",
        *(f"  [{e.tool_call_id}] {e.excerpt}" for e in r.evidence),
        f"citations:   {', '.join(r.citations) or '-'}",
        "proposed actions:",
        *(f"  {a.model_dump(exclude={'rationale'})}  — {a.rationale}" for a in r.proposed_actions),
    ]
    if not r.proposed_actions:
        lines.append("  (none)")
    return "\n".join(lines)
