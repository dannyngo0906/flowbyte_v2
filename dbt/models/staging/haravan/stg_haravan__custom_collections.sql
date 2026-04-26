{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                                  as collection_id,
    payload->>'title'                                         as title,
    payload->>'handle'                                        as handle,
    payload->>'description'                                   as description,
    nullif(payload->>'published_at', '')::timestamptz         as published_at,
    nullif(payload->>'updated_at', '')::timestamptz           as collection_updated_at,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'custom_collections') }}
