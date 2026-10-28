"""Raw-layer ingest tasks (`python:` tasks in the DAG) plus the static product dimension load.

Each ingest overwrites its `logical_date` partition, so a normal rerun is idempotent.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from rca.db import Connection
from rca.simulator.conditions import load_fault
from rca.simulator.dag import TaskContext
from rca.simulator.generator import (
    blank_regions,
    generate_inventory,
    generate_orders,
    generate_products,
)


def _copy(
    conn: Connection, table: str, columns: Sequence[str], rows: Iterable[tuple[Any, ...]]
) -> int:
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n


def load_products(conn: Connection, seed: int) -> int:
    """Upsert the static product dimension. Not a DAG task: products change rarely."""
    rows = [
        (p.product_id, p.sku, p.name, p.category, p.unit_cost, p.list_price)
        for p in generate_products(seed)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO raw.products (product_id, sku, name, category, unit_cost, list_price)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (product_id) DO NOTHING",
            rows,
        )
    return len(rows)


ORDER_COLUMNS = (
    "order_id", "logical_date", "order_ts", "customer_id", "region", "status", "_batch_id",
)  # fmt: skip
ITEM_COLUMNS = (
    "order_id", "line_no", "logical_date", "product_id", "quantity", "unit_price", "_batch_id",
)  # fmt: skip


def ingest_orders(ctx: TaskContext) -> int:
    """Load the day's order export from the (simulated) landing bucket.

    Honours source faults (`sim.faults`, target `source:orders`):
      volume_multiplier  scale the export (sale event / bad backfill)
      null_region_rate   the source sends this fraction of orders without a region
      column_renames     the source renamed columns; the loader auto-evolved raw tables to match
      replay_batch       an upload ack times out and the batch is re-sent (non-idempotent append)
      header_comment     free text from the export's header, echoed into the task log
    """
    fault = load_fault(ctx.conn, "source:orders")
    d = ctx.logical_date
    path = f"landing/orders/{d.isoformat()}.csv"
    batch_id = f"orders_{d:%Y%m%d}_01"

    orders, items = generate_orders(d, ctx.seed, scale=float(fault.get("volume_multiplier", 1)))
    if rate := fault.get("null_region_rate"):
        orders = blank_regions(orders, float(rate), ctx.seed)
    if comment := fault.get("header_comment"):
        ctx.log(f"{path} header comment: {comment}")

    renames: dict[str, str] = fault.get("column_renames", {})
    item_columns = [renames.get(c, c) for c in ITEM_COLUMNS]
    order_rows = [
        (o.order_id, o.logical_date, o.order_ts, o.customer_id, o.region, o.status, batch_id)
        for o in orders
    ]
    item_rows = [
        (i.order_id, i.line_no, i.logical_date, i.product_id, i.quantity, i.unit_price, batch_id)
        for i in items
    ]

    ctx.conn.execute("DELETE FROM raw.order_items WHERE logical_date = %s", (d,))
    ctx.conn.execute("DELETE FROM raw.orders WHERE logical_date = %s", (d,))
    sends = 2 if fault.get("replay_batch") else 1
    n = 0
    for send in range(1, sends + 1):
        if send > 1:
            ctx.log(
                f"Upload acknowledgement for batch {batch_id} timed out after 30s; "
                "re-sending batch",
                "WARNING",
            )
        n += _copy(ctx.conn, "raw.orders", ORDER_COLUMNS, order_rows)
        n += _copy(ctx.conn, "raw.order_items", item_columns, item_rows)
        ctx.log(
            f"Loaded batch {batch_id} from {path}: {len(orders)} orders, {len(items)} order items"
        )
    return n


def ingest_inventory(ctx: TaskContext) -> int:
    """Load the day's inventory snapshot. Fault `source:inventory` {missing: true}: the file
    hasn't landed, so nothing is loaded — and, as configured, that isn't a task failure."""
    fault = load_fault(ctx.conn, "source:inventory")
    d = ctx.logical_date
    path = f"landing/inventory/{d.isoformat()}.csv"
    ctx.conn.execute("DELETE FROM raw.inventory_snapshots WHERE logical_date = %s", (d,))
    if fault.get("missing"):
        ctx.log(f"No file found at {path}; source has not delivered yet. Loaded 0 rows", "WARNING")
        return 0

    snapshots = generate_inventory(d, ctx.seed)
    n = _copy(
        ctx.conn,
        "raw.inventory_snapshots",
        ("logical_date", "product_id", "warehouse_id", "on_hand"),
        ((s.logical_date, s.product_id, s.warehouse_id, s.on_hand) for s in snapshots),
    )
    ctx.log(f"Loaded {path}: {n} snapshot rows")
    return n
