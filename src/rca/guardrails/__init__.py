"""Guardrails between the LLM and the system (DESIGN §8)."""

from rca.guardrails.redact import Redaction, redact
from rca.guardrails.sql_guard import SqlGuardError, ValidatedSql, validate
from rca.guardrails.truncate import truncate


def sanitize_tool_output(text: str, max_chars: int | None = None) -> str:
    """Every tool result passes through here before the LLM sees it: redact, then truncate.

    Redacting first means a secret can't survive by straddling the truncation boundary.
    """
    redacted = redact(text).text
    return truncate(redacted) if max_chars is None else truncate(redacted, max_chars)


__all__ = [
    "Redaction",
    "SqlGuardError",
    "ValidatedSql",
    "redact",
    "sanitize_tool_output",
    "truncate",
    "validate",
]
