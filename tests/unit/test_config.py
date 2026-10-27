import pytest
from pydantic import SecretStr

from rca.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RCA_MAX_STEPS", raising=False)


def test_defaults_load_without_env() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_steps == 12
    assert settings.anthropic_api_key is None


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MAX_STEPS", "5")
    assert Settings(_env_file=None).max_steps == 5  # type: ignore[call-arg]


def test_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert "sk-test-not-real" not in repr(settings)
