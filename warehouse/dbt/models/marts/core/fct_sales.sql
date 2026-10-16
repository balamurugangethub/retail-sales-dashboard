-- Grain: one row per order line. Built incrementally: each run only adds the lines loaded
-- since the last run, and a file that was loaded again replaces its earlier lines.
{{ config(materialized='incremental', unique_key='sales_line_id', incremental_strategy='delete+insert') }}

with lines as (
    select * from {{ ref('stg_sales_lines') }}
    {% if is_incremental() %}
    where _loaded_at > (select coalesce(max(_loaded_at), timestamp '1900-01-01') from {{ this }})
    {% endif %}
)

select
    l.sales_line_id,
    l.order_id,
    cast(strftime(l.sale_date, '%Y%m%d') as integer)                        as date_key,
    s.store_key,
    p.product_key,
    l.sold_at,
    l.payment_method,
    l.quantity,
    l.unit_price,
    l.discount_pct,
    cast(l.quantity * l.unit_price as decimal(12, 2))                        as gross_amount,
    cast(l.quantity * l.unit_price * l.discount_pct / 100 as decimal(12, 2)) as discount_amount,
    cast(l.quantity * l.unit_price * (1 - l.discount_pct / 100) as decimal(12, 2)) as net_amount,
    cast(l.quantity * p.unit_cost as decimal(12, 2))                         as cost_amount,
    cast(l.quantity * l.unit_price * (1 - l.discount_pct / 100) - l.quantity * p.unit_cost as decimal(12, 2))
                                                                             as profit_amount,
    l._loaded_at
from lines l
left join {{ ref('dim_store') }} s on s.store_id = l.store_id
left join {{ ref('dim_product') }} p on p.sku = l.sku
