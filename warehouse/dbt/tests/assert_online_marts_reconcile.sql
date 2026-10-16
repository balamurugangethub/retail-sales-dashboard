-- The online reporting marts must add up to the online fact table, which must keep every staged line.
with totals as (
    select 'stg_online_retail_lines' as model, sum(cast(quantity * unit_price as decimal(14, 2))) as net_sales, count(*) as line_count
    from {{ ref('stg_online_retail_lines') }}
    union all select 'fct_online_sales', sum(line_amount), count(*) from {{ ref('fct_online_sales') }}
    union all select 'mart_online_daily', sum(net_sales), null from {{ ref('mart_online_daily') }}
    union all select 'mart_online_country_monthly', sum(net_sales), null from {{ ref('mart_online_country_monthly') }}
)
select * from totals
where abs(coalesce(net_sales, 0) - coalesce((select net_sales from totals where model = 'fct_online_sales'), 0)) > 1
   or (line_count is not null and line_count <> (select line_count from totals where model = 'fct_online_sales'))
