{{ config(materialized='ephemeral') }}

with refunded as (
    select
        order_id,
        sum(refund_amount_vnd) as total_refunded_vnd
    from {{ ref('stg_haravan__order_refunds') }}
    group by 1
)

select
    o.*,
    -- Haravan's own field (may diverge from the sum in raw.haravan_orders.refunds[])
    o.total_refunded_vnd                                           as total_refunded_haravan_vnd,
    -- Sum we compute from the exploded refunds table
    coalesce(r.total_refunded_vnd, 0)                              as total_refunded_calc_vnd,
    o.total_price_vnd - coalesce(r.total_refunded_vnd, 0)          as net_revenue_vnd
from {{ ref('stg_haravan__orders') }} o
left join refunded r using (order_id)
