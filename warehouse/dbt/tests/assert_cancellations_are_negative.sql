-- A cancelled invoice (number starting with C) takes items back, so its quantity is never positive.
select sales_line_id, invoice, quantity
from {{ ref('fct_online_sales') }}
where is_cancellation and quantity > 0
