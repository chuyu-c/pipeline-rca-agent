from datetime import date

from rca.simulator.generator import (
    REGIONS,
    blank_regions,
    generate_inventory,
    generate_orders,
    generate_products,
)

D = date(2026, 3, 2)


def test_same_seed_and_date_is_identical() -> None:
    assert generate_products(7) == generate_products(7)
    assert generate_orders(D, 7) == generate_orders(D, 7)
    assert generate_inventory(D, 7) == generate_inventory(D, 7)


def test_date_output_independent_of_generation_order() -> None:
    other = date(2026, 3, 3)
    first = generate_orders(D, 7)
    generate_orders(other, 7)
    assert generate_orders(D, 7) == first


def test_different_date_or_seed_differs() -> None:
    assert generate_orders(D, 7) != generate_orders(date(2026, 3, 3), 7)
    assert generate_orders(D, 7) != generate_orders(D, 8)
    assert generate_inventory(D, 7) != generate_inventory(D, 8)


def test_order_invariants() -> None:
    product_ids = {p.product_id for p in generate_products(7)}
    orders, items = generate_orders(D, 7)

    assert 200 <= len(orders) <= 450
    order_ids = {o.order_id for o in orders}
    assert len(order_ids) == len(orders)
    assert all(o.region in REGIONS and o.logical_date == D for o in orders)
    assert all(o.order_ts.date() == D for o in orders)

    assert {i.order_id for i in items} == order_ids
    assert all(i.product_id in product_ids for i in items)
    assert all(i.quantity > 0 and i.unit_price > 0 for i in items)


def test_order_ids_do_not_collide_across_dates() -> None:
    a, _ = generate_orders(D, 7)
    b, _ = generate_orders(date(2026, 3, 3), 7)
    assert not {o.order_id for o in a} & {o.order_id for o in b}


def test_product_and_inventory_invariants() -> None:
    products = generate_products(7)
    assert len({p.sku for p in products}) == len(products)
    assert all(p.list_price > p.unit_cost > 0 for p in products)

    snapshots = generate_inventory(D, 7)
    assert len(snapshots) == len(products) * 2  # two warehouses
    assert all(s.on_hand >= 0 for s in snapshots)


def test_scale_multiplies_volume_without_id_collisions() -> None:
    base, _ = generate_orders(D, 7)
    big, big_items = generate_orders(D, 7, scale=100)
    assert 90 * len(base) < len(big) < 110 * len(base)
    assert len({o.order_id for o in big}) == len(big)
    next_day, _ = generate_orders(date(2026, 3, 3), 7, scale=100)
    assert not {o.order_id for o in big} & {o.order_id for o in next_day}
    assert {i.order_id for i in big_items} == {o.order_id for o in big}


def test_blank_regions_is_deterministic_and_roughly_the_rate() -> None:
    orders, _ = generate_orders(D, 7)
    blanked = blank_regions(orders, 0.4, 7)
    assert blanked == blank_regions(orders, 0.4, 7)
    share = sum(o.region is None for o in blanked) / len(orders)
    assert 0.3 < share < 0.5
    assert [o.order_id for o in blanked] == [o.order_id for o in orders]
    assert blank_regions([], 0.4, 7) == []
