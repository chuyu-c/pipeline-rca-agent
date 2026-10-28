import pytest

from rca.guardrails import redact, sanitize_tool_output


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("contact jane.doe@example.com about it", "jane.doe@example.com"),
        ("connecting to postgresql://loader:hunter2@db:5432/rca", "hunter2"),
        ("Authorization: Bearer abcDEF123456789xyz", "abcDEF123456789xyz"),
        ("api_key=sk-test-abcdefghijklmnop1234", "sk-test-abcdefghijklmnop1234"),
        ("aws key AKIAABCDEFGHIJKLMNOP leaked", "AKIAABCDEFGHIJKLMNOP"),
        ('config {"password": "s3cr3t!"}', "s3cr3t!"),
        ("password='p@ss word'", "p@ss word"),
        ("token: ghp_0123456789abcdefghij", "ghp_0123456789abcdefghij"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijk", "eyJhbGciOiJIUzI1NiJ9"),
    ],
)
def test_secrets_and_emails_are_masked(text: str, secret: str) -> None:
    result = redact(text)
    assert secret not in result.text
    assert result.replacements >= 1
    assert "REDACTED" in result.text


def test_ordinary_log_lines_are_untouched() -> None:
    line = (
        "Task failed with UndefinedColumn: column i.unit_price does not exist; "
        "loaded landing/orders/2026-03-10.csv: 301 orders, rows=742"
    )
    assert redact(line) == (line, 0)


def test_url_user_is_kept_password_dropped() -> None:
    assert (
        redact("postgresql://loader:hunter2@db/rca").text == "postgresql://loader:[REDACTED]@db/rca"
    )


def test_sanitize_redacts_before_truncating() -> None:
    # The secret sits where truncation would cut; it must not survive in either half.
    text = "a" * 50 + " password=topsecretvalue " + "b" * 50
    out = sanitize_tool_output(text, max_chars=60)
    assert "topsecret" not in out and len(out) <= 60
