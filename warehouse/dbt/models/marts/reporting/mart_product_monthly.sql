select
    d.year_month,
    s.store_name,
    p.sku,
    p.product_name,
    p.category,
    sum(f.quantity)      as units,
    sum(f.net_amount)    as revenue,
    sum(f.profit_amount) as profit
from {{ ref('fct_sales') }} f
join {{ ref('dim_date') }} d on d.date_key = f.date_key
join {{ ref('dim_store') }} s on s.store_key = f.store_key
join {{ ref('dim_product') }} p on p.product_key = f.product_key
group by all
