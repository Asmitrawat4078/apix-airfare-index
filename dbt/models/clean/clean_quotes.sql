{{ config(materialized='table') }}

-- Outliers are FLAGGED, never deleted.
--
-- The temptation is to drop the ₹87,000 DEL-BOM fare because it "looks wrong". Sometimes
-- it is wrong. Sometimes it is a genuine T+1 fare on a sold-out Friday evening, which is
-- precisely the phenomenon this index exists to measure. Deleting it would systematically
-- shave the peaks off a series whose peaks are the point.
--
-- So every quote survives into this layer with a flag and a reason. The index module
-- decides what to do with flagged rows, in code that is unit-tested and documented,
-- rather than having the decision buried in SQL nobody reads.

with base as (
    select * from {{ ref('stg_quotes') }}
),

route_stats as (
    -- Robust dispersion per (route, lead time): median and MAD rather than mean and sd,
    -- because a mean-based outlier rule on a distribution with real fat tails flags the
    -- interesting observations and keeps the boring ones.
    select
        route,
        lead_time_days,
        percentile_cont(0.5) within group (order by total_fare) as median_fare,
        percentile_cont(0.5) within group (
            order by abs(total_fare - (
                select percentile_cont(0.5) within group (order by t2.total_fare)
                from base t2
                where t2.route = b.route and t2.lead_time_days = b.lead_time_days
                  and t2.is_available
            ))
        ) as mad_fare,
        count(*) as n_observations
    from base b
    where is_available and total_fare is not null
    group by route, lead_time_days
)

select
    b.*,
    r.median_fare,
    r.mad_fare,
    r.n_observations,

    case
        when not b.is_available then null
        when r.mad_fare is null or r.mad_fare = 0 then 0
        -- 0.6745 scales MAD to a standard-deviation equivalent for a normal distribution.
        else round((0.6745 * (b.total_fare - r.median_fare) / r.mad_fare)::numeric, 3)
    end as robust_z,

    case
        when not b.is_available then 'unavailable'
        when b.total_fare is null then 'no_price'
        when b.total_fare < 800 then 'implausibly_low'
        when b.total_fare > 150000 then 'implausibly_high'
        when b.observed_lead_days <> b.lead_time_days then 'lead_time_mismatch'
        when r.mad_fare is not null and r.mad_fare > 0
             and abs(0.6745 * (b.total_fare - r.median_fare) / r.mad_fare) > 5 then 'extreme_outlier'
        else 'ok'
    end as quality_flag

from base b
left join route_stats r
  on b.route = r.route and b.lead_time_days = r.lead_time_days
