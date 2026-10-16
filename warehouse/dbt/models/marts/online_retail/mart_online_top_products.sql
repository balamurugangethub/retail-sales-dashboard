-- The 50 best-selling products of the online shop over the whole history, by net sales.
select
    stock_code,
    max(description)                                                  as description,
    sum(case when not is_cancellation then quantity else 0 end)       as units,
    count(distinct case when not is_cancellation then invoice end)    as orders,
    sum(line_amount)                                                  as net_sales
from {{ ref('fct_online_sales') }}
where is_product
group by stock_code
order by net_sales desc
limit 50
