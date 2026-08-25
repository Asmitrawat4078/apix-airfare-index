{{ config(materialized='view') }}

-- Typed and deduplicated, and nothing else. Staging must not make judgements: any row
-- dropped here is a row that can never be examined again by someone asking why the index
-- moved. Cleaning happens one layer up, where it is visible and reversible.

with deduped as (
    select
        *,
        -- A run that is retried writes the same cell twice. Keep the first observation:
        -- it is the one closest to the scheduled collection instant, which is the thing
        -- the sampling design promises to hold constant.
        row_number() over (
            partition by collection_date, source, origin, destination,
                         lead_time_days, coalesce(carrier, ''), coalesce(flight_no, '')
            order by collection_ts_utc asc, quote_id asc
        ) as rn
    from {{ source('apix', 'raw_quotes') }}
)

select
    quote_id,
    run_id,
    basket_version,
    collection_ts_utc,
    collection_ts_utc at time zone 'Asia/Kolkata'          as collection_ts_ist,
    collection_date,
    source,
    url,
    origin,
    destination,
    origin || '-' || destination                            as route,
    lead_time_days,
    dep_date,
    (dep_date - collection_date)                            as observed_lead_days,
    carrier,
    flight_no,
    dep_ts,
    dep_ts at time zone 'Asia/Kolkata'                      as dep_ts_ist,
    extract(dow  from dep_ts at time zone 'Asia/Kolkata')::int as dep_dow,
    extract(hour from dep_ts at time zone 'Asia/Kolkata')::int as dep_hour,
    stops,
    fare_class,
    base_fare,
    taxes,
    fees,
    total_fare,
    currency,
    is_available,
    is_cheapest_in_cell,
    unavailable_reason
from deduped
where rn = 1
