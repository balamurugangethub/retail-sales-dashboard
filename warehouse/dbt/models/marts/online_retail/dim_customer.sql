-- One row per online customer, with where they buy from and how much. Guest checkouts have no
-- customer id and are not in this table.
select
    customer_id,
    arg_max(country, invoiced_at)                                         as country,
    min(invoice_date)                                                     as first_order_date,
    max(invoice_date)                                                     as last_order_date,
    count(distinct case when not is_cancellation then invoice end)        as orders,
    sum(case when not is_cancellation then quantity * unit_price else 0 end) as gross_sales,
    sum(quantity * unit_price)                                            as net_sales
from {{ ref('stg_online_retail_lines') }}
where customer_id is not null
group by customer_id
