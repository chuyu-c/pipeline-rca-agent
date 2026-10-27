"""Raw-layer ingest tasks (`python:` tasks in the DAG) plus the static product dimension load.

Each ingest overwrites its `logical_date` partition, so a normal rerun is idempotent.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from rca.db import Connection
from rca.simulator.dag import TaskContext
from rca.simulator.generator import generate_inventory, generate_orders, generate_products


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


def ingest_orders(ctx: TaskContext) -> int:
    orders, items = generate_orders(ctx.logical_date, ctx.seed)
    batch_id = f"orders_{ctx.logical_date:%Y%m%d}_01"
    ctx.conn.execute("DELETE FROM raw.order_items WHERE logical_date = %s", (ctx.logical_date,))
    ctx.conn.execute("DELETE FROM raw.orders WHERE logical_date = %s", (ctx.logical_date,))
    n = _copy(
        ctx.conn,
        "raw.orders",
        ("order_id", "logical_date", "order_ts", "customer_id", "region", "status", "_batch_id"),
        (
            (o.order_id, o.logical_date, o.order_ts, o.customer_id, o.region, o.status, batch_id)
            for o in orders
        ),
    )
    n += _copy(
        ctx.conn,
        "raw.order_items",
        (
            "order_id",
            "line_no",
            "logical_date",
            "product_id",
            "quantity",
            "unit_price",
            "_batch_id",
        ),
        (
            (
                i.order_id,
                i.line_no,
                i.logical_date,
                i.product_id,
                i.quantity,
                i.unit_price,
                batch_id,
            )
            for i in items
        ),
    )
    return n


def ingest_inventory(ctx: TaskContext) -> int:
    snapshots = generate_inventory(ctx.logical_date, ctx.seed)
    ctx.conn.execute(
        "DELETE FROM raw.inventory_snapshots WHERE logical_date = %s", (ctx.logical_date,)
    )
    return _copy(
        ctx.conn,
        "raw.inventory_snapshots",
        ("logical_date", "product_id", "warehouse_id", "on_hand"),
        ((s.logical_date, s.product_id, s.warehouse_id, s.on_hand) for s in snapshots),
    )
