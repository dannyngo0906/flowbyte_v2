{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['variant_id']) }}      as variant_key,
    {{ dbt_utils.generate_surrogate_key(['product_id']) }}      as product_key,
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
    variant_updated_at
from {{ ref('stg_haravan__variants') }}
