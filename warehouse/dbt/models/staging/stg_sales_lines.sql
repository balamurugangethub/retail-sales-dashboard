-- Typed, trimmed and de-duplicated order lines. Tills sometimes send a line twice; the
-- latest copy of each (order_id, line_number) wins.
with source as (
    select * from {{ source('raw', 'sales_lines') }}
),

typed as (
    select
        trim(order_id)                                                      as order_id,
        cast(line_number as integer)                                        as line_number,
        cast(sold_at as timestamp)                                          as sold_at,
        upper(trim(store_id))                                               as store_id,
        upper(trim(sku))                                                    as sku,
        cast(quantity as integer)                                           as quantity,
        cast(unit_price as decimal(10, 2))                                  as unit_price,
        cast(coalesce(nullif(trim(discount_pct), ''), '0') as decimal(5, 2)) as discount_pct,
        coalesce(nullif(lower(trim(payment_method)), ''), 'unknown')        as payment_method,
        _source_file,
        _file_date,
        _loaded_at
    from source
),

deduped as (
    select
        *,
        row_number() over (partition by order_id, line_number order by _loaded_at desc) as copy_number
    from typed
)

select
    order_id || '-' || cast(line_number as varchar) as sales_line_id,
    order_id,
    line_number,
    sold_at,
    cast(sold_at as date) as sale_date,
    store_id,
    sku,
    quantity,
    unit_price,
    discount_pct,
    payment_method,
    _source_file,
    _loaded_at
from deduped
where copy_number = 1
