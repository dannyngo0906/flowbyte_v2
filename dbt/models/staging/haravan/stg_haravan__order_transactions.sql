{{ config(materialized='view') }}

with order_tx as (
    select
        (payload->>'id')::bigint                                                 as order_id,
        null::bigint                                                             as refund_id,
        jsonb_array_elements(coalesce(payload->'transactions', '[]'::jsonb))     as tx_json,
        ingested_at,
        source_run_id
    from {{ source('raw_haravan', 'orders') }}
),

refund_envelopes as (
    select
        payload,
        jsonb_array_elements(coalesce(payload->'refunds', '[]'::jsonb)) as refund_json,
        ingested_at,
        source_run_id
    from {{ source('raw_haravan', 'orders') }}
),

refund_tx as (
    select
        (payload->>'id')::bigint                                                       as order_id,
        (refund_json->>'id')::bigint                                                   as refund_id,
        jsonb_array_elements(coalesce(refund_json->'transactions', '[]'::jsonb))       as tx_json,
        ingested_at,
        source_run_id
    from refund_envelopes
),

unioned as (
    select * from order_tx
    union all
    select * from refund_tx
)

select
    (tx_json->>'id')::bigint                  as transaction_id,
    order_id,
    refund_id,
    tx_json->>'kind'                          as kind,           -- sale | refund | authorization | capture | void | change
    tx_json->>'status'                        as status,
    tx_json->>'gateway'                       as gateway,
    nullif(tx_json->>'amount', '')::numeric(18,2) as amount_vnd,
    (tx_json->>'created_at')::timestamptz     as created_at,
    ingested_at,
    source_run_id
from unioned
where tx_json->>'id' is not null
