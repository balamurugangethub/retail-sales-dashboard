-- One row per calendar day. date_key is YYYYMMDD so it sorts and reads naturally.
with days as (
    select cast(range as date) as date_day
    from range(
        cast('{{ var("date_spine_start") }}' as date),
        cast('{{ var("date_spine_end") }}' as date) + interval 1 day,
        interval 1 day
    )
)

select
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,
    date_day,
    year(date_day)                                 as year,
    quarter(date_day)                              as quarter,
    month(date_day)                                as month,
    strftime(date_day, '%Y-%m')                    as year_month,
    strftime(date_day, '%B')                       as month_name,
    cast(date_trunc('month', date_day) as date)    as month_start,
    weekofyear(date_day)                           as iso_week,
    isodow(date_day)                               as day_of_week,
    strftime(date_day, '%A')                       as day_name,
    isodow(date_day) in (6, 7)                     as is_weekend
from days
