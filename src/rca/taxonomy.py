"""Root-cause categories shared by RCAReport (DESIGN §6) and scenario ground truth (§10)."""

from typing import Literal, get_args

Category = Literal[
    "schema_drift",
    "late_upstream",
    "data_quality",
    "duplicate_load",
    "bad_deploy",
    "resource",
    "permission",
    "unknown",
]
CATEGORIES: tuple[str, ...] = get_args(Category)
