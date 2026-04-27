{{ config(materialized='view') }}

select
    (payload->>'id')::bigint                          as order_id,
    payload->>'name'                                  as order_name,
    payload->>'order_number'                          as order_number,
    -- Haravan nests customer info under `payload.customer.id`, not a top-level
    -- `customer_id` field (verified live 2026-04-27 against boshop-8). Fall back
    -- to top-level for safety in case API ever flattens it.
    coalesce(
        nullif(payload->'customer'->>'id', '')::bigint,
        nullif(payload->>'customer_id', '')::bigint
    ) as customer_id,
    nullif(payload->>'location_id', '')::bigint       as location_id,
    payload->>'currency'                              as currency,
    payload->>'financial_status'                      as financial_status,
    payload->>'fulfillment_status'                    as fulfillment_status,
    nullif(payload->>'subtotal_price',  '')::numeric(18,2) as subtotal_vnd,
    nullif(payload->>'total_discounts', '')::numeric(18,2) as total_discount_vnd,
    nullif(payload->>'total_tax',       '')::numeric(18,2) as total_tax_vnd,
    nullif(payload->>'total_shipping',  '')::numeric(18,2) as total_shipping_vnd,
    nullif(payload->>'total_price',     '')::numeric(18,2) as total_price_vnd,
    nullif(payload->>'total_refunded',  '')::numeric(18,2) as total_refunded_vnd,
    (payload->>'created_at')::timestamptz             as created_at,
    (payload->>'updated_at')::timestamptz             as order_updated_at,
    nullif(payload->>'closed_at',     '')::timestamptz as closed_at,
    nullif(payload->>'cancelled_at',  '')::timestamptz as cancelled_at,
    payload->'line_items'                             as line_items_json,
    payload->'refunds'                                as refunds_json,
    payload->'transactions'                           as transactions_json,
    payload->'shipping_address'                       as shipping_address_json,
    payload->'billing_address'                        as billing_address_json,
    payload                                           as payload_raw,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'orders') }}
