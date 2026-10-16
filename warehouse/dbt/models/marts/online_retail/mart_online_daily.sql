-- What the Warehouse page reads for the real-data KPIs and trend: one row per day.
select
    d.date_day,
    d.year_month,
    count(distinct case when not f.is_cancellation then f.invoice end)       as orders,
    count(distinct case when not f.is_cancellation then f.customer_id end)   as customers,
    sum(case when not f.is_cancellation then f.quantity else 0 end)           as units,
    sum(case when not f.is_cancellation then f.line_amount else 0 end)        as sales,
    -sum(case when f.is_cancellation then f.line_amount else 0 end)           as returns,
    sum(f.line_amount)                                                        as net_sales
from {{ ref('fct_online_sales') }} f
join {{ ref('dim_date') }} d on d.date_key = f.date_key
group by all
