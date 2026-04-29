{{ config(
    materialized='incremental',
    unique_key='transaction_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

with base as (
    select
        transaction_id,
        order_id,
        refund_id,
        kind,
        status,
        gateway,
        amount_vnd,
        created_at,
        -- Same coalesce as dim_payment_methods so surrogate keys align.
        coalesce(nullif(trim(lower(gateway)), ''), 'unknown') as method_name
    from {{ ref('stg_haravan__order_transactions') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['transaction_id']) }}      as transaction_key,
    {{ dbt_utils.generate_surrogate_key(['order_id']) }}            as order_key,
    {{ dbt_utils.generate_surrogate_key(['method_name']) }}         as payment_method_key,
    to_char(created_at::date, 'YYYYMMDD')::int                      as transaction_date_key,
    transaction_id,
    order_id,
    refund_id,
    kind,
    status,
    gateway,
    amount_vnd,
    created_at
from base
{% if is_incremental() %}
where created_at >= (
    select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }}
)
{% endif %}
