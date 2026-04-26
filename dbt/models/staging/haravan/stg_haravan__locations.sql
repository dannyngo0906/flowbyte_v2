{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                          as location_id,
    payload->>'name'                                  as name,
    payload->>'address1'                              as address1,
    payload->>'address2'                              as address2,
    payload->>'city'                                  as city,
    payload->>'province'                              as province,
    payload->>'country'                               as country,
    payload->>'country_code'                          as country_code,
    payload->>'zip'                                   as zip,
    payload->>'phone'                                 as phone,
    (payload->>'active')::boolean                     as active,
    nullif(payload->>'created_at', '')::timestamptz   as created_at,
    nullif(payload->>'updated_at', '')::timestamptz   as location_updated_at,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'locations') }}
