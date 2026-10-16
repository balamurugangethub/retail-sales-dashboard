-- UCI Online Retail II invoice lines with proper types. Cancelled invoices start with "C" and carry
-- negative quantities. The export repeats some lines exactly (same invoice, item, quantity, time,
-- price and customer); one copy of each is kept, as is usual for this dataset.
with source as (
    select * from {{ source('raw', 'online_retail_lines') }}
),

typed as (
    select
        upper(trim(invoice))                                         as invoice,
        upper(trim(stock_code))                                      as stock_code,
        nullif(trim(description), '')                                as description,
        cast(cast(quantity as double) as integer)                    as quantity,
        cast(invoice_date as timestamp)                              as invoiced_at,
        cast(unit_price as decimal(12, 3))                           as unit_price,
        nullif(regexp_replace(trim(customer_id), '\.0+$', ''), '')   as customer_id,
        trim(country)                                                as country,
        _source_file,
        _loaded_at
    from source
),

deduped as (
    select
        *,
        row_number() over (
            partition by invoice, stock_code, quantity, invoiced_at, unit_price, coalesce(customer_id, '')
            order by _loaded_at desc, description
        ) as copy_number
    from typed
)

select
    md5(concat_ws('|', invoice, stock_code, quantity, invoiced_at, unit_price, coalesce(customer_id, '')))
                                                     as sales_line_id,
    invoice,
    stock_code,
    description,
    quantity,
    invoiced_at,
    cast(invoiced_at as date)                        as invoice_date,
    unit_price,
    customer_id,
    country,
    invoice like 'C%'                                as is_cancellation,
    regexp_matches(stock_code, '^[0-9]{5}')          as is_product,
    _source_file,
    _loaded_at
from deduped
where copy_number = 1
