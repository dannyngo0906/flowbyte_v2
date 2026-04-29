{{ config(materialized='ephemeral') }}

-- Variants observed in order line_items, including those deleted from /products API.
-- Used by dim_variants to preserve referential integrity for historical orders.
-- Picks most-recent line_item per variant_id (by order created_at) so that
-- title/sku/price reflect the latest known state.

with line_items as (
    select
        order_id,
        created_at as order_created_at,
        line_items_json
    from {{ ref('stg_haravan__orders') }}
),

exploded as (
    select
        order_id,
        order_created_at,
        jsonb_array_elements(coalesce(line_items_json, '[]'::jsonb)) as li
    from line_items
),

ranked as (
    select
        nullif(li->>'variant_id', '')::bigint                  as variant_id,
        nullif(li->>'product_id', '')::bigint                  as product_id,
        li->>'sku'                                             as sku,
        coalesce(li->>'variant_title', li->>'title')           as title,
        nullif(li->>'price', '')::numeric(18,2)                as price_vnd,
        order_created_at,
        row_number() over (
            partition by nullif(li->>'variant_id', '')::bigint
            order by order_created_at desc
        ) as rn
    from exploded
    where li->>'variant_id' is not null
)

select variant_id, product_id, sku, title, price_vnd
from ranked
where rn = 1
