select
    product_key,
    sku,
    product_name,
    category,
    list_price,
    unit_cost
from {{ ref('stg_products') }}
