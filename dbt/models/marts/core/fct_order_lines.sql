{{ config(
    materialized='incremental',
    unique_key='line_key',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

select
    {{ dbt_utils.generate_surrogate_key(['order_id', 'line_item_id']) }} as line_key,
    {{ dbt_utils.generate_surrogate_key(['order_id']) }}                 as order_key,
    {{ dbt_utils.generate_surrogate_key(['variant_id']) }}               as variant_key,
    {{ dbt_utils.generate_surrogate_key(['product_id']) }}               as product_key,
    to_char(created_at::date, 'YYYYMMDD')::int                           as order_date_key,
    order_id,
    line_item_id,
    variant_id,
    product_id,
    quantity,
    unit_price_vnd,
    line_discount_vnd,
    line_total_vnd,
    created_at
from {{ ref('int_orders__line_items_exploded') }}
{% if is_incremental() %}
where created_at >= (
    select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }}
)
{% endif %}
