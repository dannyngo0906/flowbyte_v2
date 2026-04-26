{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                                  as discount_id,
    payload->>'code'                                          as code,
    payload->>'value_type'                                    as value_type,
    nullif(payload->>'value', '')::numeric(18,2)              as value,
    payload->>'applies_to'                                    as applies_to,
    nullif(payload->>'starts_at', '')::timestamptz            as starts_at,
    nullif(payload->>'ends_at', '')::timestamptz              as ends_at,
    nullif(payload->>'usage_count', '')::int                  as usage_count,
    payload->>'status'                                        as status,
    nullif(payload->>'updated_at', '')::timestamptz           as discount_updated_at,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'discounts') }}
