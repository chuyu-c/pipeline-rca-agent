"""Read injected fault conditions (`sim.faults`) for fake sources and tasks.

Written by `rca.faults`; empty in a healthy environment, in which case everything behaves normally.
"""

from typing import Any

from rca.db import Connection


def load_fault(conn: Connection, target: str) -> dict[str, Any]:
    """Params for 'source:<name>' or 'task:<task_id>', or {} when nothing is injected."""
    row = conn.execute("SELECT params FROM sim.faults WHERE target = %s", (target,)).fetchone()
    return dict(row["params"]) if row else {}
