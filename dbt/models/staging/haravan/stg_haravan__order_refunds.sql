{{ config(materialized='view') }}

with exploded as (
    select
        (payload->>'id')::bigint                                            as order_id,
        jsonb_array_elements(coalesce(payload->'refunds', '[]'::jsonb))     as refund_json,
        ingested_at,
        source_run_id
    from {{ source('raw_haravan', 'orders') }}
)

select
    (refund_json->>'id')::bigint                  as refund_id,
    order_id,
    (refund_json->>'created_at')::timestamptz     as created_at,
    refund_json->>'note'                          as note,
    refund_json->>'reason'                        as reason,
    nullif(refund_json->>'amount', '')::numeric(18,2) as refund_amount_vnd,
    (refund_json->>'restock')::boolean            as restock,
    refund_json->'refund_line_items'              as refund_line_items_json,
    refund_json->'transactions'                   as refund_transactions_json,
    ingested_at,
    source_run_id
from exploded
where refund_json->>'id' is not null
