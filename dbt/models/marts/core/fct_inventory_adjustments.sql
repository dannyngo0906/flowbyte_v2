{{ config(
    materialized='incremental',
    unique_key='adjustment_id',
    incremental_strategy='merge'
) }}

-- One row per inventory adjustment event. Foreign keys resolve through
-- dim_variants and dim_locations (surrogate keys generated identically here).
select
    {{ dbt_utils.generate_surrogate_key(['adjustment_id']) }}     as adjustment_key,
    {{ dbt_utils.generate_surrogate_key(['variant_id']) }}        as variant_key,
    {{ dbt_utils.generate_surrogate_key(['location_id']) }}       as location_key,
    to_char(created_at::date, 'YYYYMMDD')::int                    as adjustment_date_key,
    adjustment_id,
    variant_id,
    location_id,
    quantity_delta,
    cost_delta_vnd,
    reason,
    created_at                                                    as adjusted_at
from {{ ref('stg_haravan__inventory_adjustments') }}
{% if is_incremental() %}
  where created_at >= (
      select coalesce(max(adjusted_at), '1900-01-01'::timestamptz)
      from {{ this }}
  )
{% endif %}
