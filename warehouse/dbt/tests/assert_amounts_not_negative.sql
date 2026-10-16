-- Returns the lines that break the rule; the test passes when there are none.
select sales_line_id, quantity, net_amount
from {{ ref('fct_sales') }}
where quantity <= 0 or net_amount < 0 or cost_amount < 0
