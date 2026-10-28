"""Runtime configuration, loaded from environment variables (and `.env` if present).

Keep in sync with `.env.example`.
"""

from functools import lru_cache
from pathlib import Path
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
    # Superuser from the compose `db` service; only migrations and test-DB setup use it.
    admin_database_url: str = "postgresql://rca_admin:rca_admin@localhost:5432/rca"

    # Simulator (DESIGN §3.1).
    dags_dir: Path = Path("dags")
    sim_seed: int = 42

    # Monitors (DESIGN §5.1).
    monitors_config: Path = Path("monitors/datasets.yaml")

    # Fault scenarios with ground truth (DESIGN §10).
    scenarios_dir: Path = Path("eval/scenarios")

    # LLM. The key uses the Anthropic SDK's conventional, unprefixed env var.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = "claude-sonnet-5-5"

    # Agent budgets (DESIGN §5.2).
    max_steps: int = Field(default=12, gt=0)
    max_tokens: int = Field(default=60_000, gt=0)

    # Retrieval (DESIGN §7).
    oncall_dir: Path = Path("oncall")
    embedder: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path | None = None  # fastembed model cache; default: a temp dir

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
