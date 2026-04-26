{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['customer_id']) }}     as customer_key,
    customer_id,
    email,
    phone,
    first_name,
    last_name,
    state                                                       as customer_state,
    accepts_marketing,
    orders_count,
    total_spent_vnd,
    tags,
    created_at,
    customer_updated_at
from {{ ref('stg_haravan__customers') }}
