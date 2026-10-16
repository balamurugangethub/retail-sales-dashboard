-- Grain: one invoice line of the online shop (real data). Joins to dim_date and dim_customer.
select
    l.sales_line_id,
    l.invoice,
    cast(strftime(l.invoice_date, '%Y%m%d') as integer)  as date_key,
    l.customer_id,
    l.country,
    l.stock_code,
    l.description,
    l.invoiced_at,
    l.quantity,
    l.unit_price,
    cast(l.quantity * l.unit_price as decimal(14, 2))    as line_amount,
    l.is_cancellation,
    l.is_product,
    l._loaded_at
from {{ ref('stg_online_retail_lines') }} l
