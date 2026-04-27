-- Regression: stg_haravan__orders.customer_id used to read top-level
-- `payload->>'customer_id'`, but Haravan nests it under
-- `payload->'customer'->>'id'`. The bug produced 100% null customer_id
-- → all orders mapped to the same surrogate key in fct_orders.
--
-- Test fails (returns rows) if more than 95% of orders have null
-- customer_id when the underlying raw payload exposes a customer object.
-- Guest checkouts legitimately have null, but a sane shop has < 95%.

with order_count as (
    select count(*)::numeric as total
    from {{ ref('stg_haravan__orders') }}
),
null_count as (
    select count(*)::numeric as nulls
    from {{ ref('stg_haravan__orders') }}
    where customer_id is null
)
select
    null_count.nulls,
    order_count.total,
    null_count.nulls / nullif(order_count.total, 0) as null_fraction
from order_count, null_count
where
    order_count.total > 0
    and null_count.nulls / order_count.total > 0.95
