"""Mask secrets and personal data in text before it reaches the LLM (DESIGN §8.4)."""

import re
from typing import NamedTuple


class _Rule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    replacement: str


# Order matters: URL credentials before generic key=value, tokens before emails.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "url_credentials",
        re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^\s:/@]+):(?P<pw>[^\s@/]+)@", re.I),
        r"\g<scheme>\g<user>:[REDACTED]@",
    ),
    _Rule(
        "bearer",
        re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]{8,}"),
        r"\1 [REDACTED]",
    ),
    _Rule("jwt", re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}\b"), "[REDACTED_JWT]"),
    _Rule(
        "api_key",
        re.compile(r"\b(?:sk|pk|rk)[-_](?:[a-z]+[-_])?[A-Za-z0-9]{16,}\b|\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED_KEY]",
    ),
    _Rule(
        "secret_assignment",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b"
            # optional closing quote covers JSON keys: {"password": "..."}
            r"([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    _Rule(
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
)


class Redaction(NamedTuple):
    text: str
    replacements: int


def redact(text: str) -> Redaction:
    total = 0
    for rule in _RULES:
        text, n = rule.pattern.subn(rule.replacement, text)
        total += n
    return Redaction(text, total)
