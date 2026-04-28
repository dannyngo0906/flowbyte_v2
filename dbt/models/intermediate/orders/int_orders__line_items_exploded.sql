{{ config(materialized='ephemeral') }}

with base as (
    select
        order_id, customer_id, location_id, created_at, currency, line_items_json
    from {{ ref('stg_haravan__orders') }}
),

exploded as (
    select
        order_id, customer_id, location_id, created_at, currency,
        jsonb_array_elements(coalesce(line_items_json, '[]'::jsonb)) as li
    from base
)

select
    order_id, customer_id, location_id, created_at, currency,
    (li->>'id')::bigint                                       as line_item_id,
    nullif(li->>'variant_id', '')::bigint                     as variant_id,
    nullif(li->>'product_id', '')::bigint                     as product_id,
    nullif(li->>'quantity', '')::int                          as quantity,
    nullif(li->>'price', '')::numeric(18,2)                   as unit_price_vnd,
    -- price_original = original list price before member/promo discounts (matches Haravan admin "Doanh thu")
    coalesce(nullif(li->>'price_original', '')::numeric(18,2),
             nullif(li->>'price', '')::numeric(18,2))         as unit_price_original_vnd,
    nullif(li->>'total_discount', '')::numeric(18,2)          as line_discount_vnd,
    -- line_total_vnd = actual selling price × qty (giá bán thực, không tính hàng tặng kèm/promo)
    coalesce(nullif(li->>'price', '')::numeric(18,2), 0)
        * coalesce(nullif(li->>'quantity', '')::int, 0)        as line_total_vnd
from exploded
where li->>'id' is not null
