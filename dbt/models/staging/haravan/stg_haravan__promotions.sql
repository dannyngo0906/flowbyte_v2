{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                                  as promotion_id,
    payload->>'title'                                         as title,
    payload->>'value_type'                                    as value_type,
    nullif(payload->>'value', '')::numeric(18,2)              as value,
    nullif(payload->>'starts_at', '')::timestamptz            as starts_at,
    nullif(payload->>'ends_at', '')::timestamptz              as ends_at,
    payload->>'status'                                        as status,
    nullif(payload->>'updated_at', '')::timestamptz           as promotion_updated_at,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'promotions') }}
