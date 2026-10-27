-- mart.daily_gmv_by_region: completed-order GMV per region.
DELETE FROM mart.daily_gmv_by_region WHERE logical_date = %(logical_date)s;

INSERT INTO mart.daily_gmv_by_region (logical_date, region, order_count, units, gmv)
SELECT
    o.logical_date,
    o.region,
    count(DISTINCT o.order_id) AS order_count,
    sum(i.quantity) AS units,
    sum(i.line_amount) AS gmv
FROM stg.orders AS o
JOIN stg.order_items AS i
    ON i.order_id = o.order_id
    AND i.logical_date = o.logical_date
WHERE o.logical_date = %(logical_date)s
    AND o.status = 'completed'
GROUP BY o.logical_date, o.region;
