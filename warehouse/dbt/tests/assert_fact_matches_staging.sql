-- The fact table must hold every de-duplicated line from staging, with the same total sales.
with stg as (
    select count(*) as n, sum(quantity * unit_price * (1 - discount_pct / 100)) as amount
    from {{ ref('stg_sales_lines') }}
),
fct as (
    select count(*) as n, sum(net_amount) as amount from {{ ref('fct_sales') }}
)
select stg.n as staging_rows, fct.n as fact_rows, stg.amount, fct.amount as fact_amount
from stg, fct
where stg.n <> fct.n or abs(stg.amount - fct.amount) > 0.01 * stg.n
