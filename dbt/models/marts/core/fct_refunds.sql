{{ config(
    materialized='incremental',
    unique_key='refund_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

select
    {{ dbt_utils.generate_surrogate_key(['refund_id']) }}      as refund_key,
    {{ dbt_utils.generate_surrogate_key(['order_id']) }}       as order_key,
    to_char(created_at::date, 'YYYYMMDD')::int                 as refund_date_key,
    refund_id,
    order_id,
    reason,
    note,
    refund_amount_vnd,
    restock,
    created_at
from {{ ref('stg_haravan__order_refunds') }}
{% if is_incremental() %}
where created_at >= (
    select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }}
)
{% endif %}
