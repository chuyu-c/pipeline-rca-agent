-- mart.sku_sell_through: share of available units sold per SKU for the day.
DELETE FROM mart.sku_sell_through WHERE logical_date = %(logical_date)s;

INSERT INTO mart.sku_sell_through (
    logical_date, product_id, sku, units_sold, on_hand, sell_through_rate
)
WITH stock AS (
    SELECT s.logical_date, s.product_id, sum(s.on_hand) AS on_hand
    FROM raw.inventory_snapshots AS s
    WHERE s.logical_date = %(logical_date)s
    GROUP BY s.logical_date, s.product_id
),
sold AS (
    SELECT i.product_id, sum(i.quantity) AS units_sold
    FROM stg.order_items AS i
    WHERE i.logical_date = %(logical_date)s
    GROUP BY i.product_id
)
SELECT
    st.logical_date,
    st.product_id,
    p.sku,
    coalesce(so.units_sold, 0) AS units_sold,
    st.on_hand,
    CASE
        WHEN st.on_hand + coalesce(so.units_sold, 0) = 0 THEN 0
        ELSE round(coalesce(so.units_sold, 0)::numeric / (st.on_hand + coalesce(so.units_sold, 0)), 4)
    END AS sell_through_rate
FROM stock AS st
JOIN raw.products AS p
    ON p.product_id = st.product_id
LEFT JOIN sold AS so
    ON so.product_id = st.product_id;
