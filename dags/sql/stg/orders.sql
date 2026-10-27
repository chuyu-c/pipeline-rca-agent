-- stg.orders: normalized order headers for one logical_date.
DELETE FROM stg.orders WHERE logical_date = %(logical_date)s;

INSERT INTO stg.orders (order_id, logical_date, order_ts, customer_id, region, status)
SELECT
    o.order_id,
    o.logical_date,
    o.order_ts,
    o.customer_id,
    lower(trim(o.region)) AS region,
    lower(trim(o.status)) AS status
FROM raw.orders AS o
WHERE o.logical_date = %(logical_date)s;
