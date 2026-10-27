-- mart.inventory_health: stock position vs trailing 7-day demand per SKU.
DELETE FROM mart.inventory_health WHERE logical_date = %(logical_date)s;

INSERT INTO mart.inventory_health (
    logical_date, product_id, sku, on_hand, avg_daily_units_7d, days_of_cover,
    is_stockout, is_low_stock
)
WITH stock AS (
    SELECT s.logical_date, s.product_id, sum(s.on_hand) AS on_hand
    FROM raw.inventory_snapshots AS s
    WHERE s.logical_date = %(logical_date)s
    GROUP BY s.logical_date, s.product_id
),
demand AS (
    SELECT i.product_id, sum(i.quantity) / 7.0 AS avg_daily_units
    FROM stg.order_items AS i
    WHERE i.logical_date > %(logical_date)s::date - 7
        AND i.logical_date <= %(logical_date)s
    GROUP BY i.product_id
)
SELECT
    st.logical_date,
    st.product_id,
    p.sku,
    st.on_hand,
    round(coalesce(d.avg_daily_units, 0), 2) AS avg_daily_units_7d,
    CASE
        WHEN coalesce(d.avg_daily_units, 0) = 0 THEN NULL
        ELSE round(st.on_hand / d.avg_daily_units, 1)
    END AS days_of_cover,
    st.on_hand = 0 AS is_stockout,
    st.on_hand > 0 AND st.on_hand < 7 * coalesce(d.avg_daily_units, 0) AS is_low_stock
FROM stock AS st
JOIN raw.products AS p
    ON p.product_id = st.product_id
LEFT JOIN demand AS d
    ON d.product_id = st.product_id;
