"""Bound the size of tool output given to the LLM (DESIGN §8.5)."""

DEFAULT_MAX_CHARS = 6_000


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS, *, head_ratio: float = 0.6) -> str:
    """Keep the head and the tail with an explicit marker in between.

    Errors usually sit at the end of logs and the context at the start, so both ends are kept.
    The result never exceeds `max_chars`.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return text
    marker = f"\n[... {len(text) - max_chars} characters truncated ...]\n"
    # Recompute with the marker's own length so the total stays within the cap.
    omitted = len(text) - max(0, max_chars - len(marker))
    marker = f"\n[... {omitted} characters truncated ...]\n"
    budget = max(0, max_chars - len(marker))
    head = int(budget * head_ratio)
    tail = budget - head
    return text[:head] + marker + (text[-tail:] if tail else "")
