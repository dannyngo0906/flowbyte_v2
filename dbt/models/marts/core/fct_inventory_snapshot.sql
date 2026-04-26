{{ config(
    materialized='incremental',
    unique_key=['location_id', 'variant_id', 'snapshot_date'],
    incremental_strategy='merge'
) }}

-- Daily snapshot: one row per (location, variant, snapshot_date). Re-running
-- the same day overwrites that day's rows (merge on composite key); next day
-- adds a fresh slice. The 90-day lookback in the incremental predicate keeps
-- recent days mergeable while skipping ancient history on each run.
select
    {{ dbt_utils.generate_surrogate_key(['location_id', 'variant_id', 'snapshot_date']) }} as snapshot_key,
    {{ dbt_utils.generate_surrogate_key(['variant_id']) }}        as variant_key,
    {{ dbt_utils.generate_surrogate_key(['location_id']) }}       as location_key,
    to_char(snapshot_date, 'YYYYMMDD')::int                       as snapshot_date_key,
    location_id,
    variant_id,
    snapshot_date,
    current_quantity                                              as on_hand_quantity,
    available_quantity,
    committed_quantity
from {{ ref('stg_haravan__inventory_locations') }}
{% if is_incremental() %}
  -- 90-day window keeps merges fast while still letting late-arriving rows
  -- update prior snapshots (composite unique_key dedupes inside the window).
  where snapshot_date >= current_date - interval '90 days'
{% endif %}
