{{ config(materialized='table') }}

with sources as (
    select distinct
        nullif(trim(lower(coalesce(gateway, 'unknown'))), '')          as method_name
    from {{ ref('stg_haravan__order_transactions') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['method_name']) }}             as payment_method_key,
    method_name
from sources
