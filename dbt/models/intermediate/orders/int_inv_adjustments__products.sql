{{ config(materialized='ephemeral') }}

-- Products observed in inventory adjustment line_items.
-- Same role as int_inv_adjustments__variants for product dim integrity.
-- Inventory adjustments don't carry product titles, so title is NULL —
-- dim_products will display these rows as "(unknown)" via coalesce.

with ranked as (
    select
        product_id,
        null::text as title,
        row_number() over (
            partition by product_id
            order by created_at desc nulls last
        ) as rn
    from {{ ref('stg_haravan__inventory_adjustments') }}
    where product_id is not null
)

select product_id, title
from ranked
where rn = 1
