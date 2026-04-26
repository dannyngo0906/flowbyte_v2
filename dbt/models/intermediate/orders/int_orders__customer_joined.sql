{{ config(materialized='ephemeral') }}

select
    o.*,
    c.email          as customer_email,
    c.first_name     as customer_first_name,
    c.last_name      as customer_last_name
from {{ ref('stg_haravan__orders') }} o
left join {{ ref('stg_haravan__customers') }} c
    on c.customer_id = o.customer_id
