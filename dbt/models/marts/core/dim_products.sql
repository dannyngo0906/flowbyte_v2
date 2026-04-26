{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['product_id']) }}     as product_key,
    product_id,
    title,
    handle,
    vendor,
    product_type,
    status,
    tags,
    created_at,
    product_updated_at,
    published_at
from {{ ref('stg_haravan__products') }}
