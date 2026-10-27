"""Monitor configuration (`monitors/datasets.yaml`)."""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# Dataset and column names are interpolated into SQL as identifiers; keep them boring.
_IDENT = r"[a-z_][a-z0-9_]*"
_DATASET = re.compile(rf"^{_IDENT}\.{_IDENT}$")
_COLUMN = re.compile(rf"^{_IDENT}$")


class VolumeCheck(BaseModel):
    lookback_days: int = Field(default=7, ge=2)
    z_threshold: float = Field(default=3.0, gt=0)
    min_history: int = Field(default=5, ge=2)


class DatasetMonitors(BaseModel):
    partition_column: str = "logical_date"
    freshness: bool = False
    volume: VolumeCheck | None = None
    null_rate: dict[str, float] = {}  # column -> max allowed null fraction

    @field_validator("partition_column")
    @classmethod
    def _column_name(cls, value: str) -> str:
        if not _COLUMN.match(value):
            raise ValueError(f"invalid column name {value!r}")
        return value

    @field_validator("null_rate")
    @classmethod
    def _null_rate(cls, value: dict[str, float]) -> dict[str, float]:
        for column, threshold in value.items():
            if not _COLUMN.match(column):
                raise ValueError(f"invalid column name {column!r}")
            if not 0 <= threshold < 1:
                raise ValueError(f"null_rate threshold for {column!r} must be in [0, 1)")
        return value


class MonitorConfig(BaseModel):
    datasets: dict[str, DatasetMonitors]

    @field_validator("datasets")
    @classmethod
    def _dataset_names(cls, value: dict[str, DatasetMonitors]) -> dict[str, DatasetMonitors]:
        for name in value:
            if not _DATASET.match(name):
                raise ValueError(f"dataset must look like 'schema.table', got {name!r}")
        return value


def parse_config(raw: dict[str, Any]) -> MonitorConfig:
    """Validate a config dict, filling each dataset's `volume: {}` from `defaults.volume`."""
    default_volume = raw.get("defaults", {}).get("volume", {})
    datasets = {}
    for name, monitors in (raw.get("datasets") or {}).items():
        monitors = dict(monitors or {})
        if "volume" in monitors:
            monitors["volume"] = {**default_volume, **(monitors["volume"] or {})}
        datasets[name] = monitors
    return MonitorConfig.model_validate({"datasets": datasets})


def load_config(path: Path) -> MonitorConfig:
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")))
