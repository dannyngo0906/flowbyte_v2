{{ config(materialized='view') }}

-- Append-only audit log. `created_at` is the single ordering signal.
select
    (payload->>'id')::bigint                                  as event_id,
    nullif(payload->>'subject_id', '')::bigint                as subject_id,
    payload->>'subject_type'                                  as subject_type,
    payload->>'verb'                                          as verb,
    payload->>'arguments'                                     as arguments,
    payload->>'description'                                   as description,
    nullif(payload->>'created_at', '')::timestamptz           as event_created_at,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'events') }}
