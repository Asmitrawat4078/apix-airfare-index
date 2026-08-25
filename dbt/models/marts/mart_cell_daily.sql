{{ config(materialized='table') }}

-- One row per basket cell per collection day: the input the index module consumes.
-- The cheapest *usable* fare per cell, plus everything a reader needs to judge it.

select
    collection_date,
    origin,
    destination,
    route,
    lead_time_days,
    dep_date,

    count(*) filter (where is_available and quality_flag = 'ok')          as usable_quotes,
    count(distinct source) filter (where is_available)                    as sources_reporting,
    count(distinct carrier) filter (where is_available)                   as carriers_reporting,

    min(total_fare) filter (where is_available and quality_flag = 'ok')   as cheapest_fare,
    percentile_cont(0.5) within group (order by total_fare)
        filter (where is_available and quality_flag = 'ok')               as median_fare,
    max(total_fare) filter (where is_available and quality_flag = 'ok')   as dearest_fare,

    bool_or(is_available)                                                 as cell_available,

    -- Sold out and blocked are opposite facts and are counted separately, forever.
    count(*) filter (where unavailable_reason in ('sold_out', 'no_service')) as sold_out_quotes,
    count(*) filter (where unavailable_reason in
        ('blocked', 'robots_disallowed', 'rate_limited', 'timeout', 'parse_error')) as failed_quotes,

    count(*) filter (where quality_flag = 'extreme_outlier')              as flagged_outliers

from {{ ref('clean_quotes') }}
group by collection_date, origin, destination, route, lead_time_days, dep_date
