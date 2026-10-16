-- What the dashboard reads for KPIs, the trend and the store ranking: one row per store per day.
select
    d.date_day,
    d.year_month,
    s.store_name,
    s.region,
    count(distinct f.order_id)  as orders,
    count(*)                    as order_lines,
    sum(f.quantity)             as units,
    sum(f.net_amount)           as revenue,
    sum(f.cost_amount)          as cost,
    sum(f.profit_amount)        as profit,
    sum(f.discount_amount)      as discounts
from {{ ref('fct_sales') }} f
join {{ ref('dim_date') }} d on d.date_key = f.date_key
join {{ ref('dim_store') }} s on s.store_key = f.store_key
group by all
