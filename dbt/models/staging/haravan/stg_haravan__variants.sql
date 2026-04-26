{{ config(materialized='view') }}

with exploded as (
    select
        (payload->>'id')::bigint                                          as product_id,
        jsonb_array_elements(coalesce(payload->'variants', '[]'::jsonb))  as v,
        ingested_at,
        source_run_id
    from {{ source('raw_haravan', 'products') }}
)

select
    (v->>'id')::bigint                            as variant_id,
    product_id,
    v->>'title'                                   as title,
    v->>'sku'                                     as sku,
    v->>'barcode'                                 as barcode,
    nullif(v->>'price',            '')::numeric(18,2) as price_vnd,
    nullif(v->>'compare_at_price', '')::numeric(18,2) as compare_at_price_vnd,
    nullif(v->>'inventory_quantity', '')::int     as inventory_quantity,
    v->>'option1'                                 as option1,
    v->>'option2'                                 as option2,
    v->>'option3'                                 as option3,
    v->>'inventory_management'                    as inventory_management,
    v->>'inventory_policy'                        as inventory_policy,
    nullif(v->>'weight', '')::numeric(18,3)       as weight,
    v->>'weight_unit'                             as weight_unit,
    nullif(v->>'created_at', '')::timestamptz     as created_at,
    nullif(v->>'updated_at', '')::timestamptz     as variant_updated_at,
    ingested_at,
    source_run_id
from exploded
where v->>'id' is not null
