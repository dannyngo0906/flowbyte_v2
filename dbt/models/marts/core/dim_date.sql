{{ config(materialized='table') }}

with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2020-01-01' as date)",
        end_date="cast(current_date + interval '4 years' as date)"
    ) }}
)

select
    to_char(date_day, 'YYYYMMDD')::int                                              as date_key,
    date_day::date                                                                  as date_actual,
    extract(year from date_day)::int                                                as year,
    extract(quarter from date_day)::int                                             as quarter,
    extract(month from date_day)::int                                               as month_of_year,
    extract(day from date_day)::int                                                 as day_of_month,
    extract(dow from date_day)::int                                                 as day_of_week,
    extract(week from date_day)::int                                                as week_of_year,
    case when extract(dow from date_day) in (0, 6) then true else false end         as is_weekend,
    -- VN holiday columns are filled in phase-11 (M7) via `vn_holidays.csv` seed.
    null::text                                                                      as holiday_name,
    false                                                                           as is_holiday
from spine
