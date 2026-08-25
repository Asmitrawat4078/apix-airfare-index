# CLAUDE.md — APIx project instructions

This file is the standing brief for every session working on this repository.
Read it fully before acting. If a request conflicts with an invariant below, say so
before proceeding.

---

## What this project is

**APIx** is a daily price index for Indian domestic airfares, built for Smart India
Hackathon 2026 problem statement **SIH26056** (MoSPI, Data Informatics & Innovation
Division).

A robot collects the same basket of fares every day at the same time, and turns those
prices into a single index number — the way the Sensex turns many share prices into one
number. The purpose is measurement, not price reduction. The intended consumer is the
National Statistical Office, which currently measures airfare inflation from a small
number of manual online checks, in a market where the same seat can cost three times more
depending on when you look.

**The index is the deliverable. The scraper is plumbing.** When you have to choose where
to spend effort, choose the statistical layer.

---

## Prime directives

1. **The collector runs, every day, without me.** Uptime beats features. If a change
   risks a missed collection cycle, it waits.
2. **Raw quotes are immutable.** Never update or delete a row in `raw_quotes`. Every
   correction is a new derived layer. Any published index value must be traceable back to
   the exact scrape rows that produced it.
3. **The basket is frozen.** Routes, lead times, and the offer definition do not change
   once set. Changing them silently breaks the series. If a change is genuinely needed,
   it becomes a new basket version with its own series, and the old one keeps running.
4. **Never fabricate a number.** No placeholder weights, no synthetic fares, no
   `np.random` anywhere near the index. If data is missing, impute it by the documented
   rule and flag it. A gap that is labelled is fine; a gap that is filled quietly is fraud.
5. **Document the decision when you make it**, in `docs/methodology.md`. Not later.
6. **One task at a time.** Finish and show me the result before starting the next thing.

---

## Repo layout

```
collector/     scrapers, one module per source, shared quote schema
dbt/           raw -> staging -> clean -> marts
index/         Jevons, weighting, imputation, chaining, multilateral, hedonic
api/           FastAPI, versioned endpoints, OpenAPI spec
dashboard/     Streamlit now, Next.js later
docs/          methodology.md, scraping-policy.md, limitations.md
data/          basket.yaml, route_weights.csv (versioned, committed)
tests/         pytest + pandera contracts
```

---

## Data contract

`raw_quotes` — append only:

```
collection_ts_utc   timestamptz   when we looked, not when we parsed
collection_date     date          the IST calendar day; the index's time axis
source              text          'ixigo' | 'easemytrip' | 'cleartrip' | ...
url                 text          exact URL requested
origin, destination text          IATA, uppercase
carrier             text          IATA airline code
flight_no           text
dep_ts              timestamptz
lead_time_days      int           1 | 7 | 15 | 30 | 45 only
fare_class          text          'economy'
base_fare           numeric
taxes               numeric
fees                numeric       UDF, convenience charges
total_fare          numeric
currency            text          'INR'
is_available        bool          false = sold out / no flight / blocked
unavailable_reason  text          null when available
raw_payload         jsonb         whatever the source returned
```

Rules: `total_fare` is what a traveller would pay, all-in. If a source will not split
base/taxes/fees, store nulls for the components and never guess. A blocked scrape and a
sold-out flight are both `is_available = false` but must have different
`unavailable_reason` — they mean opposite things downstream.

All of the above is enforced by database constraints in `db/migrations/001_raw_quotes.sql`
and by `collector/schema.py`, and both are tested. A contract that lives only in a
document is a suggestion.

---

## The basket — frozen

- **12 city pairs, directional.** DEL-BOM and BOM-DEL are separate cells.
  Seeded from the PS: DEL-BOM, DEL-BLR, BOM-BLR, DEL-CCU, BLR-HYD, MAA-DEL.
  Remainder chosen by DGCA passenger volume. At least one thin regional route (DEL-GAU).
- **Lead times: T+1, T+7, T+15, T+30, T+45.** Strata, never averaged together.
- **Departure date is derived**, never fixed: on collection date `d`, the T+15 cell means
  departure on `d + 15`.
- **The offer:** cheapest economy, 1 adult, one-way, non-stop where one exists, no baggage
  add-on, non-refundable. Every one of these held constant forever.
- **One run per day at a fixed IST time.** Never move it.
- Target: 120 route-cells/day, 300-600 quotes/day.

`scripts/verify_basket.py` recomputes the weights from the DGCA source file and checks its
SHA-256. It runs in CI. `data/basket.yaml` is the one file where a plausible-looking hand
edit would be invisible in review and would silently redefine a live series.

---

## Index rules

1. **Elementary aggregate is Jevons** — geometric mean of price relatives within each
   (route x direction x lead-time) stratum. Never an arithmetic mean of prices.
2. **Two weight layers.** Routes weighted by DGCA monthly city-pair passengers. Lead-time
   strata weighted under **three published scenarios** (near-term-heavy, uniform,
   advance-heavy) reported as a sensitivity band — because no public source gives India's
   booking-lead-time distribution and we will not invent one.
3. **Missing cells** are imputed by their stratum's movement, flagged, and an
   **availability rate is published alongside every index value.** Never carry the last
   price forward.
