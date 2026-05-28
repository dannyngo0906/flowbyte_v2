{{ config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

select
    {{ dbt_utils.generate_surrogate_key(['order_id']) }}         as order_key,
    order_id,
    {{ dbt_utils.generate_surrogate_key(['customer_id']) }}      as customer_key,
    {{ dbt_utils.generate_surrogate_key(['location_id']) }}      as location_key,
    to_char(created_at::date, 'YYYYMMDD')::int                   as order_date_key,
    created_at,
    order_updated_at,
    financial_status,
    fulfillment_status,
    currency,
    subtotal_vnd,
    total_discount_vnd,
    total_tax_vnd,
    total_shipping_vnd,
    total_price_vnd,
    total_refunded_haravan_vnd,                                  -- Haravan-reported (may drift)
    total_refunded_calc_vnd                                      as total_refunded_vnd,  -- our authoritative sum
    net_revenue_vnd
from {{ ref('int_orders__totals_with_refund_net') }}
{% if is_incremental() %}
-- Buffer window to capture late-arriving orders (Haravan API can deliver hours/days late).
-- Without it, an order whose `order_updated_at` is older than the current fct_orders max
-- but ingested for the first time today would be silently filtered out.
where order_updated_at >= (
    select coalesce(max(order_updated_at), '1900-01-01'::timestamptz) from {{ this }}
) - interval '30 days'
{% endif %}
