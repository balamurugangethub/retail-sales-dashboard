-- Sales, returns and customers per country per month.
select
    d.year_month,
    f.country,
    count(distinct case when not f.is_cancellation then f.invoice end)       as orders,
    count(distinct case when not f.is_cancellation then f.customer_id end)   as customers,
    sum(case when not f.is_cancellation then f.line_amount else 0 end)        as sales,
    -sum(case when f.is_cancellation then f.line_amount else 0 end)           as returns,
    sum(f.line_amount)                                                        as net_sales
from {{ ref('fct_online_sales') }} f
join {{ ref('dim_date') }} d on d.date_key = f.date_key
group by all
