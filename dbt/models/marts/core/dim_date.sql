{{ config(materialized='table') }}

-- date_spine 2020-01-01 → +4 yrs (anchored on current_date so the dim
-- self-extends as time passes). VN public holidays joined from the
-- vn_holidays seed (regen via scripts/generate-vn-holidays-seed.py).

with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2020-01-01' as date)",
        end_date="cast(current_date + interval '4 years' as date)"
    ) }}
),

enriched as (
    select
        to_char(date_day, 'YYYYMMDD')::int                                              as date_key,
        date_day::date                                                                  as date_actual,
        extract(year from date_day)::int                                                as year,
        extract(quarter from date_day)::int                                             as quarter,
        extract(month from date_day)::int                                               as month_of_year,
        extract(day from date_day)::int                                                 as day_of_month,
        extract(dow from date_day)::int                                                 as day_of_week,
        extract(week from date_day)::int                                                as week_of_year,
        case when extract(dow from date_day) in (0, 6) then true else false end         as is_weekend
    from spine
)

select
    e.*,
    coalesce(h.is_public_holiday, false)                                                as is_holiday,
    h.holiday_name,
    case
        when e.is_weekend or coalesce(h.is_public_holiday, false) then false
        else true
    end                                                                                 as is_business_day
from enriched e
left join {{ ref('vn_holidays') }} h on h.date = e.date_actual
