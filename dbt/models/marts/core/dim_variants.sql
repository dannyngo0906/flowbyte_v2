{{ config(materialized='table') }}

-- Variant dim = current state from /products API ∪ historical variants
-- observed in order line_items but no longer present in /products
-- (e.g. variants deleted from the shop). Historical rows have
-- is_historical = true and only the fields available from line_items;
-- everything else is NULL.

with current_variants as (
    select
        variant_id,
        product_id,
        title,
        sku,
        barcode,
        price_vnd,
        compare_at_price_vnd,
        inventory_quantity,
        option1,
        option2,
        option3,
        inventory_management,
        inventory_policy,
        weight,
        weight_unit,
        created_at,
        variant_updated_at,
        false as is_historical
    from {{ ref('stg_haravan__variants') }}
),

-- Historical variants observed across line_items + inventory_adjustments.
-- inv_adjustments often contains variants that never appear in any order
-- (admin-only stock corrections), so we union both sources. Pick line_items
-- attributes when present (priority=1), fall back to inv_adjustments (priority=2).
historical_raw as (
    select 1 as src_priority, variant_id, product_id, sku, title, price_vnd
    from {{ ref('int_orders__line_item_variants') }}
    union all
    select 2 as src_priority, variant_id, product_id, sku, title, price_vnd
    from {{ ref('int_inv_adjustments__variants') }}
),

historical_seen as (
    select variant_id, product_id, sku, title, price_vnd
    from (
        select *,
               row_number() over (partition by variant_id order by src_priority) as rn
        from historical_raw
    ) ranked
    where rn = 1
),

historical_variants as (
    select
        h.variant_id,
        h.product_id,
        h.title,
        h.sku,
        null::text                  as barcode,
        h.price_vnd,
        null::numeric(18,2)         as compare_at_price_vnd,
        null::int                   as inventory_quantity,
        null::text                  as option1,
        null::text                  as option2,
        null::text                  as option3,
        null::text                  as inventory_management,
        null::text                  as inventory_policy,
        null::numeric(18,3)         as weight,
        null::text                  as weight_unit,
        null::timestamptz           as created_at,
        null::timestamptz           as variant_updated_at,
        true as is_historical
    from historical_seen h
    left join current_variants c on c.variant_id = h.variant_id
    where c.variant_id is null
),

-- Sentinel row for NULL variant_id (line items without variant — shipping,
-- gift wrap, custom items). Lets fct_order_lines.variant_key resolve.
unknown_placeholder as (
    select
        null::bigint               as variant_id,
        null::bigint               as product_id,
        '(unknown)'::text          as title,
        null::text                 as sku,
        null::text                 as barcode,
        null::numeric(18,2)        as price_vnd,
        null::numeric(18,2)        as compare_at_price_vnd,
        null::int                  as inventory_quantity,
        null::text                 as option1,
        null::text                 as option2,
        null::text                 as option3,
        null::text                 as inventory_management,
        null::text                 as inventory_policy,
        null::numeric(18,3)        as weight,
        null::text                 as weight_unit,
        null::timestamptz          as created_at,
        null::timestamptz          as variant_updated_at,
        true                       as is_historical
),

unioned as (
    select * from current_variants
    union all
    select * from historical_variants
    union all
    select * from unknown_placeholder
)

select
    {{ dbt_utils.generate_surrogate_key(['variant_id']) }}      as variant_key,
    {{ dbt_utils.generate_surrogate_key(['product_id']) }}      as product_key,
    *
from unioned
