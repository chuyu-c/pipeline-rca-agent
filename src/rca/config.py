"""Runtime configuration, loaded from environment variables (and `.env` if present).

Keep in sync with `.env.example`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database: `rca_app` for read/write ops; `rca_readonly` for agent SQL (DESIGN §4, §8).
    database_url: str = "postgresql://rca_app:rca_app@localhost:5432/rca"
    readonly_database_url: str = "postgresql://rca_readonly:rca_readonly@localhost:5432/rca"

    # LLM. The key uses the Anthropic SDK's conventional, unprefixed env var.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = "claude-sonnet-5-5"

    # Agent budgets (DESIGN §5.2).
    max_steps: int = Field(default=12, gt=0)
    max_tokens: int = Field(default=60_000, gt=0)

    # Retrieval (DESIGN §7).
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
