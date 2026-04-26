-- Placeholder. Populated in phase-09 (M5) when raw.haravan_inventory_adjustments lands.
-- Empty result set with the agreed schema so downstream BI / FK tests don't break.

{{ config(materialized='table') }}

select
    null::text          as adjustment_key,
    null::bigint        as adjustment_id,
    null::text          as variant_key,
    null::text          as location_key,
    null::int           as adjustment_date_key,
    null::int           as quantity_delta,
    null::numeric(18,2) as cost_delta_vnd,
    null::timestamptz   as adjusted_at
where false
