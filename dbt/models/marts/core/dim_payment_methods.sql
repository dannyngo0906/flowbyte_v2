{{ config(materialized='table') }}

with sources as (
    select distinct
        -- Empty + NULL gateways collapse to 'unknown' so method_name is
        -- never NULL (referenced as FK from fct_transactions).
        coalesce(nullif(trim(lower(gateway)), ''), 'unknown')          as method_name
    from {{ ref('stg_haravan__order_transactions') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['method_name']) }}             as payment_method_key,
    method_name
from sources
