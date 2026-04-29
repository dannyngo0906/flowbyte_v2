{{ config(materialized='ephemeral') }}

-- Products observed in order line_items, including those deleted from /products API.
-- Used by dim_products to preserve referential integrity for historical orders.
-- Picks most-recent line_item per product_id (by order created_at) for title.
-- Line items don't carry vendor/product_type/handle, so those fields stay NULL
-- for historical-only products.

with line_items as (
    select
        order_id,
        created_at as order_created_at,
        line_items_json
    from {{ ref('stg_haravan__orders') }}
),

exploded as (
    select
        order_created_at,
        jsonb_array_elements(coalesce(line_items_json, '[]'::jsonb)) as li
    from line_items
),

ranked as (
    select
        nullif(li->>'product_id', '')::bigint        as product_id,
        -- Strip variant suffix from "Product Name - Variant Title" if present
        regexp_replace(coalesce(li->>'title', ''), ' - [^-]+$', '') as title,
        order_created_at,
        row_number() over (
            partition by nullif(li->>'product_id', '')::bigint
            order by order_created_at desc
        ) as rn
    from exploded
    where li->>'product_id' is not null
)

select product_id, nullif(title, '') as title
from ranked
where rn = 1
