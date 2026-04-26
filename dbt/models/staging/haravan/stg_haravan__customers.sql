{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                          as customer_id,
    payload->>'email'                                 as email,
    payload->>'phone'                                 as phone,
    payload->>'first_name'                            as first_name,
    payload->>'last_name'                             as last_name,
    payload->>'state'                                 as state,
    (payload->>'accepts_marketing')::boolean          as accepts_marketing,
    (payload->>'orders_count')::int                   as orders_count,
    nullif(payload->>'total_spent', '')::numeric(18,2) as total_spent_vnd,
    payload->>'tags'                                  as tags,
    (payload->>'created_at')::timestamptz             as created_at,
    (payload->>'updated_at')::timestamptz             as customer_updated_at,
    payload->'addresses'                              as addresses_json,
    payload->'default_address'                        as default_address_json,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'customers') }}
