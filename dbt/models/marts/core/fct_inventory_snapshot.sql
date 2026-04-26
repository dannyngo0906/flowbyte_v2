-- Placeholder. Populated in phase-09 (M5) when daily inventory snapshots land.
-- Composite "PK" planned: (location_key, variant_key, snapshot_date_key).

{{ config(materialized='table') }}

select
    null::text          as variant_key,
    null::text          as location_key,
    null::int           as snapshot_date_key,
    null::int           as on_hand_quantity,
    null::int           as available_quantity,
    null::int           as committed_quantity
where false
