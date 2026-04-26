{{ config(materialized='view') }}

-- One row per (location, variant, snapshot_date) — produced daily by the
-- cartesian inventory_locations extractor. Feeds fct_inventory_snapshot.
select
    location_id,
    variant_id,
    snapshot_date,
    nullif(payload->>'available', '')::int                    as available_quantity,
    nullif(payload->>'on_hand', '')::int                      as current_quantity,
    nullif(payload->>'committed', '')::int                    as committed_quantity,
    ingested_at,
    source_run_id
from {{ source('raw_haravan', 'inventory_locations') }}