4. **Chained daily index**, rebased to 100 on the first collection day, aggregated up to
   weekly and monthly.
5. **Every published value is stored with its computation timestamp**, so a late-arriving
   scrape produces a visible revision rather than a silent overwrite.
6. **Hedonic robustness series**, always reported next to the headline:
   `log(fare) ~ route + carrier + lead_time + dep_dow + dep_hour + is_holiday + C(collection_date)`
   The collection-date coefficients are the quality-adjusted index. Observations are
   basket-weighted, or it is answering a different question from the headline.
7. **Chain drift is measured, not assumed away.** GEKS-Jevons (rolling window, mean splice)
   and a direct fixed-base index are published beside the chained headline, and the gap
   between them is reported as a diagnostic with its trend per day. If that gap grows
   systematically with series length, GEKS gets promoted to headline.
8. Index maths gets **unit tests against a hand-computed worked example**. This is the one
   part of the codebase where a silent bug is invisible and fatal.

---

## Scraping rules — hard limits

- Parse `robots.txt` per domain before each run, **log the decision**, skip disallowed paths.
  Fail closed: an unreachable robots.txt means we do not scrape that domain.
- Descriptive User-Agent naming the project with a contact email. Never spoof a browser
  identity to hide.
- One request per domain every few seconds, randomised. Exponential backoff on any 4xx/5xx.
  We need 120 cells a day, not 120 a minute.
- **Public, unauthenticated pages only.** No accounts, no logins, no paywall bypass.
- **Never solve or bypass a CAPTCHA.** Back off, record `is_available = false` with
  `unavailable_reason = 'blocked'`, move on. Correctly-handled missingness is the answer a
  statistics agency would give.
- No personal data is collected, ever. Prices only.
- Prefer a source's internal JSON fare endpoint over rendering HTML.
  `scripts/probe_sources.py` finds those endpoints automatically — run it from the machine
  that will actually do the collecting, because geography changes the answer.
- Every rule above is restated in `docs/scraping-policy.md` and that document is a
  deliverable, not a formality.

---

## Code standards

- Python 3.11+. Type hints on anything crossing a module boundary. `ruff` + `black`.
- Config in `basket.yaml` and environment variables. **No magic numbers in code** — a
  reader must be able to see the whole sampling design in one file.
- All timestamps stored UTC, displayed IST. Label every timestamp column with its zone.
- Money as `Decimal` or integer paise. Never float.
- Structured logging with the cell identity on every line, so a failure names the route and
  lead time it happened on.
- A failing data-contract test fails the pipeline. Do not add `try/except` around a
  validation to make a run go green.
- Commit the day's collected rows to git as CSV alongside the database write. Cheap
  insurance against losing the series to a free-tier hiccup, and it makes the whole index
  reproducible from a clone with no infrastructure.

---

## How to work with me

- **Give me one task, finish it, show me the output.** Prefer real rows and a real chart
  over a description of what the code would do.
- If something is ambiguous, ask once and propose a default. Don't build both.
- **Do not scope-creep.** Adding a fourth data source while the third is still flaky is a
  regression, not progress.
- Tell me plainly when something isn't working. A broken scraper reported today is
  recoverable; one discovered in December is not.
- When you finish a component, update `docs/methodology.md` in the same session.

---

## Never do this

- Change the basket, the lead times, or the offer definition without flagging it first.
- Mutate `raw_quotes`.
- Invent lead-time weights, or any other number, to make a chart look finished.
- Solve a CAPTCHA, log into a site, or scrape faster to "get more data".
- Report an index value without its availability rate.
- Delay the collector to build a feature.
- Let anything from `scripts/simulate.py` near a published series. It has five independent
  safeguards; do not remove any of them.

---

## Definition of done

| Component | Done means |
|---|---|
| Collector | 30+ consecutive days unattended, availability above 80%, health page live |
| Database | Immutable raw layer; any index value traceable to its source rows |
| Index | Jevons headline + hedonic robustness + sensitivity band + drift diagnostic, unit-tested |
| API | Versioned endpoints, published OpenAPI spec, revision history exposed |
| Dashboard | Index series, lead-time curve, route heatmap, availability panel, CPI overlay |
| Docs | methodology.md with an honest limitations section, scraping-policy.md |
| Evidence | Back-test vs DGCA average fares and the CPI air-transport sub-class |

---

## Glossary

- **Lead time / advance-purchase window** — days between looking and flying. Our strata.
- **Stratum** — one (route x direction x lead-time) cell. The unit we index within.
- **Matched model** — comparing the identical offer over time, so a price change is a price
  change and not a product change.
- **Jevons** — geometric mean of price relatives. Eurostat's recommended elementary
  aggregate for web-scraped data.
- **Hedonic time-dummy** — regression whose date coefficients form a quality-adjusted index.
- **Chaining** — linking period-to-period changes into a continuous series.
- **Chain drift** — the accumulation of sampling noise in a chained index. Real, measured
  here, and the reason GEKS is implemented.
- **GEKS** — a multilateral index built from every bilateral comparison in a window, which
  makes it transitive and therefore drift-free.
- **Availability rate** — share of the 120 daily cells that returned a real quote. Our
  headline data-quality number.
