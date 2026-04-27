{{ config(materialized='view') }}

-- Haravan adjustments wrap N line_items per adjustment (verified live
-- 2026-04-27). Explode so each row is one (adjustment, line_item) — keeps
-- per-variant grain that fct_inventory_adjustments expects, and uses
-- line_item.id as the unique row key (globally unique in Haravan).
with exploded as (
    select
        payload                                                         as adj_payload,
        jsonb_array_elements(coalesce(payload->'line_items', '[]'::jsonb)) as li,
        ingested_at,
        source_run_id
    from {{ source('raw_haravan', 'inventory_adjustments') }}
)

select
    (li->>'id')::bigint                                       as adjustment_id,
    (adj_payload->>'id')::bigint                              as parent_adjustment_id,
    (li->>'product_variant_id')::bigint                       as variant_id,
    (li->>'product_id')::bigint                               as product_id,
    (adj_payload->>'location_id')::bigint                     as location_id,
    nullif(li->>'quantity', '')::int                          as quantity_delta,
    nullif(li->>'cost_amount', '')::numeric(18,2)             as cost_delta_vnd,
    li->>'sku'                                                as sku,
    li->>'barcode'                                            as barcode,
    nullif(adj_payload->>'created_at', '')::timestamptz       as created_at,
    nullif(adj_payload->>'updated_at', '')::timestamptz       as adjustment_updated_at,
    adj_payload->>'reason'                                    as reason,
    adj_payload->>'note'                                      as note,
    adj_payload->>'adjust_number'                             as adjust_number,
    ingested_at,
    source_run_id
from exploded
where li->>'id' is not null
