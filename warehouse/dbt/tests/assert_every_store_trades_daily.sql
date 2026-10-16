-- A day with no sales for a store usually means its till file was cut short.
with loaded_days as (
    select distinct date_key from {{ ref('fct_sales') }}
),
expected as (
    select d.date_key, s.store_key from loaded_days d cross join {{ ref('dim_store') }} s
)
select e.date_key, e.store_key
from expected e
left join (select distinct date_key, store_key from {{ ref('fct_sales') }}) f
    on f.date_key = e.date_key and f.store_key = e.store_key
where f.store_key is null
