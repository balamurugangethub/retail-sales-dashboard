select
    product_key,
    upper(trim(sku)) as sku,
    product_name,
    category,
    list_price,
    unit_cost
from {{ ref('products') }}
