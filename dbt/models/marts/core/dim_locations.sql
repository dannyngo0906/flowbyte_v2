{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['location_id']) }}     as location_key,
    location_id,
    name,
    address1,
    address2,
    city,
    province,
    country,
    country_code,
    zip,
    phone,
    active,
    created_at,
    location_updated_at
from {{ ref('stg_haravan__locations') }}
