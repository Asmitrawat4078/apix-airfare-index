-- APIx 001 — the immutable raw layer.
--
-- raw_quotes is append-only and is enforced as append-only by the database, not by
-- convention. CLAUDE.md invariant #2 says every published index value must be traceable
-- to the exact scrape rows that produced it; a table anyone can UPDATE cannot make that
-- promise. The rule triggers below make the promise structural.

create schema if not exists apix;

create table if not exists apix.raw_quotes (
    quote_id            bigserial primary key,
    run_id              uuid          not null,
    basket_version      int           not null default 1,

    collection_ts_utc   timestamptz   not null,   -- when we looked
    inserted_at_utc     timestamptz   not null default now(),  -- when we wrote it down
    collection_date     date          not null,   -- IST calendar day; the index's time axis

    source              text          not null,
    url                 text          not null,

    origin              char(3)       not null,
    destination         char(3)       not null,
    lead_time_days      int           not null,
    dep_date            date          not null,

    carrier             text,
    flight_no           text,
    dep_ts              timestamptz,
    arr_ts              timestamptz,
    stops               int,

    fare_class          text          not null default 'economy',
    base_fare           numeric(12,2),
    taxes               numeric(12,2),
    fees                numeric(12,2),
    total_fare          numeric(12,2),
    currency            char(3)       not null default 'INR',

    is_available        boolean       not null,
    is_cheapest_in_cell boolean       not null default false,
    unavailable_reason  text,

    raw_payload         jsonb         not null default '{}'::jsonb,

    -- A lead time outside the frozen strata is a bug in the collector, and the database
    -- is the last place it can be caught before it silently pollutes a stratum.
    constraint lead_time_is_a_basket_stratum
        check (lead_time_days in (1, 7, 15, 30, 45)),

    -- An available quote with no price is not an observation.
    constraint available_quotes_must_be_priced
        check (not is_available or total_fare is not null),

    -- Sold out and blocked mean opposite things downstream; neither may be nameless.
    constraint unavailable_quotes_must_say_why
        check (is_available or unavailable_reason is not null),

    constraint fares_are_positive
        check (total_fare is null or total_fare > 0),

    constraint iata_is_uppercase
        check (origin = upper(origin) and destination = upper(destination)),

    constraint not_a_journey_to_itself
        check (origin <> destination)
);

create index if not exists idx_raw_quotes_cell
    on apix.raw_quotes (collection_date, origin, destination, lead_time_days);
create index if not exists idx_raw_quotes_run    on apix.raw_quotes (run_id);
create index if not exists idx_raw_quotes_source on apix.raw_quotes (source, collection_date);

-- Immutability, enforced. Deliberately a RULE rather than a trigger: rules reject the
-- statement outright, so there is no window in which a row is modified and then rolled back.
create or replace rule raw_quotes_no_update as
    on update to apix.raw_quotes do instead nothing;
create or replace rule raw_quotes_no_delete as
    on delete to apix.raw_quotes do instead nothing;

comment on table apix.raw_quotes is
    'Append-only. Corrections are new derived layers, never edits. See CLAUDE.md invariant 2.';
