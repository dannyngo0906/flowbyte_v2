{{ config(materialized='ephemeral') }}

-- Variants observed in inventory adjustment line_items.
-- Some variant_ids appear here without ever showing up in /products or
-- order line_items (admin-adjusted SKUs that were never sold). Used by
-- dim_variants to ensure referential integrity for fct_inventory_adjustments.

with ranked as (
    select
        variant_id,
        product_id,
        sku,
        null::text                  as title,
        null::numeric(18,2)         as price_vnd,
        row_number() over (
            partition by variant_id
            order by created_at desc nulls last
        ) as rn
    from {{ ref('stg_haravan__inventory_adjustments') }}
    where variant_id is not null
)

select variant_id, product_id, sku, title, price_vnd
from ranked
where rn = 1
