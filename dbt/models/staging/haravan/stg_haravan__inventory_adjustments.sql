{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                                  as adjustment_id,
    (payload->>'variant_id')::bigint                          as variant_id,
    (payload->>'location_id')::bigint                         as location_id,
    nullif(payload->>'quantity_delta', '')::int               as quantity_delta,
    nullif(payload->>'cost_delta', '')::numeric(18,2)         as cost_delta_vnd,
    nullif(payload->>'created_at', '')::timestamptz           as created_at,
    nullif(payload->>'updated_at', '')::timestamptz           as adjustment_updated_at,
    payload->>'reason'                                        as reason,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'inventory_adjustments') }}
