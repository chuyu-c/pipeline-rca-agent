-- Synthetic e-commerce warehouse (DESIGN §4). Every fact table is partitioned logically by
-- `logical_date`; tasks overwrite one partition per run.
-- Raw and stg tables intentionally have no primary keys: like real landing tables, they accept
-- whatever ingest writes (the `duplicate_load` scenario relies on this).

-- raw ---------------------------------------------------------------------------------------

CREATE TABLE raw.products (
    product_id  integer PRIMARY KEY,
    sku         text    NOT NULL UNIQUE,
    name        text    NOT NULL,
    category    text    NOT NULL,
    unit_cost   numeric(10, 2) NOT NULL,
    list_price  numeric(10, 2) NOT NULL
);

CREATE TABLE raw.orders (
    order_id      bigint      NOT NULL,
    logical_date  date        NOT NULL,
    order_ts      timestamptz NOT NULL,
    customer_id   integer     NOT NULL,
    region        text,
    status        text        NOT NULL,
    _batch_id     text        NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON raw.orders (logical_date);

CREATE TABLE raw.order_items (
    order_id      bigint      NOT NULL,
    line_no       integer     NOT NULL,
    logical_date  date        NOT NULL,
    product_id    integer     NOT NULL,
    quantity      integer     NOT NULL,
    unit_price    numeric(10, 2) NOT NULL,
    _batch_id     text        NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON raw.order_items (logical_date);

CREATE TABLE raw.inventory_snapshots (
    logical_date  date        NOT NULL,
    product_id    integer     NOT NULL,
    warehouse_id  text        NOT NULL,
    on_hand       integer     NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON raw.inventory_snapshots (logical_date);

-- stg ---------------------------------------------------------------------------------------

CREATE TABLE stg.orders (
    order_id      bigint      NOT NULL,
    logical_date  date        NOT NULL,
    order_ts      timestamptz NOT NULL,
    customer_id   integer     NOT NULL,
    region        text,
    status        text        NOT NULL
);
CREATE INDEX ON stg.orders (logical_date);

CREATE TABLE stg.order_items (
    order_id      bigint      NOT NULL,
    line_no       integer     NOT NULL,
    logical_date  date        NOT NULL,
    product_id    integer     NOT NULL,
    sku           text        NOT NULL,
    category      text        NOT NULL,
    quantity      integer     NOT NULL,
    unit_price    numeric(10, 2) NOT NULL,
    line_amount   numeric(12, 2) NOT NULL
);
CREATE INDEX ON stg.order_items (logical_date);

-- mart --------------------------------------------------------------------------------------

CREATE TABLE mart.daily_gmv_by_region (
    logical_date  date        NOT NULL,
    region        text,
    order_count   integer     NOT NULL,
    units         integer     NOT NULL,
    gmv           numeric(14, 2) NOT NULL
);
CREATE INDEX ON mart.daily_gmv_by_region (logical_date);

CREATE TABLE mart.sku_sell_through (
    logical_date       date    NOT NULL,
    product_id         integer NOT NULL,
    sku                text    NOT NULL,
    units_sold         integer NOT NULL,
    on_hand            integer NOT NULL,
    sell_through_rate  numeric(6, 4) NOT NULL
);
CREATE INDEX ON mart.sku_sell_through (logical_date);

CREATE TABLE mart.inventory_health (
    logical_date        date    NOT NULL,
    product_id          integer NOT NULL,
    sku                 text    NOT NULL,
    on_hand             integer NOT NULL,
    avg_daily_units_7d  numeric(10, 2) NOT NULL,
    days_of_cover       numeric(10, 1),
    is_stockout         boolean NOT NULL,
    is_low_stock        boolean NOT NULL
);
CREATE INDEX ON mart.inventory_health (logical_date);
