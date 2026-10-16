select
    store_key,
    store_id,
    store_name,
    city,
    region,
    opened_on,
    size_sqm
from {{ ref('stg_stores') }}
