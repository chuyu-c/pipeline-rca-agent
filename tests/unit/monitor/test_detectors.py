from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from rca.monitor.config import VolumeCheck, load_config, parse_config
from rca.monitor.detectors import evaluate_freshness, evaluate_null_rate, evaluate_volume

D = date(2026, 3, 2)
CHECK = VolumeCheck(lookback_days=7, z_threshold=3.0, min_history=5)
WEEKLY = [270, 285, 300, 300, 330, 375, 360]  # realistic weekday seasonality
REPO_CONFIG = Path(__file__).parents[3] / "monitors" / "datasets.yaml"


def test_freshness() -> None:
    assert evaluate_freshness("raw.x", D, 10) is None
    alert = evaluate_freshness("raw.x", D, 0)
    assert alert and alert.error_class == "partition_missing" and alert.detector == "freshness"


def test_volume_normal_day_including_weekend_peak() -> None:
    for today in (270, 300, 375, 400):
        assert evaluate_volume("raw.x", D, today, WEEKLY, CHECK) is None


def test_volume_spike_and_drop() -> None:
    high = evaluate_volume("raw.x", D, 640, WEEKLY, CHECK)  # duplicate load: ~2x
    low = evaluate_volume("raw.x", D, 60, WEEKLY, CHECK)
    assert high and high.error_class == "volume_high" and high.payload["z_score"] > 3
    assert low and low.error_class == "volume_low" and low.payload["z_score"] < -3


def test_volume_needs_history_and_skips_empty_partition() -> None:
    assert evaluate_volume("raw.x", D, 10_000, WEEKLY[:4], CHECK) is None
    assert evaluate_volume("raw.x", D, 0, WEEKLY, CHECK) is None  # freshness's job


def test_volume_zero_variance_history_uses_floor() -> None:
    flat = [50] * 7
    assert evaluate_volume("mart.x", D, 51, flat, CHECK) is None  # one-row wobble
    assert evaluate_volume("mart.x", D, 100, flat, CHECK) is not None


def test_null_rate() -> None:
    assert evaluate_null_rate("raw.o", D, "region", 4, 100, 0.05) is None
    spike = evaluate_null_rate("raw.o", D, "region", 40, 100, 0.05)
    assert spike and spike.error_class == "null_rate:region" and spike.payload["null_rate"] == 0.4
    assert evaluate_null_rate("raw.o", D, "region", 0, 0, 0.05) is None


def test_config_applies_volume_defaults() -> None:
    config = parse_config({
        "defaults": {"volume": {"lookback_days": 14, "z_threshold": 4}},
        "datasets": {"raw.a": {"volume": {}}, "raw.b": {"volume": {"z_threshold": 2}},
                     "raw.c": {"freshness": True}},
    })  # fmt: skip
    a, b, c = (config.datasets[k] for k in ("raw.a", "raw.b", "raw.c"))
    assert a.volume and (a.volume.lookback_days, a.volume.z_threshold) == (14, 4)
    assert b.volume and (b.volume.lookback_days, b.volume.z_threshold) == (14, 2)
    assert c.volume is None and c.freshness


@pytest.mark.parametrize(
    "datasets",
    [
        {"orders": {"freshness": True}},  # not schema.table
        {"raw.orders; DROP TABLE x": {"freshness": True}},
        {"raw.orders": {"null_rate": {"region": 1.5}}},
        {"raw.orders": {"null_rate": {'Region"': 0.1}}},
    ],
)
def test_config_rejects_bad_input(datasets: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_config({"datasets": datasets})


def test_repo_config_is_valid() -> None:
    config = load_config(REPO_CONFIG)
    assert "raw.orders" in config.datasets
    assert all(m.freshness for m in config.datasets.values())
