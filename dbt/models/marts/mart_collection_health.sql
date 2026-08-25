{{ config(materialized='table') }}

-- The health page, as a model. Eurostat recommends monitoring the collection itself as a
-- first-class output; almost nobody builds it, and it is the difference between catching a
-- dead source within one cycle and finding a week-long hole in the series.

with cells as (
    select
        collection_date,
        count(*)                                        as cells_in_basket,
        count(*) filter (where cell_available)          as cells_priced,
        sum(sold_out_quotes)                            as sold_out_quotes,
        sum(failed_quotes)                              as failed_quotes,
        sum(usable_quotes)                              as usable_quotes,
        sum(flagged_outliers)                           as flagged_outliers
    from {{ ref('mart_cell_daily') }}
    group by collection_date
)

select
    c.collection_date,
    c.cells_in_basket,
    c.cells_priced,
    round(c.cells_priced::numeric / nullif(c.cells_in_basket, 0), 4) as availability_rate,
    c.usable_quotes,
    c.sold_out_quotes,
    c.failed_quotes,
    c.flagged_outliers,
    r.run_id,
    r.started_at_utc,
    r.status,
    extract(epoch from (r.finished_at_utc - r.started_at_utc))::int   as duration_s,
    (select count(*) from {{ source('apix', 'robots_log') }} l
      where l.run_id = r.run_id and not l.allowed)                    as robots_disallowed_paths
from cells c
left join {{ source('apix', 'collection_runs') }} r
  on r.collection_date = c.collection_date
order by c.collection_date desc
