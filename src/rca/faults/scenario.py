"""Fault scenario definitions (`eval/scenarios/*.yaml`, DESIGN §10)."""

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from rca.taxonomy import Category


class Step(BaseModel):
    """One setup/teardown action, run as the admin role. Exactly one of `sql` / `python`.

    `sql` may use `%(logical_date)s`. `python` is 'module:function', called as
    `function(ctx: FaultContext, **args)`.
    """

    sql: str | None = None
    python: str | None = None
    args: dict[str, Any] = {}

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.sql is None) == (self.python is None):
            raise ValueError("a step needs exactly one of sql / python")
        if self.sql is not None and self.args:
            raise ValueError("args only apply to python steps")
        return self


class ExpectedAlert(BaseModel):
    detector: str
    dataset: str
    error_class: str | None = None


class GroundTruth(BaseModel):
    category: Category
    # A table ('raw.orders'), column ('raw.orders.region'), task ('ingest_orders') or
    # deploy ('deploy:d-1042').
    root_entity: str
    expected_doc_ids: list[str] = Field(min_length=1)
    expected_impacted: list[str] = []


class Scenario(BaseModel):
    id: str
    title: str
    description: str = ""
    setup: list[Step] = Field(min_length=1)
    teardown: list[Step] = []
    # Alerts the monitors must raise (a subset; others may also fire).
    expected_alerts: list[ExpectedAlert] = Field(min_length=1)
    ground_truth: GroundTruth
    _dir: Path = PrivateAttr(default=Path("."))

    @property
    def dir(self) -> Path:
        """Directory of the scenario file; python hooks resolve relative paths against it."""
        return self._dir


def load_scenario(path: Path) -> Scenario:
    scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if scenario.id != path.stem:
        raise ValueError(f"{path.name}: id {scenario.id!r} must match the file name")
    scenario._dir = path.parent
    return scenario


def load_scenarios(scenarios_dir: Path) -> dict[str, Scenario]:
    return {p.stem: load_scenario(p) for p in sorted(scenarios_dir.glob("*.yaml"))}
