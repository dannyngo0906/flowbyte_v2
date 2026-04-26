{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                          as product_id,
    payload->>'title'                                 as title,
    payload->>'handle'                                as handle,
    payload->>'vendor'                                as vendor,
    payload->>'product_type'                          as product_type,
    payload->>'status'                                as status,
    payload->>'tags'                                  as tags,
    (payload->>'created_at')::timestamptz             as created_at,
    (payload->>'updated_at')::timestamptz             as product_updated_at,
    nullif(payload->>'published_at', '')::timestamptz as published_at,
    payload->'variants'                               as variants_json,
    payload->'images'                                 as images_json,
    payload->'options'                                as options_json,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'products') }}
