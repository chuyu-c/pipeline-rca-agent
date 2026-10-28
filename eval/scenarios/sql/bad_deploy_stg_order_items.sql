-- stg.order_items: order lines enriched with product attributes and line totals.
DELETE FROM stg.order_items WHERE logical_date = %(logical_date)s;

INSERT INTO stg.order_items (
    order_id, line_no, logical_date, product_id, sku, category, quantity, unit_price, line_amount
)
SELECT
    i.order_id,
    i.line_no,
    i.logical_date,
    i.product_id,
    p.sku,
    p.category,
    i.quantity,
    i.unit_price,
    i.quantity * i.unit_price AS line_amount
FROM raw.order_items AS i
JOIN raw.products AS src
    ON src.product_id = i.product_id
JOIN raw.products AS p
    ON p.category = src.category
WHERE i.logical_date = %(logical_date)s;
