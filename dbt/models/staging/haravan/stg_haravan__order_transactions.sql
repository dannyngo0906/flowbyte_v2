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
),

-- Haravan exposes refund transactions in BOTH `order.transactions[]` (no
-- refund_id) AND `order.refunds[].transactions[]` (with refund_id + richer
-- metadata). Verified live 2026-04-27: 78 transaction_ids appeared in both
-- arrays. Prefer the refund-level row (refund_id IS NOT NULL) since it
-- carries the refund linkage; fall back to the order-level row otherwise.
ranked as (
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
        source_run_id,
        row_number() over (
            partition by (tx_json->>'id')::bigint
            order by case when refund_id is not null then 0 else 1 end
        ) as rn
    from unioned
    where tx_json->>'id' is not null
)

select
    transaction_id,
    order_id,
    refund_id,
    kind,
    status,
    gateway,
    amount_vnd,
    created_at,
    ingested_at,
    source_run_id
from ranked
where rn = 1
