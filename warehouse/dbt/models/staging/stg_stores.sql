select
    store_key,
    upper(trim(store_id)) as store_id,
    store_name,
    city,
    region,
    opened_on,
    size_sqm
from {{ ref('stores') }}
