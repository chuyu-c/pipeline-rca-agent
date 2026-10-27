"""Deterministic synthetic e-commerce data. Pure functions, no I/O.

Each logical date gets its own RNG (`seed:kind:date`), so the rows for a date never depend on which
other dates were generated or in what order — reruns and backfills are reproducible.
All names are invented; no real companies, people or products.
"""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

REGIONS = ("north", "south", "east", "west", "central")
REGION_WEIGHTS = (0.22, 0.18, 0.25, 0.20, 0.15)
WAREHOUSES = ("wh-east", "wh-west")
CATEGORIES = ("apparel", "home", "kitchen", "outdoor", "electronics", "toys")
_NOUNS = ("Widget", "Gadget", "Gizmo", "Bracket", "Lantern", "Kettle", "Tote", "Puzzle")
_STATUSES = ("completed", "cancelled", "returned")
_STATUS_WEIGHTS = (0.92, 0.05, 0.03)
# Mon..Sun multipliers on base order volume.
_WEEKDAY_FACTOR = (0.90, 0.95, 1.00, 1.00, 1.10, 1.25, 1.20)

N_PRODUCTS = 50
BASE_ORDERS_PER_DAY = 300
N_CUSTOMERS = 5_000
# orders/day x mean lines/order (2.5) x mean quantity/line (1.4)
_EXPECTED_UNITS_PER_DAY = BASE_ORDERS_PER_DAY * 2.5 * 1.4


@dataclass(frozen=True)
class Product:
    product_id: int
    sku: str
    name: str
    category: str
    unit_cost: Decimal
    list_price: Decimal


@dataclass(frozen=True)
class Order:
    order_id: int
    logical_date: date
    order_ts: datetime
    customer_id: int
    region: str | None
    status: str


@dataclass(frozen=True)
class OrderItem:
    order_id: int
    line_no: int
    logical_date: date
    product_id: int
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class InventorySnapshot:
    logical_date: date
    product_id: int
    warehouse_id: str
    on_hand: int


def _money(x: float) -> Decimal:
    return Decimal(str(round(x, 2)))


def generate_products(seed: int) -> list[Product]:
    rng = random.Random(f"{seed}:products")
    products = []
    for pid in range(1, N_PRODUCTS + 1):
        cost = rng.uniform(2, 80)
        products.append(
            Product(
                product_id=pid,
                sku=f"SKU-{pid:05d}",
                name=f"{rng.choice(_NOUNS)} {rng.choice('ABCDEFGH')}-{rng.randint(1, 999):03d}",
                category=rng.choice(CATEGORIES),
                unit_cost=_money(cost),
                list_price=_money(cost * rng.uniform(1.3, 2.5)),
            )
        )
    return products


def _popularity(products: list[Product]) -> list[float]:
    """Zipf-like weights: a few SKUs sell much more than the long tail."""
    return [1.0 / (rank**0.8) for rank in range(1, len(products) + 1)]


def generate_orders(logical_date: date, seed: int) -> tuple[list[Order], list[OrderItem]]:
    rng = random.Random(f"{seed}:orders:{logical_date.isoformat()}")
    products = generate_products(seed)
    weights = _popularity(products)

    n_orders = round(
        BASE_ORDERS_PER_DAY * _WEEKDAY_FACTOR[logical_date.weekday()] * rng.uniform(0.9, 1.1)
    )
    day_start = datetime.combine(logical_date, time.min, tzinfo=UTC)
    # Unique across dates without coordination: date ordinal prefix + sequence.
    id_base = logical_date.toordinal() * 10_000

    orders: list[Order] = []
    items: list[OrderItem] = []
    for i in range(n_orders):
        order_id = id_base + i
        orders.append(
            Order(
                order_id=order_id,
                logical_date=logical_date,
                order_ts=day_start + timedelta(seconds=rng.randrange(86_400)),
                customer_id=rng.randint(1, N_CUSTOMERS),
                region=rng.choices(REGIONS, REGION_WEIGHTS)[0],
                status=rng.choices(_STATUSES, _STATUS_WEIGHTS)[0],
            )
        )
        for line_no in range(1, rng.randint(1, 4) + 1):
            product = rng.choices(products, weights)[0]
            discount = rng.choices((0.0, 0.1, 0.2), (0.8, 0.15, 0.05))[0]
            items.append(
                OrderItem(
                    order_id=order_id,
                    line_no=line_no,
                    logical_date=logical_date,
                    product_id=product.product_id,
                    quantity=rng.choices((1, 2, 3), (0.7, 0.2, 0.1))[0],
                    unit_price=_money(float(product.list_price) * (1 - discount)),
                )
            )
    return orders, items


def generate_inventory(logical_date: date, seed: int) -> list[InventorySnapshot]:
    rng = random.Random(f"{seed}:inventory:{logical_date.isoformat()}")
    products = generate_products(seed)
    weights = _popularity(products)
    snapshots = []
    for product, weight in zip(products, weights, strict=True):
        daily_units = _EXPECTED_UNITS_PER_DAY * weight / sum(weights)
        for wh in WAREHOUSES:
            # Stable per-SKU stock level (~8-30 days of cover, split across warehouses),
            # with daily noise and occasional stockouts.
            cover_days = random.Random(f"{seed}:stock:{product.product_id}:{wh}").uniform(8, 30)
            base = max(10.0, daily_units * cover_days / len(WAREHOUSES))
            stocked_out = rng.random() < 0.03
            on_hand = 0 if stocked_out else max(0, round(rng.gauss(base, base * 0.25)))
            snapshots.append(InventorySnapshot(logical_date, product.product_id, wh, on_hand))
    return snapshots
