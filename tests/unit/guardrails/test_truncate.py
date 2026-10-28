import pytest

from rca.guardrails import truncate


def test_short_text_is_unchanged() -> None:
    assert truncate("hello", 10) == "hello"


@pytest.mark.parametrize("max_chars", [40, 100, 999, 6000])
def test_never_exceeds_cap_and_keeps_both_ends(max_chars: int) -> None:
    text = "HEAD-" + "x" * 20_000 + "-TAIL"
    out = truncate(text, max_chars)
    assert len(out) <= max_chars
    assert "characters truncated" in out
    if max_chars >= 100:
        assert out.startswith("HEAD-") and out.endswith("-TAIL")


def test_marker_reports_omitted_count() -> None:
    text = "a" * 1000
    out = truncate(text, 200)
    omitted = int(out.split("[... ")[1].split(" ")[0])
    assert len(out) - len(out.split("\n")[1]) - 2 + omitted == 1000


def test_invalid_cap() -> None:
    with pytest.raises(ValueError):
        truncate("x", 0)
