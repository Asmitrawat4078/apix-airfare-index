-- APIx 002 — collection health and the robots.txt audit trail.
--
-- Eurostat's web-scraping guidelines recommend monitoring the collection itself as a
-- first-class output. Almost no one builds it, and it is the difference between "the
-- index looks odd this week" and "easemytrip started returning 403 on Tuesday at 02:14".

create table if not exists apix.collection_runs (
    run_id              uuid primary key,
    basket_version      int         not null,
    started_at_utc      timestamptz not null,
    finished_at_utc     timestamptz,
    collection_date     date        not null,
    scheduled_ist       text,
    git_sha             text,
    runner              text,
    cells_expected      int         not null,
    cells_attempted     int         not null default 0,
    cells_available     int         not null default 0,
    quotes_written      int         not null default 0,
    availability_rate   numeric(6,4),
    status              text        not null default 'running',
    error               text
);

create table if not exists apix.source_health (
    run_id              uuid        not null references apix.collection_runs(run_id),
    source              text        not null,
    cells_attempted     int         not null default 0,
    cells_available     int         not null default 0,
    quotes_written      int         not null default 0,
    blocked_count       int         not null default 0,
    timeout_count       int         not null default 0,
    parse_error_count   int         not null default 0,
    sold_out_count      int         not null default 0,
    median_latency_ms   int,
    primary key (run_id, source)
);

-- Every robots.txt decision we ever made. This table is the answer to "is this legal?".
create table if not exists apix.robots_log (
    id              bigserial primary key,
    run_id          uuid        not null,
    checked_at_utc  timestamptz not null,
    domain          text        not null,
    url             text        not null,
    user_agent      text        not null,
    allowed         boolean     not null,
    reason          text        not null,
    crawl_delay_s   numeric(6,2)
);
create index if not exists idx_robots_log_run on apix.robots_log (run_id);

create or replace view apix.v_collection_health as
select
    r.collection_date,
    r.run_id,
    r.started_at_utc,
    r.status,
    r.cells_expected,
    r.cells_available,
    r.availability_rate,
    r.quotes_written,
    extract(epoch from (r.finished_at_utc - r.started_at_utc))::int as duration_s,
    (select count(*) from apix.robots_log l where l.run_id = r.run_id and not l.allowed)
        as robots_disallowed_paths
from apix.collection_runs r
order by r.collection_date desc;
