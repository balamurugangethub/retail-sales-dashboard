-- A cancelled invoice (number starting with C) takes items back, so a product line on it is never
-- positive. Manual adjustments (stock codes like M or D) on cancelled invoices can go either way.
-- The real export is not perfectly clean, so a handful of odd lines is a warning and more is an error.
{{ config(severity='error', warn_if='>0', error_if='>10') }}

select sales_line_id, invoice, stock_code, quantity
from {{ ref('fct_online_sales') }}
where is_cancellation and is_product and quantity > 0
