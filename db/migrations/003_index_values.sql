-- APIx 003 — published index values, with revision history.
--
-- Statistics agencies care about revisions more than almost anything, and no student
-- project implements them. The primary key includes computed_at_utc, so recomputing a
-- day (because a late scrape landed, or a bug was fixed) creates a new *vintage* sitting
-- beside the old one. Nothing is ever overwritten, so "what did you publish on the 3rd,
-- and what do you say now?" is a query rather than an apology.

create table if not exists apix.index_values (
    collection_date        date        not null,
    scenario               text        not null,
    series                 text        not null,   -- 'jevons_headline' | 'hedonic' | 'stratum'
    computed_at_utc        timestamptz not null,
    basket_version         int         not null,
    code_git_sha           text,

    index_value            numeric(12,6) not null,
    availability_rate      numeric(6,4),
    observed_weight_share  numeric(6,4),
    strata_contributing    int,
    strata_in_basket       int,
    ci_low                 numeric(12,6),
    ci_high                numeric(12,6),

    primary key (collection_date, scenario, series, computed_at_utc)
);

create index if not exists idx_index_values_lookup
    on apix.index_values (series, scenario, collection_date desc);

-- The current vintage: the most recent computation for each published point.
create or replace view apix.v_index_latest as
select distinct on (collection_date, scenario, series)
    collection_date, scenario, series, computed_at_utc, basket_version, code_git_sha,
    index_value, availability_rate, observed_weight_share,
    strata_contributing, strata_in_basket, ci_low, ci_high
from apix.index_values
order by collection_date, scenario, series, computed_at_utc desc;

-- Every point that has ever been revised, and by how much.
create or replace view apix.v_index_revisions as
with ranked as (
    select *,
           row_number() over (partition by collection_date, scenario, series
                              order by computed_at_utc) as vintage_no,
           count(*)     over (partition by collection_date, scenario, series) as vintages
    from apix.index_values
)
select
    a.collection_date, a.scenario, a.series,
    a.vintage_no as from_vintage, a.computed_at_utc as first_computed_at, a.index_value as first_value,
    b.vintage_no as to_vintage,   b.computed_at_utc as later_computed_at, b.index_value as later_value,
    round(b.index_value - a.index_value, 6) as revision_points
from ranked a
join ranked b
  on  a.collection_date = b.collection_date
  and a.scenario = b.scenario
  and a.series = b.series
  and b.vintage_no = a.vintage_no + 1
where a.index_value is distinct from b.index_value;

comment on table apix.index_values is
    'Append-only vintages. A recomputation is a new row, never an overwrite. See CLAUDE.md index rule 5.';
