-- Every reporting mart must add up to the same total as the fact table.
with totals as (
    select 'fct_sales' as model, sum(net_amount) as revenue from {{ ref('fct_sales') }}
    union all select 'mart_store_daily', sum(revenue) from {{ ref('mart_store_daily') }}
    union all select 'mart_category_monthly', sum(revenue) from {{ ref('mart_category_monthly') }}
    union all select 'mart_product_monthly', sum(revenue) from {{ ref('mart_product_monthly') }}
)
select * from totals
where abs(revenue - (select revenue from totals where model = 'fct_sales')) > 0.01
