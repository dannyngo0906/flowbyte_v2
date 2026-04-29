{{ config(materialized='table') }}

-- Product dim = current state from /products API ∪ historical products
-- observed in order line_items but no longer present in /products
-- (e.g. products deleted from the shop). Historical rows have
-- is_historical = true and carry only `title` from line_items;
-- vendor/product_type/handle/status/tags are NULL for those rows.

with current_products as (
    select
        product_id,
        title,
        handle,
        vendor,
        product_type,
        status,
        tags,
        created_at,
        product_updated_at,
        published_at,
        false as is_historical
    from {{ ref('stg_haravan__products') }}
),

-- Historical products from line_items + inventory_adjustments.
historical_raw as (
    select 1 as src_priority, product_id, title
    from {{ ref('int_orders__line_item_products') }}
    union all
    select 2 as src_priority, product_id, title
    from {{ ref('int_inv_adjustments__products') }}
),

historical_seen as (
    select product_id, coalesce(title, '(unknown)') as title
    from (
        select *,
               row_number() over (partition by product_id order by src_priority) as rn
        from historical_raw
    ) ranked
    where rn = 1
),

historical_products as (
    select
        h.product_id,
        h.title,
        null::text          as handle,
        null::text          as vendor,
        null::text          as product_type,
        null::text          as status,
        null::text          as tags,
        null::timestamptz   as created_at,
        null::timestamptz   as product_updated_at,
        null::timestamptz   as published_at,
        true as is_historical
    from historical_seen h
    left join current_products c on c.product_id = h.product_id
    where c.product_id is null
),

-- Sentinel row for NULL product_id (line items without product reference).
unknown_placeholder as (
    select
        null::bigint        as product_id,
        '(unknown)'::text   as title,
        null::text          as handle,
        null::text          as vendor,
        null::text          as product_type,
        null::text          as status,
        null::text          as tags,
        null::timestamptz   as created_at,
        null::timestamptz   as product_updated_at,
        null::timestamptz   as published_at,
        true                as is_historical
),

unioned as (
    select * from current_products
    union all
    select * from historical_products
    union all
    select * from unknown_placeholder
)

select
    {{ dbt_utils.generate_surrogate_key(['product_id']) }} as product_key,
    *
from unioned
